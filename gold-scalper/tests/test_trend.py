import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import trend_backtest as tb
from trend_backtest import TrendSettings


def bars_from(closes, spread=0.3, start="2026-01-05"):
    c = np.asarray(closes, float)
    idx = pd.date_range(start, periods=len(c), freq="D")
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "spread_usd": spread},
                        index=idx)


BASE = dict(lot=0.01, contract=100, min_spread=0.3, swap_long=-50.0, swap_short=-20.0, commission=0.0)


def test_buy_hold_pays_spread_and_swap():
    b = bars_from([2000, 2010, 2020, 2030])
    tr = tb.run(b, TrendSettings(strategy="buy_hold", **BASE))
    t = tr.iloc[0]
    assert t.entry == pytest.approx(2010.3)               # day-2 open + spread
    assert t.nights == 2
    assert t.price_pnl == pytest.approx((2030 - 2010.3) * 1)
    assert t.swap == pytest.approx(-50 * 0.01 * 2)


def test_donchian_enters_on_breakout_and_exits_on_channel():
    closes = [100] * 5 + [110, 111, 112] + [90, 90]
    b = bars_from(closes)
    tr = tb.run(b, TrendSettings(strategy="donchian", p1=5, p2=2, long_only=True, **BASE))
    assert len(tr) == 1
    assert tr.entry_time[0] == b.index[6]                  # breakout close on day 5 -> enter day 6 open
    assert tr.exit_time[0] == b.index[9]                   # breakdown close on day 8 -> exit day 9 open


def test_stop_gap_fills_at_open():
    b = bars_from([100] * 25 + [105, 106, 80, 80])
    b.loc[b.index[27], ["open", "high", "low"]] = [80, 81, 79]
    s = TrendSettings(strategy="donchian", p1=5, p2=2, atr_stop=3.0, **BASE)
    tr = tb.run(b, s)                                      # breakout day 25 -> buy day 26 @ 106.3
    assert tr.entry[0] == pytest.approx(106.3)
    assert tr.reason[0] == "STOP" and tr.exit[0] == pytest.approx(80)


def test_out_of_sample_start_keeps_indicator_history():
    b = bars_from(list(range(100, 400)))
    s = TrendSettings(strategy="sma", p1=200, long_only=True, **BASE)
    start = b.index[250]
    tr = tb.run(b, s, start)
    assert tr.entry_time.min() == start                    # in position from the first OOS bar


def test_drawdown_counts_open_trade_losses():
    b = bars_from([2000, 2000, 1900, 1950, 2100])
    tr = tb.run(b, TrendSettings(strategy="buy_hold", **{**BASE, "swap_long": 0.0}))
    st = tb.stats(tr, b)
    assert len(tr) == 1 and st["max_dd"] == pytest.approx(100.3)   # $100 dip + $0.30 spread, still open
