"""Configurable screener thresholds. Nothing here is market-specific except the defaults the caller passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ScreenerFilters:
    lookback_days: int = 20
    minimum_price: float | None = None
    maximum_price: float | None = None
    minimum_average_traded_value: float | None = None  # per session, in the market's currency
    minimum_average_volume: float | None = None  # per bar
    minimum_volatility_pct: float | None = None  # average true range as % of price
    maximum_volatility_pct: float | None = None
    minimum_candle_coverage: float = 0.8  # bars observed / bars expected
    minimum_sessions: int = 5
    rank_by: str = "liquidity"
    maximum_symbols: int | None = None  # None = every passing symbol

    def validate(self) -> "ScreenerFilters":
        from backend.screener.ranking import RANKING_KEYS

        if self.lookback_days < 1:
            raise ValueError("lookback_days must be at least 1")
        for name in ("minimum_price", "maximum_price", "minimum_average_traded_value", "minimum_average_volume", "minimum_volatility_pct", "maximum_volatility_pct"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.minimum_price is not None and self.maximum_price is not None and self.minimum_price > self.maximum_price:
            raise ValueError("minimum_price cannot exceed maximum_price")
        if self.minimum_volatility_pct is not None and self.maximum_volatility_pct is not None and self.minimum_volatility_pct > self.maximum_volatility_pct:
            raise ValueError("minimum_volatility_pct cannot exceed maximum_volatility_pct")
        if not 0 < self.minimum_candle_coverage <= 1:
            raise ValueError("minimum_candle_coverage must be in (0, 1]")
        if self.minimum_sessions < 1:
            raise ValueError("minimum_sessions must be at least 1")
        if self.rank_by not in RANKING_KEYS:
            raise ValueError("rank_by must be one of " + ", ".join(RANKING_KEYS))
        if self.maximum_symbols is not None and self.maximum_symbols < 1:
            raise ValueError("maximum_symbols must be at least 1 (omit it to keep every passing symbol)")
        return self

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "ScreenerFilters":
        aliases = {
            "lookbackDays": "lookback_days", "minimumPrice": "minimum_price", "maximumPrice": "maximum_price",
            "minimumAverageTradedValue": "minimum_average_traded_value", "minimumAverageVolume": "minimum_average_volume",
            "minimumVolatilityPct": "minimum_volatility_pct", "maximumVolatilityPct": "maximum_volatility_pct",
            "minimumCandleCoverage": "minimum_candle_coverage", "minimumSessions": "minimum_sessions", "rankBy": "rank_by", "maximumSymbols": "maximum_symbols",
        }
        kwargs: dict[str, Any] = {}
        for key, value in (values or {}).items():
            name = aliases.get(key, key)
            if name not in cls.__dataclass_fields__:
                raise ValueError(f"Unknown screener filter {key!r}")
            kwargs[name] = value
        return cls(**kwargs).validate()

    def public(self) -> dict[str, Any]:
        return {
            "lookbackDays": self.lookback_days, "minimumPrice": self.minimum_price, "maximumPrice": self.maximum_price,
            "minimumAverageTradedValue": self.minimum_average_traded_value, "minimumAverageVolume": self.minimum_average_volume,
            "minimumVolatilityPct": self.minimum_volatility_pct, "maximumVolatilityPct": self.maximum_volatility_pct,
            "minimumCandleCoverage": self.minimum_candle_coverage, "minimumSessions": self.minimum_sessions, "rankBy": self.rank_by, "maximumSymbols": self.maximum_symbols,
        }

    def evaluate(self, metrics: Mapping[str, Any]) -> str | None:
        """The first rejection reason for these metrics, or ``None`` when the symbol passes."""
        if metrics.get("sessions", 0) < self.minimum_sessions:
            return "INSUFFICIENT_SESSIONS"
        if metrics.get("candleCoverage", 0.0) < self.minimum_candle_coverage:
            return "INSUFFICIENT_CANDLE_COVERAGE"
        price = metrics.get("lastPrice")
        if price is None:
            return "NO_PRICE"
        if self.minimum_price is not None and price < self.minimum_price:
            return "PRICE_BELOW_MINIMUM"
        if self.maximum_price is not None and price > self.maximum_price:
            return "PRICE_ABOVE_MAXIMUM"
        if self.minimum_average_traded_value is not None and metrics.get("averageTradedValue", 0.0) < self.minimum_average_traded_value:
            return "LIQUIDITY_BELOW_MINIMUM"
        if self.minimum_average_volume is not None and metrics.get("averageVolume", 0.0) < self.minimum_average_volume:
            return "VOLUME_BELOW_MINIMUM"
        volatility = metrics.get("volatilityPct")
        if self.minimum_volatility_pct is not None and (volatility is None or volatility < self.minimum_volatility_pct):
            return "VOLATILITY_BELOW_MINIMUM"
        if self.maximum_volatility_pct is not None and volatility is not None and volatility > self.maximum_volatility_pct:
            return "VOLATILITY_ABOVE_MAXIMUM"
        return None
