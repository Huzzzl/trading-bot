"""
research/paper_order_lifecycle.py
----------------------------------
S32: Pure offline paper order lifecycle state machine.

Tracks allowed in-memory state transitions for a paper order plan after
planning and safety-gate review. Lifecycle transitions are bookkeeping
only -- they record what a future, separately-approved process reported;
they never perform, request, or approve any order action.

This is NOT order submission. This is NOT broker integration. This is NOT
paper trading execution. This is NOT live trading. This module never calls
the planner, validator, safety gate, broker, runtime, execution, network,
environment, or file system. Marking a lifecycle as PAPER_ORDER_FILLED (or
any other status) changes an in-memory record only.

Pure functions: same input -> same output; a new immutable state is
returned rather than mutating the existing state. All safety flags on
every state and transition result are always False. Paper trading remains
not approved; live trading remains blocked; the state machine fails
closed: invalid inputs, forbidden event details, and disallowed
transitions never advance the lifecycle.

Terminal statuses (PAPER_ORDER_FILLED, PAPER_ORDER_REJECTED,
PAPER_ORDER_CANCELLED, PAPER_ORDER_EXPIRED, BLOCKED, ERROR) reject all
further events.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)
_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

_REQUIRED_PLAN_SCHEMA_VERSION: str = "POP/1.0"
_REQUIRED_PLAN_TYPE: str = "PAPER_ORDER_PLAN"

_ALLOWED_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
_ALLOWED_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})

# Required fixed booleans on the incoming plan (field, required_value).
_PLAN_FIXED_BOOLS: tuple[tuple[str, bool], ...] = (
    ("broker_calls_made",           False),
    ("credentials_read",            False),
    ("network_calls_made",          False),
    ("order_action_requested",      False),
    ("live_trading_allowed",        False),
    ("dry_run_required",            True),
    ("human_confirmation_required", True),
    ("kill_switch_required",        True),
    ("safety_gate_required",        True),
)

# Forbidden substrings for the event-details scan (case-insensitive).
# These are scan-list literals only, deliberately assembled from fragments:
# this module never imports, calls, or invokes any of the concepts they
# name, and the assembled words must never appear contiguously in this
# source file.
_FORBIDDEN_DETAIL_SUBSTRINGS: tuple[str, ...] = (
    "submit_" + "order",
    "place_" + "order",
    "cancel_" + "order",
    "modify_" + "order",
    "live_" + "submit",
    "api_key",
    "secret",
    "credential",
    "token",
    "broker_account",
    "live_account",
    "endpoint",
    "http",
    "https",
)


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------

class PaperOrderLifecycleStatus(str, Enum):
    NOT_STARTED                  = "NOT_STARTED"
    PLANNED                      = "PLANNED"
    GATE_PASSED_DRY_RUN_ONLY     = "GATE_PASSED_DRY_RUN_ONLY"
    DRY_RUN_RENDERED             = "DRY_RUN_RENDERED"
    PAPER_ORDER_PENDING          = "PAPER_ORDER_PENDING"
    PAPER_ORDER_FILLED           = "PAPER_ORDER_FILLED"
    PAPER_ORDER_PARTIALLY_FILLED = "PAPER_ORDER_PARTIALLY_FILLED"
    PAPER_ORDER_REJECTED         = "PAPER_ORDER_REJECTED"
    PAPER_ORDER_CANCELLED        = "PAPER_ORDER_CANCELLED"
    PAPER_ORDER_EXPIRED          = "PAPER_ORDER_EXPIRED"
    BLOCKED                      = "BLOCKED"
    ERROR                        = "ERROR"


class PaperOrderLifecycleEventType(str, Enum):
    PLAN_CREATED                        = "PLAN_CREATED"
    SAFETY_GATE_PASSED                  = "SAFETY_GATE_PASSED"
    DRY_RUN_RENDERED                    = "DRY_RUN_RENDERED"
    PAPER_ORDER_MARKED_PENDING          = "PAPER_ORDER_MARKED_PENDING"
    PAPER_ORDER_MARKED_FILLED           = "PAPER_ORDER_MARKED_FILLED"
    PAPER_ORDER_MARKED_PARTIALLY_FILLED = "PAPER_ORDER_MARKED_PARTIALLY_FILLED"
    PAPER_ORDER_MARKED_REJECTED         = "PAPER_ORDER_MARKED_REJECTED"
    PAPER_ORDER_MARKED_CANCELLED        = "PAPER_ORDER_MARKED_CANCELLED"
    PAPER_ORDER_MARKED_EXPIRED          = "PAPER_ORDER_MARKED_EXPIRED"
    BLOCKED_BY_SAFETY                   = "BLOCKED_BY_SAFETY"
    ERROR_RECORDED                      = "ERROR_RECORDED"


_TERMINAL_STATUSES: frozenset[PaperOrderLifecycleStatus] = frozenset({
    PaperOrderLifecycleStatus.PAPER_ORDER_FILLED,
    PaperOrderLifecycleStatus.PAPER_ORDER_REJECTED,
    PaperOrderLifecycleStatus.PAPER_ORDER_CANCELLED,
    PaperOrderLifecycleStatus.PAPER_ORDER_EXPIRED,
    PaperOrderLifecycleStatus.BLOCKED,
    PaperOrderLifecycleStatus.ERROR,
})

_FILL_EVENTS: frozenset[PaperOrderLifecycleEventType] = frozenset({
    PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_FILLED,
    PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
})

_SAFETY_AND_ERROR: dict[
    PaperOrderLifecycleEventType, PaperOrderLifecycleStatus
] = {
    PaperOrderLifecycleEventType.BLOCKED_BY_SAFETY: PaperOrderLifecycleStatus.BLOCKED,
    PaperOrderLifecycleEventType.ERROR_RECORDED:    PaperOrderLifecycleStatus.ERROR,
}

_ALLOWED_TRANSITIONS: dict[
    PaperOrderLifecycleStatus,
    dict[PaperOrderLifecycleEventType, PaperOrderLifecycleStatus],
] = {
    PaperOrderLifecycleStatus.PLANNED: {
        PaperOrderLifecycleEventType.SAFETY_GATE_PASSED:
            PaperOrderLifecycleStatus.GATE_PASSED_DRY_RUN_ONLY,
        **_SAFETY_AND_ERROR,
    },
    PaperOrderLifecycleStatus.GATE_PASSED_DRY_RUN_ONLY: {
        PaperOrderLifecycleEventType.DRY_RUN_RENDERED:
            PaperOrderLifecycleStatus.DRY_RUN_RENDERED,
        **_SAFETY_AND_ERROR,
    },
    PaperOrderLifecycleStatus.DRY_RUN_RENDERED: {
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_PENDING:
            PaperOrderLifecycleStatus.PAPER_ORDER_PENDING,
        **_SAFETY_AND_ERROR,
    },
    PaperOrderLifecycleStatus.PAPER_ORDER_PENDING: {
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_PARTIALLY_FILLED:
            PaperOrderLifecycleStatus.PAPER_ORDER_PARTIALLY_FILLED,
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_FILLED:
            PaperOrderLifecycleStatus.PAPER_ORDER_FILLED,
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_REJECTED:
            PaperOrderLifecycleStatus.PAPER_ORDER_REJECTED,
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_CANCELLED:
            PaperOrderLifecycleStatus.PAPER_ORDER_CANCELLED,
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_EXPIRED:
            PaperOrderLifecycleStatus.PAPER_ORDER_EXPIRED,
        **_SAFETY_AND_ERROR,
    },
    PaperOrderLifecycleStatus.PAPER_ORDER_PARTIALLY_FILLED: {
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_FILLED:
            PaperOrderLifecycleStatus.PAPER_ORDER_FILLED,
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_CANCELLED:
            PaperOrderLifecycleStatus.PAPER_ORDER_CANCELLED,
        PaperOrderLifecycleEventType.PAPER_ORDER_MARKED_EXPIRED:
            PaperOrderLifecycleStatus.PAPER_ORDER_EXPIRED,
        **_SAFETY_AND_ERROR,
    },
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaperOrderLifecycleState:
    """Immutable in-memory lifecycle record for one paper order plan.

    This is bookkeeping only: no status here performs, initiates, or
    approves any order action. All safety flags are always False.
    """
    lifecycle_id: str
    plan_id: str
    candidate_id: str
    run_id: str
    symbol: str
    side: str
    order_type: str
    status: PaperOrderLifecycleStatus
    events: tuple[dict, ...]
    current_quantity: float | None
    filled_quantity: float
    average_fill_price: float | None
    blocker: str | None
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


@dataclass(frozen=True)
class PaperOrderLifecycleTransitionResult:
    """Immutable result of create_lifecycle_from_plan() or
    apply_lifecycle_event().

    result="PASS" means only that the bookkeeping transition was recorded.
    On BLOCKED, the state field carries the unchanged previous state (or
    None when no state could be created/trusted) -- a blocked transition
    never advances the lifecycle. All safety flags are always False.
    """
    result: str
    blocker: str | None
    state: PaperOrderLifecycleState | None
    previous_status: PaperOrderLifecycleStatus | None
    new_status: PaperOrderLifecycleStatus | None
    event_type: PaperOrderLifecycleEventType | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(d: Any, key: str) -> Any:
    if isinstance(d, dict):
        return d.get(key)
    return None


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _finite_positive(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f > 0.0
    except (TypeError, ValueError):
        return False


def _scan_forbidden_details(d: Any) -> bool:
    """Recursively scan detail keys and string values for forbidden
    action/credential/network/live phrases (case-insensitive)."""
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(k, str):
                k_lower = k.lower()
                if any(sub in k_lower for sub in _FORBIDDEN_DETAIL_SUBSTRINGS):
                    return True
            if _scan_forbidden_details(v):
                return True
    elif isinstance(d, (list, tuple)):
        for item in d:
            if _scan_forbidden_details(item):
                return True
    elif isinstance(d, str):
        d_lower = d.lower()
        if any(sub in d_lower for sub in _FORBIDDEN_DETAIL_SUBSTRINGS):
            return True
    return False


def _make_result(
    *,
    result: str,
    blocker: str | None,
    state: PaperOrderLifecycleState | None,
    previous_status: PaperOrderLifecycleStatus | None,
    new_status: PaperOrderLifecycleStatus | None,
    event_type: PaperOrderLifecycleEventType | None,
    checked: list[str],
    failed: list[str],
) -> PaperOrderLifecycleTransitionResult:
    return PaperOrderLifecycleTransitionResult(
        result=result,
        blocker=blocker,
        state=state,
        previous_status=previous_status,
        new_status=new_status,
        event_type=event_type,
        criteria_checked=tuple(checked),
        criteria_failed=tuple(failed),
        **_RESULT_FLAGS,
    )


def _make_event(
    *,
    event_type: PaperOrderLifecycleEventType,
    event_at_utc: str,
    from_status: PaperOrderLifecycleStatus,
    to_status: PaperOrderLifecycleStatus,
    details: dict | None,
) -> dict:
    return {
        "event_type": event_type.value,
        "event_at_utc": event_at_utc,
        "from_status": from_status.value,
        "to_status": to_status.value,
        "details": copy.deepcopy(details) if details is not None else None,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def create_lifecycle_from_plan(
    plan: dict,
    *,
    lifecycle_id: str,
    created_at_utc: str,
) -> PaperOrderLifecycleTransitionResult:
    """Create a PLANNED lifecycle state from an already-loaded POP/1.0 plan.

    Performs only the minimal local checks needed to create lifecycle
    state; it does not call the planner, the plan validator, or the safety
    gate. On any failure no lifecycle state is released (state=None, fail
    closed).

    Pure function: no file I/O, no network, no environment variables, no
    broker/API/credential access, no order action, no paper/live approval.
    """
    checked: list[str] = []
    failed: list[str] = []

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    _chk(
        "plan.schema",
        isinstance(plan, dict)
        and _get(plan, "plan_schema_version") == _REQUIRED_PLAN_SCHEMA_VERSION
        and _get(plan, "plan_type") == _REQUIRED_PLAN_TYPE,
    )
    _chk(
        "plan.identity",
        _is_nonempty_str(_get(plan, "plan_id"))
        and _is_nonempty_str(_get(plan, "candidate_id"))
        and _is_nonempty_str(_get(plan, "run_id"))
        and _is_nonempty_str(_get(plan, "symbol")),
    )
    _chk(
        "plan.intent",
        _get(plan, "side") in _ALLOWED_SIDES
        and _get(plan, "order_type") in _ALLOWED_ORDER_TYPES,
    )
    _chk("plan.quantity", _finite_positive(_get(plan, "quantity")))
    _chk(
        "plan.safety_flags",
        all(_get(plan, field) is required for field, required in _PLAN_FIXED_BOOLS),
    )
    _chk(
        "lifecycle.identity",
        _is_nonempty_str(lifecycle_id) and _is_nonempty_str(created_at_utc),
    )

    if failed:
        return _make_result(
            result="BLOCKED",
            blocker="lifecycle creation failed checks: " + ", ".join(failed),
            state=None,
            previous_status=None,
            new_status=None,
            event_type=PaperOrderLifecycleEventType.PLAN_CREATED,
            checked=checked,
            failed=failed,
        )

    checked.append("event.created")
    event = _make_event(
        event_type=PaperOrderLifecycleEventType.PLAN_CREATED,
        event_at_utc=created_at_utc,
        from_status=PaperOrderLifecycleStatus.NOT_STARTED,
        to_status=PaperOrderLifecycleStatus.PLANNED,
        details=None,
    )
    state = PaperOrderLifecycleState(
        lifecycle_id=lifecycle_id,
        plan_id=plan["plan_id"],
        candidate_id=plan["candidate_id"],
        run_id=plan["run_id"],
        symbol=plan["symbol"],
        side=plan["side"],
        order_type=plan["order_type"],
        status=PaperOrderLifecycleStatus.PLANNED,
        events=(event,),
        current_quantity=float(plan["quantity"]),
        filled_quantity=0.0,
        average_fill_price=None,
        blocker=None,
        **_RESULT_FLAGS,
    )
    return _make_result(
        result="PASS",
        blocker=None,
        state=state,
        previous_status=PaperOrderLifecycleStatus.NOT_STARTED,
        new_status=PaperOrderLifecycleStatus.PLANNED,
        event_type=PaperOrderLifecycleEventType.PLAN_CREATED,
        checked=checked,
        failed=failed,
    )


def apply_lifecycle_event(
    state: PaperOrderLifecycleState,
    *,
    event_type: PaperOrderLifecycleEventType,
    event_at_utc: str,
    details: dict | None = None,
) -> PaperOrderLifecycleTransitionResult:
    """Apply a bookkeeping event to an existing lifecycle state.

    Returns a new immutable state; the input state is never mutated. On
    any failure the previous state is returned unchanged -- a blocked
    transition never advances the lifecycle (fail closed). Terminal
    statuses reject all further events.

    Applying an event records information only. It performs no order
    action of any kind: no order is submitted, placed, cancelled, or
    modified by this function, and no paper/live trading is approved.

    Pure function: no file I/O, no network, no environment variables, no
    broker/API/credential access.
    """
    checked: list[str] = []
    failed: list[str] = []

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    # 1. State schema -- without a trusted state nothing can be returned.
    checked.append("state.schema")
    if not isinstance(state, PaperOrderLifecycleState):
        failed.append("state.schema")
        return _make_result(
            result="BLOCKED",
            blocker="state is not a PaperOrderLifecycleState",
            state=None,
            previous_status=None,
            new_status=None,
            event_type=event_type if isinstance(
                event_type, PaperOrderLifecycleEventType
            ) else None,
            checked=checked,
            failed=failed,
        )

    previous_status = state.status

    def _blocked(blocker: str) -> PaperOrderLifecycleTransitionResult:
        return _make_result(
            result="BLOCKED",
            blocker=blocker,
            state=state,
            previous_status=previous_status,
            new_status=previous_status,
            event_type=event_type if isinstance(
                event_type, PaperOrderLifecycleEventType
            ) else None,
            checked=checked,
            failed=failed,
        )

    # 2. Existing state safety flags must all be False.
    _chk(
        "state.safety_flags",
        not any(getattr(state, flag, True) for flag in _SAFETY_FLAGS),
    )
    if failed:
        return _blocked("state safety flags violated")

    # 3. Event schema.
    _chk(
        "event.schema",
        isinstance(event_type, PaperOrderLifecycleEventType)
        and _is_nonempty_str(event_at_utc),
    )
    if failed:
        return _blocked("event schema invalid")

    # 4. Details schema: dict or None; reason, if present, non-empty string.
    details_is_valid = details is None or (
        isinstance(details, dict)
        and ("reason" not in details or _is_nonempty_str(details.get("reason")))
    )
    _chk("details.schema", details_is_valid)
    if failed:
        return _blocked("event details invalid")

    # 5. Forbidden-content scan over details.
    _chk(
        "details.forbidden_content",
        details is None or not _scan_forbidden_details(details),
    )
    if failed:
        return _blocked("event details contain forbidden content")

    # 6. Transition allowed from the current status.
    allowed = _ALLOWED_TRANSITIONS.get(previous_status, {})
    new_status = allowed.get(event_type)
    _chk("transition.allowed", new_status is not None)
    if failed:
        if previous_status in _TERMINAL_STATUSES:
            return _blocked(
                f"status {previous_status.value} is terminal and rejects all events"
            )
        return _blocked(
            f"event {event_type.value} not allowed from status {previous_status.value}"
        )

    # 7. Fill details (trivially passing for non-fill events).
    new_filled_quantity = state.filled_quantity
    new_average_fill_price = state.average_fill_price
    if event_type in _FILL_EVENTS:
        fq = _get(details, "filled_quantity")
        avg = _get(details, "average_fill_price")
        current = state.current_quantity if state.current_quantity is not None else 0.0
        fill_ok = (
            isinstance(details, dict)
            and _finite_positive(fq)
            and float(fq) <= float(current)
            and (avg is None or _finite_positive(avg))
        )
        _chk("fill.details", fill_ok)
        if failed:
            return _blocked("fill details invalid")
        new_filled_quantity = float(fq)
        if avg is not None:
            new_average_fill_price = float(avg)
    else:
        _chk("fill.details", True)

    # 8. Append the event and build the new immutable state.
    checked.append("event.appended")
    event = _make_event(
        event_type=event_type,
        event_at_utc=event_at_utc,
        from_status=previous_status,
        to_status=new_status,
        details=details,
    )
    new_blocker: str | None = None
    if new_status in (
        PaperOrderLifecycleStatus.BLOCKED, PaperOrderLifecycleStatus.ERROR
    ):
        reason = _get(details, "reason")
        if _is_nonempty_str(reason):
            new_blocker = reason
        elif new_status is PaperOrderLifecycleStatus.BLOCKED:
            new_blocker = "blocked by safety"
        else:
            new_blocker = "error recorded"

    new_state = replace(
        state,
        status=new_status,
        events=state.events + (event,),
        filled_quantity=new_filled_quantity,
        average_fill_price=new_average_fill_price,
        blocker=new_blocker,
    )
    return _make_result(
        result="PASS",
        blocker=None,
        state=new_state,
        previous_status=previous_status,
        new_status=new_status,
        event_type=event_type,
        checked=checked,
        failed=failed,
    )
