"""Tick-level backtest of the "+$0.50 take profit" gold scalper.

Uses real bid/ask ticks, so the spread is paid exactly as in live trading:
  * a BUY opens at the ask and closes when the BID reaches entry + TP_DIST
  * a SELL opens at the bid and closes when the ASK reaches entry - TP_DIST
  * stop loss is filled at the tick that crosses it (gaps = slippage against you)

Usage:
  python backtest.py ticks.csv                      # run with config.py settings
  python backtest.py ticks.csv --signal breakout --sl 3
  python backtest.py ticks.csv --sweep --split 0.7  # try combinations on the first 70%,
                                                    # then check the best one on the last 30%
"""
import argparse
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config
import signals


@dataclass
class Settings:
    signal: str = config.SIGNAL
    tp: float = config.TP_DIST
    sl: float = config.SL_DIST
    max_hold_min: int = config.MAX_HOLD_MINUTES
    lot: float = config.LOT
    contract: float = config.CONTRACT_SIZE
    commission: float = config.COMMISSION_PER_LOT
    max_spread: float = config.MAX_SPREAD
    hours: tuple = tuple(config.TRADE_HOURS)
    max_trades_day: int = config.MAX_TRADES_PER_DAY
    daily_loss_limit: float = config.DAILY_LOSS_LIMIT


# --------------------------------------------------------------------------- data

def load_ticks(path):
    """CSV with columns time_msc (epoch ms) or time (datetime), bid, ask."""
    df = pd.read_csv(path)
    if "time_msc" in df:
        t = pd.to_datetime(df["time_msc"], unit="ms")
    else:
        t = pd.to_datetime(df["time"])
    df = pd.DataFrame({"bid": df["bid"].values, "ask": df["ask"].values}, index=t)
    df = df.replace(0, np.nan).ffill().dropna()
    df = df[df["ask"] >= df["bid"]]
    return df.sort_index(kind="stable")


def make_bars(ticks):
    bars = ticks["bid"].resample("1min").ohlc().dropna()
    return bars


# --------------------------------------------------------------------------- engine

def run(ticks, bars, s: Settings, params=None):
    if params is None:
        params = config.SIGNAL_PARAMS.get(s.signal, {})
    sig = signals.compute(s.signal, bars, params)
    entries = sig[sig != 0]

    t = ticks.index.values.astype("datetime64[ms]").astype(np.int64)
    bid, ask = ticks["bid"].values, ticks["ask"].values
    hours = set(s.hours)
    hold_ms = s.max_hold_min * 60_000
    busy_until = -1
    day_key, day_trades, day_pnl = None, 0, 0.0
    trades = []

    for bar_start, direction in entries.items():
        entry_ms = int(pd.Timestamp(bar_start).value // 1_000_000) + 60_000  # bar close
        if entry_ms < busy_until:
            continue
        i = int(np.searchsorted(t, entry_ms))
        if i >= len(t) or t[i] - entry_ms > 60_000:   # no ticks right after the bar (gap / weekend)
            continue
        ts = pd.Timestamp(t[i], unit="ms")
        if ts.hour not in hours:
            continue
        spread = ask[i] - bid[i]
        if spread > s.max_spread:
            continue
        if ts.date() != day_key:
            day_key, day_trades, day_pnl = ts.date(), 0, 0.0
        if day_trades >= s.max_trades_day or day_pnl <= -s.daily_loss_limit:
            continue

        if direction > 0:
            entry = ask[i]
            tp, sl = entry + s.tp, entry - s.sl
        else:
            entry = bid[i]
            tp, sl = entry - s.tp, entry + s.sl

        j, exit_px, reason = _find_exit(t, bid, ask, i + 1, direction, tp, sl,
                                        t[i] + hold_ms if hold_ms else None)
        if j is None:            # data ended while in the trade
            break
        pnl = direction * (exit_px - entry) * s.contract * s.lot - s.commission * s.lot
        trades.append({
            "entry_time": ts, "exit_time": pd.Timestamp(t[j], unit="ms"),
            "side": "BUY" if direction > 0 else "SELL",
            "entry": entry, "exit": exit_px, "spread": spread,
            "reason": reason, "pnl": pnl,
        })
        busy_until = t[j]
        day_trades += 1
        day_pnl += pnl

    return pd.DataFrame(trades)


def _find_exit(t, bid, ask, start, direction, tp, sl, deadline, chunk=200_000):
    """First tick at/after `start` that hits TP, SL or the time limit."""
    n = len(t)
    k = start
    while k < n:
        e = min(k + chunk, n)
        if direction > 0:   # long closes on the bid
            px = bid[k:e]
            hit_tp, hit_sl = px >= tp, px <= sl
        else:               # short closes on the ask
            px = ask[k:e]
            hit_tp, hit_sl = px <= tp, px >= sl
        hit_time = t[k:e] >= deadline if deadline is not None else np.zeros(e - k, bool)
        any_hit = hit_tp | hit_sl | hit_time
        if any_hit.any():
            m = int(np.argmax(any_hit))
            j = k + m
            if hit_sl[m]:
                return j, px[m], "SL"          # filled at market -> includes slippage
            if hit_tp[m]:
                return j, tp, "TP"             # limit order -> filled at TP price
            return j, px[m], "TIME"
        k = e
    return None, None, None


# --------------------------------------------------------------------------- report

def stats(tr):
    if tr.empty:
        return {"trades": 0, "win_rate": 0, "net": 0, "profit_factor": 0, "max_dd": 0,
                "avg_win": 0, "avg_loss": 0, "breakeven_wr": 0}
    w, l = tr[tr.pnl > 0].pnl, tr[tr.pnl <= 0].pnl
    eq = tr.pnl.cumsum()
    avg_w, avg_l = (w.mean() if len(w) else 0), (-l.mean() if len(l) else 0)
    return {
        "trades": len(tr),
        "win_rate": len(w) / len(tr),
        "net": tr.pnl.sum(),
        "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else float("inf"),
        "max_dd": (eq.cummax().clip(lower=0) - eq).max(),
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "breakeven_wr": avg_l / (avg_w + avg_l) if (avg_w + avg_l) else 0,
    }


def print_report(tr, s: Settings, title=""):
    st = stats(tr)
    print(f"\n=== {title or 'Backtest'} ===")
    print(f"signal={s.signal}  TP=+{s.tp:.2f}  SL=-{s.sl:.2f}  lot={s.lot}  "
          f"max_spread={s.max_spread}  commission/lot={s.commission}")
    if not st["trades"]:
        print("No trades.")
        return st
    print(f"trades          {st['trades']}")
    print(f"win rate        {st['win_rate']:.1%}   (needed to break even: {st['breakeven_wr']:.1%})")
    print(f"avg win / loss  ${st['avg_win']:.2f} / -${st['avg_loss']:.2f}")
    print(f"net profit      ${st['net']:.2f}")
    print(f"profit factor   {st['profit_factor']:.2f}")
    print(f"max drawdown    ${st['max_dd']:.2f}")
    print(f"avg spread paid ${tr.spread.mean():.2f}")
    print("exits           " + ", ".join(f"{k}={v}" for k, v in tr.reason.value_counts().items()))
    return st


def sweep(ticks, bars, base: Settings, sls=(1.0, 2.0, 3.0, 5.0)):
    rows = []
    for name in signals.SIGNALS:
        for sl in sls:
            s = Settings(**{**asdict(base), "signal": name, "sl": sl})
            st = stats(run(ticks, bars, s))
            rows.append({"signal": name, "sl": sl, **st})
    df = pd.DataFrame(rows).sort_values("net", ascending=False)
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 140):
        print(df[["signal", "sl", "trades", "win_rate", "breakeven_wr", "net",
                  "profit_factor", "max_dd"]].to_string(index=False))
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticks")
    ap.add_argument("--signal")
    ap.add_argument("--tp", type=float)
    ap.add_argument("--sl", type=float)
    ap.add_argument("--sweep", action="store_true", help="try every signal x stop-loss")
    ap.add_argument("--split", type=float, default=0,
                    help="fraction of data used for the sweep; the rest is the out-of-sample check")
    ap.add_argument("--trades-out", default="trades.csv")
    a = ap.parse_args()

    ticks = load_ticks(a.ticks)
    print(f"Loaded {len(ticks):,} ticks  {ticks.index[0]} -> {ticks.index[-1]}  "
          f"median spread ${(ticks.ask - ticks.bid).median():.2f}")
    s = Settings()
    if a.signal: s.signal = a.signal
    if a.tp: s.tp = a.tp
    if a.sl: s.sl = a.sl

    if a.sweep:
        if a.split:
            cut = ticks.index[int(len(ticks) * a.split)]
            ins, oos = ticks[ticks.index < cut], ticks[ticks.index >= cut]
        else:
            ins, oos = ticks, None
        print(f"\n--- Sweep on {'first %.0f%%' % (a.split * 100) if a.split else 'all data'} ---")
        table = sweep(ins, make_bars(ins), s)
        best = table.iloc[0]
        s.signal, s.sl = best.signal, float(best.sl)
        if oos is not None:
            tr = run(oos, make_bars(oos), s)
            print_report(tr, s, "OUT-OF-SAMPLE check of the best combination (data it never saw)")
            tr.to_csv(a.trades_out, index=False)
        return

    tr = run(ticks, make_bars(ticks), s)
    print_report(tr, s)
    if not tr.empty:
        tr.to_csv(a.trades_out, index=False)
        print(f"\nTrade list saved to {a.trades_out}")


if __name__ == "__main__":
    main()
