#!/usr/bin/env python3
"""Run a reproducible Market-Aligned VWAP Pullback validation job.

This script intentionally talks only to the local Backtest API. It selects the
requested sample from the locally generated liquidity ranking and writes the
complete response so the reported aggregate metrics remain auditable.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"CANCELLED", "COMPLETE", "FAILED"}


def _symbols(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            str(row.get("symbol") or "").strip().upper().removesuffix(".NS")
            for row in reader
            if str(row.get("symbol") or "").strip()
        ]


def _available_symbols(
    symbols_file: Path,
    ranking_file: Path,
    cache_directory: Path,
    duration_years: int,
    symbol_count: int,
) -> list[str]:
    universe = set(_symbols(symbols_file))
    ranked = [symbol for symbol in _symbols(ranking_file) if symbol in universe]
    ordered = ranked + sorted(universe.difference(ranked))
    available = [
        symbol
        for symbol in dict.fromkeys(ordered)
        if (cache_directory / f"{symbol}-5-{duration_years}y.csv.gz").is_file()
    ]
    if symbol_count > 0:
        available = available[:symbol_count]
    if not available:
        raise RuntimeError("No locally cached symbols match the requested validation sample")
    return available


def _request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Backtest API returned HTTP {error.code}: {detail}") from error


def _summary(job: dict[str, Any]) -> dict[str, Any]:
    result = job["result"]
    metadata = result.get("metadata", {})
    summary = result.get("summary", {})
    return {
        "status": job["status"],
        "elapsedSeconds": job.get("elapsedSeconds"),
        "symbolsProcessed": metadata.get("symbolsProcessed"),
        "configurationHash": metadata.get("configurationHash"),
        "dataSnapshot": metadata.get("dataSnapshot"),
        "resultSource": metadata.get("resultSource"),
        "rawCandidates": summary.get("rawCandidates"),
        "acceptedBuySignals": summary.get("acceptedBuySignals"),
        "executedTrades": summary.get("executedTrades"),
        "winRate": summary.get("winRate"),
        "netPnlAfterCosts": summary.get("netPnl"),
        "expectancy": summary.get("expectancy"),
        "profitFactor": summary.get("profitFactor"),
        "maximumDrawdown": summary.get("maximumDrawdown"),
        "averageR": summary.get("averageR"),
        "walkForward": result.get("walkForwardValidation"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3201")
    parser.add_argument("--symbols-file", type=Path, required=True)
    parser.add_argument("--ranking-file", type=Path, required=True)
    parser.add_argument("--cache-directory", type=Path, required=True)
    parser.add_argument("--duration-years", type=int, choices=(1, 3), default=1)
    parser.add_argument("--symbol-count", type=int, default=1)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    symbols = _available_symbols(
        args.symbols_file,
        args.ranking_file,
        args.cache_directory,
        args.duration_years,
        args.symbol_count,
    )
    payload = {
        "symbols": symbols,
        "strategyMode": "market_aligned_vwap_pullback_scalper",
        "universeMode": "all" if args.symbol_count == 0 else "selected",
        "runId": f"validation-{args.label}",
        "cachePolicy": "RUN_AGAIN",
        "durationYears": args.duration_years,
        "timeframe": "5m",
    }
    created = _request_json(f"{args.base_url}/backtest/jobs", payload)
    job_id = created["jobId"]
    last_report = 0.0
    while True:
        job = _request_json(f"{args.base_url}/backtest/jobs/{job_id}")
        now = time.monotonic()
        if now - last_report >= 10 or job["status"] in TERMINAL_STATUSES:
            print(json.dumps({
                "status": job["status"],
                "stage": job.get("currentStage"),
                "symbols": [job.get("symbolsCompleted"), job.get("symbolsTotal")],
                "elapsedSeconds": job.get("elapsedSeconds"),
                "candidates": job.get("candidatesFound"),
            }), flush=True)
            last_report = now
        if job["status"] in TERMINAL_STATUSES:
            break
        time.sleep(2)
    if job["status"] != "COMPLETE":
        raise RuntimeError(f"Validation job ended as {job['status']}: {job.get('error')}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(job, indent=2), encoding="utf-8")
    print(json.dumps(_summary(job), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
