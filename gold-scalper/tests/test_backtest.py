import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

import backtest
import signals
from backtest import Settings


def ticks_from(rows, start="2026-01-05 10:00:00"):
    """rows: list of (seconds_offset, bid, ask)."""
    t0 = pd.Timestamp(start)
    idx = [t0 + pd.Timedelta(seconds=s) for s, _, _ in rows]
    return pd.DataFrame({"bid": [b for _, b, _ in rows], "ask": [a for _, _, a in rows]}, index=idx)


def one_signal(monkeypatch, direction, bar="2026-01-05 10:00:00"):
    def fake(name, bars, params=None):
        s = pd.Series(0, index=bars.index)
        s[pd.Timestamp(bar)] = direction
        return s
    monkeypatch.setattr(signals, "compute", fake)


BASE = dict(signal="x", tp=0.5, sl=2.0, max_hold_min=60, lot=0.01, contract=100,
            commission=0.0, max_spread=0.35, hours=tuple(range(24)),
            max_trades_day=99, daily_loss_limit=999)


def run(rows, **kw):
    tk = ticks_from(rows)
    return backtest.run(tk, backtest.make_bars(tk), Settings(**{**BASE, **kw}), params={})


def test_buy_hits_tp_on_bid_after_paying_spread(monkeypatch):
    one_signal(monkeypatch, 1)
    rows = [(0, 2650.00, 2650.30), (60, 2650.00, 2650.30),     # entry at ask 2650.30
            (70, 2650.70, 2651.00),                            # bid 2650.70 < 2650.80: not yet
            (80, 2650.80, 2651.10)]                            # bid reaches TP
    tr = run(rows)
    assert len(tr) == 1 and tr.reason[0] == "TP"
    assert tr.entry[0] == pytest.approx(2650.30)
    assert tr.pnl[0] == pytest.approx(0.50)                    # 0.50 * 100 oz * 0.01 lot


def test_sell_stop_loss_fills_at_gap_price(monkeypatch):
    one_signal(monkeypatch, -1)
    rows = [(0, 2650.0, 2650.3), (60, 2650.0, 2650.3),         # sell at bid 2650.00, SL ask 2652.00
            (70, 2651.5, 2651.8), (80, 2652.5, 2652.8)]        # gaps through stop
    tr = run(rows)
    assert tr.reason[0] == "SL"
    assert tr.exit[0] == pytest.approx(2652.8)
    assert tr.pnl[0] == pytest.approx(-2.80)


def test_wide_spread_skips_entry(monkeypatch):
    one_signal(monkeypatch, 1)
    tr = run([(0, 2650.0, 2650.3), (60, 2650.0, 2650.9), (90, 2660, 2660.3)])
    assert tr.empty


def test_time_exit(monkeypatch):
    one_signal(monkeypatch, 1)
    rows = [(0, 2650.0, 2650.3), (60, 2650.0, 2650.3), (60 + 61, 2650.1, 2650.4)]
    tr = run(rows, max_hold_min=1)
    assert tr.reason[0] == "TIME" and tr.pnl[0] == pytest.approx(-0.20)


def test_commission_is_charged(monkeypatch):
    one_signal(monkeypatch, 1)
    rows = [(0, 2650.0, 2650.3), (60, 2650.0, 2650.3), (80, 2650.8, 2651.1)]
    tr = run(rows, commission=7.0)
    assert tr.pnl[0] == pytest.approx(0.50 - 0.07)


def test_signals_have_no_lookahead():
    rng = np.random.default_rng(0)
    c = 2650 + np.cumsum(rng.standard_normal(500))
    bars = pd.DataFrame({"open": c, "high": c + 0.3, "low": c - 0.3, "close": c + 0.1},
                        index=pd.date_range("2026-01-05", periods=500, freq="1min"))
    for name in signals.SIGNALS:
        full = signals.compute(name, bars)
        for cut in (100, 250, 499):
            part = signals.compute(name, bars.iloc[:cut + 1])
            assert part.iloc[-1] == full.iloc[cut], name
