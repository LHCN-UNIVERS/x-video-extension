"""Trend-following backtest on daily (D1) or 4-hour (H4) gold bars.

Idea: hold for days or weeks so the spread becomes a tiny part of each trade.
Costs included: spread on entry and exit, slippage on stop gaps, and the overnight swap
your broker charges (read from symbol_info.json written by fetch_mt5_bars.py).

Strategies (all decided at a bar's close, executed at the next bar's open):
  donchian  buy a close above the highest high of N bars, exit below the lowest low of M bars
  sma       be long above the N-bar average, short (or flat) below it
  tsmom     time-series momentum: direction of the last N-bar return

Usage:
  python trend_backtest.py bars_D1.csv                    # big grid on the first 70%, top 3 on the last 30%
  python trend_backtest.py bars_H4.csv --split 0.6
  python trend_backtest.py bars_D1.csv --only donchian:55:20 --long-only --atr-stop 3
"""
import argparse
import json
import os
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config
import signals


@dataclass
class TrendSettings:
    strategy: str = "donchian"
    p1: int = 55
    p2: int = 20
    long_only: bool = True
    atr_stop: float = 0.0          # initial stop = this x ATR(20) from entry (0 = none)
    lot: float = config.LOT
    contract: float = config.CONTRACT_SIZE
    min_spread: float = config.TREND_SPREAD
    swap_long: float = config.SWAP_LONG_PER_LOT
    swap_short: float = config.SWAP_SHORT_PER_LOT
    commission: float = config.COMMISSION_PER_LOT

    def name(self):
        p = f"{self.p1}" + (f"/{self.p2}" if self.strategy == "donchian" else "")
        stop = f" stop={self.atr_stop:g}xATR" if self.atr_stop else ""
        return f"{self.strategy}({p}) {'long-only' if self.long_only else 'long+short'}{stop}"


GRID = (
    [("donchian", a, b) for a, b in ((20, 10), (55, 20), (100, 50))]
    + [("sma", n, 0) for n in (50, 100, 200)]
    + [("tsmom", n, 0) for n in (20, 60, 120, 250)]
)


# --------------------------------------------------------------------------- data

def load_bars(path):
    df = pd.read_csv(path, parse_dates=["time"]).set_index("time").sort_index()
    if "spread_usd" not in df:
        df["spread_usd"] = 0.0
    return df


def load_swaps(s: TrendSettings):
    if os.path.exists("symbol_info.json"):
        info = json.load(open("symbol_info.json"))
        if info.get("swap_long_per_lot") is not None:
            s.swap_long, s.swap_short = info["swap_long_per_lot"], info["swap_short_per_lot"]
            return "symbol_info.json (your broker)"
    return "config.py defaults (run fetch_mt5_bars.py to use your broker's real swap)"


# --------------------------------------------------------------------------- strategies

def target_position(bars, s: TrendSettings):
    """+1 / 0 / -1 wanted at the close of each bar."""
    c = bars["close"]
    if s.strategy == "donchian":
        hi_in = bars["high"].shift(1).rolling(s.p1).max()
        lo_in = bars["low"].shift(1).rolling(s.p1).min()
        hi_out = bars["high"].shift(1).rolling(s.p2).max()
        lo_out = bars["low"].shift(1).rolling(s.p2).min()
        pos, out = 0, np.zeros(len(c), int)
        for i in range(len(c)):
            if pos == 1 and c.iloc[i] < lo_out.iloc[i]:
                pos = 0
            elif pos == -1 and c.iloc[i] > hi_out.iloc[i]:
                pos = 0
            if c.iloc[i] > hi_in.iloc[i]:
                pos = 1
            elif c.iloc[i] < lo_in.iloc[i]:
                pos = -1
            out[i] = pos
        t = pd.Series(out, index=c.index)
    elif s.strategy == "sma":
        m = c.rolling(s.p1).mean()
        t = pd.Series(np.where(c > m, 1, -1), index=c.index).where(m.notna(), 0)
    elif s.strategy == "tsmom":
        r = c / c.shift(s.p1) - 1
        t = pd.Series(np.sign(r), index=c.index).fillna(0).astype(int)
    elif s.strategy == "buy_hold":
        t = pd.Series(1, index=c.index)
    else:
        raise ValueError(s.strategy)
    if s.long_only:
        t = t.clip(lower=0)
    return t.astype(int)


# --------------------------------------------------------------------------- engine

def run(bars, s: TrendSettings, start=None):
    """Bars hold BID prices; buys pay the spread on entry, sells pay it on exit.

    Indicators use all bars; trades are only taken from `start` on (so an out-of-sample
    window doesn't lose its first months to indicator warm-up)."""
    target = target_position(bars, s).values
    o, h, l, c = (bars[k].values for k in ("open", "high", "low", "close"))
    spr = np.maximum(bars["spread_usd"].values, s.min_spread)
    atr = signals.atr(bars, 20).values
    times = bars.index
    trades = []
    pos, entry, stop, entry_i, blocked = 0, 0.0, None, 0, 0

    def close(i, px, reason):
        nights = (times[i].normalize() - times[entry_i].normalize()).days
        swap = (s.swap_long if pos > 0 else s.swap_short) * s.lot * nights
        price_pnl = pos * (px - entry) * s.contract * s.lot
        trades.append({"entry_time": times[entry_i], "exit_time": times[i],
                       "side": "BUY" if pos > 0 else "SELL", "entry": entry, "exit": px,
                       "nights": nights, "price_pnl": price_pnl, "swap": swap,
                       "spread_cost": spr[entry_i] * s.contract * s.lot,
                       "pnl": price_pnl + swap - s.commission * s.lot, "reason": reason})

    first = max(1, int(np.searchsorted(times, start))) if start is not None else 1
    equity = np.zeros(len(c))
    for i in range(first, len(c)):
        want = target[i - 1]
        if want != blocked:
            blocked = 0
        # 1) act at the open on yesterday's decision
        if pos and want != pos:
            close(i, o[i] if pos > 0 else o[i] + spr[i], "SIGNAL")
            pos = 0
        if not pos and want and want != blocked:
            pos, entry_i = want, i
            entry = o[i] + spr[i] if want > 0 else o[i]
            a = atr[i - 1]
            stop = (entry - pos * s.atr_stop * a) if (s.atr_stop and a == a) else None
        # 2) stop loss inside the bar (gap through the stop fills at the open)
        if pos and stop is not None:
            if pos > 0 and l[i] <= stop:
                close(i, min(o[i], stop) if i > entry_i else stop, "STOP")
                pos, blocked = 0, 1
            elif pos < 0 and h[i] + spr[i] >= stop:
                close(i, max(o[i] + spr[i], stop) if i > entry_i else stop, "STOP")
                pos, blocked = 0, -1
        # 3) mark-to-market equity at the close (closed P&L + open trade incl. swap so far)
        realised = sum(t["pnl"] for t in trades)
        if pos:
            nights = (times[i].normalize() - times[entry_i].normalize()).days
            exit_px = c[i] if pos > 0 else c[i] + spr[i]
            realised += (pos * (exit_px - entry) * s.contract * s.lot
                         + (s.swap_long if pos > 0 else s.swap_short) * s.lot * nights)
        equity[i] = realised
    if pos:
        close(len(c) - 1, c[-1] if pos > 0 else c[-1] + spr[-1], "END")
    df = pd.DataFrame(trades)
    df.attrs["equity"] = pd.Series(equity[first:], index=times[first:])
    return df


# --------------------------------------------------------------------------- report

def stats(tr, bars):
    years = max((bars.index[-1] - bars.index[0]).days / 365.25, 1e-9)
    if tr.empty:
        return {"trades": 0, "win_rate": 0, "net": 0, "per_year": 0, "profit_factor": 0,
                "max_dd": 0, "swap": 0, "spread": 0}
    w, l = tr[tr.pnl > 0].pnl, tr[tr.pnl <= 0].pnl
    eq = tr.attrs.get("equity", tr.pnl.cumsum())          # daily mark-to-market if available
    return {"trades": len(tr), "win_rate": len(w) / len(tr), "net": tr.pnl.sum(),
            "per_year": tr.pnl.sum() / years,
            "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else float("inf"),
            "max_dd": (eq.cummax().clip(lower=0) - eq).max(),
            "swap": tr.swap.sum(), "spread": tr.spread_cost.sum()}


def print_report(bars, s, title, start=None):
    tr = run(bars, s, start)
    bh = run(bars, TrendSettings(**{**asdict(s), "strategy": "buy_hold", "atr_stop": 0.0,
                                    "long_only": True}), start)
    if start is not None:
        bars = bars[bars.index >= start]
    st, bh_st = stats(tr, bars), stats(bh, bars)
    print(f"\n=== {title} ===")
    print(f"{s.name()}   lot={s.lot}   {bars.index[0]:%Y-%m-%d} -> {bars.index[-1]:%Y-%m-%d}")
    if not st["trades"]:
        print("No trades.")
        return
    print(f"trades          {st['trades']}   win rate {st['win_rate']:.1%}")
    print(f"avg win / loss  ${tr[tr.pnl > 0].pnl.mean():.2f} / ${tr[tr.pnl <= 0].pnl.mean():.2f}")
    print(f"net profit      ${st['net']:.2f}   (${st['per_year']:.2f}/year)")
    print(f"  of which      swap ${st['swap']:.2f}, spread paid ~${st['spread']:.2f}")
    print(f"profit factor   {st['profit_factor']:.2f}")
    print(f"max drawdown    ${st['max_dd']:.2f}  (includes open-trade losses)")
    if st["trades"] < 30:
        print(f"WARNING: only {st['trades']} trades - too few to trust on their own. "
              f"Check it also worked on the other timeframe (D1 vs H4).")
    print(f"buy & hold      ${bh_st['net']:.2f} same lot, same period (incl. swap ${bh_st['swap']:.2f}), "
          f"max drawdown ${bh_st['max_dd']:.2f}")
    yearly = tr.groupby(tr.exit_time.dt.year).pnl.sum()
    print("net by year     " + "  ".join(f"{y}: ${v:.2f}" for y, v in yearly.items()))


def sweep(bars, base: TrendSettings, top=10):
    rows = []
    for strat, p1, p2 in GRID:
        for long_only in (True, False):
            for atr_stop in (0.0, 3.0):
                s = TrendSettings(**{**asdict(base), "strategy": strat, "p1": p1, "p2": p2,
                                     "long_only": long_only, "atr_stop": atr_stop})
                rows.append({"setup": s.name(), **stats(run(bars, s), bars), "cfg": s})
    df = pd.DataFrame(rows)
    df = df[df.trades > 0].sort_values("net", ascending=False)
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 160):
        print(df[["setup", "trades", "win_rate", "net", "per_year", "swap", "profit_factor",
                  "max_dd"]].head(top).to_string(index=False))
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bars")
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--only", help="test one setup, e.g. donchian:55:20 or sma:200 or tsmom:120")
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--atr-stop", type=float, default=0.0)
    a = ap.parse_args()

    bars = load_bars(a.bars)
    base = TrendSettings()
    src = load_swaps(base)
    print(f"Loaded {len(bars):,} bars  {bars.index[0]:%Y-%m-%d} -> {bars.index[-1]:%Y-%m-%d}")
    print(f"Swap per lot per night: long ${base.swap_long:.2f}, short ${base.swap_short:.2f}  [{src}]")

    if a.only:
        parts = a.only.split(":")
        base.strategy, base.p1 = parts[0], int(parts[1])
        base.p2 = int(parts[2]) if len(parts) > 2 else 0
        base.long_only, base.atr_stop = a.long_only, a.atr_stop
        print_report(bars, base, "Single test (all data)")
        return

    cut = bars.index[int(len(bars) * a.split)]
    ins = bars[bars.index < cut]
    print(f"\n--- Grid on the first {a.split:.0%}: {ins.index[0]:%Y-%m-%d} -> {ins.index[-1]:%Y-%m-%d} ---")
    table = sweep(ins, base)
    for rank, cfg in enumerate(table.head(3).cfg, 1):
        print_report(bars, cfg, f"OUT-OF-SAMPLE #{rank} (data it never saw)", start=cut)


if __name__ == "__main__":
    main()
