"""
execution/order_intent.py
-------------------------
Broker-agnostic description of a desired order.

An OrderIntent captures *what* the strategy wants to do — symbol, side,
quantity, pricing constraints, and the reason — without any knowledge of
*how* the order will be routed.  The backtest engine records these for
auditing; a future paper/live adapter will translate them into real orders.

Design notes
------------
* Deliberately immutable after construction (frozen dataclass).
* ``limit_price`` and ``stop_price`` are both optional; omitting both
  implies a market order.
* ``metadata`` carries arbitrary key-value context (e.g. signal provenance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class OrderIntent:
    """Broker-agnostic order specification.

    Parameters
    ----------
    symbol:
        Ticker (e.g. ``"SPY"``).
    side:
        ``"buy"`` or ``"sell"``.
    quantity:
        Number of shares (whole or fractional, positive).
    order_type:
        ``"market"``, ``"limit"``, or ``"stop"``.
    reason:
        Human-readable label for why this order was generated
        (e.g. ``"breakout_entry"``, ``"stop_loss"``, ``"force_exit"``).
    timestamp:
        Bar timestamp at which the intent was created.
    limit_price:
        Required when ``order_type == "limit"``.
    stop_price:
        Required when ``order_type == "stop"``.
    metadata:
        Arbitrary key-value pairs — stored as a plain dict but kept
        immutable by convention (the frozen dataclass only prevents
        attribute reassignment, not dict mutation).
    """

    symbol:      str
    side:        str
    quantity:    float
    order_type:  str
    reason:      str
    timestamp:   pd.Timestamp
    limit_price: float | None         = None
    stop_price:  float | None         = None
    metadata:    dict[str, Any]       = field(default_factory=dict, compare=False)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.order_type not in ("market", "limit", "stop"):
            raise ValueError(
                f"order_type must be 'market', 'limit', or 'stop', got {self.order_type!r}"
            )
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this intent."""
        return {
            "symbol":      self.symbol,
            "side":        self.side,
            "quantity":    self.quantity,
            "order_type":  self.order_type,
            "reason":      self.reason,
            "timestamp":   str(self.timestamp),
            "limit_price": self.limit_price,
            "stop_price":  self.stop_price,
            "metadata":    dict(self.metadata),
        }
