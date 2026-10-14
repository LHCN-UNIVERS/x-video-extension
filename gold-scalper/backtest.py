"""Tick-level backtest of the gold scalper (1-minute signals, real bid/ask ticks).

The spread is paid exactly as in live trading:
  * a BUY opens at the ask and closes on the BID; a SELL opens at the bid and closes on the ASK
  * take profit fills at the TP price; stop loss / trailing stop fill at the tick that crosses
    them (gaps = slippage against you)

Usage:
  python backtest.py ticks.csv                      # run with config.py settings
  python backtest.py ticks.csv --signal london_breakout --tp 0 --trail 3 --sl 5
  python backtest.py ticks.csv --sweep --split 0.7  # big grid on the first 70%, then the top 3
                                                    # are checked on the last 30% it never saw
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
    trail: float = config.TRAIL_DIST
    max_hold_min: int = config.MAX_HOLD_MINUTES
    lot: float = config.LOT
    contract: float = config.CONTRACT_SIZE
    commission: float = config.COMMISSION_PER_LOT
    max_spread: float = config.MAX_SPREAD
    min_atr_spread: float = config.MIN_ATR_SPREAD
    hours: tuple = tuple(config.TRADE_HOURS)
    max_trades_day: int = config.MAX_TRADES_PER_DAY
    daily_loss_limit: float = config.DAILY_LOSS_LIMIT


# Exit styles tried by --sweep: small fixed targets vs. letting winners run.
EXIT_PRESETS = [
    {"exit": "TP0.5/SL2", "tp": 0.5, "sl": 2.0, "trail": 0.0, "max_hold_min": 60},
    {"exit": "TP1/SL3", "tp": 1.0, "sl": 3.0, "trail": 0.0, "max_hold_min": 120},
    {"exit": "TP5/SL5", "tp": 5.0, "sl": 5.0, "trail": 0.0, "max_hold_min": 480},
    {"exit": "trail2/SL3", "tp": 0.0, "sl": 3.0, "trail": 2.0, "max_hold_min": 480},
    {"exit": "trail5/SL5", "tp": 0.0, "sl": 5.0, "trail": 5.0, "max_hold_min": 960},
]
ATR_FILTERS = [0.0, 3.0]   # off, or "1-min ATR must be at least 3x the spread"


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
    return ticks["bid"].resample("1min").ohlc().dropna()


# --------------------------------------------------------------------------- engine

def run(ticks, bars, s: Settings, params=None, _cache=None):
    if params is None:
        params = config.SIGNAL_PARAMS.get(s.signal, {})
    sig = signals.compute(s.signal, bars, params)
    entries = sig[sig != 0]
    bar_atr = signals.atr(bars) if s.min_atr_spread else None

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
        if bar_atr is not None and not (bar_atr.loc[bar_start] >= s.min_atr_spread * spread):
            continue
        if ts.date() != day_key:
            day_key, day_trades, day_pnl = ts.date(), 0, 0.0
        if day_trades >= s.max_trades_day or day_pnl <= -s.daily_loss_limit:
            continue

        entry = ask[i] if direction > 0 else bid[i]
        j, exit_px, reason = find_exit(t, bid, ask, i + 1, direction, entry, s.tp, s.sl, s.trail,
                                       t[i] + hold_ms if hold_ms else None)
        if j is None:            # data ended while in the trade
            break
        pnl = direction * (exit_px - entry) * s.contract * s.lot - s.commission * s.lot
        trades.append({
            "entry_time": ts, "exit_time": pd.Timestamp(t[j], unit="ms"),
            "side": "BUY" if direction > 0 else "SELL",
            "entry": entry, "exit": exit_px, "spread": spread,
            "reason": reason, "pnl": pnl, "spread_cost": spread * s.contract * s.lot,
        })
        busy_until = t[j]
        day_trades += 1
        day_pnl += pnl

    return pd.DataFrame(trades)


def find_exit(t, bid, ask, start, direction, entry, tp_dist, sl_dist, trail, deadline, chunk=200_000):
    """First tick at/after `start` that hits TP, (trailing) stop or the time limit.

    tp_dist = 0 means no take profit. The trailing stop sits `trail` behind the best
    price reached so far and never moves back; the initial stop is `sl_dist` from entry.
    """
    n = len(t)
    long_ = direction > 0
    best = entry
    k = start
    while k < n:
        e = min(k + chunk, n)
        px = bid[k:e] if long_ else ask[k:e]          # long closes on the bid, short on the ask
        if long_:
            tp = entry + tp_dist
            hit_tp = px >= tp if tp_dist else np.zeros(e - k, bool)
            stop = np.full(e - k, entry - sl_dist)
            if trail:
                run_best = np.maximum.accumulate(np.maximum(px, best))
                stop = np.maximum(stop, run_best - trail)
            hit_sl = px <= stop
        else:
            tp = entry - tp_dist
            hit_tp = px <= tp if tp_dist else np.zeros(e - k, bool)
            stop = np.full(e - k, entry + sl_dist)
            if trail:
                run_best = np.minimum.accumulate(np.minimum(px, best))
                stop = np.minimum(stop, run_best + trail)
            hit_sl = px >= stop
        hit_time = t[k:e] >= deadline if deadline is not None else np.zeros(e - k, bool)
        any_hit = hit_tp | hit_sl | hit_time
        if any_hit.any():
            m = int(np.argmax(any_hit))
            j = k + m
            if hit_sl[m]:
                moved = trail and (stop[m] != (entry - sl_dist if long_ else entry + sl_dist))
                return j, px[m], "TRAIL" if moved else "SL"   # market fill -> includes slippage
            if hit_tp[m]:
                return j, tp, "TP"                            # limit order -> filled at TP price
            return j, px[m], "TIME"
        if trail:
            best = run_best[-1]
        k = e
    return None, None, None


# --------------------------------------------------------------------------- report

def stats(tr):
    if tr.empty:
        return {"trades": 0, "win_rate": 0, "net": 0, "profit_factor": 0, "max_dd": 0,
                "avg_win": 0, "avg_loss": 0, "breakeven_wr": 0, "spread_paid": 0}
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
        "spread_paid": tr.spread_cost.sum(),
    }


def describe(s: Settings):
    ex = (f"TP=+{s.tp:.2f}" if s.tp else "no TP") + f"  SL=-{s.sl:.2f}"
    if s.trail:
        ex += f"  trail={s.trail:.2f}"
    flt = f"  ATR filter>={s.min_atr_spread:g}x spread" if s.min_atr_spread else ""
    return f"signal={s.signal}  {ex}  max_hold={s.max_hold_min}m  lot={s.lot}{flt}"


def print_report(tr, s: Settings, title=""):
    st = stats(tr)
    print(f"\n=== {title or 'Backtest'} ===")
    print(describe(s))
    if not st["trades"]:
        print("No trades.")
        return st
    print(f"trades          {st['trades']}")
    print(f"win rate        {st['win_rate']:.1%}   (needed to break even: {st['breakeven_wr']:.1%})")
    print(f"avg win / loss  ${st['avg_win']:.2f} / -${st['avg_loss']:.2f}")
    print(f"net profit      ${st['net']:.2f}   (spread paid ~${st['spread_paid']:.2f})")
    print(f"profit factor   {st['profit_factor']:.2f}")
    print(f"max drawdown    ${st['max_dd']:.2f}")
    print("exits           " + ", ".join(f"{k}={v}" for k, v in tr.reason.value_counts().items()))
    if st["trades"] < 30:
        print(f"WARNING: only {st['trades']} trades - too few to trust either way. Test on more data.")
    return st


def print_by_hour(tr):
    if tr.empty:
        return
    g = tr.groupby(tr.entry_time.dt.hour).pnl
    df = pd.DataFrame({"trades": g.size(), "win_rate": g.apply(lambda p: (p > 0).mean()),
                       "net": g.sum()})
    print("\nBy entry hour (server time):")
    with pd.option_context("display.float_format", "{:.2f}".format):
        print(df.to_string())


MIN_TRADES = 15   # combinations with fewer trades are not ranked: too few to tell skill from luck


def sweep(ticks, bars, base: Settings, top=15):
    rows = []
    for name in signals.SIGNALS:
        for preset in EXIT_PRESETS:
            for f in ATR_FILTERS:
                kw = {k: v for k, v in preset.items() if k != "exit"}
                s = Settings(**{**asdict(base), **kw, "signal": name, "min_atr_spread": f})
                st = stats(run(ticks, bars, s))
                rows.append({"signal": name, "exit": preset["exit"], "atr_filter": f, **st,
                             "cfg": s})
                print(f"  tested {len(rows):>2}/{len(signals.SIGNALS) * len(EXIT_PRESETS) * len(ATR_FILTERS)}"
                      f"  {name:<16} {preset['exit']:<11} atr>={f:g}  net ${st['net']:.2f}",
                      flush=True)
    df = pd.DataFrame(rows).sort_values("net", ascending=False)
    few = df[df.trades < MIN_TRADES]
    df = df[df.trades >= MIN_TRADES]
    if len(few):
        print(f"\n({len(few)} combinations had fewer than {MIN_TRADES} trades and are not ranked.)")
    if df.empty:
        raise SystemExit("No combination had enough trades. Download more days of ticks.")
    print(f"\nTop {top}:")
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 160):
        print(df[["signal", "exit", "atr_filter", "trades", "win_rate", "breakeven_wr", "net",
                  "spread_paid", "profit_factor", "max_dd"]].head(top).to_string(index=False))
    rnd = df[df.signal == "coin_flip"].iloc[0] if (df.signal == "coin_flip").any() else None
    if rnd is not None:
        print(f"\nBest RANDOM entry (coin_flip): {rnd.exit}, net ${rnd.net:.2f}. "
              f"A real signal must clearly beat this.")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticks")
    ap.add_argument("--signal")
    ap.add_argument("--tp", type=float, help="take profit in $ (0 = none)")
    ap.add_argument("--sl", type=float)
    ap.add_argument("--trail", type=float, help="trailing stop in $ (0 = off)")
    ap.add_argument("--hold", type=int, help="max minutes in a trade (0 = no limit)")
    ap.add_argument("--atr", type=float, help="volatility filter: min 1-min ATR as multiple of spread")
    ap.add_argument("--sweep", action="store_true", help="try every signal x exit style x filter")
    ap.add_argument("--split", type=float, default=0,
                    help="fraction of data used for the sweep; the rest is the out-of-sample check")
    ap.add_argument("--trades-out", default="trades.csv")
    a = ap.parse_args()

    ticks = load_ticks(a.ticks)
    print(f"Loaded {len(ticks):,} ticks  {ticks.index[0]} -> {ticks.index[-1]}  "
          f"median spread ${(ticks.ask - ticks.bid).median():.2f}")
    s = Settings()
    for field, val in (("signal", a.signal), ("tp", a.tp), ("sl", a.sl), ("trail", a.trail),
                       ("max_hold_min", a.hold), ("min_atr_spread", a.atr)):
        if val is not None:
            setattr(s, field, val)

    if a.sweep:
        if a.split:
            cut = ticks.index[int(len(ticks) * a.split)]
            ins, oos = ticks[ticks.index < cut], ticks[ticks.index >= cut]
        else:
            ins, oos = ticks, None
        print(f"\n--- Sweep on {'first %.0f%%' % (a.split * 100) if a.split else 'all data'} ---")
        table = sweep(ins, make_bars(ins), s)
        if oos is not None:
            oos_bars = make_bars(oos)
            for rank, cfg in enumerate(table.head(3).cfg, 1):
                tr = run(oos, oos_bars, cfg)
                print_report(tr, cfg,
                             f"OUT-OF-SAMPLE #{rank} (data it never saw)")
                print_by_hour(tr)
                if rank == 1:
                    _save(tr, a.trades_out)
        return

    tr = run(ticks, make_bars(ticks), s)
    print_report(tr, s)
    print_by_hour(tr)
    _save(tr, a.trades_out)


def _save(tr, path):
    if tr.empty:
        return
    try:
        tr.to_csv(path, index=False)
        print(f"\nTrade list saved to {path}")
    except OSError as e:
        print(f"\n(Could not save {path}: {e})")


if __name__ == "__main__":
    main()
