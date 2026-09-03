"""Built-in universes remain complete, reproducible and compatible with the NSE catalogue."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backend.data.universe_presets import NIFTY_50, NIFTY_TOP_20, get_universe_preset, list_universe_presets


def test_official_nifty_snapshots_have_expected_membership() -> None:
    assert len(NIFTY_50.symbols) == 50
    assert len(set(NIFTY_50.symbols)) == 50
    assert len(NIFTY_TOP_20.symbols) == 20
    assert len(set(NIFTY_TOP_20.symbols)) == 20
    assert set(NIFTY_TOP_20.symbols) < set(NIFTY_50.symbols)
    assert NIFTY_50.as_of == NIFTY_TOP_20.as_of == "2026-08-31"


def test_every_preset_symbol_exists_in_the_deployed_nse_catalogue() -> None:
    with Path("data/symbols.csv").open(newline="", encoding="utf-8-sig") as source:
        catalogue = {row["symbol"].strip().upper() for row in csv.DictReader(source)}
    assert set(NIFTY_50.symbols) <= catalogue
    assert set(NIFTY_TOP_20.symbols) <= catalogue


def test_presets_are_market_scoped_and_unknown_ids_fail_closed() -> None:
    assert [preset.preset_id for preset in list_universe_presets("NSE")] == ["nifty_50", "nifty_top_20"]
    assert list_universe_presets("CRYPTO") == []
    assert get_universe_preset("NIFTY_TOP_20", "NSE") is NIFTY_TOP_20
    with pytest.raises(KeyError):
        get_universe_preset("nifty_50", "CRYPTO")
