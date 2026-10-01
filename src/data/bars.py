"""Candle helpers. Bars are DataFrames with columns open/high/low/close/volume and a
tz-aware DatetimeIndex (America/New_York) holding each bar's START time."""
from __future__ import annotations

from datetime import date, time

import pandas as pd

ET = "America/New_York"
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
COLUMNS = ["open", "high", "low", "close", "volume"]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz=ET), dtype=float)


def to_et(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return empty_bars()
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = df.copy()
    df.index = idx.tz_convert(ET)
    return df[COLUMNS].sort_index()


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Resample 1-minute bars to N-minute bars aligned so a bar starts at 09:30."""
    if df.empty or minutes == 1:
        return df
    offset = (9 * 60 + 30) % minutes
    out = df.resample(f"{minutes}min", origin="start_day", offset=f"{offset}min", label="left",
                      closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


def rth(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    t = df.index.time
    return df[(t >= RTH_OPEN) & (t < RTH_CLOSE)]


def premarket(df: pd.DataFrame, day: date) -> pd.DataFrame:
    """Overnight/pre-market bars for `day`: from the prior RTH close up to 09:30 of `day`."""
    if df.empty:
        return df
    day_open = pd.Timestamp.combine(day, RTH_OPEN).tz_localize(ET)
    prior = df[df.index < day_open]
    prior_rth = rth(prior)
    start = prior_rth.index.max() if not prior_rth.empty else prior.index.min()
    return prior[prior.index > start]


def sessions(df: pd.DataFrame) -> list[date]:
    if df.empty:
        return []
    return sorted(set(df.index.date))


def session_bars(df: pd.DataFrame, day: date) -> pd.DataFrame:
    return df[df.index.date == day]


def daily_from_intraday(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC built from regular-hours bars only."""
    r = rth(df)
    if r.empty:
        return empty_bars()
    g = r.groupby(r.index.date)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
    })
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index)).tz_localize(ET)
    return out
