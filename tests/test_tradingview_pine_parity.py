from pathlib import Path

import pandas as pd
from backend.core.indicators import wilder_rsi
from backend.core.models import MarketContext
from backend.strategies.rsi_dip_ladder_v1 import RsiDipLadderV1


def pine_reference(close: pd.Series, *, length: int, low: float, recovery: float, expiry: int) -> list[bool]:
    rsi = wilder_rsi(close, length)
    armed = False
    armed_at: int | None = None
    output: list[bool] = []
    for bar, value in enumerate(rsi):
        previous = rsi.iloc[bar - 1] if bar else float("nan")
        if pd.notna(value) and value <= low:
            armed, armed_at = True, bar
        if armed and armed_at is not None and bar - armed_at > expiry:
            armed, armed_at = False, None
        buy = bool(armed and pd.notna(previous) and previous < recovery <= value)
        output.append(buy)
        if buy:
            armed, armed_at = False, None
    return output


def test_pine_and_python_emit_on_identical_closed_candles():
    closes = [100 + ((index % 13) - 6) * 1.7 + (8 if index % 29 == 0 else 0) for index in range(180)]
    index = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    candles = pd.DataFrame({"Open": closes, "High": [x + 1 for x in closes], "Low": [x - 1 for x in closes], "Close": closes, "Volume": 1_000}, index=index)
    strategy = RsiDipLadderV1()
    cfg = strategy.resolve({})
    frame = strategy.decision_frame(candles, MarketContext(market="CRYPTO", symbol="BTC-USDT", timeframe="5m", timezone="UTC"), cfg)
    expected = pine_reference(candles["Close"], length=cfg["rsi_length"], low=cfg["rsi_low"], recovery=cfg["rsi_recovery"], expiry=cfg["setup_expiry_bars"])
    assert frame.index[frame["Decision"].eq("BUY")].tolist() == frame.index[pd.Series(expected, index=index)].tolist()


def test_multi_symbol_script_retains_contract_and_limit():
    script = Path("integrations/tradingview/rsi_dip_ladder_multi_v1.pine").read_text()
    assert "dynamic_requests=true" in script
    assert "math.min(array.size(symbols), 40)" in script
    assert '"strategyId"' not in script  # JSON is escaped inside a Pine string.
    assert "rsi_dip_ladder_v1" in script
