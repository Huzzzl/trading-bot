"""
tests/test_paper_config_validator.py
-------------------------------------
S17: Tests for the pure offline paper config validator.

No broker, API, credential, env, network, or order access.
No paper or live trading approval. No file I/O from the validator.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import sys
import types
from typing import Any

import pytest

from src.research.paper_config_validator import (
    PaperConfigStatus,
    PaperConfigValidationResult,
    validate_paper_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_config(**overrides: Any) -> dict[str, Any]:
    """Return a fully valid DRAFT paper config dict."""
    base: dict[str, Any] = {
        "config_schema_version": "PC/1.0",
        "candidate_id": "A_SPY_60m_trend_breakout_1to2d",
        "run_id": "20260604T000000Z_PASS",
        "source_git_sha": "abcdef1234567890",
        "symbol": "SPY",
        "interval": "60m",
        "strategy_family": "trend_breakout",
        "holding_horizon": "one_to_two_days",
        "paper_account_label": "alpaca-paper-primary",
        "max_notional_per_position": 5000.0,
        "max_position_fraction": 0.05,
        "max_daily_loss": 200.0,
        "max_drawdown_stop": 0.10,
        "max_orders_per_day": 5,
        "min_cash_buffer": 0.20,
        "allowed_order_types": ["market", "limit"],
        "allowed_session": "regular",
        "slippage_bps_assumption": 5.0,
        "commission_bps_assumption": 2.0,
        "risk_review_notes": "Reviewed and approved for paper simulation design.",
        "reviewer_name": "Alice Reviewer",
        "review_date_utc": "2026-06-04T12:00:00+00:00",
        "approval_status": "DRAFT",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestPaperConfigStatusEnum
# ---------------------------------------------------------------------------

class TestPaperConfigStatusEnum:
    def test_draft(self):
        assert PaperConfigStatus.DRAFT == "DRAFT"

    def test_ready_for_review(self):
        assert PaperConfigStatus.READY_FOR_PAPER_CONFIG_REVIEW == "READY_FOR_PAPER_CONFIG_REVIEW"

    def test_approved_for_simulation_design(self):
        assert PaperConfigStatus.APPROVED_FOR_PAPER_SIMULATION_DESIGN == "APPROVED_FOR_PAPER_SIMULATION_DESIGN"

    def test_rejected_config_review(self):
        assert PaperConfigStatus.REJECTED_CONFIG_REVIEW == "REJECTED_CONFIG_REVIEW"

    def test_blocked_risk_limits(self):
        assert PaperConfigStatus.BLOCKED_RISK_LIMITS == "BLOCKED_RISK_LIMITS"

    def test_blocked_provenance(self):
        assert PaperConfigStatus.BLOCKED_PROVENANCE == "BLOCKED_PROVENANCE"

    def test_all_six_values(self):
        values = {s.value for s in PaperConfigStatus}
        assert values == {
            "DRAFT",
            "READY_FOR_PAPER_CONFIG_REVIEW",
            "APPROVED_FOR_PAPER_SIMULATION_DESIGN",
            "REJECTED_CONFIG_REVIEW",
            "BLOCKED_RISK_LIMITS",
            "BLOCKED_PROVENANCE",
        }


# ---------------------------------------------------------------------------
# TestPaperConfigValidationResultDataclass
# ---------------------------------------------------------------------------

class TestPaperConfigValidationResultDataclass:
    def test_is_frozen(self):
        r = validate_paper_config(_valid_config())
        with pytest.raises((AttributeError, TypeError)):
            r.result = "BLOCKED"  # type: ignore[misc]

    def test_has_all_required_fields(self):
        r = validate_paper_config(_valid_config())
        for field in (
            "result", "blocker", "candidate_id", "run_id", "status",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        ):
            assert hasattr(r, field), f"missing field: {field}"

    def test_criteria_checked_is_tuple(self):
        r = validate_paper_config(_valid_config())
        assert isinstance(r.criteria_checked, tuple)

    def test_criteria_failed_is_tuple(self):
        r = validate_paper_config(_valid_config())
        assert isinstance(r.criteria_failed, tuple)

    def test_status_is_paper_config_status(self):
        r = validate_paper_config(_valid_config())
        assert isinstance(r.status, PaperConfigStatus)


# ---------------------------------------------------------------------------
# TestPassingConfigs
# ---------------------------------------------------------------------------

class TestPassingConfigs:
    def test_draft_passes(self):
        r = validate_paper_config(_valid_config(approval_status="DRAFT"))
        assert r.result == "PASS"
        assert r.status == PaperConfigStatus.DRAFT
        assert r.blocker is None
        assert r.criteria_failed == ()

    def test_ready_for_review_passes(self):
        r = validate_paper_config(_valid_config(
            approval_status="READY_FOR_PAPER_CONFIG_REVIEW",
        ))
        assert r.result == "PASS"
        assert r.status == PaperConfigStatus.READY_FOR_PAPER_CONFIG_REVIEW
        assert r.blocker is None

    def test_approved_for_simulation_design_passes(self):
        r = validate_paper_config(_valid_config(
            approval_status="APPROVED_FOR_PAPER_SIMULATION_DESIGN",
        ))
        assert r.result == "PASS"
        assert r.status == PaperConfigStatus.APPROVED_FOR_PAPER_SIMULATION_DESIGN
        assert r.blocker is None

    def test_approved_simulation_design_no_safety_flags(self):
        r = validate_paper_config(_valid_config(
            approval_status="APPROVED_FOR_PAPER_SIMULATION_DESIGN",
        ))
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.network_calls_made
        assert not r.order_action_requested
        assert not r.live_trading_allowed

    def test_candidate_id_preserved(self):
        r = validate_paper_config(_valid_config())
        assert r.candidate_id == "A_SPY_60m_trend_breakout_1to2d"

    def test_run_id_preserved(self):
        r = validate_paper_config(_valid_config())
        assert r.run_id == "20260604T000000Z_PASS"


# ---------------------------------------------------------------------------
# TestSafetyFlagsAlwaysFalse
# ---------------------------------------------------------------------------

class TestSafetyFlagsAlwaysFalse:
    def _check_all_false(self, r: PaperConfigValidationResult) -> None:
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.network_calls_made
        assert not r.order_action_requested
        assert not r.live_trading_allowed

    def test_pass_result(self):
        self._check_all_false(validate_paper_config(_valid_config()))

    def test_blocked_provenance(self):
        self._check_all_false(validate_paper_config(_valid_config(
            config_schema_version="UNKNOWN"
        )))

    def test_blocked_risk_limits(self):
        self._check_all_false(validate_paper_config(_valid_config(
            max_position_fraction=0.99
        )))

    def test_rejected_config_review(self):
        self._check_all_false(validate_paper_config(_valid_config(
            approval_status="NOT_A_STATUS"
        )))


# ---------------------------------------------------------------------------
# TestSchemaVersion
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_unsupported_version_blocked_provenance(self):
        r = validate_paper_config(_valid_config(config_schema_version="PC/2.0"))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_missing_schema_version_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["config_schema_version"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_none_schema_version_blocked_provenance(self):
        r = validate_paper_config(_valid_config(config_schema_version=None))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_empty_schema_version_blocked_provenance(self):
        r = validate_paper_config(_valid_config(config_schema_version=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_schema_criterion_in_checked(self):
        r = validate_paper_config(_valid_config(config_schema_version="BAD"))
        assert "schema.config" in r.criteria_checked

    def test_schema_criterion_in_failed(self):
        r = validate_paper_config(_valid_config(config_schema_version="BAD"))
        assert "schema.config" in r.criteria_failed

    def test_early_exit_on_bad_schema(self):
        # Should block before checking other criteria
        r = validate_paper_config(_valid_config(config_schema_version="BAD"))
        assert r.result == "BLOCKED"
        assert len(r.criteria_checked) == 1


# ---------------------------------------------------------------------------
# TestProvenanceFields
# ---------------------------------------------------------------------------

class TestProvenanceFields:
    def test_missing_candidate_id_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["candidate_id"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "provenance.candidate_id" in r.criteria_failed

    def test_empty_candidate_id_blocked_provenance(self):
        r = validate_paper_config(_valid_config(candidate_id=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_whitespace_candidate_id_blocked_provenance(self):
        r = validate_paper_config(_valid_config(candidate_id="   "))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_missing_run_id_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["run_id"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "provenance.run_id" in r.criteria_failed

    def test_empty_run_id_blocked_provenance(self):
        r = validate_paper_config(_valid_config(run_id=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_missing_source_git_sha_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["source_git_sha"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "provenance.source_git_sha" in r.criteria_failed

    def test_empty_source_git_sha_blocked_provenance(self):
        r = validate_paper_config(_valid_config(source_git_sha=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_none_candidate_id_candidate_id_field_is_none(self):
        r = validate_paper_config(_valid_config(candidate_id=None))
        assert r.candidate_id is None

    def test_missing_candidate_id_candidate_id_field_is_none(self):
        cfg = _valid_config()
        del cfg["candidate_id"]
        r = validate_paper_config(cfg)
        assert r.candidate_id is None


# ---------------------------------------------------------------------------
# TestStrategyFields
# ---------------------------------------------------------------------------

class TestStrategyFields:
    @pytest.mark.parametrize("field", [
        "symbol", "interval", "strategy_family", "holding_horizon",
    ])
    def test_missing_strategy_field_rejected(self, field: str):
        cfg = _valid_config()
        del cfg[field]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert f"strategy.{field}" in r.criteria_failed

    @pytest.mark.parametrize("field", [
        "symbol", "interval", "strategy_family", "holding_horizon",
    ])
    def test_empty_strategy_field_rejected(self, field: str):
        r = validate_paper_config(_valid_config(**{field: ""}))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW

    @pytest.mark.parametrize("field", [
        "symbol", "interval", "strategy_family", "holding_horizon",
    ])
    def test_strategy_field_in_checked(self, field: str):
        r = validate_paper_config(_valid_config())
        assert f"strategy.{field}" in r.criteria_checked


# ---------------------------------------------------------------------------
# TestPaperAccountLabel
# ---------------------------------------------------------------------------

class TestPaperAccountLabel:
    def test_missing_label_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["paper_account_label"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "account.paper_account_label" in r.criteria_failed

    def test_empty_label_blocked_provenance(self):
        r = validate_paper_config(_valid_config(paper_account_label=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    @pytest.mark.parametrize("bad_label", [
        "my_api_key_account",
        "account_secret",
        "auth_token_paper",
        "paper_password_1",
        "credential_store",
        "token_acct",
    ])
    def test_credential_like_label_blocked_provenance(self, bad_label: str):
        r = validate_paper_config(_valid_config(paper_account_label=bad_label))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "account.paper_account_label" in r.criteria_failed

    def test_valid_paper_label_passes(self):
        r = validate_paper_config(_valid_config(paper_account_label="alpaca-paper-primary"))
        assert r.result == "PASS"

    def test_valid_label_with_paper_word_passes(self):
        r = validate_paper_config(_valid_config(paper_account_label="my-paper-acct"))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestRiskLimits
# ---------------------------------------------------------------------------

class TestRiskLimits:
    def test_missing_max_notional_blocked_risk(self):
        cfg = _valid_config()
        del cfg["max_notional_per_position"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_notional_per_position" in r.criteria_failed

    def test_zero_max_notional_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_notional_per_position=0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_negative_max_notional_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_notional_per_position=-100.0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_max_position_fraction_over_cap_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_position_fraction=0.11))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_position_fraction" in r.criteria_failed

    def test_max_position_fraction_at_cap_passes(self):
        r = validate_paper_config(_valid_config(max_position_fraction=0.10))
        assert r.result == "PASS"

    def test_max_position_fraction_zero_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_position_fraction=0.0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_missing_max_daily_loss_blocked_risk(self):
        cfg = _valid_config()
        del cfg["max_daily_loss"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_missing_max_drawdown_stop_blocked_risk(self):
        cfg = _valid_config()
        del cfg["max_drawdown_stop"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_max_drawdown_stop_over_one_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_drawdown_stop=1.01))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_drawdown_stop" in r.criteria_failed

    def test_max_drawdown_stop_at_one_passes(self):
        r = validate_paper_config(_valid_config(max_drawdown_stop=1.0))
        assert r.result == "PASS"

    def test_max_orders_per_day_over_cap_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_orders_per_day=11))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_orders_per_day" in r.criteria_failed

    def test_max_orders_per_day_at_cap_passes(self):
        r = validate_paper_config(_valid_config(max_orders_per_day=10))
        assert r.result == "PASS"

    def test_max_orders_per_day_zero_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_orders_per_day=0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_max_orders_per_day_noninteger_blocked_risk(self):
        r = validate_paper_config(_valid_config(max_orders_per_day=3.5))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_min_cash_buffer_at_one_blocked_risk(self):
        r = validate_paper_config(_valid_config(min_cash_buffer=1.0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS
        assert "risk.min_cash_buffer" in r.criteria_failed

    def test_min_cash_buffer_over_one_blocked_risk(self):
        r = validate_paper_config(_valid_config(min_cash_buffer=1.5))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_min_cash_buffer_zero_blocked_risk(self):
        r = validate_paper_config(_valid_config(min_cash_buffer=0.0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_min_cash_buffer_just_under_one_passes(self):
        r = validate_paper_config(_valid_config(min_cash_buffer=0.99))
        assert r.result == "PASS"

    def test_all_risk_criteria_in_checked(self):
        r = validate_paper_config(_valid_config())
        for criterion in (
            "risk.max_notional_per_position",
            "risk.max_position_fraction",
            "risk.max_daily_loss",
            "risk.max_drawdown_stop",
            "risk.max_orders_per_day",
            "risk.min_cash_buffer",
        ):
            assert criterion in r.criteria_checked


# ---------------------------------------------------------------------------
# TestExecutionFields
# ---------------------------------------------------------------------------

class TestExecutionFields:
    def test_empty_allowed_order_types_rejected(self):
        r = validate_paper_config(_valid_config(allowed_order_types=[]))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert "execution.allowed_order_types" in r.criteria_failed

    def test_unsupported_order_type_rejected(self):
        r = validate_paper_config(_valid_config(allowed_order_types=["market", "stop"]))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert "execution.allowed_order_types" in r.criteria_failed

    def test_only_market_passes(self):
        r = validate_paper_config(_valid_config(allowed_order_types=["market"]))
        assert r.result == "PASS"

    def test_only_limit_passes(self):
        r = validate_paper_config(_valid_config(allowed_order_types=["limit"]))
        assert r.result == "PASS"

    def test_not_a_list_rejected(self):
        r = validate_paper_config(_valid_config(allowed_order_types="market"))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW

    def test_allowed_session_not_regular_rejected(self):
        r = validate_paper_config(_valid_config(allowed_session="extended"))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert "execution.allowed_session" in r.criteria_failed

    def test_missing_allowed_session_rejected(self):
        cfg = _valid_config()
        del cfg["allowed_session"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW

    def test_regular_session_passes(self):
        r = validate_paper_config(_valid_config(allowed_session="regular"))
        assert r.result == "PASS"

    def test_negative_slippage_rejected(self):
        r = validate_paper_config(_valid_config(slippage_bps_assumption=-1.0))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert "execution.slippage_bps_assumption" in r.criteria_failed

    def test_zero_slippage_passes(self):
        r = validate_paper_config(_valid_config(slippage_bps_assumption=0.0))
        assert r.result == "PASS"

    def test_negative_commission_rejected(self):
        r = validate_paper_config(_valid_config(commission_bps_assumption=-0.5))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert "execution.commission_bps_assumption" in r.criteria_failed

    def test_zero_commission_passes(self):
        r = validate_paper_config(_valid_config(commission_bps_assumption=0.0))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestReviewFields
# ---------------------------------------------------------------------------

class TestReviewFields:
    def test_missing_risk_review_notes_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["risk_review_notes"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "review.risk_review_notes" in r.criteria_failed

    def test_empty_risk_review_notes_blocked_provenance(self):
        r = validate_paper_config(_valid_config(risk_review_notes=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_missing_reviewer_name_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["reviewer_name"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "review.reviewer_name" in r.criteria_failed

    def test_empty_reviewer_name_blocked_provenance(self):
        r = validate_paper_config(_valid_config(reviewer_name=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_missing_review_date_blocked_provenance(self):
        cfg = _valid_config()
        del cfg["review_date_utc"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "review.review_date_utc" in r.criteria_failed

    def test_empty_review_date_blocked_provenance(self):
        r = validate_paper_config(_valid_config(review_date_utc=""))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_invalid_approval_status_rejected(self):
        r = validate_paper_config(_valid_config(approval_status="NOT_A_STATUS"))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW
        assert "review.approval_status" in r.criteria_failed

    def test_missing_approval_status_rejected(self):
        cfg = _valid_config()
        del cfg["approval_status"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.REJECTED_CONFIG_REVIEW

    def test_review_criteria_in_checked(self):
        r = validate_paper_config(_valid_config())
        for criterion in (
            "review.risk_review_notes",
            "review.reviewer_name",
            "review.review_date_utc",
            "review.approval_status",
        ):
            assert criterion in r.criteria_checked


# ---------------------------------------------------------------------------
# TestForbiddenScan
# ---------------------------------------------------------------------------

class TestForbiddenScan:
    def test_forbidden_key_name_blocked_provenance(self):
        cfg = _valid_config()
        cfg["api_key"] = "some_value"
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE
        assert "forbidden.scan" in r.criteria_failed

    def test_forbidden_value_string_blocked_provenance(self):
        r = validate_paper_config(_valid_config(
            risk_review_notes="use os.environ to get key"
        ))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_secret_key_in_nested_dict_blocked(self):
        cfg = _valid_config()
        cfg["metadata"] = {"secret_key": "x"}
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_live_trading_value_blocked(self):
        r = validate_paper_config(_valid_config(
            risk_review_notes="enable live_trading for this run"
        ))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_submit_order_value_blocked(self):
        r = validate_paper_config(_valid_config(
            risk_review_notes="submit_order on signal"
        ))
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_place_order_in_nested_list_blocked(self):
        cfg = _valid_config()
        cfg["notes_list"] = ["place_order at open"]
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_auth_token_key_blocked(self):
        cfg = _valid_config()
        cfg["auth_token"] = "xyz"
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_harmless_words_not_blocked(self):
        r = validate_paper_config(_valid_config(
            risk_review_notes="Normal paper market analysis complete.",
            symbol="SPY",
            paper_account_label="alpaca-paper-primary",
        ))
        assert r.result == "PASS"

    def test_forbidden_scan_in_checked(self):
        r = validate_paper_config(_valid_config())
        assert "forbidden.scan" in r.criteria_checked


# ---------------------------------------------------------------------------
# TestPriorityBucketing
# ---------------------------------------------------------------------------

class TestPriorityBucketing:
    def test_provenance_beats_risk(self):
        # Both missing provenance and bad risk
        cfg = _valid_config()
        del cfg["source_git_sha"]
        cfg["max_position_fraction"] = 0.99
        r = validate_paper_config(cfg)
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_provenance_beats_rejected(self):
        # Missing provenance and invalid approval_status
        cfg = _valid_config()
        del cfg["source_git_sha"]
        cfg["approval_status"] = "BAD"
        r = validate_paper_config(cfg)
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_risk_beats_rejected(self):
        # Both bad risk and invalid order types (no provenance failures)
        r = validate_paper_config(_valid_config(
            max_position_fraction=0.99,
            allowed_order_types=["stop"],
        ))
        assert r.status == PaperConfigStatus.BLOCKED_RISK_LIMITS

    def test_forbidden_scan_is_provenance(self):
        cfg = _valid_config()
        cfg["api_key"] = "x"
        r = validate_paper_config(cfg)
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE


# ---------------------------------------------------------------------------
# TestCriteriaOrder
# ---------------------------------------------------------------------------

class TestCriteriaOrder:
    def test_criteria_checked_deterministic(self):
        cfg = _valid_config()
        r1 = validate_paper_config(cfg)
        r2 = validate_paper_config(cfg)
        assert r1.criteria_checked == r2.criteria_checked

    def test_criteria_failed_deterministic(self):
        cfg = _valid_config(max_position_fraction=0.99, max_drawdown_stop=2.0)
        r1 = validate_paper_config(cfg)
        r2 = validate_paper_config(cfg)
        assert r1.criteria_failed == r2.criteria_failed

    def test_schema_is_first_criterion(self):
        r = validate_paper_config(_valid_config())
        assert r.criteria_checked[0] == "schema.config"

    def test_all_24_criteria_checked_on_pass(self):
        r = validate_paper_config(_valid_config())
        assert len(r.criteria_checked) == 24

    def test_all_criteria_unique(self):
        r = validate_paper_config(_valid_config())
        assert len(r.criteria_checked) == len(set(r.criteria_checked))


# ---------------------------------------------------------------------------
# TestPureFunctionProperties
# ---------------------------------------------------------------------------

class TestPureFunctionProperties:
    def test_does_not_mutate_input(self):
        cfg = _valid_config()
        original = copy.deepcopy(cfg)
        validate_paper_config(cfg)
        assert cfg == original

    def test_same_input_same_output_pass(self):
        cfg = _valid_config()
        r1 = validate_paper_config(cfg)
        r2 = validate_paper_config(cfg)
        assert r1 == r2

    def test_same_input_same_output_blocked(self):
        cfg = _valid_config(max_position_fraction=0.99)
        r1 = validate_paper_config(cfg)
        r2 = validate_paper_config(cfg)
        assert r1 == r2

    def test_independent_inputs(self):
        cfg_a = _valid_config(candidate_id="A_SPY_60m_trend_breakout_1to2d")
        cfg_b = _valid_config(candidate_id="B_QQQ_60m_mean_reversion_1to2d")
        ra = validate_paper_config(cfg_a)
        rb = validate_paper_config(cfg_b)
        assert ra.candidate_id != rb.candidate_id
        assert ra.result == rb.result == "PASS"


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

_validator_mod = importlib.import_module("src.research.paper_config_validator")


class TestNoForbiddenImports:
    _FORBIDDEN_MODULES = {
        "os.environ",
        "subprocess",
        "socket",
        "requests",
        "urllib.request",
        "http.client",
        "alpaca",
        "alpaca_trade_api",
        "boto3",
        "pandas",
        "numpy",
    }

    def test_no_forbidden_stdlib_imports(self):
        source = inspect.getsource(_validator_mod)
        for mod in self._FORBIDDEN_MODULES:
            assert f"import {mod}" not in source, f"found forbidden import: {mod}"

    def test_no_open_calls(self):
        source = inspect.getsource(_validator_mod)
        # Should not call open() for file I/O
        assert "open(" not in source

    def test_no_os_environ(self):
        source = inspect.getsource(_validator_mod)
        # The module may reference "os.environ" as a forbidden substring string literal.
        # What must not exist is an actual import of os or use of os.environ at runtime.
        assert "import os" not in source

    def test_no_network_patterns(self):
        source = inspect.getsource(_validator_mod)
        for pattern in ("requests.get", "requests.post", "urlopen", "socket.connect"):
            assert pattern not in source, f"found forbidden pattern: {pattern}"

    def test_no_broker_patterns(self):
        source = inspect.getsource(_validator_mod)
        # "broker", "submit_order", and "place_order" may appear as forbidden substring
        # literals or in docstrings; check for actual imports/calls only.
        for pattern in ("import alpaca", "alpaca_trade_api", "broker.submit", "broker.place"):
            assert pattern not in source, f"found forbidden pattern: {pattern}"
