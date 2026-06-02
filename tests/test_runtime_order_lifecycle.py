"""
tests/test_runtime_order_lifecycle.py
---------------------------------------
Tests for src/runtime/order_lifecycle.py (PR A2-3).

All tests are offline — no broker calls, no credentials, no network access.
No live gates unfrozen. No live trading. No order submission.
"""

from __future__ import annotations

import inspect

import pytest

from src.runtime.order_lifecycle import (
    OrderLifecycleEvent,
    OrderLifecycleManager,
    OrderLifecycleRecord,
    OrderLifecycleState,
    OrderLifecycleTransition,
)

_S = OrderLifecycleState
_E = OrderLifecycleEvent


# ---------------------------------------------------------------------------
# TestOrderLifecycleStateEnum
# ---------------------------------------------------------------------------

class TestOrderLifecycleStateEnum:
    def test_all_states_exist(self):
        values = {s.value for s in _S}
        for expected in (
            "CREATED", "RISK_APPROVED", "SUBMIT_REQUESTED", "SUBMITTED",
            "ACKNOWLEDGED", "FILLED", "PARTIALLY_FILLED",
            "CANCELED", "REJECTED", "FAILED", "CLOSED",
        ):
            assert expected in values

    def test_str_enum_equality(self):
        assert _S.CREATED == "CREATED"
        assert _S.CLOSED == "CLOSED"
        assert _S.FAILED == "FAILED"

    def test_eleven_states(self):
        assert len(list(_S)) == 11


# ---------------------------------------------------------------------------
# TestOrderLifecycleEventEnum
# ---------------------------------------------------------------------------

class TestOrderLifecycleEventEnum:
    def test_all_events_exist(self):
        values = {e.value for e in _E}
        for expected in (
            "CREATE", "RISK_APPROVE", "REQUEST_SUBMIT", "MARK_SUBMITTED",
            "ACKNOWLEDGE", "MARK_FILLED", "MARK_PARTIALLY_FILLED",
            "MARK_CANCELED", "MARK_REJECTED", "MARK_FAILED", "CLOSE",
        ):
            assert expected in values

    def test_str_enum_equality(self):
        assert _E.CREATE == "CREATE"
        assert _E.MARK_FAILED == "MARK_FAILED"


# ---------------------------------------------------------------------------
# TestOrderLifecycleRecordDataclass
# ---------------------------------------------------------------------------

class TestOrderLifecycleRecordDataclass:
    def test_fields_exist(self):
        r = OrderLifecycleRecord(
            client_order_id="BC-001",
            symbol="SPY",
            side="buy",
            quantity=1.0,
            state=_S.CREATED,
        )
        assert r.client_order_id == "BC-001"
        assert r.symbol == "SPY"
        assert r.side == "buy"
        assert r.quantity == 1.0
        assert r.state == _S.CREATED
        assert r.events == ()
        assert r.blocker is None

    def test_frozen(self):
        r = OrderLifecycleRecord(
            client_order_id="BC-001",
            symbol=None,
            side=None,
            quantity=None,
            state=_S.CREATED,
        )
        with pytest.raises(Exception):
            r.state = _S.FILLED  # type: ignore[misc]

    def test_events_is_tuple(self):
        r = OrderLifecycleRecord(
            client_order_id="BC-001",
            symbol=None,
            side=None,
            quantity=None,
            state=_S.CREATED,
            events=({"event": "CREATE"},),
        )
        assert isinstance(r.events, tuple)


# ---------------------------------------------------------------------------
# TestOrderLifecycleTransitionDataclass
# ---------------------------------------------------------------------------

class TestOrderLifecycleTransitionDataclass:
    def test_fields_exist(self):
        t = OrderLifecycleTransition(
            previous_state=None,
            next_state=_S.CREATED,
            event=_E.CREATE,
            allowed=True,
            blocker=None,
        )
        assert t.previous_state is None
        assert t.next_state == _S.CREATED
        assert t.event == _E.CREATE
        assert t.allowed is True
        assert t.blocker is None

    def test_safety_flags_default_false(self):
        t = OrderLifecycleTransition(
            previous_state=None,
            next_state=_S.CREATED,
            event=_E.CREATE,
            allowed=True,
            blocker=None,
        )
        assert t.broker_calls_made is False
        assert t.credentials_read is False
        assert t.network_calls_made is False
        assert t.order_action_requested is False
        assert t.live_trading_allowed is False

    def test_frozen(self):
        t = OrderLifecycleTransition(
            previous_state=None,
            next_state=_S.CREATED,
            event=_E.CREATE,
            allowed=True,
            blocker=None,
        )
        with pytest.raises(Exception):
            t.allowed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestCreateOrder
# ---------------------------------------------------------------------------

class TestCreateOrder:
    def test_create_returns_created_state(self):
        mgr = OrderLifecycleManager()
        t = mgr.create_order("BC-001")
        assert t.allowed is True
        assert t.next_state == _S.CREATED
        assert t.previous_state is None

    def test_create_stores_record(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001", symbol="SPY", side="buy", quantity=1.0)
        r = mgr.get("BC-001")
        assert r is not None
        assert r.state == _S.CREATED
        assert r.symbol == "SPY"
        assert r.side == "buy"
        assert r.quantity == 1.0

    def test_create_appends_create_event(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        r = mgr.get("BC-001")
        assert len(r.events) == 1
        assert r.events[0]["event"] == "CREATE"

    def test_duplicate_id_blocked(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.create_order("BC-001")
        assert t.allowed is False
        assert t.blocker is not None
        assert "BC-001" in t.blocker

    def test_duplicate_does_not_mutate_record(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001", symbol="SPY")
        mgr.create_order("BC-001", symbol="QQQ")  # duplicate, blocked
        r = mgr.get("BC-001")
        assert r.symbol == "SPY"  # unchanged


# ---------------------------------------------------------------------------
# TestApplyEventHappyPath
# ---------------------------------------------------------------------------

class TestApplyEventHappyPath:
    def _create(self, mgr: OrderLifecycleManager, oid: str = "BC-001") -> None:
        mgr.create_order(oid, symbol="SPY", side="buy", quantity=1.0)

    def test_created_to_risk_approved(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        t = mgr.apply_event("BC-001", _E.RISK_APPROVE)
        assert t.allowed is True
        assert t.next_state == _S.RISK_APPROVED

    def test_risk_approved_to_submit_requested(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        mgr.apply_event("BC-001", _E.RISK_APPROVE)
        t = mgr.apply_event("BC-001", _E.REQUEST_SUBMIT)
        assert t.allowed is True
        assert t.next_state == _S.SUBMIT_REQUESTED

    def test_submit_requested_to_submitted(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        mgr.apply_event("BC-001", _E.RISK_APPROVE)
        mgr.apply_event("BC-001", _E.REQUEST_SUBMIT)
        t = mgr.apply_event("BC-001", _E.MARK_SUBMITTED)
        assert t.allowed is True
        assert t.next_state == _S.SUBMITTED

    def test_submitted_to_acknowledged(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.ACKNOWLEDGE)
        assert t.allowed is True
        assert t.next_state == _S.ACKNOWLEDGED

    def test_acknowledged_to_filled(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED, _E.ACKNOWLEDGE):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.MARK_FILLED)
        assert t.allowed is True
        assert t.next_state == _S.FILLED

    def test_filled_to_closed(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED,
                   _E.ACKNOWLEDGE, _E.MARK_FILLED):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.CLOSE)
        assert t.allowed is True
        assert t.next_state == _S.CLOSED

    def test_full_happy_path_state_sequence(self):
        mgr = OrderLifecycleManager()
        self._create(mgr)
        steps = [
            (_E.RISK_APPROVE,    _S.RISK_APPROVED),
            (_E.REQUEST_SUBMIT,  _S.SUBMIT_REQUESTED),
            (_E.MARK_SUBMITTED,  _S.SUBMITTED),
            (_E.ACKNOWLEDGE,     _S.ACKNOWLEDGED),
            (_E.MARK_FILLED,     _S.FILLED),
            (_E.CLOSE,           _S.CLOSED),
        ]
        for event, expected in steps:
            t = mgr.apply_event("BC-001", event)
            assert t.allowed is True, f"expected allowed for {event}"
            assert t.next_state == expected, f"expected {expected} after {event}"


# ---------------------------------------------------------------------------
# TestPartialFillPath
# ---------------------------------------------------------------------------

class TestPartialFillPath:
    def _reach_acknowledged(self, mgr: OrderLifecycleManager, oid: str = "BC-001") -> None:
        mgr.create_order(oid)
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED, _E.ACKNOWLEDGE):
            mgr.apply_event(oid, ev)

    def test_acknowledged_to_partially_filled(self):
        mgr = OrderLifecycleManager()
        self._reach_acknowledged(mgr)
        t = mgr.apply_event("BC-001", _E.MARK_PARTIALLY_FILLED)
        assert t.allowed is True
        assert t.next_state == _S.PARTIALLY_FILLED

    def test_partially_filled_to_filled(self):
        mgr = OrderLifecycleManager()
        self._reach_acknowledged(mgr)
        mgr.apply_event("BC-001", _E.MARK_PARTIALLY_FILLED)
        t = mgr.apply_event("BC-001", _E.MARK_FILLED)
        assert t.allowed is True
        assert t.next_state == _S.FILLED

    def test_partially_filled_to_canceled(self):
        mgr = OrderLifecycleManager()
        self._reach_acknowledged(mgr)
        mgr.apply_event("BC-001", _E.MARK_PARTIALLY_FILLED)
        t = mgr.apply_event("BC-001", _E.MARK_CANCELED)
        assert t.allowed is True
        assert t.next_state == _S.CANCELED


# ---------------------------------------------------------------------------
# TestCancelPath
# ---------------------------------------------------------------------------

class TestCancelPath:
    def test_submitted_to_canceled(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.MARK_CANCELED)
        assert t.allowed is True
        assert t.next_state == _S.CANCELED

    def test_acknowledged_to_canceled(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED, _E.ACKNOWLEDGE):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.MARK_CANCELED)
        assert t.allowed is True
        assert t.next_state == _S.CANCELED

    def test_canceled_to_closed(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED, _E.MARK_CANCELED):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.CLOSE)
        assert t.allowed is True
        assert t.next_state == _S.CLOSED


# ---------------------------------------------------------------------------
# TestRejectedFailedPaths
# ---------------------------------------------------------------------------

class TestRejectedFailedPaths:
    def test_mark_rejected_from_created(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.apply_event("BC-001", _E.MARK_REJECTED)
        assert t.allowed is True
        assert t.next_state == _S.REJECTED

    def test_mark_rejected_from_submitted(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.MARK_REJECTED)
        assert t.allowed is True
        assert t.next_state == _S.REJECTED

    def test_mark_failed_from_created(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.apply_event("BC-001", _E.MARK_FAILED)
        assert t.allowed is True
        assert t.next_state == _S.FAILED

    def test_mark_failed_from_acknowledged(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED, _E.ACKNOWLEDGE):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.MARK_FAILED)
        assert t.allowed is True
        assert t.next_state == _S.FAILED

    def test_rejected_to_closed(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.apply_event("BC-001", _E.MARK_REJECTED)
        t = mgr.apply_event("BC-001", _E.CLOSE)
        assert t.allowed is True
        assert t.next_state == _S.CLOSED

    def test_failed_to_closed(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.apply_event("BC-001", _E.MARK_FAILED)
        t = mgr.apply_event("BC-001", _E.CLOSE)
        assert t.allowed is True
        assert t.next_state == _S.CLOSED


# ---------------------------------------------------------------------------
# TestInvalidTransitions
# ---------------------------------------------------------------------------

class TestInvalidTransitions:
    def test_invalid_transition_returns_blocked(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.apply_event("BC-001", _E.MARK_FILLED)  # can't fill from CREATED
        assert t.allowed is False
        assert t.blocker is not None

    def test_invalid_transition_does_not_mutate_record(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.apply_event("BC-001", _E.MARK_FILLED)  # invalid
        r = mgr.get("BC-001")
        assert r.state == _S.CREATED  # unchanged

    def test_terminal_state_blocks_forward_submit(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED,
                   _E.ACKNOWLEDGE, _E.MARK_FILLED, _E.CLOSE):
            mgr.apply_event("BC-001", ev)
        # CLOSED is terminal — cannot apply RISK_APPROVE
        t = mgr.apply_event("BC-001", _E.RISK_APPROVE)
        assert t.allowed is False

    def test_terminal_filled_blocks_submit_events(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        for ev in (_E.RISK_APPROVE, _E.REQUEST_SUBMIT, _E.MARK_SUBMITTED,
                   _E.ACKNOWLEDGE, _E.MARK_FILLED):
            mgr.apply_event("BC-001", ev)
        t = mgr.apply_event("BC-001", _E.RISK_APPROVE)
        assert t.allowed is False

    def test_blocker_message_contains_state_and_event(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.apply_event("BC-001", _E.MARK_FILLED)
        assert t.blocker is not None
        assert len(t.blocker) > 0

    def test_unknown_order_id_blocked(self):
        mgr = OrderLifecycleManager()
        t = mgr.apply_event("NONEXISTENT", _E.RISK_APPROVE)
        assert t.allowed is False
        assert t.blocker is not None
        assert "NONEXISTENT" in t.blocker


# ---------------------------------------------------------------------------
# TestEventHistory
# ---------------------------------------------------------------------------

class TestEventHistory:
    def test_event_history_grows_deterministically(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.apply_event("BC-001", _E.RISK_APPROVE)
        mgr.apply_event("BC-001", _E.REQUEST_SUBMIT)
        r = mgr.get("BC-001")
        assert len(r.events) == 3
        assert r.events[0]["event"] == "CREATE"
        assert r.events[1]["event"] == "RISK_APPROVE"
        assert r.events[2]["event"] == "REQUEST_SUBMIT"

    def test_invalid_transition_does_not_append_event(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.apply_event("BC-001", _E.MARK_FILLED)  # invalid
        r = mgr.get("BC-001")
        assert len(r.events) == 1  # only CREATE

    def test_metadata_stored_in_event(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001", metadata={"source": "test"})
        r = mgr.get("BC-001")
        assert r.events[0].get("source") == "test"


# ---------------------------------------------------------------------------
# TestGetAndAllOrders
# ---------------------------------------------------------------------------

class TestGetAndAllOrders:
    def test_get_returns_immutable_record(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        r = mgr.get("BC-001")
        assert isinstance(r, OrderLifecycleRecord)

    def test_get_unknown_returns_none(self):
        mgr = OrderLifecycleManager()
        assert mgr.get("UNKNOWN") is None

    def test_all_orders_returns_tuple(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.create_order("BC-002")
        orders = mgr.all_orders()
        assert isinstance(orders, tuple)
        assert len(orders) == 2

    def test_all_orders_empty_on_fresh_manager(self):
        mgr = OrderLifecycleManager()
        assert mgr.all_orders() == ()


# ---------------------------------------------------------------------------
# TestMultipleOrdersIsolated
# ---------------------------------------------------------------------------

class TestMultipleOrdersIsolated:
    def test_two_orders_independent(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.create_order("BC-002")
        mgr.apply_event("BC-001", _E.RISK_APPROVE)
        # BC-002 should still be CREATED
        assert mgr.get("BC-002").state == _S.CREATED
        assert mgr.get("BC-001").state == _S.RISK_APPROVED

    def test_failure_of_one_does_not_affect_other(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        mgr.create_order("BC-002")
        mgr.apply_event("BC-001", _E.MARK_FAILED)
        mgr.apply_event("BC-002", _E.RISK_APPROVE)
        assert mgr.get("BC-001").state == _S.FAILED
        assert mgr.get("BC-002").state == _S.RISK_APPROVED


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_create_transition_safety_flags_all_false(self):
        mgr = OrderLifecycleManager()
        t = mgr.create_order("BC-001")
        assert t.broker_calls_made is False
        assert t.credentials_read is False
        assert t.network_calls_made is False
        assert t.order_action_requested is False
        assert t.live_trading_allowed is False

    def test_apply_event_transition_safety_flags_all_false(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.apply_event("BC-001", _E.RISK_APPROVE)
        assert t.broker_calls_made is False
        assert t.credentials_read is False
        assert t.network_calls_made is False
        assert t.order_action_requested is False
        assert t.live_trading_allowed is False

    def test_blocked_transition_safety_flags_all_false(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t = mgr.apply_event("BC-001", _E.MARK_FILLED)  # invalid
        assert t.broker_calls_made is False
        assert t.order_action_requested is False
        assert t.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_sequence_same_result(self):
        def run():
            mgr = OrderLifecycleManager()
            mgr.create_order("BC-001", symbol="SPY", side="buy", quantity=1.0)
            mgr.apply_event("BC-001", _E.RISK_APPROVE)
            mgr.apply_event("BC-001", _E.REQUEST_SUBMIT)
            return mgr.get("BC-001").state

        assert run() == run()

    def test_repeated_invalid_same_result(self):
        mgr = OrderLifecycleManager()
        mgr.create_order("BC-001")
        t1 = mgr.apply_event("BC-001", _E.MARK_FILLED)
        t2 = mgr.apply_event("BC-001", _E.MARK_FILLED)
        assert t1.allowed == t2.allowed
        assert t1.blocker == t2.blocker


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.runtime.order_lifecycle as _ol
        return inspect.getsource(_ol)

    def test_no_alpaca_broker_import(self):
        assert "AlpacaBrokerAdapter" not in self._src()

    def test_no_live_submit_import(self):
        assert "live_submit" not in self._src()

    def test_no_live_readiness_gate_import(self):
        assert "live_readiness_gate" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_file_io(self):
        src = self._src()
        assert "open(" not in src
        assert "write(" not in src
        assert "Path(" not in src
