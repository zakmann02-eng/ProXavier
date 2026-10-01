"""Plain-text message formats for every alert type."""
from __future__ import annotations

from datetime import date

from src.analysis.zones import Zone
from src.journal.stats import Stats
from src.risk.exits import Event
from src.strategy.checklist import CheckResult
from src.strategy.contracts import TradePlan
from src.strategy.session import DayPlan

def tag(mode: str) -> str:
    return f"[SPY bot · {mode.upper()}]"


def _zones(zs: list[Zone], n: int = 4) -> str:
    return "\n".join(f"  • {z}" for z in zs[:n]) or "  • none in range"


def premarket_brief(plan: DayPlan, mode: str, warnings: list[str]) -> str:
    lines = [
        f"{tag(mode)} Pre-market brief {plan.day:%a %Y-%m-%d}",
        f"SPY last {plan.last_price:.2f} | bias {plan.bias.upper()} — {plan.bias_reason}",
        f"Prior day H {plan.prior_day['high']:.2f} L {plan.prior_day['low']:.2f} C {plan.prior_day['close']:.2f}",
    ]
    if plan.overnight:
        lines.append(f"Overnight H {plan.overnight['high']:.2f} L {plan.overnight['low']:.2f}")
    lines += ["Resistance:", _zones(plan.resistance()), "Support:", _zones(plan.support())]
    if plan.bias == "bullish":
        watch = "bullish setups (Kangaroo Tail / Big Shadow / Wammie / Last Kiss) at support"
    elif plan.bias == "bearish":
        watch = "bearish setups (Kangaroo Tail / Big Shadow / Moolah / Last Kiss) at resistance"
    else:
        watch = "only setups at the strongest zones (neutral bias)"
    lines.append(f"Watching for: {watch}, 9:30–9:50 closes.")
    lines += [f"⚠ {w}" for w in warnings + plan.notes]
    return "\n".join(lines)


def enter(mode: str, chk: CheckResult, plan: TradePlan, tp_pct: float, hard_stop_pct: float,
          time_stop: str) -> str:
    s = chk.setup
    tp_price = plan.limit * (1 + tp_pct / 100)
    stop_price = plan.limit * (1 - hard_stop_pct / 100)
    legs = "\n".join(f"  {l.side.upper()} {plan.qty}× {l.quote.symbol} ({l.quote.strike:g}{l.quote.right}) "
                     f"bid {l.quote.bid:.2f} / ask {l.quote.ask:.2f}" for l in plan.legs)
    kind = "debit spread" if plan.is_spread else "long option"
    target = f"{chk.target:.2f}" if chk.target is not None else "n/a"
    return "\n".join([
        f"{tag(mode)} 🟢 ENTER {s.direction.upper()} — {kind}",
        legs,
        f"Expiration {plan.expiry:%Y-%m-%d} | qty {plan.qty}",
        f"MAX LIMIT {plan.limit:.2f} (mid {plan.mid:.2f}) — do NOT chase above it",
        f"Est. cost ${plan.cost:.2f} incl. fees",
        f"Setup: {s.name} at {s.zone} | candle {s.bar_ts:%H:%M} close {s.entry:.2f}",
        f"R:R {chk.reward_risk:.1f} | SPY target zone {target}",
        f"Take profit: +{tp_pct:g}% (≈{tp_price:.2f}) or SPY at {target}",
        f"Hard stop: −{hard_stop_pct:g}% (≈{stop_price:.2f}) | structural: SPY close beyond {s.stop:.2f}",
        f"Time stop: {time_stop} ET",
    ])


def breakeven(mode: str, ev: Event) -> str:
    return f"{tag(mode)} 🔒 MOVE STOP TO BREAKEVEN — {ev.detail}"


def exit_msg(mode: str, ev: Event, exit_price: float, pnl: float, pnl_pct: float) -> str:
    emoji = "✅" if pnl > 0 else "🔴"
    return "\n".join([
        f"{tag(mode)} {emoji} EXIT — {ev.reason.upper()}",
        f"{ev.detail}",
        f"Exit at ~{exit_price:.2f} | result ${pnl:+.2f} ({pnl_pct:+.1f}%)",
    ])


def no_trade(mode: str, day: date, reason: str) -> str:
    return f"{tag(mode)} No trade today ({day:%a %Y-%m-%d}) — {reason}"


def skip_day(mode: str, day: date, reason: str) -> str:
    return f"{tag(mode)} Skipping {day:%a %Y-%m-%d} — {reason}"


def error(mode: str, what: str, detail: str) -> str:
    return f"{tag(mode)} ⚠ ERROR — {what}\n{detail}\nNo new trade will be taken."


def daily_summary(mode: str, day: date, outcome: str, equity: float, stats: Stats | None) -> str:
    lines = [f"{tag(mode)} Daily summary {day:%a %Y-%m-%d}", outcome, f"Paper equity ${equity:.2f}"]
    if stats and stats.trades:
        lines.append(f"All-time: {stats.trades} trades, win {stats.win_rate:.0f}%, "
                     f"expectancy ${stats.expectancy:.2f}/trade")
    return "\n".join(lines)


def weekly_report(mode: str, week_of: date, week: Stats, all_time: Stats, day_lines: list[str]) -> str:
    return "\n".join([f"{tag(mode)} 📊 Weekly report — week of {week_of:%Y-%m-%d}", *day_lines, "This week:",
                      *[f"  {l}" for l in week.lines()], "All-time:", *[f"  {l}" for l in all_time.lines()]])
