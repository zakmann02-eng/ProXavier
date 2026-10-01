"""Swing (pivot) detection shared by zone building and trend bias."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Pivot:
    pos: int          # integer position in the bar frame
    ts: pd.Timestamp
    price: float
    kind: str         # "H" (swing high) or "L" (swing low)


def find_pivots(bars: pd.DataFrame, strength: int) -> list[Pivot]:
    """A swing high is a bar whose high is above the `strength` bars to its left and not
    below the `strength` bars to its right (mirror for lows). The last `strength` bars can't
    qualify because their right side hasn't formed yet."""
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    out: list[Pivot] = []
    n = len(bars)
    for i in range(strength, n - strength):
        left_h, right_h = highs[i - strength:i], highs[i + 1:i + strength + 1]
        left_l, right_l = lows[i - strength:i], lows[i + 1:i + strength + 1]
        if highs[i] > left_h.max() and highs[i] >= right_h.max():
            out.append(Pivot(i, bars.index[i], float(highs[i]), "H"))
        if lows[i] < left_l.min() and lows[i] <= right_l.min():
            out.append(Pivot(i, bars.index[i], float(lows[i]), "L"))
    return out
