#!/usr/bin/env python3
"""Hash fixed-as-of outputs for the two preserved backtest strategies."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime

from backtest_api import BacktestRequest, get_store, run_backtest
from main import IST


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: regression_existing_strategies.py AS_OF_ISO8601")
    as_of = datetime.fromisoformat(sys.argv[1])
    as_of = as_of.replace(tzinfo=IST) if as_of.tzinfo is None else as_of.astimezone(IST)
    store = get_store()
    symbol = sorted(store.universe())[0]
    output: dict[str, object] = {"symbolSelection": "FIRST_SORTED_UNIVERSE_SYMBOL"}
    for strategy in ("rsi_range", "rsi_recovery"):
        result = run_backtest(
            BacktestRequest(
                symbols=[symbol],
                strategyMode=strategy,
                timeframe="5m",
                durationYears=1,
                runId=f"preserved-{strategy}",
            ),
            store,
            as_of,
        )
        stable = {
            "results": result.get("results"),
            "summary": result.get("summary"),
            "errors": result.get("errors"),
        }
        output[strategy] = {
            "sha256": _digest(stable),
            "resultCount": len(result.get("results") or []),
            "errorCount": len(result.get("errors") or []),
        }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
