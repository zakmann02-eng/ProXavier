"""
Xavier Multi-Strategy Engine

Five strategies run in parallel every scan cycle. Each returns a Signal or None.
The SignalBus deduplicates and ranks by score before the RiskManager filters them.

Strategies
----------
1. ORBStrategy          — Opening Range Breakout (day, equity/ETF)
2. VWAPReversionStrategy — VWAP mean reversion (day, equity/ETF)
3. MomentumStrategy     — 20-day high breakout (swing, equity/ETF)
4. ETFRotationStrategy  — Sector ETF relative strength ranking (swing, ETF)
5. CryptoTrendStrategy  — Multi-timeframe crypto trend (day+swing, crypto)
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .indicators import adx, atr, bollinger_bands, ema, macd, rsi, vwap
from .signal_model import Signal

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _size_equity(equity: float, risk_pct: float, entry: float, stop: float) -> float:
    risk_usd    = equity * (risk_pct / 100.0)
    risk_per_sh = abs(entry - stop)
    if risk_per_sh < 0.01:
        return 0.0
    return max(1.0, float(int(risk_usd / risk_per_sh)))


def _size_crypto(
    equity: float, risk_pct: float, entry: float, stop: float,
    max_notional_pct: float = 0.05,
) -> float:
    risk_usd      = equity * (risk_pct / 100.0)
    risk_per_unit = abs(entry - stop)
    if risk_per_unit < 0.0001 or entry <= 0:
        return 0.0
    qty = risk_usd / risk_per_unit
    max_qty = (equity * max_notional_pct) / entry
    qty = min(qty, max_qty)
    return round(qty, 6)


class ORBStrategy:
    STRATEGY_ID = "ORB"
    MIN_SCORE   = 55

    def __init__(self, *, orb_minutes: int = 15, min_rr: float = 1.5,
                 min_price: float = 5.0, allow_short: bool = False):
        self.orb_minutes = orb_minutes
        self.min_rr      = min_rr
        self.min_price   = min_price
        self.allow_short = allow_short
        self._orbs: dict[str, dict[str, float]] = {}

    def capture(self, symbol: str, df: pd.DataFrame) -> bool:
        if df.empty:
            return False
        rows = df[
            (df.index.hour == 9)
            & (df.index.minute >= 30)
            & (df.index.minute < 30 + self.orb_minutes)
        ]
        if rows.empty:
            return False
        self._orbs[symbol] = {
            "high": float(rows["high"].max()),
            "low":  float(rows["low"].min()),
        }
        logger.debug("ORB %s — H=%.2f L=%.2f", symbol,
                     self._orbs[symbol]["high"], self._orbs[symbol]["low"])
        return True

    def clear(self) -> None:
        self._orbs.clear()

    def get_orb(self, symbol: str) -> dict | None:
        return self._orbs.get(symbol)

    def evaluate(self, symbol: str, df: pd.DataFrame, equity: float,
                 risk_pct: float, asset_class: str = "equity") -> Signal | None:
        if len(df) < 30:
            return None
        orb = self._orbs.get(symbol)
        if not orb:
            return None

        close  = df["close"]
        high_s = df["high"]
        low_s  = df["low"]
        volume = df["volume"]

        price = float(close.iloc[-1])
        if price < self.min_price:
            return None

        ema9   = float(ema(close, 9).iloc[-1])
        ema20  = float(ema(close, 20).iloc[-1])
        rsi14  = float(rsi(close, 14).iloc[-1])
        atr14  = max(float(atr(high_s, low_s, close, 14).iloc[-1]), price * 0.003)
        vwap_v = float(vwap(df).iloc[-1])
        ml, sl, _ = macd(close)
        macd_v = float(ml.iloc[-1])
        sig_v  = float(sl.iloc[-1])

        avg_vol   = float(volume.iloc[-21:-1].mean()) if len(volume) > 21 else float(volume.mean())
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

        orb_high = orb["high"]
        orb_low  = orb["low"]

        direction: str | None = None
        triggers: list[str]   = []
        score = 0

        if price > orb_high:
            direction = "LONG"
            score += 40; triggers.append("ORB_BREAK_HIGH")
            if ema9 > ema20:
                score += 15; triggers.append("EMA_BULL")
            if 50.0 <= rsi14 <= 75.0:
                score += 15; triggers.append(f"RSI_{rsi14:.0f}")
            if price > vwap_v:
                score += 10; triggers.append("ABOVE_VWAP")
            if vol_ratio >= 1.5:
                score += 10; triggers.append(f"VOL_{vol_ratio:.1f}x")
            if macd_v > sig_v:
                score += 10; triggers.append("MACD_BULL")

        elif self.allow_short and price < orb_low:
            direction = "SHORT"
            score += 40; triggers.append("ORB_BREAK_LOW")
            if ema9 < ema20:
                score += 15; triggers.append("EMA_BEAR")
            if 25.0 <= rsi14 <= 50.0:
                score += 15; triggers.append(f"RSI_{rsi14:.0f}")
            if price < vwap_v:
                score += 10; triggers.append("BELOW_VWAP")
            if vol_ratio >= 1.5:
                score += 10; triggers.append(f"VOL_{vol_ratio:.1f}x")
            if macd_v < sig_v:
                score += 10; triggers.append("MACD_BEAR")

        if direction is None or score < self.MIN_SCORE:
            return None

        if direction == "LONG":
            sl_price = round(max(orb_low, price - 1.5 * atr14), 2)
            risk_per = price - sl_price
            tp1 = round(price + self.min_rr * risk_per, 2)
            tp2 = round(price + 2.5 * risk_per, 2)
        else:
            sl_price = round(min(orb_high, price + 1.5 * atr14), 2)
            risk_per = sl_price - price
            tp1 = round(price - self.min_rr * risk_per, 2)
            tp2 = round(price - 2.5 * risk_per, 2)

        if risk_per < 0.01:
            return None

        qty = _size_equity(equity, risk_pct, price, sl_price)
        if qty == 0:
            return None

        return Signal(
            symbol=symbol, asset_class=asset_class, trade_type="day",
            strategy_id=self.STRATEGY_ID, direction=direction, score=score,
            entry_price=price, tp1_price=tp1, tp2_price=tp2, sl_price=sl_price,
            qty=qty, risk_usd=risk_per * qty, rr_ratio=self.min_rr,
            trailing_atr_mult=1.5, timeframe="1m", triggers=triggers,
            raw_indicators={"rsi": round(rsi14, 1), "ema9": round(ema9, 2),
                            "ema20": round(ema20, 2), "vol_ratio": round(vol_ratio, 2)},
        )


class VWAPReversionStrategy:
    STRATEGY_ID   = "VWAP_REV"
    MIN_SCORE     = 50
    MIN_DEVIATION = 1.5

    def __init__(self, *, min_rr: float = 1.2, min_price: float = 5.0,
                 allow_short: bool = False):
        self.min_rr      = min_rr
        self.min_price   = min_price
        self.allow_short = allow_short

    def evaluate(self, symbol: str, df: pd.DataFrame, equity: float,
                 risk_pct: float, asset_class: str = "equity") -> Signal | None:
        if len(df) < 20:
            return None

        now_et = datetime.now(ET)
        in_window = (
            (now_et.hour == 10 and now_et.minute >= 30)
            or (11 <= now_et.hour < 14)
            or (now_et.hour == 14 and now_et.minute <= 30)
        )
        if not in_window:
            return None

        close  = df["close"]
        high_s = df["high"]
        low_s  = df["low"]
        volume = df["volume"]

        price = float(close.iloc[-1])
        if price < self.min_price:
            return None

        atr14  = max(float(atr(high_s, low_s, close, 14).iloc[-1]), price * 0.003)
        vwap_v = float(vwap(df).iloc[-1])
        rsi14  = float(rsi(close, 14).iloc[-1])
        bb_u, _, bb_l = bollinger_bands(close, 20, 2.0)
        bb_upper = float(bb_u.iloc[-1])
        bb_lower = float(bb_l.iloc[-1])

        vol_now   = float(volume.iloc[-1])
        vol_prev3 = float(volume.iloc[-4:-1].mean()) if len(volume) > 4 else vol_now
        avg_vol   = float(volume.iloc[-21:-1].mean()) if len(volume) > 21 else vol_now
        deviation = (price - vwap_v) / atr14

        direction: str | None = None
        triggers: list[str]   = []
        score = 0

        if deviation <= -self.MIN_DEVIATION:
            direction = "LONG"
            score += 30; triggers.append(f"BELOW_VWAP_{abs(deviation):.1f}ATR")
            if deviation <= -2.0:
                score += 10; triggers.append("DEEP_OVERSOLD")
            if rsi14 < 35:
                score += 20; triggers.append(f"RSI_OS_{rsi14:.0f}")
            elif rsi14 < 45:
                score += 10
            if price < bb_lower:
                score += 15; triggers.append("BB_LOWER")
            if vol_now < vol_prev3 * 0.8:
                score += 15; triggers.append("VOL_EXHAUST")
            elif vol_now > avg_vol * 1.3:
                score += 10; triggers.append("VOL_UPTICK")

        elif self.allow_short and deviation >= self.MIN_DEVIATION:
            direction = "SHORT"
            score += 30; triggers.append(f"ABOVE_VWAP_{deviation:.1f}ATR")
            if deviation >= 2.0:
                score += 10; triggers.append("DEEP_OVERBOUGHT")
            if rsi14 > 65:
                score += 20; triggers.append(f"RSI_OB_{rsi14:.0f}")
            elif rsi14 > 55:
                score += 10
            if price > bb_upper:
                score += 15; triggers.append("BB_UPPER")
            if vol_now < vol_prev3 * 0.8:
                score += 15; triggers.append("VOL_EXHAUST")

        if direction is None or score < self.MIN_SCORE:
            return None

        if direction == "LONG":
            sl_price = round(price - 1.5 * atr14, 2)
            risk_per = price - sl_price
            tp1 = round(vwap_v, 2)
            tp2 = round(vwap_v + 0.5 * atr14, 2)
        else:
            sl_price = round(price + 1.5 * atr14, 2)
            risk_per = sl_price - price
            tp1 = round(vwap_v, 2)
            tp2 = round(vwap_v - 0.5 * atr14, 2)

        rr = abs(tp1 - price) / risk_per if risk_per > 0 else 0.0
        if rr < self.min_rr:
            return None

        qty = _size_equity(equity, risk_pct, price, sl_price)
        if qty == 0:
            return None

        return Signal(
            symbol=symbol, asset_class=asset_class, trade_type="day",
            strategy_id=self.STRATEGY_ID, direction=direction, score=score,
            entry_price=price, tp1_price=tp1, tp2_price=tp2, sl_price=sl_price,
            qty=qty, risk_usd=risk_per * qty, rr_ratio=round(rr, 2),
            trailing_atr_mult=0.0, timeframe="1m", triggers=triggers,
            raw_indicators={"rsi": round(rsi14, 1), "vwap": round(vwap_v, 2),
                            "dev_atr": round(deviation, 2)},
        )


class MomentumStrategy:
    STRATEGY_ID = "MOMENTUM"
    MIN_SCORE   = 60
    LOOKBACK    = 20

    def __init__(self, *, min_rr: float = 2.0, min_price: float = 5.0,
                 allow_short: bool = False):
        self.min_rr      = min_rr
        self.min_price   = min_price
        self.allow_short = allow_short

    def evaluate(self, symbol: str, df_daily: pd.DataFrame, equity: float,
                 risk_pct: float, asset_class: str = "equity") -> Signal | None:
        if len(df_daily) < self.LOOKBACK + 5:
            return None

        close  = df_daily["close"]
        high_s = df_daily["high"]
        low_s  = df_daily["low"]
        volume = df_daily["volume"]

        price = float(close.iloc[-1])
        if price < self.min_price:
            return None

        prev_high = float(high_s.iloc[-(self.LOOKBACK + 1):-1].max())
        prev_low  = float(low_s.iloc[-(self.LOOKBACK + 1):-1].min())
        atr14  = max(float(atr(high_s, low_s, close, 14).iloc[-1]), price * 0.003)
        rsi14  = float(rsi(close, 14).iloc[-1])
        adx14  = float(adx(high_s, low_s, close, 14).iloc[-1]) if len(df_daily) > 20 else 0.0
        n50    = min(50, len(close) - 1)
        ema50  = float(ema(close, n50).iloc[-1])
        avg_vol   = float(volume.iloc[-self.LOOKBACK:-1].mean())
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

        direction: str | None = None
        triggers: list[str]   = []
        score = 0

        if float(high_s.iloc[-1]) > prev_high:
            direction = "LONG"
            score += 40; triggers.append(f"{self.LOOKBACK}D_HIGH_BREAK")
            if adx14 > 25:
                score += 20; triggers.append(f"ADX_{adx14:.0f}")
            if vol_ratio >= 1.5:
                score += 20; triggers.append(f"VOL_{vol_ratio:.1f}x")
            if price > ema50:
                score += 10; triggers.append("ABOVE_EMA50")
            if 55.0 <= rsi14 <= 75.0:
                score += 10; triggers.append(f"RSI_{rsi14:.0f}")

        elif self.allow_short and float(low_s.iloc[-1]) < prev_low:
            direction = "SHORT"
            score += 40; triggers.append(f"{self.LOOKBACK}D_LOW_BREAK")
            if adx14 > 25:
                score += 20; triggers.append(f"ADX_{adx14:.0f}")
            if vol_ratio >= 1.5:
                score += 20; triggers.append(f"VOL_{vol_ratio:.1f}x")
            if price < ema50:
                score += 10; triggers.append("BELOW_EMA50")
            if 25.0 <= rsi14 <= 45.0:
                score += 10; triggers.append(f"RSI_{rsi14:.0f}")

        if direction is None or score < self.MIN_SCORE:
            return None

        if direction == "LONG":
            sl_price = round(price - 2.0 * atr14, 2)
            risk_per = price - sl_price
            tp1 = round(price + self.min_rr * risk_per, 2)
            tp2 = round(price + 3.5 * risk_per, 2)
        else:
            sl_price = round(price + 2.0 * atr14, 2)
            risk_per = sl_price - price
            tp1 = round(price - self.min_rr * risk_per, 2)
            tp2 = round(price - 3.5 * risk_per, 2)

        qty = _size_equity(equity, risk_pct, price, sl_price)
        if qty == 0:
            return None

        return Signal(
            symbol=symbol, asset_class=asset_class, trade_type="swing",
            strategy_id=self.STRATEGY_ID, direction=direction, score=score,
            entry_price=price, tp1_price=tp1, tp2_price=tp2, sl_price=sl_price,
            qty=qty, risk_usd=risk_per * qty, rr_ratio=self.min_rr,
            trailing_atr_mult=1.5, timeframe="1d", triggers=triggers,
            raw_indicators={"rsi": round(rsi14, 1), "adx": round(adx14, 1),
                            "vol_ratio": round(vol_ratio, 2)},
        )


class ETFRotationStrategy:
    STRATEGY_ID = "ETF_ROT"
    MIN_SCORE   = 60

    def __init__(self, *, min_rr: float = 1.5, top_n: int = 2):
        self.min_rr  = min_rr
        self.top_n   = top_n
        self._rankings: list[tuple[str, float]] = []

    def update_rankings(self, symbol_dfs: dict[str, pd.DataFrame]) -> None:
        moms: list[tuple[str, float]] = []
        for sym, df in symbol_dfs.items():
            if len(df) < 6:
                continue
            ret5 = float(df["close"].iloc[-1]) / float(df["close"].iloc[-6]) - 1.0
            moms.append((sym, ret5))
        self._rankings = sorted(moms, key=lambda x: x[1], reverse=True)
        logger.debug("ETF rankings: %s", [(s, f"{m:+.1%}") for s, m in self._rankings[:5]])

    def evaluate(self, symbol: str, df_daily: pd.DataFrame, equity: float,
                 risk_pct: float) -> Signal | None:
        if len(df_daily) < 20 or not self._rankings:
            return None

        rank = next((i for i, (s, _) in enumerate(self._rankings) if s == symbol), None)
        if rank is None or rank >= self.top_n:
            return None

        close  = df_daily["close"]
        high_s = df_daily["high"]
        low_s  = df_daily["low"]

        price   = float(close.iloc[-1])
        n50     = min(50, len(close) - 1)
        ema50_v = float(ema(close, n50).iloc[-1])
        rsi14_v = float(rsi(close, 14).iloc[-1])
        atr14_v = max(float(atr(high_s, low_s, close, 14).iloc[-1]), price * 0.002)

        _, mom5 = self._rankings[rank]
        score = 0
        triggers: list[str] = []

        score += (40 if rank == 0 else 30)
        triggers.append(f"ETF_RANK{rank + 1}_{mom5:+.1%}")

        if price > ema50_v:
            score += 20; triggers.append("ABOVE_EMA50")
        if mom5 > 0.02:
            score += 20; triggers.append(f"MOM5D_{mom5:+.1%}")
        if rsi14_v > 55:
            score += 10; triggers.append(f"RSI_{rsi14_v:.0f}")
        if close.iloc[-1] > close.iloc[-5]:
            score += 10; triggers.append("5D_UPTREND")

        if score < self.MIN_SCORE:
            return None

        sl_price = round(price - 2.0 * atr14_v, 2)
        risk_per  = price - sl_price
        tp1 = round(price + self.min_rr * risk_per, 2)
        tp2 = round(price + 3.0 * risk_per, 2)

        qty = _size_equity(equity, risk_pct, price, sl_price)
        if qty == 0:
            return None

        return Signal(
            symbol=symbol, asset_class="etf", trade_type="swing",
            strategy_id=self.STRATEGY_ID, direction="LONG", score=score,
            entry_price=price, tp1_price=tp1, tp2_price=tp2, sl_price=sl_price,
            qty=qty, risk_usd=risk_per * qty, rr_ratio=self.min_rr,
            trailing_atr_mult=2.0, timeframe="1d", triggers=triggers,
            raw_indicators={"rsi": round(rsi14_v, 1), "rank": rank,
                            "mom5d": round(mom5, 4)},
        )


class CryptoTrendStrategy:
    STRATEGY_ID = "CRYPTO_TREND"
    MIN_SCORE   = 55

    def __init__(self, *, min_rr: float = 1.5, allow_short: bool = False):
        self.min_rr      = min_rr
        self.allow_short = allow_short

    def evaluate(self, symbol: str, df_1m: pd.DataFrame, df_daily: pd.DataFrame,
                 equity: float, risk_pct: float) -> Signal | None:
        if len(df_1m) < 50 or len(df_daily) < 20:
            return None

        close_1m = df_1m["close"]
        high_1m  = df_1m["high"]
        low_1m   = df_1m["low"]
        vol_1m   = df_1m["volume"]
        close_d  = df_daily["close"]

        price = float(close_1m.iloc[-1])
        if price <= 0:
            return None

        ema20_d = float(ema(close_d, 20).iloc[-1])
        ema50_d = float(ema(close_d, min(50, len(close_d) - 1)).iloc[-1])
        daily_bull = price > ema20_d > ema50_d
        daily_bear = price < ema20_d < ema50_d

        if not (daily_bull or (self.allow_short and daily_bear)):
            return None

        ema9_v  = float(ema(close_1m, 9).iloc[-1])
        ema20_v = float(ema(close_1m, 20).iloc[-1])
        rsi14_v = float(rsi(close_1m, 14).iloc[-1])
        atr14_v = max(float(atr(high_1m, low_1m, close_1m, 14).iloc[-1]), price * 0.003)
        vwap_v  = float(vwap(df_1m).iloc[-1])
        ml, sl, _ = macd(close_1m)
        macd_v  = float(ml.iloc[-1])
        sig_v   = float(sl.iloc[-1])

        avg_vol   = float(vol_1m.iloc[-21:-1].mean()) if len(vol_1m) > 21 else float(vol_1m.mean())
        vol_ratio = float(vol_1m.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

        direction: str | None = None
        triggers: list[str]   = []
        score = 0

        if daily_bull:
            if ema9_v > ema20_v:
                score += 20; triggers.append("EMA_BULL_1M")
            else:
                return None
            score += 20; triggers.append("EMA_BULL_DAILY")
            if 45.0 <= rsi14_v <= 70.0:
                score += 15; triggers.append(f"RSI_{rsi14_v:.0f}")
            if price > vwap_v:
                score += 15; triggers.append("ABOVE_VWAP")
            if macd_v > sig_v:
                score += 15; triggers.append("MACD_BULL")
            if vol_ratio >= 1.3:
                score += 15; triggers.append(f"VOL_{vol_ratio:.1f}x")
            if score >= self.MIN_SCORE:
                direction = "LONG"

        elif self.allow_short and daily_bear:
            if ema9_v < ema20_v:
                score += 20; triggers.append("EMA_BEAR_1M")
            else:
                return None
            score += 20; triggers.append("EMA_BEAR_DAILY")
            if 30.0 <= rsi14_v <= 55.0:
                score += 15; triggers.append(f"RSI_{rsi14_v:.0f}")
            if price < vwap_v:
                score += 15; triggers.append("BELOW_VWAP")
            if macd_v < sig_v:
                score += 15; triggers.append("MACD_BEAR")
            if vol_ratio >= 1.3:
                score += 15; triggers.append(f"VOL_{vol_ratio:.1f}x")
            if score >= self.MIN_SCORE:
                direction = "SHORT"

        if direction is None:
            return None

        if direction == "LONG":
            sl_price = round(price - 2.0 * atr14_v, 6)
            risk_per = price - sl_price
            tp1 = round(price + self.min_rr * risk_per, 6)
            tp2 = round(price + 3.0 * risk_per, 6)
        else:
            sl_price = round(price + 2.0 * atr14_v, 6)
            risk_per = sl_price - price
            tp1 = round(price - self.min_rr * risk_per, 6)
            tp2 = round(price - 3.0 * risk_per, 6)

        qty = _size_crypto(equity, risk_pct, price, sl_price)
        if qty == 0:
            return None

        daily_momentum = abs(float(close_d.iloc[-1]) / float(close_d.iloc[-6]) - 1.0) if len(close_d) > 5 else 0.0
        trade_type = "swing" if daily_momentum > 0.03 else "day"

        return Signal(
            symbol=symbol, asset_class="crypto", trade_type=trade_type,
            strategy_id=self.STRATEGY_ID, direction=direction, score=score,
            entry_price=price, tp1_price=tp1, tp2_price=tp2, sl_price=sl_price,
            qty=qty, risk_usd=risk_per * qty, rr_ratio=self.min_rr,
            trailing_atr_mult=1.5, timeframe="1m", triggers=triggers,
            raw_indicators={"rsi": round(rsi14_v, 1), "vol_ratio": round(vol_ratio, 2),
                            "daily_bull": daily_bull, "ema_diff": round(ema9_v - ema20_v, 4)},
        )
