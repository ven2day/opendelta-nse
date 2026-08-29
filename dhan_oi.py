from __future__ import annotations

import io
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from main import DhanAPIError, DhanClient, DhanConfig, historical_payload_to_frame
from nifty_oi_regime import (
    FuturesOiObservation,
    NiftyOiConfig,
    OiRegimeRepository,
    OptionOiObservation,
    _as_ist,
    _change_pct,
    combine_regime_components,
    insufficient_regime,
    score_futures,
    score_options,
    score_spot_trend,
    select_atm_strikes,
    select_expiry,
    option_contract_is_eligible,
)


DETAILED_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
NIFTY_NAMES = {"NIFTY", "NIFTY 50", "NIFTY50"}


def _column(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name].astype(str).str.strip()
    return pd.Series("", index=frame.index, dtype=str)


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class DhanInstrument:
    security_id: str
    exchange: str
    segment: str
    instrument: str
    underlying_security_id: str
    underlying_symbol: str
    symbol_name: str
    display_name: str
    expiry: date | None
    strike: float | None
    option_type: str | None
    expiry_flag: str | None


class DhanInstrumentCatalog:
    def __init__(self, instruments: Sequence[DhanInstrument]) -> None:
        self.instruments = tuple(instruments)

    @classmethod
    def from_csv(cls, payload: bytes | str) -> DhanInstrumentCatalog:
        source = io.BytesIO(payload) if isinstance(payload, bytes) else io.StringIO(payload)
        frame = pd.read_csv(source, dtype=str).fillna("")
        records: list[DhanInstrument] = []
        security = _column(frame, "SECURITY_ID", "SEM_SMST_SECURITY_ID")
        exchange = _column(frame, "EXCH_ID", "SEM_EXM_EXCH_ID")
        segment = _column(frame, "SEGMENT", "SEM_SEGMENT")
        instrument = _column(frame, "INSTRUMENT", "SEM_INSTRUMENT_NAME")
        underlying_id = _column(frame, "UNDERLYING_SECURITY_ID")
        underlying_symbol = _column(frame, "UNDERLYING_SYMBOL")
        symbol_name = _column(frame, "SYMBOL_NAME", "SM_SYMBOL_NAME")
        display_name = _column(frame, "DISPLAY_NAME", "SEM_CUSTOM_SYMBOL")
        expiry = _column(frame, "SM_EXPIRY_DATE", "SEM_EXPIRY_DATE")
        strike = _column(frame, "STRIKE_PRICE", "SEM_STRIKE_PRICE")
        option_type = _column(frame, "OPTION_TYPE", "SEM_OPTION_TYPE")
        expiry_flag = _column(frame, "EXPIRY_FLAG", "SEM_EXPIRY_FLAG")
        for index in frame.index:
            expiry_value: date | None = None
            if expiry.at[index]:
                parsed = pd.to_datetime(expiry.at[index], errors="coerce")
                expiry_value = None if pd.isna(parsed) else parsed.date()
            strike_value: float | None = None
            try:
                candidate = float(strike.at[index])
                strike_value = candidate if math.isfinite(candidate) else None
            except (TypeError, ValueError):
                pass
            records.append(DhanInstrument(
                security_id=security.at[index],
                exchange=exchange.at[index].upper(),
                segment=segment.at[index].upper(),
                instrument=instrument.at[index].upper(),
                underlying_security_id=underlying_id.at[index],
                underlying_symbol=underlying_symbol.at[index].upper(),
                symbol_name=symbol_name.at[index].upper(),
                display_name=display_name.at[index].upper(),
                expiry=expiry_value,
                strike=strike_value,
                option_type=option_type.at[index].upper() or None,
                expiry_flag=expiry_flag.at[index].upper() or None,
            ))
        return cls(records)

    def nifty_underlying(self) -> DhanInstrument:
        matches = [
            item for item in self.instruments
            if item.exchange == "NSE"
            and item.instrument == "INDEX"
            and ({item.symbol_name, item.display_name} & NIFTY_NAMES)
        ]
        if not matches:
            raise DhanAPIError("NIFTY 50 was not found in the detailed Dhan instrument master")
        return sorted(matches, key=lambda item: (item.display_name != "NIFTY 50", int(item.security_id or 10**12)))[0]

    def nifty_futures(self, evaluation_timestamp: datetime) -> list[DhanInstrument]:
        underlying = self.nifty_underlying()
        session_date = _as_ist(evaluation_timestamp).date()
        matches = [
            item for item in self.instruments
            if item.exchange == "NSE"
            and item.instrument == "FUTIDX"
            and item.expiry is not None
            and item.expiry >= session_date
            and (
                item.underlying_security_id == underlying.security_id
                or item.underlying_symbol in NIFTY_NAMES
                or item.symbol_name.startswith("NIFTY")
                or item.display_name.startswith("NIFTY")
            )
        ]
        return sorted(matches, key=lambda item: (item.expiry or date.max, item.security_id))

    def nifty_options(self, expiry: date) -> list[DhanInstrument]:
        underlying = self.nifty_underlying()
        matches = [
            item for item in self.instruments
            if item.exchange == "NSE"
            and item.instrument == "OPTIDX"
            and item.expiry == expiry
            and item.strike is not None
            and item.option_type in {"CE", "PE", "CALL", "PUT"}
            and (
                item.underlying_security_id == underlying.security_id
                or item.underlying_symbol in NIFTY_NAMES
                or item.symbol_name.startswith("NIFTY")
                or item.display_name.startswith("NIFTY")
            )
        ]
        return sorted(matches, key=lambda item: (item.strike or 0.0, item.option_type or "", item.security_id))


def download_detailed_instrument_catalog(
    cache_file: Path,
    *,
    url: str = DETAILED_MASTER_URL,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> DhanInstrumentCatalog:
    try:
        if cache_file.exists() and now() - cache_file.stat().st_mtime < 12 * 60 * 60:
            return DhanInstrumentCatalog.from_csv(cache_file.read_bytes())
    except OSError:
        pass
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": "opendelta-nifty-oi/1.0"})
            with urlopen(request, timeout=60) as response:
                payload = response.read()
            catalog = DhanInstrumentCatalog.from_csv(payload)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=cache_file.parent, prefix=".dhan-detailed-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(payload)
                temporary.chmod(0o600)
                os.replace(temporary, cache_file)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            return catalog
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, pd.errors.ParserError) as error:
            last_error = error
            if attempt < retries:
                sleep(min(2**attempt, 8))
    raise DhanAPIError("Unable to load the detailed Dhan instrument master after bounded retries") from last_error


def parse_option_chain(
    payload: Mapping[str, Any],
    *,
    timestamp: datetime,
    expiry: date,
    ingestion_timestamp: datetime,
) -> list[OptionOiObservation]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise DhanAPIError("Dhan option-chain payload omitted data")
    chain = data.get("oc")
    if not isinstance(chain, Mapping):
        raise DhanAPIError("Dhan option-chain payload omitted strikes")
    spot = float(data.get("last_price") or 0.0)
    strikes = sorted(float(value) for value in chain if math.isfinite(float(value)))
    if not strikes or spot <= 0:
        raise DhanAPIError("Dhan option-chain payload omitted a valid spot price or strike list")
    atm_index = min(range(len(strikes)), key=lambda index: (abs(strikes[index] - spot), strikes[index]))
    distance = {strike: index - atm_index for index, strike in enumerate(strikes)}
    observations: list[OptionOiObservation] = []
    for strike_text, pair in chain.items():
        if not isinstance(pair, Mapping):
            continue
        strike = float(strike_text)
        for source_key, option_type in (("ce", "CALL"), ("pe", "PUT")):
            quote = pair.get(source_key)
            if not isinstance(quote, Mapping):
                continue
            greeks = quote.get("greeks") if isinstance(quote.get("greeks"), Mapping) else {}
            ltp = _optional_float(quote.get("last_price")) or 0.0
            previous_ltp = _optional_float(quote.get("previous_close_price"))
            oi_value = _optional_float(quote.get("oi"))
            if oi_value is None:
                continue
            oi = oi_value
            previous_oi = _optional_float(quote.get("previous_oi"))
            oi_change = oi - previous_oi if previous_oi is not None else None
            observations.append(OptionOiObservation(
                timestamp=_as_ist(timestamp),
                underlying="NIFTY",
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                security_id=str(quote.get("security_id") or ""),
                ltp=ltp,
                previous_ltp=previous_ltp,
                open_interest=oi,
                previous_open_interest=previous_oi,
                oi_change=oi_change,
                oi_change_pct=_change_pct(oi, previous_oi) if previous_oi is not None else None,
                volume=float(quote.get("volume") or 0.0),
                implied_volatility=_optional_float(quote.get("implied_volatility")),
                bid=_optional_float(quote.get("top_bid_price")) or 0.0,
                ask=_optional_float(quote.get("top_ask_price")) or 0.0,
                spot_price=spot,
                distance_from_atm=distance[strike],
                data_source="DHAN_OPTION_CHAIN",
                ingestion_timestamp=_as_ist(ingestion_timestamp),
                delta=_optional_float(greeks.get("delta")),
                gamma=_optional_float(greeks.get("gamma")),
                theta=_optional_float(greeks.get("theta")),
                vega=_optional_float(greeks.get("vega")),
                source_timestamp=_as_ist(timestamp),
            ))
    return observations


def parse_rolling_option_history(
    payload: Mapping[str, Any],
    *,
    option_type: str,
    distance_from_atm: int,
    expiry_resolver: Callable[[datetime], date | None],
    ingestion_timestamp: datetime,
    underlying: str = "NIFTY",
) -> list[OptionOiObservation]:
    """Convert Dhan rolling expired-options arrays into the live canonical schema.

    The caller must resolve the expiry from information valid at each row timestamp;
    rows without an auditable expiry are discarded instead of being guessed.
    """
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise DhanAPIError("Dhan expired-options payload omitted data")
    normalized_type = option_type.upper()
    key = "ce" if normalized_type == "CALL" else "pe" if normalized_type == "PUT" else ""
    if not key:
        raise ValueError("Expired option type must be CALL or PUT")
    series = data.get(key)
    if not isinstance(series, Mapping):
        return []
    timestamps = series.get("timestamp")
    if not isinstance(timestamps, list):
        raise DhanAPIError("Dhan expired-options payload omitted timestamps")

    def value_at(name: str, index: int) -> float | None:
        values = series.get(name)
        return _optional_float(values[index]) if isinstance(values, list) and index < len(values) else None

    observations: list[OptionOiObservation] = []
    previous_by_expiry: dict[date, OptionOiObservation] = {}
    for index, epoch in enumerate(timestamps):
        epoch_value = _optional_float(epoch)
        if epoch_value is None:
            continue
        timestamp = datetime.fromtimestamp(epoch_value, tz=_as_ist(ingestion_timestamp).tzinfo)
        expiry = expiry_resolver(timestamp)
        close = value_at("close", index)
        oi = value_at("oi", index)
        strike = value_at("strike", index)
        spot = value_at("spot", index)
        volume = value_at("volume", index)
        if expiry is None or close is None or oi is None or strike is None or spot is None or volume is None:
            continue
        previous = previous_by_expiry.get(expiry)
        observation = OptionOiObservation(
            timestamp=_as_ist(timestamp),
            underlying=underlying,
            expiry=expiry,
            strike=strike,
            option_type=normalized_type,  # type: ignore[arg-type]
            security_id="",
            ltp=close,
            previous_ltp=previous.ltp if previous is not None else None,
            open_interest=oi,
            previous_open_interest=previous.open_interest if previous is not None else None,
            oi_change=oi - previous.open_interest if previous is not None else None,
            oi_change_pct=_change_pct(oi, previous.open_interest) if previous is not None else None,
            volume=volume,
            implied_volatility=value_at("iv", index),
            bid=0.0,
            ask=0.0,
            spot_price=spot,
            distance_from_atm=distance_from_atm,
            data_source="DHAN_EXPIRED_OPTIONS",
            ingestion_timestamp=_as_ist(ingestion_timestamp),
            source_timestamp=_as_ist(timestamp),
            open_price=value_at("open", index),
            high_price=value_at("high", index),
            low_price=value_at("low", index),
            close_price=close,
        )
        observations.append(observation)
        previous_by_expiry[expiry] = observation
    return observations


def parse_futures_quote(
    quote: Mapping[str, Any],
    instrument: DhanInstrument,
    *,
    timestamp: datetime,
    spot_price: float,
    ingestion_timestamp: datetime,
) -> FuturesOiObservation:
    price = float(quote.get("last_price") or 0.0)
    ohlc = quote.get("ohlc") if isinstance(quote.get("ohlc"), Mapping) else {}
    previous_price = float(ohlc.get("close") or 0.0) or None
    oi = float(quote.get("oi") or 0.0)
    return FuturesOiObservation(
        timestamp=_as_ist(timestamp),
        expiry=instrument.expiry or _as_ist(timestamp).date(),
        security_id=instrument.security_id,
        futures_price=price,
        previous_price=previous_price,
        open_interest=oi,
        previous_open_interest=None,
        price_change_pct=_change_pct(price, previous_price) if previous_price is not None else None,
        oi_change_pct=None,
        volume=float(quote.get("volume") or 0.0),
        spot_price=spot_price,
        basis=price - spot_price,
        data_source="DHAN_MARKET_QUOTE",
        ingestion_timestamp=_as_ist(ingestion_timestamp),
        source_timestamp=_as_ist(timestamp),
    )


class DhanNiftyOiService:
    """Collect and score one completed five-minute NIFTY OI snapshot at a time."""

    def __init__(
        self,
        client: DhanClient,
        repository: OiRegimeRepository,
        cache_directory: Path,
        *,
        config: NiftyOiConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        catalog_loader: Callable[[Path], DhanInstrumentCatalog] = download_detailed_instrument_catalog,
    ) -> None:
        self.client = client
        self.repository = repository
        self.cache_directory = cache_directory
        self.config = (config or NiftyOiConfig()).validate()
        self.clock = clock
        self.catalog_loader = catalog_loader
        self._lock = threading.RLock()
        self._catalog: DhanInstrumentCatalog | None = None
        self._expiry_cache: tuple[date, list[str]] | None = None
        self._last_evaluation: datetime | None = None
        self._live_options: dict[str, OptionOiObservation] = {}
        self._live_future: FuturesOiObservation | None = None
        self._live_underlying_id: str | None = None
        self._live_events: dict[str, list[dict[str, Any]]] = {}
        self._subscription_plan_timestamp: datetime | None = None
        self._subscription_refresh_required = False
        self._last_live_bar: datetime | None = None

    def _instruments(self) -> DhanInstrumentCatalog:
        if self._catalog is None:
            self._catalog = self.catalog_loader(self.cache_directory / "dhan-instruments-detailed.csv")
        return self._catalog

    def _expiry_list(self, underlying_id: str, session: date) -> list[str]:
        if self._expiry_cache is None or self._expiry_cache[0] != session:
            self._expiry_cache = (session, self.client.option_expiry_list(underlying_id, "IDX_I"))
        return list(self._expiry_cache[1])

    def _spot_candles(self, underlying_id: str, timestamp: datetime) -> pd.DataFrame:
        payload = self.client.historical_intraday(
            underlying_id,
            "5",
            _as_ist(timestamp) - timedelta(days=3),
            _as_ist(timestamp) + timedelta(seconds=1),
            exchange_segment="IDX_I",
            instrument="INDEX",
        )
        return historical_payload_to_frame(payload)

    def _liquid_option_snapshot(
        self,
        catalog: DhanInstrumentCatalog,
        underlying: DhanInstrument,
        evaluation: datetime,
        ingestion: datetime,
    ) -> list[OptionOiObservation]:
        expiries = self._expiry_list(underlying.security_id, evaluation.date())
        first = select_expiry(
            expiries,
            evaluation,
            rollover_time=clock_time(self.config.expiry_rollover_hour, self.config.expiry_rollover_minute),
        )
        if first is None:
            raise DhanAPIError("No current NIFTY option expiry is available")
        for expiry_text in sorted(expiries):
            expiry = date.fromisoformat(expiry_text)
            if expiry < first:
                continue
            payload = self.client.option_chain(underlying.security_id, expiry.isoformat(), "IDX_I")
            observations = parse_option_chain(
                payload,
                timestamp=evaluation,
                expiry=expiry,
                ingestion_timestamp=ingestion,
            )
            master_by_key = {
                (
                    float(item.strike or 0.0),
                    "CALL" if item.option_type in {"CE", "CALL"} else "PUT",
                ): item.security_id
                for item in catalog.nifty_options(expiry)
            }
            observations = [
                replace(item, security_id=master_by_key[(item.strike, item.option_type)])
                for item in observations
                if (item.strike, item.option_type) in master_by_key
            ]
            if not observations:
                continue
            spot_values = [item.spot_price for item in observations if item.spot_price > 0]
            if not spot_values:
                continue
            selected = select_atm_strikes(
                (item.strike for item in observations), median(spot_values), self.config.strikes_each_side
            )
            expected = max(1, len(selected) * 2)
            valid = [
                item for item in observations
                if item.strike in selected and option_contract_is_eligible(item, evaluation, self.config)
            ]
            if len(valid) / expected >= self.config.minimum_valid_contract_fraction:
                return observations
        raise DhanAPIError("No sufficiently liquid NIFTY option expiry passed configured quality checks")

    def _liquid_future_snapshot(
        self,
        catalog: DhanInstrumentCatalog,
        evaluation: datetime,
        ingestion: datetime,
        spot_price: float,
    ) -> FuturesOiObservation | None:
        futures = catalog.nifty_futures(evaluation)[:3]
        if not futures:
            return None
        quotes = self.client.market_quote_segments({"NSE_FNO": [item.security_id for item in futures]})
        segment = quotes.get("NSE_FNO", {})
        for future in futures:
            quote = segment.get(future.security_id)
            if not quote:
                continue
            observation = parse_futures_quote(
                quote,
                future,
                timestamp=evaluation,
                spot_price=spot_price,
                ingestion_timestamp=ingestion,
            )
            if (
                observation.futures_price > 0
                and observation.open_interest >= 0
                and observation.volume >= self.config.minimum_futures_volume
            ):
                return observation
        return None

    def invalidate_live_state(self) -> None:
        """Discard reconnect-sensitive feed state so stale OI cannot be reused."""
        with self._lock:
            self._live_options = {}
            self._live_future = None
            self._live_underlying_id = None
            self._live_events = {}
            self._subscription_plan_timestamp = None
            self._subscription_refresh_required = False

    def prepare_live_subscriptions(self, timestamp: datetime) -> list[dict[str, str]]:
        evaluation = _as_ist(timestamp)
        with self._lock:
            catalog = self._instruments()
            underlying = catalog.nifty_underlying()
            ingestion = _as_ist(self.clock())
            options = self._liquid_option_snapshot(catalog, underlying, ingestion, ingestion)
            spot = median([item.spot_price for item in options])
            selected_strikes = select_atm_strikes(
                (item.strike for item in options), spot, self.config.strikes_each_side
            )
            selected_options = [
                item for item in options
                if item.strike in selected_strikes and item.security_id
            ]
            future = self._liquid_future_snapshot(catalog, ingestion, ingestion, spot)
            self._live_options = {item.security_id: item for item in selected_options}
            self._live_future = future
            self._live_underlying_id = underlying.security_id
            self._live_events = {}
            self._subscription_plan_timestamp = evaluation
            self._subscription_refresh_required = False
            instruments = [
                {"ExchangeSegment": "NSE_FNO", "SecurityId": item.security_id}
                for item in selected_options
            ]
            if future is not None:
                instruments.append({"ExchangeSegment": "NSE_FNO", "SecurityId": future.security_id})
            instruments.append({"ExchangeSegment": "IDX_I", "SecurityId": underlying.security_id})
            return instruments

    def poll_subscription_updates(self, timestamp: datetime) -> list[dict[str, str]]:
        with self._lock:
            required = self._subscription_refresh_required or self._subscription_plan_timestamp is None
        return self.prepare_live_subscriptions(timestamp) if required else []

    def on_market_feed(self, packet: Any) -> None:
        security_id = str(getattr(packet, "security_id", ""))
        with self._lock:
            tracked = set(self._live_options)
            if self._live_future is not None:
                tracked.add(self._live_future.security_id)
            if self._live_underlying_id is not None:
                tracked.add(self._live_underlying_id)
            if security_id not in tracked:
                return
            event = {
                "timestamp": _as_ist(getattr(packet, "timestamp")),
                "price": getattr(packet, "price", None),
                "volume": getattr(packet, "cumulative_volume", None),
                "open_interest": getattr(packet, "open_interest", None),
                "bid": getattr(packet, "bid", None),
                "ask": getattr(packet, "ask", None),
            }
            rows = self._live_events.setdefault(security_id, [])
            rows.append(event)
            if len(rows) > 2048:
                del rows[:-1024]

    def _feed_state_at(self, security_id: str, evaluation: datetime) -> tuple[dict[str, Any], datetime | None]:
        state: dict[str, Any] = {}
        sources: dict[str, datetime] = {}
        for event in self._live_events.get(security_id, []):
            timestamp = _as_ist(event["timestamp"])
            if timestamp > evaluation:
                continue
            for key in ("price", "volume", "open_interest", "bid", "ask"):
                if event.get(key) is not None:
                    state[key] = event[key]
                    sources[key] = timestamp
        # Fresh price ticks must not conceal an older OI or depth observation.
        return state, min(sources.values()) if sources else None

    def complete_live_bar(self, evaluation_timestamp: datetime) -> bool:
        """Persist one five-minute snapshot using feed events received no later than the bar close."""
        evaluation = _as_ist(evaluation_timestamp)
        ingestion = _as_ist(self.clock())
        with self._lock:
            if self._last_live_bar == evaluation:
                return True
            if not self._live_options:
                return False
            option_history = self.repository.option_history()
            previous_options: dict[tuple[date, float, str], OptionOiObservation] = {}
            for item in option_history:
                if _as_ist(item.timestamp) < evaluation:
                    existing = previous_options.get(item.key)
                    if existing is None or _as_ist(existing.timestamp) < _as_ist(item.timestamp):
                        previous_options[item.key] = item
            underlying_state, underlying_source = self._feed_state_at(self._live_underlying_id or "", evaluation)
            seeds = list(self._live_options.values())
            seed_spot = median([item.spot_price for item in seeds])
            spot = _optional_float(underlying_state.get("price")) or seed_spot
            strikes = sorted({item.strike for item in seeds})
            atm_index = min(range(len(strikes)), key=lambda index: (abs(strikes[index] - spot), strikes[index]))
            distance = {strike: index - atm_index for index, strike in enumerate(strikes)}
            observations: list[OptionOiObservation] = []
            for seed in seeds:
                state, source = self._feed_state_at(seed.security_id, evaluation)
                seed_source = _as_ist(seed.source_timestamp or seed.timestamp)
                if source is None and seed_source > evaluation:
                    continue
                source_candidates = [source] if source is not None else []
                if any(state.get(key) is None for key in ("price", "open_interest", "volume", "bid", "ask")):
                    source_candidates.append(seed_source)
                source_candidates.append(underlying_source if underlying_state.get("price") is not None and underlying_source is not None else seed_source)
                source = min(source_candidates)
                previous = previous_options.get(seed.key)
                ltp = _optional_float(state.get("price")) or seed.ltp
                oi = _optional_float(state.get("open_interest"))
                if oi is None:
                    oi = seed.open_interest
                volume = _optional_float(state.get("volume"))
                if volume is None:
                    volume = seed.volume
                observations.append(replace(
                    seed,
                    timestamp=evaluation,
                    ltp=ltp,
                    previous_ltp=previous.ltp if previous is not None else None,
                    open_interest=oi,
                    previous_open_interest=previous.open_interest if previous is not None else None,
                    oi_change=oi - previous.open_interest if previous is not None else None,
                    oi_change_pct=_change_pct(oi, previous.open_interest) if previous is not None else None,
                    volume=volume,
                    bid=_optional_float(state.get("bid")) or seed.bid,
                    ask=_optional_float(state.get("ask")) or seed.ask,
                    spot_price=spot,
                    distance_from_atm=distance[seed.strike],
                    data_source="DHAN_LIVE_MARKET_FEED",
                    ingestion_timestamp=ingestion,
                    source_timestamp=source,
                ))
            if observations:
                self.repository.append_options(observations)

            if self._live_future is not None:
                state, source = self._feed_state_at(self._live_future.security_id, evaluation)
                seed_source = _as_ist(self._live_future.source_timestamp or self._live_future.timestamp)
                source_candidates = [source] if source is not None else []
                if any(state.get(key) is None for key in ("price", "open_interest", "volume")) and seed_source <= evaluation:
                    source_candidates.append(seed_source)
                source_candidates.append(underlying_source if underlying_state.get("price") is not None and underlying_source is not None else seed_source)
                source = min(source_candidates) if source_candidates else None
                if source is not None:
                    history = [
                        item for item in self.repository.futures_history()
                        if item.security_id == self._live_future.security_id and _as_ist(item.timestamp) < evaluation
                    ]
                    previous = max(history, key=lambda item: _as_ist(item.timestamp)) if history else None
                    price = _optional_float(state.get("price")) or self._live_future.futures_price
                    oi = _optional_float(state.get("open_interest"))
                    if oi is None:
                        oi = self._live_future.open_interest
                    volume = _optional_float(state.get("volume"))
                    if volume is None:
                        volume = self._live_future.volume
                    self.repository.append_futures(replace(
                        self._live_future,
                        timestamp=evaluation,
                        futures_price=price,
                        previous_price=previous.futures_price if previous is not None else None,
                        open_interest=oi,
                        previous_open_interest=previous.open_interest if previous is not None else None,
                        price_change_pct=_change_pct(price, previous.futures_price) if previous is not None else None,
                        oi_change_pct=_change_pct(oi, previous.open_interest) if previous is not None else None,
                        volume=volume,
                        spot_price=spot,
                        basis=price - spot,
                        data_source="DHAN_LIVE_MARKET_FEED",
                        ingestion_timestamp=ingestion,
                        source_timestamp=source,
                    ))
            if self._subscription_plan_timestamp is None or (
                evaluation - self._subscription_plan_timestamp
            ).total_seconds() >= self.config.completed_bar_seconds:
                self._subscription_refresh_required = True
            self._last_live_bar = evaluation
            return bool(observations)

    def refresh(
        self,
        evaluation_timestamp: datetime,
        *,
        spot_candles: pd.DataFrame | None = None,
        strict_causal: bool = False,
    ) -> dict[str, Any]:
        evaluation = _as_ist(evaluation_timestamp)
        with self._lock:
            if self._last_evaluation == evaluation:
                cached = self.repository.regime_at_or_before(evaluation, stale_seconds=self.config.stale_data_seconds)
                if cached is not None:
                    return cached
            try:
                catalog = self._instruments()
                underlying = catalog.nifty_underlying()
                if not strict_causal:
                    ingestion = _as_ist(self.clock())
                    options = self._liquid_option_snapshot(catalog, underlying, evaluation, ingestion)
                    self.repository.append_options(options)
                    spot = median([item.spot_price for item in options])
                    current_future = self._liquid_future_snapshot(catalog, evaluation, ingestion, spot)
                    if current_future is not None:
                        self.repository.append_futures(current_future)
                current_options, previous_options, stored_future, previous_future = self.repository.observations_at(
                    evaluation, self.config.lookback_bars
                )
                option_component = score_options(current_options, previous_options, evaluation, self.config)
                future_component = score_futures(stored_future, previous_future, evaluation, self.config)
                spot_frame = spot_candles if spot_candles is not None else self._spot_candles(underlying.security_id, evaluation)
                spot_component = score_spot_trend(spot_frame, evaluation, self.config)
                snapshot = combine_regime_components(
                    option_component,
                    future_component,
                    spot_component,
                    evaluation,
                    self.config,
                )
            except (DhanAPIError, OSError, ValueError, KeyError, TypeError) as error:
                snapshot = insufficient_regime(
                    evaluation,
                    reason=f"NIFTY OI collection failed safely: {error}",
                )
            self.repository.append_regime(snapshot)
            self._last_evaluation = evaluation
            return snapshot


def build_oi_service_from_environment(
    dhan_config: DhanConfig,
    root: Path,
    *,
    config: NiftyOiConfig | None = None,
) -> DhanNiftyOiService:
    return DhanNiftyOiService(
        DhanClient(dhan_config),
        OiRegimeRepository(root),
        root,
        config=config,
    )
