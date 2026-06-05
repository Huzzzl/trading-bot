"""
research/pipeline_orchestrator.py
-----------------------------------
S8: Offline research pipeline orchestrator.

Provides a higher-level entry point that selects candidates from the initial
universe using a deterministic CandidateFilter, delegates to
run_offline_report_snapshot(), and returns a compact in-memory
PipelineRunResult containing the aggregate PipelineSummary and the full
ReportSnapshotRunResult.

No files are written. No result persistence. No parameter optimisation.
No broker/API/credential/env/network access. No order submission.
No live/paper trading approval.

Aggregate result rule (mirrors snapshot runner):
  ERROR   — if snapshot result is "ERROR".
  PASS    — if snapshot result is "PASS".
  BLOCKED — if no candidates are selected, or snapshot result is "BLOCKED".

Safety flags (broker_calls_made, credentials_read, network_calls_made,
order_action_requested, live_trading_allowed) mirror the snapshot result
unchanged. Expected all False for fully offline evaluation.

Candidate filtering:
  CandidateFilter fields are OR-within-dimension, AND-across-dimensions.
  Empty tuples mean "no filter on this dimension" (all values pass).
  String values for strategy_families and holding_horizons must match the
  corresponding enum .value strings (e.g. "trend_breakout", "one_day").
  max_candidates is applied after all dimension filters.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import Any

from src.research.candidate_universe import CandidateSpec, build_initial_candidate_universe
from src.research.report_schema import ResearchReport
from src.research.report_snapshot_runner import (
    ReportSnapshotRunResult,
    run_offline_report_snapshot,
)

logger = logging.getLogger(__name__)

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

_ALL_FALSE: dict[str, bool] = {flag: False for flag in _SAFETY_FLAGS}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateFilter:
    """Deterministic multi-value filter for the candidate universe.

    Each dimension is an OR filter: an empty tuple passes all values.
    All non-empty dimensions are AND-combined.
    strategy_families and holding_horizons match enum .value strings.
    max_candidates is applied after all dimension filters.
    """
    groups: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    intervals: tuple[str, ...] = ()
    strategy_families: tuple[str, ...] = ()
    holding_horizons: tuple[str, ...] = ()
    max_candidates: int | None = None


@dataclass(frozen=True)
class PipelineSummary:
    """Compact aggregate summary of a pipeline run.

    Numeric aggregates (best_average_monthly_return, worst_max_drawdown,
    total_trades_sum) are None when no qualifying reports exist.
    best_candidate_id is chosen by highest average_monthly_return_mean
    among PASS reports; ties broken lexicographically by candidate_id.
    worst_max_drawdown is the most negative max_drawdown_worst across all
    reports with a numeric value (not restricted to PASS only).
    total_trades_sum sums total_trades_sum across all reports with a
    numeric value.
    """
    result: str
    blocker: str | None
    candidates_selected: int
    reports_created: int
    reports_passed: int
    reports_blocked: int
    reports_error: int
    best_candidate_id: str | None
    best_average_monthly_return: float | None
    worst_max_drawdown: float | None
    total_trades_sum: int | None


@dataclass(frozen=True)
class PipelineRunResult:
    """Immutable result of run_offline_research_pipeline().

    filter: the CandidateFilter used (default CandidateFilter() if none supplied).
    summary: compact aggregate summary.
    snapshot: full ReportSnapshotRunResult from the snapshot runner.
    Safety flags mirror snapshot flags; expected all False offline.
    """
    result: str
    blocker: str | None
    filter: CandidateFilter
    summary: PipelineSummary
    snapshot: ReportSnapshotRunResult
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_filter(
    candidates: tuple[CandidateSpec, ...],
    f: CandidateFilter,
) -> tuple[CandidateSpec, ...]:
    """Apply CandidateFilter to a candidate tuple; preserve original order."""
    result: list[CandidateSpec] = []
    for c in candidates:
        if f.groups and c.group not in f.groups:
            continue
        if f.symbols and c.symbol not in f.symbols:
            continue
        if f.intervals and c.interval not in f.intervals:
            continue
        if f.strategy_families and c.strategy_family.value not in f.strategy_families:
            continue
        if f.holding_horizons and c.holding_horizon.value not in f.holding_horizons:
            continue
        result.append(c)
    if f.max_candidates is not None:
        result = result[:f.max_candidates]
    return tuple(result)


def _compute_pipeline_summary(
    snapshot: ReportSnapshotRunResult,
    candidates_selected: int,
) -> PipelineSummary:
    """Compute PipelineSummary from a ReportSnapshotRunResult."""
    reports = snapshot.reports
    n_pass = sum(1 for r in reports if r.summary.result == "PASS")
    n_blocked = sum(1 for r in reports if r.summary.result == "BLOCKED")
    n_error = sum(1 for r in reports if r.summary.result == "ERROR")

    # Best candidate: highest average_monthly_return_mean among PASS reports.
    pass_with_return = [
        r for r in reports
        if r.summary.result == "PASS" and r.summary.average_monthly_return_mean is not None
    ]
    best_candidate_id: str | None = None
    best_average_monthly_return: float | None = None
    if pass_with_return:
        max_ret = max(r.summary.average_monthly_return_mean for r in pass_with_return)
        top = [r for r in pass_with_return if r.summary.average_monthly_return_mean == max_ret]
        best = min(top, key=lambda r: r.summary.candidate_id)
        best_candidate_id = best.summary.candidate_id
        best_average_monthly_return = best.summary.average_monthly_return_mean

    # Worst drawdown: most negative across all reports with a numeric value.
    drawdowns = [
        r.summary.max_drawdown_worst
        for r in reports
        if r.summary.max_drawdown_worst is not None
    ]
    worst_max_drawdown: float | None = min(drawdowns) if drawdowns else None

    # Total trades: sum across all reports with a numeric value.
    trades = [
        r.summary.total_trades_sum
        for r in reports
        if r.summary.total_trades_sum is not None
    ]
    total_trades_sum: int | None = sum(trades) if trades else None

    return PipelineSummary(
        result=snapshot.result,
        blocker=snapshot.blocker,
        candidates_selected=candidates_selected,
        reports_created=snapshot.reports_created,
        reports_passed=n_pass,
        reports_blocked=n_blocked,
        reports_error=n_error,
        best_candidate_id=best_candidate_id,
        best_average_monthly_return=best_average_monthly_return,
        worst_max_drawdown=worst_max_drawdown,
        total_trades_sum=total_trades_sum,
    )


def _empty_snapshot() -> ReportSnapshotRunResult:
    """Minimal BLOCKED snapshot for the no-candidates-selected case."""
    return ReportSnapshotRunResult(
        result="BLOCKED",
        blocker="no candidates selected",
        candidates_requested=0,
        reports_created=0,
        reports=(),
        json_reports=(),
        **_ALL_FALSE,
    )


def _blocked_summary(candidates_selected: int = 0) -> PipelineSummary:
    """Minimal BLOCKED summary for the no-candidates-selected case."""
    return PipelineSummary(
        result="BLOCKED",
        blocker="no candidates selected",
        candidates_selected=candidates_selected,
        reports_created=0,
        reports_passed=0,
        reports_blocked=0,
        reports_error=0,
        best_candidate_id=None,
        best_average_monthly_return=None,
        worst_max_drawdown=None,
        total_trades_sum=None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_offline_research_pipeline(
    *,
    candidate_filter: CandidateFilter | None = None,
    start_date: str,
    end_date: str,
    train_months: int,
    test_months: int,
    step_months: int,
    cache_dir: str | pathlib.Path | None = None,
    include_json: bool = False,
    generated_at_utc: str | None = None,
    _snapshot_runner=None,
) -> PipelineRunResult:
    """Run the offline research pipeline and return a compact in-memory summary.

    Parameters
    ----------
    candidate_filter:
        Filters to apply to the initial candidate universe. If None, all
        candidates from build_initial_candidate_universe() are used.
    start_date:
        ISO-8601 start of the walk-forward date range (inclusive).
    end_date:
        ISO-8601 end of the walk-forward date range (inclusive).
    train_months:
        Width of each training window in calendar months.
    test_months:
        Width of each test window in calendar months.
    step_months:
        Step size between successive train_start dates.
    cache_dir:
        Directory containing .parquet or .csv cache files.
    include_json:
        If True, JSON-serialised reports are included in the snapshot.
        No files are written.
    generated_at_utc:
        ISO-8601 UTC timestamp injectable for deterministic tests.
    _snapshot_runner:
        Test injection: callable(candidates, *, start_date, end_date,
        train_months, test_months, step_months, cache_dir, include_json,
        generated_at_utc) → ReportSnapshotRunResult.
        When None, run_offline_report_snapshot is used.

    Returns
    -------
    PipelineRunResult
        result="PASS"    when ≥1 report passed.
        result="BLOCKED" when no candidates selected, or all BLOCKED.
        result="ERROR"   when ≥1 report errored.
        All safety flags are False for fully offline evaluation.

    Notes
    -----
    No files are written. No result persistence. No broker calls.
    No credentials. No network calls. No order submission. No live gates.
    No parameter optimisation.
    """
    active_filter = candidate_filter if candidate_filter is not None else CandidateFilter()
    all_candidates = build_initial_candidate_universe()
    selected = _apply_filter(all_candidates, active_filter)

    if not selected:
        snap = _empty_snapshot()
        return PipelineRunResult(
            result="BLOCKED",
            blocker="no candidates selected",
            filter=active_filter,
            summary=_blocked_summary(0),
            snapshot=snap,
            **_ALL_FALSE,
        )

    active_runner = (
        _snapshot_runner
        if _snapshot_runner is not None
        else run_offline_report_snapshot
    )

    snapshot = active_runner(
        selected,
        start_date=start_date,
        end_date=end_date,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
        cache_dir=cache_dir,
        include_json=include_json,
        generated_at_utc=generated_at_utc,
    )

    summary = _compute_pipeline_summary(snapshot, len(selected))

    return PipelineRunResult(
        result=snapshot.result,
        blocker=snapshot.blocker,
        filter=active_filter,
        summary=summary,
        snapshot=snapshot,
        broker_calls_made=snapshot.broker_calls_made,
        credentials_read=snapshot.credentials_read,
        network_calls_made=snapshot.network_calls_made,
        order_action_requested=snapshot.order_action_requested,
        live_trading_allowed=snapshot.live_trading_allowed,
    )
