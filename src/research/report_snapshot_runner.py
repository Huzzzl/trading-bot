"""
research/report_snapshot_runner.py
-------------------------------------
S7: Offline report snapshot runner.

Wires CandidateSpec, cached walk-forward evaluation (run_cached_walk_forward_evaluation),
and research report schema (build_research_report) to produce ResearchReport
objects in memory.

Optionally serialises reports to deterministic JSON strings (include_json=True).
No files are written, created, or committed.

Aggregate result rule:
  ERROR  — if any report's summary.result is "ERROR".
  PASS   — if at least one report's summary.result is "PASS" and none are ERROR.
           (BLOCKED candidates from unsupported families / cache misses count
           against the run but do not prevent PASS.)
  BLOCKED — everything else (all BLOCKED, or no candidates requested).

Safety flags:
  broker_calls_made, credentials_read, network_calls_made,
  order_action_requested, live_trading_allowed — top-level flags OR across all
  individual report safety dicts. Expected all False for fully offline evaluation.

Exception handling:
  If the walk-forward runner raises an exception for a candidate, a synthetic
  ERROR CachedWalkForwardRunResult is built so a report can still be produced.
  The exception is logged as a WARNING. One candidate failure does not stop
  the entire run.

This module does not import from broker adapters, environment variables,
network libraries, or execution layers. No order submission. No live gates.
No parameter optimisation. No file writes.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import Any

from src.research.cached_walk_forward_runner import (
    CachedWalkForwardRunResult,
    run_cached_walk_forward_evaluation,
)
from src.research.candidate_universe import CandidateSpec, build_initial_candidate_universe
from src.research.report_schema import (
    ResearchReport,
    build_research_report,
    research_report_to_json,
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
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportSnapshotRunResult:
    """Immutable aggregate result of run_offline_report_snapshot().

    result is "PASS" (≥1 report PASS, no ERROR), "BLOCKED" (all BLOCKED or
    no candidates), or "ERROR" (≥1 report ERROR).
    All safety flags mirror the OR of all individual report safety dicts.
    Expected all False for fully offline evaluation.
    """
    result: str
    blocker: str | None
    candidates_requested: int
    reports_created: int
    reports: tuple[ResearchReport, ...]
    json_reports: tuple[str, ...]
    broker_calls_made: bool       # OR of all reports; always False offline
    credentials_read: bool        # OR of all reports; always False offline
    network_calls_made: bool      # OR of all reports; always False offline
    order_action_requested: bool  # OR of all reports; always False offline
    live_trading_allowed: bool    # OR of all reports; always False offline


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_error_wf_result(candidate_id: str, blocker: str) -> CachedWalkForwardRunResult:
    """Construct a minimal ERROR CachedWalkForwardRunResult for exception recovery."""
    return CachedWalkForwardRunResult(
        result="ERROR",
        blocker=blocker,
        candidate_id=candidate_id,
        splits_requested=0,
        splits_evaluated=0,
        split_results=(),
        validation_results=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _aggregate_safety(reports: list[ResearchReport]) -> dict[str, bool]:
    """OR safety flags across all reports."""
    return {
        flag: any(bool(r.safety.get(flag, False)) for r in reports)
        for flag in _SAFETY_FLAGS
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_offline_report_snapshot(
    candidates: tuple[CandidateSpec, ...] | list[CandidateSpec] | None = None,
    *,
    start_date: str,
    end_date: str,
    train_months: int,
    test_months: int,
    step_months: int,
    cache_dir: str | pathlib.Path | None = None,
    max_candidates: int | None = None,
    include_json: bool = False,
    generated_at_utc: str | None = None,
    _walk_forward_runner=None,
) -> ReportSnapshotRunResult:
    """Produce in-memory ResearchReport objects for one or more candidates.

    Parameters
    ----------
    candidates:
        Candidates to evaluate. If None, uses build_initial_candidate_universe().
    start_date:
        ISO-8601 start of the full walk-forward date range (inclusive).
    end_date:
        ISO-8601 end of the full walk-forward date range (inclusive).
    train_months:
        Width of each training window in calendar months.
    test_months:
        Width of each test window in calendar months.
    step_months:
        Step size between successive train_start dates.
    cache_dir:
        Directory containing .parquet or .csv cache files.
    max_candidates:
        If given, evaluate only the first max_candidates candidates.
    include_json:
        If True, serialise each report to a deterministic JSON string and
        include them in json_reports. No files are written.
    generated_at_utc:
        ISO-8601 UTC timestamp for all reports. Injectable for deterministic
        tests; defaults to current UTC time when None.
    _walk_forward_runner:
        Test injection: callable(candidate, *, start_date, end_date,
        train_months, test_months, step_months, cache_dir)
        → CachedWalkForwardRunResult.
        When None, run_cached_walk_forward_evaluation is used.

    Returns
    -------
    ReportSnapshotRunResult
        result="PASS"    when ≥1 report summary is PASS and none ERROR.
        result="BLOCKED" when all reports are BLOCKED, or no candidates.
        result="ERROR"   when ≥1 report summary is ERROR.
        All safety flags are False for fully offline evaluation.

    Notes
    -----
    If the walk-forward runner raises an exception for a candidate, a
    synthetic ERROR report is produced for that candidate; the remaining
    candidates are still evaluated.

    No files are written or read by this function. No network calls.
    No broker calls. No credentials. No order submission. No live gates.
    No parameter optimisation.
    """
    # 1. Resolve candidates.
    if candidates is None:
        all_candidates: list[CandidateSpec] = list(build_initial_candidate_universe())
    else:
        all_candidates = list(candidates)

    if max_candidates is not None:
        all_candidates = all_candidates[:max_candidates]

    if not all_candidates:
        return ReportSnapshotRunResult(
            result="BLOCKED",
            blocker="no candidates requested",
            candidates_requested=0,
            reports_created=0,
            reports=(),
            json_reports=(),
            **_ALL_FALSE,
        )

    # 2. Resolve walk-forward runner.
    active_runner = (
        _walk_forward_runner
        if _walk_forward_runner is not None
        else run_cached_walk_forward_evaluation
    )

    wf_kwargs: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "train_months": train_months,
        "test_months": test_months,
        "step_months": step_months,
        "cache_dir": cache_dir,
    }

    # 3. Evaluate each candidate.
    reports: list[ResearchReport] = []
    json_reports: list[str] = []

    for candidate in all_candidates:
        try:
            wf_result = active_runner(candidate, **wf_kwargs)
        except Exception as exc:
            logger.warning(
                "report_snapshot_runner: walk-forward runner raised for %s: %s",
                candidate.candidate_id,
                exc,
            )
            wf_result = _make_error_wf_result(
                candidate.candidate_id,
                f"walk-forward runner error: {exc}",
            )

        report = build_research_report(
            candidate,
            wf_result,
            generated_at_utc=generated_at_utc,
        )
        reports.append(report)

        if include_json:
            json_reports.append(research_report_to_json(report))

    # 4. Aggregate result.
    n_pass = sum(1 for r in reports if r.summary.result == "PASS")
    n_error = sum(1 for r in reports if r.summary.result == "ERROR")

    if n_error > 0:
        overall = "ERROR"
        blocker: str | None = f"{n_error} report(s) returned ERROR"
    elif n_pass > 0:
        overall = "PASS"
        blocker = None
    else:
        overall = "BLOCKED"
        blocker = "no candidates passed evaluation"

    # 5. Aggregate safety flags (OR across all reports).
    safety = _aggregate_safety(reports)

    return ReportSnapshotRunResult(
        result=overall,
        blocker=blocker,
        candidates_requested=len(all_candidates),
        reports_created=len(reports),
        reports=tuple(reports),
        json_reports=tuple(json_reports),
        **safety,
    )
