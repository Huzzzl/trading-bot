"""
tests/test_paper_order_plan_validator.py
-----------------------------------------
S27: Tests for the pure offline paper order plan validator.

All fixtures are plain in-memory dicts. No real order plan artifacts are
created. No files are read or written. No broker, network, credential,
environment-variable, subprocess, or order access is made. Validator PASS
means only that the plan's shape and scope are valid for future safety-gate
review -- not order submission approval or paper/live trading approval.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import fields as dataclass_fields

import pytest

import src.research.paper_order_plan_validator as _validator_mod
from src.research.paper_order_plan_validator import (
    PaperOrderPlanStatus,
    PaperOrderPlanValidationResult,
    validate_paper_order_plan,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _valid_plan(**overrides) -> dict:
    """Return a complete valid POP/1.0 plan dict. Override any field."""
    base = {
        "plan_schema_version": "POP/1.0",
        "plan_type": "PAPER_ORDER_PLAN",
        "plan_id": "plan-001",
        "candidate_id": "cand-001",
        "run_id": "run-001",
        "source_git_sha": "abc123def456",
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
        "signal_snapshot": {"signal": "BUY", "confidence": 0.85, "bar_close": 150.0},
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


def _run(plan: dict | None = None) -> PaperOrderPlanValidationResult:
    if plan is None:
        plan = _valid_plan()
    return validate_paper_order_plan(plan)


# ---------------------------------------------------------------------------
# Enum and dataclass shape
# ---------------------------------------------------------------------------

class TestPaperOrderPlanStatusEnum:
    def test_enum_values_present(self):
        expected = {
            "NOT_PLANNED", "PLAN_DRAFT", "PLAN_READY_FOR_SAFETY_GATE",
            "PLAN_REJECTED_SCHEMA", "PLAN_BLOCKED_PROVENANCE",
            "PLAN_BLOCKED_RISK", "PLAN_BLOCKED_SAFETY", "PLAN_EXPIRED",
        }
        actual = {m.value for m in PaperOrderPlanStatus}
        assert actual == expected

    def test_enum_is_str_subclass(self):
        assert issubclass(PaperOrderPlanStatus, str)

    def test_enum_member_equals_string(self):
        assert PaperOrderPlanStatus.PLAN_READY_FOR_SAFETY_GATE == "PLAN_READY_FOR_SAFETY_GATE"
        assert PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY == "PLAN_BLOCKED_SAFETY"
        assert PaperOrderPlanStatus.NOT_PLANNED == "NOT_PLANNED"

    def test_enum_count(self):
        assert len(PaperOrderPlanStatus) == 8


class TestPaperOrderPlanValidationResultDataclass:
    def test_is_frozen(self):
        r = _run()
        with pytest.raises((TypeError, AttributeError)):
            r.result = "BLOCKED"  # type: ignore[misc]

    def test_required_fields_present(self):
        field_names = {f.name for f in dataclass_fields(PaperOrderPlanValidationResult)}
        required = {
            "result", "blocker", "plan_id", "candidate_id", "run_id",
            "status", "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert required <= field_names

    def test_criteria_are_tuples(self):
        r = _run()
        assert isinstance(r.criteria_checked, tuple)
        assert isinstance(r.criteria_failed, tuple)

    def test_status_is_enum(self):
        r = _run()
        assert isinstance(r.status, PaperOrderPlanStatus)


# ---------------------------------------------------------------------------
# Valid plan passes
# ---------------------------------------------------------------------------

class TestValidPlanPasses:
    def test_valid_market_plan_passes(self):
        r = _run()
        assert r.result == "PASS"
        assert r.status == PaperOrderPlanStatus.PLAN_READY_FOR_SAFETY_GATE
        assert r.blocker is None

    def test_valid_limit_plan_passes(self):
        r = _run(_valid_plan(order_type="limit", limit_price=148.50))
        assert r.result == "PASS"
        assert r.blocker is None

    def test_pass_returns_correct_ids(self):
        r = _run()
        assert r.plan_id == "plan-001"
        assert r.candidate_id == "cand-001"
        assert r.run_id == "run-001"

    def test_pass_all_safety_flags_false(self):
        r = _run()
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_pass_does_not_approve_paper_trading(self):
        r = _run()
        assert r.result == "PASS"
        # PASS means shape/scope valid for safety-gate review only
        assert r.live_trading_allowed is False
        assert r.order_action_requested is False

    def test_pass_criteria_failed_empty(self):
        r = _run()
        assert r.criteria_failed == ()

    def test_valid_sell_plan_passes(self):
        r = _run(_valid_plan(side="SELL"))
        assert r.result == "PASS"

    def test_z_suffix_generated_at_passes(self):
        r = _run(_valid_plan(
            generated_at_utc="2025-02-01T10:00:00Z",
            expires_at_utc="2025-02-01T17:00:00Z",
        ))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# Schema version and plan type
# ---------------------------------------------------------------------------

class TestSchemaAndPlanType:
    def test_missing_schema_version_rejected(self):
        plan = _valid_plan()
        del plan["plan_schema_version"]
        r = _run(plan)
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "schema.plan" in r.criteria_failed

    def test_unsupported_schema_version_rejected(self):
        r = _run(_valid_plan(plan_schema_version="POP/2.0"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA

    def test_wrong_plan_type_rejected(self):
        r = _run(_valid_plan(plan_type="LIVE_ORDER_PLAN"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "plan.type" in r.criteria_failed

    def test_wrong_approval_scope_blocked_provenance(self):
        r = _run(_valid_plan(approval_scope="LIVE_TRADING_SCOPE"))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "approval.scope" in r.criteria_failed


# ---------------------------------------------------------------------------
# Fixed safety bool fields
# ---------------------------------------------------------------------------

class TestFixedSafetyBoolFields:
    @pytest.mark.parametrize("field", [
        "dry_run_required",
        "human_confirmation_required",
        "kill_switch_required",
        "safety_gate_required",
    ])
    def test_required_true_flag_false_is_blocked_safety(self, field):
        r = _run(_valid_plan(**{field: False}))
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_required_false_flag_true_is_blocked_safety(self, field):
        r = _run(_valid_plan(**{field: True}))
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY

    def test_safety_gate_required_false_is_blocked_safety(self):
        r = _run(_valid_plan(safety_gate_required=False))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY
        assert "safety.safety_gate_required" in r.criteria_failed


# ---------------------------------------------------------------------------
# Provenance and evidence fields
# ---------------------------------------------------------------------------

class TestProvenanceFields:
    @pytest.mark.parametrize("field,criterion", [
        ("plan_id",                 "plan.plan_id"),
        ("candidate_id",            "provenance.candidate_id"),
        ("run_id",                  "provenance.run_id"),
        ("source_git_sha",          "provenance.source_git_sha"),
        ("approval_artifact_hash",  "evidence.approval_artifact_hash"),
        ("paper_config_hash",       "evidence.paper_config_hash"),
        ("simulation_result_hash",  "evidence.simulation_result_hash"),
    ])
    def test_missing_field_blocked_provenance(self, field, criterion):
        plan = _valid_plan()
        del plan[field]
        r = _run(plan)
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert criterion in r.criteria_failed

    @pytest.mark.parametrize("field", [
        "plan_id", "candidate_id", "run_id", "source_git_sha",
        "approval_artifact_hash", "paper_config_hash", "simulation_result_hash",
    ])
    def test_empty_string_blocked_provenance(self, field):
        r = _run(_valid_plan(**{field: ""}))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE

    def test_missing_plan_id_returns_none_in_result(self):
        plan = _valid_plan()
        del plan["plan_id"]
        r = _run(plan)
        assert r.plan_id is None


# ---------------------------------------------------------------------------
# Datetime fields
# ---------------------------------------------------------------------------

class TestDatetimeFields:
    def test_invalid_generated_at_utc_blocked_provenance(self):
        r = _run(_valid_plan(generated_at_utc="not-a-datetime"))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "time.generated_at_utc" in r.criteria_failed

    def test_invalid_expires_at_utc_blocked_provenance(self):
        r = _run(_valid_plan(expires_at_utc="2025-01-99T00:00:00Z"))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE

    def test_expires_before_generated_blocked_provenance(self):
        r = _run(_valid_plan(
            generated_at_utc="2025-01-15T17:00:00Z",
            expires_at_utc="2025-01-15T09:00:00Z",
        ))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "time.expires_at_utc" in r.criteria_failed

    def test_expires_equal_generated_blocked_provenance(self):
        r = _run(_valid_plan(
            generated_at_utc="2025-01-15T09:00:00Z",
            expires_at_utc="2025-01-15T09:00:00Z",
        ))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE

    def test_z_suffix_is_valid(self):
        r = _run(_valid_plan(
            generated_at_utc="2025-01-15T09:00:00Z",
            expires_at_utc="2025-01-15T17:00:00Z",
        ))
        assert r.result == "PASS"

    def test_missing_generated_at_blocked_provenance(self):
        plan = _valid_plan()
        del plan["generated_at_utc"]
        r = _run(plan)
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE


# ---------------------------------------------------------------------------
# Strategy identity fields
# ---------------------------------------------------------------------------

class TestStrategyIdentityFields:
    @pytest.mark.parametrize("field,criterion", [
        ("symbol",          "strategy.symbol"),
        ("interval",        "strategy.interval"),
        ("strategy_family", "strategy.strategy_family"),
        ("holding_horizon", "strategy.holding_horizon"),
    ])
    def test_missing_field_blocked_provenance(self, field, criterion):
        plan = _valid_plan()
        del plan[field]
        r = _run(plan)
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert criterion in r.criteria_failed


# ---------------------------------------------------------------------------
# Order intent fields
# ---------------------------------------------------------------------------

class TestOrderIntentFields:
    def test_unsupported_side_rejected_schema(self):
        r = _run(_valid_plan(side="HOLD"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "order.side" in r.criteria_failed

    def test_unsupported_order_type_rejected_schema(self):
        r = _run(_valid_plan(order_type="stop"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "order.order_type" in r.criteria_failed

    def test_unsupported_time_in_force_rejected_schema(self):
        r = _run(_valid_plan(time_in_force="gtc"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "order.time_in_force" in r.criteria_failed

    def test_unsupported_session_rejected_schema(self):
        r = _run(_valid_plan(allowed_session="extended"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "order.allowed_session" in r.criteria_failed

    def test_quantity_zero_blocked_risk(self):
        r = _run(_valid_plan(quantity=0.0))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "order.quantity" in r.criteria_failed

    def test_quantity_negative_blocked_risk(self):
        r = _run(_valid_plan(quantity=-5.0))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK

    def test_notional_zero_blocked_risk(self):
        r = _run(_valid_plan(notional=0.0))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "order.notional" in r.criteria_failed

    def test_notional_negative_blocked_risk(self):
        r = _run(_valid_plan(notional=-100.0))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK

    def test_limit_order_missing_limit_price_blocked_risk(self):
        r = _run(_valid_plan(order_type="limit", limit_price=None))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "order.limit_price" in r.criteria_failed

    def test_market_order_with_limit_price_blocked_risk(self):
        r = _run(_valid_plan(order_type="market", limit_price=150.0))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "order.limit_price" in r.criteria_failed

    def test_limit_order_with_valid_limit_price_passes(self):
        r = _run(_valid_plan(order_type="limit", limit_price=148.50))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# Narrative and snapshot fields
# ---------------------------------------------------------------------------

class TestNarrativeAndSnapshotFields:
    def test_missing_rationale_blocked_provenance(self):
        plan = _valid_plan()
        del plan["rationale"]
        r = _run(plan)
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "order.rationale" in r.criteria_failed

    def test_empty_rationale_blocked_provenance(self):
        r = _run(_valid_plan(rationale=""))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE

    def test_missing_signal_snapshot_blocked_provenance(self):
        plan = _valid_plan()
        del plan["signal_snapshot"]
        r = _run(plan)
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "order.signal_snapshot" in r.criteria_failed

    def test_empty_signal_snapshot_blocked_provenance(self):
        r = _run(_valid_plan(signal_snapshot={}))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE

    def test_missing_risk_snapshot_blocked_provenance(self):
        plan = _valid_plan()
        del plan["risk_snapshot"]
        r = _run(plan)
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "risk.snapshot" in r.criteria_failed

    def test_empty_risk_snapshot_blocked_provenance(self):
        r = _run(_valid_plan(risk_snapshot={}))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE

    def test_missing_notes_blocked_provenance(self):
        plan = _valid_plan()
        del plan["notes"]
        r = _run(plan)
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE
        assert "plan.notes" in r.criteria_failed


# ---------------------------------------------------------------------------
# Risk snapshot constraints
# ---------------------------------------------------------------------------

class TestRiskSnapshotConstraints:
    def test_max_position_fraction_above_cap_blocked_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_position_fraction"] = 0.15
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "risk.max_position_fraction" in r.criteria_failed

    def test_max_position_fraction_at_cap_passes(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_position_fraction"] = 0.10
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.result == "PASS"

    def test_max_drawdown_stop_above_cap_blocked_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_drawdown_stop"] = 1.5
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "risk.max_drawdown_stop" in r.criteria_failed

    def test_max_drawdown_stop_at_cap_passes(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_drawdown_stop"] = 1.0
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.result == "PASS"

    def test_max_orders_per_day_above_cap_blocked_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_orders_per_day"] = 11
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "risk.max_orders_per_day" in r.criteria_failed

    def test_max_orders_per_day_at_cap_passes(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_orders_per_day"] = 10
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.result == "PASS"

    def test_notional_exceeds_max_notional_per_position_blocked_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 1000.0
        r = _run(_valid_plan(notional=1500.0, risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK
        assert "risk.max_notional_per_position" in r.criteria_failed

    def test_notional_at_max_notional_per_position_passes(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["max_notional_per_position"] = 1500.0
        r = _run(_valid_plan(notional=1500.0, risk_snapshot=snap))
        assert r.result == "PASS"

    def test_missing_max_position_fraction_blocked_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        del snap["max_position_fraction"]
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK

    def test_missing_max_daily_loss_blocked_risk(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        del snap["max_daily_loss"]
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_RISK


# ---------------------------------------------------------------------------
# Plan status field
# ---------------------------------------------------------------------------

class TestPlanStatus:
    def test_not_planned_returns_blocked(self):
        r = _run(_valid_plan(plan_status="NOT_PLANNED"))
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.NOT_PLANNED

    def test_plan_draft_returns_blocked(self):
        r = _run(_valid_plan(plan_status="PLAN_DRAFT"))
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_DRAFT

    def test_invalid_status_rejected_schema(self):
        r = _run(_valid_plan(plan_status="INVALID_STATUS"))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA
        assert "plan.status" in r.criteria_failed

    def test_none_status_rejected_schema(self):
        r = _run(_valid_plan(plan_status=None))
        assert r.status == PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA

    def test_plan_expired_returns_blocked(self):
        r = _run(_valid_plan(plan_status="PLAN_EXPIRED"))
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_EXPIRED


# ---------------------------------------------------------------------------
# Forbidden field/value scan
# ---------------------------------------------------------------------------

class TestForbiddenFieldValueScan:
    @pytest.mark.parametrize("forbidden_text", [
        "api_key",
        "secret_key",
        "api_secret",
        "auth_token",
        "password",
        "credential",
        "broker_secret",
        "account_number",
        "live_account_id",
        "production_account",
        "broker_account_id",
        "env:",
        "os.environ",
        "submit_order",
        "place_order",
        "live_submit",
        "paper_trading_approved",
        "live_trading_approved",
        "approved_for_live_trading",
    ])
    def test_forbidden_substring_in_notes_blocked_safety(self, forbidden_text):
        r = _run(_valid_plan(notes=f"please ignore {forbidden_text} in rationale"))
        assert r.result == "BLOCKED"
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY
        assert "forbidden.scan" in r.criteria_failed

    def test_forbidden_key_in_signal_snapshot_blocked_safety(self):
        snap = {"signal": "BUY", "api_key": "hidden"}
        r = _run(_valid_plan(signal_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY

    def test_forbidden_nested_in_risk_snapshot_blocked_safety(self):
        snap = _valid_plan()["risk_snapshot"].copy()
        snap["debug"] = {"inner": "account_number: 123456"}
        r = _run(_valid_plan(risk_snapshot=snap))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY

    def test_disguised_forbidden_text_in_rationale_blocked_safety(self):
        r = _run(_valid_plan(rationale="set live_trading_approved=true to enable"))
        assert r.status == PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY

    def test_fixed_false_safety_flag_keys_allowed(self):
        # broker_calls_made, credentials_read, etc. are legitimate keys
        # in the plan schema when their values are exactly False.
        r = _run()
        assert r.result == "PASS"

    def test_harmless_words_allowed(self):
        r = _run(_valid_plan(
            rationale="standard paper market order plan for trading signal",
            notes="approval obtained; plan type is order_type market",
        ))
        assert r.result == "PASS"

    def test_plan_ready_for_safety_gate_value_not_rejected(self):
        # The string "PLAN_READY_FOR_SAFETY_GATE" contains harmless substrings
        r = _run(_valid_plan(plan_status="PLAN_READY_FOR_SAFETY_GATE"))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# Criteria determinism
# ---------------------------------------------------------------------------

class TestCriteriaDeterminism:
    def test_criteria_checked_is_always_same_length(self):
        r1 = _run()
        r2 = _run(_valid_plan(plan_id="other-plan"))
        assert len(r1.criteria_checked) == len(r2.criteria_checked)

    def test_criteria_checked_same_order_for_pass_vs_blocked(self):
        r_pass = _run()
        r_blocked = _run(_valid_plan(notes=""))
        assert r_pass.criteria_checked == r_blocked.criteria_checked

    def test_criteria_checked_same_for_repeated_calls(self):
        plan = _valid_plan()
        r1 = _run(plan)
        r2 = _run(plan)
        assert r1.criteria_checked == r2.criteria_checked

    def test_expected_criteria_names_present(self):
        r = _run()
        checked = set(r.criteria_checked)
        expected_subset = {
            "schema.plan", "plan.type", "plan.plan_id",
            "provenance.candidate_id", "provenance.run_id",
            "evidence.approval_artifact_hash",
            "time.generated_at_utc", "time.expires_at_utc",
            "strategy.symbol", "strategy.interval",
            "order.side", "order.order_type", "order.quantity",
            "order.notional", "order.limit_price",
            "order.time_in_force", "order.allowed_session",
            "order.rationale", "order.signal_snapshot",
            "risk.snapshot", "risk.max_position_fraction",
            "risk.max_daily_loss", "risk.max_drawdown_stop",
            "risk.max_orders_per_day", "risk.max_notional_per_position",
            "approval.scope", "plan.status", "plan.notes",
            "safety.dry_run_required", "safety.human_confirmation_required",
            "safety.kill_switch_required", "safety.safety_gate_required",
            "safety.broker_calls_made", "safety.credentials_read",
            "safety.network_calls_made", "safety.order_action_requested",
            "safety.live_trading_allowed",
            "forbidden.scan",
        }
        assert expected_subset <= checked


# ---------------------------------------------------------------------------
# Pure function properties
# ---------------------------------------------------------------------------

class TestPureFunction:
    def test_does_not_mutate_input(self):
        plan = _valid_plan()
        original = copy.deepcopy(plan)
        _run(plan)
        assert plan == original

    def test_same_input_same_output(self):
        plan = _valid_plan()
        r1 = validate_paper_order_plan(plan)
        r2 = validate_paper_order_plan(plan)
        assert r1 == r2

    def test_independent_calls_independent_results(self):
        r1 = _run(_valid_plan(plan_id="plan-A"))
        r2 = _run(_valid_plan(plan_id="plan-B"))
        assert r1.plan_id == "plan-A"
        assert r2.plan_id == "plan-B"


# ---------------------------------------------------------------------------
# Source-level forbidden pattern checks on the validator module
# ---------------------------------------------------------------------------

class TestNoForbiddenPatternsInSource:
    """Confirm the validator module performs no I/O and contains no actual
    broker/network/credential/order imports or calls. Forbidden words may
    appear as scan-list string literals (the validator's own
    _FORBIDDEN_SUBSTRINGS tuple) -- never as actual imports or calls."""

    def _source(self) -> str:
        return inspect.getsource(_validator_mod)

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

    def test_forbidden_substrings_present_only_as_scan_literals(self):
        # The validator legitimately contains "submit_order", "place_order",
        # "os.environ", and "live_submit" inside its _FORBIDDEN_SUBSTRINGS
        # scan tuple -- confirm they exist there as literals and that no
        # actual usage pattern is present.
        source = self._source()
        assert '"submit_order"' in source
        assert '"place_order"' in source
        assert '"os.environ"' in source
        assert '"live_submit"' in source
        assert "import os" not in source
        assert "." + "submit_order(" not in source
        assert "." + "place_order(" not in source

    def test_no_logging_of_secrets(self):
        source = self._source()
        assert "logging" not in source
        assert "logger" not in source


# ---------------------------------------------------------------------------
# Source-level forbidden pattern checks on this test module
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
