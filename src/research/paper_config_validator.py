"""
research/paper_config_validator.py
-------------------------------------
S17: Pure offline paper trading configuration validator.

Validates an already-loaded paper config dictionary against the S16 schema
"PC/1.0". No file I/O, no network, no environment variables, no broker/API
calls, no credential access, no order action, no paper/live trading approval.

APPROVED_FOR_PAPER_SIMULATION_DESIGN is a config classification only; it does
not approve paper or live trading.

Aggregate status priority (highest first):
  BLOCKED_PROVENANCE  — schema mismatch, missing provenance, credential scan
  BLOCKED_RISK_LIMITS — any risk limit violation
  REJECTED_CONFIG_REVIEW — strategy identity, execution, or review field issues
  APPROVED_FOR_PAPER_SIMULATION_DESIGN / READY_FOR_PAPER_CONFIG_REVIEW / DRAFT
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"PC/1.0"})

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

# Risk limit caps
_MAX_POSITION_FRACTION_CAP: float = 0.10
_MAX_ORDERS_PER_DAY_CAP: int = 10

# Execution allowlists
_ALLOWED_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})
_ALLOWED_SESSIONS: frozenset[str] = frozenset({"regular"})

# Forbidden substrings for credential/safety scan (case-insensitive)
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "secret_key",
    "api_secret",
    "auth_token",
    "password",
    "credential",
    "live_account_id",
    "production_account",
    "submit_order",
    "place_order",
    "live_trading",
    "env:",
    "os.environ",
)

# Credential-like substrings for account label check (case-insensitive)
_CREDENTIAL_LABEL_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "secret",
    "token",
    "password",
    "credential",
    "auth",
)


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------

class PaperConfigStatus(str, Enum):
    DRAFT = "DRAFT"
    READY_FOR_PAPER_CONFIG_REVIEW = "READY_FOR_PAPER_CONFIG_REVIEW"
    APPROVED_FOR_PAPER_SIMULATION_DESIGN = "APPROVED_FOR_PAPER_SIMULATION_DESIGN"
    REJECTED_CONFIG_REVIEW = "REJECTED_CONFIG_REVIEW"
    BLOCKED_RISK_LIMITS = "BLOCKED_RISK_LIMITS"
    BLOCKED_PROVENANCE = "BLOCKED_PROVENANCE"


_PASSING_STATUSES: frozenset[PaperConfigStatus] = frozenset({
    PaperConfigStatus.DRAFT,
    PaperConfigStatus.READY_FOR_PAPER_CONFIG_REVIEW,
    PaperConfigStatus.APPROVED_FOR_PAPER_SIMULATION_DESIGN,
})


@dataclass(frozen=True)
class PaperConfigValidationResult:
    """Immutable result of validate_paper_config().

    result is "PASS" when all validation criteria pass and approval_status
    is a non-blocking status.  result is "BLOCKED" for any validation failure.
    All safety flags are always False — the validator makes no broker,
    credential, network, order, or live trading calls.
    APPROVED_FOR_PAPER_SIMULATION_DESIGN is a config classification only;
    it does not approve paper or live trading.
    """
    result: str
    blocker: str | None
    candidate_id: str | None
    run_id: str | None
    status: PaperConfigStatus
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


def _finite_positive(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f > 0.0
    except (TypeError, ValueError):
        return False


def _finite_nonneg(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f >= 0.0
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


def _scan_forbidden(d: Any) -> bool:
    """Recursively scan all keys and string values for forbidden substrings."""
    if isinstance(d, dict):
        for k, v in d.items():
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


def _blocked(
    status: PaperConfigStatus,
    blocker: str,
    candidate_id: str | None,
    run_id: str | None,
    checked: list[str],
    failed: list[str],
) -> PaperConfigValidationResult:
    return PaperConfigValidationResult(
        result="BLOCKED",
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

def validate_paper_config(config_dict: dict[str, Any]) -> PaperConfigValidationResult:
    """Validate an already-loaded paper config dictionary.

    Parameters
    ----------
    config_dict:
        Already-loaded plain dict from a paper config artifact.
        Expected schema_version "PC/1.0".

    Returns
    -------
    PaperConfigValidationResult
        result="PASS" when all criteria pass and approval_status is valid.
        result="BLOCKED" for any validation failure.
        All safety flags on the result are always False.

    Notes
    -----
    Pure function: same inputs -> same output.
    No file I/O, no network, no environment variables, no broker/API calls,
    no credential access, no order action, no paper/live trading approval.
    APPROVED_FOR_PAPER_SIMULATION_DESIGN is a config classification only.
    """
    checked: list[str] = []
    failed: list[str] = []

    # Extract identity for error messages early
    candidate_id: str | None = _get(config_dict, "candidate_id")
    if not _is_nonempty_str(candidate_id):
        candidate_id = None
    run_id: str | None = _get(config_dict, "run_id")
    if not _is_nonempty_str(run_id):
        run_id = None

    # -----------------------------------------------------------------------
    # 1. Schema version (BLOCKED_PROVENANCE — highest priority)
    # -----------------------------------------------------------------------
    checked.append("schema.config")
    schema_version = _get(config_dict, "config_schema_version")
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        failed.append("schema.config")
        return _blocked(
            PaperConfigStatus.BLOCKED_PROVENANCE,
            f"unsupported config_schema_version: {schema_version!r}",
            candidate_id,
            run_id,
            checked,
            failed,
        )

    # -----------------------------------------------------------------------
    # 2. Provenance fields
    # -----------------------------------------------------------------------
    checked.append("provenance.candidate_id")
    if not _is_nonempty_str(_get(config_dict, "candidate_id")):
        failed.append("provenance.candidate_id")

    checked.append("provenance.run_id")
    if not _is_nonempty_str(_get(config_dict, "run_id")):
        failed.append("provenance.run_id")

    checked.append("provenance.source_git_sha")
    if not _is_nonempty_str(_get(config_dict, "source_git_sha")):
        failed.append("provenance.source_git_sha")

    # -----------------------------------------------------------------------
    # 3. Strategy identity fields
    # -----------------------------------------------------------------------
    for field in ("symbol", "interval", "strategy_family", "holding_horizon"):
        criterion = f"strategy.{field}"
        checked.append(criterion)
        if not _is_nonempty_str(_get(config_dict, field)):
            failed.append(criterion)

    # -----------------------------------------------------------------------
    # 4. Paper account label
    # -----------------------------------------------------------------------
    checked.append("account.paper_account_label")
    label = _get(config_dict, "paper_account_label")
    if not _is_nonempty_str(label):
        failed.append("account.paper_account_label")
    else:
        label_lower = label.lower()
        if any(sub in label_lower for sub in _CREDENTIAL_LABEL_SUBSTRINGS):
            failed.append("account.paper_account_label")

    # -----------------------------------------------------------------------
    # 5. Risk limit fields
    # -----------------------------------------------------------------------
    # 5a. max_notional_per_position: finite positive
    checked.append("risk.max_notional_per_position")
    if not _finite_positive(_get(config_dict, "max_notional_per_position")):
        failed.append("risk.max_notional_per_position")

    # 5b. max_position_fraction: finite positive <= 0.10
    checked.append("risk.max_position_fraction")
    mpf = _get(config_dict, "max_position_fraction")
    if not _finite_positive(mpf) or float(mpf) > _MAX_POSITION_FRACTION_CAP:
        failed.append("risk.max_position_fraction")

    # 5c. max_daily_loss: finite positive
    checked.append("risk.max_daily_loss")
    if not _finite_positive(_get(config_dict, "max_daily_loss")):
        failed.append("risk.max_daily_loss")

    # 5d. max_drawdown_stop: finite positive <= 1.0
    checked.append("risk.max_drawdown_stop")
    mds = _get(config_dict, "max_drawdown_stop")
    if not _finite_positive(mds) or float(mds) > 1.0:
        failed.append("risk.max_drawdown_stop")

    # 5e. max_orders_per_day: positive integer <= 10
    checked.append("risk.max_orders_per_day")
    mod = _get(config_dict, "max_orders_per_day")
    if not _positive_int(mod) or int(mod) > _MAX_ORDERS_PER_DAY_CAP:
        failed.append("risk.max_orders_per_day")

    # 5f. min_cash_buffer: finite positive < 1.0
    checked.append("risk.min_cash_buffer")
    mcb = _get(config_dict, "min_cash_buffer")
    if not _finite_positive(mcb) or float(mcb) >= 1.0:
        failed.append("risk.min_cash_buffer")

    # -----------------------------------------------------------------------
    # 6. Execution assumption fields
    # -----------------------------------------------------------------------
    # 6a. allowed_order_types: non-empty list, all values in allowlist
    checked.append("execution.allowed_order_types")
    aot = _get(config_dict, "allowed_order_types")
    if (
        not isinstance(aot, list)
        or len(aot) == 0
        or not all(isinstance(t, str) and t in _ALLOWED_ORDER_TYPES for t in aot)
    ):
        failed.append("execution.allowed_order_types")

    # 6b. allowed_session: must be "regular"
    checked.append("execution.allowed_session")
    if _get(config_dict, "allowed_session") not in _ALLOWED_SESSIONS:
        failed.append("execution.allowed_session")

    # 6c. slippage_bps_assumption: finite >= 0
    checked.append("execution.slippage_bps_assumption")
    if not _finite_nonneg(_get(config_dict, "slippage_bps_assumption")):
        failed.append("execution.slippage_bps_assumption")

    # 6d. commission_bps_assumption: finite >= 0
    checked.append("execution.commission_bps_assumption")
    if not _finite_nonneg(_get(config_dict, "commission_bps_assumption")):
        failed.append("execution.commission_bps_assumption")

    # -----------------------------------------------------------------------
    # 7. Review fields
    # -----------------------------------------------------------------------
    checked.append("review.risk_review_notes")
    if not _is_nonempty_str(_get(config_dict, "risk_review_notes")):
        failed.append("review.risk_review_notes")

    checked.append("review.reviewer_name")
    if not _is_nonempty_str(_get(config_dict, "reviewer_name")):
        failed.append("review.reviewer_name")

    checked.append("review.review_date_utc")
    if not _is_nonempty_str(_get(config_dict, "review_date_utc")):
        failed.append("review.review_date_utc")

    checked.append("review.approval_status")
    approval_status_raw = _get(config_dict, "approval_status")
    approval_status: PaperConfigStatus | None = None
    try:
        approval_status = PaperConfigStatus(approval_status_raw)
    except (ValueError, KeyError):
        failed.append("review.approval_status")

    # -----------------------------------------------------------------------
    # 8. Forbidden field/value scan
    # -----------------------------------------------------------------------
    checked.append("forbidden.scan")
    if _scan_forbidden(config_dict):
        failed.append("forbidden.scan")

    # -----------------------------------------------------------------------
    # 9. Determine final status by priority
    # -----------------------------------------------------------------------
    if not failed:
        # All criteria passed — use approval_status from config
        final_status = approval_status  # type: ignore[assignment]
        return PaperConfigValidationResult(
            result="PASS",
            blocker=None,
            candidate_id=candidate_id,
            run_id=run_id,
            status=final_status,
            criteria_checked=tuple(checked),
            criteria_failed=(),
            **_RESULT_FLAGS,
        )

    # Classify failure by highest-priority bucket
    provenance_criteria = {
        "provenance.candidate_id",
        "provenance.run_id",
        "provenance.source_git_sha",
        "account.paper_account_label",
        "review.risk_review_notes",
        "review.reviewer_name",
        "review.review_date_utc",
        "forbidden.scan",
    }
    risk_criteria = {
        "risk.max_notional_per_position",
        "risk.max_position_fraction",
        "risk.max_daily_loss",
        "risk.max_drawdown_stop",
        "risk.max_orders_per_day",
        "risk.min_cash_buffer",
    }

    failed_set = set(failed)

    if failed_set & provenance_criteria:
        blocker_status = PaperConfigStatus.BLOCKED_PROVENANCE
        blocker_msg = "provenance/credential check failed: " + ", ".join(
            c for c in failed if c in provenance_criteria
        )
    elif failed_set & risk_criteria:
        blocker_status = PaperConfigStatus.BLOCKED_RISK_LIMITS
        blocker_msg = "risk limit violation: " + ", ".join(
            c for c in failed if c in risk_criteria
        )
    else:
        blocker_status = PaperConfigStatus.REJECTED_CONFIG_REVIEW
        blocker_msg = "config review failed: " + ", ".join(failed)

    return _blocked(
        blocker_status,
        blocker_msg,
        candidate_id,
        run_id,
        checked,
        failed,
    )
