from __future__ import annotations

import gzip
import json
import math
import os
import tempfile
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.collector import DhanAPIError, DhanClient, DhanConfig, historical_payload_to_frame
from backend.markets.nse.oi import (
    DhanInstrumentCatalog,
    download_detailed_instrument_catalog,
    parse_rolling_option_history,
)
from backend.markets.nse.oi_regime import (
    IST,
    NiftyOiConfig,
    OiRegimeRepository,
    _as_ist,
    _change_pct,
    _clamp,
    _finite,
    combine_regime_components,
    score_options,
)
from backend.observability import get_logger

logger = get_logger("opendelta.market-data.nse-oi-history")


HISTORY_IMPORT_VERSION = "nifty-oi-history-1.0.0"
MAX_EXPIRED_OPTION_DAYS = 30
MAX_INTRADAY_DAYS = 89


@dataclass(frozen=True)
class HistoricalOiImportConfig:
    from_date: date
    to_date: date
    strikes_each_side: int = 5
    expiry_flag: str = "WEEK"
    expiry_code: int = 1
    interval: str = "5"
    expiry_schedule: tuple[tuple[date, int], ...] = ()

    def validate(self) -> None:
        if self.to_date <= self.from_date:
            raise ValueError("Historical OI to-date must be after from-date")
        if self.to_date > date.today() + timedelta(days=1):
            raise ValueError("Historical OI import cannot request future data")
        if not 0 <= self.strikes_each_side <= 10:
            raise ValueError("Historical OI strikes_each_side must be between 0 and 10")
        if self.expiry_flag != "WEEK" or self.expiry_code < 1:
            raise ValueError("Historical NIFTY OI import currently requires WEEK expiry code 1 or later")
        if self.interval != "5":
            raise ValueError("Historical NIFTY OI import uses completed five-minute observations")
        if not self.expiry_schedule:
            raise ValueError("An audited NIFTY weekly expiry schedule is required")
        schedule = sorted(self.expiry_schedule)
        if schedule[0][0] > self.from_date or any(not 0 <= weekday <= 6 for _, weekday in schedule):
            raise ValueError("The expiry schedule must cover from-date and use weekday values 0 through 6")

    def public(self) -> dict[str, Any]:
        return {
            "fromDate": self.from_date.isoformat(),
            "toDate": self.to_date.isoformat(),
            "strikesEachSide": self.strikes_each_side,
            "expiryFlag": self.expiry_flag,
            "expiryCode": self.expiry_code,
            "interval": self.interval,
            "expirySchedule": [
                {"effectiveFrom": effective.isoformat(), "weekday": weekday}
                for effective, weekday in sorted(self.expiry_schedule)
            ],
        }


def nominal_nifty_weekly_expiry(
    timestamp: datetime,
    expiry_code: int = 1,
    expiry_schedule: tuple[tuple[date, int], ...] = (),
) -> date:
    """Resolve a causal weekly roll bucket without using a future spot or option chain.

    Dhan's rolling endpoint identifies the selected series by WEEK/expiryCode but does
    not return the contract expiry. The stored date is therefore the nominal exchange
    schedule date. Holiday-adjusted dates require an externally supplied audited
    contract calendar and are deliberately not guessed here.
    """
    session = _as_ist(timestamp)
    applicable = [weekday for effective, weekday in sorted(expiry_schedule) if effective <= session.date()]
    if not applicable:
        raise ValueError("The audited expiry schedule does not cover this observation")
    target_weekday = applicable[-1]
    days_forward = (target_weekday - session.weekday()) % 7
    nominal = session.date() + timedelta(days=days_forward)
    if days_forward == 0 and session.time() >= clock_time(15, 20):
        nominal += timedelta(days=7)
    return nominal + timedelta(days=7 * (expiry_code - 1))


def _date_chunks(start: date, end: date, maximum_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=maximum_days), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(dict(payload), handle, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _merge_rolling_payloads(*payloads: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"data": {}}
    merged_data = merged["data"]
    for side in ("ce", "pe"):
        side_values: dict[str, list[Any]] = {}
        for payload in payloads:
            data = payload.get("data")
            source = data.get(side) if isinstance(data, Mapping) else None
            if not isinstance(source, Mapping):
                continue
            for key, values in source.items():
                if isinstance(values, list):
                    side_values.setdefault(str(key), []).extend(values)
        if side_values:
            merged_data[side] = side_values
    return merged


class HistoricalOiImporter:
    """Resumable Dhan rolling-options importer and causal regime materializer."""

    def __init__(
        self,
        client: DhanClient,
        repository: OiRegimeRepository,
        cache_root: Path,
        *,
        instrument_catalog: DhanInstrumentCatalog | None = None,
        oi_config: NiftyOiConfig | None = None,
    ) -> None:
        self.client = client
        self.repository = repository
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.instrument_catalog = instrument_catalog
        self.oi_config = oi_config or NiftyOiConfig()
        self.manifest_path = repository.root / "history-import.json"

    def status(self) -> dict[str, Any]:
        return _read_json(self.manifest_path) or {
            "version": HISTORY_IMPORT_VERSION,
            "state": "NOT_IMPORTED",
            "historicalDepthAvailable": False,
            "enforcementReady": False,
            "reason": "No historical NIFTY OI import has completed",
        }

    def _catalog(self) -> DhanInstrumentCatalog:
        if self.instrument_catalog is None:
            self.instrument_catalog = download_detailed_instrument_catalog(
                self.cache_root / "instrument-master-detailed.csv"
            )
        return self.instrument_catalog

    def _response_cache_path(
        self,
        start: date,
        end: date,
        strike_label: str,
        option_type: str,
        config: HistoricalOiImportConfig,
    ) -> Path:
        safe_strike = strike_label.replace("+", "plus").replace("-", "minus")
        return (
            self.cache_root
            / "expired-options"
            / f"{start.isoformat()}_{end.isoformat()}"
            / f"{config.expiry_flag.lower()}{config.expiry_code}_{safe_strike}_{option_type.lower()}.json.gz"
        )

    @staticmethod
    def _read_gzip_json(path: Path) -> dict[str, Any] | None:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _write_gzip_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".json.gz",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
            with gzip.open(temporary, "wt", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, separators=(",", ":"), allow_nan=False)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _expired_payload(
        self,
        underlying_security_id: str,
        start: date,
        end: date,
        strike_label: str,
        option_type: str,
        config: HistoricalOiImportConfig,
    ) -> dict[str, Any]:
        path = self._response_cache_path(start, end, strike_label, option_type, config)
        cached = self._read_gzip_json(path)
        if cached is not None:
            return cached
        try:
            payload = self.client.rolling_option_history(
                underlying_security_id,
                interval=config.interval,
                expiry_flag="WEEK",
                expiry_code=config.expiry_code,
                strike=strike_label,
                option_type=option_type,  # type: ignore[arg-type]
                from_date=start,
                to_date=end,
            )
        except DhanAPIError as error:
            days = (end - start).days
            if "network request failed" not in str(error).casefold() or days <= 2:
                raise
            midpoint = start + timedelta(days=max(1, days // 2))
            payload = _merge_rolling_payloads(
                self._expired_payload(
                    underlying_security_id, start, midpoint, strike_label, option_type, config
                ),
                self._expired_payload(
                    underlying_security_id, midpoint, end, strike_label, option_type, config
                ),
            )
        self._write_gzip_json(path, payload)
        return payload

    def _fetch_spot_history(
        self,
        underlying_security_id: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        path = self.cache_root / f"nifty-spot-5m_{start.isoformat()}_{end.isoformat()}.csv.gz"
        try:
            frame = pd.read_csv(path, index_col="Timestamp", parse_dates=["Timestamp"])
            frame.index = pd.DatetimeIndex(frame.index)
            frame.index = frame.index.tz_localize(IST) if frame.index.tz is None else frame.index.tz_convert(IST)
            return frame.sort_index()
        except (OSError, ValueError, KeyError, pd.errors.ParserError) as error:
            logger.debug("nifty_spot_intraday_cache_miss", path=str(path), reason=type(error).__name__)
        chunks: list[pd.DataFrame] = []
        for chunk_start, chunk_end in _date_chunks(start, end, MAX_INTRADAY_DAYS):
            payload = self.client.historical_intraday(
                underlying_security_id,
                "5",
                datetime.combine(chunk_start, clock_time.min, tzinfo=IST),
                datetime.combine(chunk_end, clock_time.min, tzinfo=IST),
                exchange_segment="IDX_I",
                instrument="INDEX",
                include_oi=False,
            )
            frame = historical_payload_to_frame(payload)
            if not frame.empty:
                chunks.append(frame)
        if not chunks:
            return pd.DataFrame()
        result = pd.concat(chunks).sort_index()
        result = result[~result.index.duplicated(keep="last")]
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index_label="Timestamp", compression="gzip")
        path.chmod(0o600)
        return result

    @staticmethod
    def _spot_components(frame: pd.DataFrame) -> dict[datetime, dict[str, Any]]:
        if frame.empty:
            return {}
        data = frame.copy().sort_index()
        data.index = data.index.tz_localize(IST) if data.index.tz is None else data.index.tz_convert(IST)
        close = pd.to_numeric(data["Close"], errors="coerce")
        high = pd.to_numeric(data["High"], errors="coerce")
        low = pd.to_numeric(data["Low"], errors="coerce")
        volume = pd.to_numeric(data["Volume"], errors="coerce").fillna(0.0)
        ema9 = close.ewm(span=9, adjust=False, min_periods=9).mean()
        ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
        session = pd.Series(data.index.date, index=data.index)
        typical = (high + low + close) / 3.0
        vwap = (typical * volume).groupby(session).cumsum() / volume.groupby(session).cumsum().replace(0, np.nan)
        output: dict[datetime, dict[str, Any]] = {}
        for index in range(19, len(data)):
            values = (close.iloc[index], vwap.iloc[index], ema9.iloc[index], ema20.iloc[index])
            if not all(math.isfinite(float(value)) for value in values):
                continue
            last = float(close.iloc[index])
            return5 = _change_pct(last, float(close.iloc[index - 1])) or 0.0
            return15 = _change_pct(last, float(close.iloc[index - 3])) or 0.0
            slope = _change_pct(float(ema9.iloc[index]), float(ema9.iloc[index - 3])) or 0.0
            above_vwap = last > float(vwap.iloc[index])
            ema_above = float(ema9.iloc[index]) > float(ema20.iloc[index])
            score = 25.0 if above_vwap else -25.0
            score += _clamp(return5 * 20.0, -20.0, 20.0)
            score += _clamp(return15 * 10.0, -20.0, 20.0)
            score += 20.0 if ema_above else -20.0
            score += _clamp(slope * 15.0, -15.0, 15.0)
            timestamp = _as_ist(data.index[index])
            output[timestamp] = {
                "available": True,
                "score": _finite(_clamp(score, -100.0, 100.0)),
                "confidence": "HIGH",
                "aboveVwap": above_vwap,
                "return5mPct": _finite(return5),
                "return15mPct": _finite(return15),
                "ema9AboveEma20": ema_above,
                "emaSlopePct": _finite(slope),
                "sourceTimestamp": timestamp.isoformat(),
                "dataAgeSeconds": 0.0,
            }
        return output

    def _materialize_regimes(
        self,
        start: date,
        end: date,
        spot_frame: pd.DataFrame,
    ) -> tuple[int, int]:
        observations = [
            item
            for item in self.repository.option_history()
            if start <= _as_ist(item.timestamp).date() < end
        ]
        grouped: dict[datetime, list[Any]] = {}
        for item in observations:
            grouped.setdefault(_as_ist(item.timestamp), []).append(item)
        timestamps = sorted(grouped)
        spot_components = self._spot_components(spot_frame)
        spot_times = sorted(spot_components)
        existing = {str(row.get("timestamp")) for row in self.repository.regimes()}
        created = insufficient = 0
        pending_snapshots: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            if index < self.oi_config.lookback_bars or timestamp.isoformat() in existing:
                continue
            previous_timestamp = timestamps[index - self.oi_config.lookback_bars]
            options = score_options(
                grouped[timestamp],
                grouped[previous_timestamp],
                timestamp,
                self.oi_config,
            )
            spot_index = bisect_right(spot_times, timestamp) - 1
            spot = (
                spot_components[spot_times[spot_index]]
                if spot_index >= 0
                and (timestamp - spot_times[spot_index]).total_seconds() <= self.oi_config.stale_data_seconds
                else {"available": False, "reason": "Completed NIFTY spot candle is unavailable"}
            )
            snapshot = combine_regime_components(
                options,
                {"available": False, "reason": "Historical futures OI is unavailable for expired contracts"},
                spot,
                timestamp,
                self.oi_config,
            )
            snapshot["historicalImport"] = True
            snapshot["historicalDepthAvailable"] = False
            snapshot["historicalQualityWarning"] = (
                "Dhan rolling expired-options data does not include bid/ask; strict option quality rejects it for enforcement"
            )
            pending_snapshots.append(snapshot)
            created += 1
            insufficient += int(snapshot.get("regime") == "INSUFFICIENT_OI_DATA")
        self.repository.append_regimes(pending_snapshots)
        return created, insufficient

    def run(self, config: HistoricalOiImportConfig) -> dict[str, Any]:
        try:
            return self._run(config)
        except (DhanAPIError, OSError, ValueError, KeyError, TypeError) as error:
            manifest = self.status()
            manifest.update({
                "state": "INTERRUPTED",
                "updatedAt": datetime.now(IST).isoformat(),
                "errorType": type(error).__name__,
                "reason": str(error),
                "resumable": True,
            })
            _atomic_json(self.manifest_path, manifest)
            raise

    def _run(self, config: HistoricalOiImportConfig) -> dict[str, Any]:
        config.validate()
        started = datetime.now(IST)
        catalog = self._catalog()
        underlying = catalog.nifty_underlying()
        chunks = _date_chunks(config.from_date, config.to_date, MAX_EXPIRED_OPTION_DAYS)
        strikes = [
            "ATM" if distance == 0 else f"ATM{distance:+d}"
            for distance in range(-config.strikes_each_side, config.strikes_each_side + 1)
        ]
        tasks = [
            (chunk_start, chunk_end, distance, strike_label, option_type)
            for chunk_start, chunk_end in chunks
            for distance, strike_label in zip(
                range(-config.strikes_each_side, config.strikes_each_side + 1), strikes, strict=True
            )
            for option_type in ("CALL", "PUT")
        ]
        completed_tasks = 0
        imported_rows = 0
        existing_keys = {
            (
                _as_ist(item.timestamp).isoformat(),
                item.expiry.isoformat(),
                item.option_type,
                item.distance_from_atm,
                item.strike,
            )
            for item in self.repository.option_history()
        }
        rows_before = len(existing_keys)
        manifest: dict[str, Any] = {
            "version": HISTORY_IMPORT_VERSION,
            "state": "RUNNING",
            "startedAt": started.isoformat(),
            "updatedAt": started.isoformat(),
            "request": config.public(),
            "tasksTotal": len(tasks),
            "tasksCompleted": 0,
            "optionRowsImported": rows_before,
            "optionRowsAddedThisRun": 0,
            "historicalDepthAvailable": False,
            "enforcementReady": False,
            "expiryAudit": "DHAN WEEK rolling series; nominal schedule date stored because the API omits contract expiry",
        }
        _atomic_json(self.manifest_path, manifest)
        for chunk_start, chunk_end, distance, strike_label, option_type in tasks:
            payload = self._expired_payload(
                underlying.security_id,
                chunk_start,
                chunk_end,
                strike_label,
                option_type,
                config,
            )
            observations = parse_rolling_option_history(
                payload,
                option_type=option_type,
                distance_from_atm=distance,
                expiry_resolver=lambda timestamp: nominal_nifty_weekly_expiry(
                    timestamp, config.expiry_code, config.expiry_schedule
                ),
                ingestion_timestamp=datetime.now(IST),
            )
            unique = []
            for item in observations:
                key = (
                    _as_ist(item.timestamp).isoformat(),
                    item.expiry.isoformat(),
                    item.option_type,
                    item.distance_from_atm,
                    item.strike,
                )
                if key not in existing_keys:
                    existing_keys.add(key)
                    unique.append(item)
            self.repository.append_options(unique)
            imported_rows += len(unique)
            completed_tasks += 1
            manifest.update({
                "updatedAt": datetime.now(IST).isoformat(),
                "tasksCompleted": completed_tasks,
                "optionRowsImported": len(existing_keys),
                "optionRowsAddedThisRun": imported_rows,
                "lastCompletedTask": {
                    "fromDate": chunk_start.isoformat(),
                    "toDate": chunk_end.isoformat(),
                    "strike": strike_label,
                    "optionType": option_type,
                },
            })
            _atomic_json(self.manifest_path, manifest)
        spot = self._fetch_spot_history(underlying.security_id, config.from_date, config.to_date)
        regimes_created, _ = self._materialize_regimes(config.from_date, config.to_date, spot)
        imported_regimes: list[dict[str, Any]] = []
        for row in self.repository.regimes():
            if not row.get("historicalImport"):
                continue
            try:
                regime_date = _as_ist(str(row["timestamp"])).date()
            except (KeyError, TypeError, ValueError):
                continue
            if config.from_date <= regime_date < config.to_date:
                imported_regimes.append(row)
        insufficient = sum(
            1 for row in imported_regimes if row.get("regime") == "INSUFFICIENT_OI_DATA"
        )
        completed = datetime.now(IST)
        manifest.update({
            "state": "COMPLETE",
            "updatedAt": completed.isoformat(),
            "completedAt": completed.isoformat(),
            "tasksCompleted": completed_tasks,
            "optionRowsImported": len(existing_keys),
            "optionRowsAddedThisRun": imported_rows,
            "spotRows": len(spot),
            "regimeSnapshotsCreated": len(imported_regimes),
            "regimeSnapshotsAddedThisRun": regimes_created,
            "insufficientSnapshots": insufficient,
            "enforceableSnapshots": len(imported_regimes) - insufficient,
            "reason": (
                "Historical observations were imported, but strict enforcement remains unavailable because "
                "Dhan expired-options history has no bid/ask and reliable expired-futures OI is unavailable"
            ),
        })
        _atomic_json(self.manifest_path, manifest)
        return manifest


def build_historical_oi_importer(
    dhan_config: DhanConfig,
    repository: OiRegimeRepository,
    cache_root: Path,
    *,
    oi_config: NiftyOiConfig | None = None,
) -> HistoricalOiImporter:
    return HistoricalOiImporter(
        DhanClient(dhan_config),
        repository,
        cache_root,
        oi_config=oi_config,
    )
