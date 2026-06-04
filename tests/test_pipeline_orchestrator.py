"""
tests/test_pipeline_orchestrator.py
--------------------------------------
Tests for S8: offline research pipeline orchestrator.

All tests use injected _snapshot_runner fakes; no real backtests, no files
written, no broker/API/credential/env/network access.
"""

from __future__ import annotations

import inspect
import pathlib
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from src.research.candidate_universe import (
    CandidateSpec,
    HoldingHorizon,
    StrategyFamily,
    build_initial_candidate_universe,
)
from src.research.pipeline_orchestrator import (
    CandidateFilter,
    PipelineRunResult,
    PipelineSummary,
    _apply_filter,
    _compute_pipeline_summary,
    run_offline_research_pipeline,
)
from src.research.report_schema import (
    ResearchReport,
    ResearchReportCandidate,
    ResearchReportSplit,
    ResearchReportSummary,
)
from src.research.report_snapshot_runner import ReportSnapshotRunResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXED_TS = "2026-06-04T00:00:00+00:00"
_SNAP_KWARGS: dict[str, Any] = dict(
    start_date="2020-01-01",
    end_date="2021-12-31",
    train_months=12,
    test_months=3,
    step_months=3,
)

_ALL_CANDIDATES = build_initial_candidate_universe()  # 32 total


# ---------------------------------------------------------------------------
# Fake builder helpers
# ---------------------------------------------------------------------------

def _make_report_summary(
    candidate_id: str,
    result: str,
    avg_return: float | None = None,
    drawdown: float | None = None,
    trades: int | None = None,
) -> ResearchReportSummary:
    return ResearchReportSummary(
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
    )


def _make_report(
    candidate_id: str,
    result: str,
    avg_return: float | None = None,
    drawdown: float | None = None,
    trades: int | None = None,
    broker_calls_made: bool = False,
) -> ResearchReport:
    candidate = ResearchReportCandidate(
        candidate_id=candidate_id,
        group="A",
        symbol="SPY",
        interval="60m",
        strategy_family="trend_breakout",
        holding_horizon="one_to_two_days",
    )
    return ResearchReport(
        schema_version="S6/1.0",
        generated_at_utc=_FIXED_TS,
        candidate=candidate,
        summary=_make_report_summary(candidate_id, result, avg_return, drawdown, trades),
        splits=(),
        safety={
            "broker_calls_made": broker_calls_made,
            "credentials_read": False,
            "network_calls_made": False,
            "order_action_requested": False,
            "live_trading_allowed": False,
        },
        notes=(),
    )


def _make_snapshot(
    reports: list[ResearchReport],
    broker_calls_made: bool = False,
) -> ReportSnapshotRunResult:
    n_pass = sum(1 for r in reports if r.summary.result == "PASS")
    n_error = sum(1 for r in reports if r.summary.result == "ERROR")
    if n_error > 0:
        result = "ERROR"
        blocker: str | None = f"{n_error} report(s) returned ERROR"
    elif n_pass > 0:
        result = "PASS"
        blocker = None
    else:
        result = "BLOCKED"
        blocker = "no candidates passed evaluation"
    return ReportSnapshotRunResult(
        result=result,
        blocker=blocker,
        candidates_requested=len(reports),
        reports_created=len(reports),
        reports=tuple(reports),
        json_reports=(),
        broker_calls_made=broker_calls_made,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _simple_runner(result: str = "PASS") -> Any:
    """Return an injected runner that always returns a single-report snapshot."""
    def runner(candidates, **kwargs):
        return _make_snapshot([_make_report("fake_id", result, 0.05, -0.10, 10)])
    return runner


# ---------------------------------------------------------------------------
# TestCandidateFilterDataclass
# ---------------------------------------------------------------------------

class TestCandidateFilterDataclass:
    def test_default_all_empty_tuples(self):
        f = CandidateFilter()
        assert f.groups == ()
        assert f.symbols == ()
        assert f.intervals == ()
        assert f.strategy_families == ()
        assert f.holding_horizons == ()

    def test_max_candidates_default_none(self):
        assert CandidateFilter().max_candidates is None

    def test_frozen(self):
        f = CandidateFilter()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            f.groups = ("A",)  # type: ignore[misc]

    def test_can_set_fields(self):
        f = CandidateFilter(
            groups=("A", "B"),
            symbols=("SPY",),
            intervals=("60m",),
            strategy_families=("trend_breakout",),
            holding_horizons=("one_to_two_days",),
            max_candidates=5,
        )
        assert f.groups == ("A", "B")
        assert f.symbols == ("SPY",)
        assert f.max_candidates == 5


# ---------------------------------------------------------------------------
# TestPipelineSummaryDataclass
# ---------------------------------------------------------------------------

class TestPipelineSummaryDataclass:
    def _make(self, **kwargs) -> PipelineSummary:
        defaults = dict(
            result="PASS", blocker=None, candidates_selected=1,
            reports_created=1, reports_passed=1, reports_blocked=0, reports_error=0,
            best_candidate_id="id", best_average_monthly_return=0.05,
            worst_max_drawdown=-0.10, total_trades_sum=10,
        )
        defaults.update(kwargs)
        return PipelineSummary(**defaults)

    def test_can_create(self):
        s = self._make()
        assert s.result == "PASS"
        assert s.reports_passed == 1

    def test_frozen(self):
        s = self._make()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            s.result = "ERROR"  # type: ignore[misc]

    def test_optional_none_fields(self):
        s = self._make(
            best_candidate_id=None,
            best_average_monthly_return=None,
            worst_max_drawdown=None,
            total_trades_sum=None,
        )
        assert s.best_candidate_id is None
        assert s.total_trades_sum is None


# ---------------------------------------------------------------------------
# TestPipelineRunResultDataclass
# ---------------------------------------------------------------------------

class TestPipelineRunResultDataclass:
    def _make(self) -> PipelineRunResult:
        snap = _make_snapshot([_make_report("id1", "PASS", 0.05, -0.10, 10)])
        summary = _compute_pipeline_summary(snap, 1)
        return PipelineRunResult(
            result="PASS",
            blocker=None,
            filter=CandidateFilter(),
            summary=summary,
            snapshot=snap,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )

    def test_can_create(self):
        r = self._make()
        assert r.result == "PASS"

    def test_frozen(self):
        r = self._make()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            r.result = "ERROR"  # type: ignore[misc]

    def test_all_safety_flags_present(self):
        r = self._make()
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")


# ---------------------------------------------------------------------------
# TestCandidateFiltering
# ---------------------------------------------------------------------------

class TestCandidateFiltering:
    def _run(self, f: CandidateFilter) -> PipelineRunResult:
        return run_offline_research_pipeline(
            candidate_filter=f,
            _snapshot_runner=_simple_runner(),
            **_SNAP_KWARGS,
        )

    def _selected_count(self, f: CandidateFilter) -> int:
        return len(_apply_filter(_ALL_CANDIDATES, f))

    def test_default_filter_selects_all_32(self):
        assert self._selected_count(CandidateFilter()) == 32

    def test_default_filter_passed_to_runner(self):
        captured: list[tuple] = []
        def runner(candidates, **kwargs):
            captured.append(candidates)
            return _make_snapshot([_make_report("x", "PASS")])
        run_offline_research_pipeline(_snapshot_runner=runner, **_SNAP_KWARGS)
        assert len(captured[0]) == 32

    def test_filter_by_group(self):
        assert self._selected_count(CandidateFilter(groups=("A",))) == 6
        assert self._selected_count(CandidateFilter(groups=("B",))) == 4
        assert self._selected_count(CandidateFilter(groups=("A", "B"))) == 10

    def test_filter_by_symbol(self):
        assert self._selected_count(CandidateFilter(symbols=("SPY",))) == 7

    def test_filter_by_interval(self):
        assert self._selected_count(CandidateFilter(intervals=("1d",))) == 7

    def test_filter_by_strategy_family(self):
        count = self._selected_count(
            CandidateFilter(strategy_families=("trend_breakout",))
        )
        assert count == 12

    def test_filter_by_holding_horizon(self):
        count = self._selected_count(
            CandidateFilter(holding_horizons=("one_to_two_days",))
        )
        assert count == 14

    def test_combined_filters_reduce_deterministically(self):
        # Group A + symbol SPY → 2 candidates (SPY/60m and SPY/1d in group A)
        count = self._selected_count(CandidateFilter(groups=("A",), symbols=("SPY",)))
        assert count == 2

    def test_max_candidates_applied_after_filtering(self):
        f = CandidateFilter(groups=("A",), max_candidates=2)
        selected = _apply_filter(_ALL_CANDIDATES, f)
        assert len(selected) == 2
        # All 2 must still be from group A
        assert all(c.group == "A" for c in selected)

    def test_no_candidates_returns_blocked(self):
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(symbols=("NONEXISTENT",)),
            _snapshot_runner=_simple_runner(),
            **_SNAP_KWARGS,
        )
        assert result.result == "BLOCKED"
        assert result.blocker == "no candidates selected"
        assert result.summary.candidates_selected == 0
        assert result.snapshot.candidates_requested == 0

    def test_deterministic_repeated_run(self):
        f = CandidateFilter(groups=("A", "B"))
        s1 = _apply_filter(_ALL_CANDIDATES, f)
        s2 = _apply_filter(_ALL_CANDIDATES, f)
        assert s1 == s2
        assert [c.candidate_id for c in s1] == [c.candidate_id for c in s2]


# ---------------------------------------------------------------------------
# TestPipelineIntegration
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_injected_runner_called_with_selected_candidates(self):
        captured: list[Any] = []

        def runner(candidates, **kwargs):
            captured.append(list(candidates))
            return _make_snapshot([_make_report("x", "PASS")])

        run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            **_SNAP_KWARGS,
        )
        assert len(captured) == 1
        assert len(captured[0]) == 6
        assert all(c.group == "A" for c in captured[0])

    def test_start_end_dates_propagated(self):
        captured: list[dict] = []

        def runner(candidates, **kwargs):
            captured.append(kwargs)
            return _make_snapshot([_make_report("x", "PASS")])

        run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            start_date="2019-01-01",
            end_date="2022-12-31",
            train_months=12,
            test_months=3,
            step_months=3,
        )
        assert captured[0]["start_date"] == "2019-01-01"
        assert captured[0]["end_date"] == "2022-12-31"

    def test_train_test_step_months_propagated(self):
        captured: list[dict] = []

        def runner(candidates, **kwargs):
            captured.append(kwargs)
            return _make_snapshot([_make_report("x", "PASS")])

        run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            start_date="2020-01-01",
            end_date="2021-12-31",
            train_months=6,
            test_months=2,
            step_months=2,
        )
        assert captured[0]["train_months"] == 6
        assert captured[0]["test_months"] == 2
        assert captured[0]["step_months"] == 2

    def test_cache_dir_propagated(self):
        captured: list[dict] = []

        def runner(candidates, **kwargs):
            captured.append(kwargs)
            return _make_snapshot([_make_report("x", "PASS")])

        run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            cache_dir=pathlib.Path("/tmp/fake-cache"),
            **_SNAP_KWARGS,
        )
        assert captured[0]["cache_dir"] == pathlib.Path("/tmp/fake-cache")

    def test_include_json_propagated(self):
        captured: list[dict] = []

        def runner(candidates, **kwargs):
            captured.append(kwargs)
            return _make_snapshot([_make_report("x", "PASS")])

        run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            include_json=True,
            **_SNAP_KWARGS,
        )
        assert captured[0]["include_json"] is True

    def test_generated_at_utc_propagated(self):
        captured: list[dict] = []

        def runner(candidates, **kwargs):
            captured.append(kwargs)
            return _make_snapshot([_make_report("x", "PASS")])

        run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            generated_at_utc=_FIXED_TS,
            **_SNAP_KWARGS,
        )
        assert captured[0]["generated_at_utc"] == _FIXED_TS

    def test_result_mirrors_snapshot_pass(self):
        snap = _make_snapshot([_make_report("x", "PASS", 0.05, -0.10, 10)])
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=lambda c, **kw: snap,
            **_SNAP_KWARGS,
        )
        assert result.result == "PASS"
        assert result.blocker is None

    def test_result_mirrors_snapshot_error(self):
        snap = _make_snapshot([_make_report("x", "ERROR")])
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=lambda c, **kw: snap,
            **_SNAP_KWARGS,
        )
        assert result.result == "ERROR"

    def test_safety_flags_all_false_by_default(self):
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=_simple_runner(),
            **_SNAP_KWARGS,
        )
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.network_calls_made is False
        assert result.order_action_requested is False
        assert result.live_trading_allowed is False

    def test_deterministic_repeated_run(self):
        def runner(candidates, **kwargs):
            return _make_snapshot([_make_report("id1", "PASS", 0.05, -0.10, 10)])

        r1 = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            generated_at_utc=_FIXED_TS,
            **_SNAP_KWARGS,
        )
        r2 = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=runner,
            generated_at_utc=_FIXED_TS,
            **_SNAP_KWARGS,
        )
        assert r1.result == r2.result
        assert r1.summary.best_candidate_id == r2.summary.best_candidate_id
        assert r1.summary.candidates_selected == r2.summary.candidates_selected


# ---------------------------------------------------------------------------
# TestPipelineSummaryComputation
# ---------------------------------------------------------------------------

class TestPipelineSummaryComputation:
    def test_summary_counts_pass_block_error(self):
        reports = [
            _make_report("a", "PASS", 0.05, -0.10, 10),
            _make_report("b", "BLOCKED"),
            _make_report("c", "ERROR"),
        ]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 3)
        assert summary.reports_passed == 1
        assert summary.reports_blocked == 1
        assert summary.reports_error == 1
        assert summary.reports_created == 3

    def test_best_candidate_highest_return(self):
        reports = [
            _make_report("a", "PASS", 0.03, -0.10, 5),
            _make_report("b", "PASS", 0.08, -0.05, 10),
            _make_report("c", "PASS", 0.01, -0.20, 2),
        ]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 3)
        assert summary.best_candidate_id == "b"
        assert summary.best_average_monthly_return == pytest.approx(0.08)

    def test_best_candidate_tiebreak_lexicographic(self):
        reports = [
            _make_report("zzz_candidate", "PASS", 0.05, -0.10, 5),
            _make_report("aaa_candidate", "PASS", 0.05, -0.05, 10),
        ]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 2)
        assert summary.best_candidate_id == "aaa_candidate"

    def test_best_candidate_none_when_no_pass(self):
        reports = [_make_report("a", "BLOCKED"), _make_report("b", "BLOCKED")]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 2)
        assert summary.best_candidate_id is None
        assert summary.best_average_monthly_return is None

    def test_worst_drawdown_most_negative(self):
        reports = [
            _make_report("a", "PASS", drawdown=-0.05),
            _make_report("b", "PASS", drawdown=-0.25),
            _make_report("c", "BLOCKED", drawdown=-0.10),
        ]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 3)
        assert summary.worst_max_drawdown == pytest.approx(-0.25)

    def test_worst_drawdown_none_when_no_numeric(self):
        reports = [_make_report("a", "BLOCKED")]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 1)
        assert summary.worst_max_drawdown is None

    def test_total_trades_sums_numeric(self):
        reports = [
            _make_report("a", "PASS", trades=10),
            _make_report("b", "PASS", trades=20),
            _make_report("c", "BLOCKED", trades=None),
        ]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 3)
        assert summary.total_trades_sum == 30

    def test_total_trades_none_when_no_numeric(self):
        reports = [_make_report("a", "BLOCKED", trades=None)]
        snap = _make_snapshot(reports)
        summary = _compute_pipeline_summary(snap, 1)
        assert summary.total_trades_sum is None


# ---------------------------------------------------------------------------
# TestSafetyFlagAggregation
# ---------------------------------------------------------------------------

class TestSafetyFlagAggregation:
    def test_all_false_when_snapshot_all_false(self):
        snap = _make_snapshot([_make_report("x", "PASS")], broker_calls_made=False)
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=lambda c, **kw: snap,
            **_SNAP_KWARGS,
        )
        assert not result.broker_calls_made
        assert not result.credentials_read
        assert not result.network_calls_made
        assert not result.order_action_requested
        assert not result.live_trading_allowed

    def test_broker_calls_true_mirrored(self):
        snap = _make_snapshot([_make_report("x", "PASS", broker_calls_made=True)], broker_calls_made=True)
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(groups=("A",)),
            _snapshot_runner=lambda c, **kw: snap,
            **_SNAP_KWARGS,
        )
        assert result.broker_calls_made is True

    def test_no_candidates_safety_all_false(self):
        result = run_offline_research_pipeline(
            candidate_filter=CandidateFilter(symbols=("NONEXISTENT",)),
            _snapshot_runner=_simple_runner(),
            **_SNAP_KWARGS,
        )
        assert not result.broker_calls_made
        assert not result.credentials_read
        assert not result.network_calls_made
        assert not result.order_action_requested
        assert not result.live_trading_allowed


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _source(self) -> str:
        import src.research.pipeline_orchestrator as mod
        return inspect.getsource(mod)

    def test_no_broker_adapter_import(self):
        assert "AlpacaBrokerAdapter" not in self._source()
        assert "AlpacaLiveSubmitBroker" not in self._source()

    def test_no_requests_import(self):
        src = self._source()
        assert "import requests" not in src
        assert "import aiohttp" not in src

    def test_no_os_environ(self):
        assert "os.environ" not in self._source()

    def test_no_file_write(self):
        src = self._source()
        assert "open(" not in src
        assert ".write(" not in src
        assert "json.dump(" not in src

    def test_no_optimize(self):
        src = self._source()
        assert "scipy.optimize" not in src
        assert "optuna" not in src
        assert "skopt" not in src
        assert "hyperopt" not in src

    def test_no_order_action(self):
        src = self._source()
        assert "submit_order" not in src
        assert "place_order" not in src
        assert "order_action" not in src.replace("order_action_requested", "")

    def test_no_live_gate(self):
        src = self._source()
        assert "live_submit" not in src
        assert "live_readiness_gate" not in src

    def test_no_alpaca_import(self):
        src = self._source()
        assert "alpaca" not in src.lower()

    def test_no_network_libraries(self):
        src = self._source()
        assert "urllib" not in src
        assert "httpx" not in src
        assert "socket" not in src
