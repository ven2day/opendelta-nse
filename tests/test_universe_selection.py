from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from universe_selection import (
    IST,
    ReferencePrice,
    UniverseRepository,
    UniverseSelectionConfig,
    build_universe,
    historical_symbol_metrics,
    load_reference_prices,
)


def snapshots(specifications: dict[str, list[tuple[str, float, float]]]) -> pd.DataFrame:
    rows = []
    for symbol, outcomes in specifications.items():
        for speed, duration, mae in outcomes:
            open_signal = speed == "TRAPPED"
            binary = "GOOD" if speed in {"FAST_30M", "FAST_2H"} else "NEUTRAL" if speed == "SAME_DAY" else "BAD"
            rows.append(
                {
                    "symbol": symbol,
                    "outcome_target_hit": not open_signal,
                    "outcome_duration_minutes": duration,
                    "outcome_speed_bucket": speed,
                    "outcome_binary_quality_label": binary,
                    "outcome_mae_pct": mae,
                    "outcome_open_at_dataset_end": open_signal,
                }
            )
    return pd.DataFrame(rows)


def price(symbol: str, value: float, timestamp: str = "2026-08-25T15:30:00+05:30") -> ReferencePrice:
    return ReferencePrice(symbol, value, timestamp, "2026-08-25T16:18:00+05:30", "entry_price_completed_session")


def metadata(failed: list[dict] | None = None) -> dict:
    return {
        "runId": "baseline-run",
        "dataFrom": "2025-08-26T09:20:00+05:30",
        "dataTo": "2026-08-25T15:30:00+05:30",
        "symbolsRequested": 750,
        "strategySourceSha256": "abc",
        "failedSymbols": failed or [],
    }


def build(
    frame: pd.DataFrame,
    prices: dict[str, ReferencePrice],
    config: UniverseSelectionConfig,
    *,
    failed: list[dict] | None = None,
    active: dict | None = None,
):
    return build_universe(
        frame,
        historical_symbol_metrics(frame),
        prices,
        config,
        metadata(failed),
        total_nse_symbols=len(set(frame["symbol"])) + len(failed or []),
        failed_symbols=failed or [],
        active=active,
    ).payload


@pytest.fixture
def three_symbols() -> pd.DataFrame:
    return snapshots(
        {
            "AAA": [("FAST_30M", 20, -0.2)] * 4,
            "BBB": [("FAST_2H", 60, -0.4)] * 4,
            "CCC": [("SLOW", 2_000, -1.0)] * 4,
        }
    )


@pytest.mark.parametrize(
    ("value", "selected"),
    [(499.99, False), (500.0, True), (2_000.0, True), (2_000.01, False)],
)
def test_price_boundaries(three_symbols: pd.DataFrame, value: float, selected: bool):
    payload = build(
        three_symbols.loc[three_symbols["symbol"].eq("AAA")],
        {"AAA": price("AAA", value)},
        UniverseSelectionConfig(top_n=1, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
    )
    assert (payload["statistics"]["selected"] == 1) is selected


def test_changing_price_range_changes_eligibility(three_symbols: pd.DataFrame):
    prices = {symbol: price(symbol, value) for symbol, value in {"AAA": 400, "BBB": 700, "CCC": 2_500}.items()}
    narrow = build(three_symbols, prices, UniverseSelectionConfig(top_n=3, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1))
    wide = build(three_symbols, prices, UniverseSelectionConfig(top_n=3, minimum_price=300, maximum_price=3_000, minimum_buy_observations=1))
    assert narrow["selectedSymbols"] == ["BBB"]
    assert set(wide["selectedSymbols"]) == {"AAA", "BBB", "CCC"}


def test_reference_price_timestamp_is_stored(three_symbols: pd.DataFrame):
    payload = build(
        three_symbols,
        {symbol: price(symbol, 750) for symbol in ("AAA", "BBB", "CCC")},
        UniverseSelectionConfig(top_n=1, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
    )
    assert payload["selected"][0]["priceAsOf"] == "2026-08-25T15:30:00+05:30"


def test_market_open_uses_previous_completed_session(tmp_path: Path):
    path = tmp_path / "market.csv"
    path.write_text(
        "symbol,trading_date,previous_date,previous_close,entry_price\nAAA,2026-08-26,2026-08-25,700,725\n",
        encoding="utf-8",
    )
    result = load_reference_prices(path, now=datetime(2026, 8, 26, 10, 0, tzinfo=IST))["AAA"]
    assert result.reference_price == 700
    assert result.price_as_of == "2026-08-25T15:30:00+05:30"
    assert result.source_field == "previous_close"


@pytest.mark.parametrize("top_n", [1, 2, 3])
def test_top_n_returns_at_most_requested(three_symbols: pd.DataFrame, top_n: int):
    payload = build(
        three_symbols,
        {symbol: price(symbol, 750) for symbol in ("AAA", "BBB", "CCC")},
        UniverseSelectionConfig(top_n=top_n, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
    )
    assert len(payload["selectedSymbols"]) == top_n


def test_requesting_more_than_qualify_returns_only_eligible(three_symbols: pd.DataFrame):
    payload = build(
        three_symbols,
        {"AAA": price("AAA", 750), "BBB": price("BBB", 750), "CCC": price("CCC", 10)},
        UniverseSelectionConfig(top_n=300, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
    )
    assert payload["statistics"]["requestedTopN"] == 300
    assert payload["statistics"]["selected"] == 2


def test_quality_ranking_and_order_are_deterministic(three_symbols: pd.DataFrame):
    prices = {symbol: price(symbol, 750) for symbol in ("AAA", "BBB", "CCC")}
    config = UniverseSelectionConfig(top_n=3, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1)
    first = build(three_symbols, prices, config)
    second = build(three_symbols.sample(frac=1, random_state=7), prices, config)
    assert first["selectedSymbols"] == second["selectedSymbols"]
    assert first["selectedSymbols"][0] == "AAA"


def test_tie_breakers_use_good_rate_then_speed_then_open_then_mae_then_symbol():
    frame = snapshots(
        {
            "AAA": [("FAST_30M", 20, -0.2)],
            "BBB": [("FAST_30M", 20, -0.2)],
        }
    )
    result = build(
        frame,
        {"AAA": price("AAA", 700), "BBB": price("BBB", 700)},
        UniverseSelectionConfig(top_n=2, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
    )
    assert result["selectedSymbols"] == ["AAA", "BBB"]


def test_price_filter_happens_before_top_n(three_symbols: pd.DataFrame):
    payload = build(
        three_symbols,
        {"AAA": price("AAA", 100), "BBB": price("BBB", 700), "CCC": price("CCC", 800)},
        UniverseSelectionConfig(top_n=1, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
    )
    assert payload["selectedSymbols"] == ["BBB"]
    assert any(item["symbol"] == "AAA" and item["reason"] == "PRICE_BELOW_MIN" for item in payload["excluded"])


def test_failed_data_symbol_never_enters_calculated_universe(three_symbols: pd.DataFrame):
    payload = build(
        three_symbols,
        {**{symbol: price(symbol, 700) for symbol in ("AAA", "BBB", "CCC")}, "IDEA": price("IDEA", 700)},
        UniverseSelectionConfig(top_n=10, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1),
        failed=[{"symbol": "IDEA", "message": "negative volume"}],
    )
    assert "IDEA" not in payload["selectedSymbols"]
    assert payload["dataQualityExcluded"][0]["reason"] == "DATA_QUALITY_FAILED"


def test_pin_adds_eligible_symbol_outside_top_n_and_exclusion_removes_selected(three_symbols: pd.DataFrame):
    payload = build(
        three_symbols,
        {symbol: price(symbol, 700) for symbol in ("AAA", "BBB", "CCC")},
        UniverseSelectionConfig(
            top_n=1,
            minimum_price=500,
            maximum_price=2_000,
            minimum_buy_observations=1,
            manual_pins=("BBB",),
            manual_exclusions=("AAA",),
        ),
    )
    assert payload["selectedSymbols"] == ["BBB"]
    assert payload["selected"][0]["isPinned"] is True


def test_invalid_data_symbol_cannot_be_pinned(three_symbols: pd.DataFrame):
    with pytest.raises(ValueError, match="Pinned symbols must pass"):
        build(
            three_symbols,
            {"AAA": price("AAA", 700), "IDEA": price("IDEA", 700)},
            UniverseSelectionConfig(
                top_n=1,
                minimum_price=500,
                maximum_price=2_000,
                minimum_buy_observations=1,
                manual_pins=("IDEA",),
            ),
            failed=[{"symbol": "IDEA", "message": "negative volume"}],
        )


def test_frozen_universe_does_not_change_until_explicit_save(tmp_path: Path, three_symbols: pd.DataFrame):
    repository = UniverseRepository(tmp_path / "live")
    config = UniverseSelectionConfig(top_n=1, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1)
    original = build(three_symbols, {"AAA": price("AAA", 700), "BBB": price("BBB", 600), "CCC": price("CCC", 600)}, config)
    frozen = repository.save_and_activate(original)
    changed_preview = build(
        three_symbols,
        {"AAA": price("AAA", 100), "BBB": price("BBB", 600), "CCC": price("CCC", 600)},
        config,
        active=frozen,
    )
    assert repository.load_active()["selectedSymbols"] == frozen["selectedSymbols"]
    assert changed_preview["selectedSymbols"] != frozen["selectedSymbols"]
    assert changed_preview["differences"]["added"]
    assert changed_preview["differences"]["removed"]


def test_confirmed_rebuild_creates_auditable_new_version(tmp_path: Path, three_symbols: pd.DataFrame):
    repository = UniverseRepository(tmp_path / "live")
    config = UniverseSelectionConfig(top_n=1, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1)
    one = repository.save_and_activate(build(three_symbols, {symbol: price(symbol, 700) for symbol in ("AAA", "BBB", "CCC")}, config))
    two = repository.save_and_activate(build(three_symbols, {"AAA": price("AAA", 100), "BBB": price("BBB", 700), "CCC": price("CCC", 700)}, config, active=one))
    assert one["universeVersion"] != two["universeVersion"]
    assert len(repository.history()) == 2
    assert (repository.versions / f"{one['universeVersion']}.json").is_file()
    assert json.loads(repository.active_path.read_text())["universeVersion"] == two["universeVersion"]


def test_overrides_and_csv_persist_with_version(tmp_path: Path, three_symbols: pd.DataFrame):
    repository = UniverseRepository(tmp_path / "live")
    payload = build(
        three_symbols,
        {symbol: price(symbol, 700) for symbol in ("AAA", "BBB", "CCC")},
        UniverseSelectionConfig(top_n=1, minimum_price=500, maximum_price=2_000, minimum_buy_observations=1, manual_pins=("BBB",)),
    )
    record = repository.save_and_activate(payload)
    assert record["configuration"]["manualPins"] == ["BBB"]
    export = repository.export_for()
    text = export.read_text(encoding="utf-8")
    assert "universe_version,rank,symbol" in text
    assert record["universeVersion"] in text
