"""
tests/test_paper_order_planner.py
----------------------------------
S30: Tests for the pure offline paper order planner.

All fixtures are plain in-memory dicts. No real artifacts are created.
No files are read or written. No broker, network, credential, environment-
variable, or order access is made.

A paper order plan is not an order. Planner PASS (PLAN_CREATED) means only
that an in-memory POP/1.0 plan dict was created and passed S27 validation
-- it is not, and can never be, order approval or paper/live trading
approval. Paper trading remains not approved; live trading remains blocked.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import fields as dataclass_fields

import pytest

import src.research.paper_order_planner as _planner_mod
from src.research.paper_order_plan_validator import (
    PaperOrderPlanStatus,
    PaperOrderPlanValidationResult,
)
from src.research.paper_order_planner import (
    PaperOrderPlannerResult,
    PaperOrderPlannerStatus,
    create_paper_order_plan,
)

# ---------------------------------------------------------------------------
# Fixture helpers
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


def _run(
    approval=None,
    signal=None,
    sizing=None,
    **kwargs,
) -> PaperOrderPlannerResult:
    if approval is None:
        approval = _valid_approval()
    if signal is None:
        signal = _valid_signal()
    if sizing is None:
        sizing = _valid_sizing()
    kwargs.setdefault("generated_at_utc", _GENERATED_AT)
    kwargs.setdefault("expires_at_utc", _EXPIRES_AT)
    kwargs.setdefault("plan_id", _PLAN_ID)
    kwargs.setdefault("source_git_sha", _SOURCE_SHA)
    return create_paper_order_plan(
        approval,
        signal_snapshot=signal,
        sizing_snapshot=sizing,
        **kwargs,
    )


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

_EXPECTED_PASS_CRITERIA: tuple[str, ...] = (
    "approval.schema",
    "approval.type",
    "approval.scope",
    "approval.status",
    "approval.identity",
    "approval.evidence_hashes",
    "approval.allowlists",
    "approval.risk_limits",
    "approval.safety_flags",
    "signal.schema",
    "signal.allowlists",
    "signal.intent",
    "sizing.schema",
    "sizing.risk_limits",
    "plan.constructed",
    "plan.validation",
    "safety.result_flags",
)


# ---------------------------------------------------------------------------
# Mock validators for injection-seam tests
# ---------------------------------------------------------------------------

def _mock_validator_blocked(plan) -> PaperOrderPlanValidationResult:
    return PaperOrderPlanValidationResult(
        result="BLOCKED",
        blocker="mock blocked",
        plan_id=plan.get("plan_id"),
        candidate_id=plan.get("candidate_id"),
        run_id=plan.get("run_id"),
        status=PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE,
        criteria_checked=("plan.validation",),
        criteria_failed=("plan.validation",),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _mock_validator_safety_flag_true(plan) -> PaperOrderPlanValidationResult:
    return PaperOrderPlanValidationResult(
        result="PASS",
        blocker=None,
        plan_id=plan.get("plan_id"),
        candidate_id=plan.get("candidate_id"),
        run_id=plan.get("run_id"),
        status=PaperOrderPlanStatus.PLAN_READY_FOR_SAFETY_GATE,
        criteria_checked=("plan.validation",),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=True,  # safety flag True
        live_trading_allowed=False,
    )


def _raising_validator(plan):
    raise RuntimeError("validator exploded")


# ---------------------------------------------------------------------------
# TestPlannerStatusEnum
# ---------------------------------------------------------------------------

class TestPlannerStatusEnum:
    def test_all_expected_values_present(self):
        expected = {
            "NOT_PLANNED",
            "PLAN_CREATED",
            "BLOCKED_APPROVAL",
            "BLOCKED_SIGNAL",
            "BLOCKED_SIZING",
            "BLOCKED_VALIDATION",
            "BLOCKED_SAFETY",
            "ERROR_PLANNER",
        }
        actual = {m.value for m in PaperOrderPlannerStatus}
        assert actual == expected

    def test_enum_is_str_subclass(self):
        for member in PaperOrderPlannerStatus:
            assert isinstance(member, str)
            assert member == member.value


# ---------------------------------------------------------------------------
# TestPlannerResultDataclass
# ---------------------------------------------------------------------------

class TestPlannerResultDataclass:
    def test_result_is_frozen(self):
        r = _run()
        with pytest.raises(AttributeError):
            r.result = "tampered"  # type: ignore[misc]

    def test_result_has_expected_fields(self):
        field_names = {f.name for f in dataclass_fields(PaperOrderPlannerResult)}
        expected = {
            "result", "blocker", "planner_status",
            "plan", "plan_validation_result",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_criteria_are_tuples(self):
        r = _run()
        assert isinstance(r.criteria_checked, tuple)
        assert isinstance(r.criteria_failed, tuple)


# ---------------------------------------------------------------------------
# TestValidMarketPlan
# ---------------------------------------------------------------------------

class TestValidMarketPlan:
    def test_pass_result_and_status(self):
        r = _run()
        assert r.result == "PASS"
        assert r.planner_status is PaperOrderPlannerStatus.PLAN_CREATED
        assert r.blocker is None

    def test_plan_is_populated(self):
        r = _run()
        assert isinstance(r.plan, dict)

    def test_real_s27_validator_passes_generated_plan(self):
        r = _run()
        assert isinstance(r.plan_validation_result, PaperOrderPlanValidationResult)
        assert r.plan_validation_result.result == "PASS"
        assert (
            r.plan_validation_result.status
            is PaperOrderPlanStatus.PLAN_READY_FOR_SAFETY_GATE
        )

    def test_all_criteria_checked_in_order_none_failed(self):
        r = _run()
        assert r.criteria_checked == _EXPECTED_PASS_CRITERIA
        assert r.criteria_failed == ()


# ---------------------------------------------------------------------------
# TestValidLimitPlan
# ---------------------------------------------------------------------------

class TestValidLimitPlan:
    def test_limit_plan_passes(self):
        r = _run(signal=_limit_signal(), sizing=_limit_sizing())
        assert r.result == "PASS"
        assert r.planner_status is PaperOrderPlannerStatus.PLAN_CREATED

    def test_limit_plan_has_limit_price(self):
        r = _run(signal=_limit_signal(), sizing=_limit_sizing())
        assert r.plan["order_type"] == "limit"
        assert r.plan["limit_price"] == 150.0

    def test_limit_plan_validator_passes(self):
        r = _run(signal=_limit_signal(), sizing=_limit_sizing())
        assert r.plan_validation_result.result == "PASS"


# ---------------------------------------------------------------------------
# TestGeneratedPlanContents
# ---------------------------------------------------------------------------

class TestGeneratedPlanContents:
    def test_plan_has_exactly_expected_keys_no_extras(self):
        r = _run()
        assert set(r.plan.keys()) == set(_EXPECTED_PLAN_KEYS)

    def test_fixed_schema_fields(self):
        r = _run()
        assert r.plan["plan_schema_version"] == "POP/1.0"
        assert r.plan["plan_type"] == "PAPER_ORDER_PLAN"
        assert r.plan["approval_scope"] == "PAPER_TRADING_LIMITED_RUN_ONLY"
        assert r.plan["time_in_force"] == "day"
        assert r.plan["plan_status"] == "PLAN_READY_FOR_SAFETY_GATE"
        assert r.plan["notes"] == "generated by pure offline paper order planner"

    def test_provenance_fields_come_from_approval(self):
        approval = _valid_approval(
            candidate_id="cand-777",
            run_id="run-777",
            artifact_hash="hash-pta-777",
            paper_config_hash="hash-pc-777",
            simulation_result_hash="hash-sim-777",
        )
        r = _run(approval=approval)
        assert r.plan["candidate_id"] == "cand-777"
        assert r.plan["run_id"] == "run-777"
        assert r.plan["approval_artifact_hash"] == "hash-pta-777"
        assert r.plan["paper_config_hash"] == "hash-pc-777"
        assert r.plan["simulation_result_hash"] == "hash-sim-777"
        assert r.plan["allowed_session"] == "regular"

    def test_caller_provenance_fields_copied_verbatim(self):
        r = _run(
            plan_id="plan-xyz",
            source_git_sha="f" * 40,
            generated_at_utc="2025-02-01T10:00:00Z",
            expires_at_utc="2025-02-01T16:00:00Z",
        )
        assert r.plan["plan_id"] == "plan-xyz"
        assert r.plan["source_git_sha"] == "f" * 40
        assert r.plan["generated_at_utc"] == "2025-02-01T10:00:00Z"
        assert r.plan["expires_at_utc"] == "2025-02-01T16:00:00Z"

    def test_signal_fields_come_from_signal_snapshot(self):
        signal = _valid_signal(side="SELL", rationale="exit on trend reversal")
        r = _run(signal=signal)
        assert r.plan["symbol"] == "AAPL"
        assert r.plan["interval"] == "60m"
        assert r.plan["strategy_family"] == "TREND_BREAKOUT"
        assert r.plan["holding_horizon"] == "intraday"
        assert r.plan["side"] == "SELL"
        assert r.plan["order_type"] == "market"
        assert r.plan["rationale"] == "exit on trend reversal"
        assert r.plan["signal_snapshot"] == signal

    def test_sizing_fields_come_from_sizing_snapshot(self):
        sizing = _valid_sizing(quantity=7.0, notional=900.0)
        r = _run(sizing=sizing)
        assert r.plan["quantity"] == 7.0
        assert r.plan["notional"] == 900.0
        assert r.plan["limit_price"] is None
        assert r.plan["risk_snapshot"] == sizing

    def test_plan_safety_flags_all_false(self):
        r = _run()
        for flag in _SAFETY_FLAG_NAMES:
            assert r.plan[flag] is False, f"plan {flag} should be False"

    def test_plan_required_booleans_all_true(self):
        r = _run()
        assert r.plan["dry_run_required"] is True
        assert r.plan["human_confirmation_required"] is True
        assert r.plan["kill_switch_required"] is True
        assert r.plan["safety_gate_required"] is True


# ---------------------------------------------------------------------------
# TestPlannerResultSafetyFlags
# ---------------------------------------------------------------------------

class TestPlannerResultSafetyFlags:
    def test_safety_flags_false_across_scenarios(self):
        scenarios = (
            ("pass", _run()),
            ("blocked_approval", _run(approval={})),
            ("blocked_safety", _run(approval=_valid_approval(live_trading_approved=True))),
            ("blocked_signal", _run(signal={})),
            ("blocked_sizing", _run(sizing={})),
            ("blocked_validation", _run(_plan_validator=_mock_validator_blocked)),
            ("error_planner", _run(_plan_validator=_raising_validator)),
        )
        for label, r in scenarios:
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(r, flag) is False, (
                    f"{flag} should be False in scenario {label}"
                )

    def test_pass_is_not_order_approval(self):
        r = _run()
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False
        assert r.broker_calls_made is False


# ---------------------------------------------------------------------------
# TestApprovalBlocked
# ---------------------------------------------------------------------------

class TestApprovalBlocked:
    @pytest.mark.parametrize(
        ("override", "expected_criterion"),
        [
            ({"artifact_schema_version": "PTA/2.0"}, "approval.schema"),
            ({"approval_artifact_type": "LIVE_TRADING_APPROVAL"}, "approval.type"),
            ({"approval_scope": "LIVE_TRADING_FULL"}, "approval.scope"),
            ({"approval_status": "NOT_REVIEWED"}, "approval.status"),
            ({"approval_status": "DRAFT"}, "approval.status"),
        ],
        ids=["schema", "type", "scope", "status_not_reviewed", "status_draft"],
    )
    def test_wrong_fixed_values_blocked_approval(self, override, expected_criterion):
        r = _run(approval=_valid_approval(**override))
        assert r.result == "BLOCKED"
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert expected_criterion in r.criteria_failed
        assert r.plan is None

    @pytest.mark.parametrize("field", ["candidate_id", "run_id"])
    def test_missing_identity_blocked(self, field):
        approval = _valid_approval()
        del approval[field]
        r = _run(approval=approval)
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert "approval.identity" in r.criteria_failed

    @pytest.mark.parametrize(
        "field", ["paper_config_hash", "simulation_result_hash", "artifact_hash"]
    )
    def test_missing_evidence_hash_blocked(self, field):
        approval = _valid_approval()
        del approval[field]
        r = _run(approval=approval)
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert "approval.evidence_hashes" in r.criteria_failed

    @pytest.mark.parametrize(
        "field",
        [
            "allowed_symbols",
            "allowed_intervals",
            "allowed_strategy_families",
            "allowed_order_types",
        ],
    )
    def test_empty_allowlist_blocked(self, field):
        r = _run(approval=_valid_approval(**{field: []}))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert "approval.allowlists" in r.criteria_failed

    def test_non_regular_session_blocked(self):
        r = _run(approval=_valid_approval(allowed_session="extended"))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert "approval.allowlists" in r.criteria_failed

    @pytest.mark.parametrize(
        "override",
        [
            {"max_notional_per_position": 0.0},
            {"max_notional_per_position": -100.0},
            {"max_position_fraction": 0.2},   # > 0.10 cap
            {"max_daily_loss": 0.0},
            {"max_drawdown_stop": 1.5},        # > 1.0 cap
            {"max_orders_per_day": 0},
            {"max_orders_per_day": 11},        # > 10 cap
            {"max_orders_per_day": 2.5},       # not an int
            {"max_notional_per_position": float("inf")},
        ],
        ids=[
            "notional_zero", "notional_negative", "fraction_over_cap",
            "daily_loss_zero", "drawdown_over_cap", "orders_zero",
            "orders_over_cap", "orders_not_int", "notional_inf",
        ],
    )
    def test_invalid_risk_caps_blocked(self, override):
        r = _run(approval=_valid_approval(**override))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_APPROVAL
        assert "approval.risk_limits" in r.criteria_failed

    def test_live_trading_approved_true_blocked_safety(self):
        r = _run(approval=_valid_approval(live_trading_approved=True))
        assert r.result == "BLOCKED"
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SAFETY
        assert "approval.safety_flags" in r.criteria_failed
        assert r.plan is None

    def test_live_order_submission_approved_true_blocked_safety(self):
        r = _run(approval=_valid_approval(live_order_submission_approved=True))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SAFETY
        assert "approval.safety_flags" in r.criteria_failed

    @pytest.mark.parametrize(
        "field",
        ["dry_run_required", "human_confirmation_required", "kill_switch_required"],
    )
    def test_required_boolean_false_blocked_safety(self, field):
        r = _run(approval=_valid_approval(**{field: False}))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SAFETY
        assert "approval.safety_flags" in r.criteria_failed

    def test_non_dict_approval_blocked(self):
        r = _run(approval="not-a-dict")
        assert r.result == "BLOCKED"
        assert r.planner_status in (
            PaperOrderPlannerStatus.BLOCKED_APPROVAL,
            PaperOrderPlannerStatus.BLOCKED_SAFETY,
        )
        assert r.plan is None

    def test_blocked_approval_stops_before_signal_stage(self):
        r = _run(approval={})
        assert not any(c.startswith("signal.") for c in r.criteria_checked)
        assert not any(c.startswith("sizing.") for c in r.criteria_checked)


# ---------------------------------------------------------------------------
# TestSignalBlocked
# ---------------------------------------------------------------------------

class TestSignalBlocked:
    def test_signal_not_a_dict_blocked(self):
        r = _run(signal="not-a-dict")
        assert r.result == "BLOCKED"
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert "signal.schema" in r.criteria_failed
        assert r.plan is None

    @pytest.mark.parametrize(
        "field",
        [
            "symbol", "interval", "strategy_family", "side",
            "order_type", "confidence", "rationale", "holding_horizon",
        ],
    )
    def test_missing_signal_field_blocked(self, field):
        signal = _valid_signal()
        del signal[field]
        r = _run(signal=signal)
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert "signal.schema" in r.criteria_failed

    @pytest.mark.parametrize(
        ("override", "expected_criterion"),
        [
            ({"symbol": "TSLA"}, "signal.allowlists"),
            ({"interval": "1d"}, "signal.allowlists"),
            ({"strategy_family": "MEAN_REVERSION"}, "signal.allowlists"),
        ],
        ids=["symbol", "interval", "strategy_family"],
    )
    def test_signal_outside_approval_allowlists_blocked(
        self, override, expected_criterion
    ):
        r = _run(signal=_valid_signal(**override))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert expected_criterion in r.criteria_failed

    def test_order_type_not_in_approval_allowlist_blocked(self):
        approval = _valid_approval(allowed_order_types=["limit"])
        r = _run(approval=approval, signal=_valid_signal(order_type="market"))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert "signal.allowlists" in r.criteria_failed

    @pytest.mark.parametrize("side", ["HOLD", "buy", "sell", ""])
    def test_invalid_side_blocked(self, side):
        r = _run(signal=_valid_signal(side=side))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL

    def test_unsupported_order_type_blocked(self):
        r = _run(signal=_valid_signal(order_type="stop"))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert "signal.intent" in r.criteria_failed

    @pytest.mark.parametrize(
        "confidence",
        [-0.1, 1.1, float("inf"), float("nan"), "high", None, True],
        ids=["negative", "over_one", "inf", "nan", "string", "none", "bool"],
    )
    def test_invalid_confidence_blocked(self, confidence):
        r = _run(signal=_valid_signal(confidence=confidence))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIGNAL
        assert "signal.schema" in r.criteria_failed

    @pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5])
    def test_confidence_bounds_inclusive_pass(self, confidence):
        r = _run(signal=_valid_signal(confidence=confidence))
        assert r.result == "PASS"

    def test_blocked_signal_stops_before_sizing_stage(self):
        r = _run(signal={})
        assert not any(c.startswith("sizing.") for c in r.criteria_checked)


# ---------------------------------------------------------------------------
# TestSizingBlocked
# ---------------------------------------------------------------------------

class TestSizingBlocked:
    def test_sizing_not_a_dict_blocked(self):
        r = _run(sizing="not-a-dict")
        assert r.result == "BLOCKED"
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.schema" in r.criteria_failed
        assert r.plan is None

    @pytest.mark.parametrize(
        "field",
        [
            "quantity", "notional", "max_position_fraction", "max_daily_loss",
            "max_drawdown_stop", "max_orders_per_day", "max_notional_per_position",
        ],
    )
    def test_missing_sizing_field_blocked(self, field):
        sizing = _valid_sizing()
        del sizing[field]
        r = _run(sizing=sizing)
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.schema" in r.criteria_failed

    @pytest.mark.parametrize(
        "override",
        [
            {"quantity": 0.0},
            {"quantity": -5.0},
            {"quantity": float("inf")},
            {"notional": 0.0},
            {"max_orders_per_day": 0},
            {"max_orders_per_day": 1.5},
        ],
        ids=[
            "quantity_zero", "quantity_negative", "quantity_inf",
            "notional_zero", "orders_zero", "orders_not_int",
        ],
    )
    def test_invalid_sizing_values_blocked(self, override):
        r = _run(sizing=_valid_sizing(**override))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.schema" in r.criteria_failed

    @pytest.mark.parametrize(
        "override",
        [
            {"notional": 6000.0, "max_notional_per_position": 5000.0},
            {"max_position_fraction": 0.08},
            {"max_daily_loss": 3000.0},
            {"max_drawdown_stop": 0.60},
            {"max_orders_per_day": 6},
            {"max_notional_per_position": 9000.0},
        ],
        ids=[
            "notional_over_cap", "fraction_over_cap", "daily_loss_over_cap",
            "drawdown_over_cap", "orders_over_cap", "max_notional_over_cap",
        ],
    )
    def test_sizing_exceeds_approval_cap_blocked(self, override):
        # Each override stays internally valid but exceeds the matching
        # approval cap (5000 / 0.05 / 2000 / 0.50 / 5 / 5000).
        r = _run(sizing=_valid_sizing(**override))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.risk_limits" in r.criteria_failed

    def test_market_order_with_limit_price_blocked(self):
        r = _run(sizing=_valid_sizing(limit_price=150.0))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.schema" in r.criteria_failed

    @pytest.mark.parametrize(
        "limit_price", [None, 0.0, -5.0, float("inf")],
        ids=["none", "zero", "negative", "inf"],
    )
    def test_limit_order_without_positive_limit_price_blocked(self, limit_price):
        r = _run(signal=_limit_signal(), sizing=_valid_sizing(limit_price=limit_price))
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SIZING
        assert "sizing.schema" in r.criteria_failed

    def test_market_order_with_absent_limit_price_key_passes(self):
        sizing = _valid_sizing()
        del sizing["limit_price"]
        r = _run(sizing=sizing)
        assert r.result == "PASS"
        assert r.plan["limit_price"] is None


# ---------------------------------------------------------------------------
# TestValidationStage
# ---------------------------------------------------------------------------

class TestValidationStage:
    def test_injected_blocked_validator_returns_blocked_validation(self):
        r = _run(_plan_validator=_mock_validator_blocked)
        assert r.result == "BLOCKED"
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_VALIDATION
        assert "plan.validation" in r.criteria_failed
        assert r.plan is None
        assert r.plan_validation_result is not None

    def test_real_validator_blocks_misordered_timestamps(self):
        # generated_at after expires_at -> S27 time check fails; the planner
        # itself does not pre-validate timestamps, proving the real S27
        # validation stage is what fails closed here.
        r = _run(
            generated_at_utc="2025-01-15T17:00:00Z",
            expires_at_utc="2025-01-15T09:00:00Z",
        )
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_VALIDATION
        assert r.plan is None
        assert r.plan_validation_result.result == "BLOCKED"

    def test_real_validator_blocks_empty_plan_id(self):
        r = _run(plan_id="")
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_VALIDATION
        assert r.plan is None

    def test_real_validator_blocks_malformed_source_sha(self):
        r = _run(source_git_sha="")
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_VALIDATION

    def test_validator_safety_flag_true_returns_blocked_safety(self):
        r = _run(_plan_validator=_mock_validator_safety_flag_true)
        assert r.result == "BLOCKED"
        assert r.planner_status is PaperOrderPlannerStatus.BLOCKED_SAFETY
        assert "safety.result_flags" in r.criteria_failed
        assert r.plan is None

    def test_raising_validator_returns_error_planner(self):
        r = _run(_plan_validator=_raising_validator)
        assert r.result == "ERROR"
        assert r.planner_status is PaperOrderPlannerStatus.ERROR_PLANNER
        assert "plan.validation" in r.criteria_failed
        assert r.plan is None
        assert r.plan_validation_result is None

    def test_default_validator_is_real_s27(self):
        r = _run()
        assert isinstance(r.plan_validation_result, PaperOrderPlanValidationResult)


# ---------------------------------------------------------------------------
# TestPurity
# ---------------------------------------------------------------------------

class TestPurity:
    def test_inputs_not_mutated_on_pass(self):
        approval = _valid_approval()
        signal = _valid_signal()
        sizing = _valid_sizing()
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal), copy.deepcopy(sizing)
        )
        _run(approval=approval, signal=signal, sizing=sizing)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]

    def test_inputs_not_mutated_on_blocked(self):
        approval = _valid_approval(live_trading_approved=True)
        signal = _valid_signal(symbol="TSLA")
        sizing = _valid_sizing(notional=9999.0)
        snapshots = (
            copy.deepcopy(approval), copy.deepcopy(signal), copy.deepcopy(sizing)
        )
        _run(approval=approval, signal=signal, sizing=sizing)
        assert approval == snapshots[0]
        assert signal == snapshots[1]
        assert sizing == snapshots[2]

    def test_mutating_originals_after_planning_does_not_change_plan(self):
        signal = _valid_signal()
        sizing = _valid_sizing()
        r = _run(signal=signal, sizing=sizing)
        signal["symbol"] = "TSLA"
        signal["confidence"] = 0.01
        sizing["notional"] = 999999.0
        sizing["max_orders_per_day"] = 99
        assert r.plan["signal_snapshot"]["symbol"] == "AAPL"
        assert r.plan["signal_snapshot"]["confidence"] == 0.85
        assert r.plan["risk_snapshot"]["notional"] == 1500.0
        assert r.plan["risk_snapshot"]["max_orders_per_day"] == 3
        assert r.plan["symbol"] == "AAPL"
        assert r.plan["notional"] == 1500.0

    def test_same_input_same_output(self):
        r1 = _run()
        r2 = _run()
        assert r1 == r2

    def test_same_blocked_input_same_output(self):
        r1 = _run(signal=_valid_signal(symbol="TSLA"))
        r2 = _run(signal=_valid_signal(symbol="TSLA"))
        assert r1 == r2
        assert r1.criteria_failed == r2.criteria_failed

    def test_independent_calls_independent_plans(self):
        r1 = _run(plan_id="plan-A")
        r2 = _run(plan_id="plan-B")
        assert r1.plan["plan_id"] == "plan-A"
        assert r2.plan["plan_id"] == "plan-B"


# ---------------------------------------------------------------------------
# TestNoForbiddenPatternsInSource
# ---------------------------------------------------------------------------

class TestNoForbiddenPatternsInSource:
    """Confirm the planner module performs no I/O and contains no actual
    broker/network/credential/env/order imports or calls."""

    def _source(self) -> str:
        return inspect.getsource(_planner_mod)

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

    def test_no_env_or_network_calls(self):
        source = self._source()
        for pattern in (
            "os.envi" + "ron[",
            "os.envi" + "ron.get(",
            "getenv" + "(",
            "requ" + "ests.",
            "url" + "lib.",
            "sock" + "et.",
        ):
            assert pattern not in source, f"forbidden call found: {pattern!r}"

    def test_no_order_action_calls(self):
        source = self._source()
        for pattern in (
            "submit_or" + "der",
            "place_or" + "der",
            "cancel_or" + "der",
            "modify_or" + "der",
            "live_su" + "bmit",
        ):
            assert pattern not in source, f"forbidden order verb found: {pattern!r}"

    def test_no_runtime_or_execution_imports(self):
        source = self._source()
        for pattern in (
            "from src." + "runtime",
            "import src." + "runtime",
            "from src." + "execution",
            "import src." + "execution",
            "from src." + "tools",
            "import src." + "tools",
            "import main",
            "from src import main",
        ):
            assert pattern not in source, f"forbidden module import: {pattern!r}"

    def test_only_expected_local_import(self):
        # The only src import the planner needs is the S27 validator.
        source = self._source()
        assert "from src.research.paper_order_plan_validator import" in source


# ---------------------------------------------------------------------------
# TestThisModuleClean
# ---------------------------------------------------------------------------

class TestThisModuleClean:
    """Sanity check that this test module itself performs no file I/O and
    contains no actual broker/network/credential/env/order imports or calls.
    Every pattern is built from split fragments so the joined pattern never
    appears literally in this module's own source."""

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
