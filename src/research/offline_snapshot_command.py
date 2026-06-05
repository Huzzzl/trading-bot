"""
research/offline_snapshot_command.py
---------------------------------------
S11: Controlled offline research snapshot command.

Wires run_offline_research_pipeline() (S8) and
persist_pipeline_run_result() (S10) into a single entry point.

No broker/API/credential/env/network/order/live/paper access.
No live/paper trading approval. No parameter optimisation.
No default output path. No files written unless output_dir is supplied.

Aggregate result rule:
  BLOCKED — output_dir missing/empty, or persistence returned BLOCKED.
  ERROR   — pipeline returned ERROR, or persistence raised an exception.
  PASS    — persistence returned PASS (pipeline result PASS or BLOCKED).

If any safety flag on the final result is True, result is forced to BLOCKED.
Safety flags are the OR of pipeline and persistence flags; expected all False.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import Any

from src.research.pipeline_orchestrator import CandidateFilter, PipelineRunResult, run_offline_research_pipeline
from src.research.report_persistence import ReportPersistenceResult, persist_pipeline_run_result

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
# Public result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OfflineSnapshotCommandResult:
    """Immutable result of run_controlled_offline_snapshot().

    result is "PASS" when pipeline ran and persistence succeeded,
    "BLOCKED" when output_dir is missing or persistence blocked, and
    "ERROR" when pipeline errored or persistence raised an exception.
    pipeline and persistence hold the sub-results when available.
    Safety flags are the OR of pipeline and persistence flags; expected
    all False for fully offline operation.
    """
    result: str
    blocker: str | None
    pipeline: PipelineRunResult | None
    persistence: ReportPersistenceResult | None
    output_dir: str | None
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _or_safety_flags(
    pipeline: PipelineRunResult | None,
    persistence: ReportPersistenceResult | None,
) -> dict[str, bool]:
    """OR safety flags from pipeline and persistence results."""
    result: dict[str, bool] = dict(_ALL_FALSE)
    if pipeline is not None:
        for flag in _SAFETY_FLAGS:
            if getattr(pipeline, flag, False):
                result[flag] = True
    if persistence is not None:
        for flag in _SAFETY_FLAGS:
            if getattr(persistence, flag, False):
                result[flag] = True
    return result


def _any_safety_flag(flags: dict[str, bool]) -> bool:
    return any(flags.values())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_controlled_offline_snapshot(
    *,
    candidate_filter: CandidateFilter | None = None,
    start_date: str,
    end_date: str,
    train_months: int,
    test_months: int,
    step_months: int,
    cache_dir: str | pathlib.Path | None = None,
    output_dir: str | pathlib.Path | None,
    include_json: bool = False,
    include_markdown: bool = False,
    generated_at_utc: str | None = None,
    git_commit_sha: str | None = None,
    allow_overwrite: bool = False,
    allow_unsafe_path: bool = False,
    _pipeline_runner=None,
    _persistence_runner=None,
) -> OfflineSnapshotCommandResult:
    """Run the offline research pipeline and persist results to a local directory.

    Parameters
    ----------
    candidate_filter:
        Filters to apply to the initial candidate universe. None uses all.
    start_date / end_date:
        ISO-8601 walk-forward date range (inclusive).
    train_months / test_months / step_months:
        Walk-forward window configuration.
    cache_dir:
        Directory containing .parquet or .csv cache files.
    output_dir:
        Caller-supplied directory for persistence output. Required; None or
        empty returns BLOCKED without calling the pipeline or persistence.
        No default value is provided.
    include_json:
        If True, JSON-serialised reports are included in the pipeline snapshot.
    include_markdown:
        If True, a human-readable summary.md is written.
    generated_at_utc:
        ISO-8601 UTC timestamp injectable for deterministic tests.
    git_commit_sha:
        Optional git commit SHA for provenance in manifest.
    allow_overwrite:
        If True, replace an existing run directory atomically.
    allow_unsafe_path:
        If True, allow absolute output_dir paths outside the repo root.
    _pipeline_runner:
        Test injection: replaces run_offline_research_pipeline().
        Signature: (*, candidate_filter, start_date, end_date, train_months,
        test_months, step_months, cache_dir, include_json, generated_at_utc)
        → PipelineRunResult.
    _persistence_runner:
        Test injection: replaces persist_pipeline_run_result().
        Signature: (pipeline_result, *, output_dir, generated_at_utc,
        git_commit_sha, include_markdown, allow_overwrite, allow_unsafe_path)
        → ReportPersistenceResult.

    Returns
    -------
    OfflineSnapshotCommandResult
        result="PASS"    when persistence succeeded.
        result="BLOCKED" when output_dir missing or persistence blocked.
        result="ERROR"   when pipeline errored or persistence raised.
        All safety flags are False for fully offline operation.

    Notes
    -----
    No broker/API/credential/env/network/order/live/paper access.
    No live/paper trading approval. No parameter optimisation.
    No files written unless output_dir is explicitly supplied.
    Persisting a pipeline result does not approve or trigger any trade.
    """
    # 1. Validate output_dir before running the pipeline.
    if not output_dir:
        return OfflineSnapshotCommandResult(
            result="BLOCKED",
            blocker="output_dir is required and must not be empty",
            pipeline=None,
            persistence=None,
            output_dir=None,
            **_ALL_FALSE,
        )

    output_dir_str = str(output_dir)

    # 2. Run the pipeline.
    active_pipeline = (
        _pipeline_runner
        if _pipeline_runner is not None
        else run_offline_research_pipeline
    )

    pipeline_result: PipelineRunResult = active_pipeline(
        candidate_filter=candidate_filter,
        start_date=start_date,
        end_date=end_date,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
        cache_dir=cache_dir,
        include_json=include_json,
        generated_at_utc=generated_at_utc,
    )

    # 3. If pipeline errored, do not persist.
    if pipeline_result.result == "ERROR":
        flags = _or_safety_flags(pipeline_result, None)
        return OfflineSnapshotCommandResult(
            result="ERROR",
            blocker=pipeline_result.blocker,
            pipeline=pipeline_result,
            persistence=None,
            output_dir=output_dir_str,
            **flags,
        )

    # 4. Persist the pipeline result (PASS or BLOCKED pipeline both proceed).
    active_persistence = (
        _persistence_runner
        if _persistence_runner is not None
        else persist_pipeline_run_result
    )

    try:
        persistence_result: ReportPersistenceResult = active_persistence(
            pipeline_result,
            output_dir=output_dir,
            generated_at_utc=generated_at_utc,
            git_commit_sha=git_commit_sha,
            include_markdown=include_markdown,
            allow_overwrite=allow_overwrite,
            allow_unsafe_path=allow_unsafe_path,
        )
    except Exception as exc:
        logger.warning(
            "offline_snapshot_command: persistence raised: %s", exc
        )
        flags = _or_safety_flags(pipeline_result, None)
        return OfflineSnapshotCommandResult(
            result="ERROR",
            blocker=f"persistence error: {exc}",
            pipeline=pipeline_result,
            persistence=None,
            output_dir=output_dir_str,
            **flags,
        )

    # 5. Aggregate flags and final result.
    flags = _or_safety_flags(pipeline_result, persistence_result)

    if persistence_result.result == "PASS" and not _any_safety_flag(flags):
        final_result = "PASS"
        blocker: str | None = None
    elif _any_safety_flag(flags):
        final_result = "BLOCKED"
        true_flags = [f for f in _SAFETY_FLAGS if flags[f]]
        blocker = f"non-false safety flag(s): {', '.join(true_flags)}"
    else:
        final_result = "BLOCKED"
        blocker = persistence_result.blocker

    return OfflineSnapshotCommandResult(
        result=final_result,
        blocker=blocker,
        pipeline=pipeline_result,
        persistence=persistence_result,
        output_dir=output_dir_str,
        **flags,
    )
