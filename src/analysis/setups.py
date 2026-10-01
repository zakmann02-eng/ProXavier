"""Naked-trading setup detection on closed candles.

Every detector looks only at the LAST bar of `bars` as the signal/confirmation candle and
uses earlier bars as context. `bars` may include the prior session's closing bars so that
"larger than recent candles" has context at 09:35. All thresholds come from SetupsCfg.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from src.analysis.zones import Zone
from src.config import SetupsCfg

LONG, SHORT = "long", "short"


@dataclass
class Setup:
    name: str
    direction: str            # long | short
    zone: Zone
    bar_ts: pd.Timestamp
    entry: float              # signal candle close (SPY reference price)
    stop: float               # structural stop level on SPY
    note: str = ""

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


@dataclass
class Ctx:
    bars: pd.DataFrame
    today_start: int          # position of today's first RTH bar in `bars`
    zones: list[Zone]
    prior_day: dict[str, float]
    cfg: SetupsCfg

    @property
    def last(self) -> pd.Series:
        return self.bars.iloc[-1]

    @property
    def tol(self) -> float:
        return float(self.last["close"]) * self.cfg.zone_tolerance_pct / 100

    @property
    def buffer(self) -> float:
        return float(self.last["close"]) * self.cfg.stop_buffer_pct / 100

    def avg_range(self, n: int) -> float | None:
        ctx = self.bars.iloc[-1 - n:-1]
        if ctx.empty:
            return None
        return float((ctx["high"] - ctx["low"]).mean())


def _rng(bar) -> float:
    return float(bar["high"] - bar["low"])


def _strongest(zones: list[Zone]) -> Zone | None:
    return max(zones, key=lambda z: z.strength) if zones else None


def _support_hit(bar, zones: list[Zone], tol: float) -> list[Zone]:
    """Zones acting as support for this bar: its low reached the band and it closed above the mid."""
    return [z for z in zones if bar["low"] <= z.high + tol and bar["high"] >= z.low - tol
            and bar["close"] >= z.mid]


def _resistance_hit(bar, zones: list[Zone], tol: float) -> list[Zone]:
    return [z for z in zones if bar["high"] >= z.low - tol and bar["low"] <= z.high + tol
            and bar["close"] <= z.mid]


# ── Kangaroo Tail ───────────────────────────────────────────────────────────────

def kangaroo_tail(c: Ctx) -> Setup | None:
    p = c.cfg.kangaroo_tail
    b = c.last
    r = _rng(b)
    avg = c.avg_range(c.cfg.context_bars)
    if r <= 0 or avg is None or r < p.min_range_mult * avg:
        return None
    o, cl, h, lo = (float(b[k]) for k in ("open", "close", "high", "low"))
    body_lo, body_hi = min(o, cl), max(o, cl)
    end = p.body_zone_fraction * r

    # Bullish: long lower tail, body in the top part, tail pokes into support, body back above it.
    if body_lo - lo >= p.min_tail_ratio * r and body_lo >= h - end:
        zs = [z for z in c.zones if lo <= z.high + c.tol and body_lo >= z.high]
        z = _strongest(zs)
        if z:
            return Setup("Kangaroo Tail", LONG, z, b.name, cl, lo - c.buffer,
                         f"tail {body_lo - lo:.2f} of range {r:.2f}")
    # Bearish: long upper tail, body in the bottom part, tail pokes into resistance.
    if h - body_hi >= p.min_tail_ratio * r and body_hi <= lo + end:
        zs = [z for z in c.zones if h >= z.low - c.tol and body_hi <= z.low]
        z = _strongest(zs)
        if z:
            return Setup("Kangaroo Tail", SHORT, z, b.name, cl, h + c.buffer,
                         f"tail {h - body_hi:.2f} of range {r:.2f}")
    return None


# ── Big Shadow ──────────────────────────────────────────────────────────────────

def big_shadow(c: Ctx) -> Setup | None:
    p = c.cfg.big_shadow
    if len(c.bars) < p.lookback + 1:
        return None
    b, prev = c.last, c.bars.iloc[-2]
    r = _rng(b)
    prior_ranges = (c.bars["high"] - c.bars["low"]).iloc[-1 - p.lookback:-1]
    if r <= 0 or r <= prior_ranges.max():
        return None
    if not (b["high"] >= prev["high"] and b["low"] <= prev["low"]):
        return None  # second candle must engulf the first
    near = p.close_extreme_fraction * r
    if b["close"] >= b["high"] - near and b["close"] > b["open"]:
        z = _strongest(_support_hit(b, c.zones, c.tol))
        if z:
            return Setup("Big Shadow", LONG, z, b.name, float(b["close"]), float(b["low"]) - c.buffer)
    if b["close"] <= b["low"] + near and b["close"] < b["open"]:
        z = _strongest(_resistance_hit(b, c.zones, c.tol))
        if z:
            return Setup("Big Shadow", SHORT, z, b.name, float(b["close"]), float(b["high"]) + c.buffer)
    return None


# ── Wammie (double bottom) / Moolah (double top) ───────────────────────────────

SECOND_TOUCH_WINDOW = 3  # the second touch must be one of the last N bars (incl. the signal bar)


def wammie_moolah(c: Ctx) -> Setup | None:
    p = c.cfg.wammie_moolah
    w = c.bars.iloc[-p.lookback:]
    n = len(w)
    if n < p.min_bars_between + 2:
        return None
    b = w.iloc[-1]
    px = float(b["close"])
    max_new = px * p.max_new_extreme_pct / 100
    bounce = px * p.min_bounce_pct / 100
    r = _rng(b)
    if r <= 0:
        return None
    lows, highs = w["low"].to_numpy(), w["high"].to_numpy()

    # Wammie: bullish confirmation candle.
    if b["close"] > b["open"] and b["close"] >= b["low"] + r / 2:
        i2 = n - SECOND_TOUCH_WINDOW + int(lows[-SECOND_TOUCH_WINDOW:].argmin())
        first_range = lows[: i2 - p.min_bars_between + 1]
        if len(first_range):
            i1 = int(first_range.argmin())
            l1, l2 = lows[i1], lows[i2]
            between = slice(i1 + 1, i2)
            if (l2 >= l1 - max_new and highs[between].size and highs[between].max() >= max(l1, l2) + bounce
                    and lows[between].min() >= l1 - max_new):
                zs = [z for z in c.zones if z.contains(l1, c.tol) and z.contains(l2, c.tol + max_new)]
                z = _strongest(zs)
                if z:
                    return Setup("Wammie", LONG, z, b.name, px, min(l1, l2) - c.buffer,
                                 f"lows {l1:.2f} / {l2:.2f}")
    # Moolah: bearish confirmation candle.
    if b["close"] < b["open"] and b["close"] <= b["high"] - r / 2:
        i2 = n - SECOND_TOUCH_WINDOW + int(highs[-SECOND_TOUCH_WINDOW:].argmax())
        first_range = highs[: i2 - p.min_bars_between + 1]
        if len(first_range):
            i1 = int(first_range.argmax())
            h1, h2 = highs[i1], highs[i2]
            between = slice(i1 + 1, i2)
            if (h2 <= h1 + max_new and lows[between].size and lows[between].min() <= min(h1, h2) - bounce
                    and highs[between].max() <= h1 + max_new):
                zs = [z for z in c.zones if z.contains(h1, c.tol) and z.contains(h2, c.tol + max_new)]
                z = _strongest(zs)
                if z:
                    return Setup("Moolah", SHORT, z, b.name, px, max(h1, h2) + c.buffer,
                                 f"highs {h1:.2f} / {h2:.2f}")
    return None


# ── Big Belt ────────────────────────────────────────────────────────────────────

def big_belt(c: Ctx) -> Setup | None:
    p = c.cfg.big_belt
    pos = len(c.bars) - 1
    if pos - c.today_start > p.max_bar_index or c.today_start >= len(c.bars):
        return None
    b = c.last
    r = _rng(b)
    avg = c.avg_range(c.cfg.context_bars)
    if r <= 0 or (avg is not None and r < p.min_range_mult * avg):
        return None
    day_open = float(c.bars.iloc[c.today_start]["open"])
    pdh, pdl = c.prior_day["high"], c.prior_day["low"]
    gap = day_open * p.min_gap_pct / 100
    o, cl, h, lo = (float(b[k]) for k in ("open", "close", "high", "low"))

    # Gap up into resistance, opens at the high, closes near the low → short.
    if day_open > pdh + gap and h - o <= p.open_extreme_fraction * r and cl <= lo + p.close_extreme_fraction * r:
        z = _strongest(_resistance_hit(b, c.zones, c.tol))
        if z:
            return Setup("Big Belt", SHORT, z, b.name, cl, h + c.buffer, f"gap up {day_open - pdh:.2f}")
    # Gap down into support, opens at the low, closes near the high → long.
    if day_open < pdl - gap and o - lo <= p.open_extreme_fraction * r and cl >= h - p.close_extreme_fraction * r:
        z = _strongest(_support_hit(b, c.zones, c.tol))
        if z:
            return Setup("Big Belt", LONG, z, b.name, cl, lo - c.buffer, f"gap down {pdl - day_open:.2f}")
    return None


# ── Last Kiss ───────────────────────────────────────────────────────────────────

def last_kiss(c: Ctx) -> Setup | None:
    p = c.cfg.last_kiss
    w = c.bars.iloc[-p.lookback - 1:]
    if len(w) < 4:
        return None
    b = w.iloc[-1]
    hist = w.iloc[:-1]
    closes = hist["close"].to_numpy()
    lows, highs = hist["low"].to_numpy(), hist["high"].to_numpy()
    best: Setup | None = None
    for z in c.zones:
        # Bullish: broke up through the zone, cleared it, came back to kiss the top and rejected.
        if b["low"] <= z.high + c.tol and b["close"] > z.high and b["close"] > b["open"]:
            for i in range(1, len(hist)):
                if closes[i - 1] <= z.high < closes[i]:
                    after = slice(i + 1, len(hist))
                    if (closes[after].size == 0 or closes[after].min() >= z.low) and \
                            (lows[i:].max() > z.high + c.tol):
                        s = Setup("Last Kiss", LONG, z, b.name, float(b["close"]), float(b["low"]) - c.buffer,
                                  "retest of broken resistance")
                        best = s if best is None or z.strength > best.zone.strength else best
                    break
        # Bearish: broke down through the zone, came back up to kiss the bottom and rejected.
        if b["high"] >= z.low - c.tol and b["close"] < z.low and b["close"] < b["open"]:
            for i in range(1, len(hist)):
                if closes[i - 1] >= z.low > closes[i]:
                    after = slice(i + 1, len(hist))
                    if (closes[after].size == 0 or closes[after].max() <= z.high) and \
                            (highs[i:].min() < z.low - c.tol):
                        s = Setup("Last Kiss", SHORT, z, b.name, float(b["close"]), float(b["high"]) + c.buffer,
                                  "retest of broken support")
                        best = s if best is None or z.strength > best.zone.strength else best
                    break
    return best


DETECTORS: dict[str, Callable[[Ctx], Setup | None]] = {
    "kangaroo_tail": kangaroo_tail,
    "big_shadow": big_shadow,
    "wammie_moolah": wammie_moolah,
    "big_belt": big_belt,
    "last_kiss": last_kiss,
}


def detect_setups(bars: pd.DataFrame, today_start: int, zones: list[Zone],
                  prior_day: dict[str, float], cfg: SetupsCfg) -> list[Setup]:
    """Run every enabled detector against the last closed bar (which must be from today)."""
    if bars.empty or len(bars) - 1 < today_start:
        return []
    ctx = Ctx(bars, today_start, zones, prior_day, cfg)
    found = []
    for name in cfg.enabled:
        s = DETECTORS[name](ctx)
        if s is not None and s.risk > 0:
            found.append(s)
    return found
