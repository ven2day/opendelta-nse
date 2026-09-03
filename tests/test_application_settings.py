from __future__ import annotations

from pathlib import Path

import pytest

from backend.config.application_settings import (
    ApplicationSettingsRepository,
    DEFAULT_MAXIMUM_PRICE,
    GlobalPriceSettings,
    filter_symbols_by_price,
    prices_by_symbol,
)


def test_defaults_preserve_the_complete_price_universe(tmp_path: Path) -> None:
    settings = ApplicationSettingsRepository(tmp_path).get()
    assert settings.minimum_price == 0
    assert settings.maximum_price == DEFAULT_MAXIMUM_PRICE
    assert settings.updated_at is None
    assert settings.contains(0)
    assert settings.contains(3_000)


def test_price_range_persists_across_repository_instances(tmp_path: Path) -> None:
    saved = ApplicationSettingsRepository(tmp_path).update(110, 3_000)
    loaded = ApplicationSettingsRepository(tmp_path).get()
    assert loaded == saved
    assert loaded.contains(110)
    assert loaded.contains(3_000)
    assert not loaded.contains(109.99)
    assert not loaded.contains(3_000.01)


@pytest.mark.parametrize("minimum,maximum", [(-1, 100), (100, 100), (200, 100), (0, float("inf"))])
def test_invalid_price_ranges_are_rejected(minimum: float, maximum: float) -> None:
    with pytest.raises(ValueError):
        GlobalPriceSettings(minimum_price=minimum, maximum_price=maximum).validate()


def test_market_snapshot_prices_filter_symbols_and_count_missing(tmp_path: Path) -> None:
    snapshot = tmp_path / "market.csv"
    snapshot.write_text(
        "symbol,entry_price\nLOW,109.99\nMIN,110\nMID,500.25\nMAX,3000\nHIGH,3000.01\n",
        encoding="utf-8",
    )
    filtered, missing = filter_symbols_by_price(
        ["LOW", "MIN", "MID", "MAX", "HIGH", "MISSING"],
        prices_by_symbol(snapshot),
        GlobalPriceSettings(110, 3_000),
    )
    assert filtered == ["MIN", "MID", "MAX"]
    assert missing == 1
