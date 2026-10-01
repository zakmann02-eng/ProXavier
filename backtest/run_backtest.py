"""Phase 0 backtest.

    python -m backtest.run_backtest                       # config.yaml as-is, in-sample only
    python -m backtest.run_backtest --candles 2,3,5 --dte 0,1   # grid over timeframe × expiry
    python -m backtest.run_backtest --holdout             # also report the hold-out (do this ONCE)

Needs ALPACA_API_KEY/ALPACA_API_SECRET for the first download; 1-minute bars are cached in
data/bars/ and never re-downloaded. Output is a short text summary; per-trade rows go to
data/backtest_trades.csv (gitignored) — summarize it with a script rather than opening it.
"""
from __future__ import annotations

import argparse
import copy
import csv
import logging
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from backtest import engine
from backtest.report import report
from src.config import Secrets, load_config
from src.data import calendar as cal
from src.data.provider import AlpacaProvider, CachedBars


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--candles", default=None, help="comma list of candle minutes, e.g. 2,3,5")
    ap.add_argument("--dte", default=None, help="comma list, e.g. 0,1")
    ap.add_argument("--holdout", action="store_true", help="also report the hold-out period")
    ap.add_argument("--survival", action="store_true", help="apply pause/halt survival rules")
    ap.add_argument("--compound", action="store_true", help="budget = running equity (default: fixed capital)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    secrets = Secrets.from_env()
    end = args.end or (date.today() - timedelta(days=1))
    start = args.start or (end - timedelta(days=int(cfg.backtest.years * 365.25)))
    split = (pd.Timestamp(end) - pd.DateOffset(months=cfg.backtest.holdout_months)).date()

    days = cal.trading_days(start, end)
    warmup = cal.previous_trading_days(start, max(cfg.zones.swing_sessions, cfg.trend.sessions,
                                                  cfg.backtest.rv_window_days) + 5)
    provider = AlpacaProvider(secrets.alpaca_key, secrets.alpaca_secret)
    bars = CachedBars(provider, secrets.data_dir, feed="sip").get_days(cfg.symbol, warmup + days)
    if bars.empty:
        raise SystemExit("no bars available")
    skip = cal.load_skip_days()

    candles = [int(x) for x in args.candles.split(",")] if args.candles else [cfg.session.candle_minutes]
    dtes = [int(x) for x in args.dte.split(",")] if args.dte else [cfg.trade.dte]
    all_rows = []
    for tf in candles:
        for dte in dtes:
            c = copy.deepcopy(cfg)
            c.session.candle_minutes = tf
            c.trade.dte = dte
            res = engine.run(bars, days, c, skip, use_survival=args.survival, compound=args.compound)
            print()
            print(report(res, start, split, end, c.survival.starting_capital, args.holdout,
                         f"=== candles {tf}m · {dte}DTE · {'spread' if c.trade.allow_spreads else 'single'} ==="))
            for t in res.trades:
                if t.day < split or args.holdout:
                    all_rows.append({"candles": tf, "dte": dte, **asdict(t)})
    out = Path(secrets.data_dir) / "backtest_trades.csv"
    if all_rows:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0]))
            w.writeheader()
            w.writerows(all_rows)
        print(f"\n{len(all_rows)} trade rows → {out}")
    print("\nNote: options are priced synthetically (Black-Scholes on realized vol); see backtest/pricing.py.")


if __name__ == "__main__":
    main()
