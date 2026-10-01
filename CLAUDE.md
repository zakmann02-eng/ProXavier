# CLAUDE.md — SPY Naked Trading Bot

## Working rules for Claude Code in this directory
- Never open raw data files, large CSVs (`data/`), or full logs. Write and run small summary
  scripts and read only their short output.
- Run `pytest` before committing. Setup detection changes need a hand-built candle test.
- The live bot and the backtester share `src/strategy/session.py`, `checklist.py`,
  `contracts.py` and `risk/exits.py` — keep it that way so backtests measure the live logic.
- Don't tune parameters on the hold-out period; `--holdout` is for the final check only.

## Implementation notes
- Bars: pandas DataFrames, columns open/high/low/close/volume, tz-aware ET index = bar START.
- Setup detectors judge only the LAST closed candle; earlier candles (incl. the prior
  session's tail) are context.
- "Candle closed at or within a zone" = signal close within `checklist.zone_proximity_pct`
  of the setup's zone (Kangaroo Tail bodies close just outside their zone by definition).
- Paper fill = the alert's max limit price; exit fill = mid − limit_buffer × width.

---

# Original project brief

## What this project is
A Python bot that analyzes SPY each morning using price action methods inspired by Johnathon Fox's
"The Art of Naked Trading" (zones + candlestick setups, no indicators), selects a short-dated SPY
options trade, and sends entry/exit alerts to Telegram. Code lives on GitHub and deploys to Railway.

Starting capital: $100. The goal of year one is to PROVE positive expectancy after costs, not to hit
a dollar target. Every design decision should favor survival and measurable results over trade frequency.

## Cost constraints (important)
- **No LLM / Claude API calls at runtime.** The bot is plain Python; analysis must cost zero tokens.
- **Railway:** run as a cron job that starts ~7:55 AM ET on weekdays and EXITS when the day's work is
  done (no trade, or trade closed + summary sent). Railway cron uses UTC, so schedule for both DST offsets
  and have the script check the real ET time and exit early if it's not time yet. Keep memory small.
  Poll every ~30s only while a trade is open; otherwise sleep until the next candle close.
- **Data:** prefer free tiers. Cache downloaded historical data in `data/` (gitignored) and never
  re-download what's cached.
- **When working in Claude Code:** never open raw data files, large CSVs, or full logs. Write and run
  small summary scripts instead and read only their short output.

## Phases
- **Phase 0 — Backtest:** Run the strategy on 2+ years of historical SPY and options data. Hold out the
  most recent ~6 months as an unseen test period. Report win rate, avg win, avg loss, expectancy,
  max drawdown, trades per month.
- **Phase 1 — Paper / alerts-only (current target):** Bot runs live on Railway and sends Telegram alerts.
  The user places trades manually on Robinhood. Bot records paper fills in its journal. Run 30–60 trading days.
- **Phase 2 — Automated execution (later, only if Phase 1 shows positive expectancy):** Connect to
  Robinhood's official Trading MCP server (https://agent.robinhood.com/mcp/trading) using a dedicated
  agentic account funded with the trading capital only. Before building, confirm: (a) Robinhood permits
  an unattended service to use it, (b) it supports multi-leg (spread) orders.
  **Never use unofficial Robinhood libraries (robin_stocks, etc.) for real money.**

## Default safety settings
- `MODE=paper` is the default. Live order placement must require an explicit config change.
- Secrets (API keys, Telegram token) come from environment variables only. Never commit them.
- If anything fails (data missing, API error, unclear state), the bot sends a Telegram alert and does NOT trade.

## Daily schedule (America/New_York, market days only)
1. **~8:00** Wake up. Check market calendar (holidays, half days), the skip-day list (FOMC days, and
   CPI/NFP days unless backtests show they're fine), and survival rules. If skipping, send a Telegram
   message with the reason and stop.
2. **8:00–9:25** Build zones and determine trend bias. Send pre-market brief at ~9:25.
3. **9:30–9:50** Evaluate each closed 5-minute candle (9:35, 9:40, 9:45, 9:50) against the entry checklist.
   Candle timeframe is a tunable parameter (test 2, 3, and 5 min in backtests).
4. If no valid setup by 9:50 → send "No trade today" and stop.
5. On a valid setup → build the trade, send ENTER alert, begin monitoring.
6. Monitor every ~30s until exit. Send exit alert.
7. After close → write journal entry, update stats, check survival rules, send daily summary.
   Fridays: weekly report.

## Strategy rules

### Zones (built pre-market)
- Prior day high, low, close
- Overnight / pre-market high and low
- Swing highs/lows from the last ~10 sessions where price reversed at least twice (zone strength = touch count)
- Zones are price BANDS, not single lines (width is a tunable parameter, e.g. 0.10–0.25% of price).
  Merge zones that overlap.

### Trend bias (price structure, no indicators)
- Bullish: higher highs and higher lows over recent sessions/hourly swings
- Bearish: lower highs and lower lows
- Neutral: mixed → only take setups at the strongest zones, or skip (tunable)

### Setups (operational definitions; all thresholds are tunable parameters to be validated in backtests)
- **Kangaroo Tail:** One candle with a long tail (tail ≥ ~2/3 of candle range); open and close both in the
  opposite third of the range; the tail pokes into or through a zone while the body closes back outside it;
  candle range is larger than recent candles. Bullish if tail points down at support, bearish if up at resistance.
- **Big Shadow:** Two-candle engulfing pattern at a zone where the second candle's range exceeds the
  ranges of the prior several candles and it closes near its extreme in the signal direction.
- **Wammie / Moolah:** Double bottom (Wammie) / double top (Moolah) at a zone. Two touches separated by
  a minimum number of bars, the second touch failing to make a meaningful new extreme, confirmed by a
  candle closing in the signal direction.
- **Big Belt:** Market opens with a gap beyond the prior range into a zone, then a strong candle opens at
  one extreme and closes near the other, reversing away from the zone. Especially relevant at 9:30.
- **Last Kiss:** Price breaks through a zone, returns to retest it from the other side, and rejects it
  in the breakout direction.

### Entry checklist (ALL must pass)
1. Candle closed at or within a zone
2. A setup above is confirmed
3. Setup direction agrees with trend bias
4. Distance to the next opposing zone ≥ 2× distance to the structural stop (reward:risk ≥ 2)
5. Trade cost fits the budget (see below)
6. No trade has been taken today (max 1 trade/day)

## Trade construction
- **Primary: debit vertical spread.** Bullish → buy call nearest the money, sell call ~$2 higher.
  Bearish → mirror with puts. Spread width is tunable.
- **Fallback:** single long call/put if spreads aren't permitted (config flag `ALLOW_SPREADS`).
- **Expiration:** same day (0DTE) or next day (1DTE); compare both in backtests.
- **Budget:** total cost including fees ≤ available capital (initially $100).
- **Liquidity filter:** skip if bid-ask width is too large relative to the spread's price (tunable threshold).
- Alert shows a max limit price; never chase above it.

## Exits (first one triggered wins)
- **Take profit:** +50% to +70% on the position, or SPY reaching the next zone
- **Structural stop:** SPY closes beyond the setup candle's tail (setup invalidated)
- **Hard stop:** position down 40%
- **Breakeven rule:** once position reaches +30%, stop moves to entry price (send alert)
- **Time stop:** exit by 12:00 PM ET if nothing else triggered

## Survival rules
- 2 consecutive losses → pause for the rest of the week
- Account down 30% from peak → stop trading; Telegram alert requiring manual review/resume

## Telegram alerts (examples of format)
- Pre-market brief: bias, resistance zones, support zones, what it's watching for
- ENTER: contract legs, expiration, max price, setup + zone, TP levels, stop levels, time stop
- MOVE STOP TO BREAKEVEN
- EXIT: reason (TP / structural stop / hard stop / time stop), result in $ and %
- No trade today (+ reason)
- Daily summary; weekly report; errors

## Build order
1. Repo skeleton, config, `.env.example`, README
2. Zones + trend bias (with unit tests)
3. Setup detection (with unit tests on hand-built candles)
4. Entry checklist + contract selection
5. Exit logic + survival rules
6. Backtester + report (Phase 0)
7. Telegram alerts
8. Journal + stats (SQLite)
9. Scheduler / daily flow; Railway deployment from GitHub
10. Optional: TradingView Pine Script indicator that draws the same zones and setups for visual checking

## Open questions for the user
- Is the Robinhood account approved for Level 3 options (spreads)?
- Which market data provider to use (budget for options history)?
