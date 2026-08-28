from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from pandas.testing import assert_frame_equal

import market_aligned_performance as performance
from backtest_api import _market_result_cache_root
from main import IST
from market_aligned_rsi_scalper import (
    MarketAlignedConfig,
    apply_market_alignment_chronologically,
    evaluate_market_alignment,
)
from recovery_backtest import RecoveryConfig, simulate_recovery_symbol
from tests.test_market_aligned_rsi_scalper import candidate, trend_frame


def configs() -> tuple[RecoveryConfig, MarketAlignedConfig]:
    market = MarketAlignedConfig(
        minimum_breadth_symbols=2,
        minimum_sector_members=2,
        minimum_average_traded_value=0,
        sector_by_symbol={"TEST": "DEMO", "PEER": "DEMO"},
    ).validate()
    recovery = RecoveryConfig(
        rsi_length=market.rsi_length,
        ema_fast=market.ema_fast,
        ema_slow=market.ema_slow,
        volume_ema=17,
    )
    return recovery, market


def precomputed_evaluation(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    recovery, market = configs()
    stock = trend_frame(step=0.20)
    peer = trend_frame(step=0.08)
    nifty = trend_frame(step=0.05)
    trade = candidate(stock)
    timestamps = pd.DatetimeIndex([stock.index[-1]])
    stock_features = performance.calculate_market_feature_frame(stock, recovery, market)
    peer_features = performance.calculate_market_feature_frame(peer, recovery, market)
    stock_path = tmp_path / "stock.parquet"
    peer_path = tmp_path / "peer.parquet"
    stock_features.to_parquet(stock_path)
    peer_features.to_parquet(peer_path)
    breadth, sectors, _ = performance.build_support_context(
        candidate_timestamps=timestamps,
        feature_paths_by_symbol={"TEST": str(stock_path), "PEER": str(peer_path)},
        breadth_symbols=["TEST", "PEER"],
        sector_symbols=["TEST", "PEER"],
        sector_by_symbol=market.sector_by_symbol,
        config=market,
    )
    nifty_context = performance.build_nifty_context(
        nifty,
        timestamps,
        market.stale_data_seconds,
        market.relative_strength_lookback_bars,
    )
    key = timestamps[0].isoformat()
    optimized = performance.evaluate_precomputed_market_alignment(
        trade,
        stock=performance._stock_context_at(stock_features, timestamps[0].to_pydatetime(), market),
        nifty=nifty_context[key],
        breadth=breadth[key],
        sector=sectors[performance.candidate_sector_key("DEMO", timestamps[0])],
        config=market,
    )
    canonical = evaluate_market_alignment(
        trade,
        symbol_frame=stock,
        nifty_frame=nifty,
        universe_frames={"TEST": stock, "PEER": peer},
        config=market,
    )
    return optimized, canonical


def test_vectorized_market_features_preserve_indicator_and_candidate_results() -> None:
    recovery, market = configs()
    candles = trend_frame(step=0.04, periods=80)
    features = performance.calculate_market_feature_frame(candles, recovery, market)
    direct = simulate_recovery_symbol(
        "TEST", candles, timeframe="5m", config=recovery, run_id="same"
    )
    cached = simulate_recovery_symbol(
        "TEST", features, timeframe="5m", config=recovery, run_id="same",
        indicator_frame=features,
    )
    assert_frame_equal(
        features[["RecoveryRSI", "EMAFast", "EMASlow", "SessionVWAP", "VolumeEMA"]],
        performance.calculate_market_feature_frame(candles, recovery, market)[
            ["RecoveryRSI", "EMAFast", "EMASlow", "SessionVWAP", "VolumeEMA"]
        ],
        check_exact=True,
    )
    assert cached == direct


def test_precomputed_candidate_evaluation_is_exact(tmp_path: Path) -> None:
    optimized, canonical = precomputed_evaluation(tmp_path)
    assert optimized == canonical


def test_precomputed_apply_path_is_exact(tmp_path: Path) -> None:
    optimized, canonical = precomputed_evaluation(tmp_path)
    _, market = configs()
    stock = trend_frame(step=0.20)
    peer = trend_frame(step=0.08)
    nifty = trend_frame(step=0.05)
    trade = candidate(stock)
    base = [{"symbol": "TEST", "trades": [trade], "events": []}]
    old = apply_market_alignment_chronologically(
        base,
        frames_by_symbol={"TEST": stock, "PEER": peer},
        nifty_frame=nifty,
        config=market,
    )
    new = apply_market_alignment_chronologically(
        base,
        frames_by_symbol={},
        nifty_frame=pd.DataFrame(),
        config=market,
        precomputed_evaluations={str(trade["tradeId"]): optimized},
    )
    assert optimized == canonical
    assert new == old


def test_feature_cache_key_invalidates_indicators_but_not_exit_only_settings(tmp_path: Path) -> None:
    cache = performance.MarketFeatureCache(tmp_path)
    recovery, market = configs()
    source = tmp_path / "TEST-5-1y.csv.gz"
    source.write_bytes(b"immutable-candle-version")
    common = dict(
        symbol="TEST", timeframe="5m", duration_years=1,
        analysis_start=pd.Timestamp("2025-01-01", tz=IST).to_pydatetime(),
        now=pd.Timestamp("2026-01-01", tz=IST).to_pydatetime(), warmup_bars=30,
        source_fingerprint=performance.file_stat_fingerprint(source), market_config=market,
    )
    first = cache._key(recovery_config=recovery, **common)
    exit_only = RecoveryConfig(**{**recovery.__dict__, "target_pct": recovery.target_pct + 1})
    changed_rsi = RecoveryConfig(**{**recovery.__dict__, "rsi_length": recovery.rsi_length + 1})
    assert cache._key(recovery_config=exit_only, **common) == first
    assert cache._key(recovery_config=changed_rsi, **common) != first


def test_candidate_timestamp_index_is_sorted_unique_and_causal() -> None:
    later = pd.Timestamp("2025-01-02 10:00", tz=IST).isoformat()
    earlier = pd.Timestamp("2025-01-02 09:55", tz=IST).isoformat()
    result = performance.candidate_timestamp_index([
        {"trades": [{"entryTimestamp": later}, {"entryTimestamp": earlier}, {"entryTimestamp": later}]}
    ])
    assert list(result) == [pd.Timestamp(earlier), pd.Timestamp(later)]


def test_completed_result_cache_is_atomic_and_marks_reuse(tmp_path: Path) -> None:
    cache = performance.BacktestResultCache(tmp_path)
    fingerprint = "a" * 64
    response = {
        "metadata": {"fingerprint": fingerprint, "completedAt": "2025-01-02T10:00:00+05:30"},
        "results": [{"symbol": "TEST"}],
    }
    assert cache.load(fingerprint) is None
    assert cache.save(fingerprint, response) > 0
    loaded = cache.load(fingerprint)
    assert loaded is not None
    assert loaded["results"] == response["results"]
    assert loaded["metadata"]["cachedResult"] is True
    assert loaded["metadata"]["originalRunTimestamp"] == response["metadata"]["completedAt"]


def test_result_cache_defaults_inside_writable_backtest_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("BACKTEST_RESULT_CACHE_DIRECTORY", raising=False)
    store = SimpleNamespace(cache_directory=tmp_path / "backtest")
    assert _market_result_cache_root(store) == store.cache_directory / "result-cache"


def test_support_worker_builds_then_reuses_local_feature_cache(tmp_path: Path) -> None:
    recovery, market = configs()
    cache_directory = tmp_path / "raw"
    cache_directory.mkdir()
    source = cache_directory / "TEST-5-1y.csv.gz"
    candles = trend_frame(step=0.04, periods=80)
    candles.to_csv(source, index_label="Timestamp", compression="gzip")
    task = {
        "symbol": "TEST",
        "cacheDirectory": str(cache_directory),
        "featureCacheDirectory": str(tmp_path / "features"),
        "recoveryConfig": recovery,
        "marketConfig": market,
        "analysisStart": candles.index[0].to_pydatetime(),
        "now": (candles.index[-1] + pd.Timedelta(minutes=5)).to_pydatetime(),
        "timeframe": "5m",
        "durationYears": 1,
        "warmupBars": 25,
        "rawCacheTtlSeconds": 3_600,
    }
    cold = performance.prepare_support_symbol_task(task)
    warm = performance.prepare_support_symbol_task(task)
    assert Path(cold["featurePath"]).is_file()
    assert cold["featureCacheHit"] is False
    assert warm["featureCacheHit"] is True
