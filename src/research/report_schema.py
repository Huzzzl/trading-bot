"""
research/report_schema.py
--------------------------
S6: Offline research report schema.

Defines immutable, deterministic, JSON-serializable report objects that
normalise candidate metadata, walk-forward split results, per-split metrics
validation, aggregated summary statistics, and safety flags.

Aggregation rule:
  Only splits where both the split result == "PASS" AND the metrics
  validation result == "PASS" contribute to aggregate summary statistics
  (average_monthly_return_mean/min, max_drawdown_worst, total_trades_sum).

max_drawdown convention (inherited from S4):
  max_drawdown is a non-positive decimal (e.g. -0.15 means -15%).
  max_drawdown_worst is the minimum (most negative) value across valid splits.

Schema version: "S6/1.0"

Safety flags:
  broker_calls_made, credentials_read, network_calls_made,
  order_action_requested, live_trading_allowed — all always False for
  fully offline evaluation. If any flag is unexpectedly True, a note is
  appended automatically; the overall result is not changed.

No files are written or read.
No network calls, credentials, broker calls, order submission, or live gates.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any, Mapping

from src.research.cached_walk_forward_runner import CachedWalkForwardRunResult
from src.research.candidate_universe import CandidateSpec
from src.research.walk_forward import WalkForwardSplit

_SCHEMA_VERSION = "S6/1.0"

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchReportCandidate:
    """Candidate metadata snapshot."""
    candidate_id: str
    group: str
    symbol: str
    interval: str
    strategy_family: str
    holding_horizon: str


@dataclass(frozen=True)
class ResearchReportSplit:
    """Per-split evaluation result with validation status.

    train_start / train_end / test_start / test_end are None when the
    WalkForwardSplit objects are not passed to build_research_report().
    """
    split_id: str
    train_start: str | None
    train_end: str | None
    test_start: str | None
    test_end: str | None
    result: str
    blocker: str | None
    metrics: Mapping[str, Any]
    metrics_validation_result: str
    metrics_validation_blocker: str | None


@dataclass(frozen=True)
class ResearchReportSummary:
    """Aggregate summary; statistics computed only from PASS + validated splits."""
    result: str
    blocker: str | None
    candidate_id: str
    splits_requested: int
    splits_evaluated: int
    splits_passed: int
    splits_blocked: int
    splits_error: int
    validations_passed: int
    validations_blocked: int
    average_monthly_return_mean: float | None
    average_monthly_return_min: float | None
    max_drawdown_worst: float | None
    total_trades_sum: int | None


@dataclass(frozen=True)
class ResearchReport:
    """Immutable, JSON-serializable offline research report.

    generated_at_utc is injectable for deterministic tests.
    safety flags mirror the input CachedWalkForwardRunResult; all expected False.
    """
    schema_version: str
    generated_at_utc: str
    candidate: ResearchReportCandidate
    summary: ResearchReportSummary
    splits: tuple[ResearchReportSplit, ...]
    safety: Mapping[str, bool]
    notes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_float(metrics: Mapping[str, Any], key: str) -> float | None:
    """Return a finite float from metrics[key], or None if absent/non-numeric/NaN."""
    v = metrics.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if f == float("inf") or f == float("-inf"):
        return None
    return f


def _collect_finite(
    metrics_list: list[Mapping[str, Any]],
    key: str,
) -> list[float]:
    """Collect all finite float values for key across a list of metrics dicts."""
    result: list[float] = []
    for m in metrics_list:
        v = _extract_float(m, key)
        if v is not None:
            result.append(v)
    return result


def _compute_summary(
    walk_forward_result: CachedWalkForwardRunResult,
) -> ResearchReportSummary:
    """Aggregate summary statistics from split + validation results."""
    split_results = walk_forward_result.split_results
    validation_results = walk_forward_result.validation_results

    n_passed = sum(1 for r in split_results if r.result == "PASS")
    n_blocked = sum(1 for r in split_results if r.result == "BLOCKED")
    n_error = sum(1 for r in split_results if r.result == "ERROR")
    n_val_passed = sum(1 for v in validation_results if v.result == "PASS")
    n_val_blocked = sum(1 for v in validation_results if v.result == "BLOCKED")

    # Only PASS split + PASS validation pairs contribute to aggregate metrics.
    valid_metrics: list[Mapping[str, Any]] = [
        sr.metrics
        for sr, vr in zip(split_results, validation_results)
        if sr.result == "PASS" and vr.result == "PASS"
    ]

    monthly_returns = _collect_finite(valid_metrics, "average_monthly_return")
    drawdowns = _collect_finite(valid_metrics, "max_drawdown")
    trade_floats = _collect_finite(valid_metrics, "total_trades")

    avg_monthly_mean: float | None = (
        sum(monthly_returns) / len(monthly_returns) if monthly_returns else None
    )
    avg_monthly_min: float | None = min(monthly_returns) if monthly_returns else None
    max_dd_worst: float | None = min(drawdowns) if drawdowns else None
    total_trades: int | None = int(sum(trade_floats)) if trade_floats else None

    return ResearchReportSummary(
        result=walk_forward_result.result,
        blocker=walk_forward_result.blocker,
        candidate_id=walk_forward_result.candidate_id,
        splits_requested=walk_forward_result.splits_requested,
        splits_evaluated=walk_forward_result.splits_evaluated,
        splits_passed=n_passed,
        splits_blocked=n_blocked,
        splits_error=n_error,
        validations_passed=n_val_passed,
        validations_blocked=n_val_blocked,
        average_monthly_return_mean=avg_monthly_mean,
        average_monthly_return_min=avg_monthly_min,
        max_drawdown_worst=max_dd_worst,
        total_trades_sum=total_trades,
    )


def _build_split_reports(
    walk_forward_result: CachedWalkForwardRunResult,
    splits: tuple[WalkForwardSplit, ...] | None,
) -> tuple[ResearchReportSplit, ...]:
    """Build per-split report records, optionally enriched with split date metadata."""
    split_results = walk_forward_result.split_results
    validation_results = walk_forward_result.validation_results
    n = len(split_results)

    report_splits: list[ResearchReportSplit] = []
    for i, sr in enumerate(split_results):
        if splits is not None and i < len(splits):
            ws = splits[i]
            split_id = ws.split_id
            train_start: str | None = ws.train_start
            train_end: str | None = ws.train_end
            test_start: str | None = ws.test_start
            test_end: str | None = ws.test_end
        else:
            split_id = f"split_{i + 1:03d}"
            train_start = None
            train_end = None
            test_start = None
            test_end = None

        if i < len(validation_results):
            vr = validation_results[i]
            val_result = vr.result
            val_blocker = vr.blocker
        else:
            val_result = "UNKNOWN"
            val_blocker = "validation result not available"

        report_splits.append(ResearchReportSplit(
            split_id=split_id,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            result=sr.result,
            blocker=sr.blocker,
            metrics=dict(sr.metrics),
            metrics_validation_result=val_result,
            metrics_validation_blocker=val_blocker,
        ))

    return tuple(report_splits)


def _check_safety(
    walk_forward_result: CachedWalkForwardRunResult,
) -> tuple[dict[str, bool], tuple[str, ...]]:
    """Extract safety flags; append notes for any unexpectedly True flag."""
    safety: dict[str, bool] = {
        flag: bool(getattr(walk_forward_result, flag, False))
        for flag in _SAFETY_FLAGS
    }
    notes: list[str] = [
        f"non-false safety flag observed: {flag}"
        for flag, value in safety.items()
        if value
    ]
    return safety, tuple(notes)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_research_report(
    candidate: CandidateSpec,
    walk_forward_result: CachedWalkForwardRunResult,
    *,
    generated_at_utc: str | None = None,
    notes: tuple[str, ...] = (),
    splits: tuple[WalkForwardSplit, ...] | None = None,
) -> ResearchReport:
    """Build an immutable ResearchReport from offline evaluation results.

    Parameters
    ----------
    candidate:
        The CandidateSpec that was evaluated.
    walk_forward_result:
        The CachedWalkForwardRunResult from run_cached_walk_forward_evaluation().
    generated_at_utc:
        ISO-8601 UTC timestamp string. If None, current UTC time is used.
        Pass a fixed string for deterministic tests.
    notes:
        Optional caller-supplied notes appended to the report.
    splits:
        Optional WalkForwardSplit objects in chronological order. When
        provided, train/test date metadata is included in each split record.
        When None, split date fields are None and split IDs are index-based.

    Returns
    -------
    ResearchReport
        Fully immutable, JSON-serializable report. No files are written.

    Notes
    -----
    Aggregation (average_monthly_return_mean/min, max_drawdown_worst,
    total_trades_sum) uses only splits where both split result == "PASS"
    and metrics validation result == "PASS". All other splits are counted
    but excluded from numeric aggregation.

    If any safety flag is True, a note is added automatically; the result
    field is not modified.

    This function never reads credentials, contacts brokers, makes network
    calls, or submits orders.
    """
    ts = (
        generated_at_utc
        if generated_at_utc is not None
        else datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    report_candidate = ResearchReportCandidate(
        candidate_id=candidate.candidate_id,
        group=candidate.group,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        holding_horizon=candidate.holding_horizon.value,
    )

    summary = _compute_summary(walk_forward_result)
    report_splits = _build_split_reports(walk_forward_result, splits)
    safety, safety_notes = _check_safety(walk_forward_result)

    all_notes = notes + safety_notes

    return ResearchReport(
        schema_version=_SCHEMA_VERSION,
        generated_at_utc=ts,
        candidate=report_candidate,
        summary=summary,
        splits=report_splits,
        safety=safety,
        notes=all_notes,
    )


def research_report_to_dict(report: ResearchReport) -> dict[str, Any]:
    """Convert a ResearchReport to a plain JSON-serializable dict."""

    def _split_to_dict(s: ResearchReportSplit) -> dict[str, Any]:
        return {
            "split_id": s.split_id,
            "train_start": s.train_start,
            "train_end": s.train_end,
            "test_start": s.test_start,
            "test_end": s.test_end,
            "result": s.result,
            "blocker": s.blocker,
            "metrics": dict(s.metrics),
            "metrics_validation_result": s.metrics_validation_result,
            "metrics_validation_blocker": s.metrics_validation_blocker,
        }

    return {
        "schema_version": report.schema_version,
        "generated_at_utc": report.generated_at_utc,
        "candidate": {
            "candidate_id": report.candidate.candidate_id,
            "group": report.candidate.group,
            "symbol": report.candidate.symbol,
            "interval": report.candidate.interval,
            "strategy_family": report.candidate.strategy_family,
            "holding_horizon": report.candidate.holding_horizon,
        },
        "summary": {
            "result": report.summary.result,
            "blocker": report.summary.blocker,
            "candidate_id": report.summary.candidate_id,
            "splits_requested": report.summary.splits_requested,
            "splits_evaluated": report.summary.splits_evaluated,
            "splits_passed": report.summary.splits_passed,
            "splits_blocked": report.summary.splits_blocked,
            "splits_error": report.summary.splits_error,
            "validations_passed": report.summary.validations_passed,
            "validations_blocked": report.summary.validations_blocked,
            "average_monthly_return_mean": report.summary.average_monthly_return_mean,
            "average_monthly_return_min": report.summary.average_monthly_return_min,
            "max_drawdown_worst": report.summary.max_drawdown_worst,
            "total_trades_sum": report.summary.total_trades_sum,
        },
        "splits": [_split_to_dict(s) for s in report.splits],
        "safety": dict(report.safety),
        "notes": list(report.notes),
    }


def research_report_to_json(report: ResearchReport) -> str:
    """Serialize a ResearchReport to deterministic JSON (sort_keys=True, indent=2)."""
    return json.dumps(research_report_to_dict(report), sort_keys=True, indent=2)
