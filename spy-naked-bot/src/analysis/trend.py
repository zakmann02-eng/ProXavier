"""Trend bias from price structure only (no indicators)."""
from __future__ import annotations

import pandas as pd

from src.analysis.swings import find_pivots

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"


def trend_bias(bars: pd.DataFrame, pivot_strength: int) -> tuple[str, str]:
    """Compare the last two swing highs and last two swing lows.
    Returns (bias, human-readable reason)."""
    pivots = find_pivots(bars, pivot_strength)
    highs = [p.price for p in pivots if p.kind == "H"]
    lows = [p.price for p in pivots if p.kind == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return NEUTRAL, "not enough swings"
    hh, hl = highs[-1] > highs[-2], lows[-1] > lows[-2]
    lh, ll = highs[-1] < highs[-2], lows[-1] < lows[-2]
    desc = f"highs {highs[-2]:.2f}→{highs[-1]:.2f}, lows {lows[-2]:.2f}→{lows[-1]:.2f}"
    if hh and hl:
        return BULLISH, f"higher highs & higher lows ({desc})"
    if lh and ll:
        return BEARISH, f"lower highs & lower lows ({desc})"
    return NEUTRAL, f"mixed structure ({desc})"
