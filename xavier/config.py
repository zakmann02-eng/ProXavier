"""Xavier configuration — loaded once at startup from environment variables."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"Required env var '{key}' is not set.")
    return val


def _opt(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class XavierConfig:
    alpaca_api_key: str
    alpaca_api_secret: str
    alpaca_paper: bool
    telegram_token: str
    telegram_chat_id: str
    risk_pct: float
    allow_short: bool
    min_rr_ratio: float
    orb_minutes: int
    min_price: float
    max_positions: int
    max_day_positions: int
    max_swing_positions: int
    max_portfolio_risk_pct: float
    daily_loss_limit_pct: float
    consecutive_loss_halt: int
    max_crypto_alloc_pct: float
    enable_crypto: bool
    enable_swing: bool
    enable_vwap_reversion: bool
    enable_momentum: bool
    enable_etf_rotation: bool
    paused: bool

    @classmethod
    def load(cls) -> "XavierConfig":
        c = cls()
        c.alpaca_api_key    = _require("ALPACA_API_KEY")
        c.alpaca_api_secret = _require("ALPACA_API_SECRET")
        c.alpaca_paper      = _opt("ALPACA_PAPER", "true").lower() != "false"
        c.telegram_token   = _require("XAVIER_TELEGRAM_TOKEN")
        c.telegram_chat_id = _require("XAVIER_TELEGRAM_CHAT_ID")
        c.risk_pct      = float(_opt("XAVIER_RISK_PCT",     "1.0"))
        c.allow_short   = _opt("XAVIER_ALLOW_SHORT", "false").lower() == "true"
        c.min_rr_ratio  = float(_opt("XAVIER_MIN_RR_RATIO", "1.5"))
        c.orb_minutes   = int(_opt("XAVIER_ORB_MINUTES",    "15"))
        c.min_price     = float(_opt("XAVIER_MIN_PRICE",    "5.0"))
        c.max_positions       = int(_opt("XAVIER_MAX_POSITIONS",       "5"))
        c.max_day_positions   = int(_opt("XAVIER_MAX_DAY_POSITIONS",   "3"))
        c.max_swing_positions = int(_opt("XAVIER_MAX_SWING_POSITIONS", "2"))
        c.max_portfolio_risk_pct = float(_opt("XAVIER_MAX_PORTFOLIO_RISK_PCT", "6.0"))
        c.daily_loss_limit_pct   = float(_opt("XAVIER_DAILY_LOSS_LIMIT_PCT",   "3.0"))
        c.consecutive_loss_halt  = int(_opt("XAVIER_CONSECUTIVE_LOSS_HALT",    "4"))
        c.max_crypto_alloc_pct   = float(_opt("XAVIER_MAX_CRYPTO_ALLOC_PCT",   "20.0"))
        c.enable_crypto         = _opt("XAVIER_ENABLE_CRYPTO",         "true").lower() == "true"
        c.enable_swing          = _opt("XAVIER_ENABLE_SWING",          "true").lower() == "true"
        c.enable_vwap_reversion = _opt("XAVIER_ENABLE_VWAP_REVERSION", "true").lower() == "true"
        c.enable_momentum       = _opt("XAVIER_ENABLE_MOMENTUM",       "true").lower() == "true"
        c.enable_etf_rotation   = _opt("XAVIER_ENABLE_ETF_ROTATION",   "true").lower() == "true"
        c.paused = _opt("XAVIER_PAUSED", "false").lower() == "true"
        return c
