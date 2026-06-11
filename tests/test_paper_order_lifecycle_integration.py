"""
tests/test_paper_order_lifecycle_integration.py
------------------------------------------------
S33: Integration tests for the pure offline chain

    S30 create_paper_order_plan()
    -> S27 validate_paper_order_plan()  (standalone)
    -> S28 evaluate_paper_order_safety_gate()
    -> S32 create_lifecycle_from_plan()
    -> S32 apply_lifecycle_event()

Every scenario uses the REAL planner, the REAL S27 validator, the REAL S28
gate, and the REAL S32 lifecycle functions. No mocked validators or mocked
lifecycle functions are used anywhere in this module.

The chain helper proves fail-closed sequencing: a blocked planner releases
no plan and stops the chain; a non-PASS S27 validation stops the chain; a
gate result other than PASS_DRY_RUN_ONLY prevents lifecycle creation and
advancement; the SAFETY_GATE_PASSED lifecycle event is only ever applied
after the real gate returned PASS_DRY_RUN_ONLY.

All fixtures are plain in-memory dicts. No real approval artifact, order
plan, lifecycle record, config, or any other artifact is created. No files
are read or written. No broker, network, credential, environment-variable,
or order access is made.

Lifecycle integration is pure offline/in-memory bookkeeping only.
Lifecycle transitions are not order actions. A planner-generated POP/1.0
plan is an in-memory paper order plan only -- it is not an order. S28
PASS_DRY_RUN_ONLY remains only offline clearance for a future
dry-run/no-submit rendering step, not order approval. Paper trading
remains not approved; live trading remains blocked.
"""

from __future__ import annotations

import builtins
import copy
import inspect
from typing import NamedTuple

import pytest

import src.research.paper_approval_validator as _approval_mod
import src.research.paper_order_lifecycle as _lifecycle_mod
import src.research.paper_order_plan_validator as _plan_mod
import src.research.paper_order_planner as _planner_mod
import src.research.paper_order_safety_gate as _gate_mod
from src.research.paper_order_lifecycle import (
    PaperOrderLifecycleEventType,
    PaperOrderLifecycleState,
    PaperOrderLifecycleStatus,
    PaperOrderLifecycleTransitionResult,
    apply_lifecycle_event,
    create_lifecycle_from_plan,
)
from src.research.paper_order_plan_validator import validate_paper_order_plan
from src.research.paper_order_planner import (
    PaperOrderPlannerResult,
    PaperOrderPlannerStatus,
    create_paper_order_plan,
)
from src.research.paper_order_safety_gate import (
    PaperOrderSafetyGateStatus,
    evaluate_paper_order_safety_gate,
)

# ---------------------------------------------------------------------------
# Fixture helpers (plain in-memory dicts; no files; no artifacts)
# ---------------------------------------------------------------------------

_GENERATED_AT = "2025-01-15T09:00:00Z"
_EXPIRES_AT = "2025-01-15T17:00:00Z"
_PLAN_ID = "plan-001"
_SOURCE_SHA = "abc123def456abc123def456abc123def456abc1"
_LIFECYCLE_ID = "lc-001"
_CREATED_AT = "2025-01-15T09:05:00Z"
_EVENT_AT = "2025-01-15T09:10:00Z"

Status = PaperOrderLifecycleStatus
Event = PaperOrderLifecycleEventType


def _valid_approval(**overrides) -> dict:
    """Return a complete valid PTA/1.0 approval artifact dict."""
    base = {
        "artifact_schema_version": "PTA/1.0",
        "approval_artifact_type": "PAPER_TRADING_APPROVAL",
        "approval_scope": "PAPER_TRADING_LIMITED_RUN_ONLY",
        "candidate_id": "cand-001",
        "run_id": "run-001",
        "source_git_sha": _SOURCE_SHA,
        "paper_config_schema_version": "PC/1.0",
        "paper_config_hash": "hash-pc-abc001",
        "simulation_result_hash": "hash-sim-abc001",
        "architecture_review_reference": "docs/paper_trading_architecture_design.md#S22",
        "invariant_test_reference": "tests/test_paper_architecture_invariants.py#S23",
        "approved_by": "reviewer-001",
        "approved_at_utc": "2025-01-01T09:00:00Z",
        "expires_at_utc": "2025-12-31T23:59:59Z",
        "max_notional_per_position": 5000.0,
        "max_position_fraction": 0.05,
        "max_daily_loss": 2000.0,
        "max_drawdown_stop": 0.50,
        "max_orders_per_day": 5,
        "allowed_symbols": ["AAPL"],
        "allowed_intervals": ["60m"],
        "allowed_strategy_families": ["TREND_BREAKOUT"],
        "allowed_order_types": ["market", "limit"],
        "allowed_session": "regular",
        "paper_account_label": "alpaca-paper-primary",
        "live_trading_approved": False,
        "live_order_submission_approved": False,
        "dry_run_required": True,
        "human_confirmation_required": True,
        "kill_switch_required": True,
        "approval_status": "APPROVED_FOR_LIMITED_PAPER_RUN",
        "notes": "approved for limited run",
        "known_limitations": "limited sample size",
        "artifact_hash": "hash-pta-abc001",
    }
    base.update(overrides)
    return base


def _valid_signal(**overrides) -> dict:
    """Return a valid signal snapshot dict consistent with _valid_approval."""
    base = {
        "symbol": "AAPL",
        "interval": "60m",
        "strategy_family": "TREND_BREAKOUT",
        "side": "BUY",
        "order_type": "market",
        "confidence": 0.85,
        "rationale": "momentum breakout signal detected on 60m bar",
        "holding_horizon": "intraday",
    }
    base.update(overrides)
    return base


def _valid_sizing(**overrides) -> dict:
    """Return a valid sizing snapshot dict within all approval caps."""
    base = {
        "quantity": 10.0,
        "notional": 1500.0,
        "limit_price": None,
        "max_position_fraction": 0.05,
        "max_daily_loss": 500.0,
        "max_drawdown_stop": 0.15,
        "max_orders_per_day": 3,
        "max_notional_per_position": 5000.0,
    }
    base.update(overrides)
    return base


def _clean_state(**overrides) -> dict:
    """Return a clean, valid current_state dict for the S28 gate."""
    base = {
        "kill_switch_open": True,
        "current_daily_order_count": 0,
        "current_daily_pnl": 0.0,
        "current_drawdown": 0.0,
        "open_positions": [],
        "processed_plan_ids": [],
    }
    base.update(overrides)
    return base


class ChainResults(NamedTuple):
    planner_result: object
    validation_result: object
    gate_result: object
    lifecycle_creation: object
    lifecycle_after_gate_event: object


def _run_full_chain(
    approval=None,
    signal=None,
    sizing=None,
    state=None,
    *,
    apply_gate_event: bool = True,
    **planner_kwargs,
) -> ChainResults:
    """Run the full real chain with fail-closed sequencing.

    1. create_paper_order_plan(); if no plan is released, stop.
    2. validate_paper_order_plan() standalone; if not PASS, stop.
    3. evaluate_paper_order_safety_gate(); if not PASS_DRY_RUN_ONLY, stop
       and never create a lifecycle.
    4. create_lifecycle_from_plan(); if it passes and apply_gate_event is
       True, apply SAFETY_GATE_PASSED -- only ever after the real gate
       returned PASS_DRY_RUN_ONLY.
    """
    if approval is None:
        approval = _valid_approval()
    if signal is None:
        signal = _valid_signal()
    if sizing is None:
        sizing = _valid_sizing()
    if state is None:
        state = _clean_state()
    planner_kwargs.setdefault("generated_at_utc", _GENERATED_AT)
    planner_kwargs.setdefault("expires_at_utc", _EXPIRES_AT)
    planner_kwargs.setdefault("plan_id", _PLAN_ID)
    planner_kwargs.setdefault("source_git_sha", _SOURCE_SHA)

    planner_result = create_paper_order_plan(
        approval, signal_snapshot=signal, sizing_snapshot=sizing, **planner_kwargs
    )
    if planner_result.plan is None:
        return ChainResults(planner_result, None, None, None, None)

    validation_result = validate_paper_order_plan(planner_result.plan)
    if validation_result.result != "PASS":
        return ChainResults(planner_result, validation_result, None, None, None)

    gate_result = evaluate_paper_order_safety_gate(
        approval, planner_result.plan, current_state=state
    )
    if gate_result.gate_status is not PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY:
        return ChainResults(planner_result, validation_result, gate_result, None, None)

    lifecycle_creation = create_lifecycle_from_plan(
        planner_result.plan, lifecycle_id=_LIFECYCLE_ID, created_at_utc=_CREATED_AT
    )
    if lifecycle_creation.result != "PASS" or not apply_gate_event:
        return ChainResults(
            planner_result, validation_result, gate_result, lifecycle_creation, None
        )

    lifecycle_after_gate_event = apply_lifecycle_event(
        lifecycle_creation.state,
        event_type=Event.SAFETY_GATE_PASSED,
        event_at_utc=_EVENT_AT,
    )
    return ChainResults(
        planner_result, validation_result, gate_result,
        lifecycle_creation, lifecycle_after_gate_event,
    )


def _apply(state, event_type, details=None, at=_EVENT_AT):
    return apply_lifecycle_event(
        state, event_type=event_type, event_at_utc=at, details=details
    )


def _lifecycle_at_pending() -> PaperOrderLifecycleState:
    """Walk a full real chain to a PAPER_ORDER_PENDING lifecycle state."""
    chain = _run_full_chain()
    state = chain.lifecycle_after_gate_event.state
    state = _apply(state, Event.DRY_RUN_RENDERED).state
    return _apply(state, Event.PAPER_ORDER_MARKED_PENDING).state


def _limit_signal(**overrides) -> dict:
    return _valid_signal(order_type="limit", **overrides)


def _limit_sizing(**overrides) -> dict:
    return _valid_sizing(limit_price=150.0, **overrides)


_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)


# ---------------------------------------------------------------------------
# 1, 12, 13: full chain PASS with a market order plan
# ---------------------------------------------------------------------------

class TestFullChainMarketPass:
    def test_all_five_stages_pass(self):
        chain = _run_full_chain()
        assert chain.planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert chain.validation_result.result == "PASS"
        assert (
            chain.gate_result.gate_status
            is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        )
        assert chain.lifecycle_creation.result == "PASS"
        assert chain.lifecycle_after_gate_event.result == "PASS"

    def test_lifecycle_created_as_planned(self):
        chain = _run_full_chain(apply_gate_event=False)
        state = chain.lifecycle_creation.state
        assert state.status is Status.PLANNED
        assert len(state.events) == 1

    def test_plan_created_event_records_not_started_to_planned(self):
        chain = _run_full_chain(apply_gate_event=False)
        event = chain.lifecycle_creation.state.events[0]
        assert event["event_type"] == "PLAN_CREATED"
        assert event["from_status"] == "NOT_STARTED"
        assert event["to_status"] == "PLANNED"

    def test_gate_event_transitions_to_gate_passed_dry_run_only(self):
        chain = _run_full_chain()
        result = chain.lifecycle_after_gate_event
        assert result.previous_status is Status.PLANNED
        assert result.new_status is Status.GATE_PASSED_DRY_RUN_ONLY
        assert result.state.status is Status.GATE_PASSED_DRY_RUN_ONLY
        event = result.state.events[-1]
        assert event["event_type"] == "SAFETY_GATE_PASSED"
        assert event["from_status"] == "PLANNED"
        assert event["to_status"] == "GATE_PASSED_DRY_RUN_ONLY"

    def test_gate_event_only_applied_after_real_gate_pass(self):
        # The helper applies SAFETY_GATE_PASSED only after the real S28 gate
        # returned PASS_DRY_RUN_ONLY: when the gate blocks, no lifecycle is
        # created and no lifecycle event is applied.
        blocked = _run_full_chain(state=_clean_state(kill_switch_open=False))
        assert (
            blocked.gate_result.gate_status
            is PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH
        )
        assert blocked.lifecycle_creation is None
        assert blocked.lifecycle_after_gate_event is None
        passed = _run_full_chain()
        assert (
            passed.gate_result.gate_status
            is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        )
        assert passed.lifecycle_after_gate_event.result == "PASS"


# ---------------------------------------------------------------------------
# 2: full chain PASS with a limit order plan
# ---------------------------------------------------------------------------

class TestFullChainLimitPass:
    def test_limit_chain_passes_all_five_stages(self):
        chain = _run_full_chain(signal=_limit_signal(), sizing=_limit_sizing())
        assert chain.planner_result.plan["order_type"] == "limit"
        assert chain.planner_result.plan["limit_price"] == 150.0
        assert chain.validation_result.result == "PASS"
        assert (
            chain.gate_result.gate_status
            is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        )
        assert chain.lifecycle_creation.state.order_type == "limit"
        assert (
            chain.lifecycle_after_gate_event.state.status
            is Status.GATE_PASSED_DRY_RUN_ONLY
        )


# ---------------------------------------------------------------------------
# 3: full bookkeeping chain to PAPER_ORDER_FILLED
# ---------------------------------------------------------------------------

class TestFullBookkeepingChain:
    def test_planned_to_filled_via_lifecycle_events_only(self):
        chain = _run_full_chain()
        state = chain.lifecycle_after_gate_event.state
        assert state.status is Status.GATE_PASSED_DRY_RUN_ONLY
        state = _apply(state, Event.DRY_RUN_RENDERED).state
        assert state.status is Status.DRY_RUN_RENDERED
        state = _apply(state, Event.PAPER_ORDER_MARKED_PENDING).state
        assert state.status is Status.PAPER_ORDER_PENDING
        result = _apply(
            state, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": 150.25},
        )
        assert result.result == "PASS"
        final = result.state
        assert final.status is Status.PAPER_ORDER_FILLED
        assert final.filled_quantity == 10.0
        assert final.average_fill_price == 150.25
        assert [e["event_type"] for e in final.events] == [
            "PLAN_CREATED", "SAFETY_GATE_PASSED", "DRY_RUN_RENDERED",
            "PAPER_ORDER_MARKED_PENDING", "PAPER_ORDER_MARKED_FILLED",
        ]


# ---------------------------------------------------------------------------
# 4-6: planner blocks stop the chain before S27/S28/lifecycle
# ---------------------------------------------------------------------------

class TestPlannerBlocksStopChain:
    @pytest.mark.parametrize(
        ("kwargs", "expected_status"),
        [
            (
                {"approval": "BLOCKED_APPROVAL_SENTINEL"},
                PaperOrderPlannerStatus.BLOCKED_APPROVAL,
            ),
            (
                {"signal": "BLOCKED_SIGNAL_SENTINEL"},
                PaperOrderPlannerStatus.BLOCKED_SIGNAL,
            ),
            (
                {"sizing": "BLOCKED_SIZING_SENTINEL"},
                PaperOrderPlannerStatus.BLOCKED_SIZING,
            ),
        ],
        ids=["approval", "signal", "sizing"],
    )
    def test_planner_block_stops_everything_downstream(self, kwargs, expected_status):
        resolved = {
            "approval": _valid_approval(approval_status="NOT_REVIEWED"),
            "signal": _valid_signal(symbol="TSLA"),
            "sizing": _valid_sizing(notional=6000.0),
        }
        chain_kwargs = {
            key: resolved[key] for key in kwargs
        }
        chain = _run_full_chain(**chain_kwargs)
        assert chain.planner_result.planner_status is expected_status
        assert chain.planner_result.plan is None
        assert chain.validation_result is None
        assert chain.gate_result is None
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate_event is None


# ---------------------------------------------------------------------------
# 7-10: gate blocks prevent lifecycle creation and advancement
# ---------------------------------------------------------------------------

class TestGateBlocksPreventLifecycle:
    @pytest.mark.parametrize(
        ("state_overrides", "expected_gate_status"),
        [
            (
                {"kill_switch_open": False},
                PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH,
            ),
            (
                {"current_daily_order_count": 5},
                PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT,
            ),
            (
                {"processed_plan_ids": [_PLAN_ID]},
                PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE,
            ),
            (
                {"open_positions": [
                    {"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}
                ]},
                PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT,
            ),
        ],
        ids=["kill_switch", "daily_count_at_cap", "duplicate_plan_id", "open_position"],
    )
    def test_gate_block_prevents_lifecycle(self, state_overrides, expected_gate_status):
        chain = _run_full_chain(state=_clean_state(**state_overrides))
        # Upstream stages passed: the block comes only from the gate.
        assert chain.planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert chain.validation_result.result == "PASS"
        assert chain.gate_result.gate_status is expected_gate_status
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate_event is None


# ---------------------------------------------------------------------------
# 11: lifecycle preserves planner-generated plan identity
# ---------------------------------------------------------------------------

class TestLifecyclePreservesPlanIdentity:
    def test_identity_fields_preserved(self):
        chain = _run_full_chain(apply_gate_event=False)
        plan = chain.planner_result.plan
        state = chain.lifecycle_creation.state
        assert state.lifecycle_id == _LIFECYCLE_ID
        assert state.plan_id == plan["plan_id"] == _PLAN_ID
        assert state.candidate_id == plan["candidate_id"] == "cand-001"
        assert state.run_id == plan["run_id"] == "run-001"
        assert state.symbol == plan["symbol"] == "AAPL"
        assert state.side == plan["side"] == "BUY"
        assert state.order_type == plan["order_type"] == "market"
        assert state.current_quantity == plan["quantity"] == 10.0

    def test_identity_preserved_with_overridden_approval(self):
        approval = _valid_approval(candidate_id="cand-777", run_id="run-777")
        chain = _run_full_chain(approval=approval, apply_gate_event=False)
        state = chain.lifecycle_creation.state
        assert state.candidate_id == "cand-777"
        assert state.run_id == "run-777"


# ---------------------------------------------------------------------------
# 14-17: lifecycle sequencing enforced in the integrated chain
# ---------------------------------------------------------------------------

class TestLifecycleSequencingEnforced:
    def test_dry_run_before_gate_event_blocked(self):
        chain = _run_full_chain(apply_gate_event=False)
        planned = chain.lifecycle_creation.state
        result = _apply(planned, Event.DRY_RUN_RENDERED)
        assert result.result == "BLOCKED"
        assert "transition.allowed" in result.criteria_failed
        assert result.state is planned
        assert result.state.status is Status.PLANNED

    def test_pending_before_dry_run_blocked(self):
        chain = _run_full_chain()
        gate_passed = chain.lifecycle_after_gate_event.state
        result = _apply(gate_passed, Event.PAPER_ORDER_MARKED_PENDING)
        assert result.result == "BLOCKED"
        assert "transition.allowed" in result.criteria_failed
        assert result.state.status is Status.GATE_PASSED_DRY_RUN_ONLY

    def test_fill_before_pending_blocked(self):
        chain = _run_full_chain()
        rendered = _apply(
            chain.lifecycle_after_gate_event.state, Event.DRY_RUN_RENDERED
        ).state
        result = _apply(
            rendered, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0},
        )
        assert result.result == "BLOCKED"
        assert "transition.allowed" in result.criteria_failed
        assert result.state.status is Status.DRY_RUN_RENDERED

    def test_terminal_filled_rejects_further_events(self):
        pending = _lifecycle_at_pending()
        filled = _apply(
            pending, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0},
        ).state
        assert filled.status is Status.PAPER_ORDER_FILLED
        for event in (
            Event.SAFETY_GATE_PASSED, Event.PAPER_ORDER_MARKED_CANCELLED,
            Event.BLOCKED_BY_SAFETY, Event.ERROR_RECORDED,
        ):
            result = _apply(filled, event)
            assert result.result == "BLOCKED"
            assert "terminal" in result.blocker
            assert result.state is filled


# ---------------------------------------------------------------------------
# 18-19: BLOCKED_BY_SAFETY / ERROR_RECORDED are bookkeeping only
# ---------------------------------------------------------------------------

class TestBlockedAndErrorBookkeeping:
    def test_blocked_by_safety_records_blocked_state(self):
        chain = _run_full_chain()
        result = _apply(
            chain.lifecycle_after_gate_event.state, Event.BLOCKED_BY_SAFETY,
            details={"reason": "kill switch engaged during review"},
        )
        assert result.result == "PASS"
        assert result.state.status is Status.BLOCKED
        assert result.state.blocker == "kill switch engaged during review"
        # Bookkeeping only, not an order action: every safety flag stays
        # False on both the recorded state and the transition result.
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(result, flag) is False
            assert getattr(result.state, flag) is False

    def test_error_recorded_records_error_state(self):
        chain = _run_full_chain()
        result = _apply(chain.lifecycle_after_gate_event.state, Event.ERROR_RECORDED)
        assert result.result == "PASS"
        assert result.state.status is Status.ERROR
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(result, flag) is False
            assert getattr(result.state, flag) is False


# ---------------------------------------------------------------------------
# 20: fill details validated in the full chain
# ---------------------------------------------------------------------------

class TestFillValidationInChain:
    def test_valid_fill_passes(self):
        pending = _lifecycle_at_pending()
        result = _apply(
            pending, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": 151.0},
        )
        assert result.result == "PASS"
        assert result.state.filled_quantity == 10.0

    def test_fill_above_current_quantity_blocked(self):
        pending = _lifecycle_at_pending()
        result = _apply(
            pending, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 11.0},  # plan quantity is 10.0
        )
        assert result.result == "BLOCKED"
        assert "fill.details" in result.criteria_failed
        assert result.state.status is Status.PAPER_ORDER_PENDING

    def test_invalid_average_fill_price_blocked(self):
        pending = _lifecycle_at_pending()
        result = _apply(
            pending, Event.PAPER_ORDER_MARKED_FILLED,
            details={"filled_quantity": 10.0, "average_fill_price": -1.0},
        )
        assert result.result == "BLOCKED"
        assert "fill.details" in result.criteria_failed


# ---------------------------------------------------------------------------
# 21: forbidden lifecycle details blocked in integration
# ---------------------------------------------------------------------------

class TestForbiddenDetailsInChain:
    @pytest.mark.parametrize(
        "phrase",
        [
            "submit_" + "order",
            "api_key",
            "secret",
            "token",
            "http",
            "endpoint",
        ],
        ids=["order_verb", "api_key", "secret", "token", "http", "endpoint"],
    )
    def test_forbidden_phrase_blocks_and_preserves_state(self, phrase):
        chain = _run_full_chain()
        gate_passed = chain.lifecycle_after_gate_event.state
        result = _apply(
            gate_passed, Event.DRY_RUN_RENDERED,
            details={"note": f"would use {phrase} next"},
        )
        assert result.result == "BLOCKED"
        assert "details.forbidden_content" in result.criteria_failed
        assert result.state is gate_passed
        assert result.new_status is Status.GATE_PASSED_DRY_RUN_ONLY
        assert len(result.state.events) == len(gate_passed.events)


# ---------------------------------------------------------------------------
# 22-25: safety flags always False at every stage
# ---------------------------------------------------------------------------

def _scenario_chains():
    """Full-chain results for PASS and every blocked scenario family."""
    return [
        ("pass_market", _run_full_chain()),
        ("pass_limit", _run_full_chain(signal=_limit_signal(), sizing=_limit_sizing())),
        (
            "planner_blocked",
            _run_full_chain(approval=_valid_approval(approval_status="DRAFT")),
        ),
        (
            "gate_blocked_kill_switch",
            _run_full_chain(state=_clean_state(kill_switch_open=False)),
        ),
        (
            "gate_blocked_duplicate",
            _run_full_chain(state=_clean_state(processed_plan_ids=[_PLAN_ID])),
        ),
    ]


class TestSafetyFlagsAlwaysFalse:
    def test_planner_flags_false(self):
        for label, chain in _scenario_chains():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.planner_result, flag) is False, (
                    f"planner {flag} should be False in {label}"
                )

    def test_validation_flags_false_when_run(self):
        for label, chain in _scenario_chains():
            if chain.validation_result is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.validation_result, flag) is False, (
                    f"validator {flag} should be False in {label}"
                )

    def test_gate_flags_false_when_run(self):
        for label, chain in _scenario_chains():
            if chain.gate_result is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.gate_result, flag) is False, (
                    f"gate {flag} should be False in {label}"
                )

    def test_lifecycle_flags_false_when_run(self):
        for label, chain in _scenario_chains():
            for lifecycle_result in (
                chain.lifecycle_creation, chain.lifecycle_after_gate_event
            ):
                if lifecycle_result is None:
                    continue
                for flag in _SAFETY_FLAG_NAMES:
                    assert getattr(lifecycle_result, flag) is False, (
                        f"lifecycle {flag} should be False in {label}"
                    )
                    assert getattr(lifecycle_result.state, flag) is False, (
                        f"lifecycle state {flag} should be False in {label}"
                    )

    def test_blocked_lifecycle_event_flags_false(self):
        chain = _run_full_chain()
        gate_passed = chain.lifecycle_after_gate_event.state
        blocked = _apply(gate_passed, Event.PAPER_ORDER_MARKED_FILLED)
        assert blocked.result == "BLOCKED"
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(blocked, flag) is False


# ---------------------------------------------------------------------------
# 26: inputs not mutated across the full chain
# ---------------------------------------------------------------------------

class TestInputsNotMutated:
    def test_full_pass_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state()
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_full_chain(approval=approval, signal=signal, sizing=sizing, state=state)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]

    def test_lifecycle_state_not_mutated_by_further_events(self):
        chain = _run_full_chain()
        gate_passed = chain.lifecycle_after_gate_event.state
        events_snapshot = copy.deepcopy(gate_passed.events)
        _apply(gate_passed, Event.DRY_RUN_RENDERED)
        _apply(gate_passed, Event.BLOCKED_BY_SAFETY)
        assert gate_passed.status is Status.GATE_PASSED_DRY_RUN_ONLY
        assert gate_passed.events == events_snapshot

    def test_blocked_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state(kill_switch_open=False)
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_full_chain(approval=approval, signal=signal, sizing=sizing, state=state)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]


# ---------------------------------------------------------------------------
# 27: determinism across the full chain
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_results_at_every_stage(self):
        first = _run_full_chain()
        second = _run_full_chain()
        assert first.planner_result == second.planner_result
        assert first.validation_result == second.validation_result
        assert first.gate_result == second.gate_result
        assert first.lifecycle_creation == second.lifecycle_creation
        assert first.lifecycle_after_gate_event == second.lifecycle_after_gate_event

    def test_same_blocked_inputs_same_results(self):
        first = _run_full_chain(state=_clean_state(kill_switch_open=False))
        second = _run_full_chain(state=_clean_state(kill_switch_open=False))
        assert first.gate_result == second.gate_result
        assert first.lifecycle_creation is None and second.lifecycle_creation is None


# ---------------------------------------------------------------------------
# 28: deep-copy isolation from caller snapshots
# ---------------------------------------------------------------------------

class TestDeepCopyIsolation:
    def test_mutating_originals_does_not_mutate_lifecycle_state(self):
        signal = _valid_signal()
        sizing = _valid_sizing()
        chain = _run_full_chain(signal=signal, sizing=sizing)
        signal["symbol"] = "TSLA"
        signal["side"] = "SELL"
        sizing["quantity"] = 999.0
        state = chain.lifecycle_after_gate_event.state
        assert state.symbol == "AAPL"
        assert state.side == "BUY"
        assert state.current_quantity == 10.0


# ---------------------------------------------------------------------------
# 29: no real artifacts created
# ---------------------------------------------------------------------------

class TestNoArtifactsCreated:
    def test_full_chain_never_opens_a_file_at_runtime(self, monkeypatch):
        # Record every call to the builtin file constructor during a full
        # PASS chain (through a lifecycle fill) and a gate-blocked chain;
        # a pure offline chain must make none.
        calls: list = []
        _real_ctor = builtins.open

        def _recording_ctor(*args, **kwargs):
            calls.append(args)
            return _real_ctor(*args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(builtins, "open", _recording_ctor)
            pending = _lifecycle_at_pending()
            filled = _apply(
                pending, Event.PAPER_ORDER_MARKED_FILLED,
                details={"filled_quantity": 10.0},
            )
            blocked = _run_full_chain(state=_clean_state(kill_switch_open=False))
        assert calls == []
        assert filled.state.status is Status.PAPER_ORDER_FILLED
        assert blocked.lifecycle_creation is None


# ---------------------------------------------------------------------------
# 31: the source modules in the chain are clean
# ---------------------------------------------------------------------------

_CHAIN_MODULES = [
    _planner_mod, _plan_mod, _gate_mod, _lifecycle_mod, _approval_mod,
]
_CHAIN_MODULE_IDS = [
    "s30_planner", "s27_plan_validator", "s28_safety_gate",
    "s32_lifecycle", "s25_approval_validator",
]


class TestChainModuleSourcesClean:
    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_file_io(self, module):
        source = inspect.getsource(module)
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
            "read_by" + "tes(",
            "write_by" + "tes(",
        ):
            assert pattern not in source, (
                f"forbidden file I/O {pattern!r} in {module.__name__}"
            )

    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_forbidden_imports(self, module):
        source = inspect.getsource(module)
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
        ):
            assert pattern not in source, (
                f"forbidden import {pattern!r} in {module.__name__}"
            )

    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_broker_env_or_order_calls(self, module):
        # Patterns target actual usage forms only: the S25/S27 validators
        # and the S32 lifecycle legitimately list bare scan words inside
        # their own forbidden-substring scan lists.
        source = inspect.getsource(module)
        for pattern in (
            "Alpaca" + "Broker" + "Adapter",
            "os.envi" + "ron[",
            "os.envi" + "ron.get(",
            "getenv" + "(",
            "." + "submit_or" + "der(",
            "." + "place_or" + "der(",
            "." + "cancel_or" + "der(",
            "." + "modify_or" + "der(",
            "live_su" + "bmit(",
        ):
            assert pattern not in source, (
                f"forbidden call {pattern!r} in {module.__name__}"
            )

    @pytest.mark.parametrize("module", _CHAIN_MODULES, ids=_CHAIN_MODULE_IDS)
    def test_no_runtime_or_execution_imports(self, module):
        source = inspect.getsource(module)
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
        ):
            assert pattern not in source, (
                f"forbidden module import {pattern!r} in {module.__name__}"
            )


# ---------------------------------------------------------------------------
# 30: this test module itself is clean
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
