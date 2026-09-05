from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.api import platform_routes


class CryptoStatus:
    def __init__(self, *, instruments: int, last_scan: datetime | None, status: str = "READY") -> None:
        self.instruments = instruments
        self.last_scan = last_scan
        self.engine_status = status

    def status(self) -> dict[str, object]:
        return {
            "engineStatus": self.engine_status,
            "configuredInstruments": self.instruments,
            "lastScan": self.last_scan.isoformat() if self.last_scan else None,
            "pollingSeconds": 60,
        }


def test_crypto_overview_reports_continuous_fresh_feed(monkeypatch) -> None:
    service = CryptoStatus(instruments=2, last_scan=datetime.now(UTC) - timedelta(seconds=30))
    monkeypatch.setattr(platform_routes, "get_crypto_market_service", lambda: service)

    payload = platform_routes.platform_overview_payload("crypto")

    assert payload["market"] == "CRYPTO"
    assert payload["dataFreshness"]["status"] == "FRESH"
    assert payload["dataFreshness"]["reason"] == "MARKET_OPEN_24_7"
    assert payload["jobStatus"] == {
        "status": "RUNNING",
        "engineStatus": "READY",
        "connectionStatus": "CONNECTED",
    }


def test_crypto_overview_exposes_missing_instrument_setup(monkeypatch) -> None:
    service = CryptoStatus(instruments=0, last_scan=datetime.now(UTC))
    monkeypatch.setattr(platform_routes, "get_crypto_market_service", lambda: service)

    payload = platform_routes.platform_overview_payload("CRYPTO")

    assert payload["dataFreshness"]["status"] == "UNAVAILABLE"
    assert payload["dataFreshness"]["ageSeconds"] is not None
    assert payload["dataFreshness"]["reason"] == "NO_CONFIGURED_INSTRUMENTS"


def test_crypto_overview_marks_a_stalled_24_7_feed_stale(monkeypatch) -> None:
    service = CryptoStatus(instruments=1, last_scan=datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setattr(platform_routes, "get_crypto_market_service", lambda: service)

    payload = platform_routes.platform_overview_payload("CRYPTO")

    assert payload["dataFreshness"]["status"] == "STALE"
    assert payload["dataFreshness"]["reason"] == "MARKET_24_7_DATA_LAGGING"
