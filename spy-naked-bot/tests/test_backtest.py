from datetime import date

from backtest import engine
from backtest.pricing import bs_price, synthetic_chain
from src.config import Config
from src.data import calendar as cal
from tests.helpers import synth_minutes


def test_bs_price_sanity():
    c = bs_price(500, 500, 1 / 252, 0.15, "C")
    p = bs_price(500, 500, 1 / 252, 0.15, "P")
    assert 1.0 < c < 2.5 and abs(c - p) < 1e-9          # ATM call = put with r = 0
    assert bs_price(500, 490, 0, 0.15, "C") == 10


def test_synthetic_chain_monotonic():
    from datetime import datetime
    ch = synthetic_chain(500.3, datetime(2025, 3, 4, 9, 40), date(2025, 3, 4), 0, 0.15, 0.06)
    calls = sorted([q for q in ch if q.right == "C"], key=lambda q: q.strike)
    assert all(a.mid >= b.mid for a, b in zip(calls, calls[1:]))
    assert all(q.ask > q.bid >= 0 for q in ch)


def _loose_cfg() -> Config:
    c = Config()
    c.trend.neutral_policy = "strongest_only"
    c.trend.neutral_min_strength = 1
    c.checklist.min_reward_risk = 0.5
    c.checklist.no_target_policy = "measured"
    c.checklist.zone_proximity_pct = 0.5
    c.setups.zone_tolerance_pct = 0.2
    c.trade.max_bid_ask_ratio = 1.0
    c.survival.starting_capital = 1000
    return c


def test_engine_runs_end_to_end_on_synthetic_data():
    days = cal.trading_days(date(2025, 1, 2), date(2025, 3, 31))
    bars = synth_minutes(days)
    test_days = days[25:]
    res = engine.run(bars, test_days, _loose_cfg(), {})
    assert sum(res.day_outcomes.values()) == len(test_days)
    assert res.trades, res.day_outcomes
    for t in res.trades:
        assert t.entry_time < t.exit_time
        assert t.exit_time.hour < 12 or (t.exit_time.hour, t.exit_time.minute) == (12, 0)
        assert t.exit_price >= 0
        assert t.pnl > -t.entry_price * 100 - 1          # can't lose more than the debit + fees


def test_engine_respects_skip_list():
    days = cal.trading_days(date(2025, 1, 2), date(2025, 2, 28))
    bars = synth_minutes(days)
    skip = {"FOMC": {days[-1]}}
    res = engine.run(bars, days[-3:], _loose_cfg(), skip)
    assert res.day_outcomes.get("skip-list day") == 1
