"""
tests/test_paper_order_safety_gate.py
--------------------------------------
S28: Tests for the pure offline paper order safety gate.

All fixtures are plain in-memory dicts. No real artifacts are created.
No files are read or written. No broker, network, credential, environment-
variable, subprocess, or order access is made. Gate PASS means only that
the plan passed shape, provenance, risk, and state checks for offline safety
review -- it is not, and can never be, paper or live trading approval.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import fields as dataclass_fields

import pytest

import src.research.paper_order_safety_gate as _gate_mod
from src.research.paper_approval_validator import (
    PaperApprovalStatus,
    PaperApprovalValidationResult,
)
from src.research.paper_order_plan_validator import (
    PaperOrderPlanStatus,
    PaperOrderPlanValidationResult,
)
from src.research.paper_order_safety_gate import (
    PaperOrderSafetyGateResult,
    PaperOrderSafetyGateStatus,
    evaluate_paper_order_safety_gate,
)


# ---------------------------------------------------------------------------
# Fixture helpers
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


def _run(
    approval=None,
    plan=None,
    state=None,
    **gate_kwargs,
) -> PaperOrderSafetyGateResult:
    if approval is None:
        approval = _valid_approval()
    if plan is None:
        plan = _valid_plan()
    if state is None:
        state = _clean_state()
    return evaluate_paper_order_safety_gate(
        approval, plan, current_state=state, **gate_kwargs
    )


# ---------------------------------------------------------------------------
# Mock validators for testing gate-level safety checks independently
# ---------------------------------------------------------------------------

def _mock_approval_pass(artifact) -> PaperApprovalValidationResult:
    return PaperApprovalValidationResult(
        result="PASS",
        blocker=None,
        candidate_id="cand-001",
        run_id="run-001",
        status=PaperApprovalStatus.APPROVED_FOR_LIMITED_PAPER_RUN,
        criteria_checked=("approval.validator",),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _mock_approval_safety_flag_true(artifact) -> PaperApprovalValidationResult:
    return PaperApprovalValidationResult(
        result="PASS",
        blocker=None,
        candidate_id="cand-001",
        run_id="run-001",
        status=PaperApprovalStatus.APPROVED_FOR_LIMITED_PAPER_RUN,
        criteria_checked=("approval.validator",),
        criteria_failed=(),
        broker_calls_made=True,  # safety flag True
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _mock_plan_pass(plan) -> PaperOrderPlanValidationResult:
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


def _mock_plan_safety_flag_true(plan) -> PaperOrderPlanValidationResult:
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
        live_trading_allowed=True,  # safety flag True
    )


_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)


# ---------------------------------------------------------------------------
# TestPaperOrderSafetyGateStatusEnum
# ---------------------------------------------------------------------------

class TestPaperOrderSafetyGateStatusEnum:
    def test_all_expected_values_present(self):
        expected = {
            "NOT_EVALUATED",
            "PASS_DRY_RUN_ONLY",
            "BLOCKED_APPROVAL",
            "BLOCKED_PLAN",
            "BLOCKED_PROVENANCE",
            "BLOCKED_KILL_SWITCH",
            "BLOCKED_RISK_LIMIT",
            "BLOCKED_DUPLICATE",
            "BLOCKED_POSITION_CONFLICT",
            "BLOCKED_SAFETY",
            "ERROR_GATE",
        }
        actual = {m.value for m in PaperOrderSafetyGateStatus}
        assert actual == expected

    def test_enum_is_str_subclass(self):
        for member in PaperOrderSafetyGateStatus:
            assert isinstance(member, str)
            assert member == member.value


# ---------------------------------------------------------------------------
# TestPaperOrderSafetyGateResultDataclass
# ---------------------------------------------------------------------------

class TestPaperOrderSafetyGateResultDataclass:
    def test_result_is_frozen(self):
        r = _run()
        with pytest.raises(AttributeError):
            r.result = "tampered"  # type: ignore[misc]

    def test_result_has_expected_fields(self):
        field_names = {f.name for f in dataclass_fields(PaperOrderSafetyGateResult)}
        expected = {
            "result", "blocker", "gate_status",
            "plan_id", "candidate_id", "run_id",
            "checks_passed", "checks_failed",
            "approved_notional", "remaining_daily_order_capacity",
            "projected_daily_loss_after_order", "projected_drawdown_after_order",
            "dry_run_required", "human_confirmation_required", "kill_switch_required",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_checks_passed_and_failed_are_tuples(self):
        r = _run()
        assert isinstance(r.checks_passed, tuple)
        assert isinstance(r.checks_failed, tuple)


# ---------------------------------------------------------------------------
# TestValidPassCase
# ---------------------------------------------------------------------------

class TestValidPassCase:
    def test_pass_result_and_status(self):
        r = _run()
        assert r.result == "PASS"
        assert r.gate_status == PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY

    def test_pass_blocker_is_none(self):
        r = _run()
        assert r.blocker is None

    def test_pass_safety_flags_all_false(self):
        r = _run()
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(r, flag) is False, f"{flag} should be False"

    def test_pass_result_fixed_safety_bools_true(self):
        r = _run()
        assert r.dry_run_required is True
        assert r.human_confirmation_required is True
        assert r.kill_switch_required is True

    def test_pass_all_22_checks_in_checks_passed(self):
        r = _run()
        assert len(r.checks_passed) == 22
        assert r.checks_failed == ()

    def test_pass_expected_check_names_present(self):
        r = _run()
        checked_set = set(r.checks_passed)
        expected = {
            "approval.validator", "plan.validator", "state.schema",
            "provenance.candidate_id", "provenance.run_id",
            "provenance.approval_artifact_hash", "provenance.paper_config_hash",
            "provenance.simulation_result_hash",
            "kill_switch.open",
            "allowlist.symbol", "allowlist.interval", "allowlist.strategy_family",
            "allowlist.order_type", "allowlist.session",
            "risk.notional", "risk.position_fraction", "risk.daily_order_count",
            "risk.daily_loss", "risk.drawdown",
            "duplicate.plan_id", "position.conflict",
            "safety.fixed_flags",
        }
        assert expected == checked_set

    def test_pass_approved_notional_set(self):
        r = _run()
        assert r.approved_notional == 5000.0

    def test_pass_remaining_capacity_set(self):
        r = _run()
        assert r.remaining_daily_order_capacity == 5

    def test_pass_projected_values_set(self):
        r = _run()
        assert r.projected_daily_loss_after_order is not None
        assert r.projected_drawdown_after_order is not None

    def test_pass_not_live_trading_approval(self):
        r = _run()
        assert r.live_trading_allowed is False
        assert r.order_action_requested is False
        assert r.broker_calls_made is False

    def test_pass_plan_id_matches(self):
        r = _run()
        assert r.plan_id == "plan-001"

    def test_pass_candidate_and_run_id_match(self):
        r = _run()
        assert r.candidate_id == "cand-001"
        assert r.run_id == "run-001"


# ---------------------------------------------------------------------------
# TestBlockedApproval
# ---------------------------------------------------------------------------

class TestBlockedApproval:
    def test_real_validator_blocked_artifact_returns_blocked_approval(self):
        r = _run(approval={})
        assert r.result == "BLOCKED"
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_APPROVAL

    def test_blocked_approval_check_name_in_failed(self):
        r = _run(approval={})
        assert "approval.validator" in r.checks_failed

    def test_blocked_approval_plan_validator_not_called(self):
        # Only 0 checks passed before the approval gate fires
        r = _run(approval={})
        assert "plan.validator" not in r.checks_passed
        assert "plan.validator" not in r.checks_failed

    def test_approval_validator_exception_returns_blocked_approval(self):
        def _raising_validator(artifact):
            raise RuntimeError("unexpected error")

        r = _run(_approval_validator=_raising_validator)
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_APPROVAL
        assert "approval.validator" in r.checks_failed


# ---------------------------------------------------------------------------
# TestBlockedPlan
# ---------------------------------------------------------------------------

class TestBlockedPlan:
    def test_real_validator_blocked_plan_returns_blocked_plan(self):
        r = _run(plan={})
        assert r.result == "BLOCKED"
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PLAN

    def test_blocked_plan_check_name_in_failed(self):
        r = _run(plan={})
        assert "plan.validator" in r.checks_failed

    def test_approval_passed_before_plan_blocked(self):
        r = _run(plan={})
        assert "approval.validator" in r.checks_passed

    def test_plan_validator_exception_returns_blocked_plan(self):
        def _raising_plan(plan):
            raise ValueError("plan error")

        r = _run(_plan_validator=_raising_plan)
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PLAN
        assert "plan.validator" in r.checks_failed


# ---------------------------------------------------------------------------
# TestSafetyFlagOnValidator
# ---------------------------------------------------------------------------

class TestSafetyFlagOnValidator:
    def test_approval_safety_flag_true_returns_blocked_safety(self):
        r = _run(_approval_validator=_mock_approval_safety_flag_true)
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "approval.validator" in r.checks_failed

    def test_plan_safety_flag_true_returns_blocked_safety(self):
        r = _run(
            _approval_validator=_mock_approval_pass,
            _plan_validator=_mock_plan_safety_flag_true,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "plan.validator" in r.checks_failed


# ---------------------------------------------------------------------------
# TestStateSchemaGate
# ---------------------------------------------------------------------------

class TestStateSchemaGate:
    def test_not_a_dict_returns_error_gate(self):
        r = evaluate_paper_order_safety_gate(
            _valid_approval(), _valid_plan(), current_state=None
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.ERROR_GATE
        assert "state.schema" in r.checks_failed

    def test_missing_field_returns_error_gate(self):
        state = _clean_state()
        del state["kill_switch_open"]
        r = _run(state=state)
        assert r.gate_status == PaperOrderSafetyGateStatus.ERROR_GATE
        assert "state.schema" in r.checks_failed

    def test_wrong_type_for_bool_field_returns_error_gate(self):
        r = _run(state=_clean_state(kill_switch_open=1))  # int not bool
        assert r.gate_status == PaperOrderSafetyGateStatus.ERROR_GATE

    def test_wrong_type_for_int_field_returns_error_gate(self):
        r = _run(state=_clean_state(current_daily_order_count="zero"))
        assert r.gate_status == PaperOrderSafetyGateStatus.ERROR_GATE

    def test_bool_for_int_field_returns_error_gate(self):
        r = _run(state=_clean_state(current_daily_order_count=True))
        assert r.gate_status == PaperOrderSafetyGateStatus.ERROR_GATE

    def test_wrong_type_for_list_field_returns_error_gate(self):
        r = _run(state=_clean_state(open_positions=None))
        assert r.gate_status == PaperOrderSafetyGateStatus.ERROR_GATE

    def test_prior_checks_pass_before_state_error(self):
        state = _clean_state()
        del state["open_positions"]
        r = _run(state=state)
        assert "approval.validator" in r.checks_passed
        assert "plan.validator" in r.checks_passed
        assert "state.schema" in r.checks_failed


# ---------------------------------------------------------------------------
# TestProvenanceChecks
# ---------------------------------------------------------------------------

class TestProvenanceChecks:
    def test_candidate_id_mismatch_blocked_provenance(self):
        r = _run(plan=_valid_plan(candidate_id="wrong-cand"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "provenance.candidate_id" in r.checks_failed

    def test_run_id_mismatch_blocked_provenance(self):
        r = _run(plan=_valid_plan(run_id="wrong-run"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "provenance.run_id" in r.checks_failed

    def test_approval_artifact_hash_mismatch_blocked_provenance(self):
        r = _run(plan=_valid_plan(approval_artifact_hash="wrong-hash"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "provenance.approval_artifact_hash" in r.checks_failed

    def test_paper_config_hash_mismatch_blocked_provenance(self):
        r = _run(plan=_valid_plan(paper_config_hash="wrong-pc-hash"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "provenance.paper_config_hash" in r.checks_failed

    def test_simulation_result_hash_mismatch_blocked_provenance(self):
        r = _run(plan=_valid_plan(simulation_result_hash="wrong-sim-hash"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "provenance.simulation_result_hash" in r.checks_failed

    def test_artifact_hash_absent_passes_hash_check(self):
        approval = _valid_approval()
        del approval["artifact_hash"]
        r = _run(approval=approval)
        assert r.result == "PASS"
        assert "provenance.approval_artifact_hash" in r.checks_passed


# ---------------------------------------------------------------------------
# TestKillSwitchGate
# ---------------------------------------------------------------------------

class TestKillSwitchGate:
    def test_kill_switch_closed_returns_blocked_kill_switch(self):
        r = _run(state=_clean_state(kill_switch_open=False))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH
        assert "kill_switch.open" in r.checks_failed

    def test_kill_switch_open_passes(self):
        r = _run(state=_clean_state(kill_switch_open=True))
        assert r.result == "PASS"
        assert "kill_switch.open" in r.checks_passed


# ---------------------------------------------------------------------------
# TestAllowlistChecks
# ---------------------------------------------------------------------------

class TestAllowlistChecks:
    def test_symbol_not_allowed_blocked_provenance(self):
        r = _run(plan=_valid_plan(symbol="TSLA"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "allowlist.symbol" in r.checks_failed

    def test_interval_not_allowed_blocked_provenance(self):
        r = _run(plan=_valid_plan(interval="1d"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "allowlist.interval" in r.checks_failed

    def test_strategy_family_not_allowed_blocked_provenance(self):
        r = _run(plan=_valid_plan(strategy_family="MEAN_REVERSION"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE
        assert "allowlist.strategy_family" in r.checks_failed

    def test_order_type_not_allowed_blocked_risk_limit(self):
        approval = _valid_approval(allowed_order_types=["limit"])
        r = _run(approval=approval, plan=_valid_plan(order_type="market"))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "allowlist.order_type" in r.checks_failed

    def test_session_mismatch_blocked_risk_limit(self):
        # Plan validator enforces "regular"; bypass it to test gate-level session check
        plan = _valid_plan(allowed_session="extended")
        r = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "allowlist.session" in r.checks_failed

    def test_symbol_in_allowlist_passes(self):
        approval = _valid_approval(allowed_symbols=["AAPL", "SPY"])
        r = _run(approval=approval, plan=_valid_plan(symbol="SPY"))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestRiskChecks
# ---------------------------------------------------------------------------

class TestRiskChecks:
    def test_notional_exceeds_max_blocked_risk_limit(self):
        # risk_snapshot.max_notional_per_position raised so plan validator passes,
        # but approval's limit (5000) still blocks at the gate level
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 6000.0
        r = _run(plan=_valid_plan(notional=6000.0, risk_snapshot=snap))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.notional" in r.checks_failed

    def test_notional_within_limit_passes_notional_check(self):
        # Default plan: notional=1500 which is well within approval max_notional=5000
        r = _run()
        assert "risk.notional" in r.checks_passed

    def test_position_fraction_exceeds_approval_blocked_risk_limit(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_position_fraction"] = 0.08  # > approval 0.05
        r = _run(plan=_valid_plan(risk_snapshot=snap))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.position_fraction" in r.checks_failed

    def test_daily_order_count_at_limit_blocked_risk_limit(self):
        r = _run(state=_clean_state(current_daily_order_count=5))  # == max_orders_per_day
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.daily_order_count" in r.checks_failed

    def test_daily_order_count_below_limit_passes(self):
        r = _run(state=_clean_state(current_daily_order_count=4))
        assert r.result == "PASS"

    def test_projected_daily_loss_exceeds_max_blocked_risk_limit(self):
        # notional=1500, pnl=0 → proj_loss=1500 ≤ 2000 (passes)
        # notional=2100 → proj_loss=2100 > 2000 (fails)
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 5000.0
        r = _run(plan=_valid_plan(notional=2100.0, risk_snapshot=snap))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.daily_loss" in r.checks_failed

    def test_projected_drawdown_exceeds_max_blocked_risk_limit(self):
        # current_drawdown=0.4, notional=1500, max_notional=5000
        # proj_drawdown=0.4+0.3=0.7 > max_drawdown_stop=0.50
        r = _run(state=_clean_state(current_drawdown=0.4))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.drawdown" in r.checks_failed

    def test_projected_values_computed_on_blocked_result(self):
        r = _run(state=_clean_state(current_drawdown=0.4))
        assert r.projected_daily_loss_after_order is not None
        assert r.projected_drawdown_after_order is not None
        assert r.remaining_daily_order_capacity is not None


# ---------------------------------------------------------------------------
# TestDuplicateGate
# ---------------------------------------------------------------------------

class TestDuplicateGate:
    def test_duplicate_plan_id_blocked_duplicate(self):
        state = _clean_state(processed_plan_ids=["plan-001"])
        r = _run(state=state)
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE
        assert "duplicate.plan_id" in r.checks_failed

    def test_new_plan_id_passes_duplicate_check(self):
        state = _clean_state(processed_plan_ids=["plan-999"])
        r = _run(state=state)
        assert r.result == "PASS"
        assert "duplicate.plan_id" in r.checks_passed


# ---------------------------------------------------------------------------
# TestPositionConflictGate
# ---------------------------------------------------------------------------

class TestPositionConflictGate:
    def test_open_position_same_symbol_and_candidate_blocked_conflict(self):
        positions = [
            {"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}
        ]
        r = _run(state=_clean_state(open_positions=positions))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT
        assert "position.conflict" in r.checks_failed

    def test_pending_position_same_symbol_and_candidate_blocked_conflict(self):
        positions = [
            {"symbol": "AAPL", "candidate_id": "cand-001", "status": "PENDING"}
        ]
        r = _run(state=_clean_state(open_positions=positions))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT

    def test_closed_position_same_symbol_and_candidate_passes(self):
        positions = [
            {"symbol": "AAPL", "candidate_id": "cand-001", "status": "CLOSED"}
        ]
        r = _run(state=_clean_state(open_positions=positions))
        assert r.result == "PASS"

    def test_open_position_different_symbol_passes(self):
        positions = [
            {"symbol": "SPY", "candidate_id": "cand-001", "status": "OPEN"}
        ]
        r = _run(state=_clean_state(open_positions=positions))
        assert r.result == "PASS"

    def test_open_position_different_candidate_passes(self):
        positions = [
            {"symbol": "AAPL", "candidate_id": "cand-999", "status": "OPEN"}
        ]
        r = _run(state=_clean_state(open_positions=positions))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestSafetyFixedFlags
# ---------------------------------------------------------------------------

class TestSafetyFixedFlags:
    def test_dry_run_required_false_blocked_safety(self):
        plan = _valid_plan(dry_run_required=False)
        r = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "safety.fixed_flags" in r.checks_failed

    def test_human_confirmation_required_false_blocked_safety(self):
        plan = _valid_plan(human_confirmation_required=False)
        r = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "safety.fixed_flags" in r.checks_failed

    def test_safety_gate_required_false_blocked_safety(self):
        plan = _valid_plan(safety_gate_required=False)
        r = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "safety.fixed_flags" in r.checks_failed

    def test_approval_live_trading_approved_true_blocked_safety(self):
        approval = _valid_approval(live_trading_approved=True)
        r = evaluate_paper_order_safety_gate(
            approval, _valid_plan(), current_state=_clean_state(),
            _approval_validator=_mock_approval_pass,
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY
        assert "safety.fixed_flags" in r.checks_failed

    def test_approval_live_order_submission_approved_true_blocked_safety(self):
        approval = _valid_approval(live_order_submission_approved=True)
        r = evaluate_paper_order_safety_gate(
            approval, _valid_plan(), current_state=_clean_state(),
            _approval_validator=_mock_approval_pass,
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY

    def test_blocked_safety_has_higher_priority_than_provenance(self):
        # Both safety and provenance fail; BLOCKED_SAFETY wins
        plan = _valid_plan(dry_run_required=False, candidate_id="wrong-cand")
        r = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state(),
            _plan_validator=_mock_plan_pass,
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_SAFETY


# ---------------------------------------------------------------------------
# TestBucketPriority
# ---------------------------------------------------------------------------

class TestBucketPriority:
    def test_blocked_provenance_higher_than_risk(self):
        # Both provenance (symbol) and risk (notional) fail at gate level.
        # Raise risk_snapshot.max_notional so plan validator passes.
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 9999.0
        r = _run(plan=_valid_plan(symbol="TSLA", notional=9999.0, risk_snapshot=snap))
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE

    def test_blocked_kill_switch_higher_than_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 9999.0
        r = _run(
            plan=_valid_plan(notional=9999.0, risk_snapshot=snap),
            state=_clean_state(kill_switch_open=False),
        )
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH

    def test_blocked_risk_higher_than_duplicate(self):
        state = _clean_state(
            current_daily_order_count=5,
            processed_plan_ids=["plan-001"],
        )
        r = _run(state=state)
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT

    def test_blocked_duplicate_higher_than_position_conflict(self):
        positions = [
            {"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}
        ]
        state = _clean_state(
            open_positions=positions,
            processed_plan_ids=["plan-001"],
        )
        r = _run(state=state)
        assert r.gate_status == PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE


# ---------------------------------------------------------------------------
# TestDeterministicChecks
# ---------------------------------------------------------------------------

class TestDeterministicChecks:
    def test_same_input_same_checks_passed(self):
        r1 = _run()
        r2 = _run()
        assert r1.checks_passed == r2.checks_passed

    def test_same_input_same_checks_failed(self):
        r1 = _run(plan=_valid_plan(symbol="TSLA"))
        r2 = _run(plan=_valid_plan(symbol="TSLA"))
        assert r1.checks_failed == r2.checks_failed

    def test_pass_checks_passed_is_deterministic_order(self):
        expected_order = (
            "approval.validator", "plan.validator", "state.schema",
            "provenance.candidate_id", "provenance.run_id",
            "provenance.approval_artifact_hash", "provenance.paper_config_hash",
            "provenance.simulation_result_hash",
            "kill_switch.open",
            "allowlist.symbol", "allowlist.interval", "allowlist.strategy_family",
            "allowlist.order_type", "allowlist.session",
            "risk.notional", "risk.position_fraction", "risk.daily_order_count",
            "risk.daily_loss", "risk.drawdown",
            "duplicate.plan_id", "position.conflict",
            "safety.fixed_flags",
        )
        r = _run()
        assert r.checks_passed == expected_order


# ---------------------------------------------------------------------------
# TestPureFunction
# ---------------------------------------------------------------------------

class TestPureFunction:
    def test_same_input_same_output(self):
        a = _valid_approval()
        p = _valid_plan()
        s = _clean_state()
        r1 = evaluate_paper_order_safety_gate(a, p, current_state=s)
        r2 = evaluate_paper_order_safety_gate(a, p, current_state=s)
        assert r1 == r2

    def test_does_not_mutate_approval_artifact(self):
        a = _valid_approval()
        original = copy.deepcopy(a)
        _run(approval=a)
        assert a == original

    def test_does_not_mutate_order_plan(self):
        p = _valid_plan()
        original = copy.deepcopy(p)
        _run(plan=p)
        assert p == original

    def test_does_not_mutate_current_state(self):
        s = _clean_state()
        original = copy.deepcopy(s)
        _run(state=s)
        assert s == original

    def test_independent_calls_independent_results(self):
        r1 = _run(plan=_valid_plan(plan_id="plan-A"))
        r2 = _run(plan=_valid_plan(plan_id="plan-B"))
        assert r1.plan_id == "plan-A"
        assert r2.plan_id == "plan-B"


# ---------------------------------------------------------------------------
# TestProjectedValueFormulas
# ---------------------------------------------------------------------------

class TestProjectedValueFormulas:
    def test_projected_daily_loss_formula(self):
        # proj_daily_loss = max(0, -pnl) + notional
        state = _clean_state(current_daily_pnl=-300.0)
        r = _run(state=state)
        # max(0, 300) + 1500 = 1800
        assert r.projected_daily_loss_after_order == pytest.approx(1800.0)

    def test_projected_daily_loss_positive_pnl(self):
        # positive pnl → max(0, -pnl)=0
        state = _clean_state(current_daily_pnl=500.0)
        r = _run(state=state)
        assert r.projected_daily_loss_after_order == pytest.approx(1500.0)

    def test_projected_drawdown_formula(self):
        # proj_drawdown = current_drawdown + notional / max_notional
        state = _clean_state(current_drawdown=0.1)
        r = _run(state=state)
        # 0.1 + 1500/5000 = 0.1 + 0.3 = 0.4
        assert r.projected_drawdown_after_order == pytest.approx(0.4)

    def test_remaining_capacity_formula(self):
        state = _clean_state(current_daily_order_count=2)
        r = _run(state=state)
        assert r.remaining_daily_order_capacity == 3

    def test_remaining_capacity_capped_at_zero(self):
        # order count at limit (will BLOCKED_RISK_LIMIT but capacity is still computed)
        state = _clean_state(current_daily_order_count=5)
        r = _run(state=state)
        assert r.remaining_daily_order_capacity == 0


# ---------------------------------------------------------------------------
# TestNoForbiddenPatternsInSource
# ---------------------------------------------------------------------------

class TestNoForbiddenPatternsInSource:
    """Confirm the gate module performs no I/O and contains no actual
    broker/network/credential/order imports or calls."""

    def _source(self) -> str:
        return inspect.getsource(_gate_mod)

    def test_no_forbidden_imports(self):
        source = self._source()
        for pattern in (
            "import " + "requests",
            "import " + "urllib",
            "import " + "aiohttp",
            "import " + "alpaca",
            "import " + "socket",
            "import " + "subprocess",
            "import os",
            "from pathlib",
            "import pathlib",
        ):
            assert pattern not in source, f"forbidden import found: {pattern!r}"

    def test_no_broker_or_live_submit_references(self):
        source = self._source()
        for pattern in (
            "Alpaca" + "BrokerAdapter",
            "alpaca_" + "trade_api",
            "live_readiness_gate",
            "live_submit_enablement_gate",
        ):
            assert pattern not in source

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(", "Path" + "(",
            "read" + "_text(", "write" + "_text(",
            "read" + "_bytes(", "write" + "_bytes(",
        ):
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

    def test_no_network_or_env_calls(self):
        source = self._source()
        for pattern in (
            "requests.", "urllib.", "socket.",
            "os." + "environ[", "os." + "environ.get(", "getenv(",
        ):
            assert pattern not in source

    def test_no_actual_order_action_calls(self):
        source = self._source()
        for pattern in (
            "." + "submit_order(",
            "." + "place_order(",
            "broker.submit",
        ):
            assert pattern not in source

    def test_safety_flags_always_false_on_result(self):
        for scenario in (
            _run(),
            _run(approval={}),
            _run(plan={}),
            _run(state=None),
            _run(plan=_valid_plan(symbol="TSLA")),
        ):
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(scenario, flag) is False, (
                    f"{flag} should be False in scenario {scenario.gate_status}"
                )

    def test_fixed_safety_bools_always_true_on_result(self):
        for scenario in (
            _run(),
            _run(approval={}),
            _run(plan=_valid_plan(symbol="TSLA")),
        ):
            assert scenario.dry_run_required is True
            assert scenario.human_confirmation_required is True
            assert scenario.kill_switch_required is True

    def test_no_logging_in_source(self):
        source = self._source()
        assert "logging" not in source
        assert "logger" not in source


# ---------------------------------------------------------------------------
# TestForbiddenPatternsInThisTestModule
# ---------------------------------------------------------------------------

class TestForbiddenPatternsInThisTestModule:
    """Sanity check that this test module itself performs no file I/O and
    contains no actual broker/network/credential/order imports or calls."""

    def _source(self) -> str:
        import sys
        return inspect.getsource(sys.modules[__name__])

    def test_no_write_operations(self):
        source = self._source()
        write_patterns = (
            "write" + "_text(",
            "write" + "_bytes(",
            "os." + "makedirs(",
            "os." + "mkdir(",
        )
        for pat in write_patterns:
            assert source.count(pat) == 0, f"module contains write pattern {pat!r}"

    def test_no_forbidden_imports(self):
        source = self._source()
        for pattern in (
            "import " + "requests",
            "import " + "urllib",
            "import " + "aiohttp",
            "import " + "alpaca",
            "import " + "socket",
            "import " + "subprocess",
            "Alpaca" + "BrokerAdapter",
            "alpaca_" + "trade_api",
        ):
            assert pattern not in source

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open" + "(", "Path" + "(",
            "read" + "_text(", "write" + "_text(",
            "read" + "_bytes(", "write" + "_bytes(",
        ):
            assert pattern not in source, f"forbidden file I/O found: {pattern!r}"

    def test_no_actual_order_or_env_calls(self):
        source = self._source()
        for pattern in (
            "." + "submit_order(",
            "." + "place_order(",
            "os." + "environ[",
            "os." + "environ.get(",
        ):
            assert pattern not in source
