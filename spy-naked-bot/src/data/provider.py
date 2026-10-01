"""Market data behind a swappable interface.

AlpacaProvider uses Alpaca's free tier: IEX real-time stock bars (or SIP for history older
than 15 minutes) and the free "indicative" options feed for chains/quotes.
CachedBars stores completed days of 1-minute bars under data/ and never re-downloads them.
"""
from __future__ import annotations

import logging
import re
import time as _time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from src.data.bars import COLUMNS, ET, empty_bars, to_et
from src.strategy.contracts import OptionQuote

log = logging.getLogger(__name__)

OCC_RE = re.compile(r"^(?P<root>[A-Z]+)(?P<ymd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")


def parse_occ(symbol: str) -> tuple[str, date, str, float]:
    m = OCC_RE.match(symbol)
    if not m:
        raise ValueError(f"bad OCC symbol {symbol}")
    ymd = m["ymd"]
    exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
    return m["root"], exp, m["right"], int(m["strike"]) / 1000


class DataProvider(ABC):
    @abstractmethod
    def get_minute_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """1-minute bars incl. extended hours, ET index of bar start times."""

    @abstractmethod
    def get_latest_price(self, symbol: str) -> float: ...

    @abstractmethod
    def get_option_chain(self, underlying: str, expiry: date) -> list[OptionQuote]: ...

    @abstractmethod
    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]: ...


class AlpacaProvider(DataProvider):
    BASE = "https://data.alpaca.markets"

    def __init__(self, key: str, secret: str, stock_feed: str = "iex", options_feed: str = "indicative",
                 timeout: float = 20.0):
        if not key or not secret:
            raise ValueError("ALPACA_API_KEY / ALPACA_API_SECRET are not set")
        self.stock_feed = stock_feed
        self.options_feed = options_feed
        self.http = httpx.Client(base_url=self.BASE, timeout=timeout, headers={
            "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _get(self, path: str, params: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                r = self.http.get(path, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 \
                        and e.response.status_code != 429:
                    break
                _time.sleep(2 ** attempt)
        raise RuntimeError(f"Alpaca request {path} failed: {last_exc}")

    def get_minute_bars(self, symbol: str, start: datetime, end: datetime, feed: str | None = None) -> pd.DataFrame:
        rows, token = [], None
        while True:
            params = {"timeframe": "1Min", "start": pd.Timestamp(start).tz_convert("UTC").isoformat(),
                      "end": pd.Timestamp(end).tz_convert("UTC").isoformat(), "limit": 10000,
                      "adjustment": "raw", "feed": feed or self.stock_feed, "sort": "asc"}
            if token:
                params["page_token"] = token
            data = self._get(f"/v2/stocks/{symbol}/bars", params)
            rows.extend(data.get("bars") or [])
            token = data.get("next_page_token")
            if not token:
                break
        if not rows:
            return empty_bars()
        df = pd.DataFrame(rows)
        df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True))
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        return to_et(df[COLUMNS].astype(float))

    def get_latest_price(self, symbol: str) -> float:
        data = self._get(f"/v2/stocks/{symbol}/trades/latest", {"feed": self.stock_feed})
        return float(data["trade"]["p"])

    def _snapshots_to_quotes(self, snaps: dict) -> dict[str, OptionQuote]:
        out = {}
        for sym, snap in snaps.items():
            q = snap.get("latestQuote") or {}
            _, exp, right, strike = parse_occ(sym)
            out[sym] = OptionQuote(sym, exp, strike, right, float(q.get("bp") or 0), float(q.get("ap") or 0))
        return out

    def get_option_chain(self, underlying: str, expiry: date) -> list[OptionQuote]:
        quotes: dict[str, OptionQuote] = {}
        token = None
        while True:
            params = {"feed": self.options_feed, "expiration_date": expiry.isoformat(), "limit": 1000}
            if token:
                params["page_token"] = token
            data = self._get(f"/v1beta1/options/snapshots/{underlying}", params)
            quotes.update(self._snapshots_to_quotes(data.get("snapshots") or {}))
            token = data.get("next_page_token")
            if not token:
                break
        return list(quotes.values())

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        data = self._get("/v1beta1/options/snapshots", {"symbols": ",".join(symbols), "feed": self.options_feed})
        return self._snapshots_to_quotes(data.get("snapshots") or {})


class CachedBars:
    """Day-file cache of 1-minute bars: data/bars/<SYMBOL>/<YYYY-MM-DD>.csv.gz.
    Only completed trading days are cached; days with no data are remembered in a marker file."""

    def __init__(self, provider: AlpacaProvider, cache_dir: str | Path, feed: str = "sip"):
        self.provider = provider
        self.dir = Path(cache_dir) / "bars"
        self.feed = feed

    def _path(self, symbol: str, d: date) -> Path:
        return self.dir / symbol / f"{d.isoformat()}.csv.gz"

    def _empty_marker(self, symbol: str) -> Path:
        return self.dir / symbol / "_empty_days.txt"

    def get_days(self, symbol: str, days: list[date]) -> pd.DataFrame:
        (self.dir / symbol).mkdir(parents=True, exist_ok=True)
        marker = self._empty_marker(symbol)
        empty = set(marker.read_text().split()) if marker.exists() else set()
        today = pd.Timestamp.now(tz=ET).date()
        missing = [d for d in days if not self._path(symbol, d).exists() and d.isoformat() not in empty
                   and d < today]
        # Fetch month by month to keep requests few.
        by_month: dict[tuple[int, int], list[date]] = {}
        for d in missing:
            by_month.setdefault((d.year, d.month), []).append(d)
        for (_, _), ds in sorted(by_month.items()):
            start = pd.Timestamp.combine(min(ds), datetime.min.time()).tz_localize(ET)
            end = pd.Timestamp.combine(max(ds) + timedelta(days=1), datetime.min.time()).tz_localize(ET)
            log.info("downloading %s 1-min bars %s → %s", symbol, min(ds), max(ds))
            df = self.provider.get_minute_bars(symbol, start.to_pydatetime(), end.to_pydatetime(), feed=self.feed)
            for d in ds:
                part = df[df.index.date == d]
                if part.empty:
                    empty.add(d.isoformat())
                else:
                    part.to_csv(self._path(symbol, d), compression="gzip")
        if missing:
            marker.write_text("\n".join(sorted(empty)))
        frames = [self._read(symbol, d) for d in days if self._path(symbol, d).exists()]
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames).sort_index() if frames else empty_bars()

    def _read(self, symbol: str, d: date) -> pd.DataFrame:
        df = pd.read_csv(self._path(symbol, d), index_col=0, compression="gzip")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True)).tz_convert(ET)
        return df[COLUMNS]
