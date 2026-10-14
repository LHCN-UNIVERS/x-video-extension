"""Analyse YOUR OWN past trades from MetaTrader 5 history (read-only: never places orders).

Shows whether your manual trading makes money, and which hours, hold times and sides
do well or badly, so you can keep what works and drop what loses.

  python analyze_my_trades.py               # last 180 days, gold symbols only
  python analyze_my_trades.py --days 365
  python analyze_my_trades.py --all-symbols

Log in to the account you actually trade on (demo or real) before running.
"""
import argparse
import itertools
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

DEAL_BUY, DEAL_SELL = 0, 1
ENTRY_IN = 0                    # all other entry kinds (OUT, INOUT, OUT_BY) close a position

HOLD_BUCKETS = [(0, 1, "< 1 min"), (1, 5, "1-5 min"), (5, 30, "5-30 min"),
                (30, 240, "30 min - 4 h"), (240, 1e9, "> 4 h")]


def build_trades(deals):
    """Turn MT5 deals into one row per closed position."""
    rows = {}
    for d in deals:
        if d.type not in (DEAL_BUY, DEAL_SELL) or not d.position_id:
            continue
        r = rows.setdefault(d.position_id, {"symbol": d.symbol, "open_time": None, "close_time": None,
                                            "side": None, "volume": 0.0, "open_price": None,
                                            "close_price": None, "profit": 0.0, "costs": 0.0,
                                            "magic": d.magic})
        r["profit"] += d.profit
        r["costs"] += d.commission + d.swap + getattr(d, "fee", 0.0)
        t = datetime.fromtimestamp(d.time, timezone.utc).replace(tzinfo=None)
        if d.entry == ENTRY_IN:
            r["open_time"], r["open_price"] = t, d.price
            r["side"] = "BUY" if d.type == DEAL_BUY else "SELL"
            r["volume"] += d.volume
        else:
            r["close_time"], r["close_price"] = t, d.price
    df = pd.DataFrame([{"position": k, **v} for k, v in rows.items()])
    if df.empty:
        return df
    df = df.dropna(subset=["open_time", "close_time"]).copy()       # only fully closed trades
    df["net"] = df.profit + df.costs
    df["hold_min"] = (df.close_time - df.open_time).dt.total_seconds() / 60
    # result scaled to 0.01 lot, so trades of different sizes compare fairly
    df["net_per_001"] = df.net / (df.volume / 0.01)
    return df.sort_values("close_time").reset_index(drop=True)


def summary(df):
    w, l = df[df.net > 0].net, df[df.net <= 0].net
    eq = df.net.cumsum()
    avg_w, avg_l = (w.mean() if len(w) else 0), (-l.mean() if len(l) else 0)
    streak = max((len(list(g)) for k, g in itertools.groupby(df.net <= 0) if k), default=0)
    return {
        "trades": len(df), "win_rate": len(w) / len(df) if len(df) else 0,
        "net": df.net.sum(), "avg_win": avg_w, "avg_loss": avg_l,
        "breakeven_wr": avg_l / (avg_w + avg_l) if (avg_w + avg_l) else 0,
        "profit_factor": w.sum() / -l.sum() if l.sum() < 0 else float("inf"),
        "max_dd": (eq.cummax().clip(lower=0) - eq).max() if len(df) else 0,
        "costs": df.costs.sum(), "worst_streak": streak,
    }


def group_table(df, key, order=None):
    g = df.groupby(key, observed=False)
    t = pd.DataFrame({"trades": g.size(), "win_rate": g.net.apply(lambda x: (x > 0).mean()),
                      "net": g.net.sum(), "net_per_0.01lot": g.net_per_001.sum()})
    if order is not None:
        t = t.reindex([o for o in order if o in t.index])
    return t


def report(df):
    s = summary(df)
    print(f"\n=== YOUR TRADES: {df.open_time.min():%Y-%m-%d} -> {df.close_time.max():%Y-%m-%d} ===")
    print(f"trades          {s['trades']}   (symbols: {', '.join(sorted(df.symbol.unique()))})")
    print(f"win rate        {s['win_rate']:.1%}   (needed to break even: {s['breakeven_wr']:.1%})")
    print(f"avg win / loss  ${s['avg_win']:.2f} / -${s['avg_loss']:.2f}")
    print(f"NET RESULT      ${s['net']:.2f}   (commission+swap+fees: ${s['costs']:.2f})")
    print(f"profit factor   {s['profit_factor']:.2f}   (above 1.0 = profitable)")
    print(f"max drawdown    ${s['max_dd']:.2f}   worst losing streak: {s['worst_streak']} trades")
    lots = df.volume.value_counts().sort_index()
    print("lot sizes used  " + ", ".join(f"{v:g} x{n}" for v, n in lots.items()))

    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 120):
        print("\nBy hour opened (server time):")
        print(group_table(df, df.open_time.dt.hour.rename("hour")).to_string())
        labels = [b[2] for b in HOLD_BUCKETS]
        bucket = pd.cut(df.hold_min, [b[0] for b in HOLD_BUCKETS] + [1e12], labels=labels, right=False)
        print("\nBy how long the trade was held:")
        print(group_table(df, bucket.rename("held"), labels).to_string())
        print("\nBy side:")
        print(group_table(df, "side").to_string())
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        print("\nBy weekday:")
        print(group_table(df, df.open_time.dt.dayofweek.map(dict(enumerate(days))).rename("day"),
                          days).to_string())
        print("\nBy month:")
        print(group_table(df, df.close_time.dt.to_period("M").rename("month")).to_string())

    big_l = df.nsmallest(min(5, len(df)), "net")
    share = -big_l.net.sum() / max(-df[df.net < 0].net.sum(), 1e-9)
    print(f"\nYour 5 biggest losses cost ${-big_l.net.sum():.2f} = {share:.0%} of all your losses.")
    if s["trades"] < 50:
        print("NOTE: fewer than 50 trades - patterns above may be chance. Use --days 365 for more.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--all-symbols", action="store_true")
    ap.add_argument("--out", default="my_trades.csv")
    a = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()} (is the terminal open and logged in?)")
    acc = mt5.account_info()
    print(f"Account {acc.login} ({'DEMO' if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else 'REAL'})  "
          f"balance {acc.balance:.2f} {acc.currency}")
    end = datetime.now(timezone.utc) + timedelta(days=1)
    deals = mt5.history_deals_get(end - timedelta(days=a.days + 1), end) or ()
    mt5.shutdown()

    df = build_trades(deals)
    if not a.all_symbols and not df.empty:
        df = df[df.symbol.str.upper().str.contains("XAU|GOLD")]
    if df.empty:
        raise SystemExit("No closed trades found. Log in to the account you trade on, "
                         "or try --days 365 / --all-symbols.")
    report(df)
    df.to_csv(a.out, index=False)
    print(f"\nTrade list saved to {a.out}")


if __name__ == "__main__":
    main()
