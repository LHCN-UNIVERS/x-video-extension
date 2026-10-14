"""Download real XAUUSD bid/ask ticks from YOUR broker through MetaTrader 5.

Requires Windows + the MT5 terminal installed and logged in (demo is fine).
  python fetch_mt5_data.py --days 30            -> ticks.csv
"""
import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
import MetaTrader5 as mt5

import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbol", default=config.SYMBOL)
    ap.add_argument("--out", default="ticks.csv")
    a = ap.parse_args()

    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}  (is the terminal open and logged in?)")
    if not mt5.symbol_select(a.symbol, True):
        raise SystemExit(f"Symbol {a.symbol} not found. Check the name in Market Watch and edit config.SYMBOL.")

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=a.days + 1)
    parts, day = [], start
    while day < end:   # one day at a time keeps memory use low
        nxt = day + timedelta(days=1)
        ticks = mt5.copy_ticks_range(a.symbol, day, nxt, mt5.COPY_TICKS_INFO)
        if ticks is not None and len(ticks):
            parts.append(pd.DataFrame(ticks)[["time_msc", "bid", "ask"]])
            print(f"{day:%Y-%m-%d}: {len(ticks):,} ticks")
        day = nxt
    mt5.shutdown()

    if not parts:
        raise SystemExit("No ticks returned. Open the XAUUSD chart in MT5 once, then retry.")
    df = pd.concat(parts, ignore_index=True)
    df.to_csv(a.out, index=False)
    print(f"Saved {len(df):,} ticks to {a.out}")


if __name__ == "__main__":
    main()
