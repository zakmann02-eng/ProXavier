"""Per-day analysis shared by the live bot and the backtester, so both run identical logic."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

import pandas as pd

from src.analysis.setups import Setup, detect_setups
from src.analysis.trend import trend_bias
from src.analysis.zones import Zone, build_zones
from src.config import Config, parse_hhmm
from src.data.bars import ET, daily_from_intraday, premarket, resample, rth, sessions
from src.strategy.checklist import CheckResult, evaluate, pick_best


class DataError(RuntimeError):
    """Required data is missing — the bot must alert and NOT trade."""


@dataclass
class DayPlan:
    day: date
    zones: list[Zone]
    bias: str
    bias_reason: str
    prior_day: dict[str, float]
    overnight: dict[str, float] | None
    last_price: float
    notes: list[str] = field(default_factory=list)

    def resistance(self) -> list[Zone]:
        return sorted([z for z in self.zones if z.mid > self.last_price], key=lambda z: z.low)

    def support(self) -> list[Zone]:
        return sorted([z for z in self.zones if z.mid <= self.last_price], key=lambda z: -z.high)


def prepare_day(day: date, minute_bars: pd.DataFrame, cfg: Config) -> DayPlan:
    """minute_bars: 1-minute bars (incl. extended hours) covering the prior sessions and
    today's pre-market up to now. Nothing at or after today's 09:30 is used."""
    cutoff = pd.Timestamp.combine(day, time(9, 30)).tz_localize(ET)
    hist = minute_bars[minute_bars.index < cutoff]
    hist_rth = rth(hist)
    days = sessions(hist_rth)
    if not days:
        raise DataError("no prior-session bars")
    prior = days[-1]
    daily = daily_from_intraday(hist_rth[hist_rth.index.date == prior])
    p = daily.iloc[-1]
    prior_day = {"high": float(p["high"]), "low": float(p["low"]), "close": float(p["close"])}

    pre = premarket(hist, day)
    overnight = {"high": float(pre["high"].max()), "low": float(pre["low"].min())} if not pre.empty else None
    last_price = float(hist["close"].iloc[-1])

    swing_days = set(days[-cfg.zones.swing_sessions:])
    swing = resample(hist_rth[[d in swing_days for d in hist_rth.index.date]], cfg.zones.swing_timeframe_minutes)
    zones = build_zones(prior_day, overnight, swing, last_price, cfg.zones)

    trend_days = set(days[-cfg.trend.sessions:])
    tbars = resample(hist_rth[[d in trend_days for d in hist_rth.index.date]], cfg.trend.timeframe_minutes)
    bias, reason = trend_bias(tbars, cfg.trend.pivot_strength)

    notes = []
    if overnight is None:
        notes.append("no overnight/pre-market bars — ONH/ONL zones skipped")
    return DayPlan(day, zones, bias, reason, prior_day, overnight, last_price, notes)


def context_lookback(cfg: Config) -> int:
    s = cfg.setups
    return max(s.context_bars, s.big_shadow.lookback, s.wammie_moolah.lookback, s.last_kiss.lookback) + 2


def candle_frame(minute_bars: pd.DataFrame, day: date, upto: datetime, cfg: Config) -> tuple[pd.DataFrame, int]:
    """Closed N-minute candles: the prior session's tail (context) + today's candles that
    have fully closed by `upto`. Returns (bars, position of today's first candle)."""
    tf = cfg.session.candle_minutes
    r = rth(minute_bars)
    today = r[(r.index.date == day) & (r.index < upto)]
    prior = r[r.index.date < day]
    prior_tf = resample(prior.loc[prior.index.date == prior.index.date.max()], tf) if not prior.empty else prior
    today_tf = resample(today, tf)
    # Drop a trailing candle that hasn't closed yet.
    if not today_tf.empty and today_tf.index[-1] + pd.Timedelta(minutes=tf) > upto:
        today_tf = today_tf.iloc[:-1]
    ctx = prior_tf.iloc[-context_lookback(cfg):] if not prior_tf.empty else prior_tf
    bars = pd.concat([ctx, today_tf]) if not ctx.empty else today_tf
    return bars, len(ctx)


def scan_candle(plan: DayPlan, bars: pd.DataFrame, today_start: int, trades_today: int,
                cfg: Config) -> tuple[CheckResult | None, list[CheckResult]]:
    setups: list[Setup] = detect_setups(bars, today_start, plan.zones, plan.prior_day, cfg.setups)
    results = [evaluate(s, plan.zones, plan.bias, trades_today, cfg.checklist, cfg.trend) for s in setups]
    return pick_best(results), results


def entry_candle_closes(day: date, cfg: Config) -> list[datetime]:
    """Close times of candles that may trigger an entry (e.g. 9:35, 9:40, 9:45, 9:50)."""
    tf = pd.Timedelta(minutes=cfg.session.candle_minutes)
    start = pd.Timestamp.combine(day, parse_hhmm(cfg.session.entry_start)).tz_localize(ET)
    end = pd.Timestamp.combine(day, parse_hhmm(cfg.session.entry_end)).tz_localize(ET)
    out, t = [], start + tf
    while t <= end:
        out.append(t.to_pydatetime())
        t += tf
    return out
