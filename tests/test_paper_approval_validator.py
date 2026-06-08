"""
tests/test_paper_approval_validator.py
-------------------------------------
S25: Tests for the pure offline paper trading approval artifact validator.

No broker, API, credential, env, network, or order access.
No paper or live trading approval. No file I/O from the validator or these
tests. Validator PASS means only that an artifact's shape and scope match
the reviewed S24 PTA/1.0 schema -- it is not, and can never be, paper or
live trading approval.
"""

from __future__ import annotations

import copy
import inspect
from typing import Any

import pytest

from src.research import paper_approval_validator as _validator_mod
from src.research.paper_approval_validator import (
    PaperApprovalStatus,
    PaperApprovalValidationResult,
    validate_paper_approval_artifact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFETY_FLAG_NAMES: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)


def _valid_artifact(**overrides: Any) -> dict[str, Any]:
    """Return a fully valid, structurally-passing PTA/1.0 artifact dict."""
    base: dict[str, Any] = {
        "artifact_schema_version": "PTA/1.0",
        "approval_artifact_type": "PAPER_TRADING_APPROVAL",
        "approval_scope": "PAPER_TRADING_LIMITED_RUN_ONLY",
        "candidate_id": "A_SPY_60m_trend_breakout_1to2d",
        "run_id": "20260604T000000Z_PASS",
        "source_git_sha": "a" * 40,
        "paper_config_schema_version": "PC/1.0",
        "paper_config_hash": "sha256:" + "b" * 64,
        "simulation_result_hash": "sha256:" + "c" * 64,
        "architecture_review_reference": "docs/paper_trading_architecture_design.md#S22",
        "invariant_test_reference": "tests/test_paper_architecture_invariants.py#S23",
        "approved_by": "Alice Reviewer",
        "approved_at_utc": "2026-06-04T12:00:00+00:00",
        "expires_at_utc": "2026-07-04T12:00:00+00:00",
        "max_notional_per_position": 5000.0,
        "max_position_fraction": 0.05,
        "max_daily_loss": 200.0,
        "max_drawdown_stop": 0.10,
        "max_orders_per_day": 5,
        "allowed_symbols": ["SPY"],
        "allowed_intervals": ["60m"],
        "allowed_strategy_families": ["trend_breakout"],
        "allowed_order_types": ["market", "limit"],
        "allowed_session": "regular",
        "dry_run_required": True,
        "human_confirmation_required": True,
        "kill_switch_required": True,
        "paper_account_label": "alpaca-paper-primary",
        "live_trading_approved": False,
        "live_order_submission_approved": False,
        "paper_trading_limited_run_approved": False,
        "notes": "Bounded, time-boxed observation window only.",
        "known_limitations": "Small simulated trade sample; assumptions sensitive to slippage.",
        "approval_status": "APPROVED_FOR_DRY_RUN_DESIGN",
    }
    base.update(overrides)
    return base


def _run(artifact: dict[str, Any] | None = None) -> PaperApprovalValidationResult:
    return validate_paper_approval_artifact(artifact if artifact is not None else _valid_artifact())


# ---------------------------------------------------------------------------
# TestPaperApprovalStatusEnum
# ---------------------------------------------------------------------------

class TestPaperApprovalStatusEnum:
    def test_all_nine_values(self):
        values = {s.value for s in PaperApprovalStatus}
        assert values == {
            "NOT_REVIEWED",
            "DRAFT",
            "APPROVED_FOR_DRY_RUN_DESIGN",
            "APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN",
            "APPROVED_FOR_LIMITED_PAPER_RUN",
            "REJECTED_APPROVAL_REVIEW",
            "BLOCKED_PROVENANCE",
            "BLOCKED_RISK_LIMITS",
            "BLOCKED_SAFETY",
        }

    def test_is_str_enum(self):
        assert PaperApprovalStatus.NOT_REVIEWED == "NOT_REVIEWED"
        assert isinstance(PaperApprovalStatus.DRAFT, str)


# ---------------------------------------------------------------------------
# TestPaperApprovalValidationResultDataclass
# ---------------------------------------------------------------------------

class TestPaperApprovalValidationResultDataclass:
    def test_is_frozen(self):
        r = _run()
        with pytest.raises((AttributeError, TypeError)):
            r.result = "BLOCKED"  # type: ignore[misc]

    def test_has_all_required_fields(self):
        r = _run()
        for field in (
            "result", "blocker", "candidate_id", "run_id", "status",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        ):
            assert hasattr(r, field), f"missing field: {field}"

    def test_criteria_checked_and_failed_are_tuples(self):
        r = _run()
        assert isinstance(r.criteria_checked, tuple)
        assert isinstance(r.criteria_failed, tuple)


# ---------------------------------------------------------------------------
# TestValidArtifactPasses
# ---------------------------------------------------------------------------

class TestValidArtifactPasses:
    def test_approved_for_dry_run_design_passes(self):
        r = _run(_valid_artifact(approval_status="APPROVED_FOR_DRY_RUN_DESIGN"))
        assert r.result == "PASS"
        assert r.status == PaperApprovalStatus.APPROVED_FOR_DRY_RUN_DESIGN
        assert r.blocker is None
        assert r.criteria_failed == ()

    def test_approved_for_paper_order_plan_design_passes(self):
        r = _run(_valid_artifact(approval_status="APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN"))
        assert r.result == "PASS"
        assert r.status == PaperApprovalStatus.APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN

    def test_approved_for_limited_paper_run_passes(self):
        r = _run(_valid_artifact(approval_status="APPROVED_FOR_LIMITED_PAPER_RUN"))
        assert r.result == "PASS"
        assert r.status == PaperApprovalStatus.APPROVED_FOR_LIMITED_PAPER_RUN

    def test_pass_result_has_all_safety_flags_false(self):
        r = _run(_valid_artifact(approval_status="APPROVED_FOR_LIMITED_PAPER_RUN"))
        assert r.result == "PASS"
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(r, flag) is False, f"{flag} must be False"

    def test_pass_result_does_not_imply_paper_or_live_approval(self):
        r = _run(_valid_artifact(approval_status="APPROVED_FOR_LIMITED_PAPER_RUN"))
        assert r.result == "PASS"
        assert r.live_trading_allowed is False
        assert r.order_action_requested is False
        # The PASS classification confirms shape/scope only -- not approval.
        assert r.status != PaperApprovalStatus.BLOCKED_SAFETY


# ---------------------------------------------------------------------------
# TestSchemaTypeScope
# ---------------------------------------------------------------------------

class TestSchemaTypeScope:
    def test_missing_schema_version_is_blocked_provenance(self):
        artifact = _valid_artifact()
        del artifact["artifact_schema_version"]
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    def test_unsupported_schema_version_is_blocked_provenance(self):
        r = _run(_valid_artifact(artifact_schema_version="PTA/0.9"))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "schema.artifact" in r.criteria_failed

    def test_wrong_artifact_type_is_blocked_provenance(self):
        r = _run(_valid_artifact(approval_artifact_type="SOMETHING_ELSE"))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "artifact.type" in r.criteria_failed

    def test_wrong_approval_scope_is_blocked_provenance(self):
        r = _run(_valid_artifact(approval_scope="SOMETHING_BROADER"))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "artifact.scope" in r.criteria_failed


# ---------------------------------------------------------------------------
# TestFixedSafetyValues
# ---------------------------------------------------------------------------

class TestFixedSafetyValues:
    def test_live_trading_approved_true_is_blocked_safety(self):
        r = _run(_valid_artifact(live_trading_approved=True))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "safety.live_trading_approved" in r.criteria_failed

    def test_live_order_submission_approved_true_is_blocked_safety(self):
        r = _run(_valid_artifact(live_order_submission_approved=True))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "safety.live_order_submission_approved" in r.criteria_failed

    def test_dry_run_required_false_is_blocked_safety(self):
        r = _run(_valid_artifact(dry_run_required=False))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "safety.dry_run_required" in r.criteria_failed

    def test_human_confirmation_required_false_is_blocked_safety(self):
        r = _run(_valid_artifact(human_confirmation_required=False))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "safety.human_confirmation_required" in r.criteria_failed

    def test_kill_switch_required_false_is_blocked_safety(self):
        r = _run(_valid_artifact(kill_switch_required=False))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "safety.kill_switch_required" in r.criteria_failed

    def test_blocked_safety_result_still_has_all_safety_flags_false(self):
        r = _run(_valid_artifact(live_trading_approved=True))
        assert r.result == "BLOCKED"
        for flag in _SAFETY_FLAG_NAMES:
            assert getattr(r, flag) is False


# ---------------------------------------------------------------------------
# TestProvenanceAndEvidenceFields
# ---------------------------------------------------------------------------

class TestProvenanceAndEvidenceFields:
    @pytest.mark.parametrize("field", ["candidate_id", "run_id", "source_git_sha"])
    def test_missing_identity_field_is_blocked_provenance(self, field):
        artifact = _valid_artifact()
        del artifact[field]
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    @pytest.mark.parametrize("field", ["paper_config_hash", "simulation_result_hash"])
    def test_missing_evidence_hash_is_blocked_provenance(self, field):
        artifact = _valid_artifact()
        del artifact[field]
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    @pytest.mark.parametrize("field", [
        "paper_config_schema_version",
        "architecture_review_reference",
        "invariant_test_reference",
        "approved_by",
        "notes",
        "known_limitations",
    ])
    def test_missing_or_blank_evidence_field_is_blocked_provenance(self, field):
        artifact = _valid_artifact(**{field: "   "})
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    def test_non_string_provenance_field_is_blocked_provenance(self):
        r = _run(_valid_artifact(candidate_id=12345))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE


# ---------------------------------------------------------------------------
# TestDatetimeFields
# ---------------------------------------------------------------------------

class TestDatetimeFields:
    @pytest.mark.parametrize("value", ["not-a-date", "2026/06/04 12:00", "", None, 12345])
    def test_invalid_approved_at_utc_is_blocked_provenance(self, value):
        r = _run(_valid_artifact(approved_at_utc=value))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "approval.approved_at_utc" in r.criteria_failed

    @pytest.mark.parametrize("value", ["not-a-date", "2026/07/04 12:00", "", None, 12345])
    def test_invalid_expires_at_utc_is_blocked_provenance(self, value):
        r = _run(_valid_artifact(expires_at_utc=value))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "approval.expires_at_utc" in r.criteria_failed

    def test_expires_before_approved_is_blocked_provenance(self):
        r = _run(_valid_artifact(
            approved_at_utc="2026-07-04T12:00:00+00:00",
            expires_at_utc="2026-06-04T12:00:00+00:00",
        ))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "approval.expires_at_utc" in r.criteria_failed

    def test_expires_equal_to_approved_is_blocked_provenance(self):
        same = "2026-06-04T12:00:00+00:00"
        r = _run(_valid_artifact(approved_at_utc=same, expires_at_utc=same))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "approval.expires_at_utc" in r.criteria_failed

    def test_expires_after_approved_with_z_suffix_passes(self):
        r = _run(_valid_artifact(
            approved_at_utc="2026-06-04T12:00:00Z",
            expires_at_utc="2026-07-04T12:00:00Z",
            approval_status="APPROVED_FOR_DRY_RUN_DESIGN",
        ))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestRiskLimitFields
# ---------------------------------------------------------------------------

class TestRiskLimitFields:
    @pytest.mark.parametrize("field,bad_value", [
        ("max_notional_per_position", -100.0),
        ("max_notional_per_position", 0.0),
        ("max_notional_per_position", float("nan")),
        ("max_notional_per_position", float("inf")),
        ("max_position_fraction", -0.05),
        ("max_position_fraction", 0.0),
        ("max_daily_loss", -1.0),
        ("max_daily_loss", 0.0),
        ("max_drawdown_stop", -0.10),
        ("max_drawdown_stop", 0.0),
        ("max_orders_per_day", -1),
        ("max_orders_per_day", 0),
        ("max_orders_per_day", 2.5),
    ])
    def test_each_risk_field_invalid_is_blocked_risk_limits(self, field, bad_value):
        r = _run(_valid_artifact(**{field: bad_value}))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_RISK_LIMITS
        assert f"risk.{field}" in r.criteria_failed

    def test_max_position_fraction_above_cap_is_blocked_risk_limits(self):
        r = _run(_valid_artifact(max_position_fraction=0.11))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_position_fraction" in r.criteria_failed

    def test_max_position_fraction_at_cap_passes(self):
        r = _run(_valid_artifact(max_position_fraction=0.10))
        assert r.result == "PASS"

    def test_max_drawdown_stop_above_cap_is_blocked_risk_limits(self):
        r = _run(_valid_artifact(max_drawdown_stop=1.5))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_drawdown_stop" in r.criteria_failed

    def test_max_orders_per_day_above_cap_is_blocked_risk_limits(self):
        r = _run(_valid_artifact(max_orders_per_day=11))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_RISK_LIMITS
        assert "risk.max_orders_per_day" in r.criteria_failed

    def test_max_orders_per_day_at_cap_passes(self):
        r = _run(_valid_artifact(max_orders_per_day=10))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestAllowlistFields
# ---------------------------------------------------------------------------

class TestAllowlistFields:
    @pytest.mark.parametrize("field", [
        "allowed_symbols", "allowed_intervals", "allowed_strategy_families",
    ])
    def test_missing_identity_allowlist_is_blocked_provenance(self, field):
        artifact = _valid_artifact()
        del artifact[field]
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    @pytest.mark.parametrize("field", [
        "allowed_symbols", "allowed_intervals", "allowed_strategy_families",
    ])
    def test_empty_identity_allowlist_is_blocked_provenance(self, field):
        r = _run(_valid_artifact(**{field: []}))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    def test_unsupported_order_type_is_rejected_approval_review(self):
        r = _run(_valid_artifact(allowed_order_types=["market", "stop"]))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.REJECTED_APPROVAL_REVIEW
        assert "allowlist.allowed_order_types" in r.criteria_failed

    def test_empty_order_types_is_rejected_approval_review(self):
        r = _run(_valid_artifact(allowed_order_types=[]))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.REJECTED_APPROVAL_REVIEW

    def test_session_not_regular_is_rejected_approval_review(self):
        r = _run(_valid_artifact(allowed_session="extended"))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.REJECTED_APPROVAL_REVIEW
        assert "allowlist.allowed_session" in r.criteria_failed

    def test_allowed_order_types_subset_of_market_and_limit_passes(self):
        r = _run(_valid_artifact(allowed_order_types=["market"]))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestPaperAccountLabel
# ---------------------------------------------------------------------------

class TestPaperAccountLabel:
    @pytest.mark.parametrize("label", [
        "my-api_key-account",
        "paper-secret-label",
        "paper-token-account",
        "account-password-1",
        "credential-store-1",
        "auth-paper-account",
    ])
    def test_credential_like_label_is_blocked_provenance(self, label):
        r = _run(_valid_artifact(paper_account_label=label))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE
        assert "account.paper_account_label" in r.criteria_failed

    def test_blank_label_is_blocked_provenance(self):
        r = _run(_valid_artifact(paper_account_label="   "))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE

    def test_plain_label_passes(self):
        r = _run(_valid_artifact(paper_account_label="alpaca-paper-secondary"))
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestApprovalStatusShape
# ---------------------------------------------------------------------------

class TestApprovalStatusShape:
    @pytest.mark.parametrize("status_value", ["NOT_REVIEWED", "DRAFT", "REJECTED_APPROVAL_REVIEW"])
    def test_not_yet_or_never_approved_status_returns_blocked(self, status_value):
        r = _run(_valid_artifact(approval_status=status_value))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus(status_value)
        assert r.criteria_failed == ()

    def test_invalid_approval_status_is_rejected_approval_review(self):
        r = _run(_valid_artifact(approval_status="SOMETHING_MADE_UP"))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.REJECTED_APPROVAL_REVIEW
        assert "approval.status" in r.criteria_failed

    def test_missing_approval_status_is_rejected_approval_review(self):
        artifact = _valid_artifact()
        del artifact["approval_status"]
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.REJECTED_APPROVAL_REVIEW


# ---------------------------------------------------------------------------
# TestForbiddenFieldValueScan
# ---------------------------------------------------------------------------

class TestForbiddenFieldValueScan:
    def test_forbidden_key_name_is_blocked_safety(self):
        artifact = _valid_artifact()
        artifact["api_key"] = "should-never-be-here"
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "forbidden.scan" in r.criteria_failed

    @pytest.mark.parametrize("forbidden_text", [
        "please call submit_order on entry",
        "place_order at the open",
        "use os.environ to read the key",
        "wire up live_submit before running",
        "see env: ALPACA_PAPER_KEY",
        "set live_trading_approved=true to go live",
        "set live_order_submission_approved=true",
    ])
    def test_forbidden_string_value_is_blocked_safety(self, forbidden_text):
        r = _run(_valid_artifact(notes=forbidden_text))
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY
        assert "forbidden.scan" in r.criteria_failed

    def test_forbidden_substring_in_nested_structure_is_blocked_safety(self):
        artifact = _valid_artifact()
        artifact["nested"] = {"inner": ["fine", "account_number: 123456"]}
        r = _run(artifact)
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_SAFETY

    def test_live_flags_set_to_false_keys_are_allowed(self):
        # The legitimate field names live_trading_approved /
        # live_order_submission_approved, with value False, must not trip
        # the forbidden scan -- only an embedded "...=true" phrase is
        # forbidden.
        r = _run(_valid_artifact())
        assert "forbidden.scan" not in r.criteria_failed

    def test_harmless_words_are_allowed(self):
        r = _run(_valid_artifact(
            notes="This is a paper trading approval for the market session; trading remains bounded by approval gates.",
        ))
        assert "forbidden.scan" not in r.criteria_failed
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestCriteriaDeterminism
# ---------------------------------------------------------------------------

class TestCriteriaDeterminism:
    def test_criteria_checked_is_deterministic(self):
        r1 = _run()
        r2 = _run()
        assert r1.criteria_checked == r2.criteria_checked

    def test_criteria_failed_is_deterministic(self):
        artifact = _valid_artifact(max_orders_per_day=99)
        r1 = _run(copy.deepcopy(artifact))
        r2 = _run(copy.deepcopy(artifact))
        assert r1.criteria_failed == r2.criteria_failed

    def test_criteria_checked_includes_expected_names(self):
        r = _run()
        expected = {
            "schema.artifact", "artifact.type", "artifact.scope",
            "safety.live_trading_approved", "safety.live_order_submission_approved",
            "safety.dry_run_required", "safety.human_confirmation_required",
            "safety.kill_switch_required",
            "provenance.candidate_id", "provenance.run_id", "provenance.source_git_sha",
            "evidence.paper_config_schema_version", "evidence.paper_config_hash",
            "evidence.simulation_result_hash", "evidence.architecture_review_reference",
            "evidence.invariant_test_reference",
            "approval.approved_by", "approval.approved_at_utc", "approval.expires_at_utc",
            "risk.max_notional_per_position", "risk.max_position_fraction",
            "risk.max_daily_loss", "risk.max_drawdown_stop", "risk.max_orders_per_day",
            "allowlist.allowed_symbols", "allowlist.allowed_intervals",
            "allowlist.allowed_strategy_families", "allowlist.allowed_order_types",
            "allowlist.allowed_session",
            "account.paper_account_label",
            "approval.status", "approval.notes", "approval.known_limitations",
            "forbidden.scan",
        }
        assert expected.issubset(set(r.criteria_checked))


# ---------------------------------------------------------------------------
# TestPureFunction
# ---------------------------------------------------------------------------

class TestPureFunction:
    def test_does_not_mutate_input(self):
        artifact = _valid_artifact()
        snapshot = copy.deepcopy(artifact)
        validate_paper_approval_artifact(artifact)
        assert artifact == snapshot

    def test_same_input_same_output(self):
        artifact = _valid_artifact()
        r1 = validate_paper_approval_artifact(copy.deepcopy(artifact))
        r2 = validate_paper_approval_artifact(copy.deepcopy(artifact))
        assert r1 == r2

    def test_result_safety_flags_always_false(self):
        for artifact in (
            _valid_artifact(),
            _valid_artifact(approval_status="DRAFT"),
            _valid_artifact(live_trading_approved=True),
            _valid_artifact(max_orders_per_day=999),
            {},
        ):
            r = validate_paper_approval_artifact(artifact)
            for flag in _SAFETY_FLAG_NAMES:
                assert getattr(r, flag) is False

    def test_empty_dict_is_blocked(self):
        r = validate_paper_approval_artifact({})
        assert r.result == "BLOCKED"
        assert r.status == PaperApprovalStatus.BLOCKED_PROVENANCE


# ---------------------------------------------------------------------------
# TestNoForbiddenPatternsInSource
# ---------------------------------------------------------------------------

class TestNoForbiddenPatternsInSource:
    """Confirm the validator module performs no I/O and contains no actual
    broker/network/credential/order imports or calls. Forbidden words may
    appear as scan-list string literals (the validator's own forbidden
    substring tuple) -- never as actual imports or calls."""

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
            assert pattern not in source, f"forbidden import pattern found: {pattern!r}"

    def test_no_broker_or_live_submit_references(self):
        source = self._source()
        for pattern in ("Alpaca" + "BrokerAdapter", "alpaca_" + "trade_api", "live_readiness_gate", "live_submit_enablement_gate"):
            assert pattern not in source

    def test_no_file_io(self):
        source = self._source()
        for pattern in (
            "open(", "Path(",
            "read" + "_text(", "write" + "_text(",
            "read" + "_bytes(", "write" + "_bytes(",
        ):
            assert pattern not in source, f"forbidden file I/O pattern found: {pattern!r}"

    def test_no_network_or_env_calls(self):
        source = self._source()
        for pattern in (
            "requests.", "urllib.", "socket.",
            "os." + "environ[", "os." + "environ.get(", "getenv(",
        ):
            assert pattern not in source

    def test_no_actual_order_action_calls(self):
        source = self._source()
        for pattern in ("." + "submit_order(", "." + "place_order(", "broker.submit"):
            assert pattern not in source

    def test_forbidden_substrings_present_only_as_scan_literals(self):
        # The validator legitimately contains the literal strings
        # "submit_order", "place_order", "os.environ", and "live_submit"
        # inside its own _FORBIDDEN_SUBSTRINGS scan tuple -- this confirms
        # they exist there as scan literals, and that no actual usage
        # pattern (checked above) is present.
        source = self._source()
        assert '"submit_order"' in source
        assert '"place_order"' in source
        assert '"os.environ"' in source
        assert '"live_submit"' in source
        assert "import os" not in source
        assert "." + "submit_order(" not in source
        assert "." + "place_order(" not in source

    def test_module_is_pure_no_logging_of_secrets(self):
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
            "import " + "requests", "import " + "urllib", "import " + "aiohttp",
            "import " + "alpaca", "import " + "socket", "import " + "subprocess",
            "Alpaca" + "BrokerAdapter", "alpaca_" + "trade_api",
        ):
            assert pattern not in source

    def test_no_actual_order_or_env_calls(self):
        source = self._source()
        for pattern in (
            "." + "submit_order(", "." + "place_order(",
            "os." + "environ[", "os." + "environ.get(",
        ):
            assert pattern not in source
