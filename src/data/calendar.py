"""Market calendar (holidays, half days) and the manual skip-day list."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal
import yaml

from src.config import PROJECT_ROOT

SKIP_DAYS_FILE = PROJECT_ROOT / "data" / "skip_days.yaml"


@lru_cache(maxsize=1)
def _nyse():
    return mcal.get_calendar("NYSE")


def schedule(start: date, end: date) -> pd.DataFrame:
    return _nyse().schedule(start_date=start, end_date=end)


def trading_days(start: date, end: date) -> list[date]:
    return [ts.date() for ts in schedule(start, end).index]


def is_trading_day(d: date) -> bool:
    return bool(trading_days(d, d))


def market_close(d: date) -> datetime | None:
    s = schedule(d, d)
    if s.empty:
        return None
    return s.iloc[0]["market_close"].tz_convert("America/New_York").to_pydatetime()


def is_half_day(d: date) -> bool:
    c = market_close(d)
    return c is not None and (c.hour, c.minute) < (16, 0)


def previous_trading_days(d: date, n: int) -> list[date]:
    """The n trading days strictly before d (oldest first)."""
    start = pd.Timestamp(d) - pd.Timedelta(days=n * 2 + 10)
    days = [x for x in trading_days(start.date(), d) if x < d]
    return days[-n:]


def load_skip_days(path: str | Path = SKIP_DAYS_FILE) -> dict[str, set[date]]:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return {k.upper(): {pd.Timestamp(str(v)).date() for v in (vals or [])} for k, vals in raw.items()}


def skip_reason(d: date, skip_days: dict[str, set[date]], types: list[str]) -> str | None:
    hits = [t for t in types if d in skip_days.get(t.upper(), set())]
    return f"{'/'.join(hits)} day" if hits else None


def stale_skip_types(d: date, skip_days: dict[str, set[date]], types: list[str]) -> list[str]:
    """Event types with no dates listed for d's year — the skip file probably needs updating."""
    return [t for t in types if not any(x.year == d.year for x in skip_days.get(t.upper(), set()))]
