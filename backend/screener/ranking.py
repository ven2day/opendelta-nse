"""Ranking of passing symbols. The user picks the key and how many to keep; nothing is hard-coded."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

RANKING_KEYS = {
    "liquidity": ("averageTradedValue", True),
    "volume": ("averageVolume", True),
    "volatility": ("volatilityPct", True),
    "price": ("lastPrice", True),
    "coverage": ("candleCoverage", True),
}


def score(metrics: Mapping[str, Any], rank_by: str) -> float:
    key, _ = RANKING_KEYS[rank_by]
    value = metrics.get(key)
    return float(value) if value is not None else float("-inf")


def rank_symbols(rows: Sequence[Mapping[str, Any]], rank_by: str, maximum_symbols: int | None) -> list[dict[str, Any]]:
    """Return passing rows with ``score``/``rank`` set; rows beyond ``maximum_symbols`` become ranked-out rejections."""
    _, descending = RANKING_KEYS[rank_by]
    passing = [dict(row) for row in rows if row.get("passed")]
    for row in passing:
        row["score"] = score(row["metrics"], rank_by)
    passing.sort(key=lambda row: (row["score"], row["symbol"]), reverse=descending)
    if descending:  # keep deterministic symbol order for ties
        passing.sort(key=lambda row: (-row["score"], row["symbol"]))
    ranked: list[dict[str, Any]] = []
    for position, row in enumerate(passing, start=1):
        if maximum_symbols is not None and position > maximum_symbols:
            row.update(passed=False, rank=None, rejection_reason="RANKED_OUT_BY_MAXIMUM_SYMBOLS")
        else:
            row["rank"] = position
        ranked.append(row)
    return ranked
