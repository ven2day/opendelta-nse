from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

import backend.markets.crypto.strategy as crypto_strategy
from backend.markets.crypto.engine import CryptoMarketRepository, CryptoMarketService
from backend.markets.crypto.providers import OkxPublicProvider, ProviderFactory, ValrPublicProvider
from backend.markets.crypto.strategy import CryptoPullbackConfig, generate_signals, run_pullback_backtest
from backend.markets.common import MarketCandle, MarketInstrument, instrument_identifier
from backend.data.timescale import CanonicalCandle, DualWriteResult


UTC = timezone.utc


def okx_instrument() -> MarketInstrument:
    return MarketInstrument(
        instrument_id=instrument_identifier("OKX", "BTC-USDT"),
        provider="OKX",
        provider_symbol="BTC-USDT",
        display_symbol="BTCUSDT",
        market="CRYPTO",
        instrument_type="SPOT",
        base_currency="BTC",
        quote_currency="USDT",
        tick_size="0.1",
        quantity_step="0.00000001",
        minimum_quantity="0.00001",
        minimum_notional="0",
        contract_multiplier="1",
    )


def candle(opened: datetime, price: float = 100.0) -> MarketCandle:
    return MarketCandle.build(
        provider="OKX",
        provider_symbol="BTC-USDT",
        timeframe="5m",
        open_time=opened,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price + 0.5,
        base_volume=10,
        quote_volume=1_000,
    )


def signal_frame() -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=54, freq="5min", tz="UTC")
    frame = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Volume": 10.0,
            "RSI": 45.0,
            "EMAFast": 101.0,
            "EMASlow": 100.0,
            "ATR": 2.0,
            "VolumeMean": 8.0,
            "RVOL": 1.5,
            "VWAP": 100.0,
        },
        index=index,
    )
    frame.iloc[-2, frame.columns.get_loc("RSI")] = 45.0
    frame.iloc[-2, frame.columns.get_loc("EMAFast")] = 100.5
    frame.iloc[-1, frame.columns.get_loc("RSI")] = 52.0
    frame.iloc[-1, frame.columns.get_loc("Close")] = 102.0
    frame.iloc[-1, frame.columns.get_loc("High")] = 103.0
    frame.iloc[-1, frame.columns.get_loc("Low")] = 100.5
    return frame


def test_okx_instrument_catalog_and_completed_candle_parsing() -> None:
    opened = datetime(2026, 8, 1, tzinfo=UTC)

    def transport(url: str):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/instruments"):
            instrument_type = query["instType"][0]
            if instrument_type == "SWAP":
                return {"code": "0", "data": []}
            return {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "instType": "SPOT",
                        "baseCcy": "BTC",
                        "quoteCcy": "USDT",
                        "tickSz": "0.1",
                        "lotSz": "0.00000001",
                        "minSz": "0.00001",
                        "state": "live",
                    }
                ],
            }
        assert query["bar"] == ["5m"]
        return {
            "code": "0",
            "data": [
                [str(int(opened.timestamp() * 1_000)), "100", "102", "99", "101", "4", "0", "402", "1"]
            ],
        }

    provider = OkxPublicProvider(transport=transport)
    instruments = provider.instruments()
    assert [item.provider_symbol for item in instruments] == ["BTC-USDT"]
    candles = provider.candles(instruments[0], "5m", opened, opened + timedelta(minutes=10))
    assert len(candles) == 1
    assert candles[0].quote_volume == 402
    assert candles[0].complete is True


def test_okx_retries_a_rate_limited_public_request() -> None:
    responses = [
        {"code": "50011", "msg": "Requests too frequent"},
        {"code": "0", "data": []},
    ]
    delays: list[float] = []
    provider = OkxPublicProvider(
        transport=lambda _url: responses.pop(0),
        sleep=delays.append,
    )

    assert provider._request("/api/v5/market/history-candles", {}) == []
    assert delays == [0.5]


def test_valr_catalog_and_bucket_contract() -> None:
    opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
    urls: list[str] = []

    def transport(url: str):
        urls.append(url)
        if url.endswith("/v1/public/pairs"):
            return [
                {
                    "symbol": "BTCUSDT",
                    "baseCurrency": "BTC",
                    "quoteCurrency": "USDT",
                    "active": True,
                    "minBaseAmount": "0.0001",
                    "minQuoteAmount": "1",
                    "tickSize": "1",
                    "baseDecimalPlaces": "8",
                    "currencyPairType": "SPOT",
                }
            ]
        return [
            {
                "currencyPairSymbol": "BTCUSDT",
                "bucketPeriodInSeconds": 300,
                "startTime": opened.isoformat().replace("+00:00", "Z"),
                "open": "100",
                "high": "102",
                "low": "99",
                "close": "101",
                "volume": "5",
                "quoteVolume": "501",
            }
        ]

    provider = ValrPublicProvider(transport=transport)
    instrument = provider.instruments()[0]
    candles = provider.candles(instrument, "5m", opened, opened + timedelta(minutes=10))
    assert instrument.provider_symbol == "BTCUSDT"
    assert instrument.quantity_step == "0.00000001"
    assert len(candles) == 1
    bucket_url = next(url for url in urls if "/buckets?" in url)
    query = parse_qs(urlparse(bucket_url).query)
    assert query["periodSeconds"] == ["300"]
    assert query["includeEmpty"] == ["false"]


def test_shared_signal_generator_and_next_bar_backtest(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = signal_frame()
    config = CryptoPullbackConfig()
    candidates = generate_signals(frame, config)
    assert len(candidates) == 1
    assert candidates[0].side == "BUY"
    assert candidates[0].signal_index == len(frame) - 1

    extended = pd.concat([frame, frame.iloc[[-1]].copy()])
    extended.index = list(frame.index) + [frame.index[-1] + pd.Timedelta(minutes=5)]
    extended.iloc[-1, extended.columns.get_loc("Open")] = 102.0
    extended.iloc[-1, extended.columns.get_loc("High")] = 106.0
    monkeypatch.setattr(crypto_strategy, "candle_frame", lambda candles, configuration: extended)
    result = run_pullback_backtest(okx_instrument(), "5m", [], config)
    assert result["metadata"]["paperOnly"] is True
    assert result["metadata"]["liveOrdersEnabled"] is False
    assert result["summary"]["executedTrades"] == 1
    assert result["trades"][0]["entryTimestamp"] == extended.index[-1].isoformat().replace("+00:00", "Z")


def test_repository_persists_instruments_candles_and_deduplicates_signals(tmp_path: Path) -> None:
    repository = CryptoMarketRepository((tmp_path / "market.sqlite3").resolve())
    instrument = repository.save_instrument(okx_instrument())
    opened = datetime(2026, 8, 1, tzinfo=UTC)
    assert repository.save_candles([candle(opened)]) == 1
    assert len(repository.candles(instrument, "5m", opened, opened + timedelta(hours=1))) == 1
    payload = {
        "signalId": "CSIG-ONE",
        "instrumentId": instrument.instrument_id,
        "signalTimestamp": opened.isoformat(),
        "side": "BUY",
    }
    assert repository.save_signal(payload) is True
    assert repository.save_signal(payload) is False
    assert repository.signals() == [payload]


class FakeProvider:
    name = "OKX"

    def instruments(self) -> list[MarketInstrument]:
        return [okx_instrument()]

    def candles(self, instrument, timeframe, start, end):
        return [candle(start + timedelta(minutes=5 * index), 100 + index * 0.1) for index in range(80)]


class CapturingCanonicalWriter:
    def __init__(self) -> None:
        self.rows: list[CanonicalCandle] = []

    def write(self, candles: list[CanonicalCandle]) -> DualWriteResult:
        self.rows.extend(candles)
        return DualWriteResult("WRITTEN", len(candles), len(candles))


def test_service_validates_additions_against_provider_catalog(tmp_path: Path) -> None:
    repository = CryptoMarketRepository((tmp_path / "market.sqlite3").resolve())
    service = CryptoMarketService(repository, ProviderFactory({"OKX": FakeProvider()}))
    added = service.add_instrument("okx", "btc-usdt")
    assert added.display_symbol == "BTCUSDT"
    assert service.status()["liveOrdersEnabled"] is False
    with pytest.raises(ValueError, match="not an active"):
        service.add_instrument("OKX", "XAUUSD.p")


def test_service_rejects_oversized_interactive_candle_window(tmp_path: Path) -> None:
    repository = CryptoMarketRepository((tmp_path / "market.sqlite3").resolve())
    service = CryptoMarketService(repository, ProviderFactory({"OKX": FakeProvider()}))
    instrument = service.add_instrument("OKX", "BTC-USDT")
    end = datetime(2026, 8, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="20,000"):
        service.sync_candles(instrument, "1m", end - timedelta(days=30), end)


def test_service_dual_writes_provider_candles_without_changing_sqlite_reads(tmp_path: Path) -> None:
    repository = CryptoMarketRepository((tmp_path / "market.sqlite3").resolve())
    writer = CapturingCanonicalWriter()
    service = CryptoMarketService(
        repository,
        ProviderFactory({"OKX": FakeProvider()}),
        canonical_writer=writer,  # type: ignore[arg-type]
    )
    instrument = service.add_instrument("OKX", "BTC-USDT")
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(hours=8)

    legacy_rows = service.sync_candles(instrument, "5m", start, end)

    assert len(legacy_rows) == 80
    assert len(writer.rows) == 80
    assert all(item.market == "CRYPTO" and item.instrument_id == instrument.instrument_id for item in writer.rows)
