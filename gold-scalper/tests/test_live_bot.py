import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pandas as pd
import pytest

import config
import live_bot
import signals
import fake_mt5 as mt5


@pytest.fixture
def bot(monkeypatch):
    mt5.reset()
    mt5.state.trade_mode = mt5.ACCOUNT_TRADE_MODE_DEMO
    mt5.state.spread, mt5.state.slip, mt5.state.bid = 0.25, 0.0, 2650.0
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "TRADE_HOURS", range(24))
    monkeypatch.setattr(signals, "compute",
                        lambda name, bars, params=None: pd.Series([0] * (len(bars) - 1) + [1]))
    b = live_bot.Bot(mt5)
    b.start()
    return b


def test_opens_buy_with_server_side_tp_050(bot):
    bot.step()
    (req,) = mt5.state.requests
    assert req["type"] == mt5.ORDER_TYPE_BUY
    assert req["price"] == pytest.approx(2650.25)
    assert req["tp"] == pytest.approx(2650.75) and req["sl"] == pytest.approx(2648.25)


def test_realigns_tp_after_slippage(bot):
    mt5.state.slip = 0.10
    bot.step()
    p = mt5.state.positions[0]
    assert p.price_open == pytest.approx(2650.35)
    assert p.tp == pytest.approx(2650.85)


def test_one_trade_at_a_time_and_once_per_bar(bot):
    bot.step(); bot.step()                       # same bar -> evaluated once
    mt5.state.bars = mt5.state.bars.copy(); mt5.state.bars["time"] += 60   # new bar
    bot.step()                                   # position still open -> skipped
    assert len([r for r in mt5.state.requests if r["action"] == mt5.TRADE_ACTION_DEAL]) == 1


def test_spread_filter(bot):
    mt5.state.spread = 0.60
    bot.step()
    assert mt5.state.requests == []


def test_dry_run_sends_nothing(bot, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    bot.step()
    assert mt5.state.requests == []


def test_refuses_real_account(monkeypatch):
    mt5.reset()
    mt5.state.trade_mode = mt5.ACCOUNT_TRADE_MODE_REAL
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "ALLOW_REAL_ACCOUNT", False)
    with pytest.raises(SystemExit):
        live_bot.Bot(mt5).start()


def test_time_exit_closes_old_position(bot, monkeypatch):
    bot.step()
    mt5.state.now += 61 * 60
    monkeypatch.setattr(signals, "compute", lambda *a, **k: pd.Series([0, 0]))
    bot.step()
    assert mt5.state.positions == []


def test_trailing_stop_moves_sl_up(bot, monkeypatch):
    monkeypatch.setattr(config, "TRAIL_DIST", 1.0)
    bot.step()
    mt5.state.bid = 2652.0
    bot.step()
    assert mt5.state.positions[0].sl == pytest.approx(2651.0)
    mt5.state.bid = 2651.5                                    # price falls back: stop must not move down
    bot.step()
    assert mt5.state.positions[0].sl == pytest.approx(2651.0)
