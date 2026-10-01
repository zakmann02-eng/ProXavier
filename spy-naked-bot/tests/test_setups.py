import pandas as pd

from src.analysis import setups as S
from src.config import SetupsCfg
from tests.helpers import candles, flat, zone

PRIOR = {"high": 502.0, "low": 498.0, "close": 500.0}


def ctx(rows, zones, today_start=0, cfg=None, prior=PRIOR):
    return S.Ctx(candles(rows), today_start, zones, prior, cfg or SetupsCfg())


# ── Kangaroo Tail ──

def test_kangaroo_tail_bullish_at_support():
    rows = flat(8) + [(500.1, 500.3, 498.8, 500.2)]   # long lower tail into 499.0–499.4
    s = S.kangaroo_tail(ctx(rows, [zone(499.0, 499.4)]))
    assert s and s.direction == S.LONG and s.name == "Kangaroo Tail"
    assert s.stop < 498.8


def test_kangaroo_tail_bearish_at_resistance():
    rows = flat(8) + [(499.9, 501.4, 499.7, 499.8)]
    s = S.kangaroo_tail(ctx(rows, [zone(500.8, 501.1)]))
    assert s and s.direction == S.SHORT and s.stop > 501.4


def test_kangaroo_tail_rejected_without_zone_or_small_range():
    rows = flat(8) + [(500.1, 500.3, 498.8, 500.2)]
    assert S.kangaroo_tail(ctx(rows, [zone(495, 495.4)])) is None          # tail not at a zone
    small = flat(8, rng=3.0) + [(500.1, 500.3, 498.8, 500.2)]
    assert S.kangaroo_tail(ctx(small, [zone(499.0, 499.4)])) is None        # not larger than recent candles


def test_kangaroo_tail_rejected_when_body_closes_inside_zone():
    rows = flat(8) + [(500.1, 500.3, 498.8, 500.2)]
    assert S.kangaroo_tail(ctx(rows, [zone(499.0, 500.25)])) is None


# ── Big Shadow ──

def test_big_shadow_bullish():
    rows = flat(6) + [(500.0, 500.2, 499.6, 499.7), (499.7, 501.0, 499.3, 500.9)]
    s = S.big_shadow(ctx(rows, [zone(499.2, 499.5)]))
    assert s and s.direction == S.LONG and s.name == "Big Shadow"


def test_big_shadow_bearish():
    rows = flat(6) + [(500.0, 500.5, 499.9, 500.4), (500.4, 500.8, 499.0, 499.1)]
    s = S.big_shadow(ctx(rows, [zone(500.5, 500.9)]))
    assert s and s.direction == S.SHORT


def test_big_shadow_needs_engulfing_and_close_near_extreme():
    no_engulf = flat(6) + [(500.0, 501.5, 499.6, 499.7), (499.7, 501.0, 499.3, 500.9)]
    assert S.big_shadow(ctx(no_engulf, [zone(499.2, 499.5)])) is None
    weak_close = flat(6) + [(500.0, 500.2, 499.6, 499.7), (499.7, 501.0, 499.3, 500.2)]
    assert S.big_shadow(ctx(weak_close, [zone(499.2, 499.5)])) is None


# ── Wammie / Moolah ──

def test_wammie_double_bottom():
    rows = [
        (500.5, 500.6, 499.9, 500.0),
        (500.0, 500.1, 499.05, 499.3),   # first touch 499.05
        (499.3, 500.0, 499.2, 499.9),
        (499.9, 500.4, 499.8, 500.3),    # bounce
        (500.3, 500.4, 499.7, 499.8),
        (499.8, 499.9, 499.1, 499.2),    # second touch 499.1
        (499.2, 500.0, 499.15, 499.9),   # confirmation
    ]
    s = S.wammie_moolah(ctx(rows, [zone(499.0, 499.3)]))
    assert s and s.name == "Wammie" and s.direction == S.LONG
    assert s.stop < 499.05


def test_moolah_double_top():
    rows = [
        (499.5, 500.1, 499.4, 500.0),
        (500.0, 500.95, 499.9, 500.7),   # first touch 500.95
        (500.7, 500.8, 500.0, 500.1),
        (500.1, 500.2, 499.6, 499.7),    # pullback
        (499.7, 500.3, 499.6, 500.2),
        (500.2, 500.9, 500.1, 500.8),    # second touch 500.9
        (500.8, 500.85, 500.0, 500.1),   # confirmation
    ]
    s = S.wammie_moolah(ctx(rows, [zone(500.7, 501.0)]))
    assert s and s.name == "Moolah" and s.direction == S.SHORT


def test_wammie_rejects_meaningful_new_low():
    rows = [
        (500.5, 500.6, 499.9, 500.0), (500.0, 500.1, 499.05, 499.3), (499.3, 500.0, 499.2, 499.9),
        (499.9, 500.4, 499.8, 500.3), (500.3, 500.4, 499.7, 499.8),
        (499.8, 499.9, 498.2, 498.4),    # breaks well below the first low
        (498.4, 499.2, 498.3, 499.1),
    ]
    assert S.wammie_moolah(ctx(rows, [zone(499.0, 499.3)])) is None


# ── Big Belt ──

def test_big_belt_bearish_after_gap_up():
    prior = flat(8, price=501.5, rng=0.3)
    rows = prior + [(503.5, 503.55, 502.2, 502.3)]   # gap above PDH 502, opens at high, closes near low
    s = S.big_belt(ctx(rows, [zone(503.2, 503.6)], today_start=8))
    assert s and s.direction == S.SHORT and s.name == "Big Belt"


def test_big_belt_bullish_after_gap_down():
    prior = flat(8, price=498.5, rng=0.3)
    rows = prior + [(496.5, 497.8, 496.45, 497.7)]
    s = S.big_belt(ctx(rows, [zone(496.3, 496.7)], today_start=8))
    assert s and s.direction == S.LONG


def test_big_belt_only_on_opening_candle_and_with_gap():
    prior = flat(8, price=501.5, rng=0.3)
    rows = prior + [(503.5, 503.6, 503.4, 503.5), (503.5, 503.55, 502.2, 502.3)]
    assert S.big_belt(ctx(rows, [zone(503.2, 503.6)], today_start=8)) is None   # second candle
    no_gap = prior + [(501.9, 501.95, 500.6, 500.7)]
    assert S.big_belt(ctx(no_gap, [zone(501.6, 502.0)], today_start=8)) is None


# ── Last Kiss ──

def test_last_kiss_bullish_retest():
    rows = [
        (499.0, 499.2, 498.8, 499.1),
        (499.1, 499.4, 499.0, 499.3),    # below zone 499.5–499.8
        (499.3, 500.3, 499.2, 500.2),    # breakout close above 499.8
        (500.2, 500.6, 500.1, 500.5),    # clears the zone
        (500.5, 500.6, 500.0, 500.1),
        (500.1, 500.4, 499.75, 500.3),   # kisses the top of the zone, rejects up
    ]
    s = S.last_kiss(ctx(rows, [zone(499.5, 499.8)]))
    assert s and s.direction == S.LONG and s.name == "Last Kiss"


def test_last_kiss_bearish_retest():
    rows = [
        (501.0, 501.2, 500.8, 500.9), (500.9, 501.0, 500.6, 500.7),
        (500.7, 500.8, 499.7, 499.8),    # breakdown below zone 500.2–500.5
        (499.8, 499.9, 499.4, 499.5), (499.5, 500.0, 499.4, 499.9),
        (499.9, 500.25, 499.6, 499.7),   # kiss bottom of zone, reject down
    ]
    s = S.last_kiss(ctx(rows, [zone(500.2, 500.5)]))
    assert s and s.direction == S.SHORT


def test_last_kiss_rejected_if_price_closes_back_inside_after_break():
    rows = [
        (499.0, 499.2, 498.8, 499.1), (499.1, 499.4, 499.0, 499.3), (499.3, 500.3, 499.2, 500.2),
        (500.2, 500.6, 500.1, 500.5),
        (500.5, 500.6, 499.3, 499.4),    # closed back below the zone → failed breakout
        (499.4, 500.4, 499.75, 500.3),
    ]
    assert S.last_kiss(ctx(rows, [zone(499.5, 499.8)])) is None


def test_detect_setups_requires_signal_bar_from_today():
    rows = flat(8) + [(500.1, 500.3, 498.8, 500.2)]
    bars = candles(rows)
    z = [zone(499.0, 499.4)]
    assert S.detect_setups(bars, today_start=8, zones=z, prior_day=PRIOR, cfg=SetupsCfg())
    assert S.detect_setups(bars, today_start=9, zones=z, prior_day=PRIOR, cfg=SetupsCfg()) == []
