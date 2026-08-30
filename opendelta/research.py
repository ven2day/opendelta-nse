from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from .factors import FactorEngine
from .market_data import FeatureCache
from .research_v2 import ResearchEngineV2, ResearchExperimentRequestV2


# There is one executable Research contract. Legacy V1 results remain readable
# through the job API, but the invalid one-bar engine is intentionally absent.
ResearchRequest = ResearchExperimentRequestV2
CandleLoader = Callable[[ResearchRequest], pd.DataFrame]


class ResearchService:
    def __init__(
        self,
        candle_loader: CandleLoader,
        factor_engine: FactorEngine | None = None,
        feature_cache: FeatureCache | None = None,
        universe_resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.engine = ResearchEngineV2(
            candle_loader,
            factor_engine or FactorEngine(),
            feature_cache,
            universe_resolver,
        )

    def estimate(self, request: ResearchRequest) -> dict[str, Any]:
        return self.engine.estimate(request)

    def run(
        self,
        payload: dict[str, Any],
        progress: Callable[[float], None],
        cancel: Callable[[], None],
    ) -> dict[str, Any]:
        request = ResearchExperimentRequestV2.model_validate(payload)
        return self.engine.run(request, progress, cancel)
