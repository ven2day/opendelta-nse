from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prepare_candles
from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig, STRATEGY_VERSION, simulate_recovery_symbol
from backend.compat.recovery_feature_analysis import (
    FEATURE_DEFINITIONS_VERSION,
    FEATURE_SCHEMA_VERSION,
    build_signal_feature_snapshots,
    configuration_hash,
    feature_cache_key,
    write_feature_reports,
)


EXPECTED_STRATEGY_SHA256 = "5ff6bd94625a3551772ca016ff66f709fc17d3411a18b3f93d506bfe3dc3aa03"
BASELINE_OBSERVATIONS = 57_510
BASELINE_TARGETS_HIT = 56_088
BASELINE_OPEN = 1_422

_WORKER: dict[str, Any] = {}


def _load_symbols(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            row["symbol"].strip().upper()
            for row in csv.DictReader(handle)
            if row.get("symbol", "").strip()
        ]


def _read_raw_cache(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col="Timestamp", parse_dates=["Timestamp"])
    frame.index = pd.DatetimeIndex(frame.index)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize(IST)
    else:
        frame.index = frame.index.tz_convert(IST)
    return frame


def _cache_path(directory: Path, symbol: str) -> Path:
    safe = "".join(character for character in symbol if character.isalnum() or character in "-&")
    return directory / f"{safe}-5-1y.csv.gz"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _init_worker(
    candle_cache: str,
    analysis_start: str,
    data_to: str,
    run_id: str,
    config_values: dict[str, Any],
    nifty_path: str | None,
) -> None:
    _WORKER.update(
        {
            "candle_cache": Path(candle_cache),
            "analysis_start": pd.Timestamp(analysis_start).to_pydatetime(),
            "data_to": pd.Timestamp(data_to).to_pydatetime(),
            "run_id": run_id,
            "config": RecoveryConfig(**config_values),
            "nifty": None,
        }
    )
    if nifty_path:
        raw = _read_raw_cache(Path(nifty_path))
        _WORKER["nifty"] = prepare_candles(
            raw,
            "5m",
            _WORKER["analysis_start"],
            _WORKER["data_to"],
            warmup_bars=25,
        )


def _process_symbol(symbol: str) -> dict[str, Any]:
    started = time.perf_counter()
    path = _cache_path(_WORKER["candle_cache"], symbol)
    if not path.is_file():
        return {"symbol": symbol, "error": f"Cached historical candles not found: {path.name}"}
    try:
        candles = prepare_candles(
            _read_raw_cache(path),
            "5m",
            _WORKER["analysis_start"],
            _WORKER["data_to"],
            warmup_bars=25,
        )
        result = simulate_recovery_symbol(
            symbol,
            candles,
            timeframe="5m",
            config=_WORKER["config"],
            run_id=_WORKER["run_id"],
            analysis_start=_WORKER["analysis_start"],
        )
        snapshots = build_signal_feature_snapshots(
            symbol=symbol,
            timeframe="5m",
            candles=candles,
            trades=result["trades"],
            config=_WORKER["config"],
            nifty_candles=_WORKER["nifty"],
        )
        return {
            "symbol": symbol,
            "frame": snapshots,
            "bars": int(result["bars"]),
            "observations": len(snapshots),
            "targetsHit": int(snapshots["outcome_target_hit"].sum()) if len(snapshots) else 0,
            "open": int(snapshots["outcome_open_at_dataset_end"].sum()) if len(snapshots) else 0,
            "runtimeSeconds": round(time.perf_counter() - started, 4),
        }
    except Exception as error:  # each symbol must fail explicitly without aborting the universe
        return {"symbol": symbol, "error": str(error)}


def _config_from_baseline(baseline: dict[str, Any]) -> tuple[RecoveryConfig, dict[str, Any]]:
    parameters = baseline["parameters"]
    values = {
        "rsi_length": int(parameters["rsiLength"]),
        "rsi_arm_low": float(parameters["rsiArmLow"]),
        "rsi_arm_high": float(parameters["rsiArmHigh"]),
        "rsi_recovery": float(parameters["rsiRecovery"]),
        "ema_enabled": bool(parameters["emaEnabled"]),
        "ema_fast": int(parameters["emaFast"]),
        "ema_slow": int(parameters["emaSlow"]),
        "vwap_enabled": bool(parameters["vwapEnabled"]),
        "volume_enabled": bool(parameters["volumeEnabled"]),
        "volume_ema": int(parameters["volumeEma"]),
        "minimum_confirmations": int(parameters["minimumConfirmations"]),
        "target_pct": float(parameters["targetPct"]),
        "setup_expiry_bars": int(parameters["setupExpiryBars"]),
        "execution_model": parameters["executionModel"],
        "buy_cost_bps": float(parameters["buyCostBps"]),
        "sell_cost_bps": float(parameters["sellCostBps"]),
        "slippage_bps": float(parameters["slippageBps"]),
    }
    return RecoveryConfig(**values), values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate causal BUY-time feature reports from the exact RSI Recovery v1.1.0 baseline."
    )
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / "benchmarks/opendelta-rsi-recovery-overlap-baseline.json"
    )
    parser.add_argument("--symbols", type=Path, default=ROOT / "data" / "symbols.csv")
    parser.add_argument("--candle-cache", type=Path, default=Path("/var/lib/vento-nse/backtest"))
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    parser.add_argument("--feature-cache", type=Path, default=Path("/var/lib/vento-nse/backtest/features"))
    parser.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()

    baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
    if baseline.get("strategyVersion") != STRATEGY_VERSION:
        raise RuntimeError(
            f"Baseline strategy {baseline.get('strategyVersion')} does not match source {STRATEGY_VERSION}"
        )
    strategy_hash = _sha256(ROOT / "backend" / "compat" / "recovery_backtest.py")
    if strategy_hash != EXPECTED_STRATEGY_SHA256:
        raise RuntimeError(
            "Recovery strategy source changed; feature analysis refuses to run. "
            f"Expected {EXPECTED_STRATEGY_SHA256}, found {strategy_hash}."
        )

    config, config_values = _config_from_baseline(baseline)
    symbols = _load_symbols(arguments.symbols)
    if arguments.limit:
        symbols = symbols[: arguments.limit]
    run_id = baseline["runId"]
    analysis_start = baseline["dataFrom"]
    data_to = baseline["dataTo"]
    cache_key = feature_cache_key(
        run_id=run_id,
        strategy_version=STRATEGY_VERSION,
        config_hash=configuration_hash({**config_values, "timeframe": "5m"}),
        data_from=analysis_start,
        data_to=data_to,
    )
    partition_directory = arguments.feature_cache / cache_key
    partition_directory.mkdir(parents=True, exist_ok=True)
    nifty_candidate = _cache_path(arguments.candle_cache, "NIFTY50")
    nifty_path = str(nifty_candidate) if nifty_candidate.is_file() else None
    prior_analysis_path = arguments.reports / "recovery_feature_analysis.json"
    prior_metadata: dict[str, Any] = {}
    if prior_analysis_path.is_file():
        try:
            prior_metadata = json.loads(prior_analysis_path.read_text(encoding="utf-8")).get(
                "metadata", {}
            )
        except (OSError, ValueError, TypeError):
            prior_metadata = {}

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    partitions: list[Path] = []
    errors: list[dict[str, str]] = []
    bars_processed = 0
    completed_symbols = 0
    symbols_finished = 0
    benchmark_milestones: dict[str, dict[str, Any]] = {}
    pending_symbols: list[str] = []
    for symbol in symbols:
        partition = partition_directory / f"{symbol}.parquet"
        if partition.is_file() and not arguments.force:
            partitions.append(partition)
            completed_symbols += 1
            symbols_finished += 1
        else:
            pending_symbols.append(symbol)
    reuse_prior_benchmark = bool(prior_metadata.get("exactProductionBaselineReconciled")) and len(
        partitions
    ) >= int(baseline.get("summary", {}).get("symbolsProcessed", 0))

    if pending_symbols:
        with ProcessPoolExecutor(
            max_workers=max(1, min(arguments.workers, 16)),
            initializer=_init_worker,
            initargs=(
                str(arguments.candle_cache),
                analysis_start,
                data_to,
                run_id,
                config_values,
                nifty_path,
            ),
        ) as executor:
            futures = {executor.submit(_process_symbol, symbol): symbol for symbol in pending_symbols}
            for future in as_completed(futures):
                result = future.result()
                symbol = result["symbol"]
                if result.get("error"):
                    errors.append({"symbol": symbol, "message": result["error"]})
                else:
                    partition = partition_directory / f"{symbol}.parquet"
                    _atomic_parquet(result.pop("frame"), partition)
                    partitions.append(partition)
                    bars_processed += int(result["bars"])
                    completed_symbols += 1
                symbols_finished += 1
                for threshold in (1, 100, 300, 750):
                    key = str(threshold)
                    if symbols_finished >= threshold and threshold <= len(symbols) and key not in benchmark_milestones:
                        elapsed = time.perf_counter() - started
                        benchmark_milestones[key] = {
                            "symbolsRequested": threshold,
                            "symbolsFinished": symbols_finished,
                            "symbolsProcessed": completed_symbols,
                            "symbolsFailed": len(errors),
                            "candleRowsProcessed": bars_processed,
                            "runtimeSeconds": round(elapsed, 2),
                            "symbolsPerSecond": round(symbols_finished / elapsed, 4),
                            "workerCount": max(1, min(arguments.workers, 16)),
                            "peakMemoryMb": None,
                            "peakMemoryNote": "Aggregate worker peak memory is not exposed by the production container runtime.",
                        }
                print(
                    json.dumps(
                        {
                            "symbolsCompleted": symbols_finished,
                            "symbolsRequested": len(symbols),
                            "symbolsFailed": len(errors),
                            "symbol": symbol,
                            "elapsedSeconds": round(time.perf_counter() - started, 1),
                        }
                    ),
                    flush=True,
                )

    frames = [pd.read_parquet(path, engine="pyarrow") for path in sorted(partitions)]
    if not frames:
        raise RuntimeError(
            "No feature partitions were generated; failures: "
            + "; ".join(f"{item['symbol']}: {item['message']}" for item in errors)
        )
    snapshots = pd.concat(frames, ignore_index=True)
    snapshots = snapshots.sort_values(["symbol", "signal_timestamp", "trade_id"]).reset_index(drop=True)
    observations = len(snapshots)
    targets_hit = int(snapshots["outcome_target_hit"].sum()) if observations else 0
    open_count = int(snapshots["outcome_open_at_dataset_end"].sum()) if observations else 0
    exact_baseline = arguments.limit == 0 and len(symbols) == baseline["symbolsRequested"]
    if exact_baseline and (
        observations != BASELINE_OBSERVATIONS
        or targets_hit != BASELINE_TARGETS_HIT
        or open_count != BASELINE_OPEN
    ):
        raise RuntimeError(
            "Feature snapshot does not reconcile to production baseline: "
            f"observations={observations}, hits={targets_hit}, open={open_count}"
        )

    runtime = time.perf_counter() - started
    metadata = {
        "runId": run_id,
        "strategyMode": "rsi_recovery",
        "strategyVersion": STRATEGY_VERSION,
        "strategySourceSha256": strategy_hash,
        "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
        "featureDefinitionsVersion": FEATURE_DEFINITIONS_VERSION,
        "featureCacheKey": cache_key,
        "dataFrom": analysis_start,
        "dataTo": data_to,
        "timeframe": "5m",
        "symbolsRequested": len(symbols),
        "symbolsProcessed": completed_symbols,
        "symbolsFailed": len(errors),
        "failedSymbols": errors,
        "strategyParameters": baseline["parameters"],
        "executionModel": config.execution_model,
        "costAssumptions": baseline.get("costAssumptions", {}),
        "niftyContextAvailable": nifty_path is not None,
        "sectorContextAvailable": False,
        "sectorContextStatus": "sector context not implemented: no reliable sector mapping exists",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "startedAt": prior_metadata.get("startedAt", started_at.isoformat())
        if reuse_prior_benchmark
        else started_at.isoformat(),
        "runtimeSeconds": prior_metadata.get("runtimeSeconds", round(runtime, 2))
        if reuse_prior_benchmark
        else round(runtime, 2),
        "symbolsPerSecond": prior_metadata.get("symbolsPerSecond")
        if reuse_prior_benchmark
        else (round(len(symbols) / runtime, 4) if runtime else None),
        "workerCount": max(1, min(arguments.workers, 16)),
        "benchmarkMilestones": prior_metadata.get("benchmarkMilestones", benchmark_milestones)
        if reuse_prior_benchmark
        else benchmark_milestones,
        "candleRowsProcessed": baseline.get("summary", {}).get("candleRowsProcessed"),
        "candleRowsProcessedThisRun": bars_processed,
        "reportRegenerationSeconds": round(runtime, 2) if reuse_prior_benchmark else None,
        "observations": observations,
        "targetsHit": targets_hit,
        "openObservations": open_count,
        "gitCommitSha": _git_sha(),
        "exactProductionBaselineReconciled": exact_baseline,
        "dataAdjustmentWarning": (
            "Dhan equity candles are used as cached by the existing project. The cache does not record an "
            "explicit adjusted-for-corporate-actions flag; splits/bonuses may affect historical features."
        ),
    }
    payload = write_feature_reports(snapshots, arguments.reports, metadata=metadata)
    print(json.dumps({"reports": str(arguments.reports), "summary": payload["summary"], "metadata": metadata}, indent=2))


if __name__ == "__main__":
    main()
