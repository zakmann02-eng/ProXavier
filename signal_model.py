"""Signal dataclass and SignalBus for multi-strategy coordination."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Signal:
    symbol: str
    asset_class: str          # "equity" | "etf" | "crypto"
    trade_type: str           # "day" | "swing"
    strategy_id: str
    direction: str            # "LONG" | "SHORT"
    score: int                # 0–100
    entry_price: float
    tp1_price: float
    tp2_price: float | None
    sl_price: float
    qty: float                # fractional for crypto, integer for equities
    risk_usd: float
    rr_ratio: float
    trailing_atr_mult: float  # 0 = no trailing stop after TP1
    timeframe: str
    triggers: list[str] = field(default_factory=list)
    raw_indicators: dict = field(default_factory=dict)


class SignalBus:
    """
    Collects signals from all strategies and deduplicates by (symbol, direction).
    When the same symbol/direction fires from multiple strategies, keeps highest score.
    """

    def __init__(self) -> None:
        self._signals: dict[tuple[str, str], Signal] = {}

    def submit(self, signal: Signal) -> None:
        key = (signal.symbol, signal.direction)
        existing = self._signals.get(key)
        if existing is None or signal.score > existing.score:
            self._signals[key] = signal

    def flush(self) -> list[Signal]:
        """Return all pending signals sorted by score descending, then clear."""
        signals = sorted(self._signals.values(), key=lambda s: s.score, reverse=True)
        self._signals.clear()
        return signals

    def clear(self) -> None:
        self._signals.clear()

    def __len__(self) -> int:
        return len(self._signals)
