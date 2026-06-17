"""
Xavier position manager — tracks day and swing positions across restarts.

Key behaviours
--------------
• Day trades are force-closed at EOD; swing trades are NOT.
• At TP1, closes 50% of the position and moves stop to breakeven.
• After TP1 the remaining half uses a trailing stop ratchet.
• All exits fire a Telegram notification via the `notifier` callable.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .signal_model import Signal

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass
class Position:
    symbol: str
    asset_class: str        # "equity" | "etf" | "crypto"
    trade_type: str         # "day" | "swing"
    strategy_id: str
    direction: str          # "LONG" | "SHORT"
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float | None
    qty: float
    entry_time: str
    risk_usd: float
    rr_ratio: float
    trailing_atr_mult: float
    tp1_hit: bool = False
    highest_price: float = 0.0   # for trailing-stop ratchet on longs
    lowest_price: float  = 0.0   # for trailing-stop ratchet on shorts


class PositionManager:
    def __init__(
        self,
        client,
        notifier,           # async callable(text: str) → None
        risk_manager,
        *,
        persist_path: str = "xavier_positions.json",
    ):
        self._client   = client
        self._notify   = notifier
        self._risk_mgr = risk_manager
        self._positions: dict[str, Position] = {}
        self._path = Path(persist_path)

        self.daily_pnl: float    = 0.0
        self.daily_trades: int   = 0
        self.daily_wins: int     = 0

        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for sym, d in data.items():
                self._positions[sym] = Position(**d)
            logger.info("Loaded %d position(s) from %s", len(self._positions), self._path)
        except Exception as exc:
            logger.warning("Could not load positions: %s", exc)

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(
                {sym: asdict(pos) for sym, pos in self._positions.items()},
                indent=2,
            ))
        except Exception as exc:
            logger.warning("Could not save positions: %s", exc)

    # ── State ──────────────────────────────────────────────────────────────────

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def day_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.trade_type == "day")

    @property
    def swing_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.trade_type == "swing")

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    # ── Entry ──────────────────────────────────────────────────────────────────

    def record_entry(self, signal: Signal) -> None:
        self._positions[signal.symbol] = Position(
            symbol=signal.symbol,
            asset_class=signal.asset_class,
            trade_type=signal.trade_type,
            strategy_id=signal.strategy_id,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_price=signal.sl_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            qty=signal.qty,
            entry_time=datetime.now(ET).isoformat(),
            risk_usd=signal.risk_usd,
            rr_ratio=signal.rr_ratio,
            trailing_atr_mult=signal.trailing_atr_mult,
            tp1_hit=False,
            highest_price=signal.entry_price,
            lowest_price=signal.entry_price,
        )
        self._save()
        self._risk_mgr.add_open_risk(
            signal.risk_usd,
            signal.entry_price * signal.qty,
            signal.asset_class,
        )
        logger.info(
            "Opened: [%s] %s %s x%.6f @ %.4f  TP1=%.4f  SL=%.4f",
            signal.strategy_id, signal.direction, signal.symbol,
            signal.qty, signal.entry_price, signal.tp1_price, signal.sl_price,
        )

    # ── TP / SL monitoring ─────────────────────────────────────────────────────

    async def check_positions(self) -> None:
        for symbol, pos in list(self._positions.items()):
            try:
                price = (
                    self._client.get_crypto_latest_price(symbol)
                    if pos.asset_class == "crypto"
                    else self._client.get_latest_price(symbol)
                )
            except Exception as exc:
                logger.warning("Price fetch failed %s: %s", symbol, exc)
                continue
            await self._evaluate(symbol, pos, price)

    async def _evaluate(self, symbol: str, pos: Position, price: float) -> None:
        # Update extremes for trailing stop
        pos.highest_price = max(pos.highest_price, price)
        pos.lowest_price  = min(pos.lowest_price, price)

        # Ratchet trailing stop after TP1 is hit
        if pos.tp1_hit and pos.trailing_atr_mult > 0:
            self._ratchet_stop(pos, price)

        hit_sl  = (
            (pos.direction == "LONG"  and price <= pos.stop_price)
            or (pos.direction == "SHORT" and price >= pos.stop_price)
        )
        hit_tp2 = pos.tp2_price is not None and (
            (pos.direction == "LONG"  and price >= pos.tp2_price)
            or (pos.direction == "SHORT" and price <= pos.tp2_price)
        )
        hit_tp1 = (
            (pos.direction == "LONG"  and price >= pos.tp1_price)
            or (pos.direction == "SHORT" and price <= pos.tp1_price)
        )

        if hit_sl:
            await self._close(symbol, pos, price, reason="SL")
        elif hit_tp2:
            await self._close(symbol, pos, price, reason="TP2")
        elif hit_tp1 and not pos.tp1_hit:
            await self._partial_exit(symbol, pos, price)

    def _ratchet_stop(self, pos: Position, price: float) -> None:
        """Trail stop by ~1% of price per ATR multiple."""
        trail = price * 0.01 * pos.trailing_atr_mult
        if pos.direction == "LONG":
            new_stop = round(pos.highest_price - trail, 4)
            if new_stop > pos.stop_price:
                pos.stop_price = new_stop
                self._save()
        else:
            new_stop = round(pos.lowest_price + trail, 4)
            if new_stop < pos.stop_price:
                pos.stop_price = new_stop
                self._save()

    async def _partial_exit(self, symbol: str, pos: Position, price: float) -> None:
        """Sell 50% at TP1, move stop to breakeven, activate trailing stop."""
        from alpaca.trading.enums import OrderSide
        half = max(1.0, float(int(pos.qty * 0.5))) if pos.asset_class != "crypto" \
               else round(pos.qty * 0.5, 6)
        try:
            side = OrderSide.SELL if pos.direction == "LONG" else OrderSide.BUY
            self._client.place_market_order(symbol, half, side)
        except Exception as exc:
            logger.warning("Partial exit failed %s: %s", symbol, exc)
            return

        pnl = (price - pos.entry_price) * half if pos.direction == "LONG" \
              else (pos.entry_price - price) * half

        self._accrue_pnl(pnl)
        self._risk_mgr.record_trade_result(pnl)

        pos.qty -= half
        pos.tp1_hit   = True
        pos.stop_price = pos.entry_price  # move stop to breakeven
        self._save()

        sign = "+" if pnl >= 0 else ""
        await self._notify(
            f"📊 *{symbol}* TP1 — partial exit\n"
            f"{pos.direction} closed 50% @ ${price:.4f}  P&L: ${sign}{pnl:.2f}\n"
            f"Remaining {pos.qty:.6f} units — stop at breakeven"
        )

    # ── Closing ────────────────────────────────────────────────────────────────

    async def close_day_positions(self, *, reason: str = "EOD") -> None:
        day_pos = {s: p for s, p in list(self._positions.items()) if p.trade_type == "day"}
        for symbol, pos in day_pos.items():
            try:
                price = (
                    self._client.get_crypto_latest_price(symbol)
                    if pos.asset_class == "crypto"
                    else self._client.get_latest_price(symbol)
                )
            except Exception:
                price = pos.entry_price
            await self._close(symbol, pos, price, reason=reason)

    async def close_all(self, *, reason: str = "MANUAL") -> None:
        for symbol, pos in list(self._positions.items()):
            try:
                price = (
                    self._client.get_crypto_latest_price(symbol)
                    if pos.asset_class == "crypto"
                    else self._client.get_latest_price(symbol)
                )
            except Exception:
                price = pos.entry_price
            await self._close(symbol, pos, price, reason=reason)
        try:
            self._client.close_all_positions()
        except Exception as exc:
            logger.warning("Broker close_all: %s", exc)

    async def _close(self, symbol: str, pos: Position, exit_price: float, *, reason: str) -> None:
        self._client.close_position(symbol)

        pnl = (exit_price - pos.entry_price) * pos.qty if pos.direction == "LONG" \
              else (pos.entry_price - exit_price) * pos.qty

        self._accrue_pnl(pnl)
        self._risk_mgr.remove_open_risk(pos.risk_usd, pos.entry_price * pos.qty, pos.asset_class)
        self._risk_mgr.record_trade_result(pnl)

        del self._positions[symbol]
        self._save()

        sign  = "+" if pnl >= 0 else ""
        emoji = "✅" if pnl >= 0 else "❌"
        await self._notify(
            f"{emoji} *{symbol}* [{pos.strategy_id}] → {reason}\n"
            f"{pos.direction} x{pos.qty:.6f}  Entry ${pos.entry_price:.4f} → ${exit_price:.4f}\n"
            f"P&L: ${sign}{pnl:.2f}"
        )
        logger.info("Closed %s [%s/%s] P&L=$%s%.2f", symbol, reason, pos.strategy_id, sign, pnl)

    def _accrue_pnl(self, pnl: float) -> None:
        self.daily_pnl    += pnl
        self.daily_trades += 1
        if pnl > 0:
            self.daily_wins += 1

    # ── Reporting ──────────────────────────────────────────────────────────────

    async def send_daily_report(self) -> None:
        win_rate = (self.daily_wins / self.daily_trades * 100) if self.daily_trades else 0.0
        sign     = "+" if self.daily_pnl >= 0 else ""
        await self._notify(
            f"📊 *Xavier Daily Report*\n"
            f"Trades: {self.daily_trades}  |  Wins: {self.daily_wins} ({win_rate:.0f}%)\n"
            f"Net P&L: ${sign}{self.daily_pnl:.2f}\n"
            f"Open day: {self.day_count}  |  Open swing: {self.swing_count}\n"
            f"Daily PnL from risk: ${self._risk_mgr.daily_pnl:.2f}"
        )
        self.daily_pnl   = 0.0
        self.daily_trades = 0
        self.daily_wins  = 0

    def summary_text(self) -> str:
        if not self._positions:
            return "No open positions."
        lines = [f"*Open Positions ({self.open_count})*\n"]
        for sym, pos in self._positions.items():
            sign = "+" if pos.entry_price > 0 else ""
            lines.append(
                f"• `{sym}` [{pos.strategy_id}] {pos.trade_type.upper()}\n"
                f"  {pos.direction} x{pos.qty:.4f} @ ${pos.entry_price:.4f}\n"
                f"  TP ${pos.tp1_price:.4f}  SL ${pos.stop_price:.4f}"
            )
        return "\n".join(lines)
