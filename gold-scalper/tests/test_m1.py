import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

import m1_backtest as m1
from m1_backtest import M1Settings

BASE = dict(lot=0.01, contract=100, min_spread=0.3, max_spread=1.0, commission=0.0,
            swap_long=-50.0, swap_short=0.0, tp=5.0, sl=5.0, trail=0.0, max_hold_min=480)


def make(rows, start="2026-01-05 13:00"):
    """rows: (open, high, low, close) per minute."""
    idx = pd.date_range(start, periods=len(rows), freq="1min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["spread_usd"] = 0.3
    return df


def sig_at(bars, k, d):
    s = pd.Series(0, index=bars.index)
    s.iloc[k] = d
    return s


def run(bars, k=0, d=1, **kw):
    return m1.run(bars, M1Settings(**{**BASE, **kw}), sig=sig_at(bars, k, d))


def test_buy_pays_spread_and_hits_tp():
    b = make([(2000, 2000, 2000, 2000), (2000, 2001, 1999, 2000), (2003, 2006, 2002, 2005)])
    tr = run(b)
    assert tr.entry[0] == pytest.approx(2000.3)                  # next open + spread
    assert tr.reason[0] == "TP" and tr.exit[0] == pytest.approx(2005.3)
    assert tr.pnl[0] == pytest.approx(5.0)


def test_stop_assumed_first_when_bar_touches_both():
    b = make([(2000, 2000, 2000, 2000), (2000, 2001, 1999, 2000), (2000, 2010, 1990, 2000)])
    tr = run(b)
    assert tr.reason[0] == "SL" and tr.exit[0] == pytest.approx(1995.3)


def test_gap_through_stop_fills_at_open():
    b = make([(2000, 2000, 2000, 2000), (2000, 2001, 1999, 2000), (1990, 1991, 1989, 1990)])
    tr = run(b)
    assert tr.reason[0] == "SL" and tr.exit[0] == pytest.approx(1990)


def test_short_closes_on_ask():
    b = make([(2000, 2000, 2000, 2000), (2000, 2001, 1999, 2000), (1996, 1996, 1994, 1995)])
    tr = run(b, d=-1)                                            # sell at bid 2000, TP when ask <= 1995
    assert tr.reason[0] == "TP" and tr.exit[0] == pytest.approx(1995)
    assert tr.pnl[0] == pytest.approx(5.0)


def test_trailing_stop_uses_previous_bars_only():
    b = make([(2000, 2000, 2000, 2000), (2000, 2000.5, 2000, 2000),
              (2001, 2004, 2001, 2003),                          # best 2004 -> stop 2002 from next bar
              (2003, 2003, 2001.5, 2002)])
    tr = run(b, tp=0.0, sl=5.0, trail=2.0)
    assert tr.reason[0] == "TRAIL" and tr.exit[0] == pytest.approx(2002)


def test_overnight_swap_charged():
    rows = [(2000, 2000, 2000, 2000)] * 3
    b = make(rows, start="2026-01-05 23:58")
    tr = run(b, max_hold_min=1)                                   # enters 23:59, exits 00:00 next day
    assert tr.swap[0] == pytest.approx(-50 * 0.01)


def test_start_only_trades_after_cut():
    b = make([(2000, 2000, 2000, 2000)] * 30)
    s = pd.Series(0, index=b.index); s.iloc[[2, 20]] = 1
    tr = m1.run(b, M1Settings(**{**BASE, "max_hold_min": 1}), start=b.index[15], sig=s)
    assert list(tr.entry_time) == [b.index[21]]
