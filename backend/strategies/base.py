"""The stable Strategy plugin interface.

A strategy decides BUY / SELL / NONE for the latest completed candle and nothing
else. It must not touch a database, a broker or exchange API, a WebSocket, a
backtest loop, or portfolio accounting; those belong to the engines that call
it. Adding a strategy means one new file implementing this interface and one
``STRATEGIES.register(...)`` call.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd

from backend.core.models import Market, MarketContext, SignalDecision

FieldType = str  # "integer" | "integer_array" | "number" | "boolean" | "string"

ConfigSchema = Mapping[str, Mapping[str, Any]]
"""``{field_name: {"type": ..., "default": ..., "minimum"?, "maximum"?, "enum"?, "label"?}}``

The frontend renders settings forms from this; engines merge defaults from it.
"""


@runtime_checkable
class Strategy(Protocol):
    strategy_id: str
    name: str
    version: str
    supported_markets: tuple[Market, ...]
    supported_timeframes: tuple[str, ...]
    config_schema: ConfigSchema

    def required_history(self, config: Mapping[str, Any]) -> int:
        """Number of completed candles needed before ``evaluate`` is meaningful."""

    def validate_config(self, config: Mapping[str, Any]) -> None:
        """Raise ``ValueError`` for an invalid strategy-specific configuration."""

    def evaluate(
        self,
        candles: pd.DataFrame,
        market_context: MarketContext,
        config: Mapping[str, Any],
    ) -> SignalDecision:
        """Decide on the last completed candle in ``candles``; never look ahead."""


def default_config(schema: ConfigSchema) -> dict[str, Any]:
    return {name: definition["default"] for name, definition in schema.items() if "default" in definition}


def resolve_config(schema: ConfigSchema, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge caller-supplied values over schema defaults, rejecting unknown keys."""
    resolved = default_config(schema)
    unknown = sorted(set(config or {}) - set(schema))
    if unknown:
        raise ValueError("Unknown strategy configuration keys: " + ", ".join(unknown))
    for name, value in (config or {}).items():
        definition = schema[name]
        kind = definition.get("type")
        if kind == "integer":
            if isinstance(value, bool) or not float(value).is_integer():
                raise ValueError(f"{name} must be a whole number")
            value = int(value)
        elif kind == "integer_array":
            if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
                raise ValueError(f"{name} must be an integer array")
            converted: list[int] = []
            for item in value:
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not float(item).is_integer():
                    raise ValueError(f"{name} must contain whole numbers")
                converted.append(int(item))
            if "minItems" in definition and len(converted) < definition["minItems"]:
                raise ValueError(f"{name} must contain at least {definition['minItems']} values")
            if "maxItems" in definition and len(converted) > definition["maxItems"]:
                raise ValueError(f"{name} must contain at most {definition['maxItems']} values")
            if "minimum" in definition and any(item < definition["minimum"] for item in converted):
                raise ValueError(f"{name} values must be at least {definition['minimum']}")
            if "maximum" in definition and any(item > definition["maximum"] for item in converted):
                raise ValueError(f"{name} values must be at most {definition['maximum']}")
            value = converted
        elif kind == "number":
            if isinstance(value, bool):
                raise ValueError(f"{name} must be a number")
            value = float(value)
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
        elif kind == "string":
            value = str(value)
        if kind != "integer_array" and "minimum" in definition and value < definition["minimum"]:
            raise ValueError(f"{name} must be at least {definition['minimum']}")
        if kind != "integer_array" and "maximum" in definition and value > definition["maximum"]:
            raise ValueError(f"{name} must be at most {definition['maximum']}")
        if "enum" in definition and value not in definition["enum"]:
            raise ValueError(f"{name} must be one of: " + ", ".join(map(str, definition["enum"])))
        resolved[name] = value
    return resolved


DECISION_FRAME_COLUMNS = ("Open", "High", "Low", "Close", "Volume", "Decision", "SignalPrice", "TargetPrice", "StopPrice")


def decision_frame(strategy: Strategy, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> pd.DataFrame:
    """One row per completed candle with the strategy's decision for that candle.

    A strategy may provide ``decision_frame`` itself (a vectorised path over the
    same evaluator); otherwise this evaluates every history prefix, which is
    always correct but slow. Either way the result is causal: row *t* depends
    only on candles up to *t*.
    """
    vectorised = getattr(strategy, "decision_frame", None)
    if callable(vectorised):
        frame = vectorised(candles, market_context, config)
    else:
        from backend.core.models import normalize_candles

        data = normalize_candles(candles, market_context.timezone)
        rows = []
        for position in range(len(data)):
            decision = strategy.evaluate(data.iloc[: position + 1], market_context, config)
            rows.append((decision.decision, decision.signal_price, decision.target_price, decision.stop_price))
        frame = data.copy()
        frame[["Decision", "SignalPrice", "TargetPrice", "StopPrice"]] = pd.DataFrame(rows, index=data.index, columns=["Decision", "SignalPrice", "TargetPrice", "StopPrice"])
    missing = [name for name in DECISION_FRAME_COLUMNS if name not in frame]
    if missing:
        raise ValueError(f"{strategy.strategy_id} decision frame is missing: {', '.join(missing)}")
    return frame


def assert_supported(strategy: Strategy, market_context: MarketContext) -> None:
    if market_context.market not in strategy.supported_markets:
        raise ValueError(f"{strategy.strategy_id} does not support the {market_context.market} market")
    if market_context.timeframe not in strategy.supported_timeframes:
        raise ValueError(f"{strategy.strategy_id} does not support the {market_context.timeframe} timeframe")
