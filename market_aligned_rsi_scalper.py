from __future__ import annotations

import copy
import csv
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from main import IST
from nifty_oi_regime import NiftyOiConfig, score_spot_trend
from recovery_backtest import calculate_ema, calculate_session_vwap


STRATEGY_KEY = "market_aligned_rsi_scalper"
STRATEGY_NAME = "Market-Aligned RSI Scalper"
STRATEGY_VERSION = "market-aligned-rsi-scalper-1.0.0"
STRATEGY_DESCRIPTION = (
    "High-selectivity RSI scalping aligned with NIFTY, sector, breadth, "
    "relative strength, RVOL, liquidity and optional OI context."
)


@dataclass(frozen=True)
class MarketAlignedConfig:
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
            if not reader.fieldnames or not {"symbol", "sector"}.issubset(reader.fieldnames):
                raise ValueError("Market sector CSV must contain symbol and sector columns")
            rows = ((row.get("symbol"), row.get("sector")) for row in reader)
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
        close = pd.to_numeric(data["Close"], errors="coerce")
        ema = calculate_ema(close, config.ema_slow)
        if not math.isfinite(float(close.iloc[-1])) or not math.isfinite(float(ema.iloc[-1])):
            continue
        observed += 1
        bullish += int(float(close.iloc[-1]) >= float(ema.iloc[-1]))
        latest_sources.append(_as_ist(data.index[-1]))
    if observed < config.minimum_breadth_symbols:
        return {
            "available": False,
            "observedSymbols": observed,
            "requiredSymbols": config.minimum_breadth_symbols,
            "reason": "Insufficient point-in-time breadth coverage",
        }
    breadth_pct = bullish / observed * 100.0
    return {
        "available": True,
        "breadthPct": _finite(breadth_pct),
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
        return {"available": False, "reason": "No configured sector mapping for symbol"}
    peer_returns: list[float] = []
    sources: list[str] = []
    for peer, frame in frames.items():
        if config.sector_by_symbol.get(peer) != sector:
            continue
        peer_return, source = _return_at(
            frame, timestamp, config.relative_strength_lookback_bars
        )
        if peer_return is not None and source is not None:
            peer_returns.append(peer_return)
            sources.append(source)
    if len(peer_returns) < config.minimum_sector_members:
        return {
            "available": False,
            "sector": sector,
            "observedMembers": len(peer_returns),
            "requiredMembers": config.minimum_sector_members,
            "reason": "Insufficient point-in-time sector coverage",
        }
    sector_return = float(np.mean(peer_returns))
    bullish_pct = sum(value > 0 for value in peer_returns) / len(peer_returns) * 100.0
    return {
        "available": True,
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
) -> dict[str, Any]:
    """Evaluate all non-OI gates using information completed at the signal timestamp."""
    signal_timestamp = _as_ist(trade.get("signalTimestamp") or trade["entryTimestamp"])
    symbol = str(trade.get("symbol") or "")
    data = _completed(symbol_frame, signal_timestamp)
    if len(data) < max(config.ema_slow, config.rvol_period, config.room_lookback_bars + 1):
        return {
            "allowed": False,
            "score": 0.0,
            "decision": "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA",
            "reason": "Insufficient completed stock candles for the configured gates",
            "sourceTimestamp": None,
            "gates": {},
        }
    source_timestamp = _as_ist(data.index[-1])
    age = (signal_timestamp - source_timestamp).total_seconds()
    if age < 0 or age > config.stale_data_seconds:
        return {
            "allowed": False,
            "score": 0.0,
            "decision": "SKIPPED_STALE_MARKET_ALIGNMENT_DATA",
            "reason": "Latest completed stock candle is stale",
            "sourceTimestamp": source_timestamp.isoformat(),
            "gates": {},
        }

    close = pd.to_numeric(data["Close"], errors="coerce")
    high = pd.to_numeric(data["High"], errors="coerce")
    low = pd.to_numeric(data["Low"], errors="coerce")
    volume = pd.to_numeric(data["Volume"], errors="coerce")
    ema_fast = calculate_ema(close, config.ema_fast)
    ema_slow = calculate_ema(close, config.ema_slow)
    volume_average = calculate_ema(volume, config.rvol_period)
    session_vwap = calculate_session_vwap(data)
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
    breadth = _breadth_at(universe_frames, signal_timestamp, config)
    sector = _sector_at(symbol, universe_frames, signal_timestamp, config)
    stock_return, _ = _return_at(
        symbol_frame, signal_timestamp, config.relative_strength_lookback_bars
    )
    nifty_return, _ = _return_at(
        nifty_frame, signal_timestamp, config.relative_strength_lookback_bars
    )
    sector_return = sector.get("returnPct") if sector.get("available") else None
    rsi_at_entry = _finite(trade.get("rsiAtEntry"))
    gates = {
        "niftyTrend": bool(nifty.get("available")) and float(nifty.get("score", -100)) >= config.minimum_nifty_trend_score,
        "sectorBullish": bool(sector.get("available")) and bool(sector.get("bullish")),
        "breadthNotBearish": bool(breadth.get("available")) and bool(breadth.get("notBearish")),
        "relativeStrength": (
            stock_return is not None
            and nifty_return is not None
            and sector_return is not None
            and stock_return > nifty_return
            and stock_return > float(sector_return)
        ),
        "rsiRecovery": rsi_at_entry is not None and 40.0 < rsi_at_entry <= config.signal_rsi_maximum,
        "aboveSessionVwap": math.isfinite(float(session_vwap.iloc[-1])) and current_close > float(session_vwap.iloc[-1]),
        "emaTrend": (
            math.isfinite(float(ema_fast.iloc[-1]))
            and math.isfinite(float(ema_slow.iloc[-1]))
            and math.isfinite(float(ema_fast.iloc[-2]))
            and float(ema_fast.iloc[-1]) > float(ema_slow.iloc[-1])
            and float(ema_fast.iloc[-1]) > float(ema_fast.iloc[-2])
        ),
        "rvol": math.isfinite(rvol) and rvol >= config.minimum_rvol,
        "roomToTarget": room_pct is not None and room_pct >= config.target_pct,
        "liquidity": math.isfinite(float(traded_value)) and float(traded_value) >= config.minimum_average_traded_value,
        "spreadQuality": math.isfinite(range_proxy_pct) and range_proxy_pct <= config.maximum_intrabar_range_pct,
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
    allowed = not failed and score >= config.minimum_alignment_score
    unavailable = []
    if not nifty.get("available"):
        unavailable.append("NIFTY trend")
    if not sector.get("available"):
        unavailable.append("sector")
    if not breadth.get("available"):
        unavailable.append("breadth")
    decision = "MARKET_ALIGNMENT_ACCEPTED" if allowed else (
        "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA"
        if unavailable
        else "SKIPPED_MARKET_ALIGNMENT"
    )
    return {
        "allowed": allowed,
        "score": _finite(score),
        "decision": decision,
        "reason": (
            "All configured market-alignment gates passed"
            if allowed
            else "Unavailable context: " + ", ".join(unavailable)
            if unavailable
            else "Failed gates: " + ", ".join(failed)
        ),
        "sourceTimestamp": min(
            value
            for value in (
                source_timestamp.isoformat(),
                nifty.get("sourceTimestamp"),
                sector.get("sourceTimestamp"),
                breadth.get("sourceTimestamp"),
            )
            if value
        ),
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
    }


def apply_market_alignment_chronologically(
    results: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    nifty_frame: pd.DataFrame,
    config: MarketAlignedConfig,
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
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    for _, symbol, _, result_index, trade in candidates:
        evaluation = evaluate_market_alignment(
            trade,
            symbol_frame=frames_by_symbol.get(symbol, pd.DataFrame()),
            nifty_frame=nifty_frame,
            universe_frames=frames_by_symbol,
            config=config,
        )
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
        }
        target = output[result_index]
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
                "hypotheticalOutcome": enriched,
            })
        target["events"] = [
            event for event in target.get("events", [])
            if event.get("type") != "BUY" or event.get("tradeId") in {row.get("tradeId") for row in target["trades"]}
        ]
    return output
