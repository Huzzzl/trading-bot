"""
research/paper_approval_validator.py
-------------------------------------
S25: Pure offline paper trading approval artifact validator.

Validates an already-loaded paper trading approval artifact dictionary
against the S24 schema "PTA/1.0". No file I/O, no network, no environment
variables, no broker/API calls, no credential access, no order action, no
paper/live trading approval.

Validator PASS means only that the artifact's *shape and scope* are valid --
it is not, and can never be, paper or live trading approval. Live trading
remains categorically blocked: live_trading_approved and
live_order_submission_approved must always be False, and any artifact that
sets either to True is rejected as BLOCKED_SAFETY.

Aggregate status priority (highest first):
  BLOCKED_PROVENANCE       -- schema/type/scope mismatch, missing or invalid
                              provenance/evidence references, malformed
                              timestamps, missing identity allowlists,
                              credential-like account label
  BLOCKED_SAFETY           -- fixed safety values violated, forbidden
                              field/value scan hit
  BLOCKED_RISK_LIMITS      -- any risk limit violation
  REJECTED_APPROVAL_REVIEW -- unsupported order types/session, invalid
                              approval_status value
  (declared approval_status) -- reached only when every criterion above
                              passes; result is "PASS" for the three
                              APPROVED_FOR_* design/run statuses and
                              "BLOCKED" for NOT_REVIEWED / DRAFT /
                              REJECTED_APPROVAL_REVIEW
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPPORTED_ARTIFACT_SCHEMA_VERSIONS: frozenset[str] = frozenset({"PTA/1.0"})
_REQUIRED_ARTIFACT_TYPE: str = "PAPER_TRADING_APPROVAL"
_REQUIRED_APPROVAL_SCOPE: str = "PAPER_TRADING_LIMITED_RUN_ONLY"

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)
_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

_MAX_POSITION_FRACTION_CAP: float = 0.10
_MAX_DRAWDOWN_STOP_CAP: float = 1.0
_MAX_ORDERS_PER_DAY_CAP: int = 10

_ALLOWED_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})
_ALLOWED_SESSIONS: frozenset[str] = frozenset({"regular"})

# Non-empty-string fields and the (point-11) criterion name each maps to.
_FIELD_CRITERIA: dict[str, str] = {
    "candidate_id": "provenance.candidate_id",
    "run_id": "provenance.run_id",
    "source_git_sha": "provenance.source_git_sha",
    "paper_config_schema_version": "evidence.paper_config_schema_version",
    "paper_config_hash": "evidence.paper_config_hash",
    "simulation_result_hash": "evidence.simulation_result_hash",
    "architecture_review_reference": "evidence.architecture_review_reference",
    "invariant_test_reference": "evidence.invariant_test_reference",
    "approved_by": "approval.approved_by",
    "notes": "approval.notes",
    "known_limitations": "approval.known_limitations",
}

_FIXED_SAFETY_BOOL_FIELDS: tuple[tuple[str, bool, str], ...] = (
    ("live_trading_approved", False, "safety.live_trading_approved"),
    ("live_order_submission_approved", False, "safety.live_order_submission_approved"),
    ("dry_run_required", True, "safety.dry_run_required"),
    ("human_confirmation_required", True, "safety.human_confirmation_required"),
    ("kill_switch_required", True, "safety.kill_switch_required"),
)

# Forbidden substrings for the recursive forbidden field/value scan
# (case-insensitive). These are scan-list string literals only -- the
# validator never imports, calls, or invokes any of the concepts they name.
# Note: bare "live_trading_approved"/"live_order_submission_approved" are
# deliberately NOT included -- they are legitimate field names whose values
# must be exactly False; only an embedded "...=true" phrase is forbidden.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "secret_key",
    "api_secret",
    "auth_token",
    "password",
    "credential",
    "broker_secret",
    "account_number",
    "live_account_id",
    "production_account",
    "env:",
    "os.environ",
    "submit_order",
    "place_order",
    "live_submit",
    "live_trading_approved=true",
    "live_order_submission_approved=true",
)

# Credential-like substrings for the paper account label check.
_CREDENTIAL_LABEL_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "secret",
    "token",
    "password",
    "credential",
    "auth",
)

# Matches ISO-8601-like datetime strings: date + T + time + optional
# fractional seconds + optional tz offset or Z.
_ISO8601_PATTERN: re.Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------

class PaperApprovalStatus(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    DRAFT = "DRAFT"
    APPROVED_FOR_DRY_RUN_DESIGN = "APPROVED_FOR_DRY_RUN_DESIGN"
    APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN = "APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN"
    APPROVED_FOR_LIMITED_PAPER_RUN = "APPROVED_FOR_LIMITED_PAPER_RUN"
    REJECTED_APPROVAL_REVIEW = "REJECTED_APPROVAL_REVIEW"
    BLOCKED_PROVENANCE = "BLOCKED_PROVENANCE"
    BLOCKED_RISK_LIMITS = "BLOCKED_RISK_LIMITS"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


_APPROVED_DESIGN_OR_RUN_STATUSES: frozenset[PaperApprovalStatus] = frozenset({
    PaperApprovalStatus.APPROVED_FOR_DRY_RUN_DESIGN,
    PaperApprovalStatus.APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN,
    PaperApprovalStatus.APPROVED_FOR_LIMITED_PAPER_RUN,
})


@dataclass(frozen=True)
class PaperApprovalValidationResult:
    """Immutable result of validate_paper_approval_artifact().

    result is "PASS" only when every shape/scope criterion passes AND the
    artifact's declared approval_status is one of the three
    APPROVED_FOR_* design/run statuses. result is "BLOCKED" for every
    other outcome -- including a structurally valid but not-yet-approved
    or rejected artifact (NOT_REVIEWED / DRAFT / REJECTED_APPROVAL_REVIEW).

    PASS does not approve paper or live trading -- it only confirms that
    the artifact's shape and scope match the reviewed S24 PTA/1.0 schema.
    All safety flags are always False -- the validator makes no broker,
    credential, network, order, or live trading calls.
    """
    result: str
    blocker: str | None
    candidate_id: str | None
    run_id: str | None
    status: PaperApprovalStatus
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

def _get(d: dict[str, Any], key: str) -> Any:
    if isinstance(d, dict):
        return d.get(key)
    return None


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_nonempty_str_list(v: Any) -> bool:
    return (
        isinstance(v, (list, tuple))
        and len(v) > 0
        and all(isinstance(item, str) and item.strip() for item in v)
    )


def _finite_positive(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f > 0.0
    except (TypeError, ValueError):
        return False


def _positive_int(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        i = int(v)
        return i > 0 and float(v) == i
    except (TypeError, ValueError):
        return False


def _is_iso8601_like_utc_string(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return bool(_ISO8601_PATTERN.match(value.strip()))


def _parse_iso8601(value: str) -> datetime | None:
    """Parse an ISO-8601-like string into an aware UTC datetime, or None."""
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scan_forbidden(d: Any) -> bool:
    """Recursively scan all keys and string values for forbidden substrings."""
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(k, str):
                k_lower = k.lower()
                if any(sub in k_lower for sub in _FORBIDDEN_SUBSTRINGS):
                    return True
            if _scan_forbidden(v):
                return True
    elif isinstance(d, (list, tuple)):
        for item in d:
            if _scan_forbidden(item):
                return True
    elif isinstance(d, str):
        d_lower = d.lower()
        if any(sub in d_lower for sub in _FORBIDDEN_SUBSTRINGS):
            return True
    return False


def _result(
    *,
    result: str,
    blocker: str | None,
    candidate_id: str | None,
    run_id: str | None,
    status: PaperApprovalStatus,
    checked: list[str],
    failed: tuple[str, ...],
) -> PaperApprovalValidationResult:
    return PaperApprovalValidationResult(
        result=result,
        blocker=blocker,
        candidate_id=candidate_id,
        run_id=run_id,
        status=status,
        criteria_checked=tuple(checked),
        criteria_failed=tuple(failed),
        **_RESULT_FLAGS,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_paper_approval_artifact(
    artifact_dict: dict[str, Any],
) -> PaperApprovalValidationResult:
    """Validate an already-loaded paper trading approval artifact dictionary.

    Parameters
    ----------
    artifact_dict:
        Already-loaded plain dict describing a candidate paper trading
        approval artifact. Expected artifact_schema_version "PTA/1.0".

    Returns
    -------
    PaperApprovalValidationResult
        result="PASS" only when every shape/scope criterion passes and the
        declared approval_status is APPROVED_FOR_DRY_RUN_DESIGN,
        APPROVED_FOR_PAPER_ORDER_PLAN_DESIGN, or
        APPROVED_FOR_LIMITED_PAPER_RUN.
        result="BLOCKED" for every other outcome (including a structurally
        valid but not-yet-approved or rejected artifact).
        All safety flags on the result are always False.

    Notes
    -----
    Pure function: same inputs -> same output. Performs no file I/O, no
    network access, no environment variable reads, no broker/API calls, no
    credential access, and no order action. PASS means only that the
    artifact's shape and scope match the reviewed S24 PTA/1.0 schema -- it
    is not, and can never be, paper or live trading approval. Live trading
    remains categorically blocked.
    """
    checked: list[str] = []
    failed: list[str] = []

    candidate_id: str | None = _get(artifact_dict, "candidate_id")
    if not _is_nonempty_str(candidate_id):
        candidate_id = None
    run_id: str | None = _get(artifact_dict, "run_id")
    if not _is_nonempty_str(run_id):
        run_id = None

    # -----------------------------------------------------------------------
    # 1. Schema / artifact-type / approval-scope fixed values
    # -----------------------------------------------------------------------
    checked.append("schema.artifact")
    if _get(artifact_dict, "artifact_schema_version") not in _SUPPORTED_ARTIFACT_SCHEMA_VERSIONS:
        failed.append("schema.artifact")

    checked.append("artifact.type")
    if _get(artifact_dict, "approval_artifact_type") != _REQUIRED_ARTIFACT_TYPE:
        failed.append("artifact.type")

    checked.append("artifact.scope")
    if _get(artifact_dict, "approval_scope") != _REQUIRED_APPROVAL_SCOPE:
        failed.append("artifact.scope")

    # -----------------------------------------------------------------------
    # 2. Fixed safety values
    # -----------------------------------------------------------------------
    for field, required_value, criterion in _FIXED_SAFETY_BOOL_FIELDS:
        checked.append(criterion)
        value = _get(artifact_dict, field)
        if not isinstance(value, bool) or value is not required_value:
            failed.append(criterion)

    # -----------------------------------------------------------------------
    # 3. Provenance / evidence / approval non-empty string fields
    # -----------------------------------------------------------------------
    for field, criterion in _FIELD_CRITERIA.items():
        checked.append(criterion)
        if not _is_nonempty_str(_get(artifact_dict, field)):
            failed.append(criterion)

    # -----------------------------------------------------------------------
    # 4. Datetime fields: ISO-8601-like strings, expires after approved
    # -----------------------------------------------------------------------
    checked.append("approval.approved_at_utc")
    approved_at_raw = _get(artifact_dict, "approved_at_utc")
    approved_at_ok = _is_iso8601_like_utc_string(approved_at_raw)
    approved_at_dt: datetime | None = None
    if not approved_at_ok:
        failed.append("approval.approved_at_utc")
    else:
        approved_at_dt = _parse_iso8601(approved_at_raw)
        if approved_at_dt is None:
            failed.append("approval.approved_at_utc")
            approved_at_ok = False

    checked.append("approval.expires_at_utc")
    expires_at_raw = _get(artifact_dict, "expires_at_utc")
    expires_at_ok = _is_iso8601_like_utc_string(expires_at_raw)
    expires_at_dt: datetime | None = None
    if not expires_at_ok:
        failed.append("approval.expires_at_utc")
    else:
        expires_at_dt = _parse_iso8601(expires_at_raw)
        if expires_at_dt is None:
            failed.append("approval.expires_at_utc")
            expires_at_ok = False

    if (
        approved_at_ok
        and expires_at_ok
        and approved_at_dt is not None
        and expires_at_dt is not None
        and not (expires_at_dt > approved_at_dt)
    ):
        failed.append("approval.expires_at_utc")

    # -----------------------------------------------------------------------
    # 5. Risk limit fields
    # -----------------------------------------------------------------------
    checked.append("risk.max_notional_per_position")
    if not _finite_positive(_get(artifact_dict, "max_notional_per_position")):
        failed.append("risk.max_notional_per_position")

    checked.append("risk.max_position_fraction")
    mpf = _get(artifact_dict, "max_position_fraction")
    if not _finite_positive(mpf) or float(mpf) > _MAX_POSITION_FRACTION_CAP:
        failed.append("risk.max_position_fraction")

    checked.append("risk.max_daily_loss")
    if not _finite_positive(_get(artifact_dict, "max_daily_loss")):
        failed.append("risk.max_daily_loss")

    checked.append("risk.max_drawdown_stop")
    mds = _get(artifact_dict, "max_drawdown_stop")
    if not _finite_positive(mds) or float(mds) > _MAX_DRAWDOWN_STOP_CAP:
        failed.append("risk.max_drawdown_stop")

    checked.append("risk.max_orders_per_day")
    mod = _get(artifact_dict, "max_orders_per_day")
    if not _positive_int(mod) or int(mod) > _MAX_ORDERS_PER_DAY_CAP:
        failed.append("risk.max_orders_per_day")

    # -----------------------------------------------------------------------
    # 6. Allowlist fields
    # -----------------------------------------------------------------------
    checked.append("allowlist.allowed_symbols")
    if not _is_nonempty_str_list(_get(artifact_dict, "allowed_symbols")):
        failed.append("allowlist.allowed_symbols")

    checked.append("allowlist.allowed_intervals")
    if not _is_nonempty_str_list(_get(artifact_dict, "allowed_intervals")):
        failed.append("allowlist.allowed_intervals")

    checked.append("allowlist.allowed_strategy_families")
    if not _is_nonempty_str_list(_get(artifact_dict, "allowed_strategy_families")):
        failed.append("allowlist.allowed_strategy_families")

    checked.append("allowlist.allowed_order_types")
    aot = _get(artifact_dict, "allowed_order_types")
    if not _is_nonempty_str_list(aot) or not all(t in _ALLOWED_ORDER_TYPES for t in aot):
        failed.append("allowlist.allowed_order_types")

    checked.append("allowlist.allowed_session")
    if _get(artifact_dict, "allowed_session") not in _ALLOWED_SESSIONS:
        failed.append("allowlist.allowed_session")

    # -----------------------------------------------------------------------
    # 7. Paper account label -- a label only, never credential-like
    # -----------------------------------------------------------------------
    checked.append("account.paper_account_label")
    label = _get(artifact_dict, "paper_account_label")
    if not _is_nonempty_str(label):
        failed.append("account.paper_account_label")
    else:
        label_lower = label.lower()
        if any(sub in label_lower for sub in _CREDENTIAL_LABEL_SUBSTRINGS):
            failed.append("account.paper_account_label")

    # -----------------------------------------------------------------------
    # 8. Approval status -- must be a known PaperApprovalStatus value
    # -----------------------------------------------------------------------
    checked.append("approval.status")
    status_raw = _get(artifact_dict, "approval_status")
    declared_status: PaperApprovalStatus | None = None
    try:
        declared_status = PaperApprovalStatus(status_raw)
    except (ValueError, KeyError, TypeError):
        failed.append("approval.status")

    # -----------------------------------------------------------------------
    # 9. Forbidden field/value scan
    # -----------------------------------------------------------------------
    checked.append("forbidden.scan")
    if _scan_forbidden(artifact_dict):
        failed.append("forbidden.scan")

    # -----------------------------------------------------------------------
    # 10. Classify by highest-priority failing bucket (fail-closed order)
    # -----------------------------------------------------------------------
    provenance_criteria = {
        "schema.artifact",
        "artifact.type",
        "artifact.scope",
        "provenance.candidate_id",
        "provenance.run_id",
        "provenance.source_git_sha",
        "evidence.paper_config_schema_version",
        "evidence.paper_config_hash",
        "evidence.simulation_result_hash",
        "evidence.architecture_review_reference",
        "evidence.invariant_test_reference",
        "approval.approved_by",
        "approval.approved_at_utc",
        "approval.expires_at_utc",
        "approval.notes",
        "approval.known_limitations",
        "allowlist.allowed_symbols",
        "allowlist.allowed_intervals",
        "allowlist.allowed_strategy_families",
        "account.paper_account_label",
    }
    safety_criteria = {
        "safety.live_trading_approved",
        "safety.live_order_submission_approved",
        "safety.dry_run_required",
        "safety.human_confirmation_required",
        "safety.kill_switch_required",
        "forbidden.scan",
    }
    risk_criteria = {
        "risk.max_notional_per_position",
        "risk.max_position_fraction",
        "risk.max_daily_loss",
        "risk.max_drawdown_stop",
        "risk.max_orders_per_day",
    }
    rejected_criteria = {
        "allowlist.allowed_order_types",
        "allowlist.allowed_session",
        "approval.status",
    }

    failed_tuple = tuple(failed)
    failed_set = set(failed)

    if failed_set & provenance_criteria:
        return _result(
            result="BLOCKED",
            blocker="provenance/identity check failed: " + ", ".join(
                c for c in failed if c in provenance_criteria
            ),
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperApprovalStatus.BLOCKED_PROVENANCE,
            checked=checked,
            failed=failed_tuple,
        )

    if failed_set & safety_criteria:
        return _result(
            result="BLOCKED",
            blocker="safety invariant violated: " + ", ".join(
                c for c in failed if c in safety_criteria
            ),
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperApprovalStatus.BLOCKED_SAFETY,
            checked=checked,
            failed=failed_tuple,
        )

    if failed_set & risk_criteria:
        return _result(
            result="BLOCKED",
            blocker="risk limit violation: " + ", ".join(
                c for c in failed if c in risk_criteria
            ),
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperApprovalStatus.BLOCKED_RISK_LIMITS,
            checked=checked,
            failed=failed_tuple,
        )

    if failed_set & rejected_criteria:
        return _result(
            result="BLOCKED",
            blocker="approval review failed: " + ", ".join(
                c for c in failed if c in rejected_criteria
            ),
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperApprovalStatus.REJECTED_APPROVAL_REVIEW,
            checked=checked,
            failed=failed_tuple,
        )

    # -----------------------------------------------------------------------
    # 11. Every shape/scope criterion passed -- classify by declared status.
    #     PASS only for the three APPROVED_FOR_* design/run statuses; every
    #     other declared status (NOT_REVIEWED / DRAFT /
    #     REJECTED_APPROVAL_REVIEW) is a structurally valid shape that has
    #     not (or will never be) approved -- fail closed to BLOCKED.
    # -----------------------------------------------------------------------
    assert declared_status is not None  # guaranteed: approval.status passed

    if declared_status in _APPROVED_DESIGN_OR_RUN_STATUSES:
        return _result(
            result="PASS",
            blocker=None,
            candidate_id=candidate_id,
            run_id=run_id,
            status=declared_status,
            checked=checked,
            failed=(),
        )

    return _result(
        result="BLOCKED",
        blocker=f"artifact not approved: approval_status={declared_status.value}",
        candidate_id=candidate_id,
        run_id=run_id,
        status=declared_status,
        checked=checked,
        failed=(),
    )
