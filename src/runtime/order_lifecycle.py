"""
runtime/order_lifecycle.py
---------------------------
Automated runtime order lifecycle manager skeleton (PR A2-3).

Tracks order lifecycle state transitions deterministically, in memory only.
Does not submit, cancel, replace, or close orders.
No broker, credential, env, or network access in this module.

Default behavior:
  Orders start in CREATED state after create_order().
  Transitions are validated against a fixed table; invalid transitions
  return an OrderLifecycleTransition with allowed=False without mutating state.
  Terminal states (FILLED, CANCELED, REJECTED, FAILED, CLOSED) block further
  forward-submit events.

Safety flags (all always False in A2-3):
  broker_calls_made=False, credentials_read=False, network_calls_made=False,
  order_action_requested=False, live_trading_allowed=False.

This module does not import from src.tools live manual gates, broker adapters,
or environment variables. No file I/O. No network I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class OrderLifecycleState(str, Enum):
    CREATED          = "CREATED"
    RISK_APPROVED    = "RISK_APPROVED"
    SUBMIT_REQUESTED = "SUBMIT_REQUESTED"
    SUBMITTED        = "SUBMITTED"
    ACKNOWLEDGED     = "ACKNOWLEDGED"
    FILLED           = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED         = "CANCELED"
    REJECTED         = "REJECTED"
    FAILED           = "FAILED"
    CLOSED           = "CLOSED"


class OrderLifecycleEvent(str, Enum):
    CREATE               = "CREATE"
    RISK_APPROVE         = "RISK_APPROVE"
    REQUEST_SUBMIT       = "REQUEST_SUBMIT"
    MARK_SUBMITTED       = "MARK_SUBMITTED"
    ACKNOWLEDGE          = "ACKNOWLEDGE"
    MARK_FILLED          = "MARK_FILLED"
    MARK_PARTIALLY_FILLED = "MARK_PARTIALLY_FILLED"
    MARK_CANCELED        = "MARK_CANCELED"
    MARK_REJECTED        = "MARK_REJECTED"
    MARK_FAILED          = "MARK_FAILED"
    CLOSE                = "CLOSE"


@dataclass(frozen=True)
class OrderLifecycleRecord:
    """Immutable snapshot of an order's lifecycle state."""
    client_order_id: str
    symbol: str | None
    side: str | None
    quantity: float | None
    state: OrderLifecycleState
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    blocker: str | None = None


@dataclass(frozen=True)
class OrderLifecycleTransition:
    """Result of an apply_event() call."""
    previous_state: OrderLifecycleState | None
    next_state: OrderLifecycleState
    event: OrderLifecycleEvent
    allowed: bool
    blocker: str | None
    # Safety flags — always False in A2-3
    broker_calls_made: bool = False
    credentials_read: bool = False
    network_calls_made: bool = False
    order_action_requested: bool = False
    live_trading_allowed: bool = False


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

_S = OrderLifecycleState
_E = OrderLifecycleEvent

# Maps (current_state, event) -> next_state.
# None as current_state is used only for CREATE (order does not exist yet).
_TRANSITIONS: dict[tuple[OrderLifecycleState | None, OrderLifecycleEvent], OrderLifecycleState] = {
    # Creation
    (None,              _E.CREATE):               _S.CREATED,
    # Risk approval
    (_S.CREATED,        _E.RISK_APPROVE):         _S.RISK_APPROVED,
    # Submit pipeline
    (_S.RISK_APPROVED,  _E.REQUEST_SUBMIT):       _S.SUBMIT_REQUESTED,
    (_S.SUBMIT_REQUESTED, _E.MARK_SUBMITTED):     _S.SUBMITTED,
    (_S.SUBMITTED,      _E.ACKNOWLEDGE):          _S.ACKNOWLEDGED,
    # Fill paths
    (_S.ACKNOWLEDGED,   _E.MARK_FILLED):          _S.FILLED,
    (_S.ACKNOWLEDGED,   _E.MARK_PARTIALLY_FILLED): _S.PARTIALLY_FILLED,
    (_S.PARTIALLY_FILLED, _E.MARK_FILLED):        _S.FILLED,
    # Cancel paths (submitted, acknowledged, or partially filled)
    (_S.SUBMITTED,      _E.MARK_CANCELED):        _S.CANCELED,
    (_S.ACKNOWLEDGED,   _E.MARK_CANCELED):        _S.CANCELED,
    (_S.PARTIALLY_FILLED, _E.MARK_CANCELED):      _S.CANCELED,
    # Close terminal states
    (_S.FILLED,         _E.CLOSE):                _S.CLOSED,
    (_S.CANCELED,       _E.CLOSE):                _S.CLOSED,
    (_S.REJECTED,       _E.CLOSE):                _S.CLOSED,
    (_S.FAILED,         _E.CLOSE):                _S.CLOSED,
}

# MARK_REJECTED and MARK_FAILED are allowed from any non-terminal state.
_NON_TERMINAL_STATES: frozenset[OrderLifecycleState] = frozenset({
    _S.CREATED,
    _S.RISK_APPROVED,
    _S.SUBMIT_REQUESTED,
    _S.SUBMITTED,
    _S.ACKNOWLEDGED,
    _S.PARTIALLY_FILLED,
})

_TERMINAL_STATES: frozenset[OrderLifecycleState] = frozenset({
    _S.FILLED,
    _S.CANCELED,
    _S.REJECTED,
    _S.FAILED,
    _S.CLOSED,
})


def _next_state(
    current: OrderLifecycleState | None,
    event: OrderLifecycleEvent,
) -> OrderLifecycleState | None:
    """Return the next state for (current, event), or None if not allowed."""
    # MARK_REJECTED and MARK_FAILED are allowed from any non-terminal state
    if event == _E.MARK_REJECTED and current in _NON_TERMINAL_STATES:
        return _S.REJECTED
    if event == _E.MARK_FAILED and current in _NON_TERMINAL_STATES:
        return _S.FAILED
    return _TRANSITIONS.get((current, event))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class OrderLifecycleManager:
    """In-memory order lifecycle manager.

    Tracks multiple orders by client_order_id.
    Records are immutable snapshots; each transition produces a new record.
    No broker, credential, env, or network access.
    """

    def __init__(self) -> None:
        self._orders: dict[str, OrderLifecycleRecord] = {}

    def create_order(
        self,
        client_order_id: str,
        *,
        symbol: str | None = None,
        side: str | None = None,
        quantity: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrderLifecycleTransition:
        """Create a new order record in CREATED state.

        Returns an OrderLifecycleTransition with allowed=False if the
        client_order_id already exists.
        """
        if client_order_id in self._orders:
            existing = self._orders[client_order_id]
            return OrderLifecycleTransition(
                previous_state=existing.state,
                next_state=existing.state,
                event=_E.CREATE,
                allowed=False,
                blocker=f"client_order_id {client_order_id!r} already exists",
            )

        event_entry: dict[str, Any] = {"event": _E.CREATE.value}
        if metadata:
            event_entry.update(metadata)

        record = OrderLifecycleRecord(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            state=_S.CREATED,
            events=(event_entry,),
            blocker=None,
        )
        self._orders[client_order_id] = record
        logger.info("OrderLifecycle created: id=%s symbol=%s side=%s qty=%s",
                    client_order_id, symbol, side, quantity)
        return OrderLifecycleTransition(
            previous_state=None,
            next_state=_S.CREATED,
            event=_E.CREATE,
            allowed=True,
            blocker=None,
        )

    def apply_event(
        self,
        client_order_id: str,
        event: OrderLifecycleEvent,
        metadata: dict[str, Any] | None = None,
    ) -> OrderLifecycleTransition:
        """Apply an event to an existing order.

        Returns OrderLifecycleTransition with allowed=False (and does not
        mutate the record) if the transition is invalid or the order is unknown.
        """
        if client_order_id not in self._orders:
            return OrderLifecycleTransition(
                previous_state=None,
                next_state=_S.FAILED,
                event=event,
                allowed=False,
                blocker=f"unknown client_order_id {client_order_id!r}",
            )

        record = self._orders[client_order_id]
        current = record.state

        next_st = _next_state(current, event)
        if next_st is None:
            return OrderLifecycleTransition(
                previous_state=current,
                next_state=current,
                event=event,
                allowed=False,
                blocker=(
                    f"transition {current.value} + {event.value} is not allowed"
                ),
            )

        event_entry: dict[str, Any] = {"event": event.value}
        if metadata:
            event_entry.update(metadata)

        new_record = OrderLifecycleRecord(
            client_order_id=record.client_order_id,
            symbol=record.symbol,
            side=record.side,
            quantity=record.quantity,
            state=next_st,
            events=record.events + (event_entry,),
            blocker=None,
        )
        self._orders[client_order_id] = new_record
        logger.info("OrderLifecycle transition: id=%s  %s + %s -> %s",
                    client_order_id, current.value, event.value, next_st.value)
        return OrderLifecycleTransition(
            previous_state=current,
            next_state=next_st,
            event=event,
            allowed=True,
            blocker=None,
        )

    def get(self, client_order_id: str) -> OrderLifecycleRecord | None:
        """Return the current immutable record, or None if not found."""
        return self._orders.get(client_order_id)

    def all_orders(self) -> tuple[OrderLifecycleRecord, ...]:
        """Return all current records as an immutable tuple."""
        return tuple(self._orders.values())
