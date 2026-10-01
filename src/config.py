"""Configuration: tunable strategy parameters from config.yaml, secrets from env vars.

Dataclass defaults mirror config.yaml so tests can build a Config() without files.
Unknown keys in config.yaml raise an error so typos never silently fall back to defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class SessionCfg:
    timezone: str = "America/New_York"
    wake_time: str = "07:55"
    wake_window_minutes: int = 45
    brief_time: str = "09:25"
    entry_start: str = "09:30"
    entry_end: str = "09:50"
    time_stop: str = "12:00"
    candle_minutes: int = 5
    data_lag_seconds: int = 8
    skip_half_days: bool = False
    skip_day_types: list[str] = field(default_factory=lambda: ["FOMC", "CPI", "NFP"])


@dataclass
class ZonesCfg:
    width_pct: float = 0.15
    swing_sessions: int = 10
    swing_timeframe_minutes: int = 30
    pivot_strength: int = 2
    min_touches: int = 2
    key_level_strength: int = 1
    max_distance_pct: float = 3.0


@dataclass
class TrendCfg:
    timeframe_minutes: int = 60
    sessions: int = 5
    pivot_strength: int = 2
    neutral_policy: str = "strongest_only"
    neutral_min_strength: int = 3


@dataclass
class KangarooTailCfg:
    min_tail_ratio: float = 0.66
    body_zone_fraction: float = 0.34
    min_range_mult: float = 1.0


@dataclass
class BigShadowCfg:
    lookback: int = 5
    close_extreme_fraction: float = 0.25


@dataclass
class WammieMoolahCfg:
    lookback: int = 12
    min_bars_between: int = 3
    max_new_extreme_pct: float = 0.03
    min_bounce_pct: float = 0.08


@dataclass
class BigBeltCfg:
    max_bar_index: int = 0
    min_gap_pct: float = 0.10
    open_extreme_fraction: float = 0.10
    close_extreme_fraction: float = 0.25
    min_range_mult: float = 1.0


@dataclass
class LastKissCfg:
    lookback: int = 12


@dataclass
class SetupsCfg:
    enabled: list[str] = field(
        default_factory=lambda: ["kangaroo_tail", "big_shadow", "wammie_moolah", "big_belt", "last_kiss"]
    )
    context_bars: int = 8
    zone_tolerance_pct: float = 0.05
    stop_buffer_pct: float = 0.02
    kangaroo_tail: KangarooTailCfg = field(default_factory=KangarooTailCfg)
    big_shadow: BigShadowCfg = field(default_factory=BigShadowCfg)
    wammie_moolah: WammieMoolahCfg = field(default_factory=WammieMoolahCfg)
    big_belt: BigBeltCfg = field(default_factory=BigBeltCfg)
    last_kiss: LastKissCfg = field(default_factory=LastKissCfg)


@dataclass
class ChecklistCfg:
    zone_proximity_pct: float = 0.10
    min_reward_risk: float = 2.0
    no_target_policy: str = "skip"
    max_trades_per_day: int = 1


@dataclass
class TradeCfg:
    allow_spreads: bool = True
    spread_width: float = 2.0
    dte: int = 0
    max_contracts: int = 1
    fee_per_contract_leg: float = 0.05
    limit_buffer_fraction: float = 0.25
    max_bid_ask_ratio: float = 0.35
    min_debit: float = 0.10


@dataclass
class ExitsCfg:
    take_profit_pct: float = 60
    take_profit_at_zone: bool = True
    hard_stop_pct: float = 40
    breakeven_trigger_pct: float = 30
    poll_seconds: int = 30


@dataclass
class SurvivalCfg:
    starting_capital: float = 100.0
    consecutive_losses_pause: int = 2
    max_drawdown_pct: float = 30


@dataclass
class BacktestCfg:
    holdout_months: int = 6
    years: float = 2.5
    iv_rv_ratio: float = 1.15
    min_iv: float = 0.10
    rv_window_days: int = 20
    model_spread_fraction: float = 0.06


@dataclass
class Config:
    symbol: str = "SPY"
    session: SessionCfg = field(default_factory=SessionCfg)
    zones: ZonesCfg = field(default_factory=ZonesCfg)
    trend: TrendCfg = field(default_factory=TrendCfg)
    setups: SetupsCfg = field(default_factory=SetupsCfg)
    checklist: ChecklistCfg = field(default_factory=ChecklistCfg)
    trade: TradeCfg = field(default_factory=TradeCfg)
    exits: ExitsCfg = field(default_factory=ExitsCfg)
    survival: SurvivalCfg = field(default_factory=SurvivalCfg)
    backtest: BacktestCfg = field(default_factory=BacktestCfg)


def _build(cls, data: dict[str, Any], path: str = ""):
    obj = cls()
    known = {f.name: f for f in fields(cls)}
    for key, value in (data or {}).items():
        if key not in known:
            raise ValueError(f"Unknown config key: {path}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            setattr(obj, key, _build(type(current), value, f"{path}{key}."))
        else:
            setattr(obj, key, value)
    return obj


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    with open(path) as f:
        return _build(Config, yaml.safe_load(f) or {})


@dataclass
class Secrets:
    """Runtime settings that come only from environment variables."""

    mode: str = "paper"                      # paper | live (live not implemented in Phase 1)
    telegram_token: str = ""
    telegram_chat_id: str = ""
    alpaca_key: str = ""
    alpaca_secret: str = ""
    alpaca_stock_feed: str = "iex"           # iex (free real-time) | sip (paid / 15-min delayed)
    db_path: str = str(PROJECT_ROOT / "data" / "journal.sqlite3")
    data_dir: str = str(PROJECT_ROOT / "data")

    @classmethod
    def from_env(cls) -> "Secrets":
        try:
            from dotenv import load_dotenv

            load_dotenv(PROJECT_ROOT / ".env")
        except ImportError:  # python-dotenv is optional at runtime
            pass
        d = cls()
        return cls(
            mode=os.getenv("MODE", d.mode).strip().lower(),
            # XAVIER_* names are accepted so the old Xavier bot's Telegram bot/variables keep working.
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("XAVIER_TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or os.getenv("XAVIER_TELEGRAM_CHAT_ID", ""),
            alpaca_key=os.getenv("ALPACA_API_KEY", ""),
            alpaca_secret=os.getenv("ALPACA_API_SECRET", ""),
            alpaca_stock_feed=os.getenv("ALPACA_STOCK_FEED", d.alpaca_stock_feed),
            db_path=os.getenv("DB_PATH", d.db_path),
            data_dir=os.getenv("DATA_DIR", d.data_dir),
        )
