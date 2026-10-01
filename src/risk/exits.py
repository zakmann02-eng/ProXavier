"""Exit rules — the first one triggered wins. Shared by the live monitor and the backtester."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.analysis.setups import LONG
from src.config import ExitsCfg

TAKE_PROFIT = "take profit"
TARGET_ZONE = "target zone reached"
STRUCTURAL = "structural stop"
HARD_STOP = "hard stop"
BREAKEVEN_STOP = "breakeven stop"
TIME_STOP = "time stop"


@dataclass
class Event:
    kind: str            # "exit" | "breakeven"
    reason: str
    detail: str = ""


class ExitManager:
    def __init__(self, direction: str, entry_price: float, structural_stop: float,
                 target_level: float | None, time_stop: datetime, cfg: ExitsCfg):
        self.direction = direction
        self.entry_price = entry_price          # per-share debit paid
        self.structural_stop = structural_stop  # SPY level
        self.target_level = target_level        # SPY level of the next opposing zone
        self.time_stop = time_stop
        self.cfg = cfg
        self.stop_price = entry_price * (1 - cfg.hard_stop_pct / 100)
        self.breakeven_active = False
        self.closed = False

    def pnl_pct(self, mark: float) -> float:
        return (mark / self.entry_price - 1) * 100 if self.entry_price > 0 else 0.0

    def on_bar_close(self, now: datetime, spy_close: float) -> Event | None:
        """Structural stop: SPY CLOSES beyond the setup candle's tail."""
        if self.closed:
            return None
        beyond = spy_close < self.structural_stop if self.direction == LONG else spy_close > self.structural_stop
        if beyond:
            return self._exit(STRUCTURAL, f"SPY closed {spy_close:.2f} beyond {self.structural_stop:.2f}")
        return None

    def on_mark(self, now: datetime, mark: float, spy_price: float) -> list[Event]:
        """Called on every poll with the position's current per-share value."""
        if self.closed:
            return []
        if now >= self.time_stop:
            return [self._exit(TIME_STOP, f"{self.time_stop:%H:%M} reached")]
        if mark <= self.stop_price:
            reason = BREAKEVEN_STOP if self.breakeven_active else HARD_STOP
            return [self._exit(reason, f"mark {mark:.2f} ≤ stop {self.stop_price:.2f}")]
        pct = self.pnl_pct(mark)
        if pct >= self.cfg.take_profit_pct:
            return [self._exit(TAKE_PROFIT, f"+{pct:.0f}%")]
        if self.cfg.take_profit_at_zone and self.target_level is not None:
            hit = spy_price >= self.target_level if self.direction == LONG else spy_price <= self.target_level
            if hit:
                return [self._exit(TARGET_ZONE, f"SPY {spy_price:.2f} reached {self.target_level:.2f}")]
        if not self.breakeven_active and pct >= self.cfg.breakeven_trigger_pct:
            self.breakeven_active = True
            self.stop_price = self.entry_price
            return [Event("breakeven", "move stop to breakeven", f"+{pct:.0f}%; stop → {self.entry_price:.2f}")]
        return []

    def _exit(self, reason: str, detail: str) -> Event:
        self.closed = True
        return Event("exit", reason, detail)
