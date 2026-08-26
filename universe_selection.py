from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from recovery_backtest import QUALITY_WEIGHTS, SPEED_SCORES, STRATEGY_VERSION

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_TOP_N = 300
DEFAULT_MINIMUM_PRICE = 500.0
DEFAULT_MAXIMUM_PRICE = 2_000.0
DEFAULT_MINIMUM_BUY_OBSERVATIONS = 50
UNIVERSE_SCHEMA_VERSION = "live-universe-1.0.0"
RANKING_MODES = ("QUALITY", "GOOD_RATE", "TARGET_SPEED", "LOW_MAE", "TARGET_HIT_RATE")
RANKING_LABELS = {
    "QUALITY": "Quality Score",
    "GOOD_RATE": "GOOD Rate",
    "TARGET_SPEED": "Target Speed",
    "LOW_MAE": "Low MAE",
    "TARGET_HIT_RATE": "Target Achievement",
}


@dataclass(frozen=True)
class UniverseSelectionConfig:
    top_n: int = DEFAULT_TOP_N
    minimum_price: float = DEFAULT_MINIMUM_PRICE
    maximum_price: float = DEFAULT_MAXIMUM_PRICE
    ranking_mode: str = "QUALITY"
    minimum_buy_observations: int = DEFAULT_MINIMUM_BUY_OBSERVATIONS
    manual_pins: tuple[str, ...] = ()
    manual_exclusions: tuple[str, ...] = ()
    minimum_good_rate: float | None = None
    maximum_open_rate: float | None = None
    maximum_median_target_minutes: float | None = None
    minimum_target_hit_rate: float | None = None
    minimum_median_mae_pct: float | None = None
    dynamic_price_filter: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.top_n <= 750:
            raise ValueError("Number of symbols must be between 1 and 750")
        if self.minimum_price < 0:
            raise ValueError("Minimum share price cannot be negative")
        if self.maximum_price <= self.minimum_price:
            raise ValueError("Maximum share price must be greater than minimum share price")
        if self.minimum_buy_observations < 1:
            raise ValueError("Minimum historical BUY observations must be at least 1")
        if self.ranking_mode not in RANKING_MODES:
            raise ValueError("Unknown live-universe ranking mode")
        if set(self.manual_pins) & set(self.manual_exclusions):
            raise ValueError("A symbol cannot be both pinned and excluded")

    def canonical(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["manual_pins"] = sorted(set(self.manual_pins))
        payload["manual_exclusions"] = sorted(set(self.manual_exclusions))
        return payload


@dataclass(frozen=True)
class ReferencePrice:
    symbol: str
    reference_price: float
    price_as_of: str
    source_generated_at: str
    source_field: str


@dataclass
class UniverseBuild:
    payload: dict[str, Any]
    selected_frame: pd.DataFrame
    next_tier_frame: pd.DataFrame


def _finite(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _pct(count: int, total: int) -> float:
    return round(count / total * 100.0, 4) if total else 0.0


def _normalize_symbols(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(str(value).strip().upper().removesuffix(".NS") for value in values)
    )
    if any(not value or not value.replace("&", "").replace("-", "").isalnum() for value in normalized):
        raise ValueError("Symbols may only contain letters, numbers, ampersands, and hyphens")
    return normalized


def normalize_config(config: UniverseSelectionConfig) -> UniverseSelectionConfig:
    values = asdict(config)
    values["manual_pins"] = _normalize_symbols(config.manual_pins)
    values["manual_exclusions"] = _normalize_symbols(config.manual_exclusions)
    values["ranking_mode"] = str(config.ranking_mode).upper()
    return UniverseSelectionConfig(**values)


def historical_symbol_metrics(snapshots: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol",
        "outcome_target_hit",
        "outcome_duration_minutes",
        "outcome_speed_bucket",
        "outcome_binary_quality_label",
        "outcome_mae_pct",
        "outcome_open_at_dataset_end",
    }
    missing = sorted(required - set(snapshots.columns))
    if missing:
        raise ValueError(f"Recovery feature snapshot is missing required fields: {', '.join(missing)}")

    rows: list[dict[str, Any]] = []
    score_by_bucket = {
        "FAST_30M": SPEED_SCORES["LE_30_MIN"],
        "FAST_2H": SPEED_SCORES["GT_30_MIN_LE_2_HOURS"],
        "SAME_DAY": SPEED_SCORES["GT_2_HOURS_LE_24_HOURS"],
        "SLOW": SPEED_SCORES["GT_24_HOURS"],
    }
    for symbol, group in snapshots.groupby("symbol", sort=True):
        total = len(group)
        target_hit = group["outcome_target_hit"].fillna(False).astype(bool)
        completed = group.loc[target_hit]
        open_count = int(group["outcome_open_at_dataset_end"].fillna(False).astype(bool).sum())
        good_count = int(group["outcome_binary_quality_label"].eq("GOOD").sum())
        bad_count = int(group["outcome_binary_quality_label"].eq("BAD").sum())
        neutral_count = int(group["outcome_binary_quality_label"].eq("NEUTRAL").sum())
        speed_values = completed["outcome_speed_bucket"].map(score_by_bucket).dropna()
        speed_score = float(speed_values.mean()) if not speed_values.empty else 0.0
        completed_mae = pd.to_numeric(completed["outcome_mae_pct"], errors="coerce").dropna()
        median_mae = float(completed_mae.median()) if not completed_mae.empty else -10.0
        mae_score = min(max(100.0 + median_mae * 10.0, 0.0), 100.0)
        target_hit_rate = _pct(int(target_hit.sum()), total)
        open_rate = _pct(open_count, total)
        quality_score = (
            QUALITY_WEIGHTS["targetHitRate"] * target_hit_rate
            + QUALITY_WEIGHTS["targetSpeed"] * speed_score
            + QUALITY_WEIGHTS["maeQuality"] * mae_score
            + QUALITY_WEIGHTS["openPosition"] * (100.0 - open_rate)
        )
        rows.append(
            {
                "symbol": str(symbol),
                "buy_observations": total,
                "targets_hit": int(target_hit.sum()),
                "good_count": good_count,
                "bad_count": bad_count,
                "neutral_count": neutral_count,
                "open_count": open_count,
                "good_rate": _pct(good_count, total),
                "bad_rate": _pct(bad_count, total),
                "neutral_rate": _pct(neutral_count, total),
                "historical_target_hit_rate": target_hit_rate,
                "median_target_minutes": _finite(
                    pd.to_numeric(completed["outcome_duration_minutes"], errors="coerce").median()
                ),
                "median_mae_pct": _finite(median_mae, 6),
                "worst_mae_pct": _finite(completed_mae.min(), 6),
                "open_rate": open_rate,
                "le30m_pct": _pct(int(group["outcome_speed_bucket"].eq("FAST_30M").sum()), total),
                "le2h_pct": _pct(good_count, total),
                "le24h_pct": _pct(good_count + neutral_count, total),
                "hit_rate_score": target_hit_rate,
                "speed_score": _finite(speed_score),
                "mae_score": _finite(mae_score),
                "open_penalty": open_rate,
                # Existing production result precision is two decimals.
                "quality_score": round(quality_score, 2),
            }
        )
    return pd.DataFrame(rows).sort_values("symbol", kind="stable").reset_index(drop=True)


def signal_count_distribution(metrics: pd.DataFrame) -> dict[str, float | int]:
    counts = pd.to_numeric(metrics["buy_observations"], errors="coerce").dropna()
    if counts.empty:
        return {key: 0 for key in ("count", "min", "p10", "p25", "median", "p75", "p90", "max")}
    return {
        "count": int(counts.count()),
        "min": int(counts.min()),
        "p10": _finite(counts.quantile(0.10), 2),
        "p25": _finite(counts.quantile(0.25), 2),
        "median": _finite(counts.median(), 2),
        "p75": _finite(counts.quantile(0.75), 2),
        "p90": _finite(counts.quantile(0.90), 2),
        "max": int(counts.max()),
    }


def load_reference_prices(path: Path, *, now: datetime | None = None) -> dict[str, ReferencePrice]:
    if not path.is_file():
        raise FileNotFoundError(f"Completed-candle market data is unavailable: {path}")
    frame = pd.read_csv(path)
    required = {"symbol", "trading_date", "previous_date", "previous_close", "entry_price"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Market-data CSV is missing required fields: {', '.join(missing)}")

    now_ist = (now or datetime.now(IST)).astimezone(IST)
    source_generated = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
    prices: dict[str, ReferencePrice] = {}
    for row in frame.itertuples(index=False):
        symbol = str(row.symbol).strip().upper().removesuffix(".NS")
        trading_day = pd.to_datetime(row.trading_date, errors="coerce")
        previous_day = pd.to_datetime(row.previous_date, errors="coerce")
        current_price = _finite(row.entry_price, 8)
        previous_price = _finite(row.previous_close, 8)
        use_previous = False
        if not pd.isna(trading_day):
            session_day = trading_day.date()
            session_close = datetime.combine(session_day, time(15, 30), tzinfo=IST)
            use_previous = session_day >= now_ist.date() and (
                now_ist < session_close or source_generated < session_close
            )
        if use_previous and previous_price is not None and not pd.isna(previous_day):
            reference_price = previous_price
            price_day = previous_day.date()
            source_field = "previous_close"
        elif current_price is not None and not pd.isna(trading_day):
            reference_price = current_price
            price_day = trading_day.date()
            source_field = "entry_price_completed_session"
        elif previous_price is not None and not pd.isna(previous_day):
            reference_price = previous_price
            price_day = previous_day.date()
            source_field = "previous_close_fallback"
        else:
            continue
        if reference_price < 0:
            continue
        price_as_of = datetime.combine(price_day, time(15, 30), tzinfo=IST).isoformat()
        prices[symbol] = ReferencePrice(
            symbol=symbol,
            reference_price=reference_price,
            price_as_of=price_as_of,
            source_generated_at=source_generated.isoformat(),
            source_field=source_field,
        )
    return prices


def _ranking_sort(frame: pd.DataFrame, ranking_mode: str) -> pd.DataFrame:
    work = frame.copy()
    work["mae_distance_zero"] = work["median_mae_pct"].abs()
    if ranking_mode == "QUALITY":
        columns = ["quality_score", "good_rate", "median_target_minutes", "open_rate", "mae_distance_zero", "symbol"]
        ascending = [False, False, True, True, True, True]
    elif ranking_mode == "GOOD_RATE":
        columns = ["good_rate", "quality_score", "median_target_minutes", "open_rate", "mae_distance_zero", "symbol"]
        ascending = [False, False, True, True, True, True]
    elif ranking_mode == "TARGET_SPEED":
        columns = ["median_target_minutes", "quality_score", "good_rate", "open_rate", "mae_distance_zero", "symbol"]
        ascending = [True, False, False, True, True, True]
    elif ranking_mode == "LOW_MAE":
        columns = ["mae_distance_zero", "quality_score", "good_rate", "median_target_minutes", "open_rate", "symbol"]
        ascending = [True, False, False, True, True, True]
    else:
        columns = ["historical_target_hit_rate", "quality_score", "good_rate", "median_target_minutes", "open_rate", "mae_distance_zero", "symbol"]
        ascending = [False, False, False, True, True, True, True]
    return work.sort_values(columns, ascending=ascending, kind="stable").drop(columns="mae_distance_zero")


def _guards_pass(row: pd.Series, config: UniverseSelectionConfig) -> tuple[bool, str | None]:
    checks = (
        (config.minimum_good_rate, row["good_rate"] >= (config.minimum_good_rate or 0), "GOOD_RATE_BELOW_MIN"),
        (config.maximum_open_rate, row["open_rate"] <= (config.maximum_open_rate or 0), "OPEN_RATE_ABOVE_MAX"),
        (
            config.maximum_median_target_minutes,
            row["median_target_minutes"] <= (config.maximum_median_target_minutes or 0),
            "MEDIAN_TARGET_TIME_ABOVE_MAX",
        ),
        (
            config.minimum_target_hit_rate,
            row["historical_target_hit_rate"] >= (config.minimum_target_hit_rate or 0),
            "TARGET_HIT_RATE_BELOW_MIN",
        ),
        (
            config.minimum_median_mae_pct,
            row["median_mae_pct"] >= (config.minimum_median_mae_pct or 0),
            "MEDIAN_MAE_BELOW_MIN",
        ),
    )
    for configured, passed, reason in checks:
        if configured is not None and not bool(passed):
            return False, reason
    return True, None


def _row_payload(row: pd.Series, *, selection_reason: str, is_pinned: bool) -> dict[str, Any]:
    return {
        "rank": int(row["rank"]) if pd.notna(row.get("rank")) else None,
        "qualityRank": int(row["quality_rank"]) if pd.notna(row.get("quality_rank")) else None,
        "historicalQualityRank": int(row["historical_quality_rank"]),
        "symbol": str(row["symbol"]),
        "referencePrice": _finite(row["reference_price"], 4),
        "priceAsOf": str(row["price_as_of"]),
        "priceSource": str(row["price_source"]),
        "qualityScore": _finite(row["quality_score"], 2),
        "goodRate": _finite(row["good_rate"]),
        "badRate": _finite(row["bad_rate"]),
        "neutralRate": _finite(row["neutral_rate"]),
        "historicalTargetHitRate": _finite(row["historical_target_hit_rate"]),
        "medianTargetMinutes": _finite(row["median_target_minutes"]),
        "medianMaePct": _finite(row["median_mae_pct"], 6),
        "worstMaePct": _finite(row["worst_mae_pct"], 6),
        "openRate": _finite(row["open_rate"]),
        "buyObservations": int(row["buy_observations"]),
        "le30mPct": _finite(row["le30m_pct"]),
        "le2hPct": _finite(row["le2h_pct"]),
        "le24hPct": _finite(row["le24h_pct"]),
        "selectionReason": selection_reason,
        "isPinned": is_pinned,
    }


def _aggregate_observations(snapshots: pd.DataFrame, symbols: set[str]) -> dict[str, Any]:
    subset = snapshots.loc[snapshots["symbol"].isin(symbols)] if symbols else snapshots.iloc[0:0]
    total = len(subset)
    completed = subset.loc[subset["outcome_target_hit"].fillna(False).astype(bool)]
    return {
        "symbols": len(symbols),
        "buyObservations": total,
        "goodRate": _pct(int(subset["outcome_binary_quality_label"].eq("GOOD").sum()), total),
        "badRate": _pct(int(subset["outcome_binary_quality_label"].eq("BAD").sum()), total),
        "neutralRate": _pct(int(subset["outcome_binary_quality_label"].eq("NEUTRAL").sum()), total),
        "openRate": _pct(int(subset["outcome_open_at_dataset_end"].fillna(False).astype(bool).sum()), total),
        "targetHitRate": _pct(int(subset["outcome_target_hit"].fillna(False).astype(bool).sum()), total),
        "medianTargetMinutes": _finite(pd.to_numeric(completed["outcome_duration_minutes"], errors="coerce").median()),
        "medianMaePct": _finite(pd.to_numeric(completed["outcome_mae_pct"], errors="coerce").median(), 6),
    }


def _distribution(values: pd.Series) -> dict[str, float | int | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {key: None for key in ("min", "p10", "p25", "median", "p75", "p90", "max")}
    return {
        "min": _finite(clean.min()),
        "p10": _finite(clean.quantile(0.10)),
        "p25": _finite(clean.quantile(0.25)),
        "median": _finite(clean.median()),
        "p75": _finite(clean.quantile(0.75)),
        "p90": _finite(clean.quantile(0.90)),
        "max": _finite(clean.max()),
    }


def _configuration_hash(
    config: UniverseSelectionConfig,
    metadata: dict[str, Any],
    prices: dict[str, ReferencePrice],
) -> str:
    source = {
        "schema": UNIVERSE_SCHEMA_VERSION,
        "config": config.canonical(),
        "strategyVersion": STRATEGY_VERSION,
        "historicalRunId": metadata.get("runId"),
        "strategySourceSha256": metadata.get("strategySourceSha256"),
        "prices": [
            [symbol, price.reference_price, price.price_as_of]
            for symbol, price in sorted(prices.items())
        ],
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_universe(
    snapshots: pd.DataFrame,
    metrics: pd.DataFrame,
    prices: dict[str, ReferencePrice],
    config: UniverseSelectionConfig,
    metadata: dict[str, Any],
    *,
    total_nse_symbols: int = 750,
    failed_symbols: list[dict[str, Any]] | None = None,
    active: dict[str, Any] | None = None,
) -> UniverseBuild:
    config = normalize_config(config)
    failed_symbols = failed_symbols or []
    failed_by_symbol = {str(item.get("symbol", "")).upper(): str(item.get("message", "Data quality failed")) for item in failed_symbols}
    working = metrics.copy()
    historical_sorted = _ranking_sort(working, "QUALITY").reset_index(drop=True)
    historical_rank = {symbol: index + 1 for index, symbol in enumerate(historical_sorted["symbol"])}
    working["historical_quality_rank"] = working["symbol"].map(historical_rank)
    working["reference_price"] = working["symbol"].map(lambda symbol: prices.get(symbol).reference_price if symbol in prices else np.nan)
    working["price_as_of"] = working["symbol"].map(lambda symbol: prices.get(symbol).price_as_of if symbol in prices else "")
    working["price_source"] = working["symbol"].map(lambda symbol: prices.get(symbol).source_field if symbol in prices else "")

    exclusions: list[dict[str, Any]] = []
    eligibility: list[bool] = []
    for _, row in working.iterrows():
        reason: str | None = None
        price = row["reference_price"]
        if pd.isna(price):
            reason = "REFERENCE_PRICE_UNAVAILABLE"
        elif price < config.minimum_price:
            reason = "PRICE_BELOW_MIN"
        elif price > config.maximum_price:
            reason = "PRICE_ABOVE_MAX"
        elif int(row["buy_observations"]) < config.minimum_buy_observations:
            reason = "HISTORICAL_SAMPLE_BELOW_MIN"
        else:
            passed, guard_reason = _guards_pass(row, config)
            if not passed:
                reason = guard_reason
        eligibility.append(reason is None)
        if reason:
            exclusions.append(
                {
                    "symbol": row["symbol"],
                    "reason": reason,
                    "referencePrice": _finite(price, 4),
                    "priceAsOf": row["price_as_of"] or None,
                    "qualityScore": _finite(row["quality_score"], 2),
                    "buyObservations": int(row["buy_observations"]),
                }
            )
    working["eligible"] = eligibility
    eligible = _ranking_sort(working.loc[working["eligible"]], config.ranking_mode).reset_index(drop=True)
    eligible["quality_rank"] = np.arange(1, len(eligible) + 1)

    eligible_symbols = set(eligible["symbol"])
    invalid_pins = [symbol for symbol in config.manual_pins if symbol not in eligible_symbols]
    if invalid_pins:
        raise ValueError(
            "Pinned symbols must pass data quality, price, sample-size, and enabled quality guards: "
            + ", ".join(invalid_pins)
        )

    calculated = eligible.head(config.top_n).copy()
    calculated_symbols = set(calculated["symbol"])
    pinned_symbols = set(config.manual_pins) - calculated_symbols
    excluded_symbols = set(config.manual_exclusions)
    final_symbols = (calculated_symbols | pinned_symbols) - excluded_symbols
    selected = eligible.loc[eligible["symbol"].isin(final_symbols)].copy()
    selected = _ranking_sort(selected, config.ranking_mode).reset_index(drop=True)
    selected["rank"] = np.arange(1, len(selected) + 1)

    selected_payload = [
        _row_payload(
            row,
            selection_reason="MANUAL_PIN" if row["symbol"] in pinned_symbols else "CALCULATED_TOP_N",
            is_pinned=row["symbol"] in pinned_symbols,
        )
        for _, row in selected.iterrows()
    ]

    rank_cutoff = eligible.loc[~eligible["symbol"].isin(calculated_symbols | set(config.manual_pins))]
    for _, row in rank_cutoff.iterrows():
        exclusions.append(
            {
                "symbol": row["symbol"],
                "reason": "BELOW_RANK_CUTOFF",
                "qualityRank": int(row["quality_rank"]),
                "referencePrice": _finite(row["reference_price"], 4),
                "priceAsOf": row["price_as_of"],
                "qualityScore": _finite(row["quality_score"], 2),
                "buyObservations": int(row["buy_observations"]),
            }
        )
    for symbol in sorted(excluded_symbols & eligible_symbols):
        row = eligible.loc[eligible["symbol"].eq(symbol)].iloc[0]
        exclusions.append(
            {
                "symbol": symbol,
                "reason": "MANUAL_EXCLUSION",
                "qualityRank": int(row["quality_rank"]),
                "referencePrice": _finite(row["reference_price"], 4),
                "priceAsOf": row["price_as_of"],
                "qualityScore": _finite(row["quality_score"], 2),
                "buyObservations": int(row["buy_observations"]),
            }
        )

    metric_symbols = set(metrics["symbol"])
    data_quality_excluded = []
    for symbol, reason in sorted(failed_by_symbol.items()):
        data_quality_excluded.append({"symbol": symbol, "reason": "DATA_QUALITY_FAILED", "detail": reason})
    missing_historical_count = max(total_nse_symbols - len(metric_symbols) - len(data_quality_excluded), 0)
    if missing_historical_count:
        data_quality_excluded.append(
            {"symbol": None, "reason": "HISTORICAL_METRICS_UNAVAILABLE", "count": missing_historical_count}
        )

    price_eligible = working["reference_price"].between(config.minimum_price, config.maximum_price, inclusive="both")
    price_below = int((working["reference_price"] < config.minimum_price).sum())
    price_above = int((working["reference_price"] > config.maximum_price).sum())
    price_missing = int(working["reference_price"].isna().sum())
    sample_eligible = int((price_eligible & (working["buy_observations"] >= config.minimum_buy_observations)).sum())
    next_tier = eligible.iloc[config.top_n : config.top_n + 100].copy()
    next_tier["rank"] = next_tier["quality_rank"]

    all_price_as_of = sorted({price.price_as_of for price in prices.values()})
    price_as_of = all_price_as_of[-1] if all_price_as_of else None
    source_generated = max((price.source_generated_at for price in prices.values()), default=None)
    selected_symbols = set(selected["symbol"])
    next_symbols = set(next_tier["symbol"])
    active_symbols = set(active.get("selectedSymbols", [])) if active else set()
    added = sorted(selected_symbols - active_symbols)
    removed = sorted(active_symbols - selected_symbols)
    unchanged = sorted(selected_symbols & active_symbols)
    active_rows = {row["symbol"]: row for row in active.get("selected", [])} if active else {}
    selected_rows = {row["symbol"]: row for row in selected_payload}
    differences = {
        "added": [
            {"symbol": symbol, "reason": selected_rows[symbol]["selectionReason"]}
            for symbol in added
        ],
        "removed": [
            {
                "symbol": symbol,
                "reason": next(
                    (item["reason"] for item in exclusions if item.get("symbol") == symbol),
                    "NO_LONGER_ELIGIBLE",
                ),
                "previousReferencePrice": active_rows.get(symbol, {}).get("referencePrice"),
            }
            for symbol in removed
        ],
        "unchanged": unchanged,
    }

    configuration_hash = _configuration_hash(config, metadata, prices)
    payload = {
        "schemaVersion": UNIVERSE_SCHEMA_VERSION,
        "status": "PREVIEW",
        "frozen": False,
        "configurationHash": configuration_hash,
        "configuration": {
            "topN": config.top_n,
            "minimumPrice": config.minimum_price,
            "maximumPrice": config.maximum_price,
            "rankingMode": config.ranking_mode,
            "minimumBuyObservations": config.minimum_buy_observations,
            "manualPins": list(config.manual_pins),
            "manualExclusions": list(config.manual_exclusions),
            "minimumGoodRate": config.minimum_good_rate,
            "maximumOpenRate": config.maximum_open_rate,
            "maximumMedianTargetMinutes": config.maximum_median_target_minutes,
            "minimumTargetHitRate": config.minimum_target_hit_rate,
            "minimumMedianMaePct": config.minimum_median_mae_pct,
            "dynamicPriceFilter": config.dynamic_price_filter,
        },
        "ranking": {
            "mode": config.ranking_mode,
            "label": RANKING_LABELS[config.ranking_mode],
            "qualityFormula": "40% target-hit rate + 30% target speed + 20% MAE quality + 10% non-open rate",
            "qualitySortOrder": [
                "quality_score DESC",
                "GOOD rate DESC",
                "median target time ASC",
                "OPEN rate ASC",
                "median completed MAE closest to zero",
                "symbol ASC",
            ],
        },
        "source": {
            "strategyVersion": STRATEGY_VERSION,
            "historicalRunId": metadata.get("runId"),
            "backtestFrom": metadata.get("dataFrom"),
            "backtestTo": metadata.get("dataTo"),
            "strategySourceSha256": metadata.get("strategySourceSha256"),
            "priceSource": "existing Dhan market-data cache; latest completed NSE session close",
            "priceAsOf": price_as_of,
            "priceSourceGeneratedAt": source_generated,
            "priceTimestampCount": len(all_price_as_of),
        },
        "statistics": {
            "totalNseSymbols": total_nse_symbols,
            "dataQualityEligible": len(metrics),
            "dataQualityExcluded": len(data_quality_excluded),
            "priceEligible": int(price_eligible.sum()),
            "priceBelowMinimum": price_below,
            "priceAboveMaximum": price_above,
            "referencePriceUnavailable": price_missing,
            "sampleEligible": sample_eligible,
            "rankingEligible": len(eligible),
            "requestedTopN": config.top_n,
            "calculatedSelected": len(calculated),
            "pinned": len(pinned_symbols),
            "manuallyExcluded": len(excluded_symbols & (calculated_symbols | pinned_symbols)),
            "selected": len(selected),
        },
        "selectedSymbols": selected["symbol"].tolist(),
        "selected": selected_payload,
        "excluded": sorted(exclusions, key=lambda item: (str(item.get("reason")), str(item.get("symbol")))),
        "dataQualityExcluded": data_quality_excluded,
        "differences": differences,
        "aggregates": {
            "selected": _aggregate_observations(snapshots, selected_symbols),
            "fullValidatedUniverse": _aggregate_observations(snapshots, set(metrics["symbol"])),
            "nextEligible100": _aggregate_observations(snapshots, next_symbols),
        },
        "distributions": {
            "historicalBuyObservations": signal_count_distribution(metrics),
            "selectedReferencePrice": _distribution(selected["reference_price"]),
            "selectedBuyObservations": _distribution(selected["buy_observations"]),
        },
        "nextTier": [
            _row_payload(row, selection_reason="BELOW_RANK_CUTOFF", is_pinned=False)
            for _, row in next_tier.iterrows()
        ],
        "generatedAt": datetime.now(IST).isoformat(),
    }
    return UniverseBuild(payload=payload, selected_frame=selected, next_tier_frame=next_tier)


class UniverseRepository:
    def __init__(self, root: Path):
        self.root = root
        self.versions = root / "versions"
        self.active_path = root / "active.json"
        self.config_path = root / "config.json"
        self.export_path = root / "live_universe.csv"
        self._lock = threading.RLock()

    def _ensure(self) -> None:
        self.versions.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
                temporary = Path(handle.name)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_csv(path: Path, record: dict[str, Any]) -> None:
        fields = [
            "universe_version", "rank", "symbol", "reference_price", "price_as_of",
            "quality_score", "good_rate", "bad_rate", "target_hit_rate",
            "median_target_minutes", "median_mae_pct", "open_rate", "buy_observations",
            "selection_reason", "is_pinned",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in record["selected"]:
                    writer.writerow(
                        {
                            "universe_version": record["universeVersion"],
                            "rank": row["rank"],
                            "symbol": row["symbol"],
                            "reference_price": row["referencePrice"],
                            "price_as_of": row["priceAsOf"],
                            "quality_score": row["qualityScore"],
                            "good_rate": row["goodRate"],
                            "bad_rate": row["badRate"],
                            "target_hit_rate": row["historicalTargetHitRate"],
                            "median_target_minutes": row["medianTargetMinutes"],
                            "median_mae_pct": row["medianMaePct"],
                            "open_rate": row["openRate"],
                            "buy_observations": row["buyObservations"],
                            "selection_reason": row["selectionReason"],
                            "is_pinned": row["isPinned"],
                        }
                    )
                temporary = Path(handle.name)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)

    def load_active(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.active_path.is_file():
                return None
            return json.loads(self.active_path.read_text(encoding="utf-8"))

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.versions.is_dir():
                return []
            items = []
            for path in sorted(self.versions.glob("LIVE-*.json"), reverse=True):
                record = json.loads(path.read_text(encoding="utf-8"))
                items.append(
                    {
                        "universeVersion": record["universeVersion"],
                        "universeId": record["universeId"],
                        "createdAt": record["createdAt"],
                        "updatedAt": record["updatedAt"],
                        "frozen": record["frozen"],
                        "selected": len(record.get("selectedSymbols", [])),
                        "configuration": record["configuration"],
                        "configurationHash": record["configurationHash"],
                        "priceAsOf": record.get("source", {}).get("priceAsOf"),
                    }
                )
            return items

    def _next_version(self, today: date) -> str:
        prefix = f"LIVE-{today:%Y%m%d}-"
        existing = [path.stem for path in self.versions.glob(f"{prefix}*.json")]
        sequence = max((int(value.rsplit("-", 1)[-1]) for value in existing), default=0) + 1
        return f"{prefix}{sequence:03d}"

    def save_and_activate(self, preview: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            now = datetime.now(IST)
            version = self._next_version(now.date())
            record = {
                **preview,
                "universeId": str(uuid.uuid4()),
                "universeVersion": version,
                "status": "ACTIVE",
                "frozen": True,
                "createdAt": now.isoformat(),
                "updatedAt": now.isoformat(),
            }
            version_path = self.versions / f"{version}.json"
            version_csv = self.versions / f"{version}.csv"
            self._write_json(version_path, record)
            self._write_csv(version_csv, record)
            self._write_json(self.active_path, record)
            self._write_json(self.config_path, record["configuration"])
            self._write_csv(self.export_path, record)
            return record

    def export_for(self, version: str | None = None) -> Path:
        if version and version != "active":
            path = self.versions / f"{version}.csv"
        else:
            path = self.export_path
        if not path.is_file():
            raise FileNotFoundError("The requested live universe export is not available")
        return path


class UniverseService:
    def __init__(self, repository: UniverseRepository, market_data_path: Path):
        self.repository = repository
        self.market_data_path = market_data_path
        self._metrics_lock = threading.Lock()
        self._metrics_cache: tuple[int, int, pd.DataFrame] | None = None

    def metrics(self, snapshots: pd.DataFrame) -> pd.DataFrame:
        key = (id(snapshots), len(snapshots))
        with self._metrics_lock:
            if self._metrics_cache is None or self._metrics_cache[:2] != key:
                self._metrics_cache = (*key, historical_symbol_metrics(snapshots))
            return self._metrics_cache[2]

    def preview(
        self,
        snapshots: pd.DataFrame,
        metadata: dict[str, Any],
        config: UniverseSelectionConfig,
        *,
        now: datetime | None = None,
    ) -> UniverseBuild:
        prices = load_reference_prices(self.market_data_path, now=now)
        return build_universe(
            snapshots,
            self.metrics(snapshots),
            prices,
            config,
            metadata,
            total_nse_symbols=int(metadata.get("symbolsRequested", 750)),
            failed_symbols=list(metadata.get("failedSymbols", [])),
            active=self.repository.load_active(),
        )

    def get_active_live_universe(self) -> tuple[list[str], dict[str, Any] | None]:
        active = self.repository.load_active()
        return (list(active.get("selectedSymbols", [])), active) if active else ([], None)
