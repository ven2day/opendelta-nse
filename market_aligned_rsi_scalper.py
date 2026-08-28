from __future__ import annotations

import copy
import csv
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from main import IST
from nifty_oi_regime import NiftyOiConfig, score_spot_trend
from recovery_backtest import calculate_ema, calculate_session_vwap, calculate_wilder_rsi


STRATEGY_KEY = "market_aligned_rsi_scalper"
STRATEGY_NAME = "Market-Aligned RSI Scalper"
STRATEGY_VERSION = "market-aligned-rsi-scalper-1.0.0"
STRATEGY_DESCRIPTION = (
    "High-selectivity RSI scalping aligned with NIFTY, sector, breadth, "
    "relative strength, RVOL, liquidity and optional OI context."
)

DATA_UNAVAILABLE_REASON_CODES = frozenset({
    "MISSING_STOCK_DATA",
    "STALE_STOCK_DATA",
    "MISSING_NIFTY_DATA",
    "MISSING_SECTOR_MAPPING",
    "MISSING_SECTOR_DATA",
    "INSUFFICIENT_SECTOR_MEMBERS",
    "MISSING_BREADTH_DATA",
    "INSUFFICIENT_BREADTH_SYMBOLS",
})

REASON_MESSAGES = {
    "MISSING_STOCK_DATA": "Required completed stock candles were unavailable.",
    "STALE_STOCK_DATA": "The latest completed stock candle was stale at the candidate timestamp.",
    "TIME_WINDOW_FAILED": "The candidate timestamp was outside the configured entry window.",
    "MISSING_NIFTY_DATA": "Completed NIFTY context was unavailable at the candidate timestamp.",
    "MISSING_SECTOR_MAPPING": "No sector mapping was found for the candidate symbol.",
    "MISSING_SECTOR_DATA": "No completed sector-member observations were available.",
    "INSUFFICIENT_SECTOR_MEMBERS": "Too few completed sector-member observations were available.",
    "MISSING_BREADTH_DATA": "No completed breadth-universe observations were available.",
    "INSUFFICIENT_BREADTH_SYMBOLS": "Too few completed breadth-universe observations were available.",
    "NIFTY_GATE_FAILED": "NIFTY Trend Score was below the configured threshold.",
    "SECTOR_GATE_FAILED": "Sector bullish percentage was below the configured threshold.",
    "BREADTH_GATE_FAILED": "Market breadth was below the configured threshold.",
    "RELATIVE_STRENGTH_FAILED": "The stock did not outperform both its sector and NIFTY.",
    "RSI_GATE_FAILED": "Signal RSI was outside the configured recovery range.",
    "VWAP_FAILED": "Price was not above session VWAP.",
    "EMA_FAILED": "EMA9 was not above EMA20 and rising.",
    "RVOL_FAILED": "RVOL was below the configured threshold.",
    "LIQUIDITY_FAILED": "Historical traded-value or range-quality liquidity checks failed.",
    "ROOM_TO_TARGET_FAILED": "Available price room was below the configured target.",
    "ALIGNMENT_SCORE_FAILED": "Market Alignment Score was below the configured threshold.",
    "OI_GATE_FAILED": "The configured OI policy rejected the candidate.",
    "ACCEPTED": "All required candidate gates passed.",
}


@dataclass(frozen=True)
class MarketAlignedConfig:
    rsi_length: int = 14
    signal_rsi_maximum: float = 50.0
    ema_fast: int = 9
    ema_slow: int = 20
    rvol_period: int = 20
    minimum_rvol: float = 1.5
    relative_strength_lookback_bars: int = 3
    room_lookback_bars: int = 20
    target_pct: float = 0.5
    minimum_nifty_trend_score: float = 25.0
    minimum_sector_bullish_pct: float = 50.0
    minimum_breadth_pct: float = 45.0
    minimum_breadth_symbols: int = 10
    minimum_sector_members: int = 2
    minimum_average_traded_value: float = 100_000.0
    maximum_intrabar_range_pct: float = 5.0
    minimum_alignment_score: float = 85.0
    stale_data_seconds: int = 360
    entry_start_time: str = "09:20"
    last_entry_time: str = "14:45"
    sector_by_symbol: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> "MarketAlignedConfig":
        if not 0 <= self.signal_rsi_maximum <= 100:
            raise ValueError("Signal RSI maximum must be between 0 and 100")
        if self.ema_fast >= self.ema_slow:
            raise ValueError("Market-Aligned EMA fast length must be below EMA slow length")
        if self.minimum_rvol <= 0:
            raise ValueError("Market-Aligned minimum RVOL must be positive")
        if not 0 <= self.minimum_breadth_pct <= 100:
            raise ValueError("Market breadth threshold must be between 0 and 100")
        if not 0 <= self.minimum_sector_bullish_pct <= 100:
            raise ValueError("Sector bullish threshold must be between 0 and 100")
        if not 0 <= self.minimum_alignment_score <= 100:
            raise ValueError("Market Alignment Score threshold must be between 0 and 100")
        if self.minimum_breadth_symbols < 1 or self.minimum_sector_members < 1:
            raise ValueError("Breadth and sector coverage requirements must be positive")
        if self.stale_data_seconds < 1:
            raise ValueError("Market context stale-data tolerance must be positive")
        try:
            entry_start = datetime_time.fromisoformat(self.entry_start_time)
            last_entry = datetime_time.fromisoformat(self.last_entry_time)
        except ValueError as error:
            raise ValueError("Market context entry times must use HH:MM") from error
        if entry_start >= last_entry:
            raise ValueError("Market context entry start must be below last entry time")
        return self

    def public(self) -> dict[str, Any]:
        values = asdict(self)
        values["sector_by_symbol"] = dict(sorted(self.sector_by_symbol.items()))
        return values


def _as_ist(value: datetime | str | pd.Timestamp) -> datetime:
    parsed = pd.Timestamp(value)
    parsed = parsed.tz_localize(IST) if parsed.tzinfo is None else parsed.tz_convert(IST)
    return parsed.to_pydatetime()


def _finite(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _pct_change(current: float, previous: float) -> float | None:
    if not math.isfinite(current) or not math.isfinite(previous) or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _completed(frame: pd.DataFrame, timestamp: datetime) -> pd.DataFrame:
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        return pd.DataFrame()
    data = frame.copy().sort_index()
    data.index = data.index.tz_localize(IST) if data.index.tz is None else data.index.tz_convert(IST)
    return data.loc[data.index <= pd.Timestamp(timestamp)]


def load_sector_mapping(path: Path | None) -> dict[str, str]:
    """Load an operations-supplied sector map without embedding symbol classifications."""
    if path is None or not path.is_file():
        return {}
    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Market sector JSON must contain a symbol-to-sector object")
        rows = payload.items()
    else:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headings = {
                str(name).strip().casefold(): str(name)
                for name in (reader.fieldnames or [])
            }
            symbol_heading = headings.get("symbol")
            sector_heading = headings.get("sector") or headings.get("industry")
            if not symbol_heading or not sector_heading:
                raise ValueError(
                    "Market sector CSV must contain Symbol and Sector or Industry columns"
                )
            rows = [
                (row.get(symbol_heading), row.get(sector_heading))
                for row in reader
            ]
    return {
        str(symbol).strip().upper().removesuffix(".NS"): str(sector).strip()
        for symbol, sector in rows
        if str(symbol or "").strip() and str(sector or "").strip()
    }


def _return_at(frame: pd.DataFrame, timestamp: datetime, bars: int) -> tuple[float | None, str | None]:
    data = _completed(frame, timestamp)
    if len(data) <= bars:
        return None, None
    close = pd.to_numeric(data["Close"], errors="coerce")
    value = _pct_change(float(close.iloc[-1]), float(close.iloc[-(bars + 1)]))
    return value, _as_ist(data.index[-1]).isoformat()


def _breadth_at(
    frames: Mapping[str, pd.DataFrame],
    timestamp: datetime,
    config: MarketAlignedConfig,
) -> dict[str, Any]:
    bullish = 0
    observed = 0
    latest_sources: list[datetime] = []
    for frame in frames.values():
        data = _completed(frame, timestamp)
        if len(data) < config.ema_slow:
            continue
        latest = _as_ist(data.index[-1])
        age = (timestamp - latest).total_seconds()
        if age < 0 or age > config.stale_data_seconds:
            continue
        close = pd.to_numeric(data["Close"], errors="coerce")
        ema = calculate_ema(close, config.ema_slow)
        if not math.isfinite(float(close.iloc[-1])) or not math.isfinite(float(ema.iloc[-1])):
            continue
        observed += 1
        bullish += int(float(close.iloc[-1]) >= float(ema.iloc[-1]))
        latest_sources.append(latest)
    if observed < config.minimum_breadth_symbols:
        return {
            "available": False,
            "attemptedSymbols": len(frames),
            "observedSymbols": observed,
            "requiredSymbols": config.minimum_breadth_symbols,
            "reason": "Insufficient point-in-time breadth coverage",
        }
    breadth_pct = bullish / observed * 100.0
    return {
        "available": True,
        "breadthPct": _finite(breadth_pct),
        "attemptedSymbols": len(frames),
        "observedSymbols": observed,
        "bullishSymbols": bullish,
        "notBearish": breadth_pct >= config.minimum_breadth_pct,
        "sourceTimestamp": min(latest_sources).isoformat(),
    }


def _sector_at(
    symbol: str,
    frames: Mapping[str, pd.DataFrame],
    timestamp: datetime,
    config: MarketAlignedConfig,
) -> dict[str, Any]:
    sector = config.sector_by_symbol.get(symbol)
    if not sector:
        return {
            "available": False,
            "mappingFound": False,
            "reason": "No configured sector mapping for symbol",
        }
    peer_returns: list[float] = []
    sources: list[str] = []
    for peer, frame in frames.items():
        if config.sector_by_symbol.get(peer) != sector:
            continue
        peer_return, source = _return_at(
            frame, timestamp, config.relative_strength_lookback_bars
        )
        if peer_return is not None and source is not None:
            age = (timestamp - _as_ist(source)).total_seconds()
            if age < 0 or age > config.stale_data_seconds:
                continue
            peer_returns.append(peer_return)
            sources.append(source)
    if len(peer_returns) < config.minimum_sector_members:
        return {
            "available": False,
            "mappingFound": True,
            "sector": sector,
            "observedMembers": len(peer_returns),
            "requiredMembers": config.minimum_sector_members,
            "reason": "Insufficient point-in-time sector coverage",
        }
    sector_return = float(np.mean(peer_returns))
    bullish_pct = sum(value > 0 for value in peer_returns) / len(peer_returns) * 100.0
    return {
        "available": True,
        "mappingFound": True,
        "sector": sector,
        "returnPct": _finite(sector_return),
        "bullish": sector_return > 0 and bullish_pct >= config.minimum_sector_bullish_pct,
        "bullishPct": _finite(bullish_pct),
        "requiredBullishPct": config.minimum_sector_bullish_pct,
        "observedMembers": len(peer_returns),
        "sourceTimestamp": min(sources),
    }


def evaluate_market_alignment(
    trade: Mapping[str, Any],
    *,
    symbol_frame: pd.DataFrame,
    nifty_frame: pd.DataFrame,
    universe_frames: Mapping[str, pd.DataFrame],
    config: MarketAlignedConfig,
    breadth_frames: Mapping[str, pd.DataFrame] | None = None,
    sector_frames: Mapping[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Evaluate every candidate gate and retain a complete causal diagnostic."""
    signal_timestamp = _as_ist(trade.get("signalTimestamp") or trade["entryTimestamp"])
    symbol = str(trade.get("symbol") or "")
    data = _completed(symbol_frame, signal_timestamp)
    base_diagnostic: dict[str, Any] = {
        "candidateTimestamp": signal_timestamp.isoformat(),
        "symbol": symbol,
        "tradeId": trade.get("tradeId"),
        "rsiArmTimestamp": trade.get("rsiArmTimestamp"),
        "rsiAtArm": _finite(trade.get("rsiArmValue"), 6),
        "previousRsi": None,
        "signalRsi": _finite(trade.get("rsiAtEntry"), 6),
        "timeWindowPassed": False,
        "niftyDataAvailable": False,
        "niftyTrendScore": None,
        "niftyPass": False,
        "sectorMappingFound": False,
        "sectorName": None,
        "sectorDataAvailable": False,
        "sectorMemberCount": 0,
        "sectorRequiredMembers": config.minimum_sector_members,
        "sectorBullishPct": None,
        "sectorPass": False,
        "breadthDataAvailable": False,
        "breadthSymbolCount": 0,
        "breadthRequiredSymbols": config.minimum_breadth_symbols,
        "breadthPct": None,
        "breadthPass": False,
        "relativeStrengthValue": None,
        "relativeStrengthPass": False,
        "price": None,
        "sessionVwap": None,
        "priceAboveVwap": False,
        "vwapPass": False,
        "emaFastValue": None,
        "emaSlowValue": None,
        "ema9AboveEma20": False,
        "ema9AboveEma20Pass": False,
        "ema9Rising": False,
        "ema9RisingPass": False,
        "emaPass": False,
        "rvolValue": None,
        "rvolPass": False,
        "liquidityValue": None,
        "liquidityPass": False,
        "roomToTargetValue": None,
        "roomToTargetPass": False,
        "oiMode": "NOT_SET",
        "oiResult": "NOT_REACHED",
        "alignmentScore": 0.0,
        "requiredScore": config.minimum_alignment_score,
        "scorePass": False,
        "rejectionReasons": [],
        "rejectionReasonDetails": [],
        "finalStatus": "SKIPPED_DATA_UNAVAILABLE",
        "executed": False,
        "sourceTimestamps": {
            "stock": None,
            "nifty": None,
            "sector": None,
            "breadth": None,
            "oi": None,
        },
    }

    def rejected_for_stock(code: str) -> dict[str, Any]:
        diagnostic = copy.deepcopy(base_diagnostic)
        diagnostic["rejectionReasons"] = [code]
        diagnostic["rejectionReasonDetails"] = [
            {"code": code, "message": REASON_MESSAGES[code]}
        ]
        return {
            "allowed": False,
            "score": 0.0,
            "decision": "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA",
            "reason": REASON_MESSAGES[code],
            "sourceTimestamp": diagnostic["sourceTimestamps"]["stock"],
            "gates": {},
            "candidateDiagnostic": diagnostic,
        }

    if len(data) < max(config.ema_slow, config.rvol_period, config.room_lookback_bars + 1):
        return rejected_for_stock("MISSING_STOCK_DATA")
    source_timestamp = _as_ist(data.index[-1])
    base_diagnostic["sourceTimestamps"]["stock"] = source_timestamp.isoformat()
    age = (signal_timestamp - source_timestamp).total_seconds()
    if age < 0 or age > config.stale_data_seconds:
        return rejected_for_stock("STALE_STOCK_DATA")

    close = pd.to_numeric(data["Close"], errors="coerce")
    high = pd.to_numeric(data["High"], errors="coerce")
    low = pd.to_numeric(data["Low"], errors="coerce")
    volume = pd.to_numeric(data["Volume"], errors="coerce")
    ema_fast = calculate_ema(close, config.ema_fast)
    ema_slow = calculate_ema(close, config.ema_slow)
    volume_average = calculate_ema(volume, config.rvol_period)
    session_vwap = calculate_session_vwap(data)
    rsi = calculate_wilder_rsi(close, config.rsi_length)
    current_close = float(close.iloc[-1])
    current_high = float(high.iloc[-1])
    current_low = float(low.iloc[-1])
    current_volume = float(volume.iloc[-1])
    rvol = current_volume / float(volume_average.iloc[-1]) if float(volume_average.iloc[-1]) > 0 else math.nan
    prior_resistance = float(high.iloc[-(config.room_lookback_bars + 1):-1].max())
    room_pct = _pct_change(prior_resistance, current_close)
    traded_value = (close * volume).iloc[-config.rvol_period:].median()
    range_proxy_pct = (current_high - current_low) / current_close * 100.0 if current_close > 0 else math.inf
    nifty = score_spot_trend(
        nifty_frame,
        signal_timestamp,
        NiftyOiConfig(stale_data_seconds=config.stale_data_seconds),
    )
    breadth = _breadth_at(
        breadth_frames if breadth_frames is not None else universe_frames,
        signal_timestamp,
        config,
    )
    sector = _sector_at(
        symbol,
        sector_frames if sector_frames is not None else universe_frames,
        signal_timestamp,
        config,
    )
    stock_return, _ = _return_at(
        symbol_frame, signal_timestamp, config.relative_strength_lookback_bars
    )
    nifty_return, _ = _return_at(
        nifty_frame, signal_timestamp, config.relative_strength_lookback_bars
    )
    sector_return = sector.get("returnPct") if sector.get("available") else None
    rsi_at_entry = _finite(trade.get("rsiAtEntry"))
    entry_start = datetime_time.fromisoformat(config.entry_start_time)
    last_entry = datetime_time.fromisoformat(config.last_entry_time)
    time_window_passed = entry_start <= signal_timestamp.time().replace(tzinfo=None) < last_entry
    ema_fast_value = float(ema_fast.iloc[-1])
    ema_slow_value = float(ema_slow.iloc[-1])
    ema_fast_previous = float(ema_fast.iloc[-2])
    ema_above = (
        math.isfinite(ema_fast_value)
        and math.isfinite(ema_slow_value)
        and ema_fast_value > ema_slow_value
    )
    ema_rising = math.isfinite(ema_fast_value) and math.isfinite(ema_fast_previous) and ema_fast_value > ema_fast_previous
    vwap_value = float(session_vwap.iloc[-1])
    vwap_pass = math.isfinite(vwap_value) and current_close > vwap_value
    traded_value_pass = math.isfinite(float(traded_value)) and float(traded_value) >= config.minimum_average_traded_value
    range_quality_pass = math.isfinite(range_proxy_pct) and range_proxy_pct <= config.maximum_intrabar_range_pct
    relative_strength_value = None
    if stock_return is not None and nifty_return is not None and sector_return is not None:
        relative_strength_value = stock_return - max(nifty_return, float(sector_return))
    relative_strength_pass = relative_strength_value is not None and relative_strength_value > 0
    gates = {
        "timeWindow": time_window_passed,
        "niftyTrend": bool(nifty.get("available")) and float(nifty.get("score", -100)) >= config.minimum_nifty_trend_score,
        "sectorBullish": bool(sector.get("available")) and bool(sector.get("bullish")),
        "breadthNotBearish": bool(breadth.get("available")) and bool(breadth.get("notBearish")),
        "relativeStrength": relative_strength_pass,
        "rsiRecovery": rsi_at_entry is not None and 40.0 < rsi_at_entry <= config.signal_rsi_maximum,
        "aboveSessionVwap": vwap_pass,
        "emaTrend": ema_above and ema_rising,
        "rvol": math.isfinite(rvol) and rvol >= config.minimum_rvol,
        "roomToTarget": room_pct is not None and room_pct >= config.target_pct,
        "liquidity": traded_value_pass,
        "spreadQuality": range_quality_pass,
    }
    weights = {
        "niftyTrend": 15.0,
        "sectorBullish": 10.0,
        "breadthNotBearish": 10.0,
        "relativeStrength": 15.0,
        "rsiRecovery": 10.0,
        "aboveSessionVwap": 10.0,
        "emaTrend": 10.0,
        "rvol": 10.0,
        "roomToTarget": 5.0,
        "liquidity": 3.0,
        "spreadQuality": 2.0,
    }
    score = sum(weight for gate, weight in weights.items() if gates[gate])
    failed = [gate for gate, passed in gates.items() if not passed]
    reason_codes: list[str] = []
    if not time_window_passed:
        reason_codes.append("TIME_WINDOW_FAILED")
    if not nifty.get("available"):
        reason_codes.append("MISSING_NIFTY_DATA")
    elif not gates["niftyTrend"]:
        reason_codes.append("NIFTY_GATE_FAILED")
    if not sector.get("mappingFound"):
        reason_codes.append("MISSING_SECTOR_MAPPING")
    elif not sector.get("available"):
        reason_codes.append(
            "MISSING_SECTOR_DATA"
            if int(sector.get("observedMembers", 0)) == 0
            else "INSUFFICIENT_SECTOR_MEMBERS"
        )
    elif not gates["sectorBullish"]:
        reason_codes.append("SECTOR_GATE_FAILED")
    if not breadth.get("available"):
        reason_codes.append(
            "MISSING_BREADTH_DATA"
            if int(breadth.get("observedSymbols", 0)) == 0
            else "INSUFFICIENT_BREADTH_SYMBOLS"
        )
    elif not gates["breadthNotBearish"]:
        reason_codes.append("BREADTH_GATE_FAILED")
    if nifty.get("available") and sector.get("available") and not gates["relativeStrength"]:
        reason_codes.append("RELATIVE_STRENGTH_FAILED")
    if not gates["rsiRecovery"]:
        reason_codes.append("RSI_GATE_FAILED")
    if not gates["aboveSessionVwap"]:
        reason_codes.append("VWAP_FAILED")
    if not gates["emaTrend"]:
        reason_codes.append("EMA_FAILED")
    if not gates["rvol"]:
        reason_codes.append("RVOL_FAILED")
    if not gates["liquidity"] or not gates["spreadQuality"]:
        reason_codes.append("LIQUIDITY_FAILED")
    if not gates["roomToTarget"]:
        reason_codes.append("ROOM_TO_TARGET_FAILED")
    if score < config.minimum_alignment_score:
        reason_codes.append("ALIGNMENT_SCORE_FAILED")
    allowed = not reason_codes
    unavailable = any(code in DATA_UNAVAILABLE_REASON_CODES for code in reason_codes)
    decision = "MARKET_ALIGNMENT_ACCEPTED" if allowed else (
        "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA"
        if unavailable
        else "SKIPPED_MARKET_ALIGNMENT"
    )
    source_values = [
        value
        for value in (
            source_timestamp.isoformat(),
            nifty.get("sourceTimestamp"),
            sector.get("sourceTimestamp"),
            breadth.get("sourceTimestamp"),
        )
        if value
    ]
    diagnostic = {
        **base_diagnostic,
        "previousRsi": _finite(rsi.iloc[-2], 6),
        "timeWindowPassed": time_window_passed,
        "niftyDataAvailable": bool(nifty.get("available")),
        "niftyTrendScore": _finite(nifty.get("score")),
        "niftyPass": gates["niftyTrend"],
        "sectorMappingFound": bool(sector.get("mappingFound")),
        "sectorName": sector.get("sector"),
        "sectorDataAvailable": bool(sector.get("available")),
        "sectorMemberCount": int(sector.get("observedMembers", 0)),
        "sectorBullishPct": _finite(sector.get("bullishPct")),
        "sectorPass": gates["sectorBullish"],
        "breadthDataAvailable": bool(breadth.get("available")),
        "breadthSymbolCount": int(breadth.get("observedSymbols", 0)),
        "breadthPct": _finite(breadth.get("breadthPct")),
        "breadthPass": gates["breadthNotBearish"],
        "relativeStrengthValue": _finite(relative_strength_value),
        "relativeStrengthPass": gates["relativeStrength"],
        "price": _finite(current_close),
        "sessionVwap": _finite(vwap_value),
        "priceAboveVwap": vwap_pass,
        "vwapPass": gates["aboveSessionVwap"],
        "emaFastValue": _finite(ema_fast_value),
        "emaSlowValue": _finite(ema_slow_value),
        "ema9AboveEma20": ema_above,
        "ema9AboveEma20Pass": ema_above,
        "ema9Rising": ema_rising,
        "ema9RisingPass": ema_rising,
        "emaPass": gates["emaTrend"],
        "rvolValue": _finite(rvol),
        "rvolPass": gates["rvol"],
        "liquidityValue": _finite(traded_value, 2),
        "liquidityPass": gates["liquidity"] and gates["spreadQuality"],
        "roomToTargetValue": _finite(room_pct),
        "roomToTargetPass": gates["roomToTarget"],
        "alignmentScore": _finite(score),
        "scorePass": score >= config.minimum_alignment_score,
        "rejectionReasons": reason_codes if reason_codes else ["ACCEPTED"],
        "rejectionReasonDetails": [
            {"code": code, "message": REASON_MESSAGES[code]}
            for code in (reason_codes if reason_codes else ["ACCEPTED"])
        ],
        "finalStatus": (
            "ACCEPTED"
            if allowed
            else "SKIPPED_DATA_UNAVAILABLE"
            if unavailable
            else "REJECTED_GATE"
        ),
        "sourceTimestamps": {
            "stock": source_timestamp.isoformat(),
            "nifty": nifty.get("sourceTimestamp"),
            "sector": sector.get("sourceTimestamp"),
            "breadth": breadth.get("sourceTimestamp"),
            "oi": None,
        },
    }
    return {
        "allowed": allowed,
        "score": _finite(score),
        "decision": decision,
        "reason": (
            REASON_MESSAGES["ACCEPTED"]
            if allowed
            else "; ".join(REASON_MESSAGES[code] for code in reason_codes)
        ),
        "sourceTimestamp": min(source_values) if source_values else None,
        "gates": gates,
        "niftyTrend": nifty,
        "sectorTrend": sector,
        "marketBreadth": breadth,
        "stockReturnPct": _finite(stock_return),
        "niftyReturnPct": _finite(nifty_return),
        "sectorReturnPct": _finite(sector_return),
        "rvol": _finite(rvol),
        "roomToTargetPct": _finite(room_pct),
        "medianTradedValue": _finite(traded_value, 2),
        "historicalRangeQualityPct": _finite(range_proxy_pct),
        "historicalSpreadNote": "Intraday OHLC has no bid/ask history; candle range is a conservative data-quality proxy.",
        "failedGates": failed,
        "candidateDiagnostic": diagnostic,
    }


def apply_market_alignment_chronologically(
    results: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    nifty_frame: pd.DataFrame,
    config: MarketAlignedConfig,
    breadth_frames: Mapping[str, pd.DataFrame] | None = None,
    sector_frames: Mapping[str, pd.DataFrame] | None = None,
    oi_mode: str = "OFF",
) -> list[dict[str, Any]]:
    """Filter existing RSI candidates without creating a BUY candidate."""
    output = [copy.deepcopy(dict(result)) for result in results]
    candidates: list[tuple[datetime, str, str, int, dict[str, Any]]] = []
    for result_index, result in enumerate(output):
        symbol = str(result.get("symbol") or "")
        for trade in result.get("trades", []):
            timestamp = trade.get("signalTimestamp") or trade.get("entryTimestamp")
            if timestamp:
                candidates.append((_as_ist(timestamp), symbol, str(trade.get("tradeId") or ""), result_index, dict(trade)))
        result["trades"] = []
        result["marketAlignmentSkippedSignals"] = []
        result["candidateDiagnostics"] = []
        result["rsiArmedCount"] = sum(
            1 for event in result.get("events", []) if event.get("type") == "ARMED"
        )
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    for _, symbol, _, result_index, trade in candidates:
        evaluation = evaluate_market_alignment(
            trade,
            symbol_frame=frames_by_symbol.get(symbol, pd.DataFrame()),
            nifty_frame=nifty_frame,
            universe_frames=frames_by_symbol,
            config=config,
            breadth_frames=breadth_frames,
            sector_frames=sector_frames,
        )
        diagnostic = copy.deepcopy(evaluation["candidateDiagnostic"])
        diagnostic["oiMode"] = oi_mode
        diagnostic["oiResult"] = "NOT_EVALUATED" if oi_mode == "OFF" else "NOT_REACHED"
        diagnostic["executed"] = bool(evaluation["allowed"] and oi_mode == "OFF")
        enriched = {
            **trade,
            "strategyMode": STRATEGY_KEY,
            "strategyKey": STRATEGY_KEY,
            "strategyName": STRATEGY_NAME,
            "strategyVersion": STRATEGY_VERSION,
            "marketAlignmentScore": evaluation["score"],
            "marketAlignmentDecision": evaluation["decision"],
            "marketAlignmentSourceTimestamp": evaluation["sourceTimestamp"],
            "marketAlignment": evaluation,
            "candidateDiagnostic": diagnostic,
        }
        target = output[result_index]
        target["candidateDiagnostics"].append(diagnostic)
        if evaluation["allowed"]:
            target["trades"].append(enriched)
        else:
            target["marketAlignmentSkippedSignals"].append({
                "tradeId": trade.get("tradeId"),
                "symbol": symbol,
                "signalTimestamp": trade.get("signalTimestamp") or trade.get("entryTimestamp"),
                "reason": evaluation["reason"],
                "status": evaluation["decision"],
                "marketAlignment": evaluation,
                "candidateDiagnostic": diagnostic,
                "hypotheticalOutcome": enriched,
            })
        target["events"] = [
            event for event in target.get("events", [])
            if event.get("type") != "BUY" or event.get("tradeId") in {row.get("tradeId") for row in target["trades"]}
        ]
    return output
