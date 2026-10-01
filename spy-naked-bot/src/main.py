"""Daily flow. Railway cron starts this ~07:55 ET on weekdays; it exits when the day's work is done.

    python -m src.main run [--force]        # the daily job (cron entrypoint)
    python -m src.main status               # print journal stats / state
    python -m src.main resume               # clear a drawdown halt after manual review
    python -m src.main close-trade ID PRICE # manually close a trade the bot couldn't finish
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time as _time
from datetime import date, datetime, timedelta

import pandas as pd

from src.alerts import messages as msg
from src.alerts.telegram import Telegram
from src.config import Config, Secrets, load_config, parse_hhmm
from src.data import calendar as cal
from src.data.bars import ET
from src.data.provider import AlpacaProvider, CachedBars, DataProvider
from src.journal.db import Journal
from src.journal.stats import compute_stats
from src.risk import survival
from src.risk.exits import ExitManager
from src.strategy.checklist import CheckResult
from src.strategy.contracts import TradePlan, build_trade, pick_expiry, position_mark
from src.strategy.session import (DataError, DayPlan, candle_frame, entry_candle_closes, prepare_day,
                                  scan_candle)

log = logging.getLogger("spybot")

HALT_KEY = "halted"
PEAK_RESET_KEY = "drawdown_peak_from_trade"   # set on manual resume
DONE_STATUSES = {"skipped", "no_trade", "traded", "error"}


class Clock:
    def now(self) -> datetime:
        return datetime.now(tz=pd.Timestamp.now(tz=ET).tz)

    def sleep_until(self, t: datetime) -> None:
        while True:
            remaining = (t - self.now()).total_seconds()
            if remaining <= 0:
                return
            _time.sleep(min(remaining, 60))

    def sleep(self, seconds: float) -> None:
        _time.sleep(seconds)


def at(day: date, hhmm: str) -> datetime:
    return pd.Timestamp.combine(day, parse_hhmm(hhmm)).tz_localize(ET).to_pydatetime()


class DailyRunner:
    def __init__(self, cfg: Config, secrets: Secrets, provider: DataProvider, telegram: Telegram,
                 journal: Journal, clock: Clock, skip_days: dict[str, set[date]],
                 history: CachedBars | None = None):
        self.cfg = cfg
        self.mode = secrets.mode
        self.provider = provider
        self.tg = telegram
        self.journal = journal
        self.clock = clock
        self.skip_days = skip_days
        self.history = history

    # ── entry point ──
    def run(self, force: bool = False) -> int:
        today = self.clock.now().date()
        try:
            return self._run(today, force)
        except Exception as e:  # noqa: BLE001 — any failure → alert and do not trade
            log.exception("daily run failed")
            self.tg.send(msg.error(self.mode, "daily run aborted", f"{type(e).__name__}: {e}"))
            self.journal.set_day(today, "error", f"{type(e).__name__}: {e}")
            return 1

    def _run(self, today: date, force: bool) -> int:
        cfg = self.cfg
        if self.mode != "paper":
            raise RuntimeError(f"MODE={self.mode!r}: only paper mode (alerts-only) is implemented in Phase 1")
        now = self.clock.now()
        wake = at(today, cfg.session.wake_time)
        if not force and not (wake - timedelta(minutes=10) <= now < wake + timedelta(minutes=cfg.session.wake_window_minutes)):
            log.info("outside wake window (%s ET) — exiting", now.strftime("%H:%M"))
            return 0
        if self.journal.day_status(today) in DONE_STATUSES | {"running"} and not force:
            log.info("already ran today (%s) — exiting", self.journal.day_status(today))
            return 0

        # 1. Calendar, skip list, survival rules.
        if not cal.is_trading_day(today):
            return self._skip(today, "market closed (holiday)")
        if cal.is_half_day(today) and cfg.session.skip_half_days:
            return self._skip(today, "half day")
        reason = cal.skip_reason(today, self.skip_days, cfg.session.skip_day_types)
        if reason:
            return self._skip(today, reason)
        stuck = self.journal.unclosed_trades()
        if stuck:
            return self._skip(today, f"trade #{stuck[0]['id']} from {stuck[0]['day']} was never closed — "
                                     "close it with `close-trade` first")
        trades = [(t.day, t.pnl) for t in self.journal.closed_trades()]
        status = survival.check(today, trades, cfg.survival.starting_capital,
                                self.journal.get_state(HALT_KEY) == "1", cfg.survival, self._peak_from())
        if not status.can_trade:
            if status.halted:
                self.journal.set_state(HALT_KEY, "1")
            return self._skip(today, status.reason)
        self.journal.set_day(today, "running")

        # 2. Zones + bias, brief at 09:25.
        brief_t = at(today, cfg.session.brief_time)
        self.clock.sleep_until(brief_t - timedelta(minutes=5))
        history = self._history(today)
        plan = prepare_day(today, history, cfg)
        warnings = [f"skip_days.yaml has no {t} dates for {today.year} — update it"
                    for t in cal.stale_skip_types(today, self.skip_days, cfg.session.skip_day_types)]
        if cal.is_half_day(today):
            warnings.append("half day (early close)")
        self.clock.sleep_until(brief_t)
        self.tg.send(msg.premarket_brief(plan, self.mode, warnings))

        # 3–5. Scan closed candles in the entry window.
        equity = survival.equity_now(trades, cfg.survival.starting_capital)
        outcome, traded = self._scan_and_trade(today, plan, history, equity)

        # 7. Wrap up.
        self.journal.set_day(today, "traded" if traded else "no_trade", outcome)
        self._summaries(today, outcome)
        return 0

    def _peak_from(self) -> int:
        return int(self.journal.get_state(PEAK_RESET_KEY, "0") or 0)

    def _skip(self, today: date, reason: str) -> int:
        self.journal.set_day(today, "skipped", reason)
        self.tg.send(msg.skip_day(self.mode, today, reason))
        return 0

    # ── data ──
    def _history(self, today: date) -> pd.DataFrame:
        """Prior sessions (cached, completed days) + today's pre-market (live)."""
        n = max(self.cfg.zones.swing_sessions, self.cfg.trend.sessions) + 1
        days = cal.previous_trading_days(today, n)
        start = at(days[0], "04:00")
        if self.history is not None:
            prior = self.history.get_days(self.cfg.symbol, days)
            live = self.provider.get_minute_bars(self.cfg.symbol, at(today, "00:00"), self.clock.now())
            bars = pd.concat([prior, live]).sort_index()
        else:
            bars = self.provider.get_minute_bars(self.cfg.symbol, start, self.clock.now())
        bars = bars[~bars.index.duplicated(keep="last")]
        if bars.empty or len(set(bars.index.date) & set(days)) < len(days) - 1:
            raise DataError(f"history incomplete: have {len(set(bars.index.date) & set(days))}/{len(days)} sessions")
        return bars

    def _today_bars(self, today: date, history: pd.DataFrame, upto: datetime) -> pd.DataFrame:
        live = self.provider.get_minute_bars(self.cfg.symbol, at(today, "09:30"), upto)
        prior = history[history.index < at(today, "09:30")]
        out = pd.concat([prior, live]).sort_index()
        return out[~out.index.duplicated(keep="last")]

    # ── entry ──
    def _scan_and_trade(self, today: date, plan: DayPlan, history: pd.DataFrame, equity: float) -> tuple[str, bool]:
        cfg = self.cfg
        tf = timedelta(minutes=cfg.session.candle_minutes)
        lag = timedelta(seconds=cfg.session.data_lag_seconds)
        rejections: list[str] = []
        for close_t in entry_candle_closes(today, cfg):
            self.clock.sleep_until(close_t + lag)
            bars = self._today_bars(today, history, close_t)
            frame, start = candle_frame(bars, today, close_t, cfg)
            if len(frame) <= start or frame.index[-1] + tf != pd.Timestamp(close_t):
                raise DataError(f"candle closing {close_t:%H:%M} is missing from the data feed")
            best, results = scan_candle(plan, frame, start, self.journal.trades_on(today), cfg)
            for r in results:
                log.info("%s %s %s: %s", close_t.strftime("%H:%M"), r.setup.name, r.setup.direction,
                         "PASS" if r.passed else "; ".join(r.failures()))
                if not r.passed:
                    rejections.append(f"{close_t:%H:%M} {r.setup.name} {r.setup.direction}: {r.failures()[0]}")
            if best is None:
                continue
            trade = self._build(today, best, equity)
            if not trade.ok:
                rejections.append(f"{close_t:%H:%M} {best.setup.name}: 5 budget/liquidity: {trade.reject_reason}")
                continue
            return self._enter_and_monitor(today, best, trade, frame), True
        reason = "no valid setup by " + cfg.session.entry_end
        if rejections:
            reason += "\nRejected:\n" + "\n".join(f"  • {r}" for r in rejections[-5:])
        self.tg.send(msg.no_trade(self.mode, today, reason))
        return reason, False

    def _build(self, today: date, chk: CheckResult, budget: float) -> TradePlan:
        cfg = self.cfg
        horizon = cal.trading_days(today, today + timedelta(days=10))
        expiry = pick_expiry(horizon, today, cfg.trade.dte, horizon)
        if expiry is None:
            raise DataError("could not determine the option expiration")
        chain = self.provider.get_option_chain(cfg.symbol, expiry)
        if not chain:
            raise DataError(f"empty option chain for {expiry}")
        spot = self.provider.get_latest_price(cfg.symbol)
        return build_trade(chain, chk.setup.direction, spot, budget, cfg.trade)

    def _enter_and_monitor(self, today: date, chk: CheckResult, trade: TradePlan, frame: pd.DataFrame) -> str:
        cfg = self.cfg
        s = chk.setup
        now = self.clock.now()
        self.tg.send(msg.enter(self.mode, chk, trade, cfg.exits.take_profit_pct, cfg.exits.hard_stop_pct,
                               cfg.session.time_stop))
        entry_price = trade.limit   # paper fill at the max limit (conservative)
        trade_id = self.journal.open_trade(
            day=today, mode=self.mode, setup=s.name, direction=s.direction, zone=str(s.zone),
            legs=[{"symbol": l.quote.symbol, "side": l.side} for l in trade.legs], expiry=trade.expiry,
            qty=trade.qty, entry_time=now, entry_price=entry_price, spy_entry=s.entry,
            structural_stop=s.stop, target=chk.target)
        em = ExitManager(s.direction, entry_price, s.stop, chk.target, at(today, cfg.session.time_stop), cfg.exits)
        return self._monitor(today, trade_id, trade, em, frame.index[-1])

    # ── monitoring ──
    def _monitor(self, today: date, trade_id: int, trade: TradePlan, em: ExitManager, last_bar: pd.Timestamp) -> str:
        cfg = self.cfg
        tf = timedelta(minutes=cfg.session.candle_minutes)
        lag = timedelta(seconds=cfg.session.data_lag_seconds)
        symbols = [l.quote.symbol for l in trade.legs]
        next_close = last_bar.to_pydatetime() + 2 * tf
        failures = 0
        give_up = em.time_stop + timedelta(minutes=30)
        while True:
            now = self.clock.now()
            try:
                exit_ev = None
                if now >= next_close + lag and next_close <= em.time_stop:
                    bars = self.provider.get_minute_bars(cfg.symbol, next_close - tf,
                                                         next_close - timedelta(seconds=1))
                    if not bars.empty:
                        exit_ev = em.on_bar_close(now, float(bars["close"].iloc[-1]))
                    next_close += tf
                quotes = self.provider.get_option_quotes(symbols)
                mark, width = position_mark(trade.legs, quotes)
                spy = self.provider.get_latest_price(cfg.symbol)
                failures = 0
                events = [exit_ev] if exit_ev else em.on_mark(now, mark, spy)
                for ev in events:
                    if ev.kind == "breakeven":
                        self.tg.send(msg.breakeven(self.mode, ev))
                    elif ev.kind == "exit":
                        return self._close(trade_id, trade, em, ev, mark, width, now)
            except Exception as e:  # noqa: BLE001
                failures += 1
                log.warning("monitor error %d: %s", failures, e)
                if failures in (3, 10) or failures % 30 == 0:
                    self.tg.send(msg.error(self.mode, f"monitoring trade #{trade_id} failing ({failures}×)",
                                           f"{e}\nManage the position manually if this continues."))
                if now >= give_up:
                    raise RuntimeError(f"could not monitor trade #{trade_id}; it is still open in the journal — "
                                       "close it manually and run `close-trade`") from e
            self.clock.sleep(cfg.exits.poll_seconds)

    def _close(self, trade_id: int, trade: TradePlan, em: ExitManager, ev, mark: float, width: float,
               now: datetime) -> str:
        exit_price = max(0.0, mark - self.cfg.trade.limit_buffer_fraction * width)
        if trade.max_value is not None:
            exit_price = min(exit_price, trade.max_value)
        gross = (exit_price - em.entry_price) * 100 * trade.qty
        pnl = gross - trade.fees
        pnl_pct = pnl / (em.entry_price * 100 * trade.qty) * 100
        self.journal.close_trade(trade_id, now, exit_price, ev.reason, trade.fees, pnl, pnl_pct)
        self.tg.send(msg.exit_msg(self.mode, ev, exit_price, pnl, pnl_pct))
        return f"Traded: exit by {ev.reason}, ${pnl:+.2f} ({pnl_pct:+.1f}%)"

    # ── summaries ──
    def _summaries(self, today: date, outcome: str) -> None:
        cfg = self.cfg
        closed = self.journal.closed_trades()
        rows = [(t.day, t.pnl, t.pnl_pct) for t in closed]
        all_stats = compute_stats(rows, cfg.survival.starting_capital)
        equity = survival.equity_now([(t.day, t.pnl) for t in closed], cfg.survival.starting_capital)
        self.tg.send(msg.daily_summary(self.mode, today, outcome, equity, all_stats))
        status = survival.check(today, [(t.day, t.pnl) for t in closed],
                                cfg.survival.starting_capital, False, cfg.survival, self._peak_from())
        if status.halted:
            self.journal.set_state(HALT_KEY, "1")
            self.tg.send(msg.error(self.mode, "TRADING HALTED", status.reason +
                                   "\nReview, then run `python -m src.main resume`."))
        elif not status.can_trade:
            self.tg.send(f"{msg.tag(self.mode)} ⏸ {status.reason}")
        week = survival.week_start(today)
        rest_of_week = [d for d in cal.trading_days(today, week + timedelta(days=4)) if d > today]
        if not rest_of_week:
            wk = [r for r in rows if r[0] >= week]
            day_lines = [f"  {r['day']}: {r['status']} — {(r['reason'] or '').splitlines()[0] if r['reason'] else ''}"
                         for r in self.journal.days_between(week, today)]
            self.tg.send(msg.weekly_report(self.mode, week, compute_stats(wk, equity - sum(r[1] for r in wk), months=12 / 52),
                                           all_stats, day_lines))


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    ap = argparse.ArgumentParser(prog="spybot")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--force", action="store_true", help="ignore the wake window / already-ran guard")
    sub.add_parser("status")
    sub.add_parser("resume")
    c = sub.add_parser("close-trade")
    c.add_argument("trade_id", type=int)
    c.add_argument("exit_price", type=float, help="per-share exit price of the position")
    args = ap.parse_args(argv)

    cfg = load_config()
    secrets = Secrets.from_env()
    journal = Journal(secrets.db_path)
    tg = Telegram(secrets.telegram_token, secrets.telegram_chat_id)

    if args.cmd == "status":
        closed = journal.closed_trades()
        st = compute_stats([(t.day, t.pnl, t.pnl_pct) for t in closed], cfg.survival.starting_capital)
        print("\n".join(st.lines()))
        print("halted:", journal.get_state(HALT_KEY) == "1", "| unclosed trades:", len(journal.unclosed_trades()))
        return 0
    if args.cmd == "resume":
        journal.set_state(HALT_KEY, "0")
        # Drawdown is measured from the peak AFTER this point, otherwise the halt re-triggers at once.
        journal.set_state(PEAK_RESET_KEY, str(len(journal.closed_trades())))
        tg.send(f"{msg.tag(secrets.mode)} ▶ Trading resumed manually.")
        print("resumed")
        return 0
    if args.cmd == "close-trade":
        row = next((t for t in journal.unclosed_trades() if t["id"] == args.trade_id), None)
        if row is None:
            print("no such open trade")
            return 1
        legs = len(json.loads(row["legs"] or "[]")) or 1
        fees = 2 * cfg.trade.fee_per_contract_leg * legs * row["qty"]
        pnl = (args.exit_price - row["entry_price"]) * 100 * row["qty"] - fees
        pnl_pct = pnl / (row["entry_price"] * 100 * row["qty"]) * 100
        journal.close_trade(args.trade_id, datetime.now(), args.exit_price, "manual close", fees, pnl, pnl_pct)
        print(f"closed #{args.trade_id}: ${pnl:+.2f}")
        return 0

    try:
        provider = AlpacaProvider(secrets.alpaca_key, secrets.alpaca_secret, stock_feed=secrets.alpaca_stock_feed)
    except ValueError as e:
        tg.send(msg.error(secrets.mode, "configuration", str(e)))
        return 1
    history = CachedBars(provider, secrets.data_dir, feed="sip")
    runner = DailyRunner(cfg, secrets, provider, tg, journal, Clock(), cal.load_skip_days(), history)
    return runner.run(force=getattr(args, "force", False))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["run"]))
