from __future__ import annotations

import io
import struct
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

import backend.collector as main
from backend.markets.nse.oi import (
    DhanInstrument,
    DhanInstrumentCatalog,
    DhanNiftyOiService,
    parse_futures_quote,
    parse_option_chain,
    parse_rolling_option_history,
)
from backend.compat.live_signals import DhanFeedPacket, parse_dhan_feed_packets
from backend.collector import DhanClient, DhanConfig
from backend.markets.nse.oi_regime import OiRegimeRepository


IST = ZoneInfo("Asia/Kolkata")
STAMP = datetime(2026, 8, 27, 10, 15, tzinfo=IST)
EXPIRY = date(2026, 9, 3)


def test_instrument_catalog_resolves_ids_without_symbol_hardcodes() -> None:
    csv = (
        "SECURITY_ID,EXCH_ID,SEGMENT,INSTRUMENT,UNDERLYING_SECURITY_ID,UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,EXPIRY_FLAG\n"
        "13,NSE,I,INDEX,,,NIFTY,NIFTY 50,,,,\n"
        "900,NSE,D,OPTIDX,13,NIFTY,NIFTY,NIFTY CALL,2026-09-03,25050,CE,W\n"
        "901,NSE,D,FUTIDX,13,NIFTY,NIFTY,NIFTY FUT,2026-09-03,,,M\n"
    )
    catalog = DhanInstrumentCatalog.from_csv(csv)
    assert catalog.nifty_underlying().security_id == "13"
    assert catalog.nifty_options(EXPIRY)[0].security_id == "900"
    assert catalog.nifty_futures(STAMP)[0].security_id == "901"


def test_option_chain_maps_to_canonical_live_schema_and_skips_missing_oi() -> None:
    quote = {
        "security_id": "900", "last_price": 100, "previous_close_price": 98,
        "oi": 1_100, "previous_oi": 1_000, "volume": 5_000,
        "implied_volatility": 12.5, "top_bid_price": 99.5, "top_ask_price": 100.5,
        "greeks": {"delta": 0.5, "gamma": 0.01, "theta": -2, "vega": 4},
    }
    payload = {"data": {"last_price": 25_050, "oc": {"25050": {"ce": quote, "pe": {**quote, "security_id": "901"}}, "25100": {"ce": {"security_id": "missing"}}}}}
    rows = parse_option_chain(payload, timestamp=STAMP, expiry=EXPIRY, ingestion_timestamp=STAMP)
    assert len(rows) == 2
    assert rows[0].oi_change == 100
    assert rows[0].source_timestamp == STAMP
    assert rows[0].public()["data_source"] == "DHAN_OPTION_CHAIN"
    assert rows[0].public()["schema_version"] == "nifty-oi-1.0.0"
    assert {row.option_type for row in rows} == {"CALL", "PUT"}


def test_expired_options_use_same_schema_and_causal_expiry_resolver() -> None:
    payload = {"data": {"ce": {
        "timestamp": [int(STAMP.timestamp()) - 300, int(STAMP.timestamp())],
        "open": [99, 100], "high": [102, 103], "low": [98, 99], "close": [100, 101],
        "iv": [12, 13], "volume": [1_000, 1_200], "oi": [5_000, 5_500],
        "strike": [25_050, 25_050], "spot": [25_040, 25_050],
    }}}
    rows = parse_rolling_option_history(
        payload, option_type="CALL", distance_from_atm=0,
        expiry_resolver=lambda timestamp: EXPIRY if timestamp <= STAMP else None,
        ingestion_timestamp=STAMP,
    )
    assert len(rows) == 2
    assert rows[1].previous_ltp == 100
    assert rows[1].previous_open_interest == 5_000
    assert rows[1].oi_change == 500
    assert rows[1].public().keys() == rows[0].public().keys()


def test_futures_quote_canonical_mapping() -> None:
    instrument = DhanInstrument("901", "NSE", "D", "FUTIDX", "13", "NIFTY", "NIFTY", "NIFTY FUT", EXPIRY, None, None, "M")
    row = parse_futures_quote(
        {"last_price": 25_100, "ohlc": {"close": 25_000}, "oi": 10_000, "volume": 2_000},
        instrument, timestamp=STAMP, spot_price=25_050, ingestion_timestamp=STAMP,
    )
    assert row.security_id == "901"
    assert row.basis == 50
    assert row.source_timestamp == STAMP


def test_full_feed_packet_parses_oi_depth_and_received_time() -> None:
    packet = bytearray(82)
    packet[0] = 8
    struct.pack_into("<H", packet, 1, 82)
    packet[3] = 2
    struct.pack_into("<I", packet, 4, 900)
    struct.pack_into("<f", packet, 8, 100.5)
    struct.pack_into("<I", packet, 14, int(STAMP.timestamp()))
    struct.pack_into("<I", packet, 22, 5_000)
    struct.pack_into("<I", packet, 34, 10_000)
    struct.pack_into("<f", packet, 74, 100.0)
    struct.pack_into("<f", packet, 78, 101.0)
    rows = parse_dhan_feed_packets(bytes(packet), STAMP)
    assert len(rows) == 1
    assert rows[0].security_id == "900"
    assert rows[0].open_interest == 10_000
    assert rows[0].bid == 100
    assert rows[0].ask == 101
    assert rows[0].timestamp == STAMP


def test_reconnect_invalidation_discards_pre_disconnect_oi(tmp_path: Path) -> None:
    service = DhanNiftyOiService(None, OiRegimeRepository(tmp_path), tmp_path, clock=lambda: STAMP)  # type: ignore[arg-type]
    service._live_underlying_id = "13"
    service.on_market_feed(DhanFeedPacket(8, 2, "13", STAMP, price=25_050, cumulative_volume=1_000))
    assert service._live_events
    service.invalidate_live_state()
    assert service._live_events == {}
    assert service._live_options == {}
    assert service._live_future is None


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return b'{"ok":true}'


def test_dhan_rate_limit_retry_is_bounded_exponential(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    attempts = 0

    def fake_urlopen(_request, timeout: int):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise HTTPError("https://api.dhan.co/v2/optionchain", 429, "rate", {}, io.BytesIO(b'{"errorCode":"DH-429"}'))
        return _Response()

    sleeps: list[float] = []
    monkeypatch.setattr(main, "urlopen", fake_urlopen)
    config = DhanConfig(
        client_id="1", pin="000000", totp_secret="A", auth_base_url="https://auth",
        base_url="https://api.dhan.co/v2", exchange_segment="NSE_EQ", instrument="EQUITY",
        instrument_master_url="https://master", token_cache_file=tmp_path / "token",
        symbols_file=tmp_path / "symbols", output_file=tmp_path / "out", history_days=1,
        requests_per_second=1, request_retries=2, session_retry_passes=1, minimum_coverage=0.9,
    )
    client = DhanClient(config, sleep=sleeps.append)
    assert client._request_json("GET", "https://api.dhan.co/v2/optionchain") == {"ok": True}
    assert attempts == 3
    assert sleeps == [1, 2]
