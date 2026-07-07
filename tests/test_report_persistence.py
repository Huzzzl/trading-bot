"""
tests/test_report_persistence.py
--------------------------------------
Tests for S10: controlled local report persistence.

All tests use injected fakes and tmp_path; no real backtests, no real
market data, no broker/API/credential/env/network access.
"""

from __future__ import annotations

import inspect
import json
import pathlib
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

import src.research.report_persistence as _persistence_mod
from src.research.pipeline_orchestrator import (
    CandidateFilter,
    PipelineRunResult,
    PipelineSummary,
)
from src.research.report_persistence import (
    ReportPersistenceResult,
    _REPO_ROOT,
    persist_pipeline_run_result,
)
from src.research.report_schema import (
    ResearchReport,
    ResearchReportCandidate,
    ResearchReportSummary,
)
from src.research.report_snapshot_runner import ReportSnapshotRunResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXED_TS = "2026-06-04T00:00:00+00:00"
_FIXED_RUN_ID = "20260604T000000Z_PASS"
_FIXED_SHA = "a" * 40


# ---------------------------------------------------------------------------
# Fake fixture builders
# ---------------------------------------------------------------------------

def _make_report(
    candidate_id: str = "A_SPY_60m_trend_breakout_1to2d",
    result: str = "PASS",
    avg_return: float | None = 0.05,
    drawdown: float | None = -0.10,
    trades: int | None = 10,
) -> ResearchReport:
    return ResearchReport(
        schema_version="S6/1.0",
        generated_at_utc=_FIXED_TS,
        candidate=ResearchReportCandidate(
            candidate_id=candidate_id,
            group="A",
            symbol="SPY",
            interval="60m",
            strategy_family="trend_breakout",
            holding_horizon="one_to_two_days",
        ),
        summary=ResearchReportSummary(
            result=result,
            blocker=None if result == "PASS" else "test-blocker",
            candidate_id=candidate_id,
            splits_requested=1,
            splits_evaluated=1,
            splits_passed=1 if result == "PASS" else 0,
            splits_blocked=1 if result == "BLOCKED" else 0,
            splits_error=1 if result == "ERROR" else 0,
            validations_passed=1 if result == "PASS" else 0,
            validations_blocked=0,
            average_monthly_return_mean=avg_return,
            average_monthly_return_min=avg_return,
            max_drawdown_worst=drawdown,
            total_trades_sum=trades,
        ),
        splits=(),
        safety={
            "broker_calls_made": False,
            "credentials_read": False,
            "network_calls_made": False,
            "order_action_requested": False,
            "live_trading_allowed": False,
        },
        notes=(),
    )


def _make_snapshot(
    reports: list[ResearchReport] | None = None,
    **flag_overrides: bool,
) -> ReportSnapshotRunResult:
    if reports is None:
        reports = [_make_report()]
    flags: dict[str, bool] = dict(
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
    flags.update(flag_overrides)
    n_pass = sum(1 for r in reports if r.summary.result == "PASS")
    n_error = sum(1 for r in reports if r.summary.result == "ERROR")
    result = "ERROR" if n_error else ("PASS" if n_pass else "BLOCKED")
    blocker = None if result == "PASS" else "test-blocker"
    return ReportSnapshotRunResult(
        result=result,
        blocker=blocker,
        candidates_requested=len(reports),
        reports_created=len(reports),
        reports=tuple(reports),
        json_reports=(),
        **flags,
    )


def _make_pipeline_result(
    reports: list[ResearchReport] | None = None,
    candidate_filter: CandidateFilter | None = None,
    **flag_overrides: bool,
) -> PipelineRunResult:
    if reports is None:
        reports = [_make_report()]
    snap = _make_snapshot(reports, **flag_overrides)
    n_pass = sum(1 for r in reports if r.summary.result == "PASS")
    n_blocked = sum(1 for r in reports if r.summary.result == "BLOCKED")
    n_error = sum(1 for r in reports if r.summary.result == "ERROR")

    pass_with_ret = [
        r for r in reports
        if r.summary.result == "PASS" and r.summary.average_monthly_return_mean is not None
    ]
    if pass_with_ret:
        best = max(pass_with_ret, key=lambda r: r.summary.average_monthly_return_mean)
        best_id: str | None = best.summary.candidate_id
        best_ret: float | None = best.summary.average_monthly_return_mean
    else:
        best_id = None
        best_ret = None

    dds = [r.summary.max_drawdown_worst for r in reports if r.summary.max_drawdown_worst is not None]
    worst_dd: float | None = min(dds) if dds else None
    trade_vals = [r.summary.total_trades_sum for r in reports if r.summary.total_trades_sum is not None]
    total_trades: int | None = sum(trade_vals) if trade_vals else None

    summary = PipelineSummary(
        result=snap.result,
        blocker=snap.blocker,
        candidates_selected=len(reports),
        reports_created=len(reports),
        reports_passed=n_pass,
        reports_blocked=n_blocked,
        reports_error=n_error,
        best_candidate_id=best_id,
        best_average_monthly_return=best_ret,
        worst_max_drawdown=worst_dd,
        total_trades_sum=total_trades,
    )
    flags: dict[str, bool] = dict(
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
    flags.update(flag_overrides)
    return PipelineRunResult(
        result=snap.result,
        blocker=snap.blocker,
        filter=candidate_filter or CandidateFilter(),
        summary=summary,
        snapshot=snap,
        **flags,
    )


def _persist(pipeline_result: PipelineRunResult, tmp_path: pathlib.Path, **kwargs: Any) -> ReportPersistenceResult:
    """Helper: call persist_pipeline_run_result with a safe tmp_path output_dir."""
    output_dir = tmp_path / "reports"
    return persist_pipeline_run_result(
        pipeline_result,
        output_dir=output_dir,
        generated_at_utc=_FIXED_TS,
        allow_unsafe_path=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TestReportPersistenceResultDataclass
# ---------------------------------------------------------------------------

class TestReportPersistenceResultDataclass:
    def _make(self, **overrides: Any) -> ReportPersistenceResult:
        defaults = dict(
            result="PASS",
            blocker=None,
            output_dir="/tmp/test",
            run_id="20260604T000000Z_PASS",
            manifest_path="/tmp/test/run/manifest.json",
            pipeline_summary_path="/tmp/test/run/pipeline_summary.json",
            report_paths=("report1.json",),
            markdown_summary_path=None,
            files_written=("f1.json", "f2.json"),
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )
        defaults.update(overrides)
        return ReportPersistenceResult(**defaults)

    def test_can_create(self):
        r = self._make()
        assert r.result == "PASS"
        assert r.run_id == "20260604T000000Z_PASS"

    def test_frozen(self):
        r = self._make()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            r.result = "ERROR"  # type: ignore[misc]

    def test_all_safety_flags_present(self):
        r = self._make()
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_optional_none_fields(self):
        r = self._make(output_dir=None, run_id=None, manifest_path=None,
                       pipeline_summary_path=None, markdown_summary_path=None,
                       report_paths=(), files_written=())
        assert r.output_dir is None
        assert r.run_id is None
        assert r.report_paths == ()


# ---------------------------------------------------------------------------
# TestOutputDirValidation
# ---------------------------------------------------------------------------

class TestOutputDirValidation:
    def test_none_output_dir_returns_blocked(self):
        pr = _make_pipeline_result()
        result = persist_pipeline_run_result(pr, output_dir=None)
        assert result.result == "BLOCKED"
        assert "output_dir" in result.blocker.lower()
        assert result.files_written == ()

    def test_empty_string_output_dir_returns_blocked(self):
        pr = _make_pipeline_result()
        result = persist_pipeline_run_result(pr, output_dir="")
        assert result.result == "BLOCKED"
        assert result.files_written == ()

    def test_data_dir_always_refused(self):
        pr = _make_pipeline_result()
        result = persist_pipeline_run_result(
            pr,
            output_dir=str(_REPO_ROOT / "data" / "test_reports"),
            allow_unsafe_path=True,
        )
        assert result.result == "BLOCKED"
        assert "data/" in result.blocker or "data" in result.blocker
        assert result.files_written == ()

    def test_output_dir_always_refused(self):
        pr = _make_pipeline_result()
        result = persist_pipeline_run_result(
            pr,
            output_dir=str(_REPO_ROOT / "output" / "test_reports"),
            allow_unsafe_path=True,
        )
        assert result.result == "BLOCKED"
        assert "output/" in result.blocker or "output" in result.blocker
        assert result.files_written == ()

    def test_absolute_path_refused_without_flag(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = persist_pipeline_run_result(
            pr,
            output_dir=str(tmp_path / "reports"),
            generated_at_utc=_FIXED_TS,
            allow_unsafe_path=False,
        )
        assert result.result == "BLOCKED"
        assert "allow_unsafe_path" in result.blocker
        assert result.files_written == ()

    def test_absolute_path_allowed_with_flag(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = persist_pipeline_run_result(
            pr,
            output_dir=str(tmp_path / "reports"),
            generated_at_utc=_FIXED_TS,
            allow_unsafe_path=True,
        )
        assert result.result == "PASS"
        assert result.run_id is not None


# ---------------------------------------------------------------------------
# TestSafetyFlagRefusal
# ---------------------------------------------------------------------------

class TestSafetyFlagRefusal:
    def _run(self, flag: str, tmp_path: pathlib.Path) -> ReportPersistenceResult:
        pr = _make_pipeline_result(**{flag: True})
        return persist_pipeline_run_result(
            pr,
            output_dir=str(tmp_path / "reports"),
            generated_at_utc=_FIXED_TS,
            allow_unsafe_path=True,
        )

    def test_refusal_broker_calls_made(self, tmp_path: pathlib.Path):
        result = self._run("broker_calls_made", tmp_path)
        assert result.result == "BLOCKED"
        assert "broker_calls_made" in result.blocker
        assert result.files_written == ()

    def test_refusal_credentials_read(self, tmp_path: pathlib.Path):
        result = self._run("credentials_read", tmp_path)
        assert result.result == "BLOCKED"
        assert "credentials_read" in result.blocker
        assert result.files_written == ()

    def test_refusal_network_calls_made(self, tmp_path: pathlib.Path):
        result = self._run("network_calls_made", tmp_path)
        assert result.result == "BLOCKED"
        assert "network_calls_made" in result.blocker
        assert result.files_written == ()

    def test_refusal_order_action_requested(self, tmp_path: pathlib.Path):
        result = self._run("order_action_requested", tmp_path)
        assert result.result == "BLOCKED"
        assert "order_action_requested" in result.blocker
        assert result.files_written == ()

    def test_refusal_live_trading_allowed(self, tmp_path: pathlib.Path):
        result = self._run("live_trading_allowed", tmp_path)
        assert result.result == "BLOCKED"
        assert "live_trading_allowed" in result.blocker
        assert result.files_written == ()

    def test_safety_flags_on_result_always_false(self, tmp_path: pathlib.Path):
        result = self._run("broker_calls_made", tmp_path)
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.network_calls_made is False
        assert result.order_action_requested is False
        assert result.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestOverwriteBehavior
# ---------------------------------------------------------------------------

class TestOverwriteBehavior:
    def test_blocked_when_run_dir_exists(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        # First write succeeds.
        r1 = _persist(pr, tmp_path)
        assert r1.result == "PASS"
        # Second write without allow_overwrite is blocked.
        r2 = _persist(pr, tmp_path)
        assert r2.result == "BLOCKED"
        assert "allow_overwrite" in r2.blocker
        assert r2.files_written == ()

    def test_allow_overwrite_replaces(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        r1 = _persist(pr, tmp_path)
        assert r1.result == "PASS"
        r2 = _persist(pr, tmp_path, allow_overwrite=True)
        assert r2.result == "PASS"
        assert r2.run_id == r1.run_id


# ---------------------------------------------------------------------------
# TestSuccessfulPersistence
# ---------------------------------------------------------------------------

class TestSuccessfulPersistence:
    def test_manifest_created(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        assert result.result == "PASS"
        assert pathlib.Path(result.manifest_path).exists()

    def test_pipeline_summary_created(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        assert pathlib.Path(result.pipeline_summary_path).exists()

    def test_report_json_created(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        assert len(result.report_paths) == 1
        assert pathlib.Path(result.report_paths[0]).exists()

    def test_include_markdown_writes_summary_md(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path, include_markdown=True)
        assert result.markdown_summary_path is not None
        assert pathlib.Path(result.markdown_summary_path).exists()
        content = pathlib.Path(result.markdown_summary_path).read_text(encoding="utf-8")
        assert "PASS" in content
        assert _FIXED_RUN_ID in content

    def test_include_markdown_false_no_summary_md(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path, include_markdown=False)
        assert result.markdown_summary_path is None

    def test_deterministic_run_id_for_fixed_ts(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        assert result.run_id == _FIXED_RUN_ID

    def test_files_written_matches_actual_files(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        for f in result.files_written:
            assert pathlib.Path(f).exists(), f"expected file {f} not found"

    def test_manifest_files_list_matches_actual_reports(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        run_dir = pathlib.Path(result.manifest_path).parent
        for fname in manifest["files"]["reports"]:
            assert (run_dir / "reports" / fname).exists()

    def test_manifest_schema_version(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "S9/1.0"

    def test_best_candidate_in_manifest(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["best_candidate_id"] == "A_SPY_60m_trend_breakout_1to2d"
        assert manifest["best_average_monthly_return"] == pytest.approx(0.05)

    def test_worst_drawdown_in_manifest(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["worst_max_drawdown"] == pytest.approx(-0.10)

    def test_total_trades_in_manifest(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["total_trades_sum"] == 10

    def test_candidate_filter_serialized_in_pipeline_summary(self, tmp_path: pathlib.Path):
        f = CandidateFilter(groups=("A",), symbols=("SPY",))
        pr = _make_pipeline_result(candidate_filter=f)
        result = _persist(pr, tmp_path)
        ps = json.loads(pathlib.Path(result.pipeline_summary_path).read_text(encoding="utf-8"))
        assert ps["candidate_filter"]["groups"] == ["A"]
        assert ps["candidate_filter"]["symbols"] == ["SPY"]

    def test_git_commit_sha_in_manifest(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path, git_commit_sha=_FIXED_SHA)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["git_commit_sha"] == _FIXED_SHA

    def test_git_commit_sha_none_when_not_supplied(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        manifest = json.loads(pathlib.Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["git_commit_sha"] is None

    def test_report_json_is_valid_research_report(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        report_data = json.loads(pathlib.Path(result.report_paths[0]).read_text(encoding="utf-8"))
        assert report_data["schema_version"] == "S6/1.0"
        assert "candidate" in report_data
        assert "summary" in report_data

    def test_deterministic_repeated_write(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        r1 = _persist(pr, tmp_path)
        assert r1.result == "PASS"
        r2 = _persist(pr, tmp_path, allow_overwrite=True)
        assert r2.result == "PASS"
        assert r1.run_id == r2.run_id
        m1 = pathlib.Path(r1.manifest_path).read_text(encoding="utf-8")
        m2 = pathlib.Path(r2.manifest_path).read_text(encoding="utf-8")
        assert m1 == m2

    def test_multiple_reports_all_written(self, tmp_path: pathlib.Path):
        reports = [
            _make_report("cand_a", "PASS", 0.05, -0.10, 5),
            _make_report("cand_b", "BLOCKED"),
        ]
        pr = _make_pipeline_result(reports=reports)
        result = _persist(pr, tmp_path)
        assert len(result.report_paths) == 2
        for p in result.report_paths:
            assert pathlib.Path(p).exists()

    def test_result_and_run_id_in_result(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        assert result.result == "PASS"
        assert result.run_id == _FIXED_RUN_ID
        assert result.output_dir is not None

    def test_safety_flags_always_false_on_success(self, tmp_path: pathlib.Path):
        pr = _make_pipeline_result()
        result = _persist(pr, tmp_path)
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.network_calls_made is False
        assert result.order_action_requested is False
        assert result.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestAtomicWrite
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_cleanup_on_injected_failure(self, tmp_path: pathlib.Path):
        """Temp dir must be cleaned up; no partial run directory left."""
        pr = _make_pipeline_result()
        output_dir = tmp_path / "reports"

        def _fail():
            raise RuntimeError("simulated write failure")

        with pytest.raises(RuntimeError, match="simulated write failure"):
            persist_pipeline_run_result(
                pr,
                output_dir=str(output_dir),
                generated_at_utc=_FIXED_TS,
                allow_unsafe_path=True,
                _before_rename_hook=_fail,
            )

        # No final run directory.
        final_run_dir = output_dir / _FIXED_RUN_ID
        assert not final_run_dir.exists()

        # No temp directories left.
        if output_dir.exists():
            remaining = list(output_dir.iterdir())
            assert not remaining, f"unexpected files after failure: {remaining}"


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _source(self) -> str:
        return inspect.getsource(_persistence_mod)

    def test_no_broker_adapter_import(self):
        src = self._source()
        assert "AlpacaBrokerAdapter" not in src
        assert "AlpacaLiveSubmitBroker" not in src

    def test_no_requests_import(self):
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
        src = self._source()
        assert "live_submit" not in src

    def test_no_order_action_call(self):
        src = self._source()
        assert "submit_order" not in src
        assert "place_order" not in src

    def test_no_alpaca_import(self):
        assert "alpaca" not in self._source().lower()
