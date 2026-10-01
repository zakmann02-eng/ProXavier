# SPY Naked Trading Bot

Analyzes SPY each morning with price-action only (zones + candlestick setups from
*The Art of Naked Trading*), picks a short-dated SPY debit spread, and sends entry/exit
alerts to Telegram. **Phase 1 = alerts only**: you place trades manually; the bot keeps a
paper journal. Year-one goal: prove positive expectancy after costs — not a dollar target.

No LLM calls at runtime. Plain Python, runs as a Railway cron job that exits when done.

## Layout

```
config.yaml              all tunable strategy parameters (hypotheses until backtested)
data/skip_days.yaml      FOMC / CPI / NFP dates — update manually
src/main.py              daily flow + CLI (run / status / resume / close-trade)
src/analysis/            zones, trend bias, setup detection
src/strategy/            entry checklist, contract selection, shared per-day engine
src/risk/                exits, survival rules
src/data/                DataProvider (Alpaca), day-file bar cache, market calendar
src/alerts/              Telegram client + message formats
src/journal/             SQLite journal + stats
backtest/                Phase 0 backtester (same code paths as the live bot)
tests/                   pytest — setups on hand-built candles, end-to-end simulated days
```

## Setup

```bash
cd spy-naked-bot
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill in Telegram + Alpaca keys
pytest
```

## Phase 0 — backtest

```bash
python -m backtest.run_backtest                          # config.yaml, in-sample only
python -m backtest.run_backtest --candles 2,3,5 --dte 0,1  # timeframe × expiry grid
python -m backtest.run_backtest --survival --compound    # with pause/halt rules + compounding
python -m backtest.run_backtest --holdout                # ONCE, when parameters are final
```

* First run downloads ~2.5 years of SPY 1-minute bars (Alpaca SIP, free for data >15 min old)
  into `data/bars/`; later runs read the cache only.
* The most recent `holdout_months` (6) are hidden unless `--holdout` is passed, so you can't
  accidentally tune on them.
* Reports: win rate, avg win/loss, expectancy ($ and %), max drawdown, trades/month, plus
  breakdowns by setup, exit reason and direction. Per-trade rows → `data/backtest_trades.csv`.
* **Option prices are synthetic** (Black-Scholes on realized vol × `iv_rv_ratio`, modeled
  bid/ask). Free historical SPY option quotes aren't available; results are a first filter.
  Swap in real options history (Polygon/ThetaData) in `backtest/pricing.py` if you buy it.
* Fills are conservative: entry at the max limit (mid + 25% of width), exit at mid − 25%,
  intrabar adverse extreme checked before the close.

## Phase 1 — live alerts on Railway

1. Create a Railway project from this GitHub repo; set the service **Root Directory** to
   `spy-naked-bot`. `railway.json` sets the start command, cron (`55 11,12 * * 1-5` UTC — both
   DST offsets) and no restarts.
2. Add a **volume** mounted at `/data`; set `DATA_DIR=/data`, `DB_PATH=/data/journal.sqlite3`.
3. Set variables from `.env.example` (`MODE=paper`, Telegram, Alpaca).
4. The script checks the real ET time: it runs only if started within 45 min after 07:55 ET,
   so the wrong-DST cron slot exits immediately. It also refuses to run twice on one day.

Daily flow (ET): 07:55 calendar / skip-list / survival checks → 09:20 zones + bias →
09:25 brief → evaluate the 09:35–09:50 closes → ENTER alert or "No trade today" →
monitor every 30 s (TP, structural/hard/breakeven stops, 12:00 time stop) → EXIT alert →
journal + daily summary (+ weekly report on the last trading day of the week).

Any failure (missing data, API error) → Telegram error alert and no trade.

### CLI

```bash
python -m src.main run [--force]          # daily job (cron entrypoint)
python -m src.main status                 # journal stats, halt flag, open trades
python -m src.main resume                 # clear a drawdown halt after manual review
python -m src.main close-trade ID PRICE   # close a trade the bot couldn't finish
```

On Railway, run these with `railway run` or a one-off shell against the same volume.

## Safety defaults

* `MODE=paper` only. Any other mode is refused — live execution (Phase 2) isn't built.
* Secrets come from env vars only.
* Max 1 trade/day; 2 consecutive losses → paused for the week; −30% from peak → halted
  until `resume`.
* Phase 2 must use Robinhood's official Trading MCP server, never unofficial libraries.

## Open questions

* Is the Robinhood account approved for spreads (Level 3)? If not, set
  `trade.allow_spreads: false` (single long options) and re-run the backtest.
* Market data budget: Alpaca free tier is wired in. Paid options history would make the
  backtest's option pricing real instead of modeled.
