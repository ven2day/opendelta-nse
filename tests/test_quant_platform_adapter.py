from __future__ import annotations

import numpy as np
import pandas as pd

import backtest_api
from opendelta.research import ResearchExperimentRequest, ResearchService


def test_nse_research_adapter_normalizes_legacy_backtest_candles(monkeypatch) -> None:
    rows = 420
    values = np.arange(rows, dtype=float)
    close = 100 + values * 0.05
    legacy = pd.DataFrame(
        {
            "Open": close - 0.1,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1_000 + values,
            "RSI": 50.0,
        },
        index=pd.date_range("2025-01-01", periods=rows, freq="1D", tz="Asia/Kolkata"),
    )

    class LegacyStore:
        @staticmethod
        def candles(*args, **kwargs) -> pd.DataFrame:
            return legacy.copy()

    monkeypatch.setattr(backtest_api, "get_store", lambda: LegacyStore())
    request = ResearchExperimentRequest(
        mode="EXACT",
        market="NSE",
        provider="DHAN",
        symbol="LUPIN",
        timeframe="1d",
        factorIds=["ema_alignment"],
        minimumTrades=5,
    )

    normalized = backtest_api._platform_candles(request)
    assert {"open", "high", "low", "close", "volume"}.issubset(normalized.columns)
    assert {"Open", "High", "Low", "Close", "Volume"}.isdisjoint(normalized.columns)
    assert "Open" in legacy.columns

    result = ResearchService(lambda _: normalized).run(request.snapshot(), lambda _: None, lambda: None)
    assert result["paperOnly"] is True
    assert result["liveOrdersEnabled"] is False
    assert result["selectedFactorIds"] == ["ema_alignment"]
