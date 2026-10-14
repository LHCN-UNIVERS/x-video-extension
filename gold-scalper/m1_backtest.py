"""Backtest the 1-minute signals on YEARS of M1 bars (instead of a few months of ticks).

Same signals and exit styles as backtest.py, simulated bar by bar, with conservative fills:
  * entry at the next bar's open; a BUY pays the spread (bars are bid prices)
  * if a bar touches both the stop and the target, the STOP is assumed to come first
  * a bar that opens beyond the stop fills at that open (gap slippage)
  * spread = the bar's own spread, but never less than --min-spread
  * overnight swap from symbol_info.json when a trade is held past the server's midnight

Usage:
  python m1_backtest.py bars_M1.csv                         # grid on first 70%, top 3 on last 30%
  python m1_backtest.py bars_M1.csv --signal ny_breakout --tp 5 --sl 5 --hold 480
"""
import argparse
import json
import os
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config
import signals
from backtest import EXIT_PRESETS


@dataclass
class M1Settings:
    signal: str = "ny_breakout"
    tp: float = 5.0
    sl: float = 5.0
    trail: float = 0.0
    max_hold_min: int = 480
    lot: float = config.LOT
    contract: float = config.CONTRACT_SIZE
    min_spread: float = 0.25
    max_spread: float = 1.0           # skip entries when the bar's spread is wider (news spikes)
    commission: float = config.COMMISSION_PER_LOT
    swap_long: float = config.SWAP_LONG_PER_LOT
    swap_short: float = config.SWAP_SHORT_PER_LOT

    def name(self):
        ex = (f"TP{self.tp:g}" if self.tp else "noTP") + f"/SL{self.sl:g}"
        if self.trail:
            ex += f"/trail{self.trail:g}"
        return f"{self.signal} {ex} hold{self.max_hold_min}m"


MIN_TRADES = 100


def load_bars(path):
    df = pd.read_csv(path, parse_dates=["time"]).set_index("time").sort_index()
    if "spread_usd" not in df:
        df["spread_usd"] = 0.0
    return df


def load_swaps(s):
    if os.path.exists("symbol_info.json"):
        info = json.load(open("symbol_info.json"))
        if info.get("swap_long_per_lot") is not None:
            s.swap_long, s.swap_short = info["swap_long_per_lot"], info["swap_short_per_lot"]
            return "symbol_info.json (your broker)"
    return "config.py defaults"


# --------------------------------------------------------------------------- engine

def run(bars, s: M1Settings, start=None, sig=None):
    """Indicators/signals use all bars; trades are only opened from `start` on."""
    if sig is None:
        sig = signals.compute(s.signal, bars[["open", "high", "low", "close"]],
                              config.SIGNAL_PARAMS.get(s.signal, {}))
    o, h, l, c = (bars[k].values for k in ("open", "high", "low", "close"))
    spr = np.maximum(bars["spread_usd"].values, s.min_spread)
    times = bars.index
    tnum = times.values.astype("datetime64[m]").astype(np.int64)     # minutes
    n = len(c)
    first = int(np.searchsorted(times, start)) if start is not None else 0
    entries = np.flatnonzero(sig.values != 0)
    entries = entries[entries >= max(first - 1, 0)]
    trades, busy_until = [], -1

    for k in entries:
        i = k + 1                                   # enter at the next bar's open
        if i >= n or i <= busy_until or tnum[i] - tnum[k] > 5:   # skip gaps (weekend/rollover)
            continue
        if spr[i] > s.max_spread:
            continue
        d = int(sig.values[k])
        entry = o[i] + spr[i] if d > 0 else o[i]
        last = n - 1
        if s.max_hold_min:
            last = min(last, int(np.searchsorted(tnum, tnum[i] + s.max_hold_min, "right")) - 1)
        j, px, reason = find_exit(o, h, l, c, spr, i, last, d, entry, s)
        if j is None:
            break
        nights = (times[j].normalize() - times[i].normalize()).days
        swap = (s.swap_long if d > 0 else s.swap_short) * s.lot * nights
        pnl = d * (px - entry) * s.contract * s.lot + swap - s.commission * s.lot
        trades.append({"entry_time": times[i], "exit_time": times[j], "side": "BUY" if d > 0 else "SELL",
                       "entry": entry, "exit": px, "spread_cost": spr[i] * s.contract * s.lot,
                       "swap": swap, "pnl": pnl, "reason": reason})
        busy_until = j
    return pd.DataFrame(trades)


def find_exit(o, h, l, c, spr, i, last, d, entry, s):
    """Exit for a trade entered at bar i's open; looks at bars i..last."""
    if last < i:
        return None, None, None
    sl0 = entry - d * s.sl
    tp = entry + d * s.tp if s.tp else None
    # prices the position closes at: bid for longs, ask (bid + spread) for shorts
    oo = o[i:last + 1] + (0 if d > 0 else spr[i:last + 1])
    hh = h[i:last + 1] + (0 if d > 0 else spr[i:last + 1])
    ll = l[i:last + 1] + (0 if d > 0 else spr[i:last + 1])
    fav, adv = (hh, ll) if d > 0 else (ll, hh)          # favourable / adverse extreme of each bar

    stop = np.full(len(oo), sl0)
    if s.trail:
        # trail uses the best price of PREVIOUS bars (we don't know the order inside a bar)
        best_prev = np.concatenate([[entry], (np.maximum.accumulate(fav) if d > 0
                                              else np.minimum.accumulate(fav))[:-1]])
        best_prev = (np.maximum(best_prev, entry) if d > 0 else np.minimum(best_prev, entry))
        stop = np.maximum(stop, best_prev - s.trail) if d > 0 else np.minimum(stop, best_prev + s.trail)

    hit_sl = adv <= stop if d > 0 else adv >= stop
    hit_tp = (fav >= tp if d > 0 else fav <= tp) if tp is not None else np.zeros(len(oo), bool)
    hit = hit_sl | hit_tp
    if hit.any():
        m = int(np.argmax(hit))
        j = i + m
        if hit_sl[m]:                                   # stop first when both are touched
            gap = oo[m] <= stop[m] if d > 0 else oo[m] >= stop[m]
            reason = "TRAIL" if s.trail and stop[m] != sl0 else "SL"
            return j, (oo[m] if gap else stop[m]), reason
        gap = oo[m] >= tp if d > 0 else oo[m] <= tp
        return j, (oo[m] if gap and m > 0 else tp), "TP"
    if last == len(o) - 1 and not s.max_hold_min:
        return None, None, None
    close_px = c[last] + (0 if d > 0 else spr[last])
    return last, close_px, "TIME"


# --------------------------------------------------------------------------- report

def stats(tr):
    if tr.empty:
        return {"trades": 0, "win_rate": 0, "net": 0, "profit_factor": 0, "max_dd": 0,
                "breakeven_wr": 0, "pos_years": "-"}
    w, l = tr[tr.pnl > 0].pnl, tr[tr.pnl <= 0].pnl
    eq = tr.pnl.cumsum()
    avg_w, avg_l = (w.mean() if len(w) else 0), (-l.mean() if len(l) else 0)
    yearly = tr.groupby(tr.entry_time.dt.year).pnl.sum()
    return {"trades": len(tr), "win_rate": len(w) / len(tr), "net": tr.pnl.sum(),
            "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else float("inf"),
            "max_dd": (eq.cummax().clip(lower=0) - eq).max(),
            "breakeven_wr": avg_l / (avg_w + avg_l) if (avg_w + avg_l) else 0,
            "pos_years": f"{(yearly > 0).sum()}/{len(yearly)}"}


def print_report(tr, s, title):
    st = stats(tr)
    print(f"\n=== {title} ===\n{s.name()}  lot={s.lot}")
    if not st["trades"]:
        print("No trades.")
        return
    print(f"trades          {st['trades']}")
    print(f"win rate        {st['win_rate']:.1%}   (needed to break even: {st['breakeven_wr']:.1%})")
    print(f"net profit      ${st['net']:.2f}   (spread ~${tr.spread_cost.sum():.2f}, swap ${tr.swap.sum():.2f})")
    print(f"profit factor   {st['profit_factor']:.2f}")
    print(f"max drawdown    ${st['max_dd']:.2f}")
    print("exits           " + ", ".join(f"{k}={v}" for k, v in tr.reason.value_counts().items()))
    yearly = tr.groupby(tr.entry_time.dt.year).pnl.agg(["size", "sum"])
    print("by year         " + "  ".join(f"{y}: ${r['sum']:.0f} ({int(r['size'])})"
                                         for y, r in yearly.iterrows()))
    hourly = tr.groupby(tr.entry_time.dt.hour).pnl.agg(["size", "sum"])
    print("by entry hour   " + "  ".join(f"{hr}h: ${r['sum']:.0f} ({int(r['size'])})"
                                         for hr, r in hourly.iterrows()))
    if st["trades"] < 100:
        print(f"WARNING: only {st['trades']} trades - not enough to trust either way.")


def sweep(bars, base, end):
    rows, total = [], len(signals.SIGNALS) * len(EXIT_PRESETS)
    ohlc = bars[["open", "high", "low", "close"]]
    for name in signals.SIGNALS:
        sig = signals.compute(name, ohlc, config.SIGNAL_PARAMS.get(name, {}))
        sig[sig.index >= end] = 0                              # only trade the in-sample part
        for p in EXIT_PRESETS:
            kw = {k: v for k, v in p.items() if k != "exit"}
            s = M1Settings(**{**asdict(base), **kw, "signal": name})
            st = stats(run(bars[bars.index < end + pd.Timedelta(days=2)], s, sig=sig[sig.index < end + pd.Timedelta(days=2)]))
            rows.append({"setup": s.name(), **st, "cfg": s})
            print(f"  tested {len(rows):>2}/{total}  {s.name():<40} net ${st['net']:.2f}", flush=True)
    df = pd.DataFrame(rows).sort_values("net", ascending=False)
    few = df[df.trades < MIN_TRADES]
    df = df[df.trades >= MIN_TRADES]
    if len(few):
        print(f"\n({len(few)} setups had fewer than {MIN_TRADES} trades and are not ranked.)")
    print("\nTop 15:")
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 160):
        print(df[["setup", "trades", "win_rate", "breakeven_wr", "net", "profit_factor", "max_dd",
                  "pos_years"]].head(15).to_string(index=False))
    rnd = df[df.setup.str.startswith("coin_flip")]
    if len(rnd):
        print(f"\nBest RANDOM entry: {rnd.iloc[0].setup}, net ${rnd.iloc[0].net:.2f}. "
              f"A real signal must clearly beat this.")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bars")
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--signal")
    ap.add_argument("--tp", type=float)
    ap.add_argument("--sl", type=float)
    ap.add_argument("--trail", type=float)
    ap.add_argument("--hold", type=int)
    ap.add_argument("--min-spread", type=float)
    a = ap.parse_args()

    bars = load_bars(a.bars)
    base = M1Settings()
    src = load_swaps(base)
    print(f"Loaded {len(bars):,} M1 bars  {bars.index[0]:%Y-%m-%d} -> {bars.index[-1]:%Y-%m-%d}")
    print(f"Swap per lot per night: long ${base.swap_long:.2f}, short ${base.swap_short:.2f}  [{src}]")
    for field, val in (("signal", a.signal), ("tp", a.tp), ("sl", a.sl), ("trail", a.trail),
                       ("max_hold_min", a.hold), ("min_spread", a.min_spread)):
        if val is not None:
            setattr(base, field, val)

    if a.signal:
        print_report(run(bars, base), base, "Single test (all data)")
        return

    cut = bars.index[int(len(bars) * a.split)]
    print(f"\n--- Grid on the first {a.split:.0%}: up to {cut:%Y-%m-%d} ---")
    table = sweep(bars, base, cut)
    for rank, cfg in enumerate(table.head(3).cfg, 1):
        print_report(run(bars, cfg, start=cut), cfg, f"OUT-OF-SAMPLE #{rank} (data it never saw)")


if __name__ == "__main__":
    main()
