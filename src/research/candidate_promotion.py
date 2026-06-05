"""
research/candidate_promotion.py
---------------------------------
S14: Pure offline candidate promotion evaluator.

Classifies already-loaded research report dictionaries produced by S10/S11
persistence as promotion statuses per the S13 design.

No file I/O, no network, no environment variables, no broker/API calls,
no credential access, no order action, no paper/live trading approval.
PAPER_CANDIDATE_ELIGIBLE is a research classification only; it does not
approve paper or live trading.

Aggregate status priority (highest first):
  BLOCKED_SCHEMA  — unsupported or missing schema version
  BLOCKED_SAFETY  — any safety flag true
  REJECTED        — hard eligibility failure
  NEEDS_MORE_DATA — insufficient evidence (soft failure)
  PAPER_CANDIDATE_ELIGIBLE — all criteria pass
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported schema versions
# ---------------------------------------------------------------------------

_SUPPORTED_REPORT_SCHEMAS: frozenset[str] = frozenset({"S6/1.0"})
_SUPPORTED_MANIFEST_SCHEMAS: frozenset[str] = frozenset({"S9/1.0"})

# ---------------------------------------------------------------------------
# Safety flag names
# ---------------------------------------------------------------------------

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

# ---------------------------------------------------------------------------
# Eligibility thresholds (per S13 design)
# ---------------------------------------------------------------------------

_MIN_TOTAL_TRADES: int = 30
_MAX_DRAWDOWN_FLOOR: float = -0.25
_MAX_SESSION_END_FREQUENCY: float = 0.95
_LOW_EXPOSURE_THRESHOLD: float = 0.01
_EXTREME_SHARPE_THRESHOLD: float = 5.0
_MAX_BLOCKED_SPLIT_RATIO: float = 0.5

# ---------------------------------------------------------------------------
# All-false safety flags for the result itself
# ---------------------------------------------------------------------------

_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------


class PromotionStatus(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    PAPER_CANDIDATE_ELIGIBLE = "PAPER_CANDIDATE_ELIGIBLE"
    NEEDS_MORE_DATA = "NEEDS_MORE_DATA"
    REJECTED = "REJECTED"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"


@dataclass(frozen=True)
class CandidatePromotionResult:
    """Immutable result of evaluate_candidate_for_promotion().

    result is "PASS" when status is PAPER_CANDIDATE_ELIGIBLE, "BLOCKED" for
    BLOCKED_SAFETY / BLOCKED_SCHEMA / NEEDS_MORE_DATA / REJECTED.
    All safety flags are always False — the evaluator itself makes no broker,
    credential, network, order, or live trading calls.
    PAPER_CANDIDATE_ELIGIBLE is a research classification only; it does not
    approve paper or live trading.
    """
    result: str
    blocker: str | None
    candidate_id: str | None
    run_id: str | None
    status: PromotionStatus
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(d: dict[str, Any], *keys: str) -> Any:
    """Traverse nested dict by key path; return None if any key is missing."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _is_true_flag(value: Any) -> bool:
    return value is True


def _check_safety_flags(safety_dict: Any) -> list[str]:
    """Return names of any safety flags that are True."""
    if not isinstance(safety_dict, dict):
        return list(_SAFETY_FLAGS)
    return [f for f in _SAFETY_FLAGS if _is_true_flag(safety_dict.get(f))]


def _float_or_none(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        import math
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _blocked(
    status: PromotionStatus,
    blocker: str,
    candidate_id: str | None,
    run_id: str | None,
    checked: list[str],
    failed: list[str],
) -> CandidatePromotionResult:
    return CandidatePromotionResult(
        result="BLOCKED",
        blocker=blocker,
        candidate_id=candidate_id,
        run_id=run_id,
        status=status,
        criteria_checked=tuple(checked),
        criteria_failed=tuple(failed),
        **_RESULT_FLAGS,
    )


def _pass_result(
    candidate_id: str | None,
    run_id: str | None,
    checked: list[str],
) -> CandidatePromotionResult:
    return CandidatePromotionResult(
        result="PASS",
        blocker=None,
        candidate_id=candidate_id,
        run_id=run_id,
        status=PromotionStatus.PAPER_CANDIDATE_ELIGIBLE,
        criteria_checked=tuple(checked),
        criteria_failed=(),
        **_RESULT_FLAGS,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate_candidate_for_promotion(
    report_dict: dict[str, Any],
    *,
    manifest_dict: dict[str, Any],
) -> CandidatePromotionResult:
    """Classify a persisted research report dict as a promotion status.

    Parameters
    ----------
    report_dict:
        Already-loaded dict from a <run_id>/reports/<candidate_id>.json file.
        Expected schema_version "S6/1.0".
    manifest_dict:
        Already-loaded dict from a <run_id>/manifest.json file.
        Expected schema_version "S9/1.0".

    Returns
    -------
    CandidatePromotionResult
        status=PAPER_CANDIDATE_ELIGIBLE when all criteria pass.
        status=BLOCKED_SCHEMA when schema version unsupported.
        status=BLOCKED_SAFETY when any safety flag is True.
        status=REJECTED when a hard eligibility criterion fails.
        status=NEEDS_MORE_DATA when evidence is insufficient.
        All safety flags on the result are always False.

    Notes
    -----
    Pure function: same inputs -> same output.
    No file I/O, no network, no environment variables, no broker/API calls,
    no credential access, no order action, no paper/live trading approval.
    PAPER_CANDIDATE_ELIGIBLE is a research classification only.
    """
    checked: list[str] = []
    failed: list[str] = []

    # Tentative identity (extracted before schema check for error messages)
    candidate_id: str | None = _get(report_dict, "candidate", "candidate_id")
    if isinstance(candidate_id, str) and candidate_id:
        candidate_id = candidate_id
    else:
        candidate_id = None
    run_id: str | None = _get(manifest_dict, "run_id")
    if not isinstance(run_id, str) or not run_id:
        run_id = None

    # -----------------------------------------------------------------------
    # 1. Schema version checks (BLOCKED_SCHEMA — highest priority)
    # -----------------------------------------------------------------------
    checked.append("schema.report")
    report_schema = _get(report_dict, "schema_version")
    if report_schema not in _SUPPORTED_REPORT_SCHEMAS:
        failed.append("schema.report")
        return _blocked(
            PromotionStatus.BLOCKED_SCHEMA,
            f"unsupported report schema_version: {report_schema!r}",
            candidate_id,
            run_id,
            checked,
            failed,
        )

    checked.append("schema.manifest")
    manifest_schema = _get(manifest_dict, "schema_version")
    if manifest_schema not in _SUPPORTED_MANIFEST_SCHEMAS:
        failed.append("schema.manifest")
        return _blocked(
            PromotionStatus.BLOCKED_SCHEMA,
            f"unsupported manifest schema_version: {manifest_schema!r}",
            candidate_id,
            run_id,
            checked,
            failed,
        )

    # -----------------------------------------------------------------------
    # 2. Safety flag checks (BLOCKED_SAFETY)
    # -----------------------------------------------------------------------
    checked.append("safety.report")
    report_safety = _get(report_dict, "safety")
    report_bad_flags = _check_safety_flags(report_safety)
    if report_bad_flags:
        failed.append("safety.report")
        return _blocked(
            PromotionStatus.BLOCKED_SAFETY,
            f"non-false safety flag(s) in report: {', '.join(report_bad_flags)}",
            candidate_id,
            run_id,
            checked,
            failed,
        )

    checked.append("safety.manifest")
    manifest_safety = _get(manifest_dict, "safety")
    manifest_bad_flags = _check_safety_flags(manifest_safety)
    if manifest_bad_flags:
        failed.append("safety.manifest")
        return _blocked(
            PromotionStatus.BLOCKED_SAFETY,
            f"non-false safety flag(s) in manifest: {', '.join(manifest_bad_flags)}",
            candidate_id,
            run_id,
            checked,
            failed,
        )

    # -----------------------------------------------------------------------
    # 3. Candidate identity checks
    # -----------------------------------------------------------------------
    checked.append("candidate_id.present")
    if candidate_id is None:
        failed.append("candidate_id.present")

    checked.append("manifest.inventory")
    manifest_reports: Any = _get(manifest_dict, "files", "reports")
    if not isinstance(manifest_reports, list):
        # Missing files block or non-list reports field is a hard failure.
        failed.append("manifest.inventory")
    elif candidate_id is not None:
        if f"{candidate_id}.json" not in manifest_reports:
            failed.append("manifest.inventory")

    checked.append("manifest.git_commit_sha")
    git_sha = _get(manifest_dict, "git_commit_sha")
    if not (isinstance(git_sha, str) and git_sha):
        failed.append("manifest.git_commit_sha")

    # -----------------------------------------------------------------------
    # 4. Summary field extraction and threshold checks
    # -----------------------------------------------------------------------
    summary = _get(report_dict, "summary") or {}

    # 4a. summary.result
    checked.append("summary.result")
    summary_result = summary.get("result") if isinstance(summary, dict) else None
    if summary_result != "PASS":
        failed.append("summary.result")

    # 4b. summary.splits_error
    checked.append("summary.splits_error")
    splits_error = _int_or_none(summary.get("splits_error") if isinstance(summary, dict) else None)
    if splits_error is None or splits_error > 0:
        failed.append("summary.splits_error")

    # 4c. summary.validations_blocked
    checked.append("summary.validations_blocked")
    validations_blocked = _int_or_none(summary.get("validations_blocked") if isinstance(summary, dict) else None)
    if validations_blocked is None or validations_blocked > 0:
        failed.append("summary.validations_blocked")

    # 4d. summary.total_trades_sum
    checked.append("summary.total_trades_sum")
    total_trades = _int_or_none(summary.get("total_trades_sum") if isinstance(summary, dict) else None)
    if total_trades is None or total_trades < _MIN_TOTAL_TRADES:
        failed.append("summary.total_trades_sum")

    # 4e. summary.splits_requested (required; negative values → REJECTED)
    checked.append("summary.splits_requested")
    splits_requested = _int_or_none(summary.get("splits_requested") if isinstance(summary, dict) else None)
    if splits_requested is None or splits_requested < 0:
        failed.append("summary.splits_requested")

    # 4f. summary.splits_blocked (required; negative values → REJECTED)
    checked.append("summary.splits_blocked")
    splits_blocked_count = _int_or_none(summary.get("splits_blocked") if isinstance(summary, dict) else None)
    if splits_blocked_count is None or splits_blocked_count < 0:
        failed.append("summary.splits_blocked")

    # 4g. summary.average_monthly_return_mean (None → NEEDS_MORE_DATA; ≤ 0 → REJECTED)
    checked.append("summary.average_monthly_return_mean")
    avg_monthly_return = _float_or_none(summary.get("average_monthly_return_mean") if isinstance(summary, dict) else None)
    avg_return_missing = avg_monthly_return is None
    if not avg_return_missing and avg_monthly_return <= 0.0:
        # Non-null but non-positive is a hard failure → REJECTED
        failed.append("summary.average_monthly_return_mean")

    # 4h. summary.max_drawdown_worst
    checked.append("summary.max_drawdown_worst")
    max_drawdown = _float_or_none(summary.get("max_drawdown_worst") if isinstance(summary, dict) else None)
    if max_drawdown is None or max_drawdown < _MAX_DRAWDOWN_FLOOR:
        failed.append("summary.max_drawdown_worst")

    # -----------------------------------------------------------------------
    # 5. Per-split checks (session_end_frequency, low-exposure Sharpe)
    # -----------------------------------------------------------------------
    splits_raw: Any = report_dict.get("splits") if isinstance(report_dict, dict) else []
    splits_list: list[dict[str, Any]] = splits_raw if isinstance(splits_raw, list) else []

    checked.append("splits.session_end_frequency")
    session_end_rejected = False
    for split in splits_list:
        metrics: Any = split.get("metrics") if isinstance(split, dict) else {}
        if not isinstance(metrics, dict):
            continue
        sef = _float_or_none(metrics.get("session_end_frequency"))
        if sef is not None and sef > _MAX_SESSION_END_FREQUENCY:
            session_end_rejected = True
            break
    if session_end_rejected:
        failed.append("splits.session_end_frequency")

    checked.append("splits.low_exposure_sharpe")
    low_exposure_artifact = False
    for split in splits_list:
        metrics = split.get("metrics") if isinstance(split, dict) else {}
        if not isinstance(metrics, dict):
            continue
        exposure = _float_or_none(metrics.get("exposure_pct"))
        # Prefer "sharpe" (project metric key); fall back to "sharpe_ratio".
        sharpe = _float_or_none(
            metrics["sharpe"] if "sharpe" in metrics else metrics.get("sharpe_ratio")
        )
        if (
            exposure is not None
            and exposure < _LOW_EXPOSURE_THRESHOLD
            and sharpe is not None
            and abs(sharpe) >= _EXTREME_SHARPE_THRESHOLD
        ):
            low_exposure_artifact = True
            break

    # -----------------------------------------------------------------------
    # 6. Majority-blocked-splits soft failure (uses validated values from 4e/4f)
    # -----------------------------------------------------------------------
    majority_blocked = (
        splits_requested is not None
        and splits_requested > 0
        and splits_blocked_count is not None
        and (splits_blocked_count / splits_requested) > _MAX_BLOCKED_SPLIT_RATIO
    )

    # -----------------------------------------------------------------------
    # 7. Determine final status based on priority
    # -----------------------------------------------------------------------

    # Hard failures → REJECTED (all criteria in `failed` are hard failures)
    hard_failed = list(failed)
    if hard_failed:
        blocker = "failed criteria: " + ", ".join(hard_failed)
        return _blocked(
            PromotionStatus.REJECTED,
            blocker,
            candidate_id,
            run_id,
            checked,
            failed,
        )

    # Soft failures → NEEDS_MORE_DATA
    soft_failures: list[str] = []
    if avg_return_missing:
        soft_failures.append("summary.average_monthly_return_mean")
    if majority_blocked:
        soft_failures.append("splits.majority_blocked")
    if low_exposure_artifact:
        soft_failures.append("splits.low_exposure_sharpe")

    if soft_failures:
        # Include soft failure names in criteria_failed for test introspection
        all_failed = list(failed) + soft_failures
        checked_with_soft = list(checked)
        return _blocked(
            PromotionStatus.NEEDS_MORE_DATA,
            "insufficient evidence: " + ", ".join(soft_failures),
            candidate_id,
            run_id,
            checked_with_soft,
            all_failed,
        )

    # All criteria passed
    return _pass_result(candidate_id, run_id, checked)
