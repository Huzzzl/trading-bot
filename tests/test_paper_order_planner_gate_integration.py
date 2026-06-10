"""
tests/test_paper_order_planner_gate_integration.py
---------------------------------------------------
S31: Integration tests for the pure offline chain

    S30 create_paper_order_plan()
    -> S27 validate_paper_order_plan()
    -> S28 evaluate_paper_order_safety_gate()

Every scenario runs the REAL planner, the REAL S27 validator (both inside
the planner and standalone), and the REAL S28 gate. No mocked validators
are used anywhere in this module.

The chain proves sequencing: the planner must pass before standalone S27
validation runs, and standalone S27 validation must pass before the S28
gate runs. When the planner blocks, no plan is released (plan is None,
fail closed) and the downstream stages are never run.

All fixtures are plain in-memory dicts. No real approval artifact, order
plan, config, or any other artifact is created. No files are read or
written. No broker, network, credential, environment-variable, or order
access is made.

A planner-generated POP/1.0 plan is an in-memory paper order plan only --
it is not an order. Planner PASS is not order approval. S28
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
import src.research.paper_order_planner as _planner_mod
import src.research.paper_order_safety_gate as _gate_mod
from src.research.paper_order_plan_validator import (
    PaperOrderPlanValidationResult,
    validate_paper_order_plan,
)
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


def _run_chain(approval=None, signal=None, sizing=None, state=None, **planner_kwargs):
    """Run the full real chain and return (planner_result, validation_result,
    gate_result).

    Sequencing is enforced fail-closed: when the planner does not release a
    plan, standalone S27 validation and the S28 gate are never run and both
    downstream results are None.
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
        approval,
        signal_snapshot=signal,
        sizing_snapshot=sizing,
        **planner_kwargs,
    )
    if planner_result.plan is None:
        return planner_result, None, None

    validation_result = validate_paper_order_plan(planner_result.plan)
    if validation_result.result != "PASS":
        return planner_result, validation_result, None

    gate_result = evaluate_paper_order_safety_gate(
        approval, planner_result.plan, current_state=state
    )
    return planner_result, validation_result, gate_result


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

_EXPECTED_PLAN_KEYS: frozenset[str] = frozenset({
    "plan_schema_version", "plan_type", "plan_id",
    "candidate_id", "run_id", "source_git_sha",
    "approval_artifact_hash", "paper_config_hash", "simulation_result_hash",
    "generated_at_utc", "expires_at_utc",
    "symbol", "interval", "strategy_family", "holding_horizon",
    "side", "order_type", "quantity", "notional", "limit_price",
    "time_in_force", "allowed_session", "rationale",
    "signal_snapshot", "risk_snapshot", "approval_scope",
    "dry_run_required", "human_confirmation_required",
    "kill_switch_required", "safety_gate_required",
    "broker_calls_made", "credentials_read", "network_calls_made",
    "order_action_requested", "live_trading_allowed",
    "notes", "plan_status",
})


# ---------------------------------------------------------------------------
# 1, 3, 5 + sequencing: full chain PASS with a market order plan
# ---------------------------------------------------------------------------

class TestFullChainMarketPass:
    def test_stage1_planner_creates_plan(self):
        planner_result, _, _ = _run_chain()
        assert planner_result.result == "PASS"
        assert planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert isinstance(planner_result.plan, dict)

    def test_stage2_standalone_s27_validation_passes(self):
        _, validation_result, _ = _run_chain()
        assert isinstance(validation_result, PaperOrderPlanValidationResult)
        assert validation_result.result == "PASS"

    def test_stage3_gate_returns_pass_dry_run_only(self):
        _, _, gate_result = _run_chain()
        assert gate_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert gate_result.blocker is None

    def test_gate_runs_all_22_checks_on_planner_plan(self):
        _, _, gate_result = _run_chain()
        assert len(gate_result.checks_passed) == 22
        assert gate_result.checks_failed == ()

    def test_sequencing_planner_then_validator_then_gate(self):
        # The planner's own internal S27 validation passed, the standalone
        # S27 validation passed, and the gate re-ran both validators as its
        # first two checks -- three real validation layers in sequence.
        planner_result, validation_result, gate_result = _run_chain()
        assert planner_result.plan_validation_result.result == "PASS"
        assert validation_result.result == "PASS"
        assert gate_result.checks_passed[:2] == (
            "approval.validator", "plan.validator",
        )

    def test_gate_receives_exact_planner_plan_object(self):
        planner_result, _, _ = _run_chain()
        plan = planner_result.plan
        gate_result = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state()
        )
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert gate_result.plan_id == plan["plan_id"]
        assert gate_result.candidate_id == plan["candidate_id"]
        assert gate_result.run_id == plan["run_id"]

    def test_pass_is_in_memory_only_not_order_approval(self):
        # Planner PASS releases an in-memory dict only, and the gate's
        # PASS_DRY_RUN_ONLY is dry-run/no-submit clearance, never order
        # approval: every required-safety boolean stays True and every
        # safety flag stays False at all three stages.
        planner_result, validation_result, gate_result = _run_chain()
        assert isinstance(planner_result.plan, dict)
        assert planner_result.order_action_requested is False
        assert validation_result.live_trading_allowed is False
        assert gate_result.dry_run_required is True
        assert gate_result.human_confirmation_required is True
        assert gate_result.kill_switch_required is True
        assert gate_result.order_action_requested is False
        assert gate_result.live_trading_allowed is False


# ---------------------------------------------------------------------------
# 2: full chain PASS with a limit order plan
# ---------------------------------------------------------------------------

class TestFullChainLimitPass:
    def test_limit_chain_passes_all_three_stages(self):
        planner_result, validation_result, gate_result = _run_chain(
            signal=_limit_signal(), sizing=_limit_sizing()
        )
        assert planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert validation_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY

    def test_limit_plan_carries_limit_price_through_chain(self):
        planner_result, _, gate_result = _run_chain(
            signal=_limit_signal(), sizing=_limit_sizing()
        )
        assert planner_result.plan["order_type"] == "limit"
        assert planner_result.plan["limit_price"] == 150.0
        assert gate_result.result == "PASS"


# ---------------------------------------------------------------------------
# 4, 6: generated plan shape and provenance linkage before entering the gate
# ---------------------------------------------------------------------------

class TestGeneratedPlanShapeAndProvenance:
    def test_plan_has_exact_pop_fields_no_extras(self):
        planner_result, _, _ = _run_chain()
        assert set(planner_result.plan.keys()) == set(_EXPECTED_PLAN_KEYS)

    def test_plan_provenance_links_to_approval_artifact(self):
        approval = _valid_approval(
            candidate_id="cand-777",
            run_id="run-777",
            artifact_hash="hash-pta-777",
            paper_config_hash="hash-pc-777",
            simulation_result_hash="hash-sim-777",
        )
        planner_result, _, gate_result = _run_chain(approval=approval)
        plan = planner_result.plan
        assert plan["candidate_id"] == "cand-777"
        assert plan["run_id"] == "run-777"
        assert plan["approval_artifact_hash"] == "hash-pta-777"
        assert plan["paper_config_hash"] == "hash-pc-777"
        assert plan["simulation_result_hash"] == "hash-sim-777"
        # Because the linkage is exact, the gate's five cross-artifact
        # provenance checks all pass.
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        for check in (
            "provenance.candidate_id", "provenance.run_id",
            "provenance.approval_artifact_hash", "provenance.paper_config_hash",
            "provenance.simulation_result_hash",
        ):
            assert check in gate_result.checks_passed


# ---------------------------------------------------------------------------
# 7-9, 19, 20: planner blocks stop the chain before S27/S28
# ---------------------------------------------------------------------------

class TestPlannerBlocksStopChain:
    def test_blocked_approval_releases_no_plan_and_stops_chain(self):
        planner_result, validation_result, gate_result = _run_chain(
            approval=_valid_approval(approval_status="NOT_REVIEWED")
        )
        assert planner_result.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert planner_result.plan is None
        assert validation_result is None
        assert gate_result is None

    def test_blocked_signal_releases_no_plan_and_stops_chain(self):
        planner_result, validation_result, gate_result = _run_chain(
            signal=_valid_signal(symbol="TSLA")
        )
        assert planner_result.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert planner_result.plan is None
        assert validation_result is None
        assert gate_result is None

    def test_blocked_sizing_releases_no_plan_and_stops_chain(self):
        planner_result, validation_result, gate_result = _run_chain(
            sizing=_valid_sizing(max_daily_loss=3000.0)
        )
        assert planner_result.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert planner_result.plan is None
        assert validation_result is None
        assert gate_result is None

    def test_market_signal_against_limit_only_approval_blocks_at_planner(self):
        approval = _valid_approval(allowed_order_types=["limit"])
        planner_result, validation_result, gate_result = _run_chain(
            approval=approval, signal=_valid_signal(order_type="market")
        )
        assert planner_result.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert "signal.allowlists" in planner_result.criteria_failed
        assert planner_result.plan is None
        assert validation_result is None
        assert gate_result is None

    def test_notional_over_approval_cap_blocks_at_planner(self):
        sizing = _valid_sizing(notional=6000.0)
        planner_result, validation_result, gate_result = _run_chain(sizing=sizing)
        assert planner_result.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.risk_limits" in planner_result.criteria_failed
        assert planner_result.plan is None
        assert validation_result is None
        assert gate_result is None


# ---------------------------------------------------------------------------
# 10-17: planner-passed plans blocked (or passed) by gate-level state
# ---------------------------------------------------------------------------

class TestGateBlocksPlannerPassedPlans:
    def _assert_planner_and_validator_passed(self, planner_result, validation_result):
        # Each scenario here produces a structurally valid plan: the block
        # comes only from the gate's state checks.
        assert planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert validation_result.result == "PASS"

    def test_kill_switch_closed_blocked_by_gate(self):
        planner_result, validation_result, gate_result = _run_chain(
            state=_clean_state(kill_switch_open=False)
        )
        self._assert_planner_and_validator_passed(planner_result, validation_result)
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH
        assert "kill_switch.open" in gate_result.checks_failed

    def test_daily_order_count_at_cap_blocked_by_gate(self):
        planner_result, validation_result, gate_result = _run_chain(
            state=_clean_state(current_daily_order_count=5)
        )
        self._assert_planner_and_validator_passed(planner_result, validation_result)
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.daily_order_count" in gate_result.checks_failed
        assert gate_result.remaining_daily_order_capacity == 0

    def test_projected_daily_loss_above_cap_blocked_by_gate(self):
        # max(0, -(-600)) + 1500 notional = 2100 > max_daily_loss 2000.
        planner_result, validation_result, gate_result = _run_chain(
            state=_clean_state(current_daily_pnl=-600.0)
        )
        self._assert_planner_and_validator_passed(planner_result, validation_result)
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.daily_loss" in gate_result.checks_failed
        assert gate_result.projected_daily_loss_after_order == pytest.approx(2100.0)

    def test_projected_drawdown_above_cap_blocked_by_gate(self):
        # 0.4 + 1500/5000 = 0.7 > max_drawdown_stop 0.50.
        planner_result, validation_result, gate_result = _run_chain(
            state=_clean_state(current_drawdown=0.4)
        )
        self._assert_planner_and_validator_passed(planner_result, validation_result)
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT
        assert "risk.drawdown" in gate_result.checks_failed
        assert gate_result.projected_drawdown_after_order == pytest.approx(0.7)

    def test_duplicate_plan_id_blocked_by_gate(self):
        planner_result, validation_result, gate_result = _run_chain(
            state=_clean_state(processed_plan_ids=[_PLAN_ID])
        )
        self._assert_planner_and_validator_passed(planner_result, validation_result)
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE
        assert "duplicate.plan_id" in gate_result.checks_failed

    def test_open_same_candidate_symbol_position_blocked_by_gate(self):
        positions = [{"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}]
        planner_result, validation_result, gate_result = _run_chain(
            state=_clean_state(open_positions=positions)
        )
        self._assert_planner_and_validator_passed(planner_result, validation_result)
        assert (
            gate_result.gate_status
            is PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT
        )
        assert "position.conflict" in gate_result.checks_failed

    def test_pending_same_candidate_symbol_position_blocked_by_gate(self):
        positions = [{"symbol": "AAPL", "candidate_id": "cand-001", "status": "PENDING"}]
        _, _, gate_result = _run_chain(state=_clean_state(open_positions=positions))
        assert (
            gate_result.gate_status
            is PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT
        )

    def test_closed_same_candidate_symbol_position_still_passes_gate(self):
        positions = [{"symbol": "AAPL", "candidate_id": "cand-001", "status": "CLOSED"}]
        _, _, gate_result = _run_chain(state=_clean_state(open_positions=positions))
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert "position.conflict" in gate_result.checks_passed


# ---------------------------------------------------------------------------
# 18: multi-symbol approval allowlist
# ---------------------------------------------------------------------------

class TestMultiSymbolAllowlist:
    def test_second_allowed_symbol_passes_full_chain(self):
        approval = _valid_approval(allowed_symbols=["AAPL", "SPY"])
        planner_result, validation_result, gate_result = _run_chain(
            approval=approval, signal=_valid_signal(symbol="SPY")
        )
        assert planner_result.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert planner_result.plan["symbol"] == "SPY"
        assert validation_result.result == "PASS"
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert "allowlist.symbol" in gate_result.checks_passed


# ---------------------------------------------------------------------------
# 21-24: safety flags always False across all stages and scenarios
# ---------------------------------------------------------------------------

def _scenario_chains():
    """Full-chain results for one PASS and every blocked scenario family."""
    return [
        ("pass_market", _run_chain()),
        ("pass_limit", _run_chain(signal=_limit_signal(), sizing=_limit_sizing())),
        ("planner_blocked_approval",
         _run_chain(approval=_valid_approval(approval_status="DRAFT"))),
        ("planner_blocked_signal", _run_chain(signal=_valid_signal(symbol="TSLA"))),
        ("planner_blocked_sizing", _run_chain(sizing=_valid_sizing(notional=6000.0))),
        ("gate_blocked_kill_switch",
         _run_chain(state=_clean_state(kill_switch_open=False))),
        ("gate_blocked_risk",
         _run_chain(state=_clean_state(current_daily_order_count=5))),
        ("gate_blocked_duplicate",
         _run_chain(state=_clean_state(processed_plan_ids=[_PLAN_ID]))),
        ("gate_blocked_conflict",
         _run_chain(state=_clean_state(open_positions=[
             {"symbol": "AAPL", "candidate_id": "cand-001", "status": "OPEN"}
         ]))),
    ]


class TestSafetyFlagsAlwaysFalse:
    def test_planner_result_flags_false_in_all_scenarios(self):
        for label, (planner_result, _, _) in _scenario_chains():
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(planner_result, flag) is False, (
                    f"planner {flag} should be False in scenario {label}"
                )

    def test_validation_result_flags_false_when_validation_ran(self):
        for label, (_, validation_result, _) in _scenario_chains():
            if validation_result is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(validation_result, flag) is False, (
                    f"validator {flag} should be False in scenario {label}"
                )

    def test_gate_result_flags_false_when_gate_ran(self):
        for label, (_, _, gate_result) in _scenario_chains():
            if gate_result is None:
                continue
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(gate_result, flag) is False, (
                    f"gate {flag} should be False in scenario {label}"
                )

    def test_pass_chain_flags_false_at_every_stage(self):
        planner_result, validation_result, gate_result = _run_chain()
        for result in (planner_result, validation_result, gate_result):
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(result, flag) is False


# ---------------------------------------------------------------------------
# 25: inputs are not mutated across the whole chain
# ---------------------------------------------------------------------------

class TestInputsNotMutated:
    def test_pass_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state()
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_chain(approval=approval, signal=signal, sizing=sizing, state=state)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]

    def test_gate_blocked_chain_does_not_mutate_inputs(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        state = _clean_state(kill_switch_open=False)
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal),
            copy.deepcopy(sizing), copy.deepcopy(state),
        )
        _run_chain(approval=approval, signal=signal, sizing=sizing, state=state)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]
        assert state == snapshots[3]

    def test_planner_blocked_chain_does_not_mutate_inputs(self):
        approval = _valid_approval(live_trading_approved=True)
        signal = _valid_signal()
        sizing = _valid_sizing()
        snapshots = (copy.deepcopy(approval), copy.deepcopy(signal), copy.deepcopy(sizing))
        _run_chain(approval=approval, signal=signal, sizing=sizing)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]


# ---------------------------------------------------------------------------
# 26: determinism across the whole chain
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_planner_and_gate_results(self):
        first = _run_chain()
        second = _run_chain()
        assert first[0] == second[0]
        assert first[1] == second[1]
        assert first[2] == second[2]

    def test_same_blocked_inputs_same_results(self):
        state = _clean_state(kill_switch_open=False)
        first = _run_chain(state=state)
        second = _run_chain(state=_clean_state(kill_switch_open=False))
        assert first[0] == second[0]
        assert first[2] == second[2]


# ---------------------------------------------------------------------------
# 27: deep-copy isolation between caller snapshots and the gated plan
# ---------------------------------------------------------------------------

class TestDeepCopyIsolation:
    def test_mutating_originals_after_planner_does_not_mutate_gated_plan(self):
        signal = _valid_signal()
        sizing = _valid_sizing()
        planner_result = create_paper_order_plan(
            _valid_approval(),
            signal_snapshot=signal,
            sizing_snapshot=sizing,
            generated_at_utc=_GENERATED_AT,
            expires_at_utc=_EXPIRES_AT,
            plan_id=_PLAN_ID,
            source_git_sha=_SOURCE_SHA,
        )
        plan = planner_result.plan
        signal["symbol"] = "TSLA"
        signal["side"] = "SELL"
        sizing["notional"] = 999999.0
        assert plan["signal_snapshot"]["symbol"] == "AAPL"
        assert plan["signal_snapshot"]["side"] == "BUY"
        assert plan["risk_snapshot"]["notional"] == 1500.0
        # The unchanged plan still passes the real validator and gate.
        assert validate_paper_order_plan(plan).result == "PASS"
        gate_result = evaluate_paper_order_safety_gate(
            _valid_approval(), plan, current_state=_clean_state()
        )
        assert gate_result.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY


# ---------------------------------------------------------------------------
# 28: the chain creates no real artifact files
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
            _, _, pass_gate = _run_chain()
            blocked_planner, _, _ = _run_chain(signal=_valid_signal(symbol="TSLA"))
        assert calls == []
        assert pass_gate.gate_status is PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY
        assert (
            blocked_planner.planner_status
            is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        )


# ---------------------------------------------------------------------------
# 30: the source modules in the chain are clean
# ---------------------------------------------------------------------------

_CHAIN_MODULES = [_planner_mod, _plan_mod, _approval_mod, _gate_mod]
_CHAIN_MODULE_IDS = [
    "s30_planner", "s27_plan_validator", "s25_approval_validator", "s28_safety_gate",
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
# 29: this test module itself is clean
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

    def test_no_order_env_or_broker_calls(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der(",
            "place_or" + "der(",
            "cancel_or" + "der(",
            "modify_or" + "der(",
            "live_su" + "bmit(",
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
