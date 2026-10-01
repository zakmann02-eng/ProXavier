"""Trade construction: debit vertical spread (primary) or single long option (fallback)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from src.analysis.setups import LONG
from src.config import TradeCfg


@dataclass
class OptionQuote:
    symbol: str          # OCC symbol, e.g. SPY241018C00580000
    expiry: date
    strike: float
    right: str           # "C" | "P"
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class Leg:
    quote: OptionQuote
    side: str            # "buy" | "sell"


@dataclass
class TradePlan:
    legs: list[Leg]
    expiry: date
    qty: int
    mid: float                 # per-share net debit at mid
    limit: float               # max per-share debit to pay — never chase above
    natural_width: float       # per-share bid/ask width of the whole position
    fees: float                # total round-trip fees in $
    cost: float                # $ paid at the limit incl. opening fees
    max_value: float | None    # per-share max value (spread width) or None for single legs
    ok: bool = True
    reject_reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_spread(self) -> bool:
        return len(self.legs) == 2

    def describe(self) -> str:
        parts = [f"{'BUY' if l.side == 'buy' else 'SELL'} {l.quote.strike:g}{l.quote.right}" for l in self.legs]
        return " / ".join(parts) + f" exp {self.expiry:%Y-%m-%d}"


def position_mark(legs: list[Leg], quotes: dict[str, OptionQuote]) -> tuple[float, float]:
    """(mid, natural width) per share of the position from current leg quotes."""
    mid = width = 0.0
    for leg in legs:
        q = quotes[leg.quote.symbol]
        sign = 1 if leg.side == "buy" else -1
        mid += sign * q.mid
        width += q.ask - q.bid
    return mid, width


def pick_expiry(expiries: list[date], today: date, dte: int, trading_days: list[date]) -> date | None:
    """dte counts trading days: 0 = today, 1 = next trading day."""
    future = [d for d in trading_days if d >= today]
    if dte >= len(future):
        return None
    want = future[dte]
    return want if want in expiries else None


def build_trade(chain: list[OptionQuote], direction: str, spot: float, budget: float,
                cfg: TradeCfg) -> TradePlan:
    right = "C" if direction == LONG else "P"
    opts = sorted([q for q in chain if q.right == right], key=lambda q: q.strike)
    if not opts:
        return _reject("no option quotes for that expiry")
    expiry = opts[0].expiry
    long_q = min(opts, key=lambda q: (abs(q.strike - spot), q.strike))
    legs = [Leg(long_q, "buy")]
    max_value = None
    if cfg.allow_spreads:
        short_strike = long_q.strike + cfg.spread_width if right == "C" else long_q.strike - cfg.spread_width
        short_q = next((q for q in opts if math.isclose(q.strike, short_strike)), None)
        if short_q is None:
            return _reject(f"no {short_strike:g}{right} strike for the short leg")
        legs.append(Leg(short_q, "sell"))
        max_value = cfg.spread_width

    for leg in legs:
        if leg.quote.ask <= 0 or (leg.side == "sell" and leg.quote.bid <= 0):
            return _reject(f"no valid market on {leg.quote.symbol}")
    mid, width = position_mark(legs, {l.quote.symbol: l.quote for l in legs})
    if mid < cfg.min_debit:
        return _reject(f"debit {mid:.2f} below minimum {cfg.min_debit:.2f}")
    ratio = width / mid
    if ratio > cfg.max_bid_ask_ratio:
        return _reject(f"illiquid: bid/ask width {width:.2f} is {ratio:.0%} of mid {mid:.2f}")
    limit = round(mid + cfg.limit_buffer_fraction * width, 2)
    if max_value is not None:
        limit = min(limit, max_value)
    open_fees = cfg.fee_per_contract_leg * len(legs)
    per_contract = limit * 100 + open_fees
    qty = min(cfg.max_contracts, int(budget // per_contract)) if per_contract > 0 else 0
    plan = TradePlan(legs, expiry, qty, round(mid, 4), limit, width,
                     fees=2 * open_fees * max(qty, 1), cost=per_contract * max(qty, 1), max_value=max_value)
    if qty < 1:
        plan.ok = False
        plan.reject_reason = f"cost ${per_contract:.2f} exceeds budget ${budget:.2f}"
    return plan


def _reject(reason: str) -> TradePlan:
    return TradePlan([], date.min, 0, 0.0, 0.0, 0.0, 0.0, 0.0, None, ok=False, reject_reason=reason)
