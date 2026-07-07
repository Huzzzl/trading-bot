"""
tests/test_offline_snapshot_integration.py
---------------------------------------------
S12: Integration-style tests for the offline research snapshot chain.

Tests the end-to-end wiring:
  CandidateFilter
    -> run_controlled_offline_snapshot()
    -> run_offline_research_pipeline()   (injected fake)
    -> persist_pipeline_run_result()     (real, writes to tmp_path)

Uses injected fake pipeline runners and tmp_path for local writes.
No real backtests. No market data required. No broker/API/credential/
env/network/order/live/paper access.
"""

from __future__ import annotations

import inspect
import json
import pathlib
from typing import Any

import pytest

import src.research.offline_snapshot_command as _cmd_mod
import src.research.report_persistence as _persistence_mod
from src.research.offline_snapshot_command import run_controlled_offline_snapshot
from src.research.pipeline_orchestrator import (
    CandidateFilter,
    PipelineRunResult,
    PipelineSummary,
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
_FIXED_RUN_ID_BLOCKED = "20260604T000000Z_BLOCKED"

_SNAP_KWARGS: dict[str, Any] = dict(
    start_date="2020-01-01",
    end_date="2021-12-31",
    train_months=12,
    test_months=3,
    step_months=3,
)


# ---------------------------------------------------------------------------
# Fake pipeline runner builders
# ---------------------------------------------------------------------------

_FAKE_CANDIDATE_ID = "A_SPY_60m_trend_breakout_1to2d"


def _make_fake_report(result: str = "PASS") -> ResearchReport:
    candidate = ResearchReportCandidate(
        candidate_id=_FAKE_CANDIDATE_ID,
        group="A",
        symbol="SPY",
        interval="60m",
        strategy_family="trend_breakout",
        holding_horizon="one_to_two_days",
    )
    summary = ResearchReportSummary(
        result=result,
        blocker=None if result == "PASS" else "test-blocker",
        candidate_id=_FAKE_CANDIDATE_ID,
        splits_requested=1,
        splits_evaluated=1,
        splits_passed=1 if result == "PASS" else 0,
        splits_blocked=0 if result == "PASS" else 1,
        splits_error=0,
        validations_passed=1 if result == "PASS" else 0,
        validations_blocked=0,
        average_monthly_return_mean=0.05 if result == "PASS" else None,
        average_monthly_return_min=0.03 if result == "PASS" else None,
        max_drawdown_worst=-0.10 if result == "PASS" else None,
        total_trades_sum=10 if result == "PASS" else None,
    )
    return ResearchReport(
        schema_version="S6/1.0",
        generated_at_utc=_FIXED_TS,
        candidate=candidate,
        summary=summary,
        splits=(),
        safety=dict(
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        ),
        notes=(),
    )


def _make_fake_snapshot(result: str = "PASS") -> ReportSnapshotRunResult:
    reports = (_make_fake_report(result),) if result != "ERROR" else ()
    return ReportSnapshotRunResult(
        result=result,
        blocker=None if result == "PASS" else "test-blocker",
        candidates_requested=1,
        reports_created=1 if result != "ERROR" else 0,
        reports=reports,
        json_reports=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_pipeline_result(
    result: str = "PASS",
    candidate_filter: CandidateFilter | None = None,
    **flag_overrides: bool,
) -> PipelineRunResult:
    flags: dict[str, bool] = dict(
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
    flags.update(flag_overrides)
    summary = PipelineSummary(
        result=result,
        blocker=None if result == "PASS" else "pipeline-blocker",
        candidates_selected=1,
        reports_created=1,
        reports_passed=1 if result == "PASS" else 0,
        reports_blocked=1 if result == "BLOCKED" else 0,
        reports_error=1 if result == "ERROR" else 0,
        best_candidate_id="A_SPY_60m_trend_breakout_1to2d" if result == "PASS" else None,
        best_average_monthly_return=0.05 if result == "PASS" else None,
        worst_max_drawdown=-0.10 if result == "PASS" else None,
        total_trades_sum=10 if result == "PASS" else None,
    )
    return PipelineRunResult(
        result=result,
        blocker=None if result == "PASS" else "pipeline-blocker",
        filter=candidate_filter or CandidateFilter(),
        summary=summary,
        snapshot=_make_fake_snapshot(result),
        **flags,
    )


def _fake_pipeline(
    result: str = "PASS",
    capture: list | None = None,
    **flag_overrides: bool,
):
    """Return a fake pipeline runner callable."""
    def runner(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        return _make_pipeline_result(
            result,
            candidate_filter=kwargs.get("candidate_filter"),
            **flag_overrides,
        )
    return runner


def _run(
    tmp_path: pathlib.Path,
    pipeline_result: str = "PASS",
    output_subdir: str = "reports",
    **kwargs: Any,
):
    """Helper: run with real persistence into tmp_path."""
    return run_controlled_offline_snapshot(
        output_dir=str(tmp_path / output_subdir),
        generated_at_utc=_FIXED_TS,
        allow_unsafe_path=True,
        _pipeline_runner=kwargs.pop("_pipeline_runner", _fake_pipeline(pipeline_result)),
        **_SNAP_KWARGS,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------

class TestEndToEndPass:
    def test_pass_pipeline_produces_pass_result(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        assert result.result == "PASS"
        assert result.pipeline is not None
        assert result.persistence is not None

    def test_pass_run_writes_manifest(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        assert result.persistence.manifest_path is not None
        assert pathlib.Path(result.persistence.manifest_path).exists()

    def test_pass_run_writes_pipeline_summary(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        assert pathlib.Path(result.persistence.pipeline_summary_path).exists()

    def test_pass_run_writes_report_json_for_each_candidate(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        # manifest + pipeline_summary + 1 report = at least 3 files written.
        assert len(result.persistence.files_written) >= 3
        assert len(result.persistence.report_paths) == 1

    def test_include_markdown_true_writes_summary_md(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, include_markdown=True)
        assert result.result == "PASS"
        assert result.persistence.markdown_summary_path is not None
        assert pathlib.Path(result.persistence.markdown_summary_path).exists()

    def test_include_markdown_false_no_summary_md(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, include_markdown=False)
        assert result.persistence.markdown_summary_path is None

    def test_deterministic_run_id_for_fixed_ts(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        assert result.persistence.run_id == _FIXED_RUN_ID

    def test_manifest_file_inventory_matches_actual_files(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        manifest_path = pathlib.Path(result.persistence.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_dir = manifest_path.parent
        # pipeline_summary must exist.
        assert (run_dir / manifest["files"]["pipeline_summary"]).exists()
        # Each listed report must exist.
        for fname in manifest["files"]["reports"]:
            assert (run_dir / "reports" / fname).exists()


class TestEndToEndBlocked:
    def test_blocked_pipeline_persists_blocked_result(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, pipeline_result="BLOCKED")
        # Pipeline blocked but persistence should still write.
        assert result.persistence is not None
        assert result.persistence.result == "PASS"

    def test_blocked_pipeline_command_result_is_pass(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, pipeline_result="BLOCKED")
        assert result.result == "PASS"
        assert result.pipeline.result == "BLOCKED"

    def test_blocked_run_id_contains_blocked(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, pipeline_result="BLOCKED")
        assert result.persistence.run_id is not None
        assert "BLOCKED" in result.persistence.run_id


class TestEndToEndNoPersist:
    def test_output_dir_none_writes_nothing(self, tmp_path: pathlib.Path):
        result = run_controlled_offline_snapshot(
            output_dir=None,
            _pipeline_runner=_fake_pipeline(),
            **_SNAP_KWARGS,
        )
        assert result.result == "BLOCKED"
        assert result.pipeline is None
        assert result.persistence is None
        # tmp_path should have no report directories.
        assert list(tmp_path.iterdir()) == []

    def test_output_dir_empty_writes_nothing(self, tmp_path: pathlib.Path):
        result = run_controlled_offline_snapshot(
            output_dir="",
            _pipeline_runner=_fake_pipeline(),
            **_SNAP_KWARGS,
        )
        assert result.result == "BLOCKED"
        assert result.pipeline is None
        assert result.persistence is None

    def test_error_pipeline_does_not_persist(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, pipeline_result="ERROR")
        assert result.result == "ERROR"
        assert result.persistence is None
        # No run directory should be created.
        output_dir = tmp_path / "reports"
        if output_dir.exists():
            run_dir = output_dir / _FIXED_RUN_ID
            assert not run_dir.exists()

    def test_no_default_output_path(self):
        """No default output_dir: must be explicitly supplied."""
        result = run_controlled_offline_snapshot(
            output_dir=None,
            _pipeline_runner=_fake_pipeline(),
            **_SNAP_KWARGS,
        )
        assert result.result == "BLOCKED"
        assert "output_dir" in result.blocker.lower()


class TestOverwrite:
    def test_allow_overwrite_false_blocks_second_run(self, tmp_path: pathlib.Path):
        r1 = _run(tmp_path, allow_overwrite=False)
        assert r1.result == "PASS"
        r2 = _run(tmp_path, allow_overwrite=False)
        assert r2.result == "BLOCKED"

    def test_allow_overwrite_true_replaces(self, tmp_path: pathlib.Path):
        r1 = _run(tmp_path, allow_overwrite=False)
        assert r1.result == "PASS"
        r2 = _run(tmp_path, allow_overwrite=True)
        assert r2.result == "PASS"
        assert r1.persistence.run_id == r2.persistence.run_id


class TestCandidateFilterPropagation:
    def test_filter_appears_in_pipeline_summary_json(self, tmp_path: pathlib.Path):
        f = CandidateFilter(groups=("A",), symbols=("SPY",))
        result = _run(
            tmp_path,
            candidate_filter=f,
            _pipeline_runner=_fake_pipeline("PASS"),
        )
        assert result.result == "PASS"
        ps_path = pathlib.Path(result.persistence.pipeline_summary_path)
        ps = json.loads(ps_path.read_text(encoding="utf-8"))
        assert ps["candidate_filter"]["groups"] == ["A"]
        assert ps["candidate_filter"]["symbols"] == ["SPY"]

    def test_filter_passed_to_pipeline_runner(self, tmp_path: pathlib.Path):
        calls: list = []
        f = CandidateFilter(groups=("B",))
        _run(tmp_path, candidate_filter=f, _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls[0]["candidate_filter"] == f


class TestSafetyFlagIntegration:
    def test_true_safety_flag_in_pipeline_prevents_pass(self, tmp_path: pathlib.Path):
        result = _run(
            tmp_path,
            _pipeline_runner=_fake_pipeline("PASS", broker_calls_made=True),
        )
        assert result.result != "PASS"
        assert result.broker_calls_made is True

    def test_all_flags_false_in_clean_run(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        assert not result.broker_calls_made
        assert not result.credentials_read
        assert not result.network_calls_made
        assert not result.order_action_requested
        assert not result.live_trading_allowed


class TestReportJsonOutput:
    """Verify that real ResearchReport objects are written as JSON through persist_pipeline_run_result."""

    def test_pass_run_has_one_report_path(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        assert len(result.persistence.report_paths) == 1

    def test_pass_run_report_file_exists(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        report_path = pathlib.Path(result.persistence.report_paths[0])
        assert report_path.exists()

    def test_pass_run_report_filename_matches_candidate_id(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        report_path = pathlib.Path(result.persistence.report_paths[0])
        assert report_path.name == f"{_FAKE_CANDIDATE_ID}.json"

    def test_pass_run_report_json_schema_version(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        data = json.loads(pathlib.Path(result.persistence.report_paths[0]).read_text(encoding="utf-8"))
        assert data["schema_version"] == "S6/1.0"

    def test_pass_run_report_json_candidate_id(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        data = json.loads(pathlib.Path(result.persistence.report_paths[0]).read_text(encoding="utf-8"))
        assert data["candidate"]["candidate_id"] == _FAKE_CANDIDATE_ID

    def test_manifest_reports_list_contains_report_filename(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        manifest = json.loads(pathlib.Path(result.persistence.manifest_path).read_text(encoding="utf-8"))
        assert f"{_FAKE_CANDIDATE_ID}.json" in manifest["files"]["reports"]

    def test_manifest_reports_file_exists_on_disk(self, tmp_path: pathlib.Path):
        result = _run(tmp_path)
        manifest_path = pathlib.Path(result.persistence.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_dir = manifest_path.parent
        for fname in manifest["files"]["reports"]:
            assert (run_dir / "reports" / fname).exists()

    def test_blocked_pipeline_also_writes_report_json(self, tmp_path: pathlib.Path):
        result = _run(tmp_path, pipeline_result="BLOCKED")
        assert result.result == "PASS"
        assert len(result.persistence.report_paths) == 1
        assert pathlib.Path(result.persistence.report_paths[0]).exists()


class TestNoForbiddenImports:
    def _source(self) -> str:
        return inspect.getsource(_cmd_mod) + inspect.getsource(_persistence_mod)

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
