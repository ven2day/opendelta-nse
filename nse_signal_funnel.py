from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import pandas as pd

from live_signals import evaluate_latest_recovery
from main import IST
from market_aligned_vwap_pullback_scalper import (
    STRATEGY_KEY as VWAP_PULLBACK_KEY,
    STRATEGY_NAME as VWAP_PULLBACK_NAME,
    STRATEGY_VERSION as VWAP_PULLBACK_VERSION,
    VwapPullbackConfig,
    detect_pullback_candidates,
)
from recovery_backtest import RecoveryConfig


FUNNEL_VERSION = "nse-signal-funnel-1.0.0"
RSI_RECOVERY_KEY = "rsi_recovery_v1_1"


@dataclass(frozen=True)
class NseSignalFunnelConfig:
    maximum_trade_ready: int = 2
    maximum_watch: int = 3
    maximum_trades_per_day: int = 5
    maximum_concurrent: int = 2
    quantity_per_trade: int = 50
    minimum_signal_score: float = 70.0
    watch_score: float = 60.0
    minimum_rvol: float = 1.2
    stop_atr: float = 1.0
    reward_risk: float = 1.5
    last_entry_time: str = "14:45"
    buy_cost_bps: float = 5.0
    sell_cost_bps: float = 5.0
    slippage_bps: float = 2.0

    def validate(self) -> NseSignalFunnelConfig:
        if self.maximum_trade_ready < 1 or self.maximum_watch < 0:
            raise ValueError("Signal selection limits are invalid")
        if self.maximum_trades_per_day < self.maximum_trade_ready:
            raise ValueError("Daily trade limit cannot be below the trade-ready limit")
        if self.maximum_concurrent < 1 or self.quantity_per_trade < 1:
            raise ValueError("Concurrent signals and quantity must be positive")
        if not 0 <= self.watch_score <= self.minimum_signal_score <= 100:
            raise ValueError("Signal score thresholds must be ordered within 0-100")
        if self.minimum_rvol <= 0 or self.stop_atr <= 0 or self.reward_risk <= 0:
            raise ValueError("RVOL, stop, and reward:risk settings must be positive")
        datetime_time.fromisoformat(self.last_entry_time)
        return self

    def public(self) -> dict[str, Any]:
        return {
            "maximumTradeReady": self.maximum_trade_ready,
            "maximumWatch": self.maximum_watch,
            "maximumTradesPerDay": self.maximum_trades_per_day,
            "maximumConcurrent": self.maximum_concurrent,
            "quantityPerTrade": self.quantity_per_trade,
            "minimumSignalScore": self.minimum_signal_score,
            "watchScore": self.watch_score,
            "minimumRvol": self.minimum_rvol,
            "stopAtr": self.stop_atr,
            "rewardRisk": self.reward_risk,
            "lastEntryTime": self.last_entry_time,
            "executionModel": "NEXT_BAR_OPEN",
            "buyCostBps": self.buy_cost_bps,
            "sellCostBps": self.sell_cost_bps,
            "slippageBps": self.slippage_bps,
        }


class SignalDetector(Protocol):
    key: str
    name: str
    version: str

    def evaluate(self, frame: pd.DataFrame, as_of: pd.Timestamp, config: NseSignalFunnelConfig) -> dict[str, Any]: ...


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric, digits) if math.isfinite(numeric) else None


def _as_ist(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _slice(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    index = pd.DatetimeIndex(frame.index)
    index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)
    data = frame.copy()
    data.index = index
    return data.loc[data.index <= as_of].sort_index()


def _evaluation_id(symbol: str, strategy: str, timestamp: pd.Timestamp) -> str:
    identity = f"{FUNNEL_VERSION}|{strategy}|{symbol.upper()}|{timestamp.isoformat()}"
    return "NSEEV-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24].upper()


class RsiRecoveryDetector:
    key = RSI_RECOVERY_KEY
    name = "RSI Recovery v1.1"
    version = "rsi-recovery-1.1.0"
    trade_ready = True

    def __init__(self) -> None:
        self.recovery_config = RecoveryConfig()

    def evaluate(self, frame: pd.DataFrame, as_of: pd.Timestamp, config: NseSignalFunnelConfig) -> dict[str, Any]:
        data = _slice(frame, as_of)
        required = ["Open", "High", "Low", "Close", "Volume"]
        if len(data) < 24 or any(column not in data for column in required):
            return {"ready": False, "reasons": ["INSUFFICIENT_WARMUP"]}
        recovery = evaluate_latest_recovery(data[required], self.recovery_config)
        if recovery is None:
            return {"ready": False, "reasons": ["RSI_RECOVERY_NOT_TRIGGERED"]}
        confirmation_score = int(recovery["confirmationScore"])
        quality = min(100.0, 64.0 + confirmation_score * 9.0 + min(float(recovery["barsArmToRecovery"]), 10.0))
        return {
            "ready": True,
            "reasons": [],
            "strategyQuality": round(quality, 4),
            "trigger": {
                "rsi": _finite(recovery.get("rsi")),
                "rsiArmValue": _finite(recovery.get("rsiArmValue")),
                "rsiArmTimestamp": _as_ist(recovery["rsiArmTimestamp"]).isoformat(),
                "barsArmToRecovery": int(recovery["barsArmToRecovery"]),
                "confirmationScore": confirmation_score,
                "emaConfirmation": bool(recovery["emaConfirmation"]),
                "vwapConfirmation": bool(recovery["vwapConfirmation"]),
                "volumeConfirmation": bool(recovery["volumeConfirmation"]),
            },
        }


class MarketAlignedVwapPullbackDetector:
    key = VWAP_PULLBACK_KEY
    name = VWAP_PULLBACK_NAME
    version = VWAP_PULLBACK_VERSION
    trade_ready = False

    def __init__(self) -> None:
        self.strategy_config = VwapPullbackConfig()

    def evaluate(self, frame: pd.DataFrame, as_of: pd.Timestamp, config: NseSignalFunnelConfig) -> dict[str, Any]:
        data = _slice(frame, as_of)
        required = [
            "Open", "High", "Low", "Close", "Volume", "RSI", "EMAFast",
            "EMASlow", "ATR", "SessionVWAP", "RVOL", "AverageTradedValue",
        ]
        if len(data) < 5 or any(column not in data for column in required):
            return {"ready": False, "reasons": ["INSUFFICIENT_WARMUP"]}
        prepared = data.copy()
        if "ReturnPct" not in prepared:
            prepared["ReturnPct"] = (
                prepared["Close"] / prepared["Close"].shift(self.strategy_config.relative_strength_lookback_bars) - 1.0
            ) * 100.0
        if "HighQualityTrigger" not in prepared:
            prepared["HighQualityTrigger"] = (
                prepared["Close"].gt(prepared["Open"])
                & (prepared["Close"] - prepared["Open"]).ge((prepared["High"] - prepared["Low"]) * 0.5)
                & (prepared["High"] - prepared["Low"]).le(prepared["ATR"] * 1.5)
            ).fillna(False)
        result = detect_pullback_candidates("FUNNEL", prepared, self.strategy_config)
        candidates = [
            candidate
            for candidate in result["candidates"]
            if _as_ist(candidate["signalTimestamp"]) == as_of
        ]
        if not candidates:
            return {"ready": False, "reasons": ["VWAP_PULLBACK_NOT_TRIGGERED"]}
        candidate = candidates[-1]
        rvol = float(candidate.get("rvol") or 0.0)
        quality = min(
            100.0,
            72.0
            + (10.0 if bool(candidate.get("highQualityTrigger")) else 0.0)
            + min(max(rvol - self.strategy_config.minimum_trigger_rvol, 0.0) * 8.0, 8.0),
        )
        return {
            "ready": True,
            "reasons": [],
            "strategyQuality": round(quality, 4),
            "trigger": {
                "armTimestamp": candidate.get("armTimestamp"),
                "rsiAtArm": _finite(candidate.get("rsiAtArm")),
                "triggerRsi": _finite(candidate.get("triggerRsi")),
                "rvol": _finite(candidate.get("rvol")),
                "sessionVwap": _finite(candidate.get("sessionVwap")),
                "emaFast": _finite(candidate.get("emaFast")),
                "emaSlow": _finite(candidate.get("emaSlow")),
                "pullbackReference": candidate.get("nearestPullbackReference"),
                "pullbackSwingLow": _finite(candidate.get("pullbackSwingLow")),
            },
        }


DETECTORS: tuple[SignalDetector, ...] = (RsiRecoveryDetector(), MarketAlignedVwapPullbackDetector())


def _plan(symbol: str, detector: SignalDetector, frame: pd.DataFrame, as_of: pd.Timestamp, activity: Mapping[str, Any], evaluation: Mapping[str, Any], config: NseSignalFunnelConfig) -> dict[str, Any]:
    row = _slice(frame, as_of).iloc[-1]
    close = float(row["Close"])
    atr = float(row["ATR"])
    risk = atr * config.stop_atr
    activity_score = float(activity.get("score") or 0.0)
    strategy_quality = float(evaluation.get("strategyQuality") or 0.0)
    signal_score = min(100.0, strategy_quality * 0.60 + activity_score * 0.40)
    stop = close - risk
    target = close + risk * config.reward_risk
    event_id = _evaluation_id(symbol, detector.key, as_of)
    return {
        "eventId": event_id,
        "signalId": event_id.replace("NSEEV-", "NSESIG-"),
        "symbol": symbol,
        "strategyKey": detector.key,
        "strategyName": detector.name,
        "strategyVersion": detector.version,
        "strategyStatus": "ACTIVE" if bool(getattr(detector, "trade_ready", False)) else "RETIRED_RESEARCH_ONLY",
        "tradeReadyAllowed": bool(getattr(detector, "trade_ready", False)),
        "signalTimestamp": as_of.isoformat(),
        "executionModel": "NEXT_BAR_OPEN",
        "signalClose": round(close, 4),
        "estimatedEntry": round(close, 4),
        "estimatedStop": round(stop, 4),
        "estimatedTarget": round(target, 4),
        "riskPerShare": round(risk, 4),
        "rewardRisk": config.reward_risk,
        "quantity": config.quantity_per_trade,
        "estimatedCapital": round(close * config.quantity_per_trade, 2),
        "activityScore": round(activity_score, 4),
        "strategyQuality": round(strategy_quality, 4),
        "signalScore": round(signal_score, 4),
        "activityRank": activity.get("rank"),
        "rvol": _finite(activity.get("rollingRvol")),
        "relativeToNiftyPct": _finite(activity.get("relativeToNiftyPct")),
        "relativeToSectorPct": _finite(activity.get("relativeToSectorPct")),
        "trigger": dict(evaluation.get("trigger") or {}),
        "paperOnly": True,
        "liveOrdersEnabled": False,
    }


def build_nse_signal_funnel(
    feature_frames: Mapping[str, pd.DataFrame],
    ranked_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: object,
    config: NseSignalFunnelConfig | None = None,
) -> dict[str, Any]:
    settings = (config or NseSignalFunnelConfig()).validate()
    timestamp = _as_ist(as_of)
    eligible_rows = [dict(row) for row in ranked_rows if bool(row.get("eligible"))]
    eligible_by_symbol = {str(row["symbol"]): row for row in eligible_rows}
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    after_last_entry = timestamp.time().replace(tzinfo=None) > datetime_time.fromisoformat(settings.last_entry_time)
    for symbol, activity in eligible_by_symbol.items():
        frame = feature_frames.get(symbol)
        if frame is None or frame.empty:
            continue
        for detector in DETECTORS:
            evaluation = detector.evaluate(frame, timestamp, settings)
            reasons = list(evaluation.get("reasons") or [])
            if after_last_entry:
                reasons.append("AFTER_LAST_ENTRY_TIME")
            if not evaluation.get("ready") or reasons:
                for reason in reasons:
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                rejected.append({
                    "eventId": _evaluation_id(symbol, detector.key, timestamp),
                    "symbol": symbol,
                    "strategyKey": detector.key,
                    "strategyName": detector.name,
                    "signalTimestamp": timestamp.isoformat(),
                    "status": "REJECTED",
                    "reasons": reasons or ["SETUP_NOT_READY"],
                    "activityScore": activity.get("score"),
                    "paperOnly": True,
                })
                continue
            candidates.append(_plan(symbol, detector, frame, timestamp, activity, evaluation, settings))

    candidates.sort(key=lambda item: (-int(bool(item["tradeReadyAllowed"])), -float(item["signalScore"]), -float(item["activityScore"]), str(item["symbol"]), str(item["strategyKey"])))
    trade_ready: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    selected_symbols: set[str] = set()
    for candidate in candidates:
        score = float(candidate["signalScore"])
        if candidate["symbol"] in selected_symbols:
            candidate = {**candidate, "status": "REJECTED", "reasons": ["ONE_SIGNAL_PER_SYMBOL"]}
            rejected.append(candidate)
            rejection_counts["ONE_SIGNAL_PER_SYMBOL"] = rejection_counts.get("ONE_SIGNAL_PER_SYMBOL", 0) + 1
        elif bool(candidate["tradeReadyAllowed"]) and score >= settings.minimum_signal_score and len(trade_ready) < settings.maximum_trade_ready:
            candidate = {**candidate, "status": "TRADE_READY", "rank": len(trade_ready) + 1}
            trade_ready.append(candidate)
            selected_symbols.add(str(candidate["symbol"]))
        elif score >= settings.watch_score and len(watch) < settings.maximum_watch:
            candidate = {**candidate, "status": "WATCH", "rank": len(watch) + 1}
            watch.append(candidate)
            selected_symbols.add(str(candidate["symbol"]))
        else:
            if not bool(candidate["tradeReadyAllowed"]):
                reason = "RETIRED_STRATEGY_RESEARCH_ONLY"
            else:
                reason = "BELOW_SIGNAL_SCORE" if score < settings.watch_score else "SELECTION_CAP"
            rejected.append({**candidate, "status": "REJECTED", "reasons": [reason]})
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    return {
        "metadata": {
            "funnelVersion": FUNNEL_VERSION,
            "generatedAt": timestamp.isoformat(),
            "completedCandlesOnly": True,
            "paperOnly": True,
            "liveOrdersEnabled": False,
            "strategies": [
                {
                    "key": detector.key,
                    "name": detector.name,
                    "version": detector.version,
                    "tradeReadyAllowed": bool(getattr(detector, "trade_ready", False)),
                }
                for detector in DETECTORS
            ],
            "configuration": settings.public(),
        },
        "counts": {
            "tradeable": len(eligible_rows),
            "strategyEvaluations": len(eligible_rows) * len(DETECTORS),
            "validSetups": len(candidates),
            "tradeReady": len(trade_ready),
            "watch": len(watch),
            "rejected": len(rejected),
        },
        "tradeReady": trade_ready,
        "watch": watch,
        "rejected": rejected,
        "rejectionCounts": dict(sorted(rejection_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
    }


class NseSignalFunnelRepository:
    """Append-only, deduplicated audit history for accepted and rejected evaluations."""

    def __init__(self, database_path: Path) -> None:
        if not database_path.is_absolute():
            raise ValueError("Signal-funnel database path must be absolute")
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS nse_signal_funnel_events (
                    event_id TEXT PRIMARY KEY,
                    session_date TEXT NOT NULL,
                    signal_timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def persist(self, funnel: Mapping[str, Any]) -> int:
        metadata = dict(funnel.get("metadata") or {})
        generated = _as_ist(metadata.get("generatedAt") or datetime.now(IST))
        rows = [
            *(dict(item) for item in funnel.get("tradeReady", [])),
            *(dict(item) for item in funnel.get("watch", [])),
            *(dict(item) for item in funnel.get("rejected", [])),
        ]
        inserted = 0
        with self._lock, self._connect() as connection:
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO nse_signal_funnel_events (
                        event_id, session_date, signal_timestamp, symbol,
                        strategy_key, status, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["eventId"]),
                        generated.date().isoformat(),
                        str(row.get("signalTimestamp") or generated.isoformat()),
                        str(row["symbol"]),
                        str(row["strategyKey"]),
                        str(row["status"]),
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                        datetime.now(IST).isoformat(),
                    ),
                )
                inserted += int(cursor.rowcount == 1)
        return inserted

    def enforce_daily_controls_and_persist(
        self,
        funnel: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int]:
        """Apply the durable daily limits before recording the visible decision.

        A repeated refresh of the same deterministic event remains trade-ready and
        does not consume another slot. A later setup for the same symbol is held
        back for the rest of the session, which also prevents accidental duplicate
        paper positions when the scanner is refreshed every fifteen minutes.
        """
        controlled = deepcopy(dict(funnel))
        metadata = dict(controlled.get("metadata") or {})
        generated = _as_ist(metadata.get("generatedAt") or datetime.now(IST))
        session_date = generated.date().isoformat()
        configuration = dict(metadata.get("configuration") or {})
        daily_limit = max(1, int(configuration.get("maximumTradesPerDay") or 5))
        with self._lock, self._connect() as connection:
            existing_rows = connection.execute(
                """
                SELECT event_id, symbol FROM nse_signal_funnel_events
                WHERE session_date = ? AND status = 'TRADE_READY'
                """,
                (session_date,),
            ).fetchall()
        existing_ids = {str(row[0]) for row in existing_rows}
        accepted_symbols = {str(row[1]) for row in existing_rows}
        accepted_count = len(existing_ids)
        accepted_now: list[dict[str, Any]] = []
        demoted: list[dict[str, Any]] = []
        rejection_counts = dict(controlled.get("rejectionCounts") or {})
        for raw in controlled.get("tradeReady", []):
            signal = dict(raw)
            event_id = str(signal["eventId"])
            symbol = str(signal["symbol"])
            if event_id in existing_ids:
                accepted_now.append(signal)
                continue
            reason = None
            if symbol in accepted_symbols:
                reason = "ONE_TRADE_PER_SYMBOL_PER_DAY"
            elif accepted_count >= daily_limit:
                reason = "DAILY_TRADE_LIMIT"
            if reason is not None:
                demoted.append({**signal, "status": "REJECTED", "reasons": [reason]})
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                continue
            accepted_now.append(signal)
            existing_ids.add(event_id)
            accepted_symbols.add(symbol)
            accepted_count += 1

        for rank, signal in enumerate(accepted_now, start=1):
            signal["rank"] = rank
        controlled["tradeReady"] = accepted_now
        controlled["rejected"] = [*(dict(item) for item in controlled.get("rejected", [])), *demoted]
        controlled["rejectionCounts"] = dict(
            sorted(rejection_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        )
        counts = dict(controlled.get("counts") or {})
        counts["tradeReady"] = len(accepted_now)
        counts["rejected"] = len(controlled["rejected"])
        controlled["counts"] = counts
        controlled["metadata"] = {
            **metadata,
            "dailyTradeReadyAccepted": accepted_count,
            "dailyTradeReadyRemaining": max(0, daily_limit - accepted_count),
        }
        inserted = self.persist(controlled)
        return controlled, inserted

    def recent(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM nse_signal_funnel_events ORDER BY signal_timestamp DESC, event_id DESC LIMIT ?",
                (max(1, min(int(limit), 5_000)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
