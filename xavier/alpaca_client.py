"""Alpaca API wrapper for Xavier — stocks, ETFs, and crypto."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestQuoteRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

logger = logging.getLogger(__name__)

ET  = ZoneInfo("America/New_York")
UTC = timezone.utc


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, *, paper: bool = True):
        self._trading     = TradingClient(api_key, api_secret, paper=paper)
        self._stock_data  = StockHistoricalDataClient(api_key, api_secret)
        self._crypto_data = CryptoHistoricalDataClient(api_key, api_secret)
        self.paper = paper
        logger.info("AlpacaClient ready — %s mode", "PAPER" if paper else "LIVE")

    # ── Account ────────────────────────────────────────────────────────────────

    def get_account(self):
        return self._trading.get_account()

    def get_equity(self) -> float:
        return float(self._trading.get_account().equity)

    def get_buying_power(self) -> float:
        return float(self._trading.get_account().buying_power)

    def is_market_open(self) -> bool:
        return self._trading.get_clock().is_open

    def get_clock(self):
        return self._trading.get_clock()

    # ── Stock / ETF market data ────────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        timeframe: TimeFrame = TimeFrame.Minute,
        *,
        start: datetime | None = None,
        limit: int = 120,
    ) -> pd.DataFrame:
        if start is None:
            start = datetime.now(UTC) - timedelta(minutes=limit + 10)
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            feed=DataFeed.SIP,
            limit=limit,
        )
        bars = self._stock_data.get_stock_bars(request)
        return self._stock_df(bars, symbol)

    def get_today_bars(self, symbol: str) -> pd.DataFrame:
        now_et      = datetime.now(ET)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        return self.get_bars(symbol, TimeFrame.Minute, start=market_open.astimezone(UTC), limit=390)

    def get_latest_price(self, symbol: str) -> float:
        req   = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.SIP)
        quote = self._stock_data.get_stock_latest_quote(req)[symbol]
        ask   = float(quote.ask_price or 0)
        bid   = float(quote.bid_price or 0)
        return (ask + bid) / 2.0 if (ask and bid) else ask or bid

    def _stock_df(self, bars, symbol: str) -> pd.DataFrame:
        if not bars or symbol not in bars:
            return pd.DataFrame()
        raw = bars[symbol]
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(
            [{"timestamp": b.timestamp, "open": float(b.open), "high": float(b.high),
              "low": float(b.low), "close": float(b.close), "volume": float(b.volume)}
             for b in raw]
        ).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
        return df

    # ── Crypto market data ─────────────────────────────────────────────────────

    def get_crypto_bars(self, symbol: str, limit: int = 120) -> pd.DataFrame:
        start = datetime.now(UTC) - timedelta(minutes=limit + 5)
        req   = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            limit=limit,
        )
        bars = self._crypto_data.get_crypto_bars(req)
        return self._crypto_df(bars, symbol)

    def get_crypto_daily_bars(self, symbol: str, days: int = 30) -> pd.DataFrame:
        start = datetime.now(UTC) - timedelta(days=days + 2)
        req   = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            limit=days,
        )
        bars = self._crypto_data.get_crypto_bars(req)
        return self._crypto_df(bars, symbol)

    def get_crypto_latest_price(self, symbol: str) -> float:
        req   = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = self._crypto_data.get_crypto_latest_quote(req)[symbol]
        ask   = float(quote.ask_price or 0)
        bid   = float(quote.bid_price or 0)
        return (ask + bid) / 2.0 if (ask and bid) else ask or bid

    def _crypto_df(self, bars, symbol: str) -> pd.DataFrame:
        if not bars or symbol not in bars:
            return pd.DataFrame()
        raw = bars[symbol]
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(
            [{"timestamp": b.timestamp, "open": float(b.open), "high": float(b.high),
              "low": float(b.low), "close": float(b.close), "volume": float(b.volume)}
             for b in raw]
        ).set_index("timestamp")
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
        return df

    # ── Orders & positions ─────────────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        *,
        notional: float | None = None,
    ):
        if notional is not None:
            req = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        else:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        order = self._trading.submit_order(req)
        logger.info("Order: %s %s qty=%s → id=%s", side.value, symbol, qty, order.id)
        return order

    def close_position(self, symbol: str) -> None:
        try:
            self._trading.close_position(symbol)
            logger.info("Closed position: %s", symbol)
        except Exception as exc:
            logger.warning("close_position %s: %s", symbol, exc)

    def close_all_positions(self) -> None:
        try:
            self._trading.close_all_positions(cancel_orders=True)
            logger.info("Closed all positions")
        except Exception as exc:
            logger.warning("close_all_positions: %s", exc)

    def get_all_positions(self) -> list:
        return self._trading.get_all_positions()

    def cancel_all_orders(self) -> None:
        try:
            self._trading.cancel_orders()
        except Exception as exc:
            logger.warning("cancel_all_orders: %s", exc)
