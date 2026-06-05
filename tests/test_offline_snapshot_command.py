"""
tests/test_offline_snapshot_command.py
------------------------------------------
Tests for S11: controlled offline research snapshot command.

All tests use injected _pipeline_runner and _persistence_runner fakes;
no real backtests, no market data, no broker/API/credential/env/network access.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

import src.research.offline_snapshot_command as _cmd_mod
from src.research.offline_snapshot_command import (
    OfflineSnapshotCommandResult,
    run_controlled_offline_snapshot,
)
from src.research.pipeline_orchestrator import (
    CandidateFilter,
    PipelineRunResult,
    PipelineSummary,
)
from src.research.report_persistence import ReportPersistenceResult
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
_FIXED_SHA = "a" * 40
_SNAP_KWARGS: dict[str, Any] = dict(
    start_date="2020-01-01",
    end_date="2021-12-31",
    train_months=12,
    test_months=3,
    step_months=3,
)


# ---------------------------------------------------------------------------
# Fake fixture builders
# ---------------------------------------------------------------------------

def _make_fake_snapshot(result: str = "PASS") -> ReportSnapshotRunResult:
    return ReportSnapshotRunResult(
        result=result,
        blocker=None if result == "PASS" else "test-blocker",
        candidates_requested=1,
        reports_created=1,
        reports=(),
        json_reports=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_pipeline_result(
    result: str = "PASS",
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
        blocker=None if result == "PASS" else "pipeline-test-blocker",
        candidates_selected=1,
        reports_created=1,
        reports_passed=1 if result == "PASS" else 0,
        reports_blocked=1 if result == "BLOCKED" else 0,
        reports_error=1 if result == "ERROR" else 0,
        best_candidate_id="test_id" if result == "PASS" else None,
        best_average_monthly_return=0.05 if result == "PASS" else None,
        worst_max_drawdown=-0.10 if result == "PASS" else None,
        total_trades_sum=10 if result == "PASS" else None,
    )
    return PipelineRunResult(
        result=result,
        blocker=None if result == "PASS" else "pipeline-test-blocker",
        filter=CandidateFilter(),
        summary=summary,
        snapshot=_make_fake_snapshot(result),
        **flags,
    )


def _make_persistence_result(
    result: str = "PASS",
    **flag_overrides: bool,
) -> ReportPersistenceResult:
    flags: dict[str, bool] = dict(
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
    flags.update(flag_overrides)
    return ReportPersistenceResult(
        result=result,
        blocker=None if result == "PASS" else "persistence-test-blocker",
        output_dir="/tmp/test" if result == "PASS" else None,
        run_id="20260604T000000Z_PASS" if result == "PASS" else None,
        manifest_path="/tmp/test/run/manifest.json" if result == "PASS" else None,
        pipeline_summary_path="/tmp/test/run/pipeline_summary.json" if result == "PASS" else None,
        report_paths=(),
        markdown_summary_path=None,
        files_written=("f1.json",) if result == "PASS" else (),
        **flags,
    )


def _fake_pipeline(result: str = "PASS", capture: list | None = None, **flag_overrides: bool):
    """Return a fake pipeline runner that captures kwargs."""
    def runner(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        return _make_pipeline_result(result, **flag_overrides)
    return runner


def _fake_persistence(result: str = "PASS", capture: list | None = None, **flag_overrides: bool):
    """Return a fake persistence runner that captures calls."""
    def runner(pipeline_result, **kwargs):
        if capture is not None:
            capture.append({"pipeline_result": pipeline_result, **kwargs})
        return _make_persistence_result(result, **flag_overrides)
    return runner


def _run(output_dir: str | None = "/tmp/fake_output", **kwargs: Any) -> OfflineSnapshotCommandResult:
    """Helper: run with sensible defaults and always-injected fakes."""
    return run_controlled_offline_snapshot(
        output_dir=output_dir,
        _pipeline_runner=kwargs.pop("_pipeline_runner", _fake_pipeline()),
        _persistence_runner=kwargs.pop("_persistence_runner", _fake_persistence()),
        **_SNAP_KWARGS,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TestOfflineSnapshotCommandResultDataclass
# ---------------------------------------------------------------------------

class TestOfflineSnapshotCommandResultDataclass:
    def _make(self, **overrides: Any) -> OfflineSnapshotCommandResult:
        defaults = dict(
            result="PASS",
            blocker=None,
            pipeline=_make_pipeline_result(),
            persistence=_make_persistence_result(),
            output_dir="/tmp/test",
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )
        defaults.update(overrides)
        return OfflineSnapshotCommandResult(**defaults)

    def test_can_create(self):
        r = self._make()
        assert r.result == "PASS"
        assert r.output_dir == "/tmp/test"

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
        r = self._make(pipeline=None, persistence=None, output_dir=None)
        assert r.pipeline is None
        assert r.persistence is None
        assert r.output_dir is None


# ---------------------------------------------------------------------------
# TestOutputDirValidation
# ---------------------------------------------------------------------------

class TestOutputDirValidation:
    def test_none_output_dir_returns_blocked(self):
        result = _run(output_dir=None)
        assert result.result == "BLOCKED"
        assert "output_dir" in result.blocker.lower()

    def test_empty_string_output_dir_returns_blocked(self):
        result = _run(output_dir="")
        assert result.result == "BLOCKED"
        assert "output_dir" in result.blocker.lower()
        assert result.pipeline is None
        assert result.persistence is None
        assert result.output_dir is None
        assert not result.broker_calls_made
        assert not result.credentials_read
        assert not result.network_calls_made
        assert not result.order_action_requested
        assert not result.live_trading_allowed

    def test_none_does_not_call_pipeline(self):
        calls: list = []
        _run(output_dir=None, _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls == []

    def test_none_does_not_call_persistence(self):
        calls: list = []
        _run(output_dir=None, _persistence_runner=_fake_persistence(capture=calls))
        assert calls == []

    def test_empty_does_not_call_pipeline(self):
        calls: list = []
        _run(output_dir="", _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls == []


# ---------------------------------------------------------------------------
# TestPipelineRunnerCallthrough
# ---------------------------------------------------------------------------

class TestPipelineRunnerCallthrough:
    def test_candidate_filter_passed(self):
        calls: list = []
        f = CandidateFilter(groups=("A",))
        _run(candidate_filter=f, _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls[0]["candidate_filter"] == f

    def test_start_end_dates_passed(self):
        calls: list = []
        run_controlled_offline_snapshot(
            output_dir="/tmp/out",
            start_date="2019-01-01",
            end_date="2022-12-31",
            train_months=6,
            test_months=2,
            step_months=2,
            _pipeline_runner=_fake_pipeline(capture=calls),
            _persistence_runner=_fake_persistence(),
        )
        assert calls[0]["start_date"] == "2019-01-01"
        assert calls[0]["end_date"] == "2022-12-31"

    def test_train_test_step_months_passed(self):
        calls: list = []
        run_controlled_offline_snapshot(
            output_dir="/tmp/out",
            start_date="2020-01-01",
            end_date="2021-12-31",
            train_months=6,
            test_months=2,
            step_months=2,
            _pipeline_runner=_fake_pipeline(capture=calls),
            _persistence_runner=_fake_persistence(),
        )
        assert calls[0]["train_months"] == 6
        assert calls[0]["test_months"] == 2
        assert calls[0]["step_months"] == 2

    def test_cache_dir_passed(self):
        calls: list = []
        _run(cache_dir="/tmp/cache", _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls[0]["cache_dir"] == "/tmp/cache"

    def test_include_json_passed(self):
        calls: list = []
        _run(include_json=True, _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls[0]["include_json"] is True

    def test_generated_at_utc_passed(self):
        calls: list = []
        _run(generated_at_utc=_FIXED_TS, _pipeline_runner=_fake_pipeline(capture=calls))
        assert calls[0]["generated_at_utc"] == _FIXED_TS

    def test_default_candidate_filter_passed_as_none(self):
        calls: list = []
        _run(_pipeline_runner=_fake_pipeline(capture=calls))
        assert calls[0]["candidate_filter"] is None


# ---------------------------------------------------------------------------
# TestPersistenceCallthrough
# ---------------------------------------------------------------------------

class TestPersistenceCallthrough:
    def test_output_dir_passed(self):
        calls: list = []
        _run(output_dir="/tmp/myreports", _persistence_runner=_fake_persistence(capture=calls))
        assert calls[0]["output_dir"] == "/tmp/myreports"

    def test_generated_at_utc_passed(self):
        calls: list = []
        _run(generated_at_utc=_FIXED_TS, _persistence_runner=_fake_persistence(capture=calls))
        assert calls[0]["generated_at_utc"] == _FIXED_TS

    def test_git_commit_sha_passed(self):
        calls: list = []
        _run(git_commit_sha=_FIXED_SHA, _persistence_runner=_fake_persistence(capture=calls))
        assert calls[0]["git_commit_sha"] == _FIXED_SHA

    def test_include_markdown_passed(self):
        calls: list = []
        _run(include_markdown=True, _persistence_runner=_fake_persistence(capture=calls))
        assert calls[0]["include_markdown"] is True

    def test_allow_overwrite_passed(self):
        calls: list = []
        _run(allow_overwrite=True, _persistence_runner=_fake_persistence(capture=calls))
        assert calls[0]["allow_overwrite"] is True

    def test_allow_unsafe_path_passed(self):
        calls: list = []
        _run(allow_unsafe_path=True, _persistence_runner=_fake_persistence(capture=calls))
        assert calls[0]["allow_unsafe_path"] is True


# ---------------------------------------------------------------------------
# TestAggregateResult
# ---------------------------------------------------------------------------

class TestAggregateResult:
    def test_pass_pipeline_pass_persistence_is_pass(self):
        result = _run(
            _pipeline_runner=_fake_pipeline("PASS"),
            _persistence_runner=_fake_persistence("PASS"),
        )
        assert result.result == "PASS"
        assert result.blocker is None
        assert result.pipeline is not None
        assert result.persistence is not None

    def test_blocked_pipeline_pass_persistence_is_pass(self):
        result = _run(
            _pipeline_runner=_fake_pipeline("BLOCKED"),
            _persistence_runner=_fake_persistence("PASS"),
        )
        assert result.result == "PASS"
        assert result.pipeline.result == "BLOCKED"

    def test_pass_pipeline_blocked_persistence_is_blocked(self):
        result = _run(
            _pipeline_runner=_fake_pipeline("PASS"),
            _persistence_runner=_fake_persistence("BLOCKED"),
        )
        assert result.result == "BLOCKED"
        assert "persistence" in result.blocker.lower() or result.blocker is not None

    def test_error_pipeline_no_persistence_called(self):
        calls: list = []
        result = _run(
            _pipeline_runner=_fake_pipeline("ERROR"),
            _persistence_runner=_fake_persistence(capture=calls),
        )
        assert result.result == "ERROR"
        assert calls == []
        assert result.persistence is None

    def test_persistence_exception_is_error(self):
        def raising_persistence(pipeline_result, **kwargs):
            raise RuntimeError("disk full")

        result = _run(
            _pipeline_runner=_fake_pipeline("PASS"),
            _persistence_runner=raising_persistence,
        )
        assert result.result == "ERROR"
        assert "disk full" in result.blocker
        assert result.persistence is None

    def test_persistence_exception_safety_flags_all_false(self):
        def raising_persistence(pipeline_result, **kwargs):
            raise RuntimeError("oops")

        result = _run(
            _pipeline_runner=_fake_pipeline("PASS"),
            _persistence_runner=raising_persistence,
        )
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.network_calls_made is False
        assert result.order_action_requested is False
        assert result.live_trading_allowed is False

    def test_no_default_output_path(self):
        """output_dir must be explicitly supplied; None blocks immediately."""
        result = run_controlled_offline_snapshot(
            output_dir=None,
            **_SNAP_KWARGS,
            _pipeline_runner=_fake_pipeline(),
            _persistence_runner=_fake_persistence(),
        )
        assert result.result == "BLOCKED"


# ---------------------------------------------------------------------------
# TestSafetyFlagAggregation
# ---------------------------------------------------------------------------

class TestSafetyFlagAggregation:
    def test_all_false_normally(self):
        result = _run()
        assert not result.broker_calls_made
        assert not result.credentials_read
        assert not result.network_calls_made
        assert not result.order_action_requested
        assert not result.live_trading_allowed

    def test_pipeline_flag_ored_into_result(self):
        result = _run(
            _pipeline_runner=_fake_pipeline("PASS", broker_calls_made=True),
            _persistence_runner=_fake_persistence("PASS"),
        )
        assert result.broker_calls_made is True

    def test_persistence_flag_ored_into_result(self):
        result = _run(
            _pipeline_runner=_fake_pipeline("PASS"),
            _persistence_runner=_fake_persistence("PASS", credentials_read=True),
        )
        assert result.credentials_read is True

    def test_any_true_safety_flag_prevents_pass(self):
        result = _run(
            _pipeline_runner=_fake_pipeline("PASS", network_calls_made=True),
            _persistence_runner=_fake_persistence("PASS"),
        )
        assert result.result != "PASS"


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _source(self) -> str:
        return inspect.getsource(_cmd_mod)

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

    def test_no_order_action_call(self):
        src = self._source()
        assert "submit_order" not in src
        assert "place_order" not in src

    def test_no_alpaca(self):
        assert "alpaca" not in self._source().lower()
