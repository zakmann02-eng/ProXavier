from src.analysis.swings import find_pivots
from src.analysis.trend import BEARISH, BULLISH, NEUTRAL, trend_bias
from src.analysis.zones import Zone, build_zones, merge_zones, zones_above, zones_below
from src.config import ZonesCfg
from tests.helpers import candles


def test_merge_overlapping_zones_sums_strength():
    z = merge_zones([Zone(100, 101, 1, ["PDH"]), Zone(100.5, 102, 2, ["swing"]), Zone(105, 106, 1, ["PDL"])])
    assert len(z) == 2
    assert (z[0].low, z[0].high, z[0].strength) == (100, 102, 3)
    assert z[0].labels == ["PDH", "swing"]


def test_zone_distance_and_contains():
    z = Zone(100, 101, 1)
    assert z.distance(100.5) == 0 and z.distance(99) == 1 and z.distance(103) == 2
    assert z.contains(101.05, tol=0.1) and not z.contains(101.2, tol=0.1)


def test_pivots_found():
    bars = candles([(1, 2, 0.5, 1.5), (1.5, 3, 1, 2.5), (2.5, 5, 2, 4), (4, 4.5, 3, 3.2), (3.2, 3.5, 2.5, 3)])
    piv = find_pivots(bars, 2)
    assert [(p.kind, p.price) for p in piv] == [("H", 5)]


def _wave(points: list[float]) -> list[tuple[float, float, float, float]]:
    """Candles walking linearly through the given turning points."""
    rows = []
    for a, b in zip(points, points[1:]):
        step = (b - a) / 4
        for i in range(4):
            o = a + step * i
            c = o + step
            rows.append((o, max(o, c) + 0.05, min(o, c) - 0.05, c))
    return rows


def test_trend_bullish_bearish_neutral():
    assert trend_bias(candles(_wave([100, 104, 102, 106, 104, 108, 107])), 2)[0] == BULLISH
    assert trend_bias(candles(_wave([108, 104, 106, 102, 104, 100, 101])), 2)[0] == BEARISH
    assert trend_bias(candles(_wave([100, 104, 102, 103, 99, 105, 104])), 2)[0] == NEUTRAL


def test_build_zones_key_levels_and_swing_cluster():
    # Price reverses twice near 104 → a swing zone with strength 2.
    swing = candles(_wave([100, 104, 101, 104.05, 100.5, 102]), minutes=30)
    cfg = ZonesCfg(width_pct=0.2, min_touches=2, max_distance_pct=10)
    zones = build_zones({"high": 103, "low": 99, "close": 101}, {"high": 102, "low": 100.2}, swing, 101.5, cfg)
    labels = {l for z in zones for l in z.labels}
    assert {"PDH", "PDL", "PDC", "ONH", "ONL", "swing"} <= labels
    top = max(zones, key=lambda z: z.high)
    assert top.low <= 104 <= top.high and top.strength >= 2
    # No overlaps after merging.
    for a, b in zip(zones, zones[1:]):
        assert a.high < b.low
    assert zones_above(zones, 101.5)[0].low > 101.5
    assert zones_below(zones, 101.5)[0].high < 101.5


def test_far_zones_filtered():
    cfg = ZonesCfg(width_pct=0.2, max_distance_pct=1.0)
    zones = build_zones({"high": 150, "low": 99, "close": 100}, None, candles([]), 100, cfg)
    assert all("PDH" not in z.labels for z in zones)
