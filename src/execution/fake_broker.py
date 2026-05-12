"""
execution/fake_broker.py
------------------------
In-memory fake broker adapter for testing and paper-trading dry-runs.

FakeBrokerAdapter stores submitted orders locally, generates deterministic
incremental order IDs, and never touches a network or reads API keys.

Behaviour
---------
* Market orders are marked ``"filled"`` immediately when ``fill_immediately=True``
  (the default).  The fill price is taken from ``intent.limit_price`` if set,
  otherwise from ``intent.metadata.get("entry_price")``; if neither is
  available the fill price is left as ``None`` and status is ``"accepted"``.
* All other order types are always marked ``"accepted"`` (pending).
* ``cancel_order`` removes a non-terminal order and returns ``True``;
  returns ``False`` for unknown or already-filled/cancelled orders.
* ``get_positions`` derives open positions from the net quantity of filled
  buy/sell orders per symbol.
* ``get_account`` returns a static snapshot with a configurable initial
  cash balance.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.execution.broker import BrokerAdapter, OrderResult
from src.execution.order_intent import OrderIntent
from src.utils.logger import get_logger

logger = get_logger(__name__)

_TERMINAL_STATUSES = {"filled", "cancelled", "rejected"}


class FakeBrokerAdapter(BrokerAdapter):
    """Offline, in-memory broker adapter.

    Parameters
    ----------
    fill_immediately:
        When ``True`` (default), market orders are immediately marked
        ``"filled"``; otherwise all orders are ``"accepted"`` until
        explicitly cancelled.
    initial_cash:
        Starting cash balance reported by :meth:`get_account`.
    """

    def __init__(
        self,
        fill_immediately: bool = True,
        initial_cash: float = 100_000.0,
    ) -> None:
        self._fill_immediately = fill_immediately
        self._initial_cash     = initial_cash
        self._next_id: int     = 1
        # order_id → (intent, result)
        self._orders: dict[str, tuple[OrderIntent, OrderResult]] = {}

    # ------------------------------------------------------------------
    # BrokerAdapter interface
    # ------------------------------------------------------------------

    def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Accept *intent*, assign an order ID, and return an :class:`OrderResult`.

        Market orders with a resolvable fill price are immediately filled
        when ``fill_immediately=True``; all others are left as ``"accepted"``.
        """
        order_id = self._next_order_id()
        now      = intent.timestamp

        # Resolve fill price for market orders
        fill_price: float | None = None
        if intent.order_type == "market":
            fill_price = (
                intent.limit_price
                or intent.metadata.get("entry_price")
                or intent.metadata.get("price")
            )

        if self._fill_immediately and intent.order_type == "market" and fill_price is not None:
            status    = "filled"
            filled_at = now
        else:
            status    = "accepted"
            filled_at = None
            fill_price = None  # not filled yet

        result = OrderResult(
            order_id     = order_id,
            symbol       = intent.symbol,
            side         = intent.side,
            quantity     = intent.quantity,
            status       = status,
            submitted_at = now,
            reason       = intent.reason,
            filled_at    = filled_at,
            filled_price = fill_price,
            metadata     = dict(intent.metadata),
        )

        self._orders[order_id] = (intent, result)
        logger.debug(
            "FakeBroker submit %s %s %.0f %s → %s  id=%s",
            intent.side, intent.symbol, intent.quantity,
            intent.order_type, status, order_id,
        )
        return result

    def get_positions(self) -> dict[str, Any]:
        """Return net open positions derived from filled orders.

        Returns
        -------
        dict
            ``{symbol: {"quantity": float, "avg_price": float}}`` for symbols
            with a non-zero net quantity.  Only ``"filled"`` orders are counted.
        """
        net_qty:   dict[str, float] = {}
        cost_basis: dict[str, float] = {}

        for _, (intent, result) in self._orders.items():
            if result.status != "filled" or result.filled_price is None:
                continue
            qty   = result.quantity if intent.side == "buy" else -result.quantity
            value = abs(qty) * result.filled_price
            net_qty[intent.symbol]    = net_qty.get(intent.symbol, 0.0)    + qty
            cost_basis[intent.symbol] = cost_basis.get(intent.symbol, 0.0) + value

        positions: dict[str, Any] = {}
        for sym, qty in net_qty.items():
            if abs(qty) > 1e-9:
                filled_qty = abs(qty)
                positions[sym] = {
                    "quantity":  round(qty, 6),
                    "avg_price": round(cost_basis[sym] / filled_qty, 6) if filled_qty else 0.0,
                }
        return positions

    def get_account(self) -> dict[str, Any]:
        """Return a static account snapshot.

        Returns
        -------
        dict
            Contains ``cash``, ``currency``, ``status``, and ``broker`` keys.
            Cash is ``initial_cash`` minus the cost of all filled buy orders
            plus proceeds of all filled sell orders (no commission modelling).
        """
        cash = self._initial_cash
        for _, (intent, result) in self._orders.items():
            if result.status != "filled" or result.filled_price is None:
                continue
            cost = result.quantity * result.filled_price
            cash += -cost if intent.side == "buy" else cost

        return {
            "cash":     round(cash, 2),
            "currency": "USD",
            "status":   "active",
            "broker":   "fake",
        }

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Returns ``True`` if the order was found and was not in a terminal
        state; ``False`` otherwise.
        """
        if order_id not in self._orders:
            logger.debug("FakeBroker cancel: unknown order_id=%s", order_id)
            return False

        intent, result = self._orders[order_id]
        if result.status in _TERMINAL_STATUSES:
            logger.debug(
                "FakeBroker cancel: order_id=%s already in terminal status %s",
                order_id, result.status,
            )
            return False

        cancelled = OrderResult(
            order_id     = result.order_id,
            symbol       = result.symbol,
            side         = result.side,
            quantity     = result.quantity,
            status       = "cancelled",
            submitted_at = result.submitted_at,
            reason       = result.reason,
            filled_at    = None,
            filled_price = None,
            metadata     = dict(result.metadata),
        )
        self._orders[order_id] = (intent, cancelled)
        logger.debug("FakeBroker cancel: order_id=%s cancelled", order_id)
        return True

    # ------------------------------------------------------------------
    # Inspection helpers (not part of the abstract interface)
    # ------------------------------------------------------------------

    @property
    def order_count(self) -> int:
        """Total number of orders submitted (all statuses)."""
        return len(self._orders)

    def get_order(self, order_id: str) -> tuple[OrderIntent, OrderResult] | None:
        """Return the (intent, result) pair for *order_id*, or ``None``."""
        return self._orders.get(order_id)

    def all_orders(self) -> list[tuple[OrderIntent, OrderResult]]:
        """Return all (intent, result) pairs in submission order."""
        return list(self._orders.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_order_id(self) -> str:
        oid = f"FAKE-{self._next_id:06d}"
        self._next_id += 1
        return oid
