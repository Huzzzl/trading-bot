"""
tests/test_paper_order_lifecycle.py
------------------------------------
S32: Tests for the pure offline paper order lifecycle state machine.

All fixtures are plain in-memory dicts. No real artifacts are created.
No files are read or written. No broker, network, credential, environment-
variable, or order access is made.

Lifecycle transitions are bookkeeping only -- marking a lifecycle as
filled/rejected/cancelled changes an in-memory record and never performs,
initiates, or approves any order action. Paper trading remains not
approved; live trading remains blocked.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import FrozenInstanceError, fields as dataclass_fields, replace

import pytest

import src.research.paper_order_lifecycle as _lifecycle_mod
from src.research.paper_order_lifecycle import (
    PaperOrderLifecycleEventType,
    PaperOrderLifecycleState,
    PaperOrderLifecycleStatus,
    PaperOrderLifecycleTransitionResult,
    apply_lifecycle_event,
    create_lifecycle_from_plan,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_CREATED_AT = "2025-01-15T09:05:00Z"
_EVENT_AT = "2025-01-15T09:10:00Z"
_LIFECYCLE_ID = "lc-001"

Status = PaperOrderLifecycleStatus
Event = PaperOrderLifecycleEventType


def _valid_plan(**overrides) -> dict:
    """Return a POP/1.0 plan dict with the fields the lifecycle checks."""
    base = {
        "plan_schema_version": "POP/1.0",
        "plan_type": "PAPER_ORDER_PLAN",
        "plan_id": "plan-001",
        "candidate_id": "cand-001",
        "run_id": "run-001",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "market",
        "quantity": 10.0,
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
        "live_trading_allowed": False,
        "dry_run_required": True,
        "human_confirmation_required": True,
        "kill_switch_required": True,
        "safety_gate_required": True,
    }
    base.update(overrides)
    return base


def _create(plan=None, **kwargs) -> PaperOrderLifecycleTransitionResult:
    if plan is None:
        plan = _valid_plan()
    kwargs.setdefault("lifecycle_id", _LIFECYCLE_ID)
    kwargs.setdefault("created_at_utc", _CREATED_AT)
    return create_lifecycle_from_plan(plan, **kwargs)


def _apply(state, event_type, details=None, at=_EVENT_AT):
    return apply_lifecycle_event(
        state, event_type=event_type, event_at_utc=at, details=details
    )


def _state_at(status: Status) -> PaperOrderLifecycleState:
    """Walk the real machine to a given status (no hand-built states)."""
    state = _create().state
    if status is Status.PLANNED:
        return state
    state = _apply(state, Event.SAFETY_GATE_PASSED).state
    if status is Status.GATE_PASSED_DRY_RUN_ONLY:
        return state
    state = _apply(state, Event.DRY_RUN_RENDERED).state
    if status is Status.DRY_RUN_RENDERED:
        return state
    state = _apply(state, Event.PAPER_ORDER_MARKED_PENDING).state
    if status is Status.PAPER_ORDER_PENDING:
        return state
    if status is Status.PAPER_ORDER_PARTIALLY_FILLED:
        return _apply(
            state, Event.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
            details={"filled_quantity": 4.0},
        ).state
    if status is Status.PAPER_ORDER_FILLED:
        return _apply(
            state, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0},
        ).state
    if status is Status.PAPER_ORDER_REJECTED:
        return _apply(state, Event.PAPER_ORDER_MARKED_REJECTED).state
    if status is Status.PAPER_ORDER_CANCELLED:
        return _apply(state, Event.PAPER_ORDER_MARKED_CANCELLED).state
    if status is Status.PAPER_ORDER_EXPIRED:
        return _apply(state, Event.PAPER_ORDER_MARKED_EXPIRED).state
    if status is Status.BLOCKED:
        return _apply(state, Event.BLOCKED_BY_SAFETY).state
    if status is Status.ERROR:
        return _apply(state, Event.ERROR_RECORDED).state
    raise AssertionError(f"unreachable status fixture: {status}")


_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

_NON_TERMINAL_STATUSES: tuple[Status, ...] = (
    Status.PLANNED,
    Status.GATE_PASSED_DRY_RUN_ONLY,
    Status.DRY_RUN_RENDERED,
    Status.PAPER_ORDER_PENDING,
    Status.PAPER_ORDER_PARTIALLY_FILLED,
)

_TERMINAL_STATUSES: tuple[Status, ...] = (
    Status.PAPER_ORDER_FILLED,
    Status.PAPER_ORDER_REJECTED,
    Status.PAPER_ORDER_CANCELLED,
    Status.PAPER_ORDER_EXPIRED,
    Status.BLOCKED,
    Status.ERROR,
)

# Forbidden detail words, assembled from fragments so the joined words
# never appear contiguously in this module's source.
_FORBIDDEN_ACTION_WORDS: tuple[str, ...] = (
    "submit_" + "order",
    "place_" + "order",
    "cancel_" + "order",
    "modify_" + "order",
    "live_" + "submit",
)
_FORBIDDEN_CREDENTIAL_WORDS: tuple[str, ...] = (
    "api_key", "secret", "credential", "token",
)
_FORBIDDEN_NETWORK_WORDS: tuple[str, ...] = (
    "http", "https", "endpoint",
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestStatusEnum:
    def test_all_expected_values_present(self):
        expected = {
            "NOT_STARTED", "PLANNED", "GATE_PASSED_DRY_RUN_ONLY",
            "DRY_RUN_RENDERED", "PAPER_ORDER_PENDING", "PAPER_ORDER_FILLED",
            "PAPER_ORDER_PARTIALLY_FILLED", "PAPER_ORDER_REJECTED",
            "PAPER_ORDER_CANCELLED", "PAPER_ORDER_EXPIRED", "BLOCKED", "ERROR",
        }
        assert {m.value for m in Status} == expected

    def test_status_enum_is_str_subclass(self):
        for member in Status:
            assert isinstance(member, str)
            assert member == member.value


class TestEventTypeEnum:
    def test_all_expected_values_present(self):
        expected = {
            "PLAN_CREATED", "SAFETY_GATE_PASSED", "DRY_RUN_RENDERED",
            "PAPER_ORDER_MARKED_PENDING", "PAPER_ORDER_MARKED_FILLED",
            "PAPER_ORDER_MARKED_PARTIALLY_FILLED", "PAPER_ORDER_MARKED_REJECTED",
            "PAPER_ORDER_MARKED_CANCELLED", "PAPER_ORDER_MARKED_EXPIRED",
            "BLOCKED_BY_SAFETY", "ERROR_RECORDED",
        }
        assert {m.value for m in Event} == expected

    def test_event_enum_is_str_subclass(self):
        for member in Event:
            assert isinstance(member, str)
            assert member == member.value


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_state_is_frozen(self):
        state = _create().state
        with pytest.raises(FrozenInstanceError):
            state.status = Status.ERROR  # type: ignore[misc]

    def test_transition_result_is_frozen(self):
        result = _create()
        with pytest.raises(FrozenInstanceError):
            result.result = "tampered"  # type: ignore[misc]

    def test_state_has_expected_fields(self):
        field_names = {f.name for f in dataclass_fields(PaperOrderLifecycleState)}
        expected = {
            "lifecycle_id", "plan_id", "candidate_id", "run_id",
            "symbol", "side", "order_type", "status", "events",
            "current_quantity", "filled_quantity", "average_fill_price",
            "blocker",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_transition_result_has_expected_fields(self):
        field_names = {
            f.name for f in dataclass_fields(PaperOrderLifecycleTransitionResult)
        }
        expected = {
            "result", "blocker", "state", "previous_status", "new_status",
            "event_type", "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected


# ---------------------------------------------------------------------------
# create_lifecycle_from_plan
# ---------------------------------------------------------------------------

class TestCreateLifecycle:
    def test_valid_plan_creates_planned_lifecycle(self):
        result = _create()
        assert result.result == "PASS"
        assert result.blocker is None
        assert result.previous_status is Status.NOT_STARTED
        assert result.new_status is Status.PLANNED
        assert result.event_type is Event.PLAN_CREATED
        state = result.state
        assert state.status is Status.PLANNED
        assert state.lifecycle_id == _LIFECYCLE_ID
        assert state.plan_id == "plan-001"
        assert state.candidate_id == "cand-001"
        assert state.run_id == "run-001"
        assert state.symbol == "AAPL"
        assert state.side == "BUY"
        assert state.order_type == "market"
        assert state.current_quantity == 10.0
        assert state.filled_quantity == 0.0
        assert state.average_fill_price is None
        assert state.blocker is None

    def test_valid_plan_records_plan_created_event(self):
        state = _create().state
        assert len(state.events) == 1
        event = state.events[0]
        assert event["event_type"] == "PLAN_CREATED"
        assert event["event_at_utc"] == _CREATED_AT
        assert event["from_status"] == "NOT_STARTED"
        assert event["to_status"] == "PLANNED"
        assert event["details"] is None

    def test_criteria_checked_in_order(self):
        result = _create()
        assert result.criteria_checked == (
            "plan.schema", "plan.identity", "plan.intent", "plan.quantity",
            "plan.safety_flags", "lifecycle.identity", "event.created",
        )
        assert result.criteria_failed == ()

    def test_non_dict_plan_blocked(self):
        result = _create(plan="not-a-dict")
        assert result.result == "BLOCKED"
        assert result.state is None
        assert "plan.schema" in result.criteria_failed

    @pytest.mark.parametrize(
        "override",
        [
            {"plan_schema_version": "POP/2.0"},
            {"plan_type": "LIVE_ORDER"},
        ],
        ids=["schema_version", "plan_type"],
    )
    def test_wrong_schema_or_type_blocked(self, override):
        result = _create(plan=_valid_plan(**override))
        assert result.result == "BLOCKED"
        assert result.state is None
        assert "plan.schema" in result.criteria_failed

    @pytest.mark.parametrize("field", ["plan_id", "candidate_id", "run_id", "symbol"])
    def test_missing_identity_field_blocked(self, field):
        plan = _valid_plan()
        del plan[field]
        result = _create(plan=plan)
        assert result.result == "BLOCKED"
        assert "plan.identity" in result.criteria_failed

    @pytest.mark.parametrize(
        "override",
        [{"side": "HOLD"}, {"side": "buy"}, {"order_type": "stop"}, {"order_type": ""}],
        ids=["side_hold", "side_lowercase", "order_type_stop", "order_type_empty"],
    )
    def test_invalid_intent_blocked(self, override):
        result = _create(plan=_valid_plan(**override))
        assert result.result == "BLOCKED"
        assert "plan.intent" in result.criteria_failed

    @pytest.mark.parametrize(
        "quantity", [0.0, -5.0, float("inf"), float("nan"), None, "ten"],
        ids=["zero", "negative", "inf", "nan", "none", "string"],
    )
    def test_invalid_quantity_blocked(self, quantity):
        result = _create(plan=_valid_plan(quantity=quantity))
        assert result.result == "BLOCKED"
        assert "plan.quantity" in result.criteria_failed

    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    def test_plan_safety_flag_true_blocked(self, flag):
        result = _create(plan=_valid_plan(**{flag: True}))
        assert result.result == "BLOCKED"
        assert result.state is None
        assert "plan.safety_flags" in result.criteria_failed

    @pytest.mark.parametrize(
        "field",
        [
            "dry_run_required", "human_confirmation_required",
            "kill_switch_required", "safety_gate_required",
        ],
    )
    def test_required_plan_boolean_false_blocked(self, field):
        result = _create(plan=_valid_plan(**{field: False}))
        assert result.result == "BLOCKED"
        assert "plan.safety_flags" in result.criteria_failed

    @pytest.mark.parametrize("lifecycle_id", ["", "   "])
    def test_empty_lifecycle_id_blocked(self, lifecycle_id):
        result = _create(lifecycle_id=lifecycle_id)
        assert result.result == "BLOCKED"
        assert "lifecycle.identity" in result.criteria_failed

    def test_empty_created_at_blocked(self):
        result = _create(created_at_utc="")
        assert result.result == "BLOCKED"
        assert "lifecycle.identity" in result.criteria_failed

    def test_create_does_not_mutate_plan(self):
        plan = _valid_plan()
        snapshot = copy.deepcopy(plan)
        _create(plan=plan)
        assert plan == snapshot


# ---------------------------------------------------------------------------
# Happy-path transitions
# ---------------------------------------------------------------------------

class TestHappyPathTransitions:
    def test_planned_to_gate_passed(self):
        result = _apply(_state_at(Status.PLANNED), Event.SAFETY_GATE_PASSED)
        assert result.result == "PASS"
        assert result.previous_status is Status.PLANNED
        assert result.new_status is Status.GATE_PASSED_DRY_RUN_ONLY
        assert result.state.status is Status.GATE_PASSED_DRY_RUN_ONLY
        assert len(result.state.events) == 2

    def test_gate_passed_to_dry_run_rendered(self):
        result = _apply(
            _state_at(Status.GATE_PASSED_DRY_RUN_ONLY), Event.DRY_RUN_RENDERED
        )
        assert result.result == "PASS"
        assert result.state.status is Status.DRY_RUN_RENDERED

    def test_dry_run_rendered_to_pending(self):
        result = _apply(
            _state_at(Status.DRY_RUN_RENDERED), Event.PAPER_ORDER_MARKED_PENDING
        )
        assert result.result == "PASS"
        assert result.state.status is Status.PAPER_ORDER_PENDING

    def test_pending_to_filled(self):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": 150.25},
        )
        assert result.result == "PASS"
        assert result.state.status is Status.PAPER_ORDER_FILLED
        assert result.state.filled_quantity == 10.0
        assert result.state.average_fill_price == 150.25

    def test_pending_to_partially_filled(self):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
            details={"filled_quantity": 4.0},
        )
        assert result.result == "PASS"
        assert result.state.status is Status.PAPER_ORDER_PARTIALLY_FILLED
        assert result.state.filled_quantity == 4.0

    def test_partially_filled_to_filled(self):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PARTIALLY_FILLED),
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0},
        )
        assert result.result == "PASS"
        assert result.state.status is Status.PAPER_ORDER_FILLED
        assert result.state.filled_quantity == 10.0

    @pytest.mark.parametrize(
        ("event", "expected_status"),
        [
            (Event.PAPER_ORDER_MARKED_REJECTED, Status.PAPER_ORDER_REJECTED),
            (Event.PAPER_ORDER_MARKED_CANCELLED, Status.PAPER_ORDER_CANCELLED),
            (Event.PAPER_ORDER_MARKED_EXPIRED, Status.PAPER_ORDER_EXPIRED),
        ],
        ids=["rejected", "cancelled", "expired"],
    )
    def test_pending_to_terminal_outcomes(self, event, expected_status):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING), event,
            details={"reason": "paper venue closed the request"},
        )
        assert result.result == "PASS"
        assert result.state.status is expected_status

    def test_event_dict_records_transition(self):
        result = _apply(_state_at(Status.PLANNED), Event.SAFETY_GATE_PASSED)
        event = result.state.events[-1]
        assert event["event_type"] == "SAFETY_GATE_PASSED"
        assert event["event_at_utc"] == _EVENT_AT
        assert event["from_status"] == "PLANNED"
        assert event["to_status"] == "GATE_PASSED_DRY_RUN_ONLY"

    def test_full_chain_events_grow_deterministically(self):
        state = _state_at(Status.PAPER_ORDER_FILLED)
        assert state.status is Status.PAPER_ORDER_FILLED
        assert [e["event_type"] for e in state.events] == [
            "PLAN_CREATED", "SAFETY_GATE_PASSED", "DRY_RUN_RENDERED",
            "PAPER_ORDER_MARKED_PENDING", "PAPER_ORDER_MARKED_FILLED",
        ]


# ---------------------------------------------------------------------------
# Invalid transitions and terminal states
# ---------------------------------------------------------------------------

class TestInvalidTransitions:
    @pytest.mark.parametrize(
        ("status", "event"),
        [
            (Status.PLANNED, Event.DRY_RUN_RENDERED),
            (Status.PLANNED, Event.PAPER_ORDER_MARKED_FILLED),
            (Status.PLANNED, Event.PAPER_ORDER_MARKED_PENDING),
            (Status.GATE_PASSED_DRY_RUN_ONLY, Event.PAPER_ORDER_MARKED_PENDING),
            (Status.GATE_PASSED_DRY_RUN_ONLY, Event.SAFETY_GATE_PASSED),
            (Status.DRY_RUN_RENDERED, Event.PAPER_ORDER_MARKED_FILLED),
            (Status.PAPER_ORDER_PENDING, Event.SAFETY_GATE_PASSED),
            (Status.PAPER_ORDER_PARTIALLY_FILLED, Event.PAPER_ORDER_MARKED_REJECTED),
            (Status.PAPER_ORDER_PARTIALLY_FILLED, Event.PAPER_ORDER_MARKED_PENDING),
        ],
    )
    def test_disallowed_transition_blocked(self, status, event):
        state = _state_at(status)
        details = (
            {"filled_quantity": 5.0} if event in (
                Event.PAPER_ORDER_MARKED_FILLED,
                Event.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
            ) else None
        )
        result = _apply(state, event, details=details)
        assert result.result == "BLOCKED"
        assert "transition.allowed" in result.criteria_failed
        assert result.state is state
        assert result.new_status is status
        assert len(result.state.events) == len(state.events)

    @pytest.mark.parametrize("status", _TERMINAL_STATUSES)
    @pytest.mark.parametrize(
        "event",
        [
            Event.SAFETY_GATE_PASSED, Event.DRY_RUN_RENDERED,
            Event.PAPER_ORDER_MARKED_PENDING, Event.PAPER_ORDER_MARKED_CANCELLED,
            Event.BLOCKED_BY_SAFETY, Event.ERROR_RECORDED,
        ],
    )
    def test_terminal_states_reject_all_events(self, status, event):
        state = _state_at(status)
        result = _apply(state, event)
        assert result.result == "BLOCKED"
        assert "transition.allowed" in result.criteria_failed
        assert "terminal" in result.blocker
        assert result.state is state
        assert result.state.status is status

    @pytest.mark.parametrize("status", _NON_TERMINAL_STATUSES)
    def test_blocked_by_safety_from_any_non_terminal(self, status):
        result = _apply(
            _state_at(status), Event.BLOCKED_BY_SAFETY,
            details={"reason": "kill switch engaged during review"},
        )
        assert result.result == "PASS"
        assert result.previous_status is status
        assert result.state.status is Status.BLOCKED
        assert result.state.blocker == "kill switch engaged during review"

    @pytest.mark.parametrize("status", _NON_TERMINAL_STATUSES)
    def test_error_recorded_from_any_non_terminal(self, status):
        result = _apply(_state_at(status), Event.ERROR_RECORDED)
        assert result.result == "PASS"
        assert result.state.status is Status.ERROR
        assert result.state.blocker == "error recorded"


# ---------------------------------------------------------------------------
# Fill events
# ---------------------------------------------------------------------------

class TestFillEvents:
    @pytest.mark.parametrize(
        "details",
        [
            None,
            {},
            {"filled_quantity": 0.0},
            {"filled_quantity": -1.0},
            {"filled_quantity": float("inf")},
            {"filled_quantity": float("nan")},
            {"filled_quantity": "four"},
        ],
        ids=["none", "empty", "zero", "negative", "inf", "nan", "string"],
    )
    def test_fill_requires_positive_filled_quantity(self, details):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_FILLED, details=details,
        )
        assert result.result == "BLOCKED"
        assert "fill.details" in result.criteria_failed
        assert result.state.status is Status.PAPER_ORDER_PENDING

    def test_fill_rejects_quantity_above_current(self):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 11.0},  # current_quantity is 10.0
        )
        assert result.result == "BLOCKED"
        assert "fill.details" in result.criteria_failed

    def test_partial_fill_rejects_quantity_above_current(self):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
            details={"filled_quantity": 10.5},
        )
        assert result.result == "BLOCKED"
        assert "fill.details" in result.criteria_failed

    def test_fill_accepts_finite_positive_average_price(self):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": 151.5},
        )
        assert result.result == "PASS"
        assert result.state.average_fill_price == 151.5

    @pytest.mark.parametrize(
        "price", [0.0, -1.0, float("inf"), float("nan"), "abc", True],
        ids=["zero", "negative", "inf", "nan", "string", "bool"],
    )
    def test_fill_rejects_invalid_average_price(self, price):
        result = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": price},
        )
        assert result.result == "BLOCKED"
        assert "fill.details" in result.criteria_failed

    def test_partial_then_full_fill_updates_quantities_and_price(self):
        partial = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
            details={"filled_quantity": 4.0, "average_fill_price": 150.0},
        )
        assert partial.state.filled_quantity == 4.0
        assert partial.state.average_fill_price == 150.0
        full = _apply(
            partial.state,
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": 150.4},
        )
        assert full.state.filled_quantity == 10.0
        assert full.state.average_fill_price == 150.4

    def test_fill_without_price_keeps_previous_price(self):
        partial = _apply(
            _state_at(Status.PAPER_ORDER_PENDING),
            Event.PAPER_ORDER_MARKED_PARTIALLY_FILLED,
            details={"filled_quantity": 4.0, "average_fill_price": 150.0},
        )
        full = _apply(
            partial.state,
            Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0},
        )
        assert full.state.average_fill_price == 150.0

    @pytest.mark.parametrize(
        "event",
        [
            Event.PAPER_ORDER_MARKED_CANCELLED,
            Event.PAPER_ORDER_MARKED_EXPIRED,
        ],
        ids=["cancelled", "expired"],
    )
    def test_cancel_expire_keep_filled_quantity(self, event):
        partial_state = _state_at(Status.PAPER_ORDER_PARTIALLY_FILLED)
        assert partial_state.filled_quantity == 4.0
        result = _apply(partial_state, event)
        assert result.result == "PASS"
        assert result.state.filled_quantity == 4.0

    def test_reject_keeps_filled_quantity(self):
        pending = _state_at(Status.PAPER_ORDER_PENDING)
        result = _apply(pending, Event.PAPER_ORDER_MARKED_REJECTED)
        assert result.result == "PASS"
        assert result.state.filled_quantity == pending.filled_quantity == 0.0


# ---------------------------------------------------------------------------
# Forbidden detail content
# ---------------------------------------------------------------------------

class TestForbiddenDetails:
    def _assert_blocked_unchanged(self, state, result, event):
        assert result.result == "BLOCKED"
        assert "details.forbidden_content" in result.criteria_failed
        assert result.state is state
        assert result.previous_status is state.status
        assert result.new_status is state.status
        assert result.event_type is event
        assert len(result.state.events) == len(state.events)

    @pytest.mark.parametrize("word", _FORBIDDEN_ACTION_WORDS)
    def test_action_words_in_value_blocked(self, word):
        state = _state_at(Status.PLANNED)
        result = _apply(
            state, Event.SAFETY_GATE_PASSED,
            details={"note": f"next step calls {word} for execution"},
        )
        self._assert_blocked_unchanged(state, result, Event.SAFETY_GATE_PASSED)

    @pytest.mark.parametrize("word", _FORBIDDEN_ACTION_WORDS)
    def test_action_words_in_key_blocked(self, word):
        state = _state_at(Status.PLANNED)
        result = _apply(
            state, Event.SAFETY_GATE_PASSED, details={word: True},
        )
        self._assert_blocked_unchanged(state, result, Event.SAFETY_GATE_PASSED)

    @pytest.mark.parametrize("word", _FORBIDDEN_CREDENTIAL_WORDS)
    def test_credential_words_blocked(self, word):
        state = _state_at(Status.PAPER_ORDER_PENDING)
        result = _apply(
            state, Event.PAPER_ORDER_MARKED_CANCELLED,
            details={"context": {"value": f"uses {word} internally"}},
        )
        self._assert_blocked_unchanged(
            state, result, Event.PAPER_ORDER_MARKED_CANCELLED
        )

    @pytest.mark.parametrize("word", _FORBIDDEN_NETWORK_WORDS)
    def test_network_words_blocked(self, word):
        state = _state_at(Status.GATE_PASSED_DRY_RUN_ONLY)
        result = _apply(
            state, Event.DRY_RUN_RENDERED,
            details={"target": f"{word}-bound destination"},
        )
        self._assert_blocked_unchanged(state, result, Event.DRY_RUN_RENDERED)

    def test_forbidden_word_in_nested_list_blocked(self):
        state = _state_at(Status.PLANNED)
        result = _apply(
            state, Event.SAFETY_GATE_PASSED,
            details={"steps": ["render", "then " + "live_" + "submit"]},
        )
        self._assert_blocked_unchanged(state, result, Event.SAFETY_GATE_PASSED)

    def test_scan_is_case_insensitive(self):
        state = _state_at(Status.PLANNED)
        result = _apply(
            state, Event.SAFETY_GATE_PASSED,
            details={"note": "API_KEY rotation pending"},
        )
        self._assert_blocked_unchanged(state, result, Event.SAFETY_GATE_PASSED)

    def test_harmless_details_pass(self):
        result = _apply(
            _state_at(Status.PLANNED), Event.SAFETY_GATE_PASSED,
            details={"note": "gate review recorded for audit trail"},
        )
        assert result.result == "PASS"


# ---------------------------------------------------------------------------
# Input validation for apply_lifecycle_event
# ---------------------------------------------------------------------------

class TestApplyInputValidation:
    def test_non_state_input_blocked(self):
        result = _apply("not-a-state", Event.SAFETY_GATE_PASSED)
        assert result.result == "BLOCKED"
        assert result.state is None
        assert "state.schema" in result.criteria_failed

    def test_non_enum_event_type_blocked(self):
        state = _state_at(Status.PLANNED)
        result = _apply(state, "SAFETY_GATE_PASSED_RAW_STRING")
        assert result.result == "BLOCKED"
        assert "event.schema" in result.criteria_failed
        assert result.state is state

    def test_empty_event_at_blocked(self):
        state = _state_at(Status.PLANNED)
        result = _apply(state, Event.SAFETY_GATE_PASSED, at="")
        assert result.result == "BLOCKED"
        assert "event.schema" in result.criteria_failed

    def test_non_dict_details_blocked(self):
        state = _state_at(Status.PLANNED)
        result = _apply(state, Event.SAFETY_GATE_PASSED, details="notes")
        assert result.result == "BLOCKED"
        assert "details.schema" in result.criteria_failed

    def test_empty_reason_blocked(self):
        state = _state_at(Status.PAPER_ORDER_PENDING)
        result = _apply(
            state, Event.PAPER_ORDER_MARKED_CANCELLED, details={"reason": ""}
        )
        assert result.result == "BLOCKED"
        assert "details.schema" in result.criteria_failed

    def test_tainted_state_safety_flag_blocked(self):
        state = replace(_state_at(Status.PLANNED), order_action_requested=True)
        result = _apply(state, Event.SAFETY_GATE_PASSED)
        assert result.result == "BLOCKED"
        assert "state.safety_flags" in result.criteria_failed
        assert result.new_status is state.status


# ---------------------------------------------------------------------------
# Purity and immutability
# ---------------------------------------------------------------------------

class TestPurityAndImmutability:
    def test_apply_does_not_mutate_old_state(self):
        old_state = _state_at(Status.PLANNED)
        old_events = copy.deepcopy(old_state.events)
        result = _apply(old_state, Event.SAFETY_GATE_PASSED)
        assert old_state.status is Status.PLANNED
        assert old_state.events == old_events
        assert len(old_state.events) == 1
        assert result.state is not old_state
        assert len(result.state.events) == 2

    def test_details_deep_copied_into_event(self):
        details = {"context": {"window": "60m"}}
        result = _apply(
            _state_at(Status.PLANNED), Event.SAFETY_GATE_PASSED, details=details
        )
        details["context"]["window"] = "mutated"
        assert result.state.events[-1]["details"] == {"context": {"window": "60m"}}

    def test_events_tuple_grows_by_one_per_event(self):
        state = _state_at(Status.PLANNED)
        for index, event in enumerate(
            (
                Event.SAFETY_GATE_PASSED,
                Event.DRY_RUN_RENDERED,
                Event.PAPER_ORDER_MARKED_PENDING,
            ),
            start=2,
        ):
            state = _apply(state, event).state
            assert len(state.events) == index

    def test_same_create_input_same_output(self):
        assert _create() == _create()

    def test_same_apply_input_same_output(self):
        state = _state_at(Status.PLANNED)
        r1 = _apply(state, Event.SAFETY_GATE_PASSED)
        r2 = _apply(state, Event.SAFETY_GATE_PASSED)
        assert r1 == r2

    def test_same_blocked_input_same_output(self):
        state = _state_at(Status.PAPER_ORDER_FILLED)
        r1 = _apply(state, Event.SAFETY_GATE_PASSED)
        r2 = _apply(state, Event.SAFETY_GATE_PASSED)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Safety flags always False
# ---------------------------------------------------------------------------

class TestSafetyFlagsAlwaysFalse:
    def _scenarios(self):
        planned = _state_at(Status.PLANNED)
        return [
            ("create_pass", _create()),
            ("create_blocked", _create(plan={})),
            ("apply_pass", _apply(planned, Event.SAFETY_GATE_PASSED)),
            ("apply_blocked_transition", _apply(planned, Event.DRY_RUN_RENDERED)),
            (
                "apply_blocked_details",
                _apply(planned, Event.SAFETY_GATE_PASSED, details={"k": "api_key"}),
            ),
            ("apply_to_blocked_status", _apply(planned, Event.BLOCKED_BY_SAFETY)),
            ("apply_to_error_status", _apply(planned, Event.ERROR_RECORDED)),
        ]

    def test_transition_result_flags_always_false(self):
        for label, result in self._scenarios():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(result, flag) is False, (
                    f"{flag} should be False in scenario {label}"
                )

    def test_state_flags_always_false(self):
        for label, result in self._scenarios():
            if result.state is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(result.state, flag) is False, (
                    f"state {flag} should be False in scenario {label}"
                )

    def test_all_reachable_statuses_have_false_flags(self):
        for status in _NON_TERMINAL_STATUSES + _TERMINAL_STATUSES:
            state = _state_at(status)
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(state, flag) is False


# ---------------------------------------------------------------------------
# Source hygiene: lifecycle module
# ---------------------------------------------------------------------------

class TestNoForbiddenPatternsInSource:
    """Confirm the lifecycle module performs no I/O and contains no actual
    broker/network/credential/env/order imports or calls. The forbidden
    detail-scan words inside the module are assembled from fragments, so
    the joined words must never appear contiguously in its source."""

    def _source(self) -> str:
        return inspect.getsource(_lifecycle_mod)

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
            "read_by" + "tes(",
            "write_by" + "tes(",
        ):
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

    def test_no_forbidden_imports(self):
        source = self._source()
        for pattern in (
            "import " + "requ" + "ests",
            "import " + "url" + "lib",
            "import " + "aio" + "http",
            "import " + "alp" + "aca",
            "import " + "sock" + "et",
            "import " + "subpro" + "cess",
            "import" + " " + "os",
            "from " + "path" + "lib",
            "import " + "path" + "lib",
            "Alpaca" + "Broker" + "Adapter",
        ):
            assert pattern not in source, f"forbidden import found: {pattern!r}"

    def test_no_assembled_order_verbs_in_source(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, (
                f"order verb appears contiguously in source: {pattern!r}"
            )

    def test_no_env_or_network_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
            "requ" + "ests.",
            "url" + "lib.",
            "sock" + "et.",
        ):
            assert pattern not in source, f"forbidden call found: {pattern!r}"

    def test_no_runtime_execution_or_research_chain_imports(self):
        # The lifecycle must not call the planner, validator, or safety gate.
        source = self._source()
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
            "import main",
            "paper_order_pl" + "anner",
            "paper_order_plan_vali" + "dator",
            "paper_order_safety_g" + "ate",
            "paper_approval_vali" + "dator",
        ):
            assert pattern not in source, f"forbidden module import: {pattern!r}"


# ---------------------------------------------------------------------------
# Source hygiene: this test module
# ---------------------------------------------------------------------------

class TestThisModuleClean:
    """Every pattern is built from split fragments so the joined pattern
    never appears literally in this module's own source."""

    def _source(self) -> str:
        import sys
        return inspect.getsource(sys.modules[__name__])

    def test_no_write_operations(self):
        source = self._source()
        for pattern in (
            "write_te" + "xt(",
            "write_by" + "tes(",
            "make" + "dirs(",
            "mk" + "dir(",
        ):
            assert pattern not in source, f"module contains write pattern {pattern!r}"

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
        ):
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

    def test_no_forbidden_imports(self):
        source = self._source()
        for pattern in (
            "import " + "requ" + "ests",
            "import " + "url" + "lib",
            "import " + "aio" + "http",
            "import " + "alp" + "aca",
            "import " + "sock" + "et",
            "import " + "subpro" + "cess",
            "import" + " " + "os",
            "from " + "path" + "lib",
            "import " + "path" + "lib",
            "Alpaca" + "Broker" + "Adapter",
        ):
            assert pattern not in source, f"forbidden import found: {pattern!r}"

    def test_no_assembled_order_verbs(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, (
                f"order verb appears contiguously in source: {pattern!r}"
            )

    def test_no_env_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
        ):
            assert pattern not in source, f"forbidden call found: {pattern!r}"

    def test_no_runtime_or_execution_imports(self):
        source = self._source()
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
        ):
            assert pattern not in source, f"forbidden module import: {pattern!r}"
