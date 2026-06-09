"""
tests/test_paper_order_safety_gate_integration.py
--------------------------------------------------
S29: Integration tests for the pure offline chain

    S25 validate_paper_approval_artifact()
    -> S27 validate_paper_order_plan()
    -> S28 evaluate_paper_order_safety_gate()

Every core scenario runs the REAL S25 and S27 validators through the REAL
S28 gate. The single documented exception is the gate's `_plan_validator`
injection seam, used only where both real validators structurally pin a
value (allowed_session == "regular") so a mismatch can never reach the
gate through validated inputs -- see TestSessionMismatchGateLevel.

All fixtures are plain in-memory dicts. No real approval artifact, order
plan, config, or any other artifact is created. No files are read or
written. No broker, network, credential, environment-variable, or order
access is made.

A paper order plan is not an order. Validator PASS is not order approval.
PASS_DRY_RUN_ONLY only allows a future dry-run/no-submit rendering step --
it is never paper or live trading approval or order submission approval.
Paper trading remains not approved; live trading remains blocked; the
chain fails closed at every stage.
"""

from __future__ import annotations

import builtins
import copy
import inspect

import pytest

import src.research.paper_approval_validator as _approval_mod
import src.research.paper_order_plan_validator as _plan_mod
import src.research.paper_order_safety_gate as _gate_mod
from src.research.paper_approval_validator import validate_paper_approval_artifact
from src.research.paper_order_plan_validator import (
    PaperOrderPlanStatus,
    PaperOrderPlanValidationResult,
    validate_paper_order_plan,
)
from src.research.paper_order_safety_gate import (
    PaperOrderSafetyGateStatus,
    evaluate_paper_order_safety_gate,
)


# ---------------------------------------------------------------------------
# Fixture helpers (plain in-memory dicts; no files; no artifacts)
# ---------------------------------------------------------------------------

def _valid_approval(**overrides) -> dict:
    """Return a complete valid PTA/1.0 approval artifact dict."""
    base = {
        "artifact_schema_version": "PTA/1.0",
        "approval_artifact_type": "PAPER_TRADING_APPROVAL",
        "approval_scope": "PAPER_TRADING_LIMITED_RUN_ONLY",
        "candidate_id": "cand-001",
        "run_id": "run-001",
        "source_git_sha": "abc123def456abc123def456abc123def456abc1",
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


def _valid_plan(**overrides) -> dict:
    """Return a complete valid POP/1.0 order plan dict consistent with _valid_approval."""
    base = {
        "plan_schema_version": "POP/1.0",
        "plan_type": "PAPER_ORDER_PLAN",
        "plan_id": "plan-001",
        "candidate_id": "cand-001",
        "run_id": "run-001",
        "source_git_sha": "abc123def456abc123def456abc123def456abc1",
        "approval_artifact_hash": "hash-pta-abc001",
        "paper_config_hash": "hash-pc-abc001",
        "simulation_result_hash": "hash-sim-abc001",
        "generated_at_utc": "2025-01-15T09:00:00Z",
        "expires_at_utc": "2025-01-15T17:00:00Z",
        "symbol": "AAPL",
        "interval": "60m",
        "strategy_family": "TREND_BREAKOUT",
        "holding_horizon": "intraday",
        "side": "BUY",
        "order_type": "market",
        "quantity": 10.0,
        "notional": 1500.0,
        "limit_price": None,
        "time_in_force": "day",
        "allowed_session": "regular",
        "rationale": "momentum breakout signal detected on 60m bar",
        "signal_snapshot": {"signal": "BUY", "confidence": 0.85},
        "risk_snapshot": {
            "max_position_fraction": 0.05,
            "max_daily_loss": 500.0,
            "max_drawdown_stop": 0.15,
            "max_orders_per_day": 3,
            "max_notional_per_position": 5000.0,
        },
        "approval_scope": "PAPER_TRADING_LIMITED_RUN_ONLY",
        "dry_run_required": True,
        "human_confirmation_required": True,
        "kill_switch_required": True,
        "safety_gate_required": True,
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
        "live_trading_allowed": False,
        "notes": "standard intraday momentum breakout plan",
        "plan_status": "PLAN_READY_FOR_SAFETY_GATE",
    }
    base.update(overrides)
    return base


def _clean_state(**overrides) -> dict:
    """Return a clean, valid current_state dict."""
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


def _run_chain(approval=None, plan=None, state=None):
    """Run all three real stages and return (approval_result, plan_result, gate_result).

    The S25 and S27 validators are run standalone first (so tests can assert
    where in the chain a scenario is blocked), then the real S28 gate is run
    on the same inputs with its default (real) validators.
    """
    if approval is None:
        approval = _valid_approval()
    if plan is None:
        plan = _valid_plan()
    if state is None:
        state = _clean_state()
    approval_result = validate_paper_approval_artifact(approval)
    plan_result = validate_paper_order_plan(plan)
    gate_result = evaluate_paper_order_safety_gate(
        approval, plan, current_state=state
    )
    return approval_result, plan_result, gate_result


_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)


# ---------------------------------------------------------------------------
# Gate-level bypass stub -- used ONLY where the real validators structurally
# pin a value so the corresponding gate check can never fail through
# validated inputs. Both S25 and S27 pin allowed_session to "regular" (the
# only supported session), so a session mismatch can never reach the gate
# through the real chain; likewise the plan validator rejects any mutated
# safety flag before the gate's own safety.fixed_flags defense-in-depth
# check can see it. The gate's documented `_plan_validator` injection seam
# is the only way to isolate these two gate-level checks.
# ---------------------------------------------------------------------------

def _plan_validator_pass_stub(plan) -> PaperOrderPlanValidationResult:
    return PaperOrderPlanValidationResult(
        result="PASS",
        blocker=None,
        plan_id=plan.get("plan_id") if isinstance(plan, dict) else None,
        candidate_id="cand-001",
        run_id="run-001",
        status=PaperOrderPlanStatus.PLAN_READY_FOR_SAFETY_GATE,
        criteria_checked=("plan.validator",),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


# ---------------------------------------------------------------------------
# 1. Full chain PASS
# ---------------------------------------------------------------------------

class TestFullChainPass:
    def test_stage1_approval_validator_passes(self):
        approval_result, _, _ = _run_chain()
        assert approval_result.result == "PASS"
        assert approval_result.blocker is None

    def test_stage2_plan_validator_passes(self):
        _, plan_result, _ = _run_chain()
        assert plan_result.result == "PASS"
        assert plan_result.blocker is None

    def test_stage3_gate_returns_pass_dry_run_only(self):
        _, _, gate_result = _run_chain()
        assert gate_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert gate_result.blocker is None

    def test_pass_all_22_checks_passed_none_failed(self):
        _, _, gate_result = _run_chain()
        assert len(gate_result.checks_passed) == 22
        assert gate_result.checks_failed == ()

    def test_pass_identity_propagated_through_chain(self):
        approval_result, plan_result, gate_result = _run_chain()
        assert approval_result.candidate_id == "cand-001"
        assert plan_result.plan_id == "plan-001"
        assert gate_result.plan_id == "plan-001"
        assert gate_result.candidate_id == "cand-001"
        assert gate_result.run_id == "run-001"

    def test_pass_is_dry_run_clearance_only_not_order_approval(self):
        # PASS_DRY_RUN_ONLY only allows a future dry-run/no-submit rendering
        # step; it never approves paper trading, live trading, or any order.
        _, _, gate_result = _run_chain()
        assert gate_result.live_trading_allowed is False
        assert gate_result.order_action_requested is False
        assert gate_result.dry_run_required is True
        assert gate_result.human_confirmation_required is True
        assert gate_result.kill_switch_required is True


# ---------------------------------------------------------------------------
# 2. Approval artifact blocked by S25 -> gate BLOCKED_APPROVAL
# ---------------------------------------------------------------------------

class TestApprovalStageBlocksChain:
    def test_wrong_scope_blocked_by_s25_then_gate_blocked_approval(self):
        approval = _valid_approval(approval_scope="LIVE_TRADING_FULL")
        approval_result, _, gate_result = _run_chain(approval=approval)
        assert approval_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_APPROVAL
        assert "approval.validator" in gate_result.checks_failed

    def test_empty_approval_dict_gate_blocked_approval(self):
        approval_result, _, gate_result = _run_chain(approval={})
        assert approval_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_APPROVAL

    def test_blocked_approval_stops_chain_before_plan_stage(self):
        _, _, gate_result = _run_chain(approval={})
        assert "plan.validator" not in gate_result.checks_passed
        assert "plan.validator" not in gate_result.checks_failed


# ---------------------------------------------------------------------------
# 3. Order plan blocked by S27 -> gate BLOCKED_PLAN
# ---------------------------------------------------------------------------

class TestPlanStageBlocksChain:
    def test_unsupported_order_type_blocked_by_s27_then_gate_blocked_plan(self):
        plan = _valid_plan(order_type="stop")
        _, plan_result, gate_result = _run_chain(plan=plan)
        assert plan_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PLAN
        assert "plan.validator" in gate_result.checks_failed

    def test_empty_plan_dict_gate_blocked_plan(self):
        _, plan_result, gate_result = _run_chain(plan={})
        assert plan_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PLAN

    def test_approval_stage_passed_before_plan_blocked(self):
        _, _, gate_result = _run_chain(plan={})
        assert "approval.validator" in gate_result.checks_passed


# ---------------------------------------------------------------------------
# 4-8. Cross-artifact provenance mismatches -> gate BLOCKED_PROVENANCE
#
# Each scenario uses inputs that individually PASS the real S25 and S27
# validators -- proving the mismatch is caught only at the S28 gate stage.
# ---------------------------------------------------------------------------

class TestProvenanceMismatchCaughtAtGate:
    @pytest.mark.parametrize(
        ("plan_override", "expected_failed_check"),
        [
            ({"candidate_id": "cand-002"}, "provenance.candidate_id"),
            ({"run_id": "run-002"}, "provenance.run_id"),
            ({"approval_artifact_hash": "hash-pta-other"}, "provenance.approval_artifact_hash"),
            ({"paper_config_hash": "hash-pc-other"}, "provenance.paper_config_hash"),
            ({"simulation_result_hash": "hash-sim-other"}, "provenance.simulation_result_hash"),
        ],
        ids=[
            "candidate_id_mismatch",
            "run_id_mismatch",
            "approval_artifact_hash_mismatch",
            "paper_config_hash_mismatch",
            "simulation_result_hash_mismatch",
        ],
    )
    def test_mismatch_passes_both_validators_but_gate_blocks(
        self, plan_override, expected_failed_check
    ):
        plan = _valid_plan(**plan_override)
        approval_result, plan_result, gate_result = _run_chain(plan=plan)
        # Both inputs are individually valid...
        assert approval_result.result == "PASS"
        assert plan_result.result == "PASS"
        # ...so only the gate's cross-artifact check can catch the mismatch.
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert expected_failed_check in gate_result.checks_failed


# ---------------------------------------------------------------------------
# 9. Kill switch closed -> gate BLOCKED_KILL_SWITCH
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_kill_switch_closed_blocks_gate(self):
        approval_result, plan_result, gate_result = _run_chain(
            state=_clean_state(kill_switch_open=False)
        )
        assert approval_result.result == "PASS"
        assert plan_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH
        assert "kill_switch.open" in gate_result.checks_failed

    def test_kill_switch_open_chain_passes(self):
        _, _, gate_result = _run_chain(state=_clean_state(kill_switch_open=True))
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY


# ---------------------------------------------------------------------------
# 10-13. Approval allowlist enforcement at the gate
#
# Each plan individually passes the real S27 validator; the block comes from
# comparing against the approval artifact's allowlists at the gate.
# ---------------------------------------------------------------------------

class TestAllowlistEnforcement:
    def test_symbol_not_in_approval_allowlist_blocked_provenance(self):
        plan = _valid_plan(symbol="MSFT")
        _, plan_result, gate_result = _run_chain(plan=plan)
        assert plan_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "allowlist.symbol" in gate_result.checks_failed

    def test_interval_not_in_approval_allowlist_blocked_provenance(self):
        plan = _valid_plan(interval="1d")
        _, plan_result, gate_result = _run_chain(plan=plan)
        assert plan_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "allowlist.interval" in gate_result.checks_failed

    def test_strategy_family_not_in_approval_allowlist_blocked_provenance(self):
        plan = _valid_plan(strategy_family="MEAN_REVERSION")
        _, plan_result, gate_result = _run_chain(plan=plan)
        assert plan_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "allowlist.strategy_family" in gate_result.checks_failed

    def test_order_type_not_in_approval_allowlist_blocked_risk_limit(self):
        # "market" is valid for S27, but this approval only allows "limit".
        approval = _valid_approval(allowed_order_types=["limit"])
        plan = _valid_plan(order_type="market")
        approval_result, plan_result, gate_result = _run_chain(
            approval=approval, plan=plan
        )
        assert approval_result.result == "PASS"
        assert plan_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "allowlist.order_type" in gate_result.checks_failed


# ---------------------------------------------------------------------------
# 14. Session mismatch -> gate BLOCKED_RISK_LIMIT (gate-level only)
# ---------------------------------------------------------------------------

class TestSessionMismatchGateLevel:
    def test_session_mismatch_blocked_risk_limit_via_injection_seam(self):
        # Bypass justification: both S25 and S27 pin allowed_session to
        # "regular" -- the only supported value -- so a session mismatch can
        # never pass through the real validators. The gate's allowlist.session
        # check is defense-in-depth, and the documented `_plan_validator`
        # injection seam is the only way to isolate it.
        plan = _valid_plan(allowed_session="extended")
        gate_result = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_plan_validator_pass_stub,
        )
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "allowlist.session" in gate_result.checks_failed

    def test_session_mismatch_cannot_pass_real_plan_validator(self):
        # Confirms why the bypass above is necessary: the real S27 validator
        # already fails closed on any non-"regular" session.
        plan_result = validate_paper_order_plan(_valid_plan(allowed_session="extended"))
        assert plan_result.result == "BLOCKED"


# ---------------------------------------------------------------------------
# 15-18. Risk limits enforced at the gate
# ---------------------------------------------------------------------------

class TestRiskLimits:
    def test_notional_exceeds_approval_cap_blocked_risk_limit(self):
        # risk_snapshot cap raised to 6000 so the plan passes S27 on its own;
        # the approval cap (5000) still blocks at the gate.
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 6000.0
        plan = _valid_plan(notional=5500.0, risk_snapshot=snap)
        _, plan_result, gate_result = _run_chain(plan=plan)
        assert plan_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.notional" in gate_result.checks_failed

    def test_daily_order_count_at_cap_blocked_risk_limit(self):
        # max_orders_per_day is 5; a count already at 5 leaves no capacity.
        gate_result = _run_chain(
            state=_clean_state(current_daily_order_count=5)
        )[2]
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.daily_order_count" in gate_result.checks_failed
        assert gate_result.remaining_daily_order_capacity == 0

    def test_projected_daily_loss_exceeds_cap_blocked_risk_limit(self):
        # max(0, -(-600)) + 1500 notional = 2100 > max_daily_loss 2000.
        gate_result = _run_chain(
            state=_clean_state(current_daily_pnl=-600.0)
        )[2]
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.daily_loss" in gate_result.checks_failed
        assert gate_result.projected_daily_loss_after_order == pytest.approx(2100.0)

    def test_projected_drawdown_exceeds_cap_blocked_risk_limit(self):
        # 0.4 + 1500/5000 = 0.7 > max_drawdown_stop 0.50.
        gate_result = _run_chain(
            state=_clean_state(current_drawdown=0.4)
        )[2]
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.drawdown" in gate_result.checks_failed
        assert gate_result.projected_drawdown_after_order == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# 19. Duplicate plan_id -> gate BLOCKED_DUPLICATE
# ---------------------------------------------------------------------------

class TestDuplicatePlan:
    def test_duplicate_plan_id_blocked_duplicate(self):
        gate_result = _run_chain(
            state=_clean_state(processed_plan_ids=["plan-001"])
        )[2]
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE
        assert "duplicate.plan_id" in gate_result.checks_failed

    def test_unrelated_processed_plan_id_does_not_block(self):
        gate_result = _run_chain(
            state=_clean_state(processed_plan_ids=["plan-other"])
        )[2]
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY


# ---------------------------------------------------------------------------
# 20-22. Position conflicts
# ---------------------------------------------------------------------------

class TestPositionConflict:
    def test_open_same_candidate_and_symbol_blocked_conflict(self):
        positions = [{"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}]
        gate_result = _run_chain(state=_clean_state(open_positions=positions))[2]
        assert (
            gate_result.gate_status
            is PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT
        )
        assert "position.conflict" in gate_result.checks_failed

    def test_pending_same_candidate_and_symbol_blocked_conflict(self):
        positions = [{"symbol": "AAPL", "candidate_id": "cand-001", "status": "PENDING"}]
        gate_result = _run_chain(state=_clean_state(open_positions=positions))[2]
        assert (
            gate_result.gate_status
            is PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT
        )

    def test_closed_same_candidate_and_symbol_does_not_block(self):
        positions = [{"symbol": "AAPL", "candidate_id": "cand-001", "status": "CLOSED"}]
        gate_result = _run_chain(state=_clean_state(open_positions=positions))[2]
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert "position.conflict" in gate_result.checks_passed


# ---------------------------------------------------------------------------
# 23. Safety flag mutation is blocked before PASS at the earliest stage
# ---------------------------------------------------------------------------

class TestSafetyFlagMutationBlockedBeforePass:
    @pytest.mark.parametrize("flag", _SAFETY_FLAG_NAMES)
    def test_plan_safety_flag_true_blocked_at_plan_stage(self, flag):
        # The real S27 validator fails closed on any mutated safety flag,
        # so the chain never reaches PASS.
        plan = _valid_plan(**{flag: True})
        _, plan_result, gate_result = _run_chain(plan=plan)
        assert plan_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_PLAN
        assert gate_result.result == "BLOCKED"

    def test_approval_live_trading_approved_true_blocked_at_approval_stage(self):
        approval = _valid_approval(live_trading_approved=True)
        approval_result, _, gate_result = _run_chain(approval=approval)
        assert approval_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_APPROVAL

    def test_approval_live_order_submission_approved_true_blocked(self):
        approval = _valid_approval(live_order_submission_approved=True)
        approval_result, _, gate_result = _run_chain(approval=approval)
        assert approval_result.result == "BLOCKED"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_APPROVAL

    def test_gate_level_defense_in_depth_catches_mutation_past_plan_stage(self):
        # Bypass justification: the real S27 validator blocks a mutated
        # order_action_requested before the gate's own safety.fixed_flags
        # defense-in-depth check can ever see it; the documented
        # `_plan_validator` injection seam is the only way to prove the gate
        # would still fail closed if the upstream stage were ever wrong.
        plan = _valid_plan(order_action_requested=True)
        gate_result = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_plan_validator_pass_stub,
        )
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "safety.fixed_flags" in gate_result.checks_failed


# ---------------------------------------------------------------------------
# 24. Result safety flags always False across PASS and blocked scenarios
# ---------------------------------------------------------------------------

def _scenario_results():
    """Full-chain results for one PASS and every blocked scenario family."""
    return [
        ("pass", _run_chain()),
        ("blocked_approval", _run_chain(approval={})),
        ("blocked_plan", _run_chain(plan={})),
        ("blocked_provenance", _run_chain(plan=_valid_plan(candidate_id="cand-002"))),
        ("blocked_kill_switch", _run_chain(state=_clean_state(kill_switch_open=False))),
        ("blocked_risk", _run_chain(state=_clean_state(current_daily_order_count=5))),
        ("blocked_duplicate", _run_chain(state=_clean_state(processed_plan_ids=["plan-001"]))),
        (
            "blocked_conflict",
            _run_chain(state=_clean_state(open_positions=[
                {"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}
            ])),
        ),
    ]


class TestSafetyFlagsAlwaysFalse:
    def test_gate_result_safety_flags_false_in_all_scenarios(self):
        for label, (_, _, gate_result) in _scenario_results():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(gate_result, flag) is False, (
                    f"{flag} should be False in scenario {label}"
                )

    def test_validator_result_safety_flags_false_in_all_scenarios(self):
        for label, (approval_result, plan_result, _) in _scenario_results():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(approval_result, flag) is False, (
                    f"approval {flag} should be False in scenario {label}"
                )
                assert getattr(plan_result, flag) is False, (
                    f"plan {flag} should be False in scenario {label}"
                )

    def test_gate_result_fixed_safety_bools_true_in_all_scenarios(self):
        for label, (_, _, gate_result) in _scenario_results():
            assert gate_result.dry_run_required is True, label
            assert gate_result.human_confirmation_required is True, label
            assert gate_result.kill_switch_required is True, label


# ---------------------------------------------------------------------------
# 25. Inputs are not mutated by any stage
# ---------------------------------------------------------------------------

class TestInputsNotMutated:
    def test_pass_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        plan = _valid_plan()
        state = _clean_state()
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(plan), copy.deepcopy(state)
        )
        _run_chain(approval=approval, plan=plan, state=state)
        assert approval == snapshots[0]
        assert plan == snapshots[1]
        assert state == snapshots[2]

    def test_blocked_chain_does_not_mutate_inputs(self):
        approval = _valid_approval(live_trading_approved=True)
        plan = _valid_plan(candidate_id="cand-002")
        state = _clean_state(kill_switch_open=False)
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(plan), copy.deepcopy(state)
        )
        _run_chain(approval=approval, plan=plan, state=state)
        assert approval == snapshots[0]
        assert plan == snapshots[1]
        assert state == snapshots[2]


# ---------------------------------------------------------------------------
# 26. Determinism: same input -> same output at every stage
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_pass_chain_is_deterministic(self):
        first = _run_chain()
        second = _run_chain()
        assert first[0] == second[0]
        assert first[1] == second[1]
        assert first[2] == second[2]

    def test_blocked_chain_is_deterministic(self):
        plan = _valid_plan(symbol="MSFT")
        first = _run_chain(plan=plan)
        second = _run_chain(plan=_valid_plan(symbol="MSFT"))
        assert first[2] == second[2]
        assert first[2].checks_failed == second[2].checks_failed


# ---------------------------------------------------------------------------
# 27. No real artifacts are created by the chain
# ---------------------------------------------------------------------------

class TestNoArtifactsCreated:
    def test_chain_never_opens_a_file_at_runtime(self, monkeypatch):
        # Record every call to the builtin file constructor during a full
        # PASS chain and a blocked chain; a pure offline chain must make none.
        calls: list = []
        _real_ctor = builtins.open

        def _recording_ctor(*args, **kwargs):
            calls.append(args)
            return _real_ctor(*args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(builtins, "open", _recording_ctor)
            _, _, pass_result = _run_chain()
            _, _, blocked_result = _run_chain(
                state=_clean_state(kill_switch_open=False)
            )
        assert calls == []
        assert pass_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert (
            blocked_result.gate_status
            is PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH
        )

    @pytest.mark.parametrize(
        "module", [_approval_mod, _plan_mod, _gate_mod],
        ids=["s25_approval_validator", "s27_plan_validator", "s28_safety_gate"],
    )
    def test_chain_module_sources_contain_no_file_io(self, module):
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

    @pytest.mark.parametrize(
        "module", [_approval_mod, _plan_mod, _gate_mod],
        ids=["s25_approval_validator", "s27_plan_validator", "s28_safety_gate"],
    )
    def test_chain_module_sources_contain_no_forbidden_imports(self, module):
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

    @pytest.mark.parametrize(
        "module", [_approval_mod, _plan_mod, _gate_mod],
        ids=["s25_approval_validator", "s27_plan_validator", "s28_safety_gate"],
    )
    def test_chain_module_sources_contain_no_broker_env_or_order_calls(self, module):
        # Patterns target actual usage forms only: the S25/S27 validators
        # legitimately list bare words like the env-var mapping and order
        # verbs inside their own forbidden-substring scan lists.
        source = inspect.getsource(module)
        for pattern in (
            "Alpaca" + "Broker" + "Adapter",
            "os.envi" + "ron[",
            "os.envi" + "ron.get(",
            "getenv" + "(",
            "." + "submit_or" + "der(",
            "." + "place_or" + "der(",
            "live_su" + "bmit(",
        ):
            assert pattern not in source, (
                f"forbidden call {pattern!r} in {module.__name__}"
            )


# ---------------------------------------------------------------------------
# 28. This test module itself is clean
# ---------------------------------------------------------------------------

class TestThisModuleClean:
    def _source(self) -> str:
        import sys
        return inspect.getsource(sys.modules[__name__])

    def test_no_write_operations_in_this_module(self):
        source = self._source()
        for pattern in (
            "write_te" + "xt(",
            "write_by" + "tes(",
            "make" + "dirs(",
            "mk" + "dir(",
        ):
            assert pattern not in source, f"module contains write pattern {pattern!r}"

    def test_no_file_io_in_this_module(self):
        source = self._source()
        for pattern in (
            "open" + "(",
            "Path" + "(",
            "read_te" + "xt(",
            "write_te" + "xt(",
        ):
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

    def test_no_forbidden_imports_in_this_module(self):
        # Every pattern is built from split fragments so the joined pattern
        # never appears literally in this module's own source.
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

    def test_no_order_env_or_broker_calls_in_this_module(self):
        source = self._source()
        for pattern in (
            "." + "submit_or" + "der(",
            "." + "place_or" + "der(",
            "live_su" + "bmit(",
            "os.envi" + "ron",
            "getenv" + "(",
        ):
            assert pattern not in source, f"forbidden call found: {pattern!r}"

    def test_no_runtime_or_execution_imports_in_this_module(self):
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
