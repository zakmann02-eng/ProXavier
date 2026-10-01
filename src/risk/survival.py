"""Survival rules: pause after consecutive losses, halt on drawdown from peak."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from src.config import SurvivalCfg


@dataclass
class SurvivalStatus:
    can_trade: bool
    reason: str = ""
    halted: bool = False        # requires manual resume


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def consecutive_losses(pnls: list[float]) -> int:
    n = 0
    for p in reversed(pnls):
        if p < 0:
            n += 1
        else:
            break
    return n


def drawdown_pct(equity_curve: list[float]) -> tuple[float, float]:
    """(current drawdown % from peak, peak)."""
    if not equity_curve:
        return 0.0, 0.0
    peak = max(equity_curve)
    cur = equity_curve[-1]
    return ((peak - cur) / peak * 100 if peak > 0 else 0.0), peak


def check(today: date, trades: list[tuple[date, float]], starting_capital: float,
          manual_halt: bool, cfg: SurvivalCfg, peak_from: int = 0) -> SurvivalStatus:
    """trades: (trade date, $ pnl) in chronological order. peak_from: drawdown peak is taken
    only from the equity after this many trades (set when trading is manually resumed)."""
    if manual_halt:
        return SurvivalStatus(False, "halted — waiting for manual review/resume", halted=True)
    equity = [starting_capital]
    for _, pnl in trades:
        equity.append(equity[-1] + pnl)
    dd, peak = drawdown_pct(equity[peak_from:])
    if dd >= cfg.max_drawdown_pct:
        return SurvivalStatus(False, f"account down {dd:.1f}% from peak ${peak:.2f} "
                                     f"(limit {cfg.max_drawdown_pct:g}%) — manual review required", halted=True)
    pnls = [p for _, p in trades]
    streak = consecutive_losses(pnls)
    if streak >= cfg.consecutive_losses_pause:
        last_loss_day = trades[-1][0]
        if week_start(last_loss_day) == week_start(today):
            return SurvivalStatus(False, f"{streak} consecutive losses — paused for the rest of the week")
    return SurvivalStatus(True)


def equity_now(trades: list[tuple[date, float]], starting_capital: float) -> float:
    return starting_capital + sum(p for _, p in trades)
