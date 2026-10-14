"""All settings in one place. Both the backtester and the live bot read this file."""

# --- Instrument -------------------------------------------------------------
SYMBOL = "XAUUSDm"         # your broker's gold name (others: "XAUUSD", "GOLD", "XAUUSD.r" ...)
CONTRACT_SIZE = 100        # ounces per 1.00 lot (standard for XAUUSD)
LOT = 0.01                 # 0.01 lot -> a $0.50 price move = $0.50 profit

# --- Exit rules -------------------------------------------------------------
TP_DIST = 0.50             # take profit: price must move +$0.50 in your favour (after spread). 0 = no TP
SL_DIST = 2.00             # stop loss distance in price. NEVER trade without one.
TRAIL_DIST = 0.0           # trailing stop distance in $ (0 = off). Follows price, never moves back.
MAX_HOLD_MINUTES = 60      # close at market if neither TP nor SL hit (0 = disabled)

# --- Entry rules ------------------------------------------------------------
# One of: "ema_pullback", "breakout", "rsi_reversion", "london_breakout", "ny_breakout", "coin_flip"
# london/ny_breakout use broker SERVER hours (Exness server time = GMT).
SIGNAL = "ema_pullback"
SIGNAL_PARAMS = {
    "ema_pullback": {"fast": 20, "slow": 50},
    "breakout": {"lookback": 15},
    "rsi_reversion": {"period": 14, "low": 30, "high": 70},
    "london_breakout": {},
    "ny_breakout": {},
    "coin_flip": {},
}

# --- Filters / risk guards (used by backtest AND live bot) ------------------
MAX_SPREAD = 0.35            # skip entries when ask-bid is wider than this ($)
TRADE_HOURS = range(1, 21)   # broker SERVER hours when entries are allowed (skips the ~21-01 rollover)
MIN_ATR_SPREAD = 0.0         # volatility filter: only enter if 1-min ATR >= this x current spread (0 = off)
MAX_TRADES_PER_DAY = 40
DAILY_LOSS_LIMIT = 10.0      # stop trading for the day after losing this many $ (account currency)
COMMISSION_PER_LOT = 0.0     # round-trip commission per 1.00 lot, $ (check your broker; ECN ~ $5-7)

# --- Trend backtest (trend_backtest.py) ------------------------------------
# Overnight swap per 1.00 lot per night in $ (negative = you pay). fetch_mt5_bars.py reads the
# real values from your broker into symbol_info.json, which overrides these.
SWAP_LONG_PER_LOT = -50.0
SWAP_SHORT_PER_LOT = -20.0
TREND_SPREAD = 0.30          # $ spread assumed per trade if the bar file has no spread column

# --- Live bot only ----------------------------------------------------------
DRY_RUN = True               # True = log what it WOULD do, send no orders
ALLOW_REAL_ACCOUNT = False   # bot refuses to run on a real-money account unless True
MAGIC = 50050                # id stamped on the bot's orders
DEVIATION_POINTS = 30        # max slippage accepted when opening
