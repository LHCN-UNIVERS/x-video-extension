import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from types import SimpleNamespace as NS

import pytest

import config
import guard

NOW = 1_767_607_200 + 10 * 3600          # 2026-01-05 20:00 server time (not a blocked hour)
DAY0 = 1_767_607_200 - (1_767_607_200 % 86400)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    TRADE_RETCODE_DONE = 10009
    DEAL_ENTRY_IN = 0

    def __init__(self):
        self.now, self.bid, self.spread = NOW, 4000.0, 0.3
        self.positions, self.deals, self.requests = [], [], []
        self._t = 100

    # helpers
    def open(self, side="BUY", price=4000.3, vol=0.01, sl=0.0, tp=0.0, t=None, profit=0.0):
        self._t += 1
        p = NS(ticket=self._t, identifier=self._t, symbol="XAUUSDm", magic=0,
               type=0 if side == "BUY" else 1, volume=vol, price_open=price, sl=sl, tp=tp,
               time=t or self.now - 30, profit=profit, swap=0.0)
        self.positions.append(p)
        self.deals.append(NS(position_id=p.identifier, entry=0, time=p.time, symbol=p.symbol,
                             profit=0.0, commission=0.0, swap=0.0, fee=0.0))
        return p

    # MT5 API
    def initialize(self): return True
    def shutdown(self): pass
    def last_error(self): return (0, "")
    def symbol_select(self, s, on): return True
    def account_info(self): return NS(login=1, trade_mode=0, balance=100.0, currency="USD")
    def symbol_info(self, s): return NS(digits=2, point=0.01, filling_mode=2, trade_stops_level=0,
                                        volume_step=0.01, volume_min=0.01)
    def symbol_info_tick(self, s): return NS(time=self.now, bid=self.bid, ask=round(self.bid + self.spread, 2))
    def positions_get(self): return tuple(self.positions)
    def history_deals_get(self, a, b): return tuple(self.deals)

    def order_send(self, r):
        self.requests.append(r)
        p = next(p for p in self.positions if p.ticket == r["position"])
        if r["action"] == self.TRADE_ACTION_SLTP:
            p.sl, p.tp = r["sl"], r["tp"]
        else:
            p.volume = round(p.volume - r["volume"], 8)
            if p.volume <= 0:
                self.positions.remove(p)
        return NS(retcode=self.TRADE_RETCODE_DONE)


@pytest.fixture
def env(monkeypatch):
    for k, v in dict(GUARD_DRY_RUN=False, GUARD_SL_DIST=3.0, GUARD_TP_DIST=0.0, GUARD_TRAIL_DIST=2.0,
                     GUARD_TRAIL_START=1.0, GUARD_MAX_LOT=0.01, GUARD_BLOCKED_HOURS=[0, 1, 2],
                     GUARD_MAX_TRADES_PER_DAY=10, GUARD_DAILY_LOSS_LIMIT=20.0).items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(guard, "RETRY_SECONDS", 0)
    m = FakeMT5()
    g = guard.Guard(m)
    return m, g


def test_adds_stop_loss_to_buy_and_sell(env):
    m, g = env
    b = m.open("BUY", 4000.3)
    s = m.open("SELL", 4000.0)
    g.step()
    assert b.sl == pytest.approx(3997.3)
    assert s.sl == pytest.approx(4003.0)


def test_adds_take_profit_when_enabled(env, monkeypatch):
    m, g = env
    monkeypatch.setattr(config, "GUARD_TP_DIST", 3.0)
    b = m.open("BUY", 4000.3)
    g.step()
    assert b.tp == pytest.approx(4003.3)


def test_keeps_your_own_stop_loss(env):
    m, g = env
    b = m.open("BUY", 4000.3, sl=3999.0)
    g.step()
    assert b.sl == pytest.approx(3999.0) and m.requests == []


def test_trailing_starts_after_profit_and_never_moves_back(env):
    m, g = env
    b = m.open("BUY", 4000.0)
    g.step()                                   # adds SL 3997
    m.bid = 4000.5; g.step()                   # +0.5 < start 1.0 -> no trail
    assert b.sl == pytest.approx(3997.0)
    m.bid = 4006.0; g.step()                   # trail to 4004
    assert b.sl == pytest.approx(4004.0)
    m.bid = 4005.0; g.step()                   # price falls back: SL stays
    assert b.sl == pytest.approx(4004.0)


def test_trailing_for_sell_uses_ask(env):
    m, g = env
    s = m.open("SELL", 4000.0, sl=4003.0)
    m.bid = 3994.7                              # ask 3995.0 -> +5 profit
    g.step()
    assert s.sl == pytest.approx(3997.0)


def test_cuts_big_lot_down_to_max(env):
    m, g = env
    b = m.open("BUY", 4000.3, vol=0.05)
    g.step()
    assert b.volume == pytest.approx(0.01)
    assert m.requests[0]["volume"] == pytest.approx(0.04)


def test_closes_trade_opened_in_blocked_hour(env):
    m, g = env
    m.now = DAY0 + 1 * 3600 + 600               # 01:10 server time
    m.open("BUY", 4000.3)
    g.step()
    assert m.positions == []


def test_closes_trades_after_daily_max(env, monkeypatch):
    m, g = env
    monkeypatch.setattr(config, "GUARD_MAX_TRADES_PER_DAY", 2)
    first, second, third = (m.open("BUY", 4000.3, t=NOW - 300 + i) for i in range(3))
    g.step()
    assert [p.ticket for p in m.positions] == [first.ticket, second.ticket]


def test_daily_loss_limit_closes_everything_and_new_trades(env):
    m, g = env
    m.deals.append(NS(position_id=99, entry=1, time=NOW - 600, symbol="XAUUSDm",
                      profit=-18.0, commission=0.0, swap=0.0, fee=0.0))
    m.open("BUY", 4000.3, profit=-2.5)          # -18 closed + -2.5 open = -20.5
    g.step()
    assert m.positions == []
    m.open("BUY", 4000.3)                        # new trade the same day -> closed too
    g.step()
    assert m.positions == []


def test_closes_trade_already_past_stop_without_sl(env):
    m, g = env
    m.bid = 3996.0
    m.open("BUY", 4000.3)                        # already -4.3, beyond the $3 stop
    g.step()
    assert m.positions == []


def test_dry_run_changes_nothing(env, monkeypatch):
    m, g = env
    monkeypatch.setattr(config, "GUARD_DRY_RUN", True)
    b = m.open("BUY", 4000.3, vol=0.05)
    g.step()
    assert m.requests == [] and b.sl == 0.0 and b.volume == 0.05


def test_ignores_other_symbols(env):
    m, g = env
    p = m.open("BUY", 1.1)
    p.symbol = "EURUSDm"
    g.step()
    assert m.requests == []


def test_dry_run_logs_each_action_once(env, monkeypatch, caplog):
    m, g = env
    monkeypatch.setattr(config, "GUARD_DRY_RUN", True)
    m.open("BUY", 4000.3)
    with caplog.at_level("INFO", logger="guard"):
        for _ in range(5):
            g.step()
    assert sum("would add SL" in r.message for r in caplog.records) == 1
