"""
tests/test_paper_dry_run_preview_integration.py
------------------------------------------------
S37: Integration tests for the full pure offline preview chain

    S30 create_paper_order_plan()
    -> S27 validate_paper_order_plan()
    -> S28 evaluate_paper_order_safety_gate()
    -> S32 create_lifecycle_from_plan()
    -> S32 apply_lifecycle_event(SAFETY_GATE_PASSED)
    -> S36 render_paper_dry_run_preview()

Every scenario uses the REAL planner, the REAL S27 validator, the REAL S28
gate, the REAL S32 lifecycle functions, and the REAL S36 preview renderer.
No mocked validators, gate, lifecycle, or preview functions are used for
core integration tests.

The _run_chain_to_preview() helper proves fail-closed sequencing: the
preview is rendered only after planner PASS, S27 PASS, S28
PASS_DRY_RUN_ONLY, lifecycle PLANNED creation, and the lifecycle
SAFETY_GATE_PASSED event. No preview is rendered when the planner,
validation, gate, or lifecycle stage blocks.

All fixtures are plain in-memory dicts. No real approval artifact, order
plan, lifecycle record, preview artifact, config, or any other artifact is
created. No files are read or written. No broker, network, credential,
environment-variable, or order access is made.

The rendered preview is display-only. It is not an order, not a broker
payload, never order approval, and never advances the lifecycle by itself.
S28 PASS_DRY_RUN_ONLY remains only offline clearance for a future
dry-run/no-submit rendering step. Paper trading remains not approved; live
trading remains blocked.
"""

from __future__ import annotations

import builtins
import copy
import inspect
from typing import NamedTuple

import pytest

import src.research.paper_approval_validator as _approval_mod
import src.research.paper_dry_run_preview as _preview_mod
import src.research.paper_order_lifecycle as _lifecycle_mod
import src.research.paper_order_plan_validator as _plan_mod
import src.research.paper_order_planner as _planner_mod
import src.research.paper_order_safety_gate as _gate_mod
from src.research.paper_dry_run_preview import (
    PaperDryRunPreviewStatus,
    render_paper_dry_run_preview,
)
from src.research.paper_order_lifecycle import (
    PaperOrderLifecycleEventType,
    PaperOrderLifecycleState,
    PaperOrderLifecycleStatus,
    apply_lifecycle_event,
    create_lifecycle_from_plan,
)
from src.research.paper_order_plan_validator import validate_paper_order_plan
from src.research.paper_order_planner import (
    PaperOrderPlannerStatus,
    create_paper_order_plan,
)
from src.research.paper_order_safety_gate import (
    PaperOrderSafetyGateResult,
    PaperOrderSafetyGateStatus,
    evaluate_paper_order_safety_gate,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GENERATED_AT = "2025-01-15T09:00:00Z"
_EXPIRES_AT   = "2025-01-15T17:00:00Z"
_PLAN_ID      = "plan-001"
_SOURCE_SHA   = "abc123def456abc123def456abc123def456abc1"
_LIFECYCLE_ID = "lc-001"
_PREVIEW_ID   = "preview-001"
_CREATED_AT   = "2025-01-15T09:05:00Z"
_EVENT_AT     = "2025-01-15T09:10:00Z"
_RENDERED_AT  = "2025-01-15T09:20:00Z"

_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

PreviewStatus = PaperDryRunPreviewStatus
LcStatus = PaperOrderLifecycleStatus
LcEvent = PaperOrderLifecycleEventType


# ---------------------------------------------------------------------------
# Fixture helpers (plain in-memory dicts; no files; no artifacts)
# ---------------------------------------------------------------------------

def _valid_approval(**overrides) -> dict:
    base = {
        "artifact_schema_version":        "PTA/1.0",
        "approval_artifact_type":         "PAPER_TRADING_APPROVAL",
        "approval_scope":                 "PAPER_TRADING_LIMITED_RUN_ONLY",
        "candidate_id":                   "cand-001",
        "run_id":                         "run-001",
        "source_git_sha":                 _SOURCE_SHA,
        "paper_config_schema_version":    "PC/1.0",
        "paper_config_hash":              "hash-pc-abc001",
        "simulation_result_hash":         "hash-sim-abc001",
        "architecture_review_reference":  "docs/paper_trading_architecture_design.md#S22",
        "invariant_test_reference":       "tests/test_paper_architecture_invariants.py#S23",
        "approved_by":                    "reviewer-001",
        "approved_at_utc":                "2025-01-01T09:00:00Z",
        "expires_at_utc":                 "2025-12-31T23:59:59Z",
        "max_notional_per_position":       5000.0,
        "max_position_fraction":           0.05,
        "max_daily_loss":                  2000.0,
        "max_drawdown_stop":               0.50,
        "max_orders_per_day":              5,
        "allowed_symbols":                ["AAPL"],
        "allowed_intervals":              ["60m"],
        "allowed_strategy_families":      ["TREND_BREAKOUT"],
        "allowed_order_types":            ["market", "limit"],
        "allowed_session":                "regular",
        "paper_account_label":            "alpaca-paper-primary",
        "live_trading_approved":           False,
        "live_order_submission_approved":  False,
        "dry_run_required":                True,
        "human_confirmation_required":     True,
        "kill_switch_required":            True,
        "approval_status":                "APPROVED_FOR_LIMITED_PAPER_RUN",
        "notes":                          "approved for limited run",
        "known_limitations":              "limited sample size",
        "artifact_hash":                  "hash-pta-abc001",
    }
    base.update(overrides)
    return base


def _valid_signal(**overrides) -> dict:
    base = {
        "symbol":           "AAPL",
        "interval":         "60m",
        "strategy_family":  "TREND_BREAKOUT",
        "side":             "BUY",
        "order_type":       "market",
        "confidence":       0.85,
        "rationale":        "momentum breakout signal detected on 60m bar",
        "holding_horizon":  "intraday",
    }
    base.update(overrides)
    return base


def _valid_sizing(**overrides) -> dict:
    base = {
        "quantity":                  10.0,
        "notional":                  1500.0,
        "limit_price":               None,
        "max_position_fraction":     0.05,
        "max_daily_loss":            500.0,
        "max_drawdown_stop":         0.15,
        "max_orders_per_day":        3,
        "max_notional_per_position": 5000.0,
    }
    base.update(overrides)
    return base


def _clean_state(**overrides) -> dict:
    base = {
        "kill_switch_open":          True,
        "current_daily_order_count": 0,
        "current_daily_pnl":         0.0,
        "current_drawdown":          0.0,
        "open_positions":            [],
        "processed_plan_ids":        [],
    }
    base.update(overrides)
    return base


def _gate_result_to_snapshot(gate_result: PaperOrderSafetyGateResult) -> dict:
    """Convert a real S28 gate result into the plain dict snapshot the S36
    renderer expects."""
    return {
        "result":                       gate_result.result,
        "gate_status":                  gate_result.gate_status.value,
        "plan_id":                      gate_result.plan_id,
        "candidate_id":                 gate_result.candidate_id,
        "run_id":                       gate_result.run_id,
        "dry_run_required":             gate_result.dry_run_required,
        "human_confirmation_required":  gate_result.human_confirmation_required,
        "kill_switch_required":         gate_result.kill_switch_required,
        "broker_calls_made":            gate_result.broker_calls_made,
        "credentials_read":             gate_result.credentials_read,
        "network_calls_made":           gate_result.network_calls_made,
        "order_action_requested":       gate_result.order_action_requested,
        "live_trading_allowed":         gate_result.live_trading_allowed,
    }


def _lifecycle_state_to_snapshot(state: PaperOrderLifecycleState) -> dict:
    """Convert a real S32 lifecycle state into the plain dict snapshot the
    S36 renderer expects."""
    return {
        "lifecycle_id":           state.lifecycle_id,
        "plan_id":                state.plan_id,
        "candidate_id":           state.candidate_id,
        "run_id":                 state.run_id,
        "status":                 state.status.value,
        "broker_calls_made":      state.broker_calls_made,
        "credentials_read":       state.credentials_read,
        "network_calls_made":     state.network_calls_made,
        "order_action_requested": state.order_action_requested,
        "live_trading_allowed":   state.live_trading_allowed,
    }


class PreviewChainResults(NamedTuple):
    planner_result:       object
    validation_result:    object
    gate_result:          object
    lifecycle_creation:   object
    lifecycle_after_gate: object
    preview_result:       object


def _run_chain_to_preview(
    approval=None,
    signal=None,
    sizing=None,
    state=None,
    **planner_kwargs,
) -> PreviewChainResults:
    """Run the full real chain to a rendered preview with fail-closed
    sequencing.

    1.  create_paper_order_plan(); if no plan is released, stop
        (preview_result=None).
    2.  validate_paper_order_plan(); if not PASS, stop.
    3.  evaluate_paper_order_safety_gate(); if not PASS_DRY_RUN_ONLY, stop
        and never create a lifecycle or preview.
    4.  create_lifecycle_from_plan(); apply SAFETY_GATE_PASSED -- only ever
        after the real gate returned PASS_DRY_RUN_ONLY.
    5.  Convert the real gate result and lifecycle state into plain dict
        snapshots and call render_paper_dry_run_preview().

    The preview is rendered only after planner PASS, S27 PASS, S28
    PASS_DRY_RUN_ONLY, lifecycle creation, and SAFETY_GATE_PASSED.
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
    planner_kwargs.setdefault("expires_at_utc",   _EXPIRES_AT)
    planner_kwargs.setdefault("plan_id",          _PLAN_ID)
    planner_kwargs.setdefault("source_git_sha",   _SOURCE_SHA)

    planner_result = create_paper_order_plan(
        approval, signal_snapshot=signal, sizing_snapshot=sizing, **planner_kwargs
    )
    if planner_result.plan is None:
        return PreviewChainResults(planner_result, None, None, None, None, None)

    validation_result = validate_paper_order_plan(planner_result.plan)
    if validation_result.result != "PASS":
        return PreviewChainResults(
            planner_result, validation_result, None, None, None, None
        )

    gate_result = evaluate_paper_order_safety_gate(
        approval, planner_result.plan, current_state=state
    )
    if gate_result.gate_status is not PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY:
        return PreviewChainResults(
            planner_result, validation_result, gate_result, None, None, None
        )

    lifecycle_creation = create_lifecycle_from_plan(
        planner_result.plan, lifecycle_id=_LIFECYCLE_ID, created_at_utc=_CREATED_AT
    )
    if lifecycle_creation.result != "PASS":
        return PreviewChainResults(
            planner_result, validation_result, gate_result,
            lifecycle_creation, None, None,
        )

    lifecycle_after_gate = apply_lifecycle_event(
        lifecycle_creation.state,
        event_type=LcEvent.SAFETY_GATE_PASSED,
        event_at_utc=_EVENT_AT,
    )
    if lifecycle_after_gate.result != "PASS":
        return PreviewChainResults(
            planner_result, validation_result, gate_result,
            lifecycle_creation, lifecycle_after_gate, None,
        )

    preview_result = render_paper_dry_run_preview(
        planner_result.plan,
        gate_snapshot=_gate_result_to_snapshot(gate_result),
        lifecycle_snapshot=_lifecycle_state_to_snapshot(lifecycle_after_gate.state),
        preview_id=_PREVIEW_ID,
        rendered_at_utc=_RENDERED_AT,
    )
    return PreviewChainResults(
        planner_result, validation_result, gate_result,
        lifecycle_creation, lifecycle_after_gate, preview_result,
    )


def _limit_signal(**overrides) -> dict:
    return _valid_signal(order_type="limit", **overrides)


def _limit_sizing(**overrides) -> dict:
    return _valid_sizing(limit_price=150.0, **overrides)


# ---------------------------------------------------------------------------
# 1-2: valid market and limit full chains render a preview
# ---------------------------------------------------------------------------

class TestFullChainRendersPreview:
    def test_market_chain_renders_preview(self):
        chain = _run_chain_to_preview()
        assert chain.planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert chain.validation_result.result == "PASS"
        assert (
            chain.gate_result.gate_status
            is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        )
        assert chain.lifecycle_after_gate.state.status is LcStatus.GATE_PASSED_DRY_RUN_ONLY
        assert chain.preview_result.result == "PASS"
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        assert chain.preview_result.preview is not None

    def test_limit_chain_renders_preview(self):
        chain = _run_chain_to_preview(signal=_limit_signal(), sizing=_limit_sizing())
        assert chain.preview_result.result == "PASS"
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        assert chain.preview_result.preview is not None


# ---------------------------------------------------------------------------
# 3-8: rendered preview contents
# ---------------------------------------------------------------------------

class TestPreviewContents:
    def test_market_preview_fixed_values(self):
        p = _run_chain_to_preview().preview_result.preview
        assert p["preview_schema_version"] == "PDRP/1.0"
        assert p["preview_type"] == "PAPER_DRY_RUN_NO_SUBMIT_PREVIEW"
        assert p["display_only"] is True
        assert p["no_submit"] is True
        assert p["broker_payload_created"] is False
        for flag in _SAFETY_FLAG_NAMES:
            assert p[flag] is False

    def test_limit_preview_preserves_limit_fields(self):
        chain = _run_chain_to_preview(signal=_limit_signal(), sizing=_limit_sizing())
        p = chain.preview_result.preview
        assert p["order_type"] == "limit"
        assert p["limit_price"] == 150.0
        assert p["limit_price"] > 0

    def test_preview_preserves_identity_and_intent_fields(self):
        chain = _run_chain_to_preview()
        plan = chain.planner_result.plan
        p = chain.preview_result.preview
        assert p["plan_id"] == plan["plan_id"] == _PLAN_ID
        assert p["candidate_id"] == plan["candidate_id"] == "cand-001"
        assert p["run_id"] == plan["run_id"] == "run-001"
        assert p["lifecycle_id"] == _LIFECYCLE_ID
        assert p["symbol"] == plan["symbol"] == "AAPL"
        assert p["side"] == plan["side"] == "BUY"
        assert p["quantity"] == plan["quantity"] == 10.0
        assert p["notional"] == plan["notional"] == 1500.0

    def test_preview_gate_status_is_pass_dry_run_only(self):
        p = _run_chain_to_preview().preview_result.preview
        assert p["gate_status"] == "PASS_DRY_RUN_ONLY"

    def test_preview_lifecycle_status_is_gate_passed_dry_run_only(self):
        p = _run_chain_to_preview().preview_result.preview
        assert p["lifecycle_status"] == "GATE_PASSED_DRY_RUN_ONLY"

    def test_preview_notes_say_display_only_not_an_order(self):
        notes = _run_chain_to_preview().preview_result.preview["notes"]
        assert "display-only" in notes
        assert "not an order" in notes
        assert "not a" in notes and "broker payload" in notes
        assert "cannot be submitted" in notes


# ---------------------------------------------------------------------------
# 9-11: planner blocks prevent everything downstream
# ---------------------------------------------------------------------------

class TestPlannerBlocksPreventPreview:
    @pytest.mark.parametrize(
        ("kwargs", "expected_status"),
        [
            (
                {"approval": _valid_approval(approval_status="NOT_REVIEWED")},
                PaperOrderPlannerStatus.BLOCKED_APPROVAL,
            ),
            (
                {"signal": _valid_signal(symbol="TSLA")},
                PaperOrderPlannerStatus.BLOCKED_SIGNAL,
            ),
            (
                {"sizing": _valid_sizing(notional=6000.0)},
                PaperOrderPlannerStatus.BLOCKED_SIZING,
            ),
        ],
        ids=["blocked_approval", "blocked_signal", "blocked_sizing"],
    )
    def test_planner_block_prevents_all_downstream(self, kwargs, expected_status):
        chain = _run_chain_to_preview(**kwargs)
        assert chain.planner_result.planner_status is expected_status
        assert chain.planner_result.plan is None
        assert chain.validation_result is None
        assert chain.gate_result is None
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None
        assert chain.preview_result is None


# ---------------------------------------------------------------------------
# 12-15: gate blocks prevent lifecycle and preview
# ---------------------------------------------------------------------------

class TestGateBlocksPreventPreview:
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
        ids=["kill_switch", "daily_count", "duplicate_plan_id", "open_position"],
    )
    def test_gate_block_prevents_lifecycle_and_preview(
        self, state_overrides, expected_gate_status
    ):
        chain = _run_chain_to_preview(state=_clean_state(**state_overrides))
        assert chain.planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert chain.validation_result.result == "PASS"
        assert chain.gate_result.gate_status is expected_gate_status
        assert chain.lifecycle_creation is None
        assert chain.lifecycle_after_gate is None
        assert chain.preview_result is None


# ---------------------------------------------------------------------------
# 16-19: manual snapshot tampering blocked by the renderer
# ---------------------------------------------------------------------------

class TestManualSnapshotTamperingBlocked:
    def _pass_chain(self) -> PreviewChainResults:
        return _run_chain_to_preview()

    def test_non_pass_gate_snapshot_blocked_as_blocked_gate(self):
        chain = self._pass_chain()
        gate_snapshot = _gate_result_to_snapshot(chain.gate_result)
        gate_snapshot["result"] = "BLOCKED"
        gate_snapshot["gate_status"] = "BLOCKED_KILL_SWITCH"
        result = render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=gate_snapshot,
            lifecycle_snapshot=_lifecycle_state_to_snapshot(
                chain.lifecycle_after_gate.state
            ),
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert result.result == "BLOCKED"
        assert result.preview_status is PreviewStatus.BLOCKED_GATE
        assert result.preview is None

    def test_lifecycle_before_gate_event_blocked_as_blocked_lifecycle(self):
        chain = self._pass_chain()
        # Snapshot of the PLANNED state (before SAFETY_GATE_PASSED).
        planned_snapshot = _lifecycle_state_to_snapshot(chain.lifecycle_creation.state)
        assert planned_snapshot["status"] == "PLANNED"
        result = render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=_gate_result_to_snapshot(chain.gate_result),
            lifecycle_snapshot=planned_snapshot,
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert result.result == "BLOCKED"
        assert result.preview_status is PreviewStatus.BLOCKED_LIFECYCLE
        assert result.preview is None

    def test_mismatched_lifecycle_plan_id_blocked_as_blocked_lifecycle(self):
        chain = self._pass_chain()
        lifecycle_snapshot = _lifecycle_state_to_snapshot(
            chain.lifecycle_after_gate.state
        )
        lifecycle_snapshot["plan_id"] = "other-plan"
        result = render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=_gate_result_to_snapshot(chain.gate_result),
            lifecycle_snapshot=lifecycle_snapshot,
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert result.result == "BLOCKED"
        assert result.preview_status is PreviewStatus.BLOCKED_LIFECYCLE
        assert result.preview is None

    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    @pytest.mark.parametrize("target", ["plan", "gate", "lifecycle"])
    def test_safety_flag_true_anywhere_blocked_as_blocked_safety(self, flag, target):
        chain = self._pass_chain()
        plan = copy.deepcopy(chain.planner_result.plan)
        gate_snapshot = _gate_result_to_snapshot(chain.gate_result)
        lifecycle_snapshot = _lifecycle_state_to_snapshot(
            chain.lifecycle_after_gate.state
        )
        {"plan": plan, "gate": gate_snapshot, "lifecycle": lifecycle_snapshot}[
            target
        ][flag] = True
        result = render_paper_dry_run_preview(
            plan,
            gate_snapshot=gate_snapshot,
            lifecycle_snapshot=lifecycle_snapshot,
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert result.result == "BLOCKED"
        assert result.preview_status is PreviewStatus.BLOCKED_SAFETY
        assert result.preview is None


# ---------------------------------------------------------------------------
# 20-21: preview only after SAFETY_GATE_PASSED
# ---------------------------------------------------------------------------

class TestPreviewOnlyAfterGateEvent:
    def test_no_preview_before_safety_gate_passed_event(self):
        chain = _run_chain_to_preview()
        # Rendering against the PLANNED lifecycle (before the gate event)
        # must block: the renderer requires GATE_PASSED_DRY_RUN_ONLY.
        result = render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=_gate_result_to_snapshot(chain.gate_result),
            lifecycle_snapshot=_lifecycle_state_to_snapshot(
                chain.lifecycle_creation.state
            ),
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert result.result == "BLOCKED"
        assert result.preview_status is PreviewStatus.BLOCKED_LIFECYCLE

    def test_preview_renders_after_safety_gate_passed_event(self):
        chain = _run_chain_to_preview()
        assert (
            chain.lifecycle_after_gate.state.status
            is LcStatus.GATE_PASSED_DRY_RUN_ONLY
        )
        assert chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED

    def test_rendered_preview_does_not_advance_lifecycle(self):
        chain = _run_chain_to_preview()
        # Rendering is display-only: the lifecycle state object is unchanged
        # after the preview was rendered.
        state = chain.lifecycle_after_gate.state
        assert state.status is LcStatus.GATE_PASSED_DRY_RUN_ONLY
        assert len(state.events) == 2  # PLAN_CREATED + SAFETY_GATE_PASSED only


# ---------------------------------------------------------------------------
# 22-23: preview contains no broker/credential/network fields or values
# ---------------------------------------------------------------------------

class TestPreviewNoForbiddenContent:
    # Phrases assembled from fragments; the five safety-flag keys (value
    # exactly False) are the only allowed keys touching these families.
    _FORBIDDEN_PHRASES = (
        "broker_" + "account",
        "live_" + "account",
        "end" + "point",
        "credential",
        "token",
        "api_" + "key",
        "secret",
        "submit_" + "order",
        "place_" + "order",
        "cancel_" + "order",
        "modify_" + "order",
        "live_" + "submit",
        "ht" + "tp",
    )

    def test_preview_keys_and_values_clean(self):
        p = _run_chain_to_preview().preview_result.preview
        for key, value in p.items():
            if key in _SAFETY_FLAG_NAMES:
                assert value is False
                continue
            key_lower = key.lower()
            for phrase in self._FORBIDDEN_PHRASES:
                assert phrase not in key_lower, (
                    f"forbidden phrase {phrase!r} in preview key {key!r}"
                )
            if isinstance(value, str):
                value_lower = value.lower()
                for phrase in self._FORBIDDEN_PHRASES:
                    assert phrase not in value_lower, (
                        f"forbidden phrase {phrase!r} in preview value of {key!r}"
                    )

    def test_preview_is_plain_dict(self):
        chain = _run_chain_to_preview()
        assert type(chain.preview_result.preview) is dict


# ---------------------------------------------------------------------------
# 24: inputs not mutated across the full chain
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
        _run_chain_to_preview(
            approval=approval, signal=signal, sizing=sizing, state=state
        )
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]

    def test_blocked_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state(kill_switch_open=False)
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_chain_to_preview(
            approval=approval, signal=signal, sizing=sizing, state=state
        )
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]

    def test_plan_not_mutated_by_render(self):
        chain = _run_chain_to_preview()
        plan_snapshot = copy.deepcopy(chain.planner_result.plan)
        render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=_gate_result_to_snapshot(chain.gate_result),
            lifecycle_snapshot=_lifecycle_state_to_snapshot(
                chain.lifecycle_after_gate.state
            ),
            preview_id="preview-002",
            rendered_at_utc=_RENDERED_AT,
        )
        assert chain.planner_result.plan == plan_snapshot


# ---------------------------------------------------------------------------
# 25: determinism across the full chain
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_results_at_every_stage(self):
        first = _run_chain_to_preview()
        second = _run_chain_to_preview()
        assert first.planner_result == second.planner_result
        assert first.validation_result == second.validation_result
        assert first.gate_result == second.gate_result
        assert first.lifecycle_creation == second.lifecycle_creation
        assert first.lifecycle_after_gate == second.lifecycle_after_gate
        assert first.preview_result == second.preview_result

    def test_same_blocked_inputs_same_results(self):
        first = _run_chain_to_preview(state=_clean_state(kill_switch_open=False))
        second = _run_chain_to_preview(state=_clean_state(kill_switch_open=False))
        assert first.gate_result == second.gate_result
        assert first.preview_result is None and second.preview_result is None


# ---------------------------------------------------------------------------
# 26-27: safety flags always False at every stage
# ---------------------------------------------------------------------------

def _scenario_chains():
    return [
        ("pass_market", _run_chain_to_preview()),
        ("pass_limit", _run_chain_to_preview(
            signal=_limit_signal(), sizing=_limit_sizing()
        )),
        (
            "planner_blocked",
            _run_chain_to_preview(approval=_valid_approval(approval_status="DRAFT")),
        ),
        (
            "gate_blocked_kill_switch",
            _run_chain_to_preview(state=_clean_state(kill_switch_open=False)),
        ),
        (
            "gate_blocked_duplicate",
            _run_chain_to_preview(state=_clean_state(processed_plan_ids=[_PLAN_ID])),
        ),
    ]


class TestSafetyFlagsAlwaysFalse:
    def test_upstream_flags_false_at_every_stage(self):
        for label, chain in _scenario_chains():
            for stage_result in (
                chain.planner_result, chain.validation_result, chain.gate_result,
                chain.lifecycle_creation, chain.lifecycle_after_gate,
            ):
                if stage_result is None:
                    continue
                for flag in _SAFETY_FLAG_NAMES:
                    assert getattr(stage_result, flag) is False, (
                        f"{flag} should be False in {label}"
                    )

    def test_preview_result_flags_false_when_rendered(self):
        for label, chain in _scenario_chains():
            if chain.preview_result is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(chain.preview_result, flag) is False, (
                    f"preview result {flag} should be False in {label}"
                )

    def test_blocked_preview_result_flags_false(self):
        chain = _run_chain_to_preview()
        gate_snapshot = _gate_result_to_snapshot(chain.gate_result)
        gate_snapshot["gate_status"] = "BLOCKED_KILL_SWITCH"
        blocked = render_paper_dry_run_preview(
            chain.planner_result.plan,
            gate_snapshot=gate_snapshot,
            lifecycle_snapshot=_lifecycle_state_to_snapshot(
                chain.lifecycle_after_gate.state
            ),
            preview_id=_PREVIEW_ID,
            rendered_at_utc=_RENDERED_AT,
        )
        assert blocked.result == "BLOCKED"
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(blocked, flag) is False


# ---------------------------------------------------------------------------
# 28: no real artifacts created
# ---------------------------------------------------------------------------

class TestNoArtifactsCreated:
    def test_full_chain_never_opens_a_file_at_runtime(self, monkeypatch):
        calls: list = []
        _real_ctor = builtins.open

        def _recording_ctor(*args, **kwargs):
            calls.append(args)
            return _real_ctor(*args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(builtins, "open", _recording_ctor)
            pass_chain = _run_chain_to_preview()
            blocked_chain = _run_chain_to_preview(
                state=_clean_state(kill_switch_open=False)
            )
        assert calls == [], f"unexpected file opens: {calls}"
        assert pass_chain.preview_result.preview_status is PreviewStatus.PREVIEW_RENDERED
        assert blocked_chain.preview_result is None


# ---------------------------------------------------------------------------
# 29: this test module itself is clean
# ---------------------------------------------------------------------------

class TestThisModuleClean:
    def _source(self) -> str:
        import sys
        return inspect.getsource(sys.modules[__name__])

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
        ):
            assert pattern not in source, f"forbidden file I/O: {pattern!r}"

    def test_no_write_operations(self):
        source = self._source()
        for pattern in (
            "write_by" + "tes(",
            "make" + "dirs(",
            "mk" + "dir(",
        ):
            assert pattern not in source, f"forbidden write op: {pattern!r}"

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
            assert pattern not in source, f"forbidden import: {pattern!r}"

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
                f"order verb appears contiguously: {pattern!r}"
            )

    def test_no_env_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron",
            "getenv" + "(",
        ):
            assert pattern not in source, f"forbidden call: {pattern!r}"

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


# ---------------------------------------------------------------------------
# 30: source modules in the chain are clean
# ---------------------------------------------------------------------------

_CHAIN_MODULES = [
    _planner_mod, _plan_mod, _gate_mod,
    _lifecycle_mod, _preview_mod, _approval_mod,
]
_CHAIN_MODULE_IDS = [
    "s30_planner", "s27_plan_validator", "s28_safety_gate",
    "s32_lifecycle", "s36_dry_run_preview", "s25_approval_validator",
]


class TestChainModulesClean:
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
