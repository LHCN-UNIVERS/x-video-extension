"""Minimal stand-in for the MetaTrader5 package so live_bot can be exercised off-Windows."""
from types import SimpleNamespace as NS

import numpy as np

ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
TIMEFRAME_M1 = 1
TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
TRADE_RETCODE_DONE = 10009
DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1

state = NS(trade_mode=ACCOUNT_TRADE_MODE_DEMO, now=1_767_607_200, bars=None, bid=2650.0,
           spread=0.25, positions=[], requests=[], ticket=1000, slip=0.0)


def reset(signal_dir=1, n=300):
    rng = np.random.default_rng(1)
    c = 2650 + np.cumsum(rng.standard_normal(n) * 0.2)
    t = state.now - 60 * np.arange(n)[::-1]
    state.bars = np.array(list(zip(t, c, c + 0.2, c - 0.2, c)),
                          dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")])
    state.positions, state.requests = [], []


def initialize(): return True
def shutdown(): pass
def last_error(): return (0, "ok")
def symbol_select(sym, on): return True
def account_info(): return NS(login=1, trade_mode=state.trade_mode, balance=1000.0, currency="USD")
def symbol_info(sym): return NS(digits=2, point=0.01, filling_mode=2, trade_stops_level=0,
                                volume_min=0.01, trade_contract_size=100)
def symbol_info_tick(sym): return NS(time=int(state.now), bid=state.bid, ask=round(state.bid + state.spread, 2))
def copy_rates_from_pos(sym, tf, start, n): return state.bars[-n:]
def positions_get(symbol=None): return tuple(state.positions)
def history_deals_get(a, b): return ()


def order_send(req):
    state.requests.append(req)
    if req["action"] == TRADE_ACTION_DEAL and "position" not in req:
        state.ticket += 1
        fill = req["price"] + state.slip
        state.positions.append(NS(ticket=state.ticket, magic=req["magic"], price_open=fill,
                                  tp=req["tp"], sl=req["sl"], type=req["type"], volume=req["volume"],
                                  time=state.now))
        return NS(retcode=TRADE_RETCODE_DONE, volume=req["volume"], price=fill, order=state.ticket)
    if req["action"] == TRADE_ACTION_SLTP:
        for p in state.positions:
            if p.ticket == req["position"]:
                p.tp, p.sl = req["tp"], req["sl"]
    if req["action"] == TRADE_ACTION_DEAL and "position" in req:
        state.positions = [p for p in state.positions if p.ticket != req["position"]]
    return NS(retcode=TRADE_RETCODE_DONE)
