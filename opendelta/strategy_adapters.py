from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import timedelta
from typing import Any, Literal, Protocol

import pandas as pd

from crypto_strategy import CryptoPullbackConfig, candle_frame as crypto_candle_frame, generate_signals
from main import calculate_rsi
from market_core import MarketCandle, TIMEFRAME_SECONDS
from recovery_backtest import RecoveryConfig, simulate_recovery_symbol


SignalSide = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    signal_index: int
    signal_timestamp: pd.Timestamp
    side: SignalSide
    explanation: str


@dataclass(frozen=True)
class StrategyAdapterMetadata:
    strategy_id: str
    version: str
    name: str
    parameter_schema: dict[str, Any]
    required_data: tuple[str, ...]
    supported_markets: tuple[str, ...]
    supported_timeframes: tuple[str, ...]
    supported_directions: tuple[SignalSide, ...]
    locked_factor_families: tuple[str, ...]
    compatibility_rules: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return asdict(self)


class StrategyAdapter(Protocol):
    metadata: StrategyAdapterMetadata

    def signals(
        self,
        symbol: str,
        frame: pd.DataFrame,
        timeframe: str,
        parameters: dict[str, Any] | None = None,
        direction: str = "LONG",
    ) -> list[StrategySignal]: ...


def _validated_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Strategy data is missing: {', '.join(missing)}")
    data = frame.copy().reset_index(drop=True)
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    return data.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def _legacy_recovery_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = _validated_frame(frame)
    result = data.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    ).set_index("timestamp")
    return result[["Open", "High", "Low", "Close", "Volume"]]


def rsi_range_entries(rsi: pd.Series, low: float, high: float) -> pd.Series:
    """Canonical range-entry event shared with the legacy RSI Range engine."""

    inside = rsi.between(low, high, inclusive="both").fillna(False)
    return inside & ~inside.shift(1, fill_value=False)


class RsiRangeAdapter:
    metadata = StrategyAdapterMetadata(
        strategy_id="rsi_range",
        version="rsi-range-1.0.0",
        name="RSI Range Strategy",
        parameter_schema={"rsiLength": {"type": "integer", "default": 14, "minimum": 2, "maximum": 500}, "entryLow": {"type": "number", "default": 20.0, "minimum": 0.0, "maximum": 100.0}, "entryHigh": {"type": "number", "default": 30.0, "minimum": 0.0, "maximum": 100.0}},
        required_data=("close",),
        supported_markets=("NSE",),
        supported_timeframes=("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"),
        supported_directions=("LONG",),
        locked_factor_families=("MOMENTUM",),
        compatibility_rules=("Momentum is locked because RSI is the base entry trigger.",),
    )

    def signals(self, symbol: str, frame: pd.DataFrame, timeframe: str, parameters: dict[str, Any] | None = None, direction: str = "LONG") -> list[StrategySignal]:
        if direction != "LONG":
            raise ValueError("RSI Range supports LONG research only")
        if timeframe not in self.metadata.supported_timeframes:
            raise ValueError("RSI Range does not support the selected setup timeframe")
        supplied = parameters or {}
        unknown = set(supplied).difference(self.metadata.parameter_schema)
        if unknown:
            raise ValueError(f"Unknown RSI Range parameters: {', '.join(sorted(unknown))}")
        length = int(supplied.get("rsiLength", 14))
        low = float(supplied.get("entryLow", 20.0))
        high = float(supplied.get("entryHigh", 30.0))
        if not 2 <= length <= 500 or not 0 <= low < high <= 100:
            raise ValueError("RSI Range parameters are invalid")
        data = _validated_frame(frame)
        rsi = calculate_rsi(data["close"], length)
        entries = rsi_range_entries(rsi, low, high)
        return [
            StrategySignal(symbol, index, data.loc[index, "timestamp"], "LONG", f"RSI entered the configured {low:g}-{high:g} range")
            for index in data.index[entries]
        ]


class RsiRecoveryAdapter:
    metadata = StrategyAdapterMetadata(
        strategy_id="rsi_recovery",
        version="rsi-recovery-1.1.0",
        name="RSI Recovery Scalping",
        parameter_schema={
            "rsiLength": {"type": "integer", "default": 14},
            "rsiArmLow": {"type": "number", "default": 30.0},
            "rsiArmHigh": {"type": "number", "default": 40.0},
            "rsiRecovery": {"type": "number", "default": 40.0},
            "emaEnabled": {"type": "boolean", "default": True},
            "emaFast": {"type": "integer", "default": 9},
            "emaSlow": {"type": "integer", "default": 20},
            "vwapEnabled": {"type": "boolean", "default": True},
            "volumeEnabled": {"type": "boolean", "default": True},
            "volumeEma": {"type": "integer", "default": 20},
            "minimumConfirmations": {"type": "integer", "default": 2},
            "setupExpiryBars": {"type": "integer", "default": 50},
        },
        required_data=("open", "high", "low", "close", "volume"),
        supported_markets=("NSE",),
        supported_timeframes=("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"),
        supported_directions=("LONG",),
        locked_factor_families=("MOMENTUM",),
        compatibility_rules=("Momentum is locked because RSI arming and recovery are the mandatory base trigger.",),
    )

    _mapping = {
        "rsiLength": "rsi_length", "rsiArmLow": "rsi_arm_low", "rsiArmHigh": "rsi_arm_high",
        "rsiRecovery": "rsi_recovery", "emaEnabled": "ema_enabled", "emaFast": "ema_fast",
        "emaSlow": "ema_slow", "vwapEnabled": "vwap_enabled", "volumeEnabled": "volume_enabled",
        "volumeEma": "volume_ema", "minimumConfirmations": "minimum_confirmations",
        "setupExpiryBars": "setup_expiry_bars",
    }

    def signals(self, symbol: str, frame: pd.DataFrame, timeframe: str, parameters: dict[str, Any] | None = None, direction: str = "LONG") -> list[StrategySignal]:
        if direction != "LONG":
            raise ValueError("RSI Recovery supports LONG research only")
        if timeframe not in self.metadata.supported_timeframes:
            raise ValueError("RSI Recovery does not support the selected setup timeframe")
        supplied = parameters or {}
        unknown = set(supplied).difference(self._mapping)
        if unknown:
            raise ValueError(f"Unknown RSI Recovery parameters: {', '.join(sorted(unknown))}")
        config_values = {self._mapping[key]: value for key, value in supplied.items()}
        config = RecoveryConfig(**config_values, target_pct=1_000_000.0, execution_model="NEXT_BAR_OPEN")
        if not 2 <= config.rsi_length <= 500:
            raise ValueError("RSI Recovery rsiLength must be between 2 and 500")
        if not 0 <= config.rsi_arm_low <= config.rsi_arm_high <= config.rsi_recovery <= 100:
            raise ValueError("RSI Recovery thresholds must satisfy armLow <= armHigh <= recovery")
        if not 2 <= config.ema_fast < config.ema_slow <= 500:
            raise ValueError("RSI Recovery EMA lengths must satisfy 2 <= fast < slow <= 500")
        if not 2 <= config.volume_ema <= 500 or config.setup_expiry_bars < 0:
            raise ValueError("RSI Recovery volume and expiry parameters are invalid")
        if not 0 <= config.minimum_confirmations <= config.enabled_confirmations:
            raise ValueError("minimumConfirmations exceeds the number of enabled confirmations")
        data = _legacy_recovery_frame(frame)
        result = simulate_recovery_symbol(
            symbol,
            data,
            timeframe=timeframe,
            config=config,
            run_id="research-adapter",
        )
        normalized = _validated_frame(frame)
        signals: dict[int, StrategySignal] = {}
        for trade in result["trades"]:
            signal_index = int(trade["entryBarIndex"]) - 1
            if signal_index < 0:
                continue
            signals[signal_index] = StrategySignal(
                symbol,
                signal_index,
                normalized.loc[signal_index, "timestamp"],
                "LONG",
                f"RSI Recovery with {trade['confirmationScore']} confirmations",
            )
        return [signals[index] for index in sorted(signals)]


class CryptoTrendPullbackAdapter:
    metadata = StrategyAdapterMetadata(
        strategy_id="crypto_trend_pullback_recovery",
        version="1.0.0",
        name="Crypto Trend Pullback Recovery",
        parameter_schema={item.name: {"type": "strategy-field"} for item in fields(CryptoPullbackConfig)},
        required_data=("open", "high", "low", "close", "volume"),
        supported_markets=("CRYPTO",),
        supported_timeframes=("1m", "5m", "15m", "30m", "1h", "6h", "1d"),
        supported_directions=("LONG", "SHORT"),
        locked_factor_families=("TREND_DIRECTION", "MOMENTUM", "VOLUME"),
        compatibility_rules=("Trend, momentum and volume are locked by the shared crypto signal generator.",),
    )

    _camel = {
        "rsiLength": "rsi_length", "buyArmLow": "buy_arm_low", "buyArmHigh": "buy_arm_high",
        "sellArmLow": "sell_arm_low", "sellArmHigh": "sell_arm_high", "recoveryLevel": "recovery_level",
        "emaFast": "ema_fast", "emaSlow": "ema_slow", "atrLength": "atr_length",
        "volumePeriod": "volume_period", "minimumRvol": "minimum_rvol", "setupExpiryBars": "setup_expiry_bars",
        "stopAtrMultiplier": "stop_atr_multiplier", "rewardRiskRatio": "reward_risk_ratio",
        "maximumHoldingBars": "maximum_holding_bars", "side": "side",
        "makerTakerCostBps": "maker_taker_cost_bps", "slippageBps": "slippage_bps",
    }

    def signals(self, symbol: str, frame: pd.DataFrame, timeframe: str, parameters: dict[str, Any] | None = None, direction: str = "LONG") -> list[StrategySignal]:
        supplied = parameters or {}
        if timeframe not in self.metadata.supported_timeframes:
            raise ValueError("Crypto Trend Pullback Recovery does not support the selected timeframe")
        allowed = {item.name for item in fields(CryptoPullbackConfig)}.union(self._camel)
        unknown = set(supplied).difference(allowed)
        if unknown:
            raise ValueError(f"Unknown crypto strategy parameters: {', '.join(sorted(unknown))}")
        values = {self._camel.get(key, key): value for key, value in supplied.items()}
        values["side"] = "BUY" if direction == "LONG" else "SELL" if direction == "SHORT" else "BOTH"
        config = CryptoPullbackConfig(**values).validate()
        data = _validated_frame(frame)
        seconds = TIMEFRAME_SECONDS[timeframe]
        candles = [
            MarketCandle.build(
                provider="OKX",
                provider_symbol=symbol,
                timeframe=timeframe,
                open_time=row.timestamp.to_pydatetime() - timedelta(seconds=seconds),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                base_volume=row.volume,
            )
            for row in data.itertuples(index=False)
        ]
        signal_frame = crypto_candle_frame(candles, config)
        return [
            StrategySignal(
                symbol,
                candidate.signal_index,
                data.loc[candidate.signal_index, "timestamp"],
                "LONG" if candidate.side == "BUY" else "SHORT",
                f"Crypto pullback recovery; RVOL {candidate.rvol:.3f}",
            )
            for candidate in generate_signals(signal_frame, config)
        ]


class NeutralResearchAdapter:
    metadata = StrategyAdapterMetadata(
        strategy_id="neutral_research_trigger",
        version="1.0.0",
        name="Neutral completed-bar trigger",
        parameter_schema={"warmupBars": {"type": "integer", "default": 1, "minimum": 1, "maximum": 10_000}},
        required_data=("open", "high", "low", "close", "volume"),
        supported_markets=("NSE", "CRYPTO"),
        supported_timeframes=("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"),
        supported_directions=("LONG", "SHORT"),
        locked_factor_families=(),
        compatibility_rules=("Generates one neutral opportunity per completed setup bar after warm-up.",),
    )

    def signals(self, symbol: str, frame: pd.DataFrame, timeframe: str, parameters: dict[str, Any] | None = None, direction: str = "LONG") -> list[StrategySignal]:
        data = _validated_frame(frame)
        if timeframe not in self.metadata.supported_timeframes:
            raise ValueError("Neutral trigger does not support the selected timeframe")
        supplied = parameters or {}
        unknown = set(supplied).difference(self.metadata.parameter_schema)
        if unknown:
            raise ValueError(f"Unknown neutral trigger parameters: {', '.join(sorted(unknown))}")
        warmup_value = supplied.get("warmupBars", 1)
        if isinstance(warmup_value, bool) or not isinstance(warmup_value, int):
            raise ValueError("Neutral trigger warmupBars must be an integer")
        warmup = int(warmup_value)
        if warmup < 1 or warmup >= len(data):
            raise ValueError("Neutral trigger warmupBars is outside the available data")
        sides: tuple[SignalSide, ...] = ("LONG", "SHORT") if direction == "BOTH" else (direction,)  # type: ignore[assignment]
        return [
            StrategySignal(symbol, index, data.loc[index, "timestamp"], side, "Neutral completed-bar research opportunity")
            for index in range(warmup - 1, len(data) - 1)
            for side in sides
        ]


class StrategyAdapterRegistry:
    def __init__(self) -> None:
        adapters: tuple[StrategyAdapter, ...] = (
            RsiRecoveryAdapter(), RsiRangeAdapter(), CryptoTrendPullbackAdapter(), NeutralResearchAdapter()
        )
        self._adapters = {adapter.metadata.strategy_id: adapter for adapter in adapters}

    def get(self, strategy_id: str) -> StrategyAdapter:
        try:
            return self._adapters[strategy_id]
        except KeyError as error:
            raise ValueError(f"Unsupported Research base strategy: {strategy_id}") from error

    def list(self) -> list[StrategyAdapterMetadata]:
        return [self._adapters[key].metadata for key in sorted(self._adapters)]
