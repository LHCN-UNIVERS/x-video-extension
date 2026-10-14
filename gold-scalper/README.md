# Gold +$0.50 Scalper (XAUUSD, MetaTrader 5)

Opens a gold trade on a signal and closes it when price has moved **+$0.50** in your favour
(after the spread). Every trade also has a stop loss. TP and SL sit on the broker's server,
so they still work if your PC or the script stops.

| File | What it does |
|---|---|
| `config.py` | **All settings** (lot, TP, SL, signal, spread filter, hours, daily loss limit, dry-run) |
| `fetch_mt5_data.py` | Downloads real bid/ask ticks from your broker → `ticks.csv` |
| `backtest.py` | Replays those ticks and shows whether the rules would have made money |
| `fetch_mt5_bars.py` | Downloads years of daily + 4-hour bars and your broker's swap → `bars_D1.csv`, `bars_H4.csv` |
| `m1_backtest.py` | Intraday signals tested on years of 1-minute bars |
| `trend_backtest.py` | Trend-following test (hold days/weeks) on those bars, vs. buy & hold |
| `analyze_my_trades.py` | Reads YOUR past trades from MT5 history (read-only) and shows what makes/loses money |
| `live_bot.py` | Trades automatically in MT5 using the same rules |
| `signals.py` | Entry signals: `ema_pullback`, `breakout`, `rsi_reversion`, `london_breakout`, `ny_breakout`, `coin_flip` (baseline) |
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

## Analyse your own manual trading

Log in to the account you trade on, then:
```
python analyze_my_trades.py --days 365
```
Shows your real net result after costs, plus results by hour, hold time, side, weekday and month.
It only reads history; it never places or changes orders.

## Step A: scalper with time, volatility and trailing-stop options

```
python backtest.py ticks.csv --sweep --split 0.7
```
Tries 6 signals x 5 exit styles (fixed targets and trailing stops) x volatility filter on/off,
then shows the top 3 on the unseen 30%, each with a **by-hour** table. Combinations with fewer
than 15 trades are not ranked. Single tests, e.g.:
```
python backtest.py ticks.csv --signal london_breakout --tp 0 --trail 3 --sl 5 --hold 480
```
London/NY breakout hours are broker **server** hours (GMT on Exness). They trade at most once a
day, so they need many days of ticks to judge: download more with `fetch_mt5_data.py --days 120`
if your disk has room (60 days of ticks ~ 0.5 GB).

## Step B: trend following on years of data

```
python fetch_mt5_bars.py
python trend_backtest.py bars_D1.csv
python trend_backtest.py bars_H4.csv
```
Tests Donchian breakout, moving average and momentum rules, long-only and long+short, with and
without an ATR stop. Includes spread and your broker's real overnight swap. Every result is
compared with plain **buy & hold** over the same period: a trend rule that can't beat holding
gold with less drawdown isn't worth running. Trust a setup only if it works on the unseen 30%
on **both** D1 and H4. (There is no live bot for this yet; it trades rarely enough to do by hand
or we add one if the test is convincing.)

## Years of 1-minute bars (more trades for the intraday signals)

In MT5 set Tools > Options > Charts > "Max. bars in chart" to Unlimited and restart MT5, then:
```
python fetch_mt5_bars.py --m1 --years 5
python m1_backtest.py bars_M1.csv
```
Same signals and exits as `backtest.py`, simulated on 1-minute bars with conservative fills
(stop assumed first when a bar touches both stop and target). Shows results by year and hour.

## Built-in safety

- Stop loss on every trade; optional trailing stop (`TRAIL_DIST`) and time exit (`MAX_HOLD_MINUTES`)
- Optional volatility filter (`MIN_ATR_SPREAD`): skips entries when the market is too quiet
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
