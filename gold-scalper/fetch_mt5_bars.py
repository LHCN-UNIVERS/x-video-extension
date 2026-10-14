"""Download years of XAUUSD daily and 4-hour bars from your broker through MetaTrader 5.

  python fetch_mt5_bars.py                 -> bars_D1.csv and bars_H4.csv (10 years if available)
  python fetch_mt5_bars.py --years 5

Tip: if you get few bars, open a D1 and an H4 gold chart in MT5 and press Home a few times
to scroll back, so the terminal downloads more history, then run this again.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import MetaTrader5 as mt5

import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--symbol", default=config.SYMBOL)
    a = ap.parse_args()

    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}  (is the terminal open and logged in?)")
    if not mt5.symbol_select(a.symbol, True):
        raise SystemExit(f"Symbol {a.symbol} not found. Check config.SYMBOL.")
    info = mt5.symbol_info(a.symbol)
    print(f"{a.symbol}: point={info.point}  digits={info.digits}  "
          f"swap long={info.swap_long}  swap short={info.swap_short}  (swap mode {info.swap_mode})")

    swap = swap_per_lot_usd(info)
    with open("symbol_info.json", "w") as f:
        json.dump({"symbol": a.symbol, "point": info.point, "contract_size": info.trade_contract_size,
                   "swap_long_per_lot": swap[0], "swap_short_per_lot": swap[1]}, f, indent=1)
    if swap[0] is None:
        print("Could not convert this broker's swap to $; set SWAP_LONG/SHORT_PER_LOT in config.py by hand.")
    else:
        print(f"Overnight swap per 1.00 lot: long ${swap[0]:.2f}, short ${swap[1]:.2f} -> symbol_info.json")

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * a.years)
    for name, tf in (("D1", mt5.TIMEFRAME_D1), ("H4", mt5.TIMEFRAME_H4)):
        rates = mt5.copy_rates_range(a.symbol, tf, start, end)
        if rates is None or not len(rates):
            print(f"{name}: no bars returned ({mt5.last_error()})")
            continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df["spread_usd"] = df["spread"] * info.point      # bar's spread in points -> $
        df[["time", "open", "high", "low", "close", "spread_usd"]].to_csv(f"bars_{name}.csv", index=False)
        print(f"{name}: {len(df):,} bars  {df.time.iloc[0]:%Y-%m-%d} -> {df.time.iloc[-1]:%Y-%m-%d}"
              f"  -> bars_{name}.csv")
    mt5.shutdown()


def swap_per_lot_usd(info):
    """Overnight swap in $ per 1.00 lot, for the common swap modes. (None, None) if unknown."""
    mode = info.swap_mode
    if mode == 0:                       # swap-free
        return 0.0, 0.0
    if mode == 1:                       # in points
        k = info.point * info.trade_contract_size
        return info.swap_long * k, info.swap_short * k
    if mode in (2, 3, 4):               # already money per lot (assumes a USD account)
        return float(info.swap_long), float(info.swap_short)
    return None, None


if __name__ == "__main__":
    main()
