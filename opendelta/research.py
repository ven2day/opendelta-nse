from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from .analytics import summarize_returns
from .core import PLATFORM_VERSION, UNSUPPORTED_DATA_REQUIREMENT, stable_id, utc_now_iso
from .factors import FactorEngine, FactorRegistry
from .market_data import FeatureCache
from .research_v2 import ResearchEngineV2, ResearchExperimentRequestV2


class ResearchExperimentRequest(BaseModel):
    researchVersion: Literal["1"] = "1"
    mode: Literal["EXACT", "TOURNAMENT", "FORWARD_SELECTION"] = "EXACT"
    market: Literal["NSE", "CRYPTO"] = "NSE"
    provider: Literal["DHAN", "OKX", "VALR"] = "DHAN"
    symbol: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._&:-]+$")
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"] = "5m"
    durationYears: int = Field(default=1, ge=1, le=2)
    durationDays: int = Field(default=30, ge=7, le=365)
    factorIds: list[str] = Field(default_factory=lambda: ["ema_alignment"], min_length=1, max_length=20)
    factorParameters: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    minimumTrades: int = Field(default=30, ge=5, le=10_000)
    beamWidth: int = Field(default=2, ge=1, le=3)
    costBpsPerRoundTrip: float = Field(default=10, ge=0, le=500)
    validationFraction: float = Field(default=0.2, ge=0.1, le=0.3)
    testFraction: float = Field(default=0.2, ge=0.1, le=0.3)
    dataVersion: str | None = Field(default=None, max_length=120)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_market_provider(self) -> "ResearchExperimentRequest":
        if self.market == "NSE" and self.provider != "DHAN":
            raise ValueError("NSE research requires the Dhan provider boundary")
        if self.market == "CRYPTO" and self.provider == "DHAN":
            raise ValueError("Crypto research requires OKX or VALR")
        if self.validationFraction + self.testFraction >= 0.6:
            raise ValueError("Training must retain more than 40% of the data")
        return self

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class ResearchSplit:
    training: tuple[int, int]
    validation: tuple[int, int]
    test: tuple[int, int]

    def public(self, timestamps: pd.Series) -> dict[str, Any]:
        def item(bounds: tuple[int, int]) -> dict[str, Any]:
            start, end = bounds
            return {
                "startIndex": start,
                "endIndexExclusive": end,
                "rows": end - start,
                "start": timestamps.iloc[start].isoformat() if end > start else None,
                "end": timestamps.iloc[end - 1].isoformat() if end > start else None,
            }
        return {"training": item(self.training), "validation": item(self.validation), "test": item(self.test)}


def chronological_split(rows: int, validation_fraction: float, test_fraction: float) -> ResearchSplit:
    if rows < 30:
        raise ValueError("At least 30 completed candles are required")
    test_start = rows - max(1, int(rows * test_fraction))
    validation_start = test_start - max(1, int(rows * validation_fraction))
    if validation_start < 10:
        raise ValueError("Training split is too small")
    return ResearchSplit((0, validation_start), (validation_start, test_start), (test_start, rows))


def combination_count(factor_ids: list[str], registry: FactorRegistry) -> int:
    families: dict[str, int] = defaultdict(int)
    for factor_id in factor_ids:
        families[registry.get(factor_id).family] += 1
    return max(0, math.prod(count + 1 for count in families.values()) - 1)


def monthly_stability(timestamps: pd.Series, returns: pd.Series) -> dict[str, Any]:
    rows = pd.DataFrame({"timestamp": pd.to_datetime(timestamps, utc=True), "return": returns.fillna(0.0)})
    month = rows["timestamp"].dt.tz_convert(None).dt.to_period("M")
    grouped = rows.groupby(month)["return"].sum()
    positive = int((grouped > 0).sum())
    return {
        "months": len(grouped),
        "positiveMonths": positive,
        "positiveMonthRate": positive / len(grouped) if len(grouped) else 0.0,
        "rows": [{"month": str(month), "netReturn": float(value)} for month, value in grouped.items()],
    }


ResearchRequest = ResearchExperimentRequest | ResearchExperimentRequestV2
CandleLoader = Callable[[ResearchRequest], pd.DataFrame]


class ResearchService:
    def __init__(
        self,
        candle_loader: CandleLoader,
        factor_engine: FactorEngine | None = None,
        feature_cache: FeatureCache | None = None,
        universe_resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.candle_loader = candle_loader
        self.factor_engine = factor_engine or FactorEngine()
        self.registry = self.factor_engine.registry
        self.v2 = ResearchEngineV2(
            candle_loader,
            self.factor_engine,
            feature_cache,
            universe_resolver,
        )

    def estimate(self, request: ResearchRequest) -> dict[str, Any]:
        if isinstance(request, ResearchExperimentRequestV2):
            return self.v2.estimate(request)
        self._validate_factors(request)
        combinations = combination_count(request.factorIds, self.registry)
        evaluated = len(request.factorIds) if request.mode == "TOURNAMENT" else min(combinations, request.beamWidth * len(request.factorIds))
        if request.mode == "EXACT":
            evaluated = 1
        return {
            "mode": request.mode,
            "candidateFactors": len(request.factorIds),
            "possibleCombinations": combinations,
            "plannedEvaluations": evaluated,
            "beamWidth": request.beamWidth,
            "bounded": evaluated <= 100,
        }

    def _validate_factors(self, request: ResearchExperimentRequest) -> None:
        seen = set()
        families = set()
        for factor_id in request.factorIds:
            definition = self.registry.get(factor_id)
            if factor_id in seen:
                raise ValueError("Factor IDs must be unique")
            seen.add(factor_id)
            families.add(definition.family)
            if request.mode == "EXACT" and len(request.factorIds) != 1:
                raise ValueError("Exact mode requires exactly one factor")
            if request.market not in definition.supported_markets:
                raise ValueError(f"{factor_id} is incompatible with {request.market}")
        if request.mode == "TOURNAMENT" and len(families) != 1:
            raise ValueError("Tournament factors must belong to one factor family")

    def run(self, payload: dict[str, Any], progress: Callable[[float], None], cancel: Callable[[], None]) -> dict[str, Any]:
        if str(payload.get("researchVersion", "1")) == "2":
            return self.v2.run(
                ResearchExperimentRequestV2.model_validate(payload), progress, cancel
            )
        request = ResearchExperimentRequest.model_validate(payload)
        self._validate_factors(request)
        estimate = self.estimate(request)
        progress(5)
        frame = self.candle_loader(request).copy()
        cancel()
        if "timestamp" not in frame.columns:
            frame = frame.reset_index()
            if "timestamp" not in frame.columns:
                frame = frame.rename(columns={frame.columns[0]: "timestamp"})
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        frame = frame.loc[frame[["open", "high", "low", "close", "volume"]].notna().all(axis=1)].reset_index(drop=True)
        split = chronological_split(len(frame), request.validationFraction, request.testFraction)
        progress(15)
        factor_values: dict[str, pd.Series] = {}
        unsupported = []
        for index, factor_id in enumerate(request.factorIds):
            cancel()
            output = self.factor_engine.calculate(
                factor_id,
                frame,
                market=request.market,
                timeframe=request.timeframe,
                parameters=request.factorParameters.get(factor_id),
            )
            if output.status == UNSUPPORTED_DATA_REQUIREMENT or output.values is None:
                unsupported.append({"factorId": factor_id, "status": output.status, "reason": output.reason})
            else:
                factor_values[factor_id] = pd.to_numeric(output.values, errors="coerce")
            progress(15 + (index + 1) / len(request.factorIds) * 35)
        if not factor_values:
            raise ValueError("No selected factor has its required data")
        next_open = frame["open"].shift(-1)
        next_close = frame["close"].shift(-1)
        raw_returns = (next_close - next_open) / next_open
        costs = request.costBpsPerRoundTrip / 10_000
        net_returns = raw_returns - costs

        def mask_for(factor_ids: list[str], bounds: tuple[int, int]) -> pd.Series:
            start, end = bounds
            mask = pd.Series(True, index=frame.index)
            for factor_id in factor_ids:
                values = factor_values[factor_id]
                training = values.iloc[split.training[0] : split.training[1]].dropna()
                if training.empty:
                    mask &= False
                    continue
                unique = set(training.unique())
                threshold = 0.0 if unique.issubset({0.0, 1.0}) else float(training.quantile(0.7))
                mask &= values > threshold
            selection = pd.Series(False, index=frame.index)
            selection.iloc[start:end] = True
            return mask & selection & net_returns.notna()

        def score(factor_ids: list[str], bounds: tuple[int, int]) -> dict[str, Any]:
            mask = mask_for(factor_ids, bounds)
            selected = net_returns.loc[mask]
            summary = summarize_returns(
                selected.tolist(), costs=[costs] * len(selected), minimum_trades=request.minimumTrades
            )
            summary["factorIds"] = factor_ids
            summary["monthlyStability"] = monthly_stability(frame.loc[mask, "timestamp"], selected)
            return summary

        baseline = score([], split.validation)
        results = []
        selected_factors: list[str] = []
        if request.mode == "EXACT":
            selected_factors = [next(iter(factor_values))]
            results.append({"phase": "VALIDATION", **score(selected_factors, split.validation)})
        elif request.mode == "TOURNAMENT":
            for factor_id in factor_values:
                cancel()
                result = score([factor_id], split.validation)
                results.append({"phase": "VALIDATION", **result, "addsValue": result["expectancy"] > baseline["expectancy"] and result["status"] == "CONCLUSIVE"})
            results.sort(key=lambda item: (item["status"] == "CONCLUSIVE", item["expectancy"], item["returnToDrawdown"]), reverse=True)
            selected_factors = results[0]["factorIds"] if results else []
        else:
            current_score = baseline
            remaining = set(factor_values)
            used_families: set[str] = set()
            while remaining:
                cancel()
                candidates = []
                for factor_id in remaining:
                    family = self.registry.get(factor_id).family
                    if family in used_families:
                        continue
                    candidate_score = score([*selected_factors, factor_id], split.validation)
                    candidates.append((factor_id, family, candidate_score))
                candidates.sort(
                    key=lambda row: (
                        row[2]["status"] != "CONCLUSIVE",
                        -row[2]["expectancy"],
                        -row[2]["returnToDrawdown"],
                        row[0],
                    )
                )
                best = candidates[: request.beamWidth]
                if not best:
                    break
                factor_id, family, candidate_score = best[0]
                if candidate_score["status"] != "CONCLUSIVE" or candidate_score["expectancy"] <= current_score["expectancy"]:
                    break
                selected_factors.append(factor_id)
                used_families.add(family)
                remaining.remove(factor_id)
                current_score = candidate_score
                results.append({"phase": "FORWARD_SELECTION", **candidate_score})
        progress(85)
        final_test = score(selected_factors, split.test) if selected_factors else score([], split.test)
        experiment = {
            "experimentId": stable_id("experiment", {"request": request.snapshot(), "generatedAt": utc_now_iso()}),
            "platformVersion": PLATFORM_VERSION,
            "generatedAt": utc_now_iso(),
            "configuration": request.snapshot(),
            "configurationId": stable_id("research-config", request.snapshot()),
            "dataVersion": request.dataVersion or "PROVIDER_CACHE_RUNTIME",
            "split": split.public(frame["timestamp"]),
            "estimate": estimate,
            "baselineValidation": baseline,
            "validationResults": results,
            "selectedFactorIds": selected_factors,
            "untouchedTestResult": final_test,
            "unsupported": unsupported,
            "warnings": [
                "Research results are not live-trading approval",
                "One-bar factor evaluation uses next-bar open and completed next-bar close",
            ],
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }
        progress(100)
        return experiment
