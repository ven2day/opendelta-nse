"""Release-contract coverage for the default OpenDelta platform."""

from __future__ import annotations

import pytest
from backend.markets.base import market_spec
from scripts.validate_v2_cutover import collect_checks, validation_payload


def test_v2_cutover_contract_passes_without_credentials_or_mutations() -> None:
    payload = validation_payload()

    assert payload["status"] == "PASS"
    assert payload["productionMutationsAttempted"] is False
    assert payload["externalCredentialsRequired"] is False
    assert all(item["passed"] for item in payload["checks"])


def test_v2_cutover_contract_reports_every_required_boundary() -> None:
    assert {item.name for item in collect_checks()} == {
        "supported-markets",
        "supported-providers",
        "migration-chain",
        "v2-route-surface",
        "v2-platform-defaults",
        "paper-only-execution",
        "agent-safety-boundary",
        "strategy-runner-isolation",
        "obsolete-v1-removal",
    }


@pytest.mark.parametrize("unsupported", ["FOREX", "US_EQUITIES", "CN", "", "nse-cash"])
def test_unknown_markets_fail_closed(unsupported: str) -> None:
    with pytest.raises(ValueError):
        market_spec(unsupported)
