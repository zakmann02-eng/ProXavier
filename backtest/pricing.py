"""Synthetic option pricing for the backtest (Black-Scholes on a volatility estimate).

Free historical options QUOTES for SPY aren't available, so the backtest prices legs with
Black-Scholes using realized volatility × an IV/RV ratio, plus a modeled bid/ask width.
This is an approximation: it ignores intraday IV changes, skew, and real fill quality.
Treat results as a first filter, then confirm with Phase 1 paper trading (real quotes) or
plug a real options-history source in here.
"""
from __future__ import annotations

import math
from datetime import date, datetime

from src.strategy.contracts import OptionQuote

TRADING_MIN_PER_YEAR = 252 * 390


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float, right: str, r: float = 0.0) -> float:
    if t_years <= 0 or sigma <= 0:
        return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    sq = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + sigma * sigma / 2) * t_years) / sq
    d2 = d1 - sq
    if right == "C":
        return spot * _ncdf(d1) - strike * math.exp(-r * t_years) * _ncdf(d2)
    return strike * math.exp(-r * t_years) * _ncdf(-d2) - spot * _ncdf(-d1)


def years_to_expiry(now: datetime, expiry: date, trading_days_between: int, close_hhmm=(16, 0)) -> float:
    """Trading-time year fraction: minutes left today until the close + 390 per full day after."""
    close = now.replace(hour=close_hhmm[0], minute=close_hhmm[1], second=0, microsecond=0)
    left_today = max((close - now).total_seconds() / 60, 0.0)
    return (left_today + 390 * trading_days_between) / TRADING_MIN_PER_YEAR


def quote(spot: float, strike: float, right: str, t_years: float, sigma: float, expiry: date,
          spread_fraction: float) -> OptionQuote:
    mid = bs_price(spot, strike, t_years, sigma, right)
    half = max(0.01, mid * spread_fraction) / 2
    sym = f"SPY{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"
    return OptionQuote(sym, expiry, strike, right, max(round(mid - half, 2), 0.0), round(mid + half, 2))


def synthetic_chain(spot: float, now: datetime, expiry: date, days_after_today: int, sigma: float,
                    spread_fraction: float, strikes_each_side: int = 8) -> list[OptionQuote]:
    t = years_to_expiry(now, expiry, days_after_today)
    center = round(spot)
    out = []
    for k in range(center - strikes_each_side, center + strikes_each_side + 1):
        for right in ("C", "P"):
            out.append(quote(spot, float(k), right, t, sigma, expiry, spread_fraction))
    return out
