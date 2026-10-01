from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from src.analysis.setups import LONG, SHORT, Setup
from src.analysis.trend import BEARISH, BULLISH, NEUTRAL
from src.config import ChecklistCfg, ExitsCfg, SurvivalCfg, TradeCfg, TrendCfg
from src.journal.db import Journal
from src.journal.stats import compute_stats
from src.risk import survival
from src.risk.exits import (BREAKEVEN_STOP, HARD_STOP, STRUCTURAL, TAKE_PROFIT, TARGET_ZONE, TIME_STOP,
                            ExitManager)
from src.strategy.checklist import evaluate, pick_best
from src.strategy.contracts import OptionQuote, build_trade, pick_expiry
from tests.helpers import zone

TS = pd.Timestamp("2025-03-04 09:40", tz="America/New_York")


def setup(direction=LONG, entry=500.0, stop=499.5, z=None):
    return Setup("Kangaroo Tail", direction, z or zone(499.6, 499.95, 2), TS, entry, stop)


# ── checklist ──

def test_checklist_passes_with_room_to_target():
    zones = [zone(499.6, 499.95, 2), zone(501.5, 501.8, 1)]
    r = evaluate(setup(), zones, BULLISH, 0, ChecklistCfg(), TrendCfg())
    assert r.passed, r.failures()
    assert r.target == 501.5 and r.reward_risk == pytest.approx(3.0)


def test_checklist_fails_rr_bias_and_trade_count():
    zones = [zone(499.6, 499.95, 2), zone(500.6, 500.8, 1)]
    r = evaluate(setup(), zones, BULLISH, 0, ChecklistCfg(), TrendCfg())
    assert not r.passed and any(f.startswith("4") for f in r.failures())
    zones = [zone(499.6, 499.95, 2), zone(502, 502.3, 1)]
    assert not evaluate(setup(), zones, BEARISH, 0, ChecklistCfg(), TrendCfg()).passed
    assert not evaluate(setup(), zones, BULLISH, 1, ChecklistCfg(), TrendCfg()).passed


def test_checklist_neutral_bias_needs_strong_zone():
    zones = [zone(499.6, 499.95, 2), zone(502, 502.3, 1)]
    assert not evaluate(setup(), zones, NEUTRAL, 0, ChecklistCfg(), TrendCfg()).passed
    strong = zone(499.6, 499.95, 4)
    assert evaluate(setup(z=strong), [strong, zones[1]], NEUTRAL, 0, ChecklistCfg(), TrendCfg()).passed
    assert not evaluate(setup(z=strong), [strong, zones[1]], NEUTRAL, 0, ChecklistCfg(),
                        TrendCfg(neutral_policy="skip")).passed


def test_checklist_close_must_be_near_zone_and_short_target_below():
    far = setup(entry=500.0, z=zone(498.0, 498.3, 2), stop=499.5)
    assert not evaluate(far, [far.zone, zone(502, 502.3)], BULLISH, 0, ChecklistCfg(), TrendCfg()).passed
    s = setup(SHORT, entry=500.0, stop=500.5, z=zone(500.05, 500.4, 2))
    r = evaluate(s, [s.zone, zone(498.5, 498.8, 1)], BEARISH, 0, ChecklistCfg(), TrendCfg())
    assert r.passed and r.target == 498.8


def test_no_target_policy_measured():
    r = evaluate(setup(), [zone(499.6, 499.95)], BULLISH, 0, ChecklistCfg(no_target_policy="measured"), TrendCfg())
    assert r.passed and r.target == pytest.approx(501.0)
    assert not evaluate(setup(), [zone(499.6, 499.95)], BULLISH, 0, ChecklistCfg(), TrendCfg()).passed


def test_pick_best_prefers_strongest_zone():
    a = evaluate(setup(z=zone(499.6, 499.95, 2)), [zone(499.6, 499.95, 2), zone(502, 502.2)], BULLISH, 0,
                 ChecklistCfg(), TrendCfg())
    b = evaluate(setup(z=zone(499.6, 499.95, 5)), [zone(499.6, 499.95, 5), zone(502, 502.2)], BULLISH, 0,
                 ChecklistCfg(), TrendCfg())
    assert pick_best([a, b]) is b


# ── contracts ──

EXP = date(2025, 3, 4)


def chain(spot=500.0, half_width=0.02):
    """Smooth toy chain: intrinsic + time value decaying away from the money."""
    import math
    out = []
    for k in range(495, 506):
        tv = 1.5 * math.exp(-abs(k - spot) / 2)
        for right, intrinsic in (("C", max(spot - k, 0)), ("P", max(k - spot, 0))):
            px = intrinsic + tv
            out.append(OptionQuote(f"SPY250304{right}{k * 1000:08d}", EXP, float(k), right,
                                   round(px - half_width, 2), round(px + half_width, 2)))
    return out


def test_bull_call_spread_built_within_budget():
    plan = build_trade(chain(500.2), LONG, 500.2, 100.0, TradeCfg())
    assert plan.ok, plan.reject_reason
    assert [(l.side, l.quote.strike, l.quote.right) for l in plan.legs] == [("buy", 500, "C"), ("sell", 502, "C")]
    long_q, short_q = plan.legs[0].quote, plan.legs[1].quote
    assert plan.mid == pytest.approx(long_q.mid - short_q.mid)
    assert plan.limit == pytest.approx(round(plan.mid + 0.25 * plan.natural_width, 2))
    assert plan.qty == 1 and plan.cost == pytest.approx(plan.limit * 100 + 0.10)
    assert plan.cost <= 100 and plan.max_value == 2.0


def test_put_spread_and_budget_rejection():
    plan = build_trade(chain(), SHORT, 500.0, 1000.0, TradeCfg())
    assert plan.ok and [(l.quote.strike, l.quote.right) for l in plan.legs] == [(500, "P"), (498, "P")]
    poor = build_trade(chain(), SHORT, 500.0, 50.0, TradeCfg())
    assert not poor.ok and "budget" in poor.reject_reason


def test_single_leg_fallback_and_liquidity_filter():
    plan = build_trade(chain(), LONG, 500.0, 1000.0, TradeCfg(allow_spreads=False))
    assert plan.ok and len(plan.legs) == 1 and plan.max_value is None
    bad = build_trade(chain(half_width=0.25), LONG, 500.0, 1000.0, TradeCfg())
    assert not bad.ok and "illiquid" in bad.reject_reason


def test_pick_expiry():
    days = [date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6)]
    assert pick_expiry(days, date(2025, 3, 4), 0, days) == date(2025, 3, 4)
    assert pick_expiry(days, date(2025, 3, 4), 1, days) == date(2025, 3, 5)
    assert pick_expiry(days[:1], date(2025, 3, 4), 1, days) is None


# ── exits ──

T0 = datetime(2025, 3, 4, 9, 45)
STOP_T = datetime(2025, 3, 4, 12, 0)


def em(direction=LONG, target=502.0):
    return ExitManager(direction, 1.0, 499.0, target, STOP_T, ExitsCfg())


def test_take_profit_and_target_zone():
    m = em()
    assert m.on_mark(T0, 1.2, 500.5) == []
    assert m.on_mark(T0, 1.6, 500.5)[0].reason == TAKE_PROFIT
    assert em().on_mark(T0, 1.1, 502.1)[0].reason == TARGET_ZONE
    assert em(SHORT, target=498.0).on_mark(T0, 1.1, 497.9)[0].reason == TARGET_ZONE


def test_hard_stop_breakeven_and_time_stop():
    assert em().on_mark(T0, 0.59, 500)[0].reason == HARD_STOP
    m = em()
    ev = m.on_mark(T0, 1.31, 500.5)
    assert ev[0].kind == "breakeven" and m.stop_price == 1.0
    assert m.on_mark(T0, 0.99, 500)[0].reason == BREAKEVEN_STOP
    assert em().on_mark(STOP_T, 1.1, 500)[0].reason == TIME_STOP


def test_structural_stop_on_close_only():
    m = em()
    assert m.on_bar_close(T0, 499.1) is None
    assert m.on_bar_close(T0, 498.9).reason == STRUCTURAL
    assert m.on_mark(T0, 2.0, 600) == []          # closed: nothing more fires
    s = em(SHORT)
    s.structural_stop = 501.0
    assert s.on_bar_close(T0, 501.2).reason == STRUCTURAL


# ── survival ──

def test_two_losses_pause_rest_of_week():
    cfg = SurvivalCfg()
    tue, wed, thu, next_mon = date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6), date(2025, 3, 10)
    trades = [(tue, -10.0), (wed, -10.0)]
    assert not survival.check(thu, trades, 100, False, cfg).can_trade
    assert survival.check(next_mon, trades, 100, False, cfg).can_trade
    assert survival.check(thu, [(tue, -10.0), (wed, 5.0)], 100, False, cfg).can_trade


def test_drawdown_halt_and_resume_peak_reset():
    cfg = SurvivalCfg()
    d = date(2025, 3, 3)
    trades = [(d, 50.0), (d + timedelta(days=7), -20), (d + timedelta(days=14), 1), (d + timedelta(days=21), -30)]
    st = survival.check(date(2025, 4, 1), trades, 100, False, cfg)   # peak 150 → 101 = 32.7% down
    assert not st.can_trade and st.halted
    assert survival.check(date(2025, 4, 1), trades, 100, False, cfg, peak_from=len(trades)).can_trade
    assert survival.check(date(2025, 4, 1), [], 100, True, cfg).halted


# ── stats + journal ──

def test_stats():
    d = date(2025, 1, 1)
    st = compute_stats([(d, 30, 30), (d + timedelta(days=30), -20, -20), (d + timedelta(days=61), 50, 40)], 100)
    assert st.trades == 3 and st.wins == 2 and st.win_rate == pytest.approx(66.67, 0.01)
    assert st.avg_win == 40 and st.avg_loss == -20 and st.expectancy == pytest.approx(20)
    assert st.max_drawdown == 20 and st.max_drawdown_pct == pytest.approx(20 / 130 * 100)
    assert st.final_equity == 160


def test_journal_roundtrip(tmp_path):
    j = Journal(tmp_path / "j.sqlite3")
    d = date(2025, 3, 4)
    j.set_day(d, "running")
    tid = j.open_trade(day=d, mode="paper", setup="Big Shadow", direction=LONG, legs=[{"symbol": "X"}],
                       qty=1, entry_price=1.0, entry_time=T0)
    assert j.trades_on(d) == 1 and len(j.unclosed_trades()) == 1
    j.close_trade(tid, T0, 1.5, TAKE_PROFIT, 0.2, 49.8, 49.8)
    j.set_day(d, "traded", "ok")
    assert j.day_status(d) == "traded" and j.closed_trades()[0].pnl == 49.8 and not j.unclosed_trades()
    j.set_state("halted", "1")
    assert j.get_state("halted") == "1"
