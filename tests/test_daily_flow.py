"""End-to-end daily flow with a fake clock and a synthetic data feed (no network)."""
from datetime import date, datetime, timedelta

import pandas as pd

from backtest.pricing import quote, synthetic_chain
from src.alerts.telegram import Telegram
from src.config import Secrets
from src.data import calendar as cal
from src.data.bars import ET
from src.data.provider import DataProvider, parse_occ
from src.journal.db import Journal
from src.main import DailyRunner, at
from tests.helpers import synth_minutes
from tests.test_backtest import _loose_cfg


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self):
        return self.t

    def sleep_until(self, t):
        self.t = max(self.t, t)

    def sleep(self, s):
        self.t += timedelta(seconds=s)


class FakeProvider(DataProvider):
    def __init__(self, bars: pd.DataFrame, clock: FakeClock):
        self.bars, self.clock = bars, clock

    def _visible(self):
        cutoff = pd.Timestamp(self.clock.now()) - pd.Timedelta(minutes=1)
        return self.bars[self.bars.index <= cutoff]

    def get_minute_bars(self, symbol, start, end):
        b = self._visible()
        return b[(b.index >= pd.Timestamp(start)) & (b.index <= pd.Timestamp(end))]

    def get_latest_price(self, symbol):
        return float(self._visible()["close"].iloc[-1])

    def get_option_chain(self, underlying, expiry):
        dte = len([d for d in cal.trading_days(self.clock.now().date(), expiry)]) - 1
        return synthetic_chain(self.get_latest_price("SPY"), self.clock.now(), expiry, dte, 0.15, 0.06)

    def get_option_quotes(self, symbols):
        spot, now = self.get_latest_price("SPY"), self.clock.now()
        out = {}
        for s in symbols:
            _, exp, right, k = parse_occ(s)
            dte = len(cal.trading_days(now.date(), exp)) - 1
            from backtest.pricing import years_to_expiry
            out[s] = quote(spot, k, right, years_to_expiry(now, exp, dte), 0.15, exp, 0.06)
        return out


DAYS = cal.trading_days(date(2025, 1, 2), date(2025, 3, 31))
BARS = synth_minutes(DAYS, seed=3)


def make_runner(day, tmp_path, skip=None, start="07:55", db="journal"):
    clock = FakeClock(at(day, start))
    tg = Telegram("", "")
    j = Journal(tmp_path / f"{db}-{day}.sqlite3")
    r = DailyRunner(_loose_cfg(), Secrets(), FakeProvider(BARS[BARS.index.date <= day], clock), tg, j,
                    clock, skip or {})
    return r, tg, j


def test_full_day_with_a_trade(tmp_path):
    traded = 0
    for day in DAYS[20:45]:
        r, tg, j = make_runner(day, tmp_path)
        assert r.run() == 0, tg.sent
        assert "Pre-market brief" in tg.sent[0]
        status = j.day_status(day)
        assert status in ("traded", "no_trade"), tg.sent
        if status == "traded":
            traded += 1
            text = "\n".join(tg.sent)
            assert "ENTER" in text and "EXIT" in text and "MAX LIMIT" in text and "Daily summary" in text
            assert not j.unclosed_trades() and len(j.closed_trades()) == 1
        else:
            assert any("No trade today" in m for m in tg.sent)
        if day.weekday() == 4:
            assert any("Weekly report" in m for m in tg.sent)
    assert traded > 0


def test_skip_day_and_wake_window(tmp_path):
    day = DAYS[30]
    r, tg, j = make_runner(day, tmp_path, skip={"FOMC": {day}})
    assert r.run() == 0
    assert j.day_status(day) == "skipped" and "FOMC" in tg.sent[0]

    r, tg, j = make_runner(day, tmp_path, start="06:55", db="other")   # wrong DST cron slot → exit silently
    assert r.run() == 0 and tg.sent == [] and j.day_status(day) is None


def test_data_failure_alerts_and_does_not_trade(tmp_path):
    day = DAYS[30]
    r, tg, j = make_runner(day, tmp_path)
    r.provider.bars = BARS[BARS.index.date < day]   # nothing for today → candle missing
    assert r.run() == 1
    assert j.day_status(day) == "error" and any("ERROR" in m for m in tg.sent)
    assert j.trades_on(day) == 0


def test_survival_pause_skips_day(tmp_path):
    day = next(d for d in DAYS[25:] if d.weekday() == 3
               and len([x for x in DAYS if x < d and x.isocalendar()[1] == d.isocalendar()[1]]) >= 2)
    prev = [d for d in DAYS if d < day and d.isocalendar()[1] == day.isocalendar()[1]]
    r, tg, j = make_runner(day, tmp_path)
    for d in prev[-2:]:
        tid = j.open_trade(day=d, mode="paper", qty=1, entry_price=1.0, legs=[])
        j.close_trade(tid, at(d, "10:00"), 0.5, "hard stop", 0.2, -50.2, -50.2)
    assert r.run() == 0
    assert j.day_status(day) == "skipped" and "consecutive losses" in tg.sent[0]
