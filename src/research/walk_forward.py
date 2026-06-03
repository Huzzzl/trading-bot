"""
research/walk_forward.py
--------------------------
S4: Walk-forward split generation and evaluation skeleton.

Provides deterministic rolling walk-forward splits over a date range and a
fail-closed skeleton evaluator that accepts an injected per-split evaluator.

Walk-forward split design (rolling):
  Given start_date, end_date, train_months, test_months, step_months:

  - Training window: train_months wide, anchored at train_start.
  - Test window: test_months wide, immediately following each training window.
    test_start = train_end + 1 day.
    test_end   = test_start + test_months months, minus 1 day (inclusive end).
  - Step: train_start advances by step_months for each successive split.
  - Stop when test_end > end_date (split would exceed the available range).

  Training windows overlap (by design); test windows do NOT overlap.
  Date boundaries are all ISO-8601 strings (YYYY-MM-DD).

  Split ID format: WF{n:03d}_{train_start}_{train_end}_{test_start}_{test_end}
  Example: WF001_2020-01-01_2021-01-01_2021-01-02_2021-04-01

Safety flags:
  broker_calls_made, credentials_read, network_calls_made,
  order_action_requested, live_trading_allowed — all always False.

This module does not import from broker adapters, live tools, environment
variables, network libraries, or execution layers. No file I/O. No parameter
optimization. No order submission.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from src.research.candidate_evaluator import (
    REQUIRED_METRIC_KEYS,
    CandidateEvaluationResult,
)
from src.research.candidate_universe import CandidateSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date arithmetic
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    """Add months to a date, clamping the day to the end of the target month."""
    total_months = d.month - 1 + months
    year = d.year + total_months // 12
    month = total_months % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardSplit:
    """Immutable definition of a single walk-forward train/test split.

    All dates are ISO-8601 strings (YYYY-MM-DD, inclusive ends).
    split_id is a deterministic human-readable key.
    """
    split_id: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class WalkForwardResult:
    """Immutable aggregate result of evaluate_walk_forward().

    result is "PASS" (all splits passed), "BLOCKED" (≥1 blocked, no errors),
    or "ERROR" (≥1 error).
    splits_requested: total splits passed in.
    splits_evaluated: splits for which the evaluator was called.
    split_results: per-split CandidateEvaluationResult in order.
    All safety flags are always False.
    """
    result: str
    blocker: str | None
    candidate_id: str
    splits_requested: int
    splits_evaluated: int
    split_results: tuple[CandidateEvaluationResult, ...]
    broker_calls_made: bool      # always False
    credentials_read: bool       # always False
    network_calls_made: bool     # always False
    order_action_requested: bool # always False
    live_trading_allowed: bool   # always False


# ---------------------------------------------------------------------------
# Split generation
# ---------------------------------------------------------------------------

def build_walk_forward_splits(
    *,
    start_date: str,
    end_date: str,
    train_months: int,
    test_months: int,
    step_months: int,
) -> tuple[WalkForwardSplit, ...]:
    """Generate deterministic rolling walk-forward splits.

    Parameters
    ----------
    start_date:
        ISO-8601 start of the full date range (inclusive), e.g. "2020-01-01".
    end_date:
        ISO-8601 end of the full date range (inclusive), e.g. "2022-12-31".
    train_months:
        Width of each training window in calendar months. Must be >= 1.
    test_months:
        Width of each test window in calendar months. Must be >= 1.
    step_months:
        Step size between successive train_start dates. Must be >= test_months
        to keep test windows non-overlapping.

    Returns
    -------
    tuple[WalkForwardSplit, ...]
        Deterministic immutable tuple of splits in chronological order.
        Returns an empty tuple when no complete split fits in the date range.

    Raises
    ------
    ValueError
        When train_months, test_months, or step_months is < 1.
        When step_months < test_months (would cause test windows to overlap).
        When end_date <= start_date.
        When start_date or end_date is not a valid ISO-8601 date.

    Notes
    -----
    Train windows overlap by (train_months - step_months) months when
    step_months < train_months; test windows never overlap.
    Month arithmetic clamps to month-end (e.g. Jan 31 + 1 month = Feb 28/29).
    """
    if train_months < 1:
        raise ValueError("train_months must be >= 1")
    if test_months < 1:
        raise ValueError("test_months must be >= 1")
    if step_months < 1:
        raise ValueError("step_months must be >= 1")
    if step_months < test_months:
        raise ValueError(
            "step_months must be >= test_months to keep test windows non-overlapping"
        )

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    if end <= start:
        raise ValueError("end_date must be after start_date")

    splits: list[WalkForwardSplit] = []
    n = 1
    train_start = start

    while True:
        train_end = _add_months(train_start, train_months)
        test_start = train_end + timedelta(days=1)
        test_end = _add_months(test_start, test_months) - timedelta(days=1)

        if test_end > end:
            break

        split_id = (
            f"WF{n:03d}"
            f"_{train_start.isoformat()}"
            f"_{train_end.isoformat()}"
            f"_{test_start.isoformat()}"
            f"_{test_end.isoformat()}"
        )
        splits.append(WalkForwardSplit(
            split_id=split_id,
            train_start=train_start.isoformat(),
            train_end=train_end.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
        ))

        train_start = _add_months(train_start, step_months)
        n += 1

    return tuple(splits)


# ---------------------------------------------------------------------------
# Walk-forward evaluation skeleton
# ---------------------------------------------------------------------------

def _make_error_result(
    candidate: CandidateSpec,
    exc: Exception,
) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="ERROR",
        blocker=str(exc),
        metrics={k: None for k in REQUIRED_METRIC_KEYS},
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


_SAFETY: dict[str, Any] = dict(
    broker_calls_made=False,
    credentials_read=False,
    network_calls_made=False,
    order_action_requested=False,
    live_trading_allowed=False,
)


def evaluate_walk_forward(
    candidate: CandidateSpec,
    splits: tuple[WalkForwardSplit, ...],
    *,
    split_evaluator=None,
) -> WalkForwardResult:
    """Evaluate a candidate across walk-forward splits using an injected evaluator.

    Parameters
    ----------
    candidate:
        The CandidateSpec to evaluate.
    splits:
        Walk-forward splits as returned by build_walk_forward_splits().
    split_evaluator:
        Callable(candidate, split) → CandidateEvaluationResult.
        When None, returns BLOCKED immediately (fail-closed default).

    Returns
    -------
    WalkForwardResult
        result="PASS"    when all splits returned PASS.
        result="BLOCKED" when ≥1 split was BLOCKED and none errored.
        result="ERROR"   when ≥1 split errored.
        All splits are evaluated even if earlier ones fail (evaluate-all policy).
        All safety flags are always False.

    Notes
    -----
    If split_evaluator raises an exception for a split, that split is recorded
    as result="ERROR" and evaluation continues with subsequent splits.
    """
    if split_evaluator is None:
        return WalkForwardResult(
            result="BLOCKED",
            blocker="walk-forward split evaluator not wired",
            candidate_id=candidate.candidate_id,
            splits_requested=len(splits),
            splits_evaluated=0,
            split_results=(),
            **_SAFETY,
        )

    if not splits:
        return WalkForwardResult(
            result="BLOCKED",
            blocker="no walk-forward splits",
            candidate_id=candidate.candidate_id,
            splits_requested=0,
            splits_evaluated=0,
            split_results=(),
            **_SAFETY,
        )

    all_results: list[CandidateEvaluationResult] = []

    for split in splits:
        try:
            result = split_evaluator(candidate, split)
        except Exception as exc:
            logger.error(
                "evaluate_walk_forward: split %s error: %s",
                split.split_id,
                exc,
            )
            result = _make_error_result(candidate, exc)
        all_results.append(result)

    split_results = tuple(all_results)
    n_pass = sum(1 for r in split_results if r.result == "PASS")
    n_error = sum(1 for r in split_results if r.result == "ERROR")
    n_blocked = sum(1 for r in split_results if r.result == "BLOCKED")

    if n_error > 0:
        overall = "ERROR"
        blocker: str | None = f"{n_error} split(s) returned ERROR"
    elif n_blocked > 0:
        overall = "BLOCKED"
        blocker = f"{n_blocked} split(s) were BLOCKED"
    else:
        overall = "PASS"
        blocker = None

    return WalkForwardResult(
        result=overall,
        blocker=blocker,
        candidate_id=candidate.candidate_id,
        splits_requested=len(splits),
        splits_evaluated=len(split_results),
        split_results=split_results,
        **_SAFETY,
    )
