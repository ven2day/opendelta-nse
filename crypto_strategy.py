from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from market_core import MarketCandle, MarketInstrument, iso_utc


STRATEGY_KEY = "crypto_trend_pullback_recovery"
STRATEGY_NAME = "Crypto Trend Pullback Recovery"
STRATEGY_VERSION = "1.0.0"


@dataclass(frozen=True)
class CryptoPullbackConfig:
    rsi_length: int = 14
    buy_arm_low: float = 40.0
    buy_arm_high: float = 50.0
    sell_arm_low: float = 50.0
    sell_arm_high: float = 60.0
    recovery_level: float = 50.0
    ema_fast: int = 20
    ema_slow: int = 50
    atr_length: int = 14
    volume_period: int = 20
    minimum_rvol: float = 1.2
    setup_expiry_bars: int = 6
    stop_atr_multiplier: float = 1.0
    reward_risk_ratio: float = 1.5
    maximum_holding_bars: int = 6
    side: Literal["BOTH", "BUY", "SELL"] = "BOTH"
    maker_taker_cost_bps: float = 8.0
    slippage_bps: float = 2.0

    def validate(self) -> CryptoPullbackConfig:
        if not 2 <= self.rsi_length <= 500:
            raise ValueError("RSI length must be between 2 and 500")
        if not 0 <= self.buy_arm_low < self.buy_arm_high <= self.recovery_level <= 100:
            raise ValueError("BUY RSI arm range must end at or below the recovery level")
        if not 0 <= self.recovery_level <= self.sell_arm_low < self.sell_arm_high <= 100:
            raise ValueError("SELL RSI arm range must start at or above the recovery level")
        if not 1 <= self.ema_fast < self.ema_slow <= 1_000:
            raise ValueError("EMA lengths must be positive and fast must be below slow")
        if self.atr_length < 2 or self.volume_period < 2:
            raise ValueError("ATR and volume periods must be at least two bars")
        if self.minimum_rvol <= 0 or self.setup_expiry_bars < 1:
            raise ValueError("RVOL and setup expiry must be positive")
        if self.stop_atr_multiplier <= 0 or self.reward_risk_ratio <= 0:
            raise ValueError("Stop and reward:risk values must be positive")
        if self.maximum_holding_bars < 1:
            raise ValueError("Maximum holding bars must be positive")
        if self.side not in {"BOTH", "BUY", "SELL"}:
            raise ValueError("Side must be BOTH, BUY, or SELL")
        if self.maker_taker_cost_bps < 0 or self.slippage_bps < 0:
            raise ValueError("Costs and slippage cannot be negative")
        return self

    def public(self) -> dict[str, Any]:
        return {
            "rsiLength": self.rsi_length,
            "buyArmLow": self.buy_arm_low,
            "buyArmHigh": self.buy_arm_high,
            "sellArmLow": self.sell_arm_low,
            "sellArmHigh": self.sell_arm_high,
            "recoveryLevel": self.recovery_level,
            "emaFast": self.ema_fast,
            "emaSlow": self.ema_slow,
            "atrLength": self.atr_length,
            "volumePeriod": self.volume_period,
            "minimumRvol": self.minimum_rvol,
            "setupExpiryBars": self.setup_expiry_bars,
            "stopAtrMultiplier": self.stop_atr_multiplier,
            "rewardRiskRatio": self.reward_risk_ratio,
            "maximumHoldingBars": self.maximum_holding_bars,
            "side": self.side,
            "makerTakerCostBps": self.maker_taker_cost_bps,
            "slippageBps": self.slippage_bps,
        }


@dataclass(frozen=True)
class SignalCandidate:
    side: Literal["BUY", "SELL"]
    signal_index: int
    signal_time: datetime
    signal_price: float
    stop_price: float
    target_price: float
    initial_risk: float
    rsi: float
    arm_rsi: float
    arm_time: datetime
    rvol: float
    atr: float
    ema_fast: float
    ema_slow: float
    vwap: float


def _wilder_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    average_loss = losses.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    return result.mask((average_loss == 0) & (average_gain == 0), 50.0)


def _wilder_atr(frame: pd.DataFrame, length: int) -> pd.Series:
    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def _utc_session_vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    session = frame.index.floor("D")
    cumulative_value = (typical * frame["Volume"]).groupby(session).cumsum()
    cumulative_volume = frame["Volume"].groupby(session).cumsum().replace(0, np.nan)
    return cumulative_value / cumulative_volume


def candle_frame(candles: Sequence[MarketCandle], config: CryptoPullbackConfig) -> pd.DataFrame:
    config.validate()
    complete = sorted((item for item in candles if item.complete), key=lambda item: item.open_time)
    if not complete:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    frame = pd.DataFrame(
        [
            {
                "Open": item.open,
                "High": item.high,
                "Low": item.low,
                "Close": item.close,
                "Volume": item.base_volume,
            }
            for item in complete
        ],
        index=pd.DatetimeIndex([item.open_time for item in complete]),
    )
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["RSI"] = _wilder_rsi(frame["Close"], config.rsi_length)
    frame["EMAFast"] = frame["Close"].ewm(span=config.ema_fast, adjust=False).mean()
    frame["EMASlow"] = frame["Close"].ewm(span=config.ema_slow, adjust=False).mean()
    frame["ATR"] = _wilder_atr(frame, config.atr_length)
    frame["VolumeMean"] = frame["Volume"].shift(1).rolling(config.volume_period).mean()
    frame["RVOL"] = frame["Volume"] / frame["VolumeMean"].replace(0, np.nan)
    frame["VWAP"] = _utc_session_vwap(frame)
    return frame


def generate_signals(frame: pd.DataFrame, config: CryptoPullbackConfig) -> list[SignalCandidate]:
    config.validate()
    minimum_rows = max(config.ema_slow, config.rsi_length, config.atr_length, config.volume_period) + 2
    if len(frame) < minimum_rows:
        return []
    candidates: list[SignalCandidate] = []
    buy_arm: tuple[int, float, datetime] | None = None
    sell_arm: tuple[int, float, datetime] | None = None
    for index in range(1, len(frame)):
        row, previous = frame.iloc[index], frame.iloc[index - 1]
        required = (row["RSI"], previous["RSI"], row["ATR"], row["RVOL"], row["VWAP"])
        if any(pd.isna(value) for value in required):
            continue
        rsi = float(row["RSI"])
        stamp = frame.index[index].to_pydatetime()
        if config.buy_arm_low <= rsi <= config.buy_arm_high:
            buy_arm = (index, rsi, stamp)
        if config.sell_arm_low <= rsi <= config.sell_arm_high:
            sell_arm = (index, rsi, stamp)
        if buy_arm and index - buy_arm[0] > config.setup_expiry_bars:
            buy_arm = None
        if sell_arm and index - sell_arm[0] > config.setup_expiry_bars:
            sell_arm = None

        volume_pass = float(row["RVOL"]) >= config.minimum_rvol
        buy_cross = float(previous["RSI"]) <= config.recovery_level < rsi
        sell_cross = float(previous["RSI"]) >= config.recovery_level > rsi
        buy_structure = (
            float(row["EMAFast"]) > float(row["EMASlow"])
            and float(row["EMAFast"]) > float(previous["EMAFast"])
            and float(row["Close"]) > float(row["VWAP"])
            and float(row["Close"]) > float(previous["High"])
        )
        sell_structure = (
            float(row["EMAFast"]) < float(row["EMASlow"])
            and float(row["EMAFast"]) < float(previous["EMAFast"])
            and float(row["Close"]) < float(row["VWAP"])
            and float(row["Close"]) < float(previous["Low"])
        )
        if buy_arm and config.side in {"BOTH", "BUY"} and buy_cross and volume_pass and buy_structure:
            risk = float(row["ATR"]) * config.stop_atr_multiplier
            price = float(row["Close"])
            if risk > 0:
                candidates.append(
                    SignalCandidate(
                        side="BUY",
                        signal_index=index,
                        signal_time=stamp,
                        signal_price=price,
                        stop_price=price - risk,
                        target_price=price + risk * config.reward_risk_ratio,
                        initial_risk=risk,
                        rsi=rsi,
                        arm_rsi=buy_arm[1],
                        arm_time=buy_arm[2],
                        rvol=float(row["RVOL"]),
                        atr=float(row["ATR"]),
                        ema_fast=float(row["EMAFast"]),
                        ema_slow=float(row["EMASlow"]),
                        vwap=float(row["VWAP"]),
                    )
                )
            buy_arm = None
        if sell_arm and config.side in {"BOTH", "SELL"} and sell_cross and volume_pass and sell_structure:
            risk = float(row["ATR"]) * config.stop_atr_multiplier
            price = float(row["Close"])
            if risk > 0:
                candidates.append(
                    SignalCandidate(
                        side="SELL",
                        signal_index=index,
                        signal_time=stamp,
                        signal_price=price,
                        stop_price=price + risk,
                        target_price=price - risk * config.reward_risk_ratio,
                        initial_risk=risk,
                        rsi=rsi,
                        arm_rsi=sell_arm[1],
                        arm_time=sell_arm[2],
                        rvol=float(row["RVOL"]),
                        atr=float(row["ATR"]),
                        ema_fast=float(row["EMAFast"]),
                        ema_slow=float(row["EMASlow"]),
                        vwap=float(row["VWAP"]),
                    )
                )
            sell_arm = None
    return candidates


def deterministic_signal_id(instrument: MarketInstrument, timeframe: str, candidate: SignalCandidate) -> str:
    identity = "|".join(
        (
            STRATEGY_KEY,
            STRATEGY_VERSION,
            instrument.instrument_id,
            timeframe,
            candidate.side,
            iso_utc(candidate.signal_time),
        )
    )
    return "CSIG-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24].upper()


def signal_payload(
    instrument: MarketInstrument,
    timeframe: str,
    candidate: SignalCandidate,
) -> dict[str, Any]:
    return {
        "signalId": deterministic_signal_id(instrument, timeframe, candidate),
        "instrumentId": instrument.instrument_id,
        "provider": instrument.provider,
        "providerSymbol": instrument.provider_symbol,
        "displaySymbol": instrument.display_symbol,
        "market": instrument.market,
        "instrumentType": instrument.instrument_type,
        "strategyKey": STRATEGY_KEY,
        "strategyName": STRATEGY_NAME,
        "strategyVersion": STRATEGY_VERSION,
        "timeframe": timeframe,
        "side": candidate.side,
        "signalTimestamp": iso_utc(candidate.signal_time),
        "signalPrice": round(candidate.signal_price, 10),
        "stopPrice": round(candidate.stop_price, 10),
        "targetPrice": round(candidate.target_price, 10),
        "initialRisk": round(candidate.initial_risk, 10),
        "rsi": round(candidate.rsi, 4),
        "rsiArmValue": round(candidate.arm_rsi, 4),
        "rsiArmTimestamp": iso_utc(candidate.arm_time),
        "rvol": round(candidate.rvol, 4),
        "atr": round(candidate.atr, 10),
        "emaFast": round(candidate.ema_fast, 10),
        "emaSlow": round(candidate.ema_slow, 10),
        "vwap": round(candidate.vwap, 10),
        "paperOnly": True,
        "liveOrdersEnabled": False,
    }


def run_pullback_backtest(
    instrument: MarketInstrument,
    timeframe: str,
    candles: Sequence[MarketCandle],
    config: CryptoPullbackConfig,
) -> dict[str, Any]:
    frame = candle_frame(candles, config)
    candidates = generate_signals(frame, config)
    trades: list[dict[str, Any]] = []
    occupied_through = -1
    cost_rate = config.maker_taker_cost_bps / 10_000.0
    slip_rate = config.slippage_bps / 10_000.0
    for candidate in candidates:
        entry_index = candidate.signal_index + 1
        if entry_index >= len(frame) or entry_index <= occupied_through:
            continue
        entry_row = frame.iloc[entry_index]
        raw_entry = float(entry_row["Open"])
        entry = raw_entry * (1 + slip_rate if candidate.side == "BUY" else 1 - slip_rate)
        risk = candidate.initial_risk
        stop = entry - risk if candidate.side == "BUY" else entry + risk
        target = entry + risk * config.reward_risk_ratio if candidate.side == "BUY" else entry - risk * config.reward_risk_ratio
        exit_index = min(len(frame) - 1, entry_index + config.maximum_holding_bars)
        raw_exit = float(frame.iloc[exit_index]["Close"])
        outcome = "TIME_EXIT"
        for monitor in range(entry_index + 1, exit_index + 1):
            bar = frame.iloc[monitor]
            stop_hit = float(bar["Low"]) <= stop if candidate.side == "BUY" else float(bar["High"]) >= stop
            target_hit = float(bar["High"]) >= target if candidate.side == "BUY" else float(bar["Low"]) <= target
            if stop_hit:
                raw_exit, exit_index, outcome = stop, monitor, "STOP"
                break
            if target_hit:
                raw_exit, exit_index, outcome = target, monitor, "TARGET"
                break
        exit_price = raw_exit * (1 - slip_rate if candidate.side == "BUY" else 1 + slip_rate)
        gross = exit_price - entry if candidate.side == "BUY" else entry - exit_price
        costs = (entry + exit_price) * cost_rate
        net = gross - costs
        trades.append(
            {
                "signalId": deterministic_signal_id(instrument, timeframe, candidate),
                "side": candidate.side,
                "signalTimestamp": iso_utc(candidate.signal_time),
                "entryTimestamp": iso_utc(frame.index[entry_index].to_pydatetime()),
                "exitTimestamp": iso_utc(frame.index[exit_index].to_pydatetime()),
                "entryPrice": round(entry, 10),
                "exitPrice": round(exit_price, 10),
                "stopPrice": round(stop, 10),
                "targetPrice": round(target, 10),
                "outcome": outcome,
                "grossPnlPerUnit": round(gross, 10),
                "costPerUnit": round(costs, 10),
                "netPnlPerUnit": round(net, 10),
                "netR": round(net / risk, 6) if risk else None,
                "barsHeld": max(0, exit_index - entry_index),
            }
        )
        occupied_through = exit_index
    wins = sum(1 for trade in trades if float(trade["netPnlPerUnit"]) > 0)
    gross_profit = sum(max(0.0, float(trade["netPnlPerUnit"])) for trade in trades)
    gross_loss = abs(sum(min(0.0, float(trade["netPnlPerUnit"])) for trade in trades))
    net_total = sum(float(trade["netPnlPerUnit"]) for trade in trades)
    return {
        "metadata": {
            "provider": instrument.provider,
            "instrumentId": instrument.instrument_id,
            "providerSymbol": instrument.provider_symbol,
            "displaySymbol": instrument.display_symbol,
            "market": instrument.market,
            "strategyKey": STRATEGY_KEY,
            "strategyName": STRATEGY_NAME,
            "strategyVersion": STRATEGY_VERSION,
            "timeframe": timeframe,
            "configuration": config.public(),
            "paperOnly": True,
            "liveOrdersEnabled": False,
        },
        "summary": {
            "completedCandles": len(frame),
            "rawSignals": len(candidates),
            "executedTrades": len(trades),
            "wins": wins,
            "losses": len(trades) - wins,
            "winRatePct": round(wins / len(trades) * 100, 2) if trades else 0.0,
            "netPnlPerUnit": round(net_total, 10),
            "averageNetR": round(sum(float(item["netR"] or 0) for item in trades) / len(trades), 6) if trades else 0.0,
            "profitFactor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else math.inf),
        },
        "trades": trades,
        "warnings": [
            "Research and paper-signal only; no exchange order API is connected.",
            "Signals use completed candles and backtests enter only at the next candle open.",
            "If stop and target are both touched in one candle, the stop is applied first.",
            "Funding is not yet included; do not use perpetual results for live approval.",
        ],
    }
