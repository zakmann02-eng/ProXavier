"""Technical analysis indicators — pure pandas/numpy, no external TA library."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal_period: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_period)
    return macd_line, signal_line, macd_line - signal_line


def vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative intraday VWAP — df should contain today's bars only."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    return (typical * df["volume"]).cumsum() / df["volume"].cumsum()


def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (upper, middle, lower)."""
    mid = sma(series, period)
    std = series.rolling(window=period).std()
    return mid + std_dev * std, mid, mid - std_dev * std


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — values >25 indicate a trending market."""
    tr_series = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr_smooth   = tr_series.ewm(span=period, adjust=False).mean()
    plus_di     = 100.0 * plus_dm.ewm(span=period, adjust=False).mean() / tr_smooth
    minus_di    = 100.0 * minus_dm.ewm(span=period, adjust=False).mean() / tr_smooth
    denom       = (plus_di + minus_di).replace(0, np.nan)
    dx          = 100.0 * (plus_di - minus_di).abs() / denom
    return dx.ewm(span=period, adjust=False).mean().fillna(0.0)


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k_period: int = 14, d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic oscillator — returns (%K, %D)."""
    low_min  = low.rolling(window=k_period).min()
    high_max = high.rolling(window=k_period).max()
    k = 100.0 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(window=d_period).mean()
    return k, d


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume — cumulative volume signed by price direction."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def donchian(
    high: pd.Series, low: pd.Series, period: int = 20
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian Channels — returns (upper, mid, lower)."""
    upper = high.rolling(window=period).max()
    lower = low.rolling(window=period).min()
    return upper, (upper + lower) / 2.0, lower
