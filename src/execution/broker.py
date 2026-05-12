"""
execution/broker.py
--------------------
Abstract broker adapter interface and OrderResult dataclass.

BrokerAdapter defines the contract that every execution backend must satisfy.
Concrete implementations (FakeBrokerAdapter, future AlpacaAdapter, etc.) subclass
it and fill in the four abstract methods.

OrderResult captures the broker's response to a submitted OrderIntent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.execution.order_intent import OrderIntent


# ---------------------------------------------------------------------------
# OrderResult
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    """Broker response to a submitted :class:`~src.execution.order_intent.OrderIntent`.

    Parameters
    ----------
    order_id:
        Unique identifier assigned by the broker (or generated locally for
        the fake adapter).
    symbol:
        Ticker.
    side:
        ``"buy"`` or ``"sell"``.
    quantity:
        Number of shares requested.
    status:
        Current order status.  Common values: ``"accepted"``, ``"filled"``,
        ``"cancelled"``, ``"rejected"``.
    submitted_at:
        Timestamp when the order was submitted.
    reason:
        Echoed from the originating :class:`OrderIntent`.
    filled_at:
        Timestamp of fill; ``None`` if not yet filled.
    filled_price:
        Average fill price; ``None`` if not yet filled.
    metadata:
        Arbitrary key-value pairs forwarded from the intent or added by the
        broker adapter.
    """

    order_id:     str
    symbol:       str
    side:         str
    quantity:     float
    status:       str
    submitted_at: pd.Timestamp
    reason:       str
    filled_at:    pd.Timestamp | None  = None
    filled_price: float | None         = None
    metadata:     dict[str, Any]       = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this result."""
        return {
            "order_id":     self.order_id,
            "symbol":       self.symbol,
            "side":         self.side,
            "quantity":     self.quantity,
            "status":       self.status,
            "submitted_at": str(self.submitted_at),
            "reason":       self.reason,
            "filled_at":    str(self.filled_at) if self.filled_at is not None else None,
            "filled_price": self.filled_price,
            "metadata":     dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# BrokerAdapter (abstract interface)
# ---------------------------------------------------------------------------

class BrokerAdapter(ABC):
    """Abstract base class for all broker backends.

    Every execution target — fake, paper, live — must implement these four
    methods.  The engine and risk manager interact only with this interface,
    so swapping backends requires no changes upstream.
    """

    @abstractmethod
    def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Submit *intent* to the broker and return the broker's response.

        Parameters
        ----------
        intent:
            The order to submit.

        Returns
        -------
        OrderResult
            At minimum contains an ``order_id`` and a ``status``.
        """

    @abstractmethod
    def get_positions(self) -> dict[str, Any]:
        """Return the broker's current open positions.

        Returns
        -------
        dict
            Mapping of symbol → position details.  Schema is adapter-specific.
        """

    @abstractmethod
    def get_account(self) -> dict[str, Any]:
        """Return account-level information (equity, buying power, etc.).

        Returns
        -------
        dict
            Adapter-specific account snapshot.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Request cancellation of a pending order.

        Parameters
        ----------
        order_id:
            The identifier returned by :meth:`submit_order`.

        Returns
        -------
        bool
            ``True`` if the order was found and cancellation was accepted;
            ``False`` if the order was unknown or already in a terminal state.
        """
