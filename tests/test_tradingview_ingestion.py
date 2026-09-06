from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from backend.integrations.tradingview import (
    TradingViewAlert,
    TradingViewIngestionService,
    TradingViewRejected,
    resolve_watchlist_symbol,
)
from backend.strategies import STRATEGIES


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


class Rows:
    def __init__(self, value):
        self.value = value

    def get(self, *_args):
        return self.value

    def active(self, *_args):
        return self.value

    def symbols(self, *_args, **_kwargs):
        return list(self.value)


class Signals:
    def __init__(self):
        self.events = {}
        self.inserted = []

    def get_external_event(self, source, event_id):
        return self.events.get((source, event_id))

    def insert_new(self, **values):
        self.inserted.append(values)
        row = {
            "signalId": "signal-1",
            "market": values["market"],
            "strategyId": values["strategy_id"],
            "strategyVersion": values["strategy_version"],
            "symbol": values["symbol"],
            "timeframe": values["timeframe"],
            "signalType": values["signal_type"],
            "signalPrice": values["signal_price"],
            "configurationSnapshot": values["configuration_snapshot"],
            "source": values["source"],
            "externalEventId": values["external_event_id"],
        }
        self.events[(values["source"], values["external_event_id"])] = row
        return row


class Broker:
    def __init__(self):
        self.received = []

    def on_signal(self, signal):
        self.received.append(signal)


class Activity:
    def __init__(self):
        self.rows = []

    def record(self, **values):
        self.rows.append(values)
        return values

    def list(self, market, *, limit=100):
        return [item for item in self.rows if item["alert"]["market"] == market][:limit]


def alert(**overrides):
    values = {
        "eventId": "rsi:BTCUSDT:1725624000",
        "webhookKey": "test-webhook-key-12345",
        "market": "CRYPTO",
        "exchange": "OKX",
        "symbol": "OKX:BTCUSDT",
        "timeframe": "15m",
        "strategyId": "rsi_dip_ladder_v1",
        "strategyVersion": STRATEGIES.get("rsi_dip_ladder_v1").version,
        "action": "BUY",
        "candleTimestamp": NOW - timedelta(minutes=15),
        "sentAt": NOW,
        "signalPrice": 55_000,
        "targetPrice": 57_750,
    }
    values.update(overrides)
    return TradingViewAlert(**values)


def service(*, source="TRADINGVIEW", mode="PAPER", symbols=None, events=None):
    strategy = STRATEGIES.get("rsi_dip_ladder_v1")
    deployment = {
        "market": "CRYPTO",
        "strategyId": strategy.strategy_id,
        "strategyVersion": strategy.version,
        "configId": "config-1",
        "universeId": "watchlist-1",
        "timeframe": "15m",
        "mode": mode,
        "signalSource": source,
    }
    signal_rows = Signals()
    broker = Broker()
    result = TradingViewIngestionService(
        registry=STRATEGIES,
        deployments=lambda: Rows(deployment),
        configs=lambda: Rows({"configId": "config-1", "configuration": {"target_pct": 5}, "riskSettings": {"stopLossPct": 2}}),
        universes=lambda: Rows(symbols or ["BTC-USDT", "ETH-USDT"]),
        signals=lambda: signal_rows,
        broker=lambda _market: broker,
        clock=lambda: NOW,
        events=(lambda: events) if events else None,
    )
    return result, signal_rows, broker


def test_resolves_tradingview_ticker_only_against_approved_watchlist():
    assert resolve_watchlist_symbol("OKX:BTCUSDT", ["BTC-USDT", "ETH-USDT"]) == "BTC-USDT"
    assert resolve_watchlist_symbol("NSE:RELIANCE", ["RELIANCE", "TCS"]) == "RELIANCE"
    assert resolve_watchlist_symbol("BTCUSDT", ["BTC-USDT", "BTC/USDT"]) is None


@patch.dict("os.environ", {"TRADINGVIEW_WEBHOOK_KEY": "test-webhook-key-12345"})
def test_approved_alert_is_stored_and_forwarded_to_paper():
    ingestion, signals, broker = service()
    result = ingestion.ingest(alert())
    assert result["accepted"] is True
    assert result["duplicate"] is False
    assert signals.inserted[0]["symbol"] == "BTC-USDT"
    assert signals.inserted[0]["source"] == "TRADINGVIEW"
    assert signals.inserted[0]["target_price"] == 57_750
    assert signals.inserted[0]["stop_price"] == 53_900
    assert broker.received[0]["signalId"] == "signal-1"


@patch.dict("os.environ", {"TRADINGVIEW_WEBHOOK_KEY": "test-webhook-key-12345"})
def test_duplicate_event_is_idempotent():
    ingestion, signals, broker = service()
    first = ingestion.ingest(alert())
    second = ingestion.ingest(alert())
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(signals.inserted) == 1
    assert len(broker.received) == 1


@pytest.mark.parametrize(
    ("kwargs", "detail"),
    [
        ({"source": "OPENDELTA"}, "not accepting TradingView"),
        ({"mode": "OFF"}, "deployment is off"),
        ({"symbols": ["ETH-USDT"]}, "not in the deployment watchlist"),
    ],
)
@patch.dict("os.environ", {"TRADINGVIEW_WEBHOOK_KEY": "test-webhook-key-12345"})
def test_unapproved_alerts_fail_closed(kwargs, detail):
    ingestion, _, broker = service(**kwargs)
    with pytest.raises(TradingViewRejected, match=detail):
        ingestion.ingest(alert())
    assert broker.received == []


@patch.dict("os.environ", {"TRADINGVIEW_WEBHOOK_KEY": "test-webhook-key-12345"})
def test_stale_and_badly_authenticated_alerts_fail_closed():
    ingestion, _, _ = service()
    with pytest.raises(TradingViewRejected, match="credentials"):
        ingestion.ingest(alert(webhookKey="wrong-webhook-key-12345"))
    with pytest.raises(TradingViewRejected, match="delivery window"):
        ingestion.ingest(alert(sentAt=NOW - timedelta(hours=1)))


@patch.dict("os.environ", {"TRADINGVIEW_WEBHOOK_KEY": "test-webhook-key-12345"})
def test_safe_test_and_activity_expose_results_without_storing_the_key():
    events = Activity()
    ingestion, signals, broker = service(mode="SIGNALS", events=events)
    checked = ingestion.test("CRYPTO", "rsi_dip_ladder_v1", "OKX:BTCUSDT")
    assert checked["safe"] is True
    assert checked["ready"] is True
    assert all(checked["checks"].values())
    assert checked["resolvedSymbol"] == "BTC-USDT"
    assert signals.inserted == []
    assert broker.received == []

    ingestion.ingest_tracked(alert())
    with pytest.raises(TradingViewRejected):
        ingestion.ingest_tracked(alert(webhookKey="wrong-webhook-key-12345", eventId="bad-event-12345"))
    assert [item["accepted"] for item in events.rows] == [True, False]
    assert all("webhookKey" not in item["alert"] for item in events.rows)
