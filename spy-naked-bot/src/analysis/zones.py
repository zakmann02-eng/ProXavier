"""Support/resistance zones built pre-market. Zones are price bands, not lines."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.analysis.swings import find_pivots
from src.config import ZonesCfg


@dataclass
class Zone:
    low: float
    high: float
    strength: int
    labels: list[str] = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def contains(self, price: float, tol: float = 0.0) -> bool:
        return self.low - tol <= price <= self.high + tol

    def distance(self, price: float) -> float:
        """0 inside the band, otherwise the gap to the nearest edge."""
        if price < self.low:
            return self.low - price
        if price > self.high:
            return price - self.high
        return 0.0

    def label(self) -> str:
        return "/".join(dict.fromkeys(self.labels)) or "zone"

    def __str__(self) -> str:
        return f"{self.low:.2f}–{self.high:.2f} [{self.label()}, str {self.strength}]"


def _band(center: float, width: float, strength: int, label: str) -> Zone:
    return Zone(center - width / 2, center + width / 2, strength, [label])


def merge_zones(zones: list[Zone]) -> list[Zone]:
    out: list[Zone] = []
    for z in sorted(zones, key=lambda z: z.low):
        if out and z.low <= out[-1].high:
            cur = out[-1]
            cur.high = max(cur.high, z.high)
            cur.strength += z.strength
            cur.labels.extend(z.labels)
        else:
            out.append(Zone(z.low, z.high, z.strength, list(z.labels)))
    return out


def swing_clusters(bars: pd.DataFrame, cfg: ZonesCfg, width: float) -> list[Zone]:
    """Cluster swing highs/lows whose prices sit within one zone width of each other.
    Strength = number of reversals in the cluster."""
    pivots = sorted(find_pivots(bars, cfg.pivot_strength), key=lambda p: p.price)
    zones: list[Zone] = []
    group: list[float] = []
    for p in pivots:
        if group and p.price - group[0] > width:
            zones.append(_cluster_zone(group, width))
            group = []
        group.append(p.price)
    if group:
        zones.append(_cluster_zone(group, width))
    return zones


def _cluster_zone(prices: list[float], width: float) -> Zone:
    mean = sum(prices) / len(prices)
    low = min(mean - width / 2, min(prices))
    high = max(mean + width / 2, max(prices))
    return Zone(low, high, len(prices), ["swing"])


def build_zones(
    prior_day: dict[str, float],
    overnight: dict[str, float] | None,
    swing_bars: pd.DataFrame,
    last_price: float,
    cfg: ZonesCfg,
) -> list[Zone]:
    """prior_day: {high, low, close}; overnight: {high, low} or None;
    swing_bars: RTH bars of the last N sessions at the swing timeframe."""
    width = last_price * cfg.width_pct / 100
    k = cfg.key_level_strength
    cands = [
        _band(prior_day["high"], width, k, "PDH"),
        _band(prior_day["low"], width, k, "PDL"),
        _band(prior_day["close"], width, k, "PDC"),
    ]
    if overnight:
        cands += [_band(overnight["high"], width, k, "ONH"), _band(overnight["low"], width, k, "ONL")]
    if not swing_bars.empty:
        cands += swing_clusters(swing_bars, cfg, width)

    key_labels = {"PDH", "PDL", "PDC", "ONH", "ONL"}
    merged = merge_zones(cands)
    max_dist = last_price * cfg.max_distance_pct / 100
    return [
        z for z in merged
        if (z.strength >= cfg.min_touches or key_labels.intersection(z.labels))
        and z.distance(last_price) <= max_dist
    ]


def zones_above(zones: list[Zone], price: float) -> list[Zone]:
    return sorted([z for z in zones if z.low > price], key=lambda z: z.low)


def zones_below(zones: list[Zone], price: float) -> list[Zone]:
    return sorted([z for z in zones if z.high < price], key=lambda z: -z.high)
