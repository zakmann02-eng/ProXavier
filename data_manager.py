"""
Multi-timeframe bar cache — fetch once per scan cycle, share across strategies.
Bars older than STALE_SECONDS are re-fetched on next access.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

ET  = ZoneInfo("America/New_York")
UTC = timezone.utc

STALE_SECONDS = 90   # re-fetch if cached data is older than 90 s


class DataManager:
    def __init__(self, client) -> None:
        self._client = client
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _key(self, symbol: str, kind: str) -> str:
        return f"{symbol}:{kind}"

    def _fresh(self, ts: float) -> bool:
        return (time.monotonic() - ts) < STALE_SECONDS

    def _get(self, key: str) -> pd.DataFrame | None:
        entry = self._cache.get(key)
        if entry and self._fresh(entry[0]):
            return entry[1]
        return None

    def _put(self, key: str, df: pd.DataFrame) -> pd.DataFrame:
        self._cache[key] = (time.monotonic(), df)
        return df

    # ── Stock / ETF data ───────────────────────────────────────────────────────

    def today_1m(self, symbol: str) -> pd.DataFrame:
        k = self._key(symbol, "1m_today")
        cached = self._get(k)
        if cached is not None:
            return cached
        try:
            df = self._client.get_today_bars(symbol)
        except Exception as exc:
            logger.debug("1m fetch failed %s: %s", symbol, exc)
            return pd.DataFrame()
        return self._put(k, df)

    def daily(self, symbol: str, lookback_days: int = 60) -> pd.DataFrame:
        k = self._key(symbol, f"daily_{lookback_days}")
        cached = self._get(k)
        if cached is not None:
            return cached
        try:
            from alpaca.data.timeframe import TimeFrame
            start = datetime.now(UTC) - timedelta(days=lookback_days + 5)
            df = self._client.get_bars(symbol, TimeFrame.Day, start=start, limit=lookback_days)
        except Exception as exc:
            logger.debug("daily fetch failed %s: %s", symbol, exc)
            return pd.DataFrame()
        return self._put(k, df)

    def intraday_5m(self, symbol: str) -> pd.DataFrame:
        k = self._key(symbol, "5m_today")
        cached = self._get(k)
        if cached is not None:
            return cached
        try:
            from alpaca.data.timeframe import TimeFrame
            now_et = datetime.now(ET)
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            start_utc = market_open.astimezone(UTC)
            df = self._client.get_bars(symbol, TimeFrame.Minute * 5, start=start_utc, limit=80)
        except Exception as exc:
            logger.debug("5m fetch failed %s: %s", symbol, exc)
            return pd.DataFrame()
        return self._put(k, df)

    # ── Crypto data ────────────────────────────────────────────────────────────

    def crypto_1m(self, symbol: str) -> pd.DataFrame:
        k = self._key(symbol, "crypto_1m")
        cached = self._get(k)
        if cached is not None:
            return cached
        try:
            df = self._client.get_crypto_bars(symbol, limit=120)
        except Exception as exc:
            logger.debug("crypto 1m fetch failed %s: %s", symbol, exc)
            return pd.DataFrame()
        return self._put(k, df)

    def crypto_daily(self, symbol: str, days: int = 30) -> pd.DataFrame:
        k = self._key(symbol, f"crypto_daily_{days}")
        cached = self._get(k)
        if cached is not None:
            return cached
        try:
            df = self._client.get_crypto_daily_bars(symbol, days=days)
        except Exception as exc:
            logger.debug("crypto daily fetch failed %s: %s", symbol, exc)
            return pd.DataFrame()
        return self._put(k, df)

    # ── Cache management ───────────────────────────────────────────────────────

    def invalidate(self, symbol: str | None = None) -> None:
        if symbol:
            for key in [k for k in self._cache if k.startswith(f"{symbol}:")]:
                del self._cache[key]
        else:
            self._cache.clear()
