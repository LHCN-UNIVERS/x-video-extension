# Gold +$0.50 Scalper (XAUUSD, MetaTrader 5)

Opens a gold trade on a signal and closes it when price has moved **+$0.50** in your favour
(after the spread). Every trade also has a stop loss. TP and SL sit on the broker's server,
so they still work if your PC or the script stops.

| File | What it does |
|---|---|
| `config.py` | **All settings** (lot, TP, SL, signal, spread filter, hours, daily loss limit, dry-run) |
| `fetch_mt5_data.py` | Downloads real bid/ask ticks from your broker → `ticks.csv` |
| `backtest.py` | Replays those ticks and shows whether the rules would have made money |
| `live_bot.py` | Trades automatically in MT5 using the same rules |
| `signals.py` | Entry signals: `ema_pullback`, `breakout`, `rsi_reversion`, `coin_flip` (baseline) |
| `make_synthetic_ticks.py` | Fake data, only for checking that the code runs |

## Why the spread matters so much

A buy opens at the **ask** and closes at the **bid**. With a typical $0.25–0.35 gold spread,
"+$0.50" really needs a ~$0.80 move. With a $2.00 stop, you need to win **about 80% of trades
just to break even**. The backtest prints this number ("needed to break even") next to the
actual win rate. If actual < needed, the setup loses money; don't run it live.

On random data (no edge) every combination loses roughly the spread on each trade, which
is what this tool showed when tested. A signal is only worth trading if it clearly beats
`coin_flip` **on data it was not tuned on** (use `--split`).

## Step by step (Windows)

1. Install [MetaTrader 5](https://www.metatrader5.com/) from your broker, open a **demo account**, log in.
   Show `XAUUSD` in Market Watch (note its exact name; set `SYMBOL` in `config.py` if different).
2. Install Python 3.10+ and the packages:
   ```
   pip install MetaTrader5 pandas numpy
   ```
3. Download real ticks (MT5 must be open):
   ```
   python fetch_mt5_data.py --days 60
   ```
4. Test the default rules, then compare all signals and stop losses:
   ```
   python backtest.py ticks.csv
   python backtest.py ticks.csv --sweep --split 0.7
   ```
   The sweep picks the best combination on the first 70% of the data, then shows how it did
   on the last 30%. **Trust only that last report.** Put your broker's commission in
   `COMMISSION_PER_LOT` if you pay one.
5. Copy the winning `SIGNAL` / `SL_DIST` into `config.py` and run the bot. It starts in
   **dry-run** (only logs what it would do):
   ```
   python live_bot.py
   ```
   Log goes to the screen and `bot.log`. Stop with Ctrl+C.
6. After a day or two of sensible dry-run logs, set `DRY_RUN = False` and let it trade the
   **demo** account for a few weeks. Compare demo results with the backtest.
7. Only then, if results hold up: `ALLOW_REAL_ACCOUNT = True`, smallest lot (0.01).

## Built-in safety

- Stop loss on every trade; optional time exit (`MAX_HOLD_MINUTES`)
- One trade at a time; at most one decision per completed 1-minute bar
- Skips entries when the spread is too wide (news) or outside `TRADE_HOURS`
- `MAX_TRADES_PER_DAY` and `DAILY_LOSS_LIMIT` stop the bot for the day
- If the fill slips, TP/SL are moved so the target is exactly +$0.50 from the real fill
- Refuses a real-money account unless `ALLOW_REAL_ACCOUNT = True`
- Checks the broker's minimum stop distance (some brokers can't place a TP only $0.50 away)

## Tests

```
pip install pytest
python -m pytest tests
```
`tests/fake_mt5.py` simulates the MT5 API so the bot's logic is tested without a broker.

*Not financial advice. Trading gold with leverage can lose more than you expect; test on demo first.*
