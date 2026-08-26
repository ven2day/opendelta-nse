import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pandas as pd

from main import (
    OUTPUT_COLUMNS,
    RSI_PERIOD,
    DhanAPIError,
    DhanClient,
    build_security_map,
    build_session_consistent_output,
    calculate_recent_levels,
    calculate_rsi,
    choose_target_session,
    generate_totp,
    historical_payload_to_frame,
    merge_live_quote,
    parse_instrument_master,
    process_symbol,
    write_csv_atomically,
)


class ProcessSymbolTests(unittest.TestCase):
    def test_returns_previous_and_current_rsi_without_intraday_volumes(self):
        index = pd.date_range("2026-07-20", periods=RSI_PERIOD + 6, freq="B")
        closes = pd.Series(
            [
                100,
                102,
                101,
                104,
                103,
                105,
                108,
                106,
                109,
                111,
                110,
                112,
                115,
                113,
                116,
                114,
                117,
                119,
                118,
                121,
            ],
            index=index,
            dtype="float64",
        )
        data = pd.DataFrame(
            {"Close": closes, "Volume": range(1_000, 1_000 + len(closes))},
            index=index,
        )

        result = process_symbol("TEST.NS", data)
        expected_rsi = calculate_rsi(closes, RSI_PERIOD)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["symbol"], "TEST")
        self.assertEqual(result["previous_close"], round(float(closes.iloc[-2]), 2))
        self.assertEqual(result["entry_price"], round(float(closes.iloc[-1]), 2))
        self.assertEqual(
            result["previous_rsi_14"], round(float(expected_rsi.iloc[-2]), 2)
        )
        self.assertEqual(result["rsi_14"], round(float(expected_rsi.iloc[-1]), 2))
        self.assertEqual(result["volume_24h"], 1_000 + len(closes) - 1)
        self.assertIn("support_1_price", result)
        self.assertIn("resistance_2_time", result)
        self.assertNotIn("volume_2h", result)
        self.assertNotIn("volume_4h", result)

    def test_parses_epoch_timestamps_as_ist_daily_candles(self):
        timestamps = [
            int(value.timestamp())
            for value in pd.date_range(
                "2026-07-20 00:00:00+00:00", periods=20, freq="D"
            )
        ]
        payload = {
            "timestamp": timestamps,
            "open": list(range(100, 120)),
            "high": list(range(101, 121)),
            "low": list(range(99, 119)),
            "close": list(range(100, 120)),
            "volume": list(range(1_000, 1_020)),
        }

        frame = historical_payload_to_frame(payload)

        self.assertEqual(str(frame.index.tz), "Asia/Kolkata")
        self.assertEqual(frame.index[-1].date(), date(2026, 8, 8))
        self.assertEqual(frame.iloc[-1]["Volume"], 1_019)

    def test_live_quote_becomes_todays_partial_daily_candle(self):
        index = pd.bdate_range(
            end="2026-08-21",
            periods=RSI_PERIOD + 6,
            tz="Asia/Kolkata",
        )
        daily = pd.DataFrame(
            {
                "Open": range(100, 120),
                "High": range(102, 122),
                "Low": range(99, 119),
                "Close": range(101, 121),
                "Volume": range(1_000, 1_020),
            },
            index=index,
        )
        quote = {
            "last_price": 125.5,
            "last_trade_time": "24/08/2026 14:33:41",
            "volume": 987_654,
            "ohlc": {"open": 121.0, "high": 126.0, "low": 120.5, "close": 120.0},
        }

        merged = merge_live_quote(daily, quote, date(2026, 8, 24))
        result = process_symbol("TEST", merged)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["trading_date"], date(2026, 8, 24))
        self.assertEqual(result["previous_date"], date(2026, 8, 21))
        self.assertEqual(result["previous_close"], 120.0)
        self.assertEqual(result["entry_price"], 125.5)
        self.assertEqual(result["volume_24h"], 987_654)

    def test_previous_session_quote_survives_after_midnight_rollover(self):
        index = pd.bdate_range(
            end="2026-08-25",
            periods=RSI_PERIOD + 6,
            tz="Asia/Kolkata",
        )
        daily = pd.DataFrame(
            {
                "Open": range(100, 120),
                "High": range(102, 122),
                "Low": range(99, 119),
                "Close": range(101, 121),
                "Volume": range(1_000, 1_020),
            },
            index=index,
        )
        quote = {
            "last_price": 125.5,
            "last_trade_time": "26/08/2026 15:29:58",
            "volume": 987_654,
            "ohlc": {"open": 121.0, "high": 126.0, "low": 120.5, "close": 120.0},
        }

        merged = merge_live_quote(daily, quote, date(2026, 8, 27))
        result = process_symbol("TEST", merged)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["trading_date"], date(2026, 8, 26))
        self.assertEqual(result["previous_date"], date(2026, 8, 25))
        self.assertEqual(result["entry_price"], 125.5)

    def test_stale_quote_cannot_replace_newer_historical_session(self):
        index = pd.bdate_range(
            end="2026-08-26",
            periods=RSI_PERIOD + 6,
            tz="Asia/Kolkata",
        )
        daily = pd.DataFrame(
            {
                "Open": range(100, 120),
                "High": range(102, 122),
                "Low": range(99, 119),
                "Close": range(101, 121),
                "Volume": range(1_000, 1_020),
            },
            index=index,
        )
        quote = {
            "last_price": 99.0,
            "last_trade_time": "25/08/2026 15:29:58",
            "volume": 100,
            "ohlc": {"open": 99.0, "high": 100.0, "low": 98.0, "close": 98.5},
        }

        merged = merge_live_quote(daily, quote, date(2026, 8, 27))

        self.assertEqual(merged.index[-1].date(), date(2026, 8, 26))
        self.assertEqual(float(merged.iloc[-1]["Close"]), 120.0)


class DhanHelpersTests(unittest.TestCase):
    def test_totp_matches_rfc_6238_vector_truncated_to_six_digits(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(generate_totp(secret, timestamp=59), "287082")

    def test_access_token_rechecks_shared_cache_instead_of_using_stale_memory(self):
        client = DhanClient(SimpleNamespace())
        client._access_token = "stale-token"
        client._load_cached_token = Mock(return_value="new-token")
        client._generate_access_token = Mock()

        self.assertEqual(client.access_token(), "new-token")
        client._generate_access_token.assert_not_called()

    def test_invalid_token_is_refreshed_and_request_is_retried_once(self):
        client = DhanClient(SimpleNamespace())
        client.access_token = Mock(return_value="expired-token")
        client._refresh_access_token = Mock(return_value="fresh-token")
        client._request_json = Mock(
            side_effect=[DhanAPIError("Dhan API returned HTTP 400: DH-906 Invalid Token"), {"ok": True}]
        )

        payload = client._request_with_access_token(
            "POST",
            "https://api.dhan.co/v2/charts/intraday",
            body={"securityId": "123"},
            throttle_data_api=True,
        )

        self.assertEqual(payload, {"ok": True})
        client._refresh_access_token.assert_called_once_with("expired-token")
        self.assertEqual(
            client._request_json.call_args_list,
            [
                call(
                    "POST",
                    "https://api.dhan.co/v2/charts/intraday",
                    headers={"access-token": "expired-token"},
                    body={"securityId": "123"},
                    throttle_data_api=True,
                ),
                call(
                    "POST",
                    "https://api.dhan.co/v2/charts/intraday",
                    headers={"access-token": "fresh-token"},
                    body={"securityId": "123"},
                    throttle_data_api=True,
                ),
            ],
        )

    def test_market_quote_fetches_all_security_ids_in_one_request(self):
        config = SimpleNamespace(
            base_url="https://api.dhan.co/v2",
            client_id="1234567890",
            exchange_segment="NSE_EQ",
        )
        client = DhanClient(config)
        client._request_with_access_token = Mock(
            return_value={
                "status": "success",
                "data": {
                    "NSE_EQ": {
                        "111": {"last_price": 100.0},
                        "222": {"last_price": 200.0},
                    }
                },
            }
        )

        quotes = client.market_quote(["111", "222", "111"])

        self.assertEqual(set(quotes), {"111", "222"})
        client._request_with_access_token.assert_called_once_with(
            "POST",
            "https://api.dhan.co/v2/marketfeed/quote",
            headers={"client-id": "1234567890"},
            body={"NSE_EQ": [111, 222]},
        )

    def test_instrument_map_prefers_eq_series_for_duplicate_symbols(self):
        csv_data = (
            b"SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,"
            b"SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES\n"
            b"NSE,E,111,EQUITY,MOTHERSON,D1\n"
            b"NSE,E,222,EQUITY,MOTHERSON,EQ\n"
            b"NSE,E,333,EQUITY,LUPIN,EQ\n"
            b"BSE,E,444,EQUITY,LUPIN,A\n"
        )

        instruments = parse_instrument_master(csv_data)
        security_map, missing = build_security_map(
            ["MOTHERSON", "LUPIN", "UNKNOWN"], instruments
        )

        self.assertEqual(security_map["MOTHERSON"], "222")
        self.assertEqual(security_map["LUPIN"], "333")
        self.assertEqual(missing, ["UNKNOWN"])

    def test_finds_two_most_recent_confirmed_daily_supports_and_resistances(self):
        index = pd.date_range(
            "2026-08-03",
            periods=12,
            freq="B",
            tz="Asia/Kolkata",
        )
        daily = pd.DataFrame(
            {
                "High": [101, 105, 102, 108, 103, 104, 110, 103, 106, 102, 109, 104],
                "Low": [100, 99, 95, 99, 101, 98, 96, 98, 100, 94, 99, 101],
            },
            index=index,
        )

        levels = calculate_recent_levels(daily, date(2026, 8, 18))

        self.assertEqual(levels["support_1_price"], 94)
        self.assertEqual(levels["support_1_time"], "2026-08-14")
        self.assertEqual(levels["support_2_price"], 96)
        self.assertEqual(levels["support_2_time"], "2026-08-11")
        self.assertEqual(levels["resistance_1_price"], 110)
        self.assertEqual(levels["resistance_1_time"], "2026-08-11")
        self.assertEqual(levels["resistance_2_price"], 108)
        self.assertEqual(levels["resistance_2_time"], "2026-08-06")

    def test_output_contains_only_the_common_target_session(self):
        current = date(2026, 8, 21)
        stale = date(2026, 8, 20)
        results = {
            "A": {"symbol": "A", "trading_date": current, "entry_price": 100},
            "B": {"symbol": "B", "trading_date": current, "entry_price": 200},
            "C": {"symbol": "C", "trading_date": stale, "entry_price": 300},
        }

        target = choose_target_session(results)
        output = build_session_consistent_output(["A", "B", "C", "D"], results, target)

        self.assertEqual(target, current)
        self.assertEqual(output.columns.tolist(), OUTPUT_COLUMNS)
        self.assertEqual(output.loc[0, "entry_price"], 100)
        self.assertTrue(pd.isna(output.loc[2, "trading_date"]))
        self.assertTrue(pd.isna(output.loc[3, "entry_price"]))

    def test_csv_write_is_atomic_and_keeps_all_rows(self):
        output = pd.DataFrame(
            [
                {"rank": 1, "symbol": "A"},
                {"rank": 2, "symbol": "B"},
            ]
        ).reindex(columns=OUTPUT_COLUMNS)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "data" / "nse.csv"
            write_csv_atomically(output, target)
            loaded = pd.read_csv(target)

        self.assertEqual(loaded["symbol"].tolist(), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
