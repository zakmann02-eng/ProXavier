# ProXavier — Xavier Quant Day Trading Bot

A multi-strategy quant trading bot built on Alpaca Markets with Telegram control.

## Strategies

| Strategy | Type | Assets |
|----------|------|--------|
| **ORB** — Opening Range Breakout | Day | Equities, ETFs |
| **VWAP Reversion** — Mean-reversion fade | Day | Equities, ETFs |
| **Momentum Breakout** — 20-day high/low | Swing | Equities, ETFs |
| **ETF Sector Rotation** — Relative strength | Swing | ETFs |
| **Crypto Trend** — Multi-TF trend follow | Day + Swing | BTC, ETH, SOL |

## Setup

1. Copy `.Env.example` to `.env` and fill in your credentials:
   - `ALPACA_API_KEY` / `ALPACA_API_SECRET` — from [alpaca.markets](https://alpaca.markets)
   - `XAVIER_TELEGRAM_TOKEN` / `XAVIER_TELEGRAM_CHAT_ID` — from [@BotFather](https://t.me/BotFather)

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run in paper mode (default):
   ```bash
   python xavier_main.py
   ```

## Telegram Commands

| Command | Action |
|---------|--------|
| `/status` | Account equity, P&L, market status |
| `/positions` | Open positions with TP/SL levels |
| `/risk` | Portfolio risk exposure dashboard |
| `/strategies` | Active strategy configuration |
| `/pause` | Halt new trades (keep positions) |
| `/resume` | Re-enable trading |
| `/closeall` | Close every open position |
| `/closeday` | Close day trades only |
| `/watchlist` | Current equity/ETF/crypto watchlist |

## Risk Management

- Fixed % risk per trade (default 1% of equity)
- Daily loss limit halt (default 3%)
- Consecutive loss halt (default 4 losses)
- Portfolio risk cap (default 6% total open risk)
- TP1: exit 50%, move stop to breakeven, activate trailing stop
- TP2: exit remaining position

## Configuration

All settings via environment variables — see `.Env.example` for the full list.
Key params: `XAVIER_RISK_PCT`, `XAVIER_MAX_POSITIONS`, `XAVIER_ALLOW_SHORT`, `ALPACA_PAPER`.
