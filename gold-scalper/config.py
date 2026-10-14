"""All settings in one place. Both the backtester and the live bot read this file."""

# --- Instrument -------------------------------------------------------------
SYMBOL = "XAUUSD"          # your broker may call it "GOLD", "XAUUSDm", "XAUUSD.r" ...
CONTRACT_SIZE = 100        # ounces per 1.00 lot (standard for XAUUSD)
LOT = 0.01                 # 0.01 lot -> a $0.50 price move = $0.50 profit

# --- Exit rules -------------------------------------------------------------
TP_DIST = 0.50             # take profit: price must move +$0.50 in your favour (after spread)
SL_DIST = 2.00             # stop loss distance in price. NEVER trade without one.
MAX_HOLD_MINUTES = 60      # close at market if neither TP nor SL hit (0 = disabled)

# --- Entry rules ------------------------------------------------------------
# One of: "ema_pullback", "breakout", "rsi_reversion", "coin_flip"
SIGNAL = "ema_pullback"
SIGNAL_PARAMS = {
    "ema_pullback": {"fast": 20, "slow": 50},
    "breakout": {"lookback": 15},
    "rsi_reversion": {"period": 14, "low": 30, "high": 70},
    "coin_flip": {},
}

# --- Filters / risk guards (used by backtest AND live bot) ------------------
MAX_SPREAD = 0.35            # skip entries when ask-bid is wider than this ($)
TRADE_HOURS = range(8, 21)   # broker SERVER hours when entries are allowed
MAX_TRADES_PER_DAY = 40
DAILY_LOSS_LIMIT = 10.0      # stop trading for the day after losing this many $ (account currency)
COMMISSION_PER_LOT = 0.0     # round-trip commission per 1.00 lot, $ (check your broker; ECN ~ $5-7)

# --- Live bot only ----------------------------------------------------------
DRY_RUN = True               # True = log what it WOULD do, send no orders
ALLOW_REAL_ACCOUNT = False   # bot refuses to run on a real-money account unless True
MAGIC = 50050                # id stamped on the bot's orders
DEVIATION_POINTS = 30        # max slippage accepted when opening
