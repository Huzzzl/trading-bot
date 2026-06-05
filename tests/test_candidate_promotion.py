"""
tests/test_candidate_promotion.py
-------------------------------------
S14: Tests for the pure offline candidate promotion evaluator.

No file I/O, no network, no broker/API, no backtests, no market data.
All inputs are plain dict fixtures built in-test.
"""

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

import src.research.candidate_promotion as _mod
from src.research.candidate_promotion import (
    CandidatePromotionResult,
    PromotionStatus,
    evaluate_candidate_for_promotion,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_CANDIDATE_ID = "A_SPY_60m_trend_breakout_1to2d"
_RUN_ID = "20260604T000000Z_PASS"
_GIT_SHA = "a" * 40

_SAFE_FLAGS: dict[str, bool] = dict(
    broker_calls_made=False,
    credentials_read=False,
    network_calls_made=False,
    order_action_requested=False,
    live_trading_allowed=False,
)


def _make_report(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid report dict that passes all criteria."""
    base: dict[str, Any] = {
        "schema_version": "S6/1.0",
        "candidate": {
            "candidate_id": _CANDIDATE_ID,
            "group": "A",
            "symbol": "SPY",
            "interval": "60m",
            "strategy_family": "trend_breakout",
            "holding_horizon": "one_to_two_days",
        },
        "summary": {
            "result": "PASS",
            "blocker": None,
            "candidate_id": _CANDIDATE_ID,
            "splits_requested": 4,
            "splits_evaluated": 4,
            "splits_passed": 4,
            "splits_blocked": 0,
            "splits_error": 0,
            "validations_passed": 4,
            "validations_blocked": 0,
            "average_monthly_return_mean": 0.015,
            "average_monthly_return_min": 0.005,
            "max_drawdown_worst": -0.12,
            "total_trades_sum": 80,
        },
        "splits": [],
        "safety": dict(_SAFE_FLAGS),
        "notes": [],
    }
    for k, v in overrides.items():
        base[k] = v
    return base


def _make_manifest(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid manifest dict."""
    base: dict[str, Any] = {
        "schema_version": "S9/1.0",
        "run_id": _RUN_ID,
        "git_commit_sha": _GIT_SHA,
        "result": "PASS",
        "safety": dict(_SAFE_FLAGS),
        "files": {
            "pipeline_summary": "pipeline_summary.json",
            "reports": [f"{_CANDIDATE_ID}.json"],
        },
    }
    for k, v in overrides.items():
        base[k] = v
    return base


def _patch_summary(report: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Return a copy of report with summary fields patched."""
    r = deepcopy(report)
    r["summary"].update(kwargs)
    return r


def _run(report: dict[str, Any] | None = None,
         manifest: dict[str, Any] | None = None) -> CandidatePromotionResult:
    return evaluate_candidate_for_promotion(
        report or _make_report(),
        manifest_dict=manifest or _make_manifest(),
    )


# ---------------------------------------------------------------------------
# TestPromotionStatusEnum
# ---------------------------------------------------------------------------

class TestPromotionStatusEnum:
    def test_all_six_values_present(self):
        values = {s.value for s in PromotionStatus}
        assert values == {
            "NOT_REVIEWED",
            "PAPER_CANDIDATE_ELIGIBLE",
            "NEEDS_MORE_DATA",
            "REJECTED",
            "BLOCKED_SAFETY",
            "BLOCKED_SCHEMA",
        }

    def test_is_str_enum(self):
        assert PromotionStatus.PAPER_CANDIDATE_ELIGIBLE == "PAPER_CANDIDATE_ELIGIBLE"

    def test_each_value_accessible(self):
        assert PromotionStatus("REJECTED") is PromotionStatus.REJECTED
        assert PromotionStatus("BLOCKED_SAFETY") is PromotionStatus.BLOCKED_SAFETY
        assert PromotionStatus("BLOCKED_SCHEMA") is PromotionStatus.BLOCKED_SCHEMA
        assert PromotionStatus("NEEDS_MORE_DATA") is PromotionStatus.NEEDS_MORE_DATA
        assert PromotionStatus("NOT_REVIEWED") is PromotionStatus.NOT_REVIEWED


# ---------------------------------------------------------------------------
# TestCandidatePromotionResultDataclass
# ---------------------------------------------------------------------------

class TestCandidatePromotionResultDataclass:
    def _make(self, **overrides: Any) -> CandidatePromotionResult:
        defaults = dict(
            result="PASS",
            blocker=None,
            candidate_id=_CANDIDATE_ID,
            run_id=_RUN_ID,
            status=PromotionStatus.PAPER_CANDIDATE_ELIGIBLE,
            criteria_checked=("schema.report",),
            criteria_failed=(),
            **_SAFE_FLAGS,
        )
        defaults.update(overrides)
        return CandidatePromotionResult(**defaults)

    def test_can_create(self):
        r = self._make()
        assert r.result == "PASS"
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_frozen(self):
        r = self._make()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            r.result = "BLOCKED"  # type: ignore[misc]

    def test_safety_flags_present(self):
        r = self._make()
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_optional_none_fields(self):
        r = self._make(candidate_id=None, run_id=None, blocker=None)
        assert r.candidate_id is None
        assert r.run_id is None

    def test_criteria_fields_are_tuples(self):
        r = self._make(criteria_checked=("a", "b"), criteria_failed=("b",))
        assert isinstance(r.criteria_checked, tuple)
        assert isinstance(r.criteria_failed, tuple)


# ---------------------------------------------------------------------------
# TestFullyEligibleCandidate
# ---------------------------------------------------------------------------

class TestFullyEligibleCandidate:
    def test_pass_result(self):
        r = _run()
        assert r.result == "PASS"

    def test_status_paper_candidate_eligible(self):
        r = _run()
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_blocker_is_none(self):
        r = _run()
        assert r.blocker is None

    def test_candidate_id_extracted(self):
        r = _run()
        assert r.candidate_id == _CANDIDATE_ID

    def test_run_id_extracted(self):
        r = _run()
        assert r.run_id == _RUN_ID

    def test_no_criteria_failed(self):
        r = _run()
        assert r.criteria_failed == ()

    def test_safety_flags_all_false(self):
        r = _run()
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.network_calls_made
        assert not r.order_action_requested
        assert not r.live_trading_allowed

    def test_criteria_checked_non_empty(self):
        r = _run()
        assert len(r.criteria_checked) > 0


# ---------------------------------------------------------------------------
# TestSchemaBlocking
# ---------------------------------------------------------------------------

class TestSchemaBlocking:
    def test_unsupported_report_schema(self):
        report = _make_report(schema_version="S5/1.0")
        r = _run(report)
        assert r.result == "BLOCKED"
        assert r.status is PromotionStatus.BLOCKED_SCHEMA
        assert "schema.report" in r.criteria_failed

    def test_missing_report_schema(self):
        report = _make_report()
        del report["schema_version"]
        r = _run(report)
        assert r.status is PromotionStatus.BLOCKED_SCHEMA

    def test_unsupported_manifest_schema(self):
        manifest = _make_manifest(schema_version="S8/1.0")
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.BLOCKED_SCHEMA
        assert "schema.manifest" in r.criteria_failed

    def test_missing_manifest_schema(self):
        manifest = _make_manifest()
        del manifest["schema_version"]
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.BLOCKED_SCHEMA

    def test_schema_check_before_safety(self):
        # Schema check must precede safety check
        report = _make_report(schema_version="WRONG")
        report["safety"]["broker_calls_made"] = True
        r = _run(report)
        assert r.status is PromotionStatus.BLOCKED_SCHEMA


# ---------------------------------------------------------------------------
# TestSafetyFlagBlocking
# ---------------------------------------------------------------------------

class TestSafetyFlagBlocking:
    def _report_with_flag(self, flag: str) -> dict[str, Any]:
        r = _make_report()
        r["safety"][flag] = True
        return r

    def _manifest_with_flag(self, flag: str) -> dict[str, Any]:
        m = _make_manifest()
        m["safety"][flag] = True
        return m

    def test_report_broker_calls_made_blocks(self):
        r = _run(self._report_with_flag("broker_calls_made"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY
        assert "safety.report" in r.criteria_failed

    def test_report_credentials_read_blocks(self):
        r = _run(self._report_with_flag("credentials_read"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY

    def test_report_network_calls_made_blocks(self):
        r = _run(self._report_with_flag("network_calls_made"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY

    def test_report_order_action_requested_blocks(self):
        r = _run(self._report_with_flag("order_action_requested"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY

    def test_report_live_trading_allowed_blocks(self):
        r = _run(self._report_with_flag("live_trading_allowed"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY

    def test_manifest_broker_calls_made_blocks(self):
        r = _run(manifest=self._manifest_with_flag("broker_calls_made"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY
        assert "safety.manifest" in r.criteria_failed

    def test_safety_blocking_does_not_set_result_flags(self):
        r = _run(self._report_with_flag("network_calls_made"))
        assert r.status is PromotionStatus.BLOCKED_SAFETY
        # The evaluator itself never makes network calls
        assert not r.network_calls_made


# ---------------------------------------------------------------------------
# TestRejected
# ---------------------------------------------------------------------------

class TestRejected:
    def test_summary_result_blocked(self):
        r = _run(_patch_summary(_make_report(), result="BLOCKED"))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.result" in r.criteria_failed

    def test_summary_result_error(self):
        r = _run(_patch_summary(_make_report(), result="ERROR"))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.result" in r.criteria_failed

    def test_splits_error_positive(self):
        r = _run(_patch_summary(_make_report(), splits_error=1))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_error" in r.criteria_failed

    def test_validations_blocked_positive(self):
        r = _run(_patch_summary(_make_report(), validations_blocked=2))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.validations_blocked" in r.criteria_failed

    def test_total_trades_below_minimum(self):
        r = _run(_patch_summary(_make_report(), total_trades_sum=29))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.total_trades_sum" in r.criteria_failed

    def test_total_trades_exactly_at_minimum_passes(self):
        r = _run(_patch_summary(_make_report(), total_trades_sum=30))
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_total_trades_none(self):
        r = _run(_patch_summary(_make_report(), total_trades_sum=None))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.total_trades_sum" in r.criteria_failed

    def test_average_monthly_return_zero(self):
        r = _run(_patch_summary(_make_report(), average_monthly_return_mean=0.0))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.average_monthly_return_mean" in r.criteria_failed

    def test_average_monthly_return_negative(self):
        r = _run(_patch_summary(_make_report(), average_monthly_return_mean=-0.01))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.average_monthly_return_mean" in r.criteria_failed

    def test_max_drawdown_worse_than_floor(self):
        r = _run(_patch_summary(_make_report(), max_drawdown_worst=-0.26))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.max_drawdown_worst" in r.criteria_failed

    def test_max_drawdown_exactly_at_floor_passes(self):
        r = _run(_patch_summary(_make_report(), max_drawdown_worst=-0.25))
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_max_drawdown_none(self):
        r = _run(_patch_summary(_make_report(), max_drawdown_worst=None))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.max_drawdown_worst" in r.criteria_failed

    def test_session_end_frequency_too_high(self):
        report = _make_report(splits=[
            {"metrics": {"session_end_frequency": 0.96, "exposure_pct": 0.5}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.REJECTED
        assert "splits.session_end_frequency" in r.criteria_failed

    def test_session_end_frequency_exactly_at_threshold_passes(self):
        # 0.95 is not > 0.95, so it should pass
        report = _make_report(splits=[
            {"metrics": {"session_end_frequency": 0.95, "exposure_pct": 0.5}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_missing_candidate_id(self):
        report = _make_report()
        report["candidate"]["candidate_id"] = None
        r = _run(report)
        assert r.status is PromotionStatus.REJECTED
        assert "candidate_id.present" in r.criteria_failed

    def test_missing_git_commit_sha(self):
        manifest = _make_manifest(git_commit_sha=None)
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.git_commit_sha" in r.criteria_failed

    def test_empty_git_commit_sha(self):
        manifest = _make_manifest(git_commit_sha="")
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.git_commit_sha" in r.criteria_failed


# ---------------------------------------------------------------------------
# TestManifestInventory
# ---------------------------------------------------------------------------

class TestManifestInventory:
    def test_missing_files_key_rejected(self):
        manifest = _make_manifest()
        del manifest["files"]
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.inventory" in r.criteria_failed

    def test_missing_reports_key_rejected(self):
        manifest = _make_manifest()
        del manifest["files"]["reports"]
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.inventory" in r.criteria_failed

    def test_reports_not_list_rejected(self):
        manifest = _make_manifest()
        manifest["files"]["reports"] = "not-a-list"
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.inventory" in r.criteria_failed

    def test_reports_none_rejected(self):
        manifest = _make_manifest()
        manifest["files"]["reports"] = None
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.inventory" in r.criteria_failed

    def test_existing_mismatch_still_rejected(self):
        manifest = _make_manifest()
        manifest["files"]["reports"] = ["other_candidate.json"]
        r = _run(manifest=manifest)
        assert r.status is PromotionStatus.REJECTED
        assert "manifest.inventory" in r.criteria_failed

    def test_valid_inventory_passes(self):
        r = _run()
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE
        assert "manifest.inventory" not in r.criteria_failed


# ---------------------------------------------------------------------------
# TestRequiredSplitCounts
# ---------------------------------------------------------------------------

class TestRequiredSplitCounts:
    def test_missing_splits_requested_rejected(self):
        r = _run(_patch_summary(_make_report(), splits_requested=None))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_requested" in r.criteria_failed

    def test_missing_splits_blocked_rejected(self):
        r = _run(_patch_summary(_make_report(), splits_blocked=None))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_blocked" in r.criteria_failed

    def test_nonnumeric_splits_requested_rejected(self):
        r = _run(_patch_summary(_make_report(), splits_requested="four"))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_requested" in r.criteria_failed

    def test_nonnumeric_splits_blocked_rejected(self):
        r = _run(_patch_summary(_make_report(), splits_blocked="three"))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_blocked" in r.criteria_failed

    def test_negative_splits_requested_rejected(self):
        r = _run(_patch_summary(_make_report(), splits_requested=-1))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_requested" in r.criteria_failed

    def test_negative_splits_blocked_rejected(self):
        r = _run(_patch_summary(_make_report(), splits_blocked=-1))
        assert r.status is PromotionStatus.REJECTED
        assert "summary.splits_blocked" in r.criteria_failed

    def test_zero_splits_requested_passes_no_majority_check(self):
        # splits_requested=0 is valid; majority-blocked ratio check is skipped.
        r = _run(_patch_summary(_make_report(), splits_requested=0, splits_blocked=0))
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_criteria_checked_includes_both_names(self):
        r = _run()
        assert "summary.splits_requested" in r.criteria_checked
        assert "summary.splits_blocked" in r.criteria_checked


# ---------------------------------------------------------------------------
# TestNeedsMoreData
# ---------------------------------------------------------------------------

class TestNeedsMoreData:
    def test_average_monthly_return_none(self):
        r = _run(_patch_summary(_make_report(), average_monthly_return_mean=None))
        assert r.status is PromotionStatus.NEEDS_MORE_DATA
        assert r.result == "BLOCKED"
        assert "summary.average_monthly_return_mean" in r.criteria_failed

    def test_majority_blocked_splits(self):
        r = _run(_patch_summary(
            _make_report(),
            splits_requested=4,
            splits_blocked=3,
            splits_passed=1,
        ))
        assert r.status is PromotionStatus.NEEDS_MORE_DATA
        assert "splits.majority_blocked" in r.criteria_failed

    def test_exactly_half_blocked_does_not_trigger_needs_more_data(self):
        # ratio == 0.5 is NOT > 0.5, so should pass
        r = _run(_patch_summary(
            _make_report(),
            splits_requested=4,
            splits_blocked=2,
            splits_passed=2,
        ))
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_low_exposure_extreme_sharpe(self):
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.005, "sharpe_ratio": 7.0}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.NEEDS_MORE_DATA
        assert "splits.low_exposure_sharpe" in r.criteria_failed

    def test_low_exposure_negative_extreme_sharpe(self):
        # abs(-6.0) >= 5.0 → artifact
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.005, "sharpe_ratio": -6.0}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.NEEDS_MORE_DATA

    def test_low_exposure_moderate_sharpe_not_artifact(self):
        # abs(2.0) < 5.0 → not an artifact
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.005, "sharpe_ratio": 2.0}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_normal_exposure_extreme_sharpe_not_artifact(self):
        # exposure >= 0.01 → not an artifact even with extreme Sharpe
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.5, "sharpe_ratio": 10.0}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_low_exposure_extreme_sharpe_primary_key(self):
        # Project metric key is "sharpe" (not "sharpe_ratio")
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.005, "sharpe": 8.0}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.NEEDS_MORE_DATA
        assert "splits.low_exposure_sharpe" in r.criteria_failed

    def test_low_exposure_extreme_sharpe_alias_key(self):
        # "sharpe_ratio" retained as backward-compatible alias
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.005, "sharpe_ratio": 7.0}}
        ])
        r = _run(report)
        assert r.status is PromotionStatus.NEEDS_MORE_DATA

    def test_primary_key_takes_precedence_over_alias(self):
        # "sharpe" present → alias "sharpe_ratio" ignored
        report = _make_report(splits=[
            {"metrics": {"exposure_pct": 0.005, "sharpe": 2.0, "sharpe_ratio": 8.0}}
        ])
        # "sharpe"=2.0 is moderate → no artifact
        r = _run(report)
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE


# ---------------------------------------------------------------------------
# TestCriteriaTracking
# ---------------------------------------------------------------------------

class TestCriteriaTracking:
    def test_criteria_checked_is_deterministic(self):
        r1 = _run()
        r2 = _run()
        assert r1.criteria_checked == r2.criteria_checked

    def test_criteria_checked_includes_all_standard_names(self):
        r = _run()
        checked = r.criteria_checked
        for name in (
            "schema.report",
            "schema.manifest",
            "safety.report",
            "safety.manifest",
            "candidate_id.present",
            "manifest.inventory",
            "manifest.git_commit_sha",
            "summary.result",
            "summary.splits_error",
            "summary.validations_blocked",
            "summary.splits_requested",
            "summary.splits_blocked",
            "summary.total_trades_sum",
            "summary.average_monthly_return_mean",
            "summary.max_drawdown_worst",
            "splits.session_end_frequency",
            "splits.low_exposure_sharpe",
        ):
            assert name in checked, f"criterion {name!r} not in criteria_checked"

    def test_criteria_failed_empty_for_pass(self):
        r = _run()
        assert r.criteria_failed == ()

    def test_criteria_failed_contains_failed_criterion(self):
        r = _run(_patch_summary(_make_report(), splits_error=2))
        assert "summary.splits_error" in r.criteria_failed

    def test_criteria_failed_deterministic(self):
        report = _patch_summary(_make_report(), splits_error=1, validations_blocked=1)
        r1 = evaluate_candidate_for_promotion(report, manifest_dict=_make_manifest())
        r2 = evaluate_candidate_for_promotion(deepcopy(report), manifest_dict=_make_manifest())
        assert r1.criteria_failed == r2.criteria_failed


# ---------------------------------------------------------------------------
# TestPurityAndImmutability
# ---------------------------------------------------------------------------

class TestPurityAndImmutability:
    def test_does_not_mutate_report_dict(self):
        report = _make_report()
        original = deepcopy(report)
        _run(report)
        assert report == original

    def test_does_not_mutate_manifest_dict(self):
        manifest = _make_manifest()
        original = deepcopy(manifest)
        _run(manifest=manifest)
        assert manifest == original

    def test_same_inputs_same_output(self):
        report = _make_report()
        manifest = _make_manifest()
        r1 = evaluate_candidate_for_promotion(report, manifest_dict=manifest)
        r2 = evaluate_candidate_for_promotion(report, manifest_dict=manifest)
        assert r1 == r2

    def test_empty_splits_list_is_valid(self):
        report = _make_report(splits=[])
        r = _run(report)
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE

    def test_multiple_splits_all_safe(self):
        report = _make_report(splits=[
            {"metrics": {"session_end_frequency": 0.1, "exposure_pct": 0.4, "sharpe_ratio": 1.2}},
            {"metrics": {"session_end_frequency": 0.2, "exposure_pct": 0.5, "sharpe_ratio": 0.9}},
        ])
        r = _run(report)
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE


# ---------------------------------------------------------------------------
# TestSafetyFlagsOnResult
# ---------------------------------------------------------------------------

class TestSafetyFlagsOnResult:
    def test_pass_result_flags_all_false(self):
        r = _run()
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.network_calls_made
        assert not r.order_action_requested
        assert not r.live_trading_allowed

    def test_blocked_schema_result_flags_all_false(self):
        r = _run(_make_report(schema_version="BAD"))
        assert r.status is PromotionStatus.BLOCKED_SCHEMA
        assert not r.broker_calls_made
        assert not r.network_calls_made

    def test_rejected_result_flags_all_false(self):
        r = _run(_patch_summary(_make_report(), total_trades_sum=5))
        assert r.status is PromotionStatus.REJECTED
        assert not r.broker_calls_made
        assert not r.credentials_read


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _source(self) -> str:
        return inspect.getsource(_mod)

    def test_no_broker_adapter(self):
        src = self._source()
        assert "AlpacaBrokerAdapter" not in src
        assert "AlpacaLiveSubmitBroker" not in src

    def test_no_requests(self):
        src = self._source()
        assert "import requests" not in src
        assert "import aiohttp" not in src

    def test_no_os_environ(self):
        assert "os.environ" not in self._source()

    def test_no_subprocess(self):
        assert "subprocess" not in self._source()

    def test_no_socket(self):
        assert "import socket" not in self._source()

    def test_no_urllib(self):
        assert "urllib" not in self._source()

    def test_no_live_submit(self):
        assert "live_submit" not in self._source()

    def test_no_order_action(self):
        src = self._source()
        assert "submit_order" not in src
        assert "place_order" not in src

    def test_no_alpaca(self):
        assert "alpaca" not in self._source().lower()

    def test_no_file_open(self):
        assert "open(" not in self._source()

    def test_no_read_text(self):
        assert "read_text" not in self._source()

    def test_no_write_text(self):
        assert "write_text" not in self._source()

    def test_no_pathlib_path_init(self):
        # No Path(...) file construction
        assert "Path(" not in self._source()
