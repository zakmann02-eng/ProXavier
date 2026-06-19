"""Xavier configuration — loaded once at startup from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


@dataclass
class XavierConfig:
    # Alpaca
    alpaca_api_key:    str
    alpaca_api_secret: str
    alpaca_paper:      bool

    # Telegram
    telegram_token:   str
    telegram_chat_id: str

    # Risk
    risk_pct:                float
    max_trade_risk_usd:      float   # hard cap per trade in dollars
    hard_loss_halt_usd:      float   # halt all trading if cumulative loss hits this
    max_portfolio_risk_pct:  float
    daily_loss_limit_pct:    float
    consecutive_loss_halt:   int
    max_crypto_alloc_pct:    float
    max_positions:           int
    max_day_positions:       int
    max_swing_positions:     int

    # Strategy toggles
    orb_minutes:            int
    min_rr_ratio:           float
    min_price:              float
    allow_short:            bool
    enable_crypto:          bool
    enable_swing:           bool
    enable_vwap_reversion:  bool
    enable_momentum:        bool
    enable_etf_rotation:    bool

    # Runtime
    paused: bool

    @classmethod
    def from_env(cls) -> "XavierConfig":
        api_key    = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_API_SECRET", "")
        tg_token   = os.getenv("XAVIER_TELEGRAM_TOKEN", "")
        tg_chat    = os.getenv("XAVIER_TELEGRAM_CHAT_ID", "")

        if not all([api_key, api_secret, tg_token, tg_chat]):
            missing = [k for k, v in {
                "ALPACA_API_KEY": api_key,
                "ALPACA_API_SECRET": api_secret,
                "XAVIER_TELEGRAM_TOKEN": tg_token,
                "XAVIER_TELEGRAM_CHAT_ID": tg_chat,
            }.items() if not v]
            raise EnvironmentError(f"Missing required env vars: {missing}")

        return cls(
            alpaca_api_key=api_key,
            alpaca_api_secret=api_secret,
            alpaca_paper=_bool("ALPACA_PAPER", True),
            telegram_token=tg_token,
            telegram_chat_id=tg_chat,
            risk_pct=_float("XAVIER_RISK_PCT", 1.0),
            max_trade_risk_usd=_float("XAVIER_MAX_TRADE_RISK_USD", 5.0),
            hard_loss_halt_usd=_float("XAVIER_HARD_LOSS_HALT_USD", 100.0),
            max_portfolio_risk_pct=_float("XAVIER_MAX_PORTFOLIO_RISK_PCT", 6.0),
            daily_loss_limit_pct=_float("XAVIER_DAILY_LOSS_LIMIT_PCT", 3.0),
            consecutive_loss_halt=_int("XAVIER_CONSECUTIVE_LOSS_HALT", 4),
            max_crypto_alloc_pct=_float("XAVIER_MAX_CRYPTO_ALLOC_PCT", 80.0),
            max_positions=_int("XAVIER_MAX_POSITIONS", 3),
            max_day_positions=_int("XAVIER_MAX_DAY_POSITIONS", 2),
            max_swing_positions=_int("XAVIER_MAX_SWING_POSITIONS", 1),
            orb_minutes=_int("XAVIER_ORB_MINUTES", 15),
            min_rr_ratio=_float("XAVIER_MIN_RR_RATIO", 2.0),
            min_price=_float("XAVIER_MIN_PRICE", 1.0),
            allow_short=_bool("XAVIER_ALLOW_SHORT", False),
            enable_crypto=_bool("XAVIER_ENABLE_CRYPTO", True),
            enable_swing=_bool("XAVIER_ENABLE_SWING", False),
            enable_vwap_reversion=_bool("XAVIER_ENABLE_VWAP_REVERSION", True),
            enable_momentum=_bool("XAVIER_ENABLE_MOMENTUM", False),
            enable_etf_rotation=_bool("XAVIER_ENABLE_ETF_ROTATION", False),
            paused=_bool("XAVIER_PAUSED", False),
        )
