"""Entry checklist: ALL items must pass before a trade is built."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.analysis.setups import LONG, Setup
from src.analysis.trend import BEARISH, BULLISH, NEUTRAL
from src.analysis.zones import Zone, zones_above, zones_below
from src.config import ChecklistCfg, TrendCfg


@dataclass
class CheckResult:
    setup: Setup
    passed: bool
    target: float | None = None        # SPY level of the next opposing zone (take-profit-at-zone)
    target_zone: Zone | None = None
    reward_risk: float | None = None
    items: list[tuple[str, bool, str]] = field(default_factory=list)

    def failures(self) -> list[str]:
        return [f"{name}: {why}" for name, ok, why in self.items if not ok]


def _target(setup: Setup, zones: list[Zone], cfg: ChecklistCfg) -> tuple[float | None, Zone | None]:
    """Near edge of the next opposing zone beyond the setup's own zone."""
    if setup.direction == LONG:
        opp = [z for z in zones_above(zones, setup.entry) if z.low > setup.zone.high]
        if opp:
            return opp[0].low, opp[0]
    else:
        opp = [z for z in zones_below(zones, setup.entry) if z.high < setup.zone.low]
        if opp:
            return opp[0].high, opp[0]
    if cfg.no_target_policy == "measured":
        sign = 1 if setup.direction == LONG else -1
        return setup.entry + sign * cfg.min_reward_risk * setup.risk, None
    return None, None


def evaluate(setup: Setup, zones: list[Zone], bias: str, trades_today: int,
             cfg: ChecklistCfg, trend_cfg: TrendCfg) -> CheckResult:
    """Checklist items 1–4 and 6. Item 5 (cost fits budget) is checked after contract selection."""
    res = CheckResult(setup, passed=False)
    items = res.items

    prox = setup.entry * cfg.zone_proximity_pct / 100
    d = setup.zone.distance(setup.entry)
    items.append(("1 at zone", d <= prox, f"close {setup.entry:.2f} is {d:.2f} from {setup.zone}"))

    items.append(("2 setup", True, f"{setup.name} {setup.direction}"))

    want = BULLISH if setup.direction == LONG else BEARISH
    if bias == want:
        items.append(("3 bias", True, f"{bias} agrees"))
    elif bias == NEUTRAL and trend_cfg.neutral_policy == "strongest_only":
        ok = setup.zone.strength >= trend_cfg.neutral_min_strength
        items.append(("3 bias", ok, f"neutral bias; zone strength {setup.zone.strength} "
                                    f"(need ≥{trend_cfg.neutral_min_strength})"))
    else:
        items.append(("3 bias", False, f"bias {bias} vs {setup.direction}"))

    target, tz = _target(setup, zones, cfg)
    if target is None:
        items.append(("4 reward:risk", False, "no opposing zone for a target"))
    else:
        reward = abs(target - setup.entry)
        rr = reward / setup.risk if setup.risk > 0 else 0.0
        res.target, res.target_zone, res.reward_risk = target, tz, rr
        items.append(("4 reward:risk", rr >= cfg.min_reward_risk,
                      f"R:R {rr:.2f} (reward {reward:.2f}, risk {setup.risk:.2f})"))

    items.append(("6 one trade/day", trades_today < cfg.max_trades_per_day, f"{trades_today} taken today"))
    res.passed = all(ok for _, ok, _ in items)
    return res


def pick_best(results: list[CheckResult]) -> CheckResult | None:
    """Among passing setups on the same candle, prefer the strongest zone, then the best R:R."""
    ok = [r for r in results if r.passed]
    if not ok:
        return None
    return max(ok, key=lambda r: (r.setup.zone.strength, r.reward_risk or 0))
