"""Performance statistics shared by the journal reports and the backtester."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Stats:
    trades: int
    wins: int
    losses: int
    win_rate: float          # %
    avg_win: float           # $
    avg_loss: float          # $ (negative)
    expectancy: float        # $ per trade
    expectancy_pct: float    # avg % return on the position per trade
    total_pnl: float
    max_drawdown: float      # $
    max_drawdown_pct: float  # % from peak equity
    trades_per_month: float
    final_equity: float

    def lines(self) -> list[str]:
        return [
            f"Trades: {self.trades} ({self.wins}W / {self.losses}L) — win rate {self.win_rate:.1f}%",
            f"Avg win ${self.avg_win:.2f} | avg loss ${self.avg_loss:.2f}",
            f"Expectancy ${self.expectancy:.2f}/trade ({self.expectancy_pct:+.1f}% per position)",
            f"Total P&L ${self.total_pnl:.2f} → equity ${self.final_equity:.2f}",
            f"Max drawdown ${self.max_drawdown:.2f} ({self.max_drawdown_pct:.1f}%)",
            f"Trades/month {self.trades_per_month:.1f}",
        ]


def compute_stats(trades: list[tuple[date, float, float]], starting_capital: float,
                  months: float | None = None) -> Stats:
    """trades: (day, $ pnl, % pnl) in chronological order. `months` = length of the period;
    defaults to the span between the first and last trade."""
    pnls = [t[1] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)
    equity, peak, max_dd, max_dd_pct = starting_capital, starting_capital, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd / peak * 100 if peak > 0 else 0)
    if months is None:
        if n >= 2:
            span = (trades[-1][0] - trades[0][0]).days / 30.44
            months = max(span, 1.0)
        else:
            months = 1.0
    return Stats(
        trades=n, wins=len(wins), losses=len(losses),
        win_rate=len(wins) / n * 100 if n else 0.0,
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        expectancy=sum(pnls) / n if n else 0.0,
        expectancy_pct=sum(t[2] for t in trades) / n if n else 0.0,
        total_pnl=sum(pnls), max_drawdown=max_dd, max_drawdown_pct=max_dd_pct,
        trades_per_month=n / months if months else 0.0, final_equity=equity,
    )
