"""Day-by-day simulation using the SAME analysis/checklist/contract/exit code as the live bot."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from backtest.pricing import synthetic_chain, quote, years_to_expiry
from src.analysis.setups import LONG
from src.config import Config, parse_hhmm
from src.data import calendar as cal
from src.data.bars import ET, daily_from_intraday
from src.risk import survival
from src.risk.exits import ExitManager
from src.strategy.contracts import build_trade
from src.strategy.session import DataError, candle_frame, entry_candle_closes, prepare_day, scan_candle


@dataclass
class BtTrade:
    day: date
    setup: str
    direction: str
    zone: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl: float
    pnl_pct: float
    spy_entry: float
    rr: float


@dataclass
class BtResult:
    trades: list[BtTrade] = field(default_factory=list)
    day_outcomes: dict[str, int] = field(default_factory=dict)
    halted_on: date | None = None

    def count(self, key: str) -> None:
        self.day_outcomes[key] = self.day_outcomes.get(key, 0) + 1


def _at(day: date, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp.combine(day, parse_hhmm(hhmm)).tz_localize(ET)


def realized_vol(daily_closes: pd.Series, window: int) -> float | None:
    rets = (daily_closes / daily_closes.shift(1)).apply(math.log).dropna().iloc[-window:]
    if len(rets) < max(5, window // 2):
        return None
    return float(rets.std() * math.sqrt(252))


def run(minute_bars: pd.DataFrame, days: list[date], cfg: Config, skip_days: dict[str, set[date]],
        use_survival: bool = False, compound: bool = False) -> BtResult:
    res = BtResult()
    by_day = {d: g for d, g in minute_bars.groupby(minute_bars.index.date)}
    all_days = sorted(by_day)
    daily = daily_from_intraday(minute_bars)
    daily_closes = pd.Series(daily["close"].to_numpy(), index=[ts.date() for ts in daily.index])
    lookback = max(cfg.zones.swing_sessions, cfg.trend.sessions) + 1
    capital = cfg.survival.starting_capital
    calendar_days = cal.trading_days(all_days[0], all_days[-1] + timedelta(days=10)) if all_days else []

    for day in days:
        if day not in by_day:
            res.count("no data")
            continue
        if cal.skip_reason(day, skip_days, cfg.session.skip_day_types):
            res.count("skip-list day")
            continue
        if cfg.session.skip_half_days and cal.is_half_day(day):
            res.count("half day")
            continue
        closed = [(t.day, t.pnl) for t in res.trades]
        if use_survival:
            st = survival.check(day, closed, cfg.survival.starting_capital, False, cfg.survival)
            if not st.can_trade:
                if st.halted:
                    res.halted_on = res.halted_on or day
                res.count("halted" if st.halted else "paused")
                continue
        prior_days = [d for d in all_days if d < day][-lookback:]
        if len(prior_days) < lookback - 1:
            res.count("insufficient history")
            continue
        window = pd.concat([by_day[d] for d in prior_days] + [by_day[day]])
        try:
            plan = prepare_day(day, window, cfg)
        except DataError:
            res.count("data error")
            continue
        sigma = realized_vol(daily_closes[daily_closes.index < day], cfg.backtest.rv_window_days)
        if sigma is None:
            res.count("insufficient history")
            continue
        sigma = max(sigma * cfg.backtest.iv_rv_ratio, cfg.backtest.min_iv)
        budget = survival.equity_now(closed, capital) if compound else capital

        trade = _scan_day(day, window, plan, cfg, sigma, budget, calendar_days)
        if trade is None:
            res.count("no trade")
        elif isinstance(trade, str):
            res.count(trade)
        else:
            res.trades.append(trade)
            res.count("traded")
    return res


def _scan_day(day, window, plan, cfg: Config, sigma, budget, calendar_days):
    future = [d for d in calendar_days if d >= day]
    if cfg.trade.dte >= len(future):
        return "no expiry"
    expiry = future[cfg.trade.dte]
    rejected = None
    for close_t in entry_candle_closes(day, cfg):
        frame, start = candle_frame(window, day, close_t, cfg)
        if len(frame) <= start:
            continue
        best, _ = scan_candle(plan, frame, start, 0, cfg)
        if best is None:
            continue
        spot = float(frame["close"].iloc[-1])
        chain = synthetic_chain(spot, close_t, expiry, cfg.trade.dte, sigma, cfg.backtest.model_spread_fraction)
        tp = build_trade(chain, best.setup.direction, spot, budget, cfg.trade)
        if not tp.ok:
            rejected = "rejected: " + tp.reject_reason.split(":")[0].split(" ")[0]
            continue
        return _simulate(day, window, best, tp, close_t, expiry, sigma, cfg)
    return rejected


def _simulate(day, window, chk, tp, entry_t, expiry, sigma, cfg: Config) -> BtTrade:
    s = chk.setup
    tf = cfg.session.candle_minutes
    time_stop = _at(day, cfg.session.time_stop).to_pydatetime()
    em = ExitManager(s.direction, tp.limit, s.stop, chk.target, time_stop, cfg.exits)
    days_after = cfg.trade.dte
    legs = [(l.quote.strike, l.quote.right, 1 if l.side == "buy" else -1) for l in tp.legs]
    frac = cfg.backtest.model_spread_fraction

    def mark_at(spot: float, now: datetime) -> tuple[float, float]:
        t = years_to_expiry(now, expiry, days_after)
        mid = width = 0.0
        for k, right, sign in legs:
            q = quote(spot, k, right, t, sigma, expiry, frac)
            mid += sign * q.mid
            width += q.ask - q.bid
        return mid, width

    path = window[(window.index >= pd.Timestamp(entry_t)) & (window.index < pd.Timestamp(time_stop) + pd.Timedelta(minutes=1))]
    ev = None
    mark = width = 0.0
    now = entry_t
    for ts, bar in path.iterrows():
        now = (ts + pd.Timedelta(minutes=1)).to_pydatetime()
        # Adverse extreme first (conservative), then the close.
        adverse = bar["low"] if s.direction == LONG else bar["high"]
        for spot in (float(adverse), float(bar["close"])):
            mark, width = mark_at(spot, now)
            evs = [e for e in em.on_mark(now, mark, spot) if e.kind == "exit"]
            if evs:
                ev = evs[0]
                break
        if ev:
            break
        if (now - _at(day, "09:30").to_pydatetime()).total_seconds() / 60 % tf == 0:
            ev = em.on_bar_close(now, float(bar["close"]))
            if ev:
                mark, width = mark_at(float(bar["close"]), now)
                break
    if ev is None:  # data ended before the time stop
        ev = em._exit("time stop", "end of data")
    exit_price = max(0.0, mark - cfg.trade.limit_buffer_fraction * width)
    if tp.max_value is not None:
        exit_price = min(exit_price, tp.max_value)
    pnl = (exit_price - tp.limit) * 100 * tp.qty - tp.fees
    pnl_pct = pnl / (tp.limit * 100 * tp.qty) * 100
    return BtTrade(day, s.name, s.direction, s.zone.label(), entry_t, now, tp.limit, exit_price,
                   ev.reason, pnl, pnl_pct, s.entry, chk.reward_risk or 0.0)
