import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from types import SimpleNamespace as D

import pytest

import analyze_my_trades as am

T0 = 1_767_607_200   # 2026-01-05 10:00 UTC


def deal(pos, typ, entry, t, price, profit=0.0, vol=0.01, commission=0.0, swap=0.0, sym="XAUUSDm"):
    return D(position_id=pos, type=typ, entry=entry, time=T0 + t, price=price, profit=profit,
             volume=vol, commission=commission, swap=swap, fee=0.0, symbol=sym, magic=0)


DEALS = [
    D(position_id=0, type=2, entry=0, time=T0, price=0, profit=1000, volume=0, commission=0,
      swap=0, fee=0, symbol="", magic=0),                                   # deposit: ignored
    deal(1, 0, 0, 0, 2650.0, commission=-0.05), deal(1, 1, 1, 120, 2651.0, profit=1.0),   # buy +1
    deal(2, 1, 0, 600, 2652.0, vol=0.02),                                                  # sell 0.02
    deal(2, 0, 1, 900, 2653.0, profit=-1.0, vol=0.01),                                     # partial close
    deal(2, 0, 1, 1200, 2654.0, profit=-2.0, vol=0.01),
    deal(3, 0, 0, 2000, 2655.0),                                                           # still open
]


def test_build_trades_groups_positions():
    df = am.build_trades(DEALS)
    assert list(df.position) == [1, 2]                     # deposit and open trade excluded
    t1, t2 = df.iloc[0], df.iloc[1]
    assert t1.side == "BUY" and t1.net == pytest.approx(0.95) and t1.hold_min == pytest.approx(2)
    assert t2.side == "SELL" and t2.volume == pytest.approx(0.02)
    assert t2.net == pytest.approx(-3.0) and t2.net_per_001 == pytest.approx(-1.5)
    assert t2.hold_min == pytest.approx(10)                # until the LAST partial close


def test_summary_and_report_run(capsys):
    df = am.build_trades(DEALS)
    s = am.summary(df)
    assert s["trades"] == 2 and s["win_rate"] == 0.5 and s["net"] == pytest.approx(-2.05)
    am.report(df)
    out = capsys.readouterr().out
    assert "NET RESULT" in out and "By hour opened" in out
