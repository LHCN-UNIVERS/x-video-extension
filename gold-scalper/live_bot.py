"""Live XAUUSD scalper for MetaTrader 5: opens on a signal, closes at +$0.50.

The take profit and stop loss are placed ON THE BROKER'S SERVER with the order,
so the trade still closes at +$0.50 (or at the stop) even if this script or
your PC stops.

Safety defaults (change in config.py):
  DRY_RUN = True              -> only logs what it would do
  ALLOW_REAL_ACCOUNT = False  -> refuses to trade a real-money account

Run:  python live_bot.py        (Ctrl+C to stop)
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
import signals

log = logging.getLogger("gold-bot")

FILLING_FOK, FILLING_IOC = 1, 2   # bits of symbol_info().filling_mode


class Bot:
    def __init__(self, mt5):
        self.mt5 = mt5
        self.last_bar = None

    # ------------------------------------------------------------------ setup
    def start(self):
        m = self.mt5
        if not m.initialize():
            raise SystemExit(f"MT5 initialize failed: {m.last_error()} (is the terminal open and logged in?)")
        acc = m.account_info()
        is_demo = acc.trade_mode == m.ACCOUNT_TRADE_MODE_DEMO
        log.info("Account %s  %s  balance=%.2f %s", acc.login, "DEMO" if is_demo else "REAL",
                 acc.balance, acc.currency)
        if not is_demo and not config.ALLOW_REAL_ACCOUNT and not config.DRY_RUN:
            raise SystemExit("Real-money account detected. Set ALLOW_REAL_ACCOUNT = True in config.py "
                             "only after testing on demo.")
        if not m.symbol_select(config.SYMBOL, True):
            raise SystemExit(f"Symbol {config.SYMBOL} not found - check its name in Market Watch.")
        info = m.symbol_info(config.SYMBOL)
        self.digits, self.point = info.digits, info.point
        self.filling = (m.ORDER_FILLING_IOC if info.filling_mode & FILLING_IOC else
                        m.ORDER_FILLING_FOK if info.filling_mode & FILLING_FOK else
                        m.ORDER_FILLING_RETURN)
        min_stop = info.trade_stops_level * info.point
        if config.TP_DIST < min_stop:
            raise SystemExit(f"Broker minimum stop distance is {min_stop:.2f}; "
                             f"TP_DIST={config.TP_DIST} is too close. This broker can't do +0.50 TPs.")
        if info.volume_min > config.LOT:
            raise SystemExit(f"Minimum lot for {config.SYMBOL} is {info.volume_min}")
        log.info("Symbol %s  digits=%d  contract=%s  stops_level=%.2f  signal=%s  TP=+%.2f SL=-%.2f  %s",
                 config.SYMBOL, info.digits, info.trade_contract_size, min_stop, config.SIGNAL,
                 config.TP_DIST, config.SL_DIST, "DRY RUN" if config.DRY_RUN else "LIVE ORDERS")

    # ------------------------------------------------------------------ loop
    def step(self):
        """One pass: manage open trade, then check for a new completed bar."""
        m = self.mt5
        tick = m.symbol_info_tick(config.SYMBOL)
        if tick is None:
            return
        now = datetime.fromtimestamp(tick.time, timezone.utc)   # broker server time
        self.close_stale_positions(tick, now)

        rates = m.copy_rates_from_pos(config.SYMBOL, m.TIMEFRAME_M1, 0, 300)
        if rates is None or len(rates) < 60:
            return
        bars = pd.DataFrame(rates)[:-1]          # drop the bar still forming
        bar_time = int(bars["time"].iloc[-1])
        if bar_time == self.last_bar:
            return
        self.last_bar = bar_time

        sig = signals.compute(config.SIGNAL, bars[["open", "high", "low", "close"]],
                              config.SIGNAL_PARAMS.get(config.SIGNAL, {}))
        direction = int(sig.iloc[-1])
        if direction == 0:
            return
        side = "BUY" if direction > 0 else "SELL"
        reason = self.blocked(tick, now)
        if reason:
            log.info("%s signal skipped: %s", side, reason)
            return
        self.open_trade(direction, tick)

    def blocked(self, tick, now):
        if self.my_positions():
            return "a trade is already open"
        spread = tick.ask - tick.bid
        if spread > config.MAX_SPREAD:
            return f"spread {spread:.2f} > {config.MAX_SPREAD}"
        if now.hour not in config.TRADE_HOURS:
            return f"outside trading hours (server hour {now.hour})"
        trades, pnl = self.today_stats(now)
        if trades >= config.MAX_TRADES_PER_DAY:
            return f"max trades for today reached ({trades})"
        if pnl <= -config.DAILY_LOSS_LIMIT:
            return f"daily loss limit hit ({pnl:.2f})"
        return None

    # ------------------------------------------------------------------ orders
    def open_trade(self, direction, tick):
        m = self.mt5
        buy = direction > 0
        price = tick.ask if buy else tick.bid
        tp, sl = self.levels(price, buy)
        req = {
            "action": m.TRADE_ACTION_DEAL, "symbol": config.SYMBOL, "volume": config.LOT,
            "type": m.ORDER_TYPE_BUY if buy else m.ORDER_TYPE_SELL,
            "price": price, "tp": tp, "sl": sl, "deviation": config.DEVIATION_POINTS,
            "magic": config.MAGIC, "comment": "gold +0.5", "type_time": m.ORDER_TIME_GTC,
            "type_filling": self.filling,
        }
        side = "BUY" if buy else "SELL"
        if config.DRY_RUN:
            log.info("[DRY RUN] would %s %.2f lot @ %.2f  TP %.2f  SL %.2f  (spread %.2f)",
                     side, config.LOT, price, tp, sl, tick.ask - tick.bid)
            return
        res = m.order_send(req)
        if res is None or res.retcode != m.TRADE_RETCODE_DONE:
            log.warning("%s failed: %s %s", side, getattr(res, "retcode", None),
                        getattr(res, "comment", m.last_error()))
            return
        log.info("OPENED %s %.2f lot @ %.2f  TP %.2f  SL %.2f  ticket=%s",
                 side, res.volume, res.price, tp, sl, res.order)
        self.realign_stops(buy)

    def realign_stops(self, buy):
        """If the fill slipped, move TP/SL so the target is exactly +TP_DIST from the real fill."""
        m = self.mt5
        for p in self.my_positions():
            tp, sl = self.levels(p.price_open, buy)
            if abs(p.tp - tp) < self.point / 2 and abs(p.sl - sl) < self.point / 2:
                continue
            res = m.order_send({"action": m.TRADE_ACTION_SLTP, "symbol": config.SYMBOL,
                                "position": p.ticket, "tp": tp, "sl": sl, "magic": config.MAGIC})
            if res is not None and res.retcode == m.TRADE_RETCODE_DONE:
                log.info("Adjusted stops to fill %.2f -> TP %.2f SL %.2f", p.price_open, tp, sl)

    def close_stale_positions(self, tick, now):
        if not config.MAX_HOLD_MINUTES:
            return
        m = self.mt5
        for p in self.my_positions():
            age = now - datetime.fromtimestamp(p.time, timezone.utc)
            if age < timedelta(minutes=config.MAX_HOLD_MINUTES):
                continue
            long_ = p.type == m.POSITION_TYPE_BUY
            if config.DRY_RUN:
                log.info("[DRY RUN] would close ticket %s after %s", p.ticket, age)
                continue
            res = m.order_send({
                "action": m.TRADE_ACTION_DEAL, "symbol": config.SYMBOL, "volume": p.volume,
                "type": m.ORDER_TYPE_SELL if long_ else m.ORDER_TYPE_BUY, "position": p.ticket,
                "price": tick.bid if long_ else tick.ask, "deviation": config.DEVIATION_POINTS,
                "magic": config.MAGIC, "comment": "time exit", "type_time": m.ORDER_TIME_GTC,
                "type_filling": self.filling})
            ok = res is not None and res.retcode == m.TRADE_RETCODE_DONE
            log.info("Time exit ticket %s after %s: %s", p.ticket, age, "done" if ok else res)

    # ------------------------------------------------------------------ helpers
    def levels(self, price, buy):
        tp = price + config.TP_DIST if buy else price - config.TP_DIST
        sl = price - config.SL_DIST if buy else price + config.SL_DIST
        return round(tp, self.digits), round(sl, self.digits)

    def my_positions(self):
        ps = self.mt5.positions_get(symbol=config.SYMBOL) or ()
        return [p for p in ps if p.magic == config.MAGIC]

    def today_stats(self, now):
        """Trades opened and realised P&L (incl. commission & swap) since server midnight."""
        m = self.mt5
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = m.history_deals_get(day_start, now + timedelta(minutes=1)) or ()
        mine = [d for d in deals if d.magic == config.MAGIC and d.symbol == config.SYMBOL]
        opened = sum(1 for d in mine if d.entry == m.DEAL_ENTRY_IN)
        pnl = sum(d.profit + d.commission + d.swap for d in mine)
        return opened, pnl


def main(mt5=None, max_steps=None, poll_seconds=1.0):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")])
    if mt5 is None:
        import MetaTrader5 as mt5
    bot = Bot(mt5)
    bot.start()
    steps = 0
    try:
        while max_steps is None or steps < max_steps:
            try:
                bot.step()
            except Exception:                     # keep running; the broker holds TP/SL anyway
                log.exception("error in loop")
            steps += 1
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Stopped by user. Any open trade keeps its TP/SL on the server.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
