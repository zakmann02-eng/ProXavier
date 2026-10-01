"""Backtest report: the Phase 0 metrics, split into in-sample and hold-out periods."""
from __future__ import annotations

from collections import Counter
from datetime import date

from backtest.engine import BtResult, BtTrade
from src.journal.stats import compute_stats


def _months(start: date, end: date) -> float:
    return max((end - start).days / 30.44, 1.0)


def summarize(trades: list[BtTrade], start: date, end: date, capital: float) -> list[str]:
    st = compute_stats([(t.day, t.pnl, t.pnl_pct) for t in trades], capital, months=_months(start, end))
    lines = st.lines()
    if trades:
        by_setup = Counter(t.setup for t in trades)
        wins = Counter(t.setup for t in trades if t.pnl > 0)
        pnl = Counter()
        for t in trades:
            pnl[t.setup] += t.pnl
        lines.append("By setup: " + ", ".join(f"{k} {v} ({wins[k] / v:.0%} win, ${pnl[k]:+.0f})"
                                              for k, v in by_setup.most_common()))
        lines.append("Exit reasons: " + ", ".join(f"{k} {v}" for k, v in Counter(t.exit_reason for t in trades).most_common()))
        lines.append("Direction: " + ", ".join(f"{k} {v}" for k, v in Counter(t.direction for t in trades).most_common()))
    return lines


def report(res: BtResult, start: date, split: date, end: date, capital: float, show_holdout: bool,
           title: str) -> str:
    ins = [t for t in res.trades if t.day < split]
    out = [title, f"In-sample {start} → {split}:", *[f"  {l}" for l in summarize(ins, start, split, capital)]]
    if show_holdout:
        hold = [t for t in res.trades if t.day >= split]
        out += [f"HOLD-OUT {split} → {end}:", *[f"  {l}" for l in summarize(hold, split, end, capital)]]
    else:
        out.append(f"Hold-out {split} → {end}: hidden (run once with --holdout when parameters are final)")
    out.append("Days: " + ", ".join(f"{k} {v}" for k, v in sorted(res.day_outcomes.items())))
    if res.halted_on:
        out.append(f"Survival halt triggered on {res.halted_on}")
    return "\n".join(out)
