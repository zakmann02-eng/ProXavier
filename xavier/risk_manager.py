"""
Portfolio-level risk gate for Xavier.

Checks every proposed signal against hard limits before any order is placed.
Can scale down qty rather than outright reject to maximise capital utilisation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .signal_model import Signal

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    adjusted_qty: float | None = None


class RiskManager:
    def __init__(
        self,
        *,
        max_portfolio_risk_pct: float = 6.0,
        max_single_risk_pct: float    = 2.0,
        daily_loss_limit_pct: float   = 3.0,
        consecutive_loss_halt: int    = 4,
        max_crypto_alloc_pct: float   = 20.0,
        max_open_positions: int       = 5,
    ):
        self._max_portfolio_risk = max_portfolio_risk_pct / 100.0
        self._max_single_risk    = max_single_risk_pct / 100.0
        self._daily_loss_limit   = daily_loss_limit_pct / 100.0
        self._consec_halt        = consecutive_loss_halt
        self._max_crypto_alloc   = max_crypto_alloc_pct / 100.0
        self._max_positions      = max_open_positions

        self._equity_start: float    = 0.0
        self._daily_pnl: float       = 0.0
        self._consecutive_losses: int = 0
        self._open_risk_usd: float   = 0.0
        self._crypto_notional: float = 0.0

    def set_day_start(self, equity: float) -> None:
        self._equity_start = equity
        self._daily_pnl    = 0.0
        logger.info("RiskManager day start: equity=%.2f", equity)

    def record_trade_result(self, pnl: float) -> None:
        self._daily_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._consec_halt:
                logger.warning(
                    "RiskManager: %d consecutive losses — trading halted",
                    self._consecutive_losses,
                )
        else:
            self._consecutive_losses = 0

    def add_open_risk(self, risk_usd: float, notional: float, asset_class: str) -> None:
        self._open_risk_usd += risk_usd
        if asset_class == "crypto":
            self._crypto_notional += notional

    def remove_open_risk(self, risk_usd: float, notional: float, asset_class: str) -> None:
        self._open_risk_usd  = max(0.0, self._open_risk_usd - risk_usd)
        if asset_class == "crypto":
            self._crypto_notional = max(0.0, self._crypto_notional - notional)

    @property
    def is_halted(self) -> bool:
        return self._consecutive_losses >= self._consec_halt

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    def check(
        self,
        signal: Signal,
        equity: float,
        open_positions: int,
    ) -> RiskDecision:
        if equity <= 0:
            return RiskDecision(False, "no equity data")

        if open_positions >= self._max_positions:
            return RiskDecision(False, f"position cap ({self._max_positions}) reached")

        if self._equity_start > 0:
            daily_loss_pct = self._daily_pnl / self._equity_start
            if daily_loss_pct <= -self._daily_loss_limit:
                return RiskDecision(False, f"daily loss limit {daily_loss_pct:.1%}")

        if self._consecutive_losses >= self._consec_halt:
            return RiskDecision(False, f"{self._consecutive_losses} consecutive losses")

        new_total_risk = (self._open_risk_usd + signal.risk_usd) / equity
        if new_total_risk > self._max_portfolio_risk:
            return RiskDecision(False, f"portfolio risk cap {new_total_risk:.1%}")

        single_risk_pct = signal.risk_usd / equity
        adjusted_qty: float | None = None
        if single_risk_pct > self._max_single_risk:
            max_risk_usd = equity * self._max_single_risk
            scale        = max_risk_usd / signal.risk_usd
            raw_qty      = signal.qty * scale
            if signal.asset_class == "crypto":
                new_qty = round(raw_qty, 6)
            else:
                new_qty = max(1.0, float(int(raw_qty)))
            if new_qty < 1e-6:
                return RiskDecision(False, "position too small after scaling")
            adjusted_qty = new_qty
            logger.info(
                "RiskManager scaled %s qty %.6f → %.6f (single-risk limit)",
                signal.symbol, signal.qty, new_qty,
            )

        if signal.asset_class == "crypto":
            qty_to_use = adjusted_qty if adjusted_qty is not None else signal.qty
            new_crypto = self._crypto_notional + signal.entry_price * qty_to_use
            if new_crypto / equity > self._max_crypto_alloc:
                return RiskDecision(False, f"crypto alloc cap {self._max_crypto_alloc:.0%}")

        return RiskDecision(True, "ok", adjusted_qty=adjusted_qty)
