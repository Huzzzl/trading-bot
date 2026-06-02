"""
tests/test_runtime_state_machine.py
-------------------------------------
Tests for src/runtime/state_machine.py (PR A2-1).

All tests use fake/injected runners — no real Alpaca calls, no real paper runners.
No live gates unfrozen. No live trading. No broker/API/credential access.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest

from src.config.loader import load_config
from src.runtime.state_machine import (
    AutomatedRuntimeStateMachine,
    RuntimeAction,
    RuntimeDecision,
    RuntimeState,
    RuntimeStepResult,
    _NO_RISK_GATE_BLOCKER,
    _extract_safety_flags,
)


# ---------------------------------------------------------------------------
# Fake runner result objects (no real execution)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeBuyResult:
    result: str = "PREVIEW_COMPLETE"
    order_action_requested: bool = False
    credentials_read: bool = False
    network_calls_made: bool = False
    broker_calls_made: bool = False


@dataclass(frozen=True)
class _FakeCloseResult:
    result: str = "PREVIEW_COMPLETE"
    order_action_requested: bool = False
    credentials_read: bool = False
    network_calls_made: bool = False
    broker_calls_made: bool = False


def _fake_buy_runner(cfg):
    return _FakeBuyResult()


def _fake_close_runner(cfg):
    return _FakeCloseResult()


def _approving_risk_gate():
    return True


def _rejecting_risk_gate():
    return False


@pytest.fixture
def base_cfg():
    return load_config(None)


# ---------------------------------------------------------------------------
# TestRuntimeStateEnum
# ---------------------------------------------------------------------------

class TestRuntimeStateEnum:
    def test_all_states_exist(self):
        values = {s.value for s in RuntimeState}
        for expected in ("IDLE", "CHECKING_RISK", "PAPER_PREVIEW", "PAPER_SUBMIT_READY",
                         "PAPER_SUBMITTED", "PAPER_CLOSE_PREVIEW", "PAPER_CLOSED",
                         "BLOCKED", "ERROR"):
            assert expected in values

    def test_str_enum(self):
        assert RuntimeState.IDLE == "IDLE"
        assert RuntimeState.BLOCKED == "BLOCKED"
        assert RuntimeState.ERROR == "ERROR"


# ---------------------------------------------------------------------------
# TestRuntimeActionEnum
# ---------------------------------------------------------------------------

class TestRuntimeActionEnum:
    def test_all_actions_exist(self):
        values = {a.value for a in RuntimeAction}
        for expected in ("NONE", "PREVIEW_BUY", "SUBMIT_BUY", "PREVIEW_CLOSE", "SUBMIT_CLOSE"):
            assert expected in values

    def test_str_enum(self):
        assert RuntimeAction.NONE == "NONE"
        assert RuntimeAction.SUBMIT_BUY == "SUBMIT_BUY"


# ---------------------------------------------------------------------------
# TestRuntimeDecisionDataclass
# ---------------------------------------------------------------------------

class TestRuntimeDecisionDataclass:
    def test_fields_exist(self):
        d = RuntimeDecision(
            state=RuntimeState.IDLE,
            action=RuntimeAction.NONE,
            blocker=None,
            reason="test",
        )
        assert d.state == RuntimeState.IDLE
        assert d.action == RuntimeAction.NONE
        assert d.blocker is None
        assert d.reason == "test"

    def test_frozen(self):
        d = RuntimeDecision(
            state=RuntimeState.IDLE,
            action=RuntimeAction.NONE,
            blocker=None,
            reason="x",
        )
        with pytest.raises(Exception):
            d.state = RuntimeState.BLOCKED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestRuntimeStepResultDataclass
# ---------------------------------------------------------------------------

class TestRuntimeStepResultDataclass:
    def test_fields_exist(self):
        r = RuntimeStepResult(
            previous_state=RuntimeState.IDLE,
            next_state=RuntimeState.PAPER_PREVIEW,
            action=RuntimeAction.PREVIEW_BUY,
            blocker=None,
            paper_result=None,
            safety_flags={},
        )
        assert r.previous_state == RuntimeState.IDLE
        assert r.next_state == RuntimeState.PAPER_PREVIEW
        assert r.action == RuntimeAction.PREVIEW_BUY
        assert r.blocker is None
        assert r.paper_result is None
        assert r.safety_flags == {}

    def test_frozen(self):
        r = RuntimeStepResult(
            previous_state=RuntimeState.IDLE,
            next_state=RuntimeState.IDLE,
            action=RuntimeAction.NONE,
            blocker=None,
            paper_result=None,
            safety_flags={},
        )
        with pytest.raises(Exception):
            r.blocker = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestDefaultFailClosed — no risk gate injected
# ---------------------------------------------------------------------------

class TestDefaultFailClosed:
    def test_submit_buy_blocked_without_risk_gate(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(
            paper_buy_runner=_fake_buy_runner,
            paper_close_runner=_fake_close_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED
        assert result.blocker == _NO_RISK_GATE_BLOCKER
        assert result.paper_result is None

    def test_submit_close_blocked_without_risk_gate(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(
            paper_buy_runner=_fake_buy_runner,
            paper_close_runner=_fake_close_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_CLOSE, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED
        assert result.blocker == _NO_RISK_GATE_BLOCKER
        assert result.paper_result is None

    def test_submit_buy_state_is_blocked_after(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert sm.state == RuntimeState.BLOCKED

    def test_submit_close_state_is_blocked_after(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_close_runner=_fake_close_runner)
        sm.step(RuntimeAction.SUBMIT_CLOSE, base_cfg)
        assert sm.state == RuntimeState.BLOCKED

    def test_no_risk_gate_blocker_mentions_automated_gate(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert "automated risk gate" in result.blocker


# ---------------------------------------------------------------------------
# TestRiskGateRejection
# ---------------------------------------------------------------------------

class TestRiskGateRejection:
    def test_rejected_buy_does_not_call_runner(self, base_cfg):
        called: list = []

        def spy_runner(cfg):
            called.append(cfg)
            return _FakeBuyResult()

        sm = AutomatedRuntimeStateMachine(
            risk_gate=_rejecting_risk_gate,
            paper_buy_runner=spy_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED
        assert called == []

    def test_rejected_close_does_not_call_runner(self, base_cfg):
        called: list = []

        def spy_runner(cfg):
            called.append(cfg)
            return _FakeCloseResult()

        sm = AutomatedRuntimeStateMachine(
            risk_gate=_rejecting_risk_gate,
            paper_close_runner=spy_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_CLOSE, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED
        assert called == []

    def test_rejection_sets_non_empty_blocker(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(
            risk_gate=_rejecting_risk_gate,
            paper_buy_runner=_fake_buy_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert result.blocker is not None
        assert len(result.blocker) > 0


# ---------------------------------------------------------------------------
# TestPreviewBuyAction
# ---------------------------------------------------------------------------

class TestPreviewBuyAction:
    def test_preview_buy_calls_runner(self, base_cfg):
        called: list = []

        def spy_runner(cfg):
            called.append(cfg)
            return _FakeBuyResult()

        sm = AutomatedRuntimeStateMachine(paper_buy_runner=spy_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert len(called) == 1
        assert result.next_state == RuntimeState.PAPER_PREVIEW

    def test_preview_buy_no_order_action_requested(self, base_cfg):
        fake_result = _FakeBuyResult(order_action_requested=False)
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=lambda cfg: fake_result)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.safety_flags.get("order_action_requested") is False

    def test_preview_buy_sets_preview_flag_on_copy(self, base_cfg):
        seen: list = []

        def spy_runner(cfg):
            seen.append(cfg.execution.paper_preview_only)
            return _FakeBuyResult()

        sm = AutomatedRuntimeStateMachine(paper_buy_runner=spy_runner)
        sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert seen[0] is True

    def test_preview_buy_does_not_require_risk_gate(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.next_state == RuntimeState.PAPER_PREVIEW
        assert result.blocker is None

    def test_preview_buy_records_previous_state(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.previous_state == RuntimeState.IDLE


# ---------------------------------------------------------------------------
# TestSubmitBuyAction
# ---------------------------------------------------------------------------

class TestSubmitBuyAction:
    def test_submit_buy_approved_calls_runner(self, base_cfg):
        called: list = []

        def spy_runner(cfg):
            called.append(cfg)
            return _FakeBuyResult(result="SUBMIT_COMPLETE", order_action_requested=True)

        sm = AutomatedRuntimeStateMachine(
            risk_gate=_approving_risk_gate,
            paper_buy_runner=spy_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert len(called) == 1
        assert result.next_state == RuntimeState.PAPER_SUBMITTED

    def test_submit_buy_sets_preview_false_on_copy(self, base_cfg):
        seen: list = []

        def spy_runner(cfg):
            seen.append(cfg.execution.paper_preview_only)
            return _FakeBuyResult()

        sm = AutomatedRuntimeStateMachine(
            risk_gate=_approving_risk_gate,
            paper_buy_runner=spy_runner,
        )
        sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert seen[0] is False

    def test_submit_buy_blocked_without_approval(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(
            risk_gate=_rejecting_risk_gate,
            paper_buy_runner=_fake_buy_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED


# ---------------------------------------------------------------------------
# TestPreviewCloseAction
# ---------------------------------------------------------------------------

class TestPreviewCloseAction:
    def test_preview_close_calls_runner(self, base_cfg):
        called: list = []

        def spy_runner(cfg):
            called.append(cfg)
            return _FakeCloseResult()

        sm = AutomatedRuntimeStateMachine(paper_close_runner=spy_runner)
        result = sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert len(called) == 1
        assert result.next_state == RuntimeState.PAPER_CLOSE_PREVIEW

    def test_preview_close_no_order_action_requested(self, base_cfg):
        fake_result = _FakeCloseResult(order_action_requested=False)
        sm = AutomatedRuntimeStateMachine(paper_close_runner=lambda cfg: fake_result)
        result = sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert result.safety_flags.get("order_action_requested") is False

    def test_preview_close_sets_preview_flag_on_copy(self, base_cfg):
        seen: list = []

        def spy_runner(cfg):
            seen.append(cfg.execution.paper_close_preview_only)
            return _FakeCloseResult()

        sm = AutomatedRuntimeStateMachine(paper_close_runner=spy_runner)
        sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert seen[0] is True

    def test_preview_close_does_not_require_risk_gate(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_close_runner=_fake_close_runner)
        result = sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert result.next_state == RuntimeState.PAPER_CLOSE_PREVIEW
        assert result.blocker is None


# ---------------------------------------------------------------------------
# TestSubmitCloseAction
# ---------------------------------------------------------------------------

class TestSubmitCloseAction:
    def test_submit_close_approved_calls_runner(self, base_cfg):
        called: list = []

        def spy_runner(cfg):
            called.append(cfg)
            return _FakeCloseResult(result="SUBMIT_COMPLETE", order_action_requested=True)

        sm = AutomatedRuntimeStateMachine(
            risk_gate=_approving_risk_gate,
            paper_close_runner=spy_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_CLOSE, base_cfg)
        assert len(called) == 1
        assert result.next_state == RuntimeState.PAPER_CLOSED

    def test_submit_close_sets_preview_false_on_copy(self, base_cfg):
        seen: list = []

        def spy_runner(cfg):
            seen.append(cfg.execution.paper_close_preview_only)
            return _FakeCloseResult()

        sm = AutomatedRuntimeStateMachine(
            risk_gate=_approving_risk_gate,
            paper_close_runner=spy_runner,
        )
        sm.step(RuntimeAction.SUBMIT_CLOSE, base_cfg)
        assert seen[0] is False

    def test_submit_close_blocked_without_approval(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(
            risk_gate=_rejecting_risk_gate,
            paper_close_runner=_fake_close_runner,
        )
        result = sm.step(RuntimeAction.SUBMIT_CLOSE, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED


# ---------------------------------------------------------------------------
# TestRunnerException
# ---------------------------------------------------------------------------

class TestRunnerException:
    def test_buy_runner_exception_moves_to_error(self, base_cfg):
        def exploding_runner(cfg):
            raise RuntimeError("runner exploded")

        sm = AutomatedRuntimeStateMachine(paper_buy_runner=exploding_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.next_state == RuntimeState.ERROR
        assert sm.state == RuntimeState.ERROR

    def test_exception_blocker_contains_message(self, base_cfg):
        def exploding_runner(cfg):
            raise RuntimeError("something went wrong")

        sm = AutomatedRuntimeStateMachine(paper_buy_runner=exploding_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.blocker is not None
        assert "something went wrong" in result.blocker

    def test_exception_paper_result_and_flags_empty(self, base_cfg):
        def exploding_runner(cfg):
            raise ValueError("bad value")

        sm = AutomatedRuntimeStateMachine(paper_buy_runner=exploding_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.paper_result is None
        assert result.safety_flags == {}

    def test_close_runner_exception_moves_to_error(self, base_cfg):
        def exploding_runner(cfg):
            raise RuntimeError("close exploded")

        sm = AutomatedRuntimeStateMachine(paper_close_runner=exploding_runner)
        result = sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert result.next_state == RuntimeState.ERROR


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_all_false_flags_from_fake_buy_runner(self, base_cfg):
        fake_result = _FakeBuyResult(
            order_action_requested=False,
            credentials_read=False,
            network_calls_made=False,
            broker_calls_made=False,
        )
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=lambda cfg: fake_result)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.safety_flags["order_action_requested"] is False
        assert result.safety_flags["credentials_read"] is False
        assert result.safety_flags["network_calls_made"] is False
        assert result.safety_flags["broker_calls_made"] is False

    def test_all_false_flags_from_fake_close_runner(self, base_cfg):
        fake_result = _FakeCloseResult(
            order_action_requested=False,
            credentials_read=False,
            network_calls_made=False,
            broker_calls_made=False,
        )
        sm = AutomatedRuntimeStateMachine(paper_close_runner=lambda cfg: fake_result)
        result = sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert result.safety_flags["order_action_requested"] is False
        assert result.safety_flags["credentials_read"] is False

    def test_no_safety_flags_when_blocked(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        result = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert result.next_state == RuntimeState.BLOCKED
        assert result.safety_flags == {}

    def test_no_order_action_in_preview_mode(self, base_cfg):
        fake_result = _FakeBuyResult(order_action_requested=False)
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=lambda cfg: fake_result)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.safety_flags.get("order_action_requested") is False


# ---------------------------------------------------------------------------
# TestConfigImmutability
# ---------------------------------------------------------------------------

class TestConfigImmutability:
    def test_original_config_not_mutated_by_preview_buy(self, base_cfg):
        original = base_cfg.execution.paper_preview_only
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert base_cfg.execution.paper_preview_only == original

    def test_original_config_not_mutated_by_submit_buy(self, base_cfg):
        original = base_cfg.execution.paper_preview_only
        sm = AutomatedRuntimeStateMachine(
            risk_gate=_approving_risk_gate,
            paper_buy_runner=_fake_buy_runner,
        )
        sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert base_cfg.execution.paper_preview_only == original

    def test_original_config_not_mutated_by_preview_close(self, base_cfg):
        original = base_cfg.execution.paper_close_preview_only
        sm = AutomatedRuntimeStateMachine(paper_close_runner=_fake_close_runner)
        sm.step(RuntimeAction.PREVIEW_CLOSE, base_cfg)
        assert base_cfg.execution.paper_close_preview_only == original

    def test_runner_sees_preview_true_regardless_of_original(self, base_cfg):
        base_cfg.execution.paper_preview_only = False
        seen: list = []

        def spy(cfg):
            seen.append(cfg.execution.paper_preview_only)
            return _FakeBuyResult()

        sm = AutomatedRuntimeStateMachine(paper_buy_runner=spy)
        sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert seen[0] is True


# ---------------------------------------------------------------------------
# TestStateTransitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    def test_initial_state_is_idle(self):
        sm = AutomatedRuntimeStateMachine()
        assert sm.state == RuntimeState.IDLE

    def test_state_store_none_accepted(self):
        sm = AutomatedRuntimeStateMachine(state_store=None)
        assert sm.state == RuntimeState.IDLE

    def test_state_store_object_accepted(self):
        sm = AutomatedRuntimeStateMachine(state_store={"persisted": True})
        assert sm.state == RuntimeState.IDLE

    def test_none_action_does_not_change_state(self, base_cfg):
        sm = AutomatedRuntimeStateMachine()
        result = sm.step(RuntimeAction.NONE, base_cfg)
        assert result.next_state == RuntimeState.IDLE
        assert sm.state == RuntimeState.IDLE

    def test_transitions_are_deterministic(self, base_cfg):
        sm1 = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        sm2 = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        r1 = sm1.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        r2 = sm2.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert r1.next_state == r2.next_state
        assert r1.action == r2.action

    def test_previous_state_recorded_in_result(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(paper_buy_runner=_fake_buy_runner)
        result = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert result.previous_state == RuntimeState.IDLE

    def test_multi_step_state_tracking(self, base_cfg):
        sm = AutomatedRuntimeStateMachine(
            risk_gate=_approving_risk_gate,
            paper_buy_runner=_fake_buy_runner,
            paper_close_runner=_fake_close_runner,
        )
        r1 = sm.step(RuntimeAction.PREVIEW_BUY, base_cfg)
        assert r1.next_state == RuntimeState.PAPER_PREVIEW
        r2 = sm.step(RuntimeAction.SUBMIT_BUY, base_cfg)
        assert r2.previous_state == RuntimeState.PAPER_PREVIEW
        assert r2.next_state == RuntimeState.PAPER_SUBMITTED


# ---------------------------------------------------------------------------
# TestNoLiveGateImports
# ---------------------------------------------------------------------------

class TestNoLiveGateImports:
    def _src(self) -> str:
        import src.runtime.state_machine as _sm
        return inspect.getsource(_sm)

    def test_no_live_submit_import(self):
        assert "live_submit" not in self._src()

    def test_no_live_readiness_gate_import(self):
        assert "live_readiness_gate" not in self._src()

    def test_no_manual_checklist_import(self):
        assert "manual_checklist" not in self._src()

    def test_no_alpaca_direct_import(self):
        src = self._src()
        assert "AlpacaBrokerAdapter" not in src
        assert "AlpacaLiveSubmitBroker" not in src


# ---------------------------------------------------------------------------
# TestExtractSafetyFlags
# ---------------------------------------------------------------------------

class TestExtractSafetyFlags:
    def test_extract_all_flags_true(self):
        result = _FakeBuyResult(
            order_action_requested=True,
            credentials_read=True,
            network_calls_made=True,
            broker_calls_made=True,
        )
        flags = _extract_safety_flags(result)
        assert flags["order_action_requested"] is True
        assert flags["credentials_read"] is True
        assert flags["network_calls_made"] is True
        assert flags["broker_calls_made"] is True

    def test_extract_from_none_returns_empty(self):
        assert _extract_safety_flags(None) == {}

    def test_extract_partial_attributes(self):
        class _Partial:
            order_action_requested = False

        flags = _extract_safety_flags(_Partial())
        assert flags["order_action_requested"] is False
        assert "credentials_read" not in flags
