"""Xavier watchlist — equities, ETFs, and crypto symbols."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Highly-liquid equities suitable for intraday / swing trading
EQUITY_SYMBOLS: list[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA",
    # Active day-trading names
    "PLTR", "SOFI", "RIVN", "INTC",
    # Financials
    "BAC", "JPM", "GS",
    # Energy
    "XOM", "CVX",
]

# Sector and index ETFs — used by both ORB and ETFRotation strategies
ETF_SYMBOLS: list[str] = [
    "SPY", "QQQ", "IWM", "DIA",            # Major indices
    "XLK", "XLF", "XLE", "XLV", "XLI",    # GICS sectors
    "XLY", "XLB", "XLC", "XLRE",
    "XLU", "XLP",
    "GLD", "SLV",                           # Precious metals
    "TLT", "HYG",                           # Fixed income
]

# Liquid crypto pairs available on Alpaca
CRYPTO_SYMBOLS: list[str] = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
]


class Scanner:
    def __init__(
        self,
        *,
        enable_crypto: bool = True,
        watchlist_override: list[str] | None = None,
    ):
        env_raw = os.getenv("XAVIER_WATCHLIST", "").strip()
        if env_raw:
            custom = [s.strip().upper() for s in env_raw.split(",") if s.strip()]
            self._equity = custom
            self._etf    = []
            self._crypto = []
        elif watchlist_override:
            self._equity = watchlist_override
            self._etf    = []
            self._crypto = []
        else:
            self._equity = EQUITY_SYMBOLS[:]
            self._etf    = ETF_SYMBOLS[:]
            self._crypto = CRYPTO_SYMBOLS[:] if enable_crypto else []

        logger.info(
            "Scanner: %d equities | %d ETFs | %d crypto",
            len(self._equity), len(self._etf), len(self._crypto),
        )

    @property
    def equity_symbols(self) -> list[str]:
        return list(self._equity)

    @property
    def etf_symbols(self) -> list[str]:
        return list(self._etf)

    @property
    def crypto_symbols(self) -> list[str]:
        return list(self._crypto)

    @property
    def all_equity(self) -> list[str]:
        return self._equity + self._etf

    @property
    def all_symbols(self) -> list[str]:
        return self._equity + self._etf + self._crypto

    def set_equity_symbols(self, symbols: list[str]) -> None:
        self._equity = [s.strip().upper() for s in symbols if s.strip()]
        logger.info("Equity watchlist updated (%d symbols)", len(self._equity))
