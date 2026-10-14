"""Entry signals computed on M1 bid bars.

Every function takes a DataFrame with columns open/high/low/close (one row per
COMPLETED bar) and returns a Series aligned to it: +1 = buy, -1 = sell, 0 = nothing.
The value on bar t only uses data up to the close of bar t, so it can be acted
on at the first tick after that bar closes (no look-ahead).
"""
import numpy as np
import pandas as pd


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def ema_pullback(bars, fast=20, slow=50):
    """Trade with the trend after a dip to the fast EMA.

    Buy: fast EMA above slow EMA and rising, the bar dipped to the fast EMA and
    closed back above it. Sell is the mirror image.
    """
    c = bars["close"]
    ef, es = _ema(c, fast), _ema(c, slow)
    up = (ef > es) & (es > es.shift(5))
    dn = (ef < es) & (es < es.shift(5))
    buy = up & (bars["low"] <= ef) & (c > ef)
    sell = dn & (bars["high"] >= ef) & (c < ef)
    return _combine(buy, sell, bars.index, warmup=slow)


def breakout(bars, lookback=15):
    """Momentum: close breaks the high/low of the previous `lookback` bars."""
    c = bars["close"]
    hh = bars["high"].shift(1).rolling(lookback).max()
    ll = bars["low"].shift(1).rolling(lookback).min()
    return _combine(c > hh, c < ll, bars.index, warmup=lookback + 1)


def rsi_reversion(bars, period=14, low=30, high=70):
    """Mean reversion: RSI crosses back up through `low` (buy) or down through `high` (sell)."""
    d = bars["close"].diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    buy = (rsi.shift(1) < low) & (rsi >= low)
    sell = (rsi.shift(1) > high) & (rsi <= high)
    return _combine(buy, sell, bars.index, warmup=period + 1)


def session_breakout(bars, range_start=0, range_end=7, trade_until=10):
    """Opening-range breakout (the idea behind the repo's London Breakout).

    Builds the high/low of each day between `range_start` and `range_end`
    (broker server hours), then takes the FIRST close outside that range
    between `range_end` and `trade_until`. One signal per day at most.
    """
    idx = pd.DatetimeIndex(bars.index)
    day, hour = idx.normalize(), idx.hour
    in_range = pd.Series((hour >= range_start) & (hour < range_end), index=bars.index)
    in_trade = pd.Series((hour >= range_end) & (hour < trade_until), index=bars.index)
    hi = bars["high"].where(in_range).groupby(day).cummax().groupby(day).ffill()
    lo = bars["low"].where(in_range).groupby(day).cummin().groupby(day).ffill()
    c = bars["close"]
    buy = in_trade & (c > hi)
    sell = in_trade & (c < lo)
    first = (buy | sell).astype(int).groupby(day).cumsum() == 1
    return _combine(buy & first, sell & first, bars.index, warmup=0)


def london_breakout(bars):
    """Range = Asian session 00:00-07:00, trade the London open 07:00-10:00 (server time)."""
    return session_breakout(bars, 0, 7, 10)


def ny_breakout(bars):
    """Range = 10:00-13:00, trade the US data / New York open 13:00-16:00 (server time)."""
    return session_breakout(bars, 10, 13, 16)


def atr(bars, period=14):
    """Average true range in $, per bar."""
    prev = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(),
                    (bars["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def coin_flip(bars):
    """Baseline with no edge: buy after an up bar, sell after a down bar, every bar.
    Any real signal has to beat this."""
    d = np.sign(bars["close"] - bars["open"]).fillna(0).astype(int)
    return pd.Series(d.values, index=bars.index)


def _combine(buy, sell, index, warmup):
    sig = pd.Series(0, index=index, dtype=int)
    sig[buy.fillna(False).values] = 1
    sig[sell.fillna(False).values] = -1
    sig.iloc[:warmup] = 0
    return sig


SIGNALS = {
    "ema_pullback": ema_pullback,
    "breakout": breakout,
    "rsi_reversion": rsi_reversion,
    "london_breakout": london_breakout,
    "ny_breakout": ny_breakout,
    "coin_flip": coin_flip,
}


def compute(name, bars, params=None):
    return SIGNALS[name](bars, **(params or {}))
