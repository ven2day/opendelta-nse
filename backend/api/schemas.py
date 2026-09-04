"""Pydantic request models for the non-v2 HTTP routes.

Kept separate from the route modules so they can be imported without pulling
in service singletons.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.config.application_settings import DEFAULT_MAXIMUM_PRICE as GLOBAL_DEFAULT_MAXIMUM_PRICE


class GlobalPriceSettingsRequest(BaseModel):
    minimumPrice: float = Field(ge=0, le=GLOBAL_DEFAULT_MAXIMUM_PRICE)
    maximumPrice: float = Field(gt=0, le=GLOBAL_DEFAULT_MAXIMUM_PRICE)

    @model_validator(mode="after")
    def validate_price_range(self) -> "GlobalPriceSettingsRequest":
        if self.minimumPrice >= self.maximumPrice:
            raise ValueError("Minimum price must be less than maximum price")
        return self


class MarketSymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_market_symbol(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().upper().removesuffix(".NS")