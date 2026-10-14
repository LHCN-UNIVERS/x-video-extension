"""Generate FAKE gold ticks (random walk, realistic volatility and spread).

Only for checking that the code runs. A random walk has no edge by construction,
so results on this data show the pure cost of the spread - nothing more.
  python make_synthetic_ticks.py --days 10 --out synthetic_ticks.csv.gz
"""
import argparse

import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--start-price", type=float, default=2650.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="synthetic_ticks.csv.gz")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    frames, price = [], a.start_price
    for d in pd.bdate_range("2026-01-05", periods=a.days):
        secs = np.arange(1, 23 * 3600)                 # 23h session, ~1 tick/sec
        secs = secs[rng.random(len(secs)) < 0.8]
        hour = secs / 3600
        vol = 0.06 + 0.08 * np.exp(-((hour - 15.5) ** 2) / 4) + 0.04 * np.exp(-((hour - 10) ** 2) / 2)
        steps = rng.standard_normal(len(secs)) * vol
        steps[rng.random(len(secs)) < 0.0005] *= 15      # occasional news spikes
        mid = price + np.cumsum(steps)
        price = mid[-1]
        spread = np.round(rng.uniform(0.15, 0.40, len(secs)) + 0.3 * (vol > 0.12) * rng.random(len(secs)), 2)
        t = d + pd.to_timedelta(secs, unit="s")
        bid = np.round(mid - spread / 2, 2)
        frames.append(pd.DataFrame({"time_msc": t.values.astype("datetime64[ms]").astype(np.int64),
                                    "bid": bid, "ask": np.round(bid + spread, 2)}))
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(a.out, index=False)
    print(f"Wrote {len(df):,} synthetic ticks to {a.out}")


if __name__ == "__main__":
    main()
