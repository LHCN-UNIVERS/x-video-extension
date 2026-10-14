"""Trading guard: protects your MANUAL gold trades (from the PC or the phone app).

It does not open trades. Every second it checks your open gold positions and:
  1. adds a stop loss (and optional take profit) to any trade that has none
  2. trails the stop loss behind price once the trade is in profit
  3. cuts trades bigger than GUARD_MAX_LOT down to that size
  4. closes trades opened in GUARD_BLOCKED_HOURS
  5. closes trades opened after the GUARD_MAX_TRADES_PER_DAY-th trade of the day
  6. at GUARD_DAILY_LOSS_LIMIT (closed + open loss today) closes everything, and keeps
     closing new trades until the next server day

All settings are in config.py. GUARD_DRY_RUN = True only logs what it would do.
Run:  python guard.py      (MT5 must be open, logged in, with Algo Trading ON)
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import config

log = logging.getLogger("guard")
GUARD_MAGIC = 50051
RETRY_SECONDS = 10
FILLING_FOK, FILLING_IOC = 1, 2


def server_dt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


class Guard:
    def __init__(self, mt5):
        self.mt5 = mt5
        self.info = {}
        self.last_try = {}
        self.locked_day = None

    # ------------------------------------------------------------------ setup
    def start(self):
        m = self.mt5
        if not m.initialize():
            raise SystemExit(f"MT5 initialize failed: {m.last_error()} (is the terminal open and logged in?)")
        acc = m.account_info()
        log.info("Guard running on account %s (%s), balance %.2f %s  -  %s", acc.login,
                 "DEMO" if acc.trade_mode == m.ACCOUNT_TRADE_MODE_DEMO else "REAL", acc.balance,
                 acc.currency, "DRY RUN (no changes)" if config.GUARD_DRY_RUN else "ACTIVE")
        log.info("Rules: SL %s, TP %s, trail %s (from +%s), max lot %s, blocked hours %s, "
                 "max trades/day %s, daily loss limit %s",
                 config.GUARD_SL_DIST or "off", config.GUARD_TP_DIST or "off",
                 config.GUARD_TRAIL_DIST or "off", config.GUARD_TRAIL_START,
                 config.GUARD_MAX_LOT or "off", config.GUARD_BLOCKED_HOURS or "none",
                 config.GUARD_MAX_TRADES_PER_DAY or "off", config.GUARD_DAILY_LOSS_LIMIT or "off")

    def sym(self, name):
        if name not in self.info:
            self.mt5.symbol_select(name, True)
            i = self.mt5.symbol_info(name)
            m = self.mt5
            filling = (m.ORDER_FILLING_IOC if i.filling_mode & FILLING_IOC else
                       m.ORDER_FILLING_FOK if i.filling_mode & FILLING_FOK else m.ORDER_FILLING_RETURN)
            self.info[name] = {"digits": i.digits, "point": i.point, "filling": filling,
                               "min_stop": i.trade_stops_level * i.point,
                               "vol_step": i.volume_step or 0.01, "vol_min": i.volume_min}
        return self.info[name]

    # ------------------------------------------------------------------ loop
    def step(self):
        m = self.mt5
        positions = [p for p in (m.positions_get() or ())
                     if config.GUARD_SYMBOL_MATCH.upper() in p.symbol.upper()]
        if not positions:
            return
        tick = m.symbol_info_tick(positions[0].symbol)
        if tick is None:
            return
        now = server_dt(tick.time)
        today = now.date()
        opened_today, day_pnl = self.today(now, positions)

        if config.GUARD_DAILY_LOSS_LIMIT and day_pnl <= -config.GUARD_DAILY_LOSS_LIMIT:
            if self.locked_day != today:
                log.warning("DAILY LOSS LIMIT hit (%.2f). Closing all gold trades for today.", day_pnl)
            self.locked_day = today

        allowed = set(opened_today[:config.GUARD_MAX_TRADES_PER_DAY]) if config.GUARD_MAX_TRADES_PER_DAY else None
        for p in positions:
            opened = server_dt(p.time)
            if self.locked_day == today:
                self.close(p, "daily loss limit reached")
                continue
            if opened.date() == today and opened.hour in config.GUARD_BLOCKED_HOURS:
                self.close(p, f"opened in blocked hour {opened.hour}:00")
                continue
            if allowed is not None and opened.date() == today and p.identifier not in allowed:
                self.close(p, f"more than {config.GUARD_MAX_TRADES_PER_DAY} trades today")
                continue
            if config.GUARD_MAX_LOT and p.volume > config.GUARD_MAX_LOT + 1e-9:
                self.reduce(p)
            self.protect(p)

    def today(self, now, positions):
        """(position ids opened today in order, today's closed + open P&L) for gold."""
        m = self.mt5
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = [d for d in (m.history_deals_get(start, now + timedelta(minutes=1)) or ())
                 if config.GUARD_SYMBOL_MATCH.upper() in d.symbol.upper()]
        realised = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
        floating = sum(p.profit + p.swap for p in positions)
        ids = [d.position_id for d in sorted(deals, key=lambda d: d.time) if d.entry == m.DEAL_ENTRY_IN]
        for p in sorted(positions, key=lambda p: p.time):        # opened today but deal not seen yet
            if server_dt(p.time).date() == now.date() and p.identifier not in ids:
                ids.append(p.identifier)
        return ids, realised + floating

    # ------------------------------------------------------------------ actions
    def protect(self, p):
        """Add missing SL/TP, then trail the SL."""
        m = self.mt5
        s = self.sym(p.symbol)
        tick = m.symbol_info_tick(p.symbol)
        long_ = p.type == m.POSITION_TYPE_BUY
        price_now = tick.bid if long_ else tick.ask            # price the trade would close at
        sign = 1 if long_ else -1
        gap = max(s["min_stop"], s["point"])
        sl, tp = p.sl, p.tp

        if config.GUARD_SL_DIST and not sl:
            sl = p.price_open - sign * config.GUARD_SL_DIST
            if sign * (price_now - sl) <= gap:                  # already past the stop
                self.close(p, f"no stop loss and already {config.GUARD_SL_DIST} or more in loss")
                return
        if config.GUARD_TP_DIST and not tp:
            tp = p.price_open + sign * config.GUARD_TP_DIST
            if sign * (tp - price_now) <= gap:
                tp = p.tp                                        # target already passed: leave as is

        if config.GUARD_TRAIL_DIST and sign * (price_now - p.price_open) >= config.GUARD_TRAIL_START:
            trail_sl = price_now - sign * max(config.GUARD_TRAIL_DIST, gap)
            if not sl or sign * (trail_sl - sl) >= s["point"]:  # only ever moves forward
                sl = trail_sl

        sl, tp = round(sl, s["digits"]), round(tp, s["digits"])
        if abs(sl - p.sl) < s["point"] / 2 and abs(tp - p.tp) < s["point"] / 2:
            return
        what = ("trail SL" if p.sl else "add SL") + (f" + TP {tp:.{s['digits']}f}" if tp != p.tp else "")
        self.send(p, "sltp", {"action": m.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": p.ticket,
                              "sl": sl, "tp": tp, "magic": GUARD_MAGIC},
                  f"{what}: SL {p.sl:g} -> {sl:.{s['digits']}f}")

    def reduce(self, p):
        s = self.sym(p.symbol)
        step = s["vol_step"]
        extra = round(round((p.volume - config.GUARD_MAX_LOT) / step) * step, 8)
        if extra < s["vol_min"] - 1e-9:
            return
        self.close(p, f"lot {p.volume:g} > max {config.GUARD_MAX_LOT:g}", volume=extra)

    def close(self, p, reason, volume=None):
        m = self.mt5
        s = self.sym(p.symbol)
        tick = m.symbol_info_tick(p.symbol)
        long_ = p.type == m.POSITION_TYPE_BUY
        vol = volume or p.volume
        self.send(p, "close", {
            "action": m.TRADE_ACTION_DEAL, "symbol": p.symbol, "position": p.ticket, "volume": vol,
            "type": m.ORDER_TYPE_SELL if long_ else m.ORDER_TYPE_BUY,
            "price": tick.bid if long_ else tick.ask, "deviation": config.DEVIATION_POINTS,
            "magic": GUARD_MAGIC, "comment": "guard", "type_time": m.ORDER_TIME_GTC,
            "type_filling": s["filling"]},
            f"close {vol:g} lot ({reason})")

    def send(self, p, kind, req, text):
        key = (p.ticket, kind)
        if time.time() - self.last_try.get(key, 0) < RETRY_SECONDS:
            return
        self.last_try[key] = time.time()
        if config.GUARD_DRY_RUN:
            log.info("[DRY RUN] ticket %s %s: would %s", p.ticket, p.symbol, text)
            return
        res = self.mt5.order_send(req)
        if res is not None and res.retcode == self.mt5.TRADE_RETCODE_DONE:
            log.info("ticket %s %s: %s", p.ticket, p.symbol, text)
            self.last_try.pop(key, None)
        else:
            log.warning("ticket %s: FAILED to %s - %s %s", p.ticket, text, getattr(res, "retcode", None),
                        getattr(res, "comment", self.mt5.last_error()))


def main(mt5=None, max_steps=None, poll_seconds=1.0):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("guard.log")])
    if mt5 is None:
        import MetaTrader5 as mt5
    g = Guard(mt5)
    g.start()
    steps = 0
    try:
        while max_steps is None or steps < max_steps:
            try:
                g.step()
            except Exception:
                log.exception("error in guard loop (continuing)")
                try:                                   # reconnect if the terminal restarted
                    mt5.initialize()
                except Exception:
                    pass
            steps += 1
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Guard stopped by user.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
