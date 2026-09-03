"""Versioned, backend-owned symbol universes that can be selected without typing symbols."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniversePreset:
    preset_id: str
    market: str
    name: str
    description: str
    as_of: str
    source_url: str
    symbols: tuple[str, ...]

    def public(self) -> dict[str, object]:
        return {
            "presetId": self.preset_id,
            "market": self.market,
            "name": self.name,
            "description": self.description,
            "asOf": self.as_of,
            "sourceUrl": self.source_url,
            "symbols": list(self.symbols),
        }


# Official NSE Indices constituent snapshots as at the last trading day of August 2026.
# Keep these snapshots immutable: update them by changing `as_of` and the complete tuple
# together so old backtest runs retain their own resolved symbol list.
NIFTY_50 = UniversePreset(
    preset_id="nifty_50",
    market="NSE",
    name="NIFTY 50",
    description="Official NIFTY 50 constituents",
    as_of="2026-08-31",
    source_url="https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    symbols=(
        "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
        "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
        "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM",
        "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR",
        "ICICIBANK", "ITC", "INFY", "INDIGO", "JSWSTEEL", "JIOFIN",
        "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NTPC",
        "NESTLEIND", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
        "SHRIRAMFIN", "SBIN", "SUNPHARMA", "TCS", "TATACONSUM", "TMPV",
        "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
    ),
)

NIFTY_TOP_20 = UniversePreset(
    preset_id="nifty_top_20",
    market="NSE",
    name="NIFTY Top 20 Equal Weight",
    description="Official NIFTY Top 20 Equal Weight constituents",
    as_of="2026-08-31",
    source_url="https://www.niftyindices.com/IndexConstituent/ind_niftyTop20EqualWeight_list.csv",
    symbols=(
        "AXISBANK", "BAJFINANCE", "BHARTIARTL", "ETERNAL", "HCLTECH",
        "HDFCBANK", "HINDUNILVR", "ICICIBANK", "ITC", "INFY", "KOTAKBANK",
        "LT", "M&M", "MARUTI", "NTPC", "RELIANCE", "SBIN", "SUNPHARMA",
        "TCS", "TITAN",
    ),
)

UNIVERSE_PRESETS = (NIFTY_50, NIFTY_TOP_20)


def list_universe_presets(market: str | None = None) -> list[UniversePreset]:
    key = market.strip().upper() if market else None
    return [preset for preset in UNIVERSE_PRESETS if key is None or preset.market == key]


def get_universe_preset(preset_id: str, market: str) -> UniversePreset:
    key = preset_id.strip().lower()
    market_key = market.strip().upper()
    try:
        return next(preset for preset in UNIVERSE_PRESETS if preset.preset_id == key and preset.market == market_key)
    except StopIteration as error:
        raise KeyError(f"Universe preset {preset_id!r} is not available for {market_key}") from error
