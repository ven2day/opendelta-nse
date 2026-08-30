from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

import market_data_admin
from crypto_engine import CryptoMarketRepository
from market_core import MarketInstrument, instrument_identifier


class EnqueueStore:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def enqueue_backfill(self, **values: object) -> uuid.UUID:
        self.requests.append(values)
        return uuid.uuid5(uuid.NAMESPACE_URL, str(values))


def window() -> tuple[datetime, datetime]:
    return datetime(2024, 9, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)


def test_enqueue_nse_universe_uses_current_symbol_to_security_mapping(
    monkeypatch, tmp_path: Path
) -> None:
    store = EnqueueStore()
    start, end = window()
    monkeypatch.setattr(market_data_admin, "load_symbols", lambda _: ["HDFCBANK", "MISSING"])
    monkeypatch.setattr(market_data_admin, "download_instrument_master", lambda _: pd.DataFrame())
    monkeypatch.setattr(
        market_data_admin,
        "build_security_map",
        lambda symbols, instruments: ({"HDFCBANK": "1333"}, ["MISSING"]),
    )

    jobs, missing = market_data_admin.enqueue_nse_universe(
        store,  # type: ignore[arg-type]
        symbols_file=tmp_path / "symbols.csv",
        instrument_master_url="https://example.test/instruments.csv",
        timeframe="5m",
        start=start,
        end=end,
        chunk_days=30,
        max_attempts=5,
    )

    assert len(jobs) == 1
    assert missing == ["MISSING"]
    assert store.requests[0]["instrument_id"] == "1333"
    assert store.requests[0]["symbol"] == "HDFCBANK"


def test_enqueue_okx_configured_includes_only_active_crypto_instruments(tmp_path: Path) -> None:
    database = (tmp_path / "crypto.sqlite3").resolve()
    repository = CryptoMarketRepository(database)
    active = MarketInstrument(
        instrument_id=instrument_identifier("OKX", "BTC-USDT"),
        provider="OKX",
        provider_symbol="BTC-USDT",
        display_symbol="BTCUSDT",
        market="CRYPTO",
        instrument_type="SPOT",
        base_currency="BTC",
        quote_currency="USDT",
        tick_size="0.1",
        quantity_step="0.00001",
        minimum_quantity="0.00001",
        minimum_notional="0",
        contract_multiplier="1",
    )
    repository.save_instrument(active)
    store = EnqueueStore()
    start, end = window()

    jobs = market_data_admin.enqueue_okx_configured(
        store,  # type: ignore[arg-type]
        database_path=database,
        timeframe="5m",
        start=start,
        end=end,
        chunk_days=30,
        max_attempts=5,
    )

    assert len(jobs) == 1
    assert store.requests[0]["instrument_id"] == active.instrument_id
    assert store.requests[0]["provider"] == "OKX"
