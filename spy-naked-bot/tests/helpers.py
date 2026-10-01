"""Hand-built candle fixtures."""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.analysis.zones import Zone
from src.data.bars import ET

DAY = date(2025, 3, 4)


def candles(rows: list[tuple[float, float, float, float]], start: str = "09:30", minutes: int = 5,
            day: date = DAY) -> pd.DataFrame:
    """rows of (open, high, low, close)."""
    idx = pd.date_range(pd.Timestamp(f"{day} {start}").tz_localize(ET), periods=len(rows), freq=f"{minutes}min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    return df


def flat(n: int, price: float = 500.0, rng: float = 0.4) -> list[tuple[float, float, float, float]]:
    """n small neutral candles around `price`."""
    return [(price, price + rng / 2, price - rng / 2, price + 0.05) for _ in range(n)]


def zone(low: float, high: float, strength: int = 2, label: str = "test") -> Zone:
    return Zone(low, high, strength, [label])


def synth_minutes(days: list[date], seed: int = 7, start_price: float = 500.0,
                  first: str = "08:00", last: str = "16:00") -> pd.DataFrame:
    """Random-walk 1-minute bars (pre-market + RTH) for the given days."""
    import numpy as np

    rng = np.random.default_rng(seed)
    frames, price = [], start_price
    for d in days:
        idx = pd.date_range(pd.Timestamp(f"{d} {first}").tz_localize(ET),
                            pd.Timestamp(f"{d} {last}").tz_localize(ET), freq="1min", inclusive="left")
        price *= 1 + rng.normal(0, 0.004)          # overnight gap
        steps = rng.normal(0, 0.0006, len(idx)) * price
        closes = price + np.cumsum(steps)
        opens = np.concatenate([[price], closes[:-1]])
        wick = np.abs(rng.normal(0, 0.0003, len(idx))) * price
        highs = np.maximum(opens, closes) + wick
        lows = np.minimum(opens, closes) - np.abs(rng.normal(0, 0.0003, len(idx))) * price
        frames.append(pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                                    "volume": 1000.0}, index=idx))
        price = float(closes[-1])
    return pd.concat(frames)
