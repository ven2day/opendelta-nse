from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def load_symbols(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [row["symbol"].strip().upper() for row in csv.DictReader(handle) if row.get("symbol", "").strip()]


def post_json(url: str, payload: dict[str, Any], attempts: int = 4) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    for attempt in range(1, attempts + 1):
        request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=900) as response:
                return json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt == attempts:
                raise RuntimeError(str(error)) from error
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def summarize(results: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    trades = [trade for result in results for trade in result.get("trades", [])]
    completed = [trade for trade in trades if trade["status"] == "TARGET_HIT"]
    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]
    target_bucket_names = [
        "LE_30_MIN",
        "GT_30_MIN_LE_2_HOURS",
        "GT_2_HOURS_LE_24_HOURS",
        "GT_24_HOURS",
    ]
    session_bucket_names = [
        "SAME_SESSION",
        "NEXT_SESSION",
        "TWO_TO_FIVE_TRADING_DAYS",
        "GT_FIVE_TRADING_DAYS",
    ]

    def buckets(field: str, names: list[str]) -> dict[str, dict[str, float | int]]:
        return {
            name: {
                "count": sum(trade[field] == name for trade in completed),
                "pct": round(sum(trade[field] == name for trade in completed) / len(completed) * 100, 2) if completed else 0.0,
            }
            for name in names
        }

    durations = [float(trade["durationMinutes"]) for trade in completed]
    bars = [float(trade["barsHeld"]) for trade in completed]
    mae = [float(trade["maxAdversePct"]) for trade in completed]
    mfe = [float(trade["maxFavorablePct"]) for trade in completed]
    open_age = [float(trade["durationMinutes"]) for trade in open_trades]
    open_pnl = [float(trade["currentPnlPct"]) for trade in open_trades]
    open_mae = [float(trade["maxAdversePct"]) for trade in open_trades]
    timeline = sorted(
        (
            event
            for trade in trades
            for event in (
                (trade["entryTimestamp"], 1),
                (trade.get("targetHitTimestamp") or trade["lastTimestamp"], -1),
            )
        ),
        key=lambda event: (event[0], -event[1]),
    )
    concurrent = 0
    max_concurrent = 0
    for _, delta in timeline:
        concurrent += delta
        max_concurrent = max(max_concurrent, concurrent)
    oldest_open = max(open_trades, key=lambda trade: float(trade["durationMinutes"]), default=None)
    open_counts = [int(result.get("openSignals", result.get("openPositions", 0))) for result in results]
    max_same_symbol = max(
        (int(result.get("maximumConcurrentOpenSignals", 0)) for result in results),
        default=0,
    )
    ordered = sorted(results, key=lambda result: (-float(result["qualityScore"]), result["symbol"]))

    symbol_fields = [
        "symbol", "buySignals", "targetsHit", "targetHitRate", "medianTargetMinutes",
        "averageMaePct", "medianMaePct", "worstMaePct", "openPositions", "openPct",
        "openSignals", "maximumConcurrentOpenSignals", "averageConcurrentOpenSignals",
        "averageOpenAgeMinutes", "worstOpenPnlPct", "qualityScore",
    ]

    def symbol_row(result: dict[str, Any]) -> dict[str, Any]:
        return {field: result.get(field) for field in symbol_fields}

    return {
        "symbolsProcessed": len(results),
        "symbolsFailed": len(errors),
        "failedSymbols": errors,
        "candleRowsProcessed": sum(int(result["bars"]) for result in results),
        "buySignals": len(trades),
        "targetsHit": len(completed),
        "targetHitRate": round(len(completed) / len(trades) * 100, 2) if trades else 0.0,
        "stillOpen": len(open_trades),
        "totalOpenSignals": len(open_trades),
        "maxConcurrentPositions": max_concurrent,
        "maximumConcurrentSignalsUniverse": max_concurrent,
        "maximumConcurrentSignalsSameSymbol": max_same_symbol,
        "symbolsWithOpenSignals": sum(count > 0 for count in open_counts),
        "averageOpenSignalsPerSymbol": round(len(open_trades) / len(results), 4) if results else 0.0,
        "symbolsWith2PlusOpenSignals": sum(count >= 2 for count in open_counts),
        "symbolsWith5PlusOpenSignals": sum(count >= 5 for count in open_counts),
        "targetSpeedBuckets": buckets("targetSpeedBucket", target_bucket_names),
        "sessionSpeedBuckets": buckets("sessionSpeedBucket", session_bucket_names),
        "averageTargetMinutes": mean(durations),
        "medianTargetMinutes": median(durations),
        "averageBarsToTarget": mean(bars),
        "medianBarsToTarget": median(bars),
        "averageCompletedMaePct": mean(mae),
        "medianCompletedMaePct": median(mae),
        "worstCompletedMaePct": round(min(mae), 4) if mae else None,
        "averageCompletedMfePct": mean(mfe),
        "medianCompletedMfePct": median(mfe),
        "averageOpenAgeMinutes": mean(open_age),
        "medianOpenAgeMinutes": median(open_age),
        "oldestOpenMinutes": round(max(open_age), 2) if open_age else None,
        "oldestOpenSymbol": oldest_open["symbol"] if oldest_open else None,
        "averageOpenPnlPct": mean(open_pnl),
        "worstOpenPnlPct": round(min(open_pnl), 4) if open_pnl else None,
        "averageOpenMaePct": mean(open_mae),
        "worstOpenMaePct": round(min(open_mae), 4) if open_mae else None,
        "top20Symbols": [symbol_row(result) for result in ordered[:20]],
        "bottom20Symbols": [symbol_row(result) for result in ordered[-20:][::-1]],
    }


def compare_with_previous(previous: dict[str, Any], current_summary: dict[str, Any], runtime: float) -> dict[str, Any]:
    old = previous.get("summary", {})
    old_buys = int(old.get("buySignals", 0))
    old_same_symbol = old.get("maximumConcurrentSignalsSameSymbol")
    if old_same_symbol is None:
        old_same_symbol = 1 if old_buys else 0
    fields = {
        "buySignals": (old.get("buySignals"), current_summary.get("buySignals")),
        "targetsHit": (old.get("targetsHit"), current_summary.get("targetsHit")),
        "targetHitRate": (old.get("targetHitRate"), current_summary.get("targetHitRate")),
        "openSignals": (old.get("stillOpen"), current_summary.get("stillOpen")),
        "medianTargetMinutes": (old.get("medianTargetMinutes"), current_summary.get("medianTargetMinutes")),
        "averageCompletedMaePct": (old.get("averageCompletedMaePct"), current_summary.get("averageCompletedMaePct")),
        "medianCompletedMaePct": (old.get("medianCompletedMaePct"), current_summary.get("medianCompletedMaePct")),
        "worstCompletedMaePct": (old.get("worstCompletedMaePct"), current_summary.get("worstCompletedMaePct")),
        "maximumConcurrentSignalsUniverse": (
            old.get("maximumConcurrentSignalsUniverse", old.get("maxConcurrentPositions")),
            current_summary.get("maximumConcurrentSignalsUniverse"),
        ),
        "maximumConcurrentSignalsSameSymbol": (old_same_symbol, current_summary.get("maximumConcurrentSignalsSameSymbol")),
        "runtimeSeconds": (previous.get("runtimeSeconds"), round(runtime, 2)),
    }
    return {
        "oldSemantics": "At most one open observation per symbol; later recovery cycles were suppressed until resolution.",
        "newSemantics": "Every fresh RSI arm/recovery cycle creates an independent observation.",
        "metrics": {
            name: {
                "old": old_value,
                "new": new_value,
                "delta": round(float(new_value) - float(old_value), 4)
                if old_value is not None and new_value is not None
                else None,
            }
            for name, (old_value, new_value) in fields.items()
        },
        "additionalBuySignals": int(current_summary.get("buySignals", 0)) - old_buys,
        "additionalTargetHits": int(current_summary.get("targetsHit", 0)) - int(old.get("targetsHit", 0)),
        "additionalOpenSignals": int(current_summary.get("stillOpen", 0)) - int(old.get("stillOpen", 0)),
        "oldSameSymbolConcurrencySource": "Inferred from the old engine's enforced one-active-observation rule.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deployed baseline RSI Recovery strategy over symbols.csv.")
    parser.add_argument("--url", default="http://127.0.0.1:3200/backtest")
    parser.add_argument("--symbols", type=Path, default=Path("/opt/vento-nse/current/data/symbols.csv"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/opendelta-rsi-recovery-overlap-baseline.json"))
    parser.add_argument("--previous", type=Path, default=Path("/tmp/opendelta-rsi-recovery-baseline.json"))
    arguments = parser.parse_args()

    symbols = load_symbols(arguments.symbols)
    previous_report = json.loads(arguments.previous.read_text(encoding="utf-8")) if arguments.previous.exists() else None
    run_id = f"baseline-5m-1y-{uuid.uuid4()}"
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    batch_metadata: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    total_batches = (len(symbols) + 9) // 10

    for offset in range(0, len(symbols), 10):
        batch = symbols[offset : offset + 10]
        payload = {
            "strategyMode": "rsi_recovery",
            "universeMode": "all",
            "runId": run_id,
            "symbols": batch,
            "durationYears": 1,
            "timeframe": "5m",
            "rsiLength": 14,
            "rsiArmLow": 30,
            "rsiArmHigh": 40,
            "rsiRecovery": 40,
            "emaEnabled": True,
            "emaFast": 9,
            "emaSlow": 20,
            "vwapEnabled": True,
            "volumeEnabled": True,
            "volumeEma": 20,
            "minimumConfirmations": 2,
            "targetPct": 0.5,
            "setupExpiryBars": 50,
            "executionModel": "SIGNAL_CLOSE",
            "buyCostBps": 0,
            "sellCostBps": 0,
            "slippageBps": 0,
        }
        batch_number = offset // 10 + 1
        try:
            response = post_json(arguments.url, payload)
            results.extend(response.get("results", []))
            errors.extend(response.get("errors", []))
            warnings.extend(response.get("warnings", []))
            batch_metadata.append(response.get("metadata", {}))
            print(json.dumps({
                "batch": batch_number,
                "totalBatches": total_batches,
                "symbolsCompleted": min(offset + len(batch), len(symbols)),
                "results": len(response.get("results", [])),
                "errors": len(response.get("errors", [])),
                "elapsedSeconds": round(time.perf_counter() - started, 1),
            }), flush=True)
        except RuntimeError as error:
            errors.extend({"symbol": symbol, "message": f"Batch request failed: {error}"} for symbol in batch)
            print(json.dumps({"batch": batch_number, "requestError": str(error)}), file=sys.stderr, flush=True)

    completed_at = datetime.now(timezone.utc)
    runtime_seconds = time.perf_counter() - started
    data_from_values = [metadata.get("dataFrom") for metadata in batch_metadata if metadata.get("dataFrom")]
    data_to_values = [metadata.get("dataTo") for metadata in batch_metadata if metadata.get("dataTo")]
    summary = summarize(results, errors)
    report = {
        "runId": run_id,
        "strategyMode": "rsi_recovery",
        "strategyVersion": "rsi-recovery-1.1.0",
        "timeframe": "5m",
        "durationYears": 1,
        "executionModel": "SIGNAL_CLOSE",
        "startedAt": started_at.isoformat(),
        "completedAt": completed_at.isoformat(),
        "dataFrom": min(data_from_values) if data_from_values else None,
        "dataTo": max(data_to_values) if data_to_values else None,
        "symbolsRequested": len(symbols),
        "parameters": {key: value for key, value in payload.items() if key not in {"runId", "symbols"}},
        "costAssumptions": {"buyCostBps": 0, "sellCostBps": 0, "slippageBps": 0},
        "gitCommitSha": next(
            (metadata.get("gitCommitSha") for metadata in batch_metadata if metadata.get("gitCommitSha")),
            None,
        ),
        "runtimeSeconds": round(runtime_seconds, 2),
        "summary": summary,
        "oldVsNew": compare_with_previous(previous_report, summary, runtime_seconds) if previous_report else None,
        "warnings": list(dict.fromkeys(warnings)),
    }
    arguments.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("FINAL_REPORT " + json.dumps(report, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
