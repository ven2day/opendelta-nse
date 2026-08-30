from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from .core import UNSUPPORTED_DATA_REQUIREMENT


FactorFamily = Literal[
    "TREND_DIRECTION", "TREND_STRENGTH", "MOMENTUM", "VOLATILITY", "VOLUME",
    "RELATIVE_STRENGTH", "MARKET_STRUCTURE", "LIQUIDITY_EXECUTION", "MARKET_REGIME",
    "TIME_SESSION",
]


@dataclass(frozen=True)
class FactorParameter:
    name: str
    default: float | int
    minimum: float | int
    maximum: float | int
    description: str


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    version: str
    name: str
    family: FactorFamily
    description: str
    measures: str
    use_when: str
    avoid_when: str
    misunderstanding: str
    required_data: tuple[str, ...]
    supported_markets: tuple[str, ...]
    supported_timeframes: tuple[str, ...]
    parameters: tuple[FactorParameter, ...]
    warmup_bars: int
    output_type: Literal["BOOLEAN", "EVENT", "NUMERIC", "CATEGORY"] = "NUMERIC"
    directionality: Literal[
        "HIGHER_BETTER", "LOWER_BETTER", "BETWEEN", "CATEGORY_MATCH", "EVENT"
    ] = "HIGHER_BETTER"
    default_predicate: str = "MINIMUM"
    threshold_parameters: tuple[dict[str, Any], ...] = ()
    valid_range: tuple[Any, ...] | None = None
    entry_role: Literal["CONTEXT", "FILTER", "TRIGGER", "EXECUTION"] = "FILTER"
    missing_data_behavior: str = UNSUPPORTED_DATA_REQUIREMENT

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            outputType=self.output_type,
            directionality=self.directionality,
            defaultPredicate=self.default_predicate,
            thresholdParameters=list(self.threshold_parameters),
            validRange=self.valid_range,
            entryRole=self.entry_role,
            warmupBars=self.warmup_bars,
        )
        return payload


@dataclass(frozen=True)
class FactorOutput:
    definition: FactorDefinition
    status: Literal["SUPPORTED", "UNSUPPORTED_DATA_REQUIREMENT"]
    values: pd.Series | None
    reason: str | None = None


ALL_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d")
ALL_MARKETS = ("NSE", "CRYPTO")


FACTOR_SEMANTICS: dict[str, dict[str, Any]] = {
    "ema_alignment": {"output_type": "CATEGORY", "directionality": "CATEGORY_MATCH", "default_predicate": "POSITIVE", "valid_range": (-1, 0, 1), "entry_role": "CONTEXT"},
    "vwap_slope": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "CONTEXT"},
    "market_structure": {"output_type": "BOOLEAN", "directionality": "EVENT", "default_predicate": "TRUE", "valid_range": (0, 1), "entry_role": "CONTEXT"},
    "adx": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 25.0, "minimum": 0.0, "maximum": 100.0},), "valid_range": (0.0, 100.0), "entry_role": "CONTEXT"},
    "normalized_ema_slope": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "CONTEXT"},
    "trend_efficiency": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.3, "minimum": 0.0, "maximum": 1.0},), "valid_range": (0.0, 1.0), "entry_role": "CONTEXT"},
    "rsi_recovery": {"output_type": "EVENT", "directionality": "EVENT", "default_predicate": "TRUE", "valid_range": (0, 1), "entry_role": "TRIGGER"},
    "roc": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "FILTER"},
    "macd_histogram_acceleration": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "FILTER"},
    "atr_percentile": {"directionality": "BETWEEN", "default_predicate": "BETWEEN", "threshold_parameters": ({"name": "minimum", "default": 0.2, "minimum": 0.0, "maximum": 1.0}, {"name": "maximum", "default": 0.8, "minimum": 0.0, "maximum": 1.0}), "valid_range": (0.0, 1.0), "entry_role": "FILTER"},
    "bollinger_width_percentile": {"directionality": "BETWEEN", "default_predicate": "BETWEEN", "threshold_parameters": ({"name": "minimum", "default": 0.2, "minimum": 0.0, "maximum": 1.0}, {"name": "maximum", "default": 0.8, "minimum": 0.0, "maximum": 1.0}), "valid_range": (0.0, 1.0), "entry_role": "FILTER"},
    "candle_range_percentile": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.8, "minimum": 0.0, "maximum": 1.0},), "valid_range": (0.0, 1.0), "entry_role": "FILTER"},
    "rvol": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 1.2, "minimum": 0.0},), "entry_role": "FILTER"},
    "volume_zscore": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 1.0},), "entry_role": "FILTER"},
    "volume_breakout": {"output_type": "EVENT", "directionality": "EVENT", "default_predicate": "TRUE", "valid_range": (0, 1), "entry_role": "FILTER"},
    "relative_nifty": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "CONTEXT"},
    "relative_sector": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "CONTEXT"},
    "relative_ratio_slope": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "CONTEXT"},
    "opening_range_breakout": {"output_type": "EVENT", "directionality": "EVENT", "default_predicate": "TRUE", "valid_range": (0, 1), "entry_role": "TRIGGER"},
    "swing_breakout": {"output_type": "EVENT", "directionality": "EVENT", "default_predicate": "TRUE", "valid_range": (0, 1), "entry_role": "TRIGGER"},
    "room_to_resistance": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.5, "minimum": 0.0},), "entry_role": "FILTER"},
    "room_to_support": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.5, "minimum": 0.0},), "entry_role": "FILTER"},
    "average_traded_value": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0, "minimum": 0.0},), "entry_role": "EXECUTION"},
    "average_volume": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0, "minimum": 0.0},), "entry_role": "EXECUTION"},
    "historical_spread": {"directionality": "LOWER_BETTER", "default_predicate": "MAXIMUM", "threshold_parameters": ({"name": "maximum", "default": 10.0, "minimum": 0.0},), "entry_role": "EXECUTION"},
    "slippage_sensitivity": {"default_predicate": "MINIMUM", "threshold_parameters": ({"name": "minimum", "default": 0.0},), "entry_role": "EXECUTION"},
    "market_regime": {"output_type": "CATEGORY", "directionality": "CATEGORY_MATCH", "default_predicate": "CATEGORY_IN", "threshold_parameters": ({"name": "categories", "default": ["TREND", "EXPANSION"]},), "valid_range": ("TREND", "RANGE", "COMPRESSION", "EXPANSION", "EXTREME_CHAOTIC"), "entry_role": "CONTEXT"},
    "session_bucket": {"output_type": "CATEGORY", "directionality": "CATEGORY_MATCH", "default_predicate": "CATEGORY_IN", "threshold_parameters": ({"name": "categories", "default": []},), "entry_role": "EXECUTION"},
}


def parameter(name: str, default: int | float, minimum: int | float, maximum: int | float, description: str) -> FactorParameter:
    return FactorParameter(name, default, minimum, maximum, description)


def definition(
    factor_id: str,
    name: str,
    family: FactorFamily,
    description: str,
    measures: str,
    use_when: str,
    avoid_when: str,
    misunderstanding: str,
    required: tuple[str, ...],
    parameters: tuple[FactorParameter, ...],
    warmup: int,
    *,
    markets: tuple[str, ...] = ALL_MARKETS,
    version: str = "1.0.0",
) -> FactorDefinition:
    semantics = FACTOR_SEMANTICS[factor_id]
    return FactorDefinition(
        factor_id=factor_id,
        version=version,
        name=name,
        family=family,
        description=description,
        measures=measures,
        use_when=use_when,
        avoid_when=avoid_when,
        misunderstanding=misunderstanding,
        required_data=required,
        supported_markets=markets,
        supported_timeframes=ALL_TIMEFRAMES,
        parameters=parameters,
        warmup_bars=warmup,
        **semantics,
    )


FACTOR_CATALOG: tuple[FactorDefinition, ...] = (
    definition("ema_alignment", "EMA alignment", "TREND_DIRECTION", "Compares a fast and slow exponential average.", "Direction implied by average ordering.", "Use after both averages have warmed up.", "Avoid as a standalone entry in sideways data.", "EMA ordering is lagging context, not a price forecast.", ("close",), (parameter("fast", 20, 2, 200, "Fast EMA bars"), parameter("slow", 50, 3, 500, "Slow EMA bars")), 50),
    definition("vwap_slope", "VWAP direction and slope", "TREND_DIRECTION", "Measures the change in session or rolling VWAP.", "Direction of volume-weighted fair value.", "Use when volume is reliable.", "Avoid when volume is missing or synthetic.", "Price above VWAP is not automatically a BUY.", ("high", "low", "close", "volume"), (parameter("period", 20, 2, 500, "Rolling VWAP window"),), 20),
    definition("market_structure", "Higher-high / higher-low structure", "TREND_DIRECTION", "Compares rolling swing extrema.", "Directional market structure.", "Use on completed candles with enough swing history.", "Avoid on sparse series.", "One higher high does not establish a durable trend.", ("high", "low"), (parameter("lookback", 5, 2, 100, "Swing comparison window"),), 10),
    definition("adx", "Average Directional Index", "TREND_STRENGTH", "Wilder ADX summarizes directional movement strength.", "Trend strength, never direction.", "Use to distinguish persistent movement from chop.", "Avoid before its warm-up period.", "ADX strength does not say whether trend direction is up or down.", ("high", "low", "close"), (parameter("length", 14, 2, 100, "Wilder smoothing length"),), 28),
    definition("normalized_ema_slope", "Normalized EMA slope", "TREND_STRENGTH", "Normalizes EMA change by price.", "Scale-independent trend slope.", "Use to compare instruments with different prices.", "Avoid with very short windows.", "A steep historical slope is not guaranteed to persist.", ("close",), (parameter("length", 20, 2, 200, "EMA length"), parameter("slopeBars", 5, 1, 50, "Slope comparison bars")), 25),
    definition("trend_efficiency", "Trend-efficiency ratio", "TREND_STRENGTH", "Net movement divided by total path movement.", "How efficiently price moved in one direction.", "Use to penalize noisy paths.", "Avoid when the lookback has no movement.", "Efficiency measures path quality, not direction by itself.", ("close",), (parameter("length", 20, 2, 500, "Efficiency window"),), 20),
    definition("rsi_recovery", "RSI recovery", "MOMENTUM", "Detects RSI crossing up through a recovery level after an explicit armed state.", "Recovery in Wilder RSI momentum.", "Use with an explicit arm and recovery rule.", "Avoid interpreting one threshold in isolation.", "Oversold is not an automatic BUY and overbought is not an automatic SELL.", ("close",), (parameter("length", 14, 2, 100, "RSI length"), parameter("armLow", 30, 0, 99, "Arm-zone lower bound"), parameter("armHigh", 40, 1, 100, "Arm-zone upper bound"), parameter("recoveryLevel", 40, 1, 99, "Recovery threshold"), parameter("setupExpiryBars", 50, 0, 1000, "Maximum bars after arming; zero never expires")), 15, version="2.0.0"),
    definition("roc", "Rate of Change", "MOMENTUM", "Percentage price change over a fixed window.", "Directional price momentum.", "Use for comparable return horizons.", "Avoid across unadjusted corporate actions.", "Large ROC does not measure trend quality.", ("close",), (parameter("length", 12, 1, 500, "Return window"),), 12),
    definition("macd_histogram_acceleration", "MACD histogram acceleration", "MOMENTUM", "Change in MACD histogram rather than its absolute sign.", "Acceleration or deceleration of EMA momentum.", "Use after slow EMA and signal warm-up.", "Avoid as a standalone reversal forecast.", "A rising negative histogram is improving momentum, not necessarily bullish price.", ("close",), (parameter("fast", 12, 2, 100, "Fast EMA"), parameter("slow", 26, 3, 200, "Slow EMA"), parameter("signal", 9, 2, 100, "Signal EMA")), 35),
    definition("atr_percentile", "ATR percentile", "VOLATILITY", "Ranks ATR as a percentage of close within history.", "Relative movement size.", "Use to compare current and historical volatility.", "Avoid before percentile warm-up.", "Volatility measures movement size, not direction.", ("high", "low", "close"), (parameter("length", 14, 2, 100, "ATR length"), parameter("rankWindow", 100, 20, 1000, "Percentile history")), 114),
    definition("bollinger_width_percentile", "Bollinger bandwidth percentile", "VOLATILITY", "Ranks normalized band width within history.", "Compression or expansion in dispersion.", "Use for volatility-regime context.", "Avoid when mean price is zero or missing.", "Wide bands do not identify direction.", ("close",), (parameter("length", 20, 2, 500, "Band window"), parameter("rankWindow", 100, 20, 1000, "Percentile history")), 120),
    definition("candle_range_percentile", "Candle-range percentile", "VOLATILITY", "Ranks high-low range relative to recent candles.", "Current bar movement size.", "Use to flag expansion bars.", "Avoid on invalid OHLC data.", "A large candle is not automatically a breakout.", ("high", "low", "close"), (parameter("rankWindow", 100, 20, 1000, "Percentile history"),), 100),
    definition("rvol", "Relative volume", "VOLUME", "Compares current volume with its rolling mean.", "Participation relative to recent history.", "Use when provider volume is comparable over time.", "Avoid on synthetic or missing volume.", "High relative volume does not indicate BUY or SELL direction.", ("volume",), (parameter("length", 20, 2, 500, "Volume mean window"),), 20),
    definition("volume_zscore", "Volume Z-score", "VOLUME", "Standardizes volume against rolling mean and deviation.", "How unusual current volume is.", "Use to compare participation anomalies.", "Avoid when rolling variance is zero.", "A Z-score is not a probability of profit.", ("volume",), (parameter("length", 20, 3, 500, "Standardization window"),), 20),
    definition("volume_breakout", "Volume breakout", "VOLUME", "Flags volume exceeding a rolling maximum.", "New participation extremes.", "Use with completed bars.", "Avoid when data contains resets or unit changes.", "A volume record does not establish price direction.", ("volume",), (parameter("length", 20, 2, 500, "Breakout lookback"),), 21),
    definition("relative_nifty", "Stock versus NIFTY return", "RELATIVE_STRENGTH", "Subtracts benchmark return from instrument return.", "Performance relative to NIFTY.", "Use only with point-in-time aligned benchmark data.", "Avoid when benchmark data is absent.", "Relative strength is not RSI.", ("close", "benchmark_close"), (parameter("length", 20, 1, 500, "Return horizon"),), 20, markets=("NSE",)),
    definition("relative_sector", "Stock versus sector return", "RELATIVE_STRENGTH", "Subtracts mapped-sector return from instrument return.", "Performance relative to sector peers.", "Use only with audited sector mapping and aligned data.", "Avoid when sector data or mapping is absent.", "Do not invent sector mappings.", ("close", "sector_close"), (parameter("length", 20, 1, 500, "Return horizon"),), 20, markets=("NSE",)),
    definition("relative_ratio_slope", "Relative-strength ratio slope", "RELATIVE_STRENGTH", "Measures slope of instrument/benchmark ratio.", "Direction of relative performance.", "Use with aligned benchmark closes.", "Avoid when benchmark values are zero or missing.", "Relative-strength ratio is distinct from RSI.", ("close", "benchmark_close"), (parameter("length", 20, 2, 500, "Slope horizon"),), 20),
    definition("opening_range_breakout", "Opening-range breakout", "MARKET_STRUCTURE", "Compares close with the completed opening range.", "Breakout beyond an established session range.", "Use with market-specific session boundaries.", "Avoid before the opening range closes.", "The range must be completed before it can influence a decision.", ("high", "low", "close", "timestamp"), (parameter("bars", 3, 1, 24, "Completed opening bars"),), 4),
    definition("swing_breakout", "Swing breakout", "MARKET_STRUCTURE", "Compares close with prior rolling extrema.", "Breakout beyond recent structure.", "Use with next-bar execution.", "Avoid including the trigger bar in the reference range.", "Same-bar reference levels create lookahead.", ("high", "low", "close"), (parameter("length", 20, 2, 500, "Prior swing window"),), 21),
    definition("room_to_resistance", "Room to resistance", "MARKET_STRUCTURE", "Distance from close to prior rolling resistance.", "Available upside space before known structure.", "Use with causal prior highs.", "Avoid fabricated resistance levels.", "Distance is context, not a target guarantee.", ("high", "close"), (parameter("length", 50, 2, 1000, "Resistance window"),), 51),
    definition("room_to_support", "Room to support", "MARKET_STRUCTURE", "Distance from close to prior rolling support.", "Available downside space to known structure.", "Use with causal prior lows.", "Avoid fabricated support levels.", "Support can fail and is not a guaranteed stop.", ("low", "close"), (parameter("length", 50, 2, 1000, "Support window"),), 51),
    definition("average_traded_value", "Average traded value", "LIQUIDITY_EXECUTION", "Rolling mean of close multiplied by volume.", "Historical turnover capacity.", "Use for coarse liquidity screening.", "Avoid treating turnover as executable size.", "Traded value is not bid/ask depth.", ("close", "volume"), (parameter("length", 20, 2, 500, "Average window"),), 20),
    definition("average_volume", "Average volume", "LIQUIDITY_EXECUTION", "Rolling mean volume.", "Typical reported participation.", "Use for provider-consistent volume screening.", "Avoid cross-provider comparison without normalization.", "Average volume does not measure spread.", ("volume",), (parameter("length", 20, 2, 500, "Average window"),), 20),
    definition("historical_spread", "Historical spread", "LIQUIDITY_EXECUTION", "Computes bid/ask spread only from recorded quotes.", "Observed historical execution friction.", "Use when synchronized quotes exist.", "Return unsupported when quotes are missing.", "OHLCV cannot reconstruct historical bid/ask spread.", ("bid", "ask"), (), 1),
    definition("slippage_sensitivity", "Slippage sensitivity", "LIQUIDITY_EXECUTION", "Reprices returns across explicit slippage assumptions.", "Sensitivity of research results to execution cost.", "Use after a trade ledger exists.", "Avoid inferring historical spread.", "Sensitivity scenarios are assumptions, not observed fills.", ("trade_returns",), (parameter("bps", 10, 0, 500, "Round-trip slippage bps"),), 1),
    definition("market_regime", "Market regime", "MARKET_REGIME", "Classifies trend, range, compression, expansion or chaotic volatility.", "Combined direction efficiency and volatility state.", "Use as context rather than a hidden gate.", "Avoid before all inputs warm up.", "Regime labels summarize history and can change.", ("high", "low", "close"), (parameter("length", 20, 5, 500, "Regime window"),), 120),
    definition("session_bucket", "Market session bucket", "TIME_SESSION", "Maps timestamps to explicit NSE or UTC crypto sessions.", "Time-of-day and weekday context.", "Use with timezone-normalized timestamps.", "Avoid mixing exchange session assumptions.", "Crypto is 24/7; NSE is not.", ("timestamp",), (), 1),
)


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    ratio = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift(1)
    return pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - previous).abs(), (frame["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)


def _percentile(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).apply(lambda values: float(pd.Series(values).rank(pct=True).iloc[-1]), raw=False)


class FactorRegistry:
    def __init__(self, definitions: tuple[FactorDefinition, ...] = FACTOR_CATALOG) -> None:
        self._definitions = {item.factor_id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("Factor IDs must be unique")

    def get(self, factor_id: str) -> FactorDefinition:
        try:
            return self._definitions[factor_id]
        except KeyError as error:
            raise ValueError(f"Unknown factor: {factor_id}") from error

    def list(self, family: str | None = None) -> list[FactorDefinition]:
        rows = list(self._definitions.values())
        if family:
            rows = [item for item in rows if item.family == family]
        return sorted(rows, key=lambda item: (item.family, item.name))


class FactorEngine:
    def __init__(self, registry: FactorRegistry | None = None) -> None:
        self.registry = registry or FactorRegistry()

    def parameters(self, definition: FactorDefinition, supplied: dict[str, Any] | None) -> dict[str, float | int]:
        supplied = supplied or {}
        known = {item.name for item in definition.parameters}
        unknown = sorted(set(supplied).difference(known))
        if unknown:
            raise ValueError(f"Unknown factor parameters: {', '.join(unknown)}")
        resolved: dict[str, float | int] = {}
        for item in definition.parameters:
            value = supplied.get(item.name, item.default)
            if isinstance(item.default, int) and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{item.name} must be an integer")
            numeric = float(value)
            if not item.minimum <= numeric <= item.maximum:
                raise ValueError(f"{item.name} must be between {item.minimum} and {item.maximum}")
            resolved[item.name] = int(value) if isinstance(item.default, int) else numeric
        if definition.factor_id in {"ema_alignment", "macd_histogram_acceleration"}:
            if int(resolved["fast"]) >= int(resolved["slow"]):
                raise ValueError("fast must be less than slow")
        if definition.factor_id == "rsi_recovery":
            if float(resolved["armLow"]) > float(resolved["armHigh"]):
                raise ValueError("armLow must be less than or equal to armHigh")
            if float(resolved["recoveryLevel"]) < float(resolved["armHigh"]):
                raise ValueError("recoveryLevel must be greater than or equal to armHigh")
        return resolved

    def calculation_parameters(
        self, factor_id: str, supplied: dict[str, Any] | None
    ) -> dict[str, Any]:
        definition = self.registry.get(factor_id)
        calculation_names = {item.name for item in definition.parameters}
        return {
            key: value
            for key, value in (supplied or {}).items()
            if key in calculation_names
        }

    def calculate(
        self,
        factor_id: str,
        frame: pd.DataFrame,
        *,
        market: str,
        timeframe: str,
        parameters: dict[str, Any] | None = None,
        context: dict[str, pd.Series] | None = None,
    ) -> FactorOutput:
        definition = self.registry.get(factor_id)
        if market not in definition.supported_markets or timeframe not in definition.supported_timeframes:
            return FactorOutput(definition, UNSUPPORTED_DATA_REQUIREMENT, None, "MARKET_OR_TIMEFRAME_UNSUPPORTED")
        context = context or {}
        available = set(frame.columns).union(context)
        missing = sorted(set(definition.required_data).difference(available))
        if missing:
            return FactorOutput(definition, UNSUPPORTED_DATA_REQUIREMENT, None, f"MISSING:{','.join(missing)}")
        p = self.parameters(definition, parameters)
        values = self._calculate(definition.factor_id, frame, p, context, market)
        if definition.output_type == "CATEGORY":
            values = values.astype("object")
            values.iloc[: definition.warmup_bars] = None
        else:
            values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
            values.iloc[: definition.warmup_bars] = np.nan
        values.name = f"{definition.factor_id}@{definition.version}"
        warning = None
        if market == "NSE" and factor_id in {
            "roc", "market_structure", "swing_breakout", "room_to_resistance", "room_to_support"
        } and "corporate_action_adjusted" not in frame:
            warning = "CORPORATE_ACTION_ADJUSTMENT_UNVERIFIED"
        return FactorOutput(definition, "SUPPORTED", values, warning)

    def _calculate(self, factor_id: str, frame: pd.DataFrame, p: dict[str, Any], context: dict[str, pd.Series], market: str) -> pd.Series:
        close = frame["close"].astype(float)
        high = frame["high"].astype(float) if "high" in frame else close
        low = frame["low"].astype(float) if "low" in frame else close
        volume = frame["volume"].astype(float) if "volume" in frame else pd.Series(np.nan, index=frame.index)
        if factor_id == "ema_alignment":
            return np.sign(close.ewm(span=p["fast"], adjust=False).mean() - close.ewm(span=p["slow"], adjust=False).mean())
        if factor_id == "vwap_slope":
            typical = (high + low + close) / 3
            vwap = (typical * volume).rolling(p["period"]).sum() / volume.rolling(p["period"]).sum()
            return vwap.pct_change()
        if factor_id == "market_structure":
            lookback = p["lookback"]
            return ((high.rolling(lookback).max() > high.shift(lookback).rolling(lookback).max()) & (low.rolling(lookback).min() > low.shift(lookback).rolling(lookback).min())).astype(float)
        if factor_id == "adx":
            length = p["length"]
            up = high.diff()
            down = -low.diff()
            plus_dm = up.where((up > down) & (up > 0), 0.0)
            minus_dm = down.where((down > up) & (down > 0), 0.0)
            atr = _true_range(frame).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
            plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr
            minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
            return dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
        if factor_id == "normalized_ema_slope":
            ema = close.ewm(span=p["length"], adjust=False).mean()
            return ema.pct_change(p["slopeBars"])
        if factor_id == "trend_efficiency":
            length = p["length"]
            return close.diff(length).abs() / close.diff().abs().rolling(length).sum().replace(0, np.nan)
        if factor_id == "rsi_recovery":
            rsi = _rsi(close, p["length"])
            armed_at: int | None = None
            events = pd.Series(0.0, index=frame.index)
            for index, value in enumerate(rsi.to_numpy(dtype=float, copy=False)):
                if armed_at is not None and p["setupExpiryBars"] > 0 and index - armed_at > p["setupExpiryBars"]:
                    armed_at = None
                previous = float(rsi.iloc[index - 1]) if index else np.nan
                if armed_at is not None and index > armed_at and np.isfinite(previous) and previous <= p["recoveryLevel"] < value:
                    events.iloc[index] = 1.0
                    armed_at = None
                    continue
                if armed_at is None and np.isfinite(value) and p["armLow"] <= value <= p["armHigh"]:
                    armed_at = index
            return events
        if factor_id == "roc":
            return close.pct_change(p["length"]) * 100
        if factor_id == "macd_histogram_acceleration":
            macd = close.ewm(span=p["fast"], adjust=False).mean() - close.ewm(span=p["slow"], adjust=False).mean()
            histogram = macd - macd.ewm(span=p["signal"], adjust=False).mean()
            return histogram.diff()
        if factor_id == "atr_percentile":
            atr_pct = _true_range(frame).ewm(alpha=1 / p["length"], adjust=False).mean() / close * 100
            return _percentile(atr_pct, p["rankWindow"])
        if factor_id == "bollinger_width_percentile":
            mean = close.rolling(p["length"]).mean()
            width = 4 * close.rolling(p["length"]).std(ddof=0) / mean.replace(0, np.nan)
            return _percentile(width, p["rankWindow"])
        if factor_id == "candle_range_percentile":
            return _percentile((high - low) / close.replace(0, np.nan), p["rankWindow"])
        if factor_id == "rvol":
            return volume / volume.rolling(p["length"]).mean().replace(0, np.nan)
        if factor_id == "volume_zscore":
            mean = volume.rolling(p["length"]).mean()
            return (volume - mean) / volume.rolling(p["length"]).std(ddof=0).replace(0, np.nan)
        if factor_id == "volume_breakout":
            return (volume > volume.shift(1).rolling(p["length"]).max()).astype(float)
        if factor_id in {"relative_nifty", "relative_sector"}:
            dependency_key = "benchmark_close" if factor_id == "relative_nifty" else "sector_close"
            dependency = context[dependency_key] if dependency_key in context else frame[dependency_key]
            return close.pct_change(p["length"]) - dependency.astype(float).pct_change(p["length"])
        if factor_id == "relative_ratio_slope":
            benchmark = context["benchmark_close"] if "benchmark_close" in context else frame["benchmark_close"]
            ratio = close / benchmark.astype(float).replace(0, np.nan)
            return ratio.pct_change(p["length"])
        if factor_id == "opening_range_breakout":
            bars = p["bars"]
            timestamp = pd.to_datetime(frame["timestamp"], utc=True)
            local = timestamp.dt.tz_convert("Asia/Kolkata")
            minute = local.dt.hour * 60 + local.dt.minute
            trading = (local.dt.dayofweek < 5) & minute.between(555, 930)
            session = local.dt.date.where(trading)
            order = high.groupby(session, dropna=True).cumcount()
            opening_high = high.where(trading).groupby(session, dropna=True).transform(
                lambda values: values.iloc[:bars].max()
            )
            above = trading & (order >= bars) & (close > opening_high)
            return (above & ~above.groupby(session, dropna=False).shift(1, fill_value=False)).astype(float)
        if factor_id == "swing_breakout":
            return (close > high.shift(1).rolling(p["length"]).max()).astype(float)
        if factor_id == "room_to_resistance":
            resistance = high.shift(1).rolling(p["length"]).max()
            return (resistance - close) / close * 100
        if factor_id == "room_to_support":
            support = low.shift(1).rolling(p["length"]).min()
            return (close - support) / close * 100
        if factor_id == "average_traded_value":
            return (close * volume).rolling(p["length"]).mean()
        if factor_id == "average_volume":
            return volume.rolling(p["length"]).mean()
        if factor_id == "historical_spread":
            ask = context["ask"] if "ask" in context else frame["ask"]
            bid = context["bid"] if "bid" in context else frame["bid"]
            return (ask - bid) / ((ask + bid) / 2) * 10_000
        if factor_id == "slippage_sensitivity":
            returns = context["trade_returns"] if "trade_returns" in context else frame["trade_returns"]
            return returns.astype(float) - p["bps"] / 10_000
        if factor_id == "market_regime":
            length = p["length"]
            efficiency = close.diff(length).abs() / close.diff().abs().rolling(length).sum().replace(0, np.nan)
            atr_pct = _true_range(frame).rolling(length).mean() / close
            vol_rank = _percentile(atr_pct, 100)
            result = pd.Series("RANGE", index=frame.index, dtype="object")
            result.loc[(efficiency >= 0.55) & (vol_rank < 0.9)] = "TREND"
            result.loc[vol_rank <= 0.2] = "COMPRESSION"
            result.loc[(vol_rank >= 0.8) & (vol_rank < 0.95)] = "EXPANSION"
            result.loc[vol_rank >= 0.95] = "EXTREME_CHAOTIC"
            return result
        if factor_id == "session_bucket":
            timestamp = pd.to_datetime(frame["timestamp"], utc=True)
            if market == "NSE":
                local = timestamp.dt.tz_convert("Asia/Kolkata")
                minute = local.dt.hour * 60 + local.dt.minute
                weekday = local.dt.dayofweek < 5
                open_session = weekday & minute.between(555, 930)
                values = np.select(
                    [open_session & (minute < 630), open_session & (minute < 780), open_session],
                    ["NSE_OPEN", "NSE_MID", "NSE_CLOSE"],
                    default="CLOSED_SESSION",
                )
                return pd.Series(values, index=frame.index)
            hour = timestamp.dt.hour
            weekday = timestamp.dt.dayofweek
            session = pd.Series(np.select([hour < 8, hour < 16], ["ASIA_UTC", "EUROPE_UTC"], default="AMERICAS_UTC"), index=frame.index)
            return session + np.where(weekday >= 5, "_WEEKEND", "_WEEKDAY")
        raise ValueError(f"Factor calculation is not implemented: {factor_id}")


def factor_predicate_parameters(
    definition: FactorDefinition, supplied: dict[str, Any] | None
) -> dict[str, Any]:
    supplied = supplied or {}
    resolved: dict[str, Any] = {}
    for item in definition.threshold_parameters:
        name = str(item["name"])
        value = supplied.get(name, item.get("default"))
        if name == "categories":
            if not isinstance(value, list) or not all(isinstance(row, str) for row in value):
                raise ValueError("categories must be a list of category names")
            unknown = sorted(set(value).difference(definition.valid_range or ()))
            if unknown:
                raise ValueError(f"Unsupported categories: {', '.join(unknown)}")
            resolved[name] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        numeric = float(value)
        if "minimum" in item and numeric < float(item["minimum"]):
            raise ValueError(f"{name} must be at least {item['minimum']}")
        if "maximum" in item and numeric > float(item["maximum"]):
            raise ValueError(f"{name} must be at most {item['maximum']}")
        resolved[name] = numeric
    if "minimum" in resolved and "maximum" in resolved and resolved["minimum"] > resolved["maximum"]:
        raise ValueError("minimum must be less than or equal to maximum")
    return resolved


def factor_pass_mask(
    output: FactorOutput,
    supplied: dict[str, Any] | None = None,
    *,
    target_pct: float | None = None,
    stop_loss_pct: float | None = None,
) -> pd.Series:
    if output.status != "SUPPORTED" or output.values is None:
        raise ValueError(output.reason or UNSUPPORTED_DATA_REQUIREMENT)
    definition = output.definition
    parameters = factor_predicate_parameters(definition, supplied)
    values = output.values
    predicate = definition.default_predicate
    if definition.factor_id == "room_to_resistance" and target_pct is not None:
        parameters["minimum"] = max(float(parameters.get("minimum", 0.0)), target_pct)
    if definition.factor_id == "room_to_support" and stop_loss_pct is not None:
        parameters["minimum"] = max(float(parameters.get("minimum", 0.0)), stop_loss_pct)
    if predicate in {"TRUE", "POSITIVE"}:
        return pd.to_numeric(values, errors="coerce") > 0
    if predicate == "MINIMUM":
        return pd.to_numeric(values, errors="coerce") >= float(parameters["minimum"])
    if predicate == "MAXIMUM":
        return pd.to_numeric(values, errors="coerce") <= float(parameters["maximum"])
    if predicate == "BETWEEN":
        numeric = pd.to_numeric(values, errors="coerce")
        return numeric.between(float(parameters["minimum"]), float(parameters["maximum"]), inclusive="both")
    if predicate == "CATEGORY_IN":
        categories = parameters.get("categories") or list(definition.valid_range or ())
        return values.isin(categories)
    raise ValueError(f"Unsupported factor predicate: {predicate}")
