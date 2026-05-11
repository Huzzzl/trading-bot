"""
backtest/trade.py
-----------------
Immutable record of a completed round-trip trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Trade:
    """A completed (closed) round-trip trade.

    Attributes
    ----------
    symbol : str
    entry_time : pd.Timestamp
    exit_time : pd.Timestamp
    entry_price : float
        Price paid (after slippage, before commission).
    exit_price : float
        Price received (after slippage, before commission).
    shares : float
        Number of shares traded.
    commission : float
        Total commission charged (entry + exit).
    direction : str
        ``"LONG"`` or ``"SHORT"``.
    exit_reason : str
        Human-readable reason, e.g. ``"stop_loss"``, ``"force_exit"``,
        ``"end_of_day"``.
    pnl : float
        Net profit/loss after commission.
    meta : dict
        Arbitrary strategy metadata for research.
    """

    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: float
    commission: float
    direction: str
    exit_reason: str
    pnl: float = field(init=False)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Compute net PnL after commission."""
        if self.direction == "LONG":
            gross = (self.exit_price - self.entry_price) * self.shares
        else:
            gross = (self.entry_price - self.exit_price) * self.shares
        self.pnl = gross - self.commission

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary (useful for DataFrame construction)."""
        return {
            "symbol":       self.symbol,
            "entry_time":   self.entry_time,
            "exit_time":    self.exit_time,
            "entry_price":  self.entry_price,
            "exit_price":   self.exit_price,
            "shares":       self.shares,
            "commission":   self.commission,
            "direction":    self.direction,
            "exit_reason":  self.exit_reason,
            "pnl":          self.pnl,
        }
