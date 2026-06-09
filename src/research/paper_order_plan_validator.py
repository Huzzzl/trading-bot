"""
research/paper_order_plan_validator.py
--------------------------------------
S27: Pure offline paper order plan validator.

Validates an already-loaded paper order plan dictionary against the S26
schema "POP/1.0". No file I/O, no network, no environment variables, no
broker/API calls, no credential access, no order action, no paper/live
trading approval.

A paper order plan is not an order. Validator PASS means only that the
plan's shape and scope are valid for future safety-gate review -- it is
not, and can never be, order submission approval or paper/live trading
approval. Live trading remains categorically blocked: live_trading_allowed
must always be False, and any plan that sets it to True is rejected as
PLAN_BLOCKED_SAFETY.

Aggregate status priority (highest first):
  PLAN_BLOCKED_PROVENANCE  -- schema/type/scope mismatch, missing provenance
                              or evidence references, malformed timestamps,
                              missing strategy identity or narrative fields
  PLAN_BLOCKED_SAFETY      -- fixed safety values violated, forbidden
                              field/value scan hit
  PLAN_BLOCKED_RISK        -- quantity/notional/limit_price violation, risk
                              snapshot fields out of bounds
  PLAN_REJECTED_SCHEMA     -- unsupported side/order_type/time_in_force/
                              session, invalid plan_schema_version or
                              plan_type, invalid plan_status value
  (declared plan_status)   -- reached only when every criterion above
                              passes; result is "PASS" for
                              PLAN_READY_FOR_SAFETY_GATE and "BLOCKED" for
                              all other statuses (NOT_PLANNED, PLAN_DRAFT,
                              PLAN_REJECTED_SCHEMA, PLAN_BLOCKED_*,
                              PLAN_EXPIRED)
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

_SUPPORTED_PLAN_SCHEMA_VERSIONS: frozenset[str] = frozenset({"POP/1.0"})
_REQUIRED_PLAN_TYPE: str = "PAPER_ORDER_PLAN"
_REQUIRED_APPROVAL_SCOPE: str = "PAPER_TRADING_LIMITED_RUN_ONLY"

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)
_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

_ALLOWED_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
_ALLOWED_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})
_ALLOWED_TIME_IN_FORCE: frozenset[str] = frozenset({"day"})
_ALLOWED_SESSIONS: frozenset[str] = frozenset({"regular"})

_MAX_POSITION_FRACTION_CAP: float = 0.10
_MAX_DRAWDOWN_STOP_CAP: float = 1.0
_MAX_ORDERS_PER_DAY_CAP: int = 10

# Fixed boolean fields (field, required_value, criterion).
_FIXED_BOOL_FIELDS: tuple[tuple[str, bool, str], ...] = (
    ("dry_run_required",              True,  "safety.dry_run_required"),
    ("human_confirmation_required",   True,  "safety.human_confirmation_required"),
    ("kill_switch_required",          True,  "safety.kill_switch_required"),
    ("safety_gate_required",          True,  "safety.safety_gate_required"),
    ("broker_calls_made",             False, "safety.broker_calls_made"),
    ("credentials_read",              False, "safety.credentials_read"),
    ("network_calls_made",            False, "safety.network_calls_made"),
    ("order_action_requested",        False, "safety.order_action_requested"),
    ("live_trading_allowed",          False, "safety.live_trading_allowed"),
)

# Non-empty-string provenance/evidence fields and their criterion names.
_PROVENANCE_FIELDS: dict[str, str] = {
    "candidate_id":           "provenance.candidate_id",
    "run_id":                 "provenance.run_id",
    "source_git_sha":         "provenance.source_git_sha",
    "approval_artifact_hash": "evidence.approval_artifact_hash",
    "paper_config_hash":      "evidence.paper_config_hash",
    "simulation_result_hash": "evidence.simulation_result_hash",
}

# Non-empty-string strategy identity fields.
_STRATEGY_FIELDS: dict[str, str] = {
    "symbol":          "strategy.symbol",
    "interval":        "strategy.interval",
    "strategy_family": "strategy.strategy_family",
    "holding_horizon": "strategy.holding_horizon",
}

# Required risk-snapshot keys (finite positive) and caps where applicable.
# (field, criterion, cap_or_None, is_int)
_RISK_SNAPSHOT_CHECKS: tuple[tuple[str, str, float | None, bool], ...] = (
    ("max_position_fraction", "risk.max_position_fraction", _MAX_POSITION_FRACTION_CAP, False),
    ("max_daily_loss",        "risk.max_daily_loss",        None,                       False),
    ("max_drawdown_stop",     "risk.max_drawdown_stop",     _MAX_DRAWDOWN_STOP_CAP,     False),
    ("max_orders_per_day",    "risk.max_orders_per_day",    float(_MAX_ORDERS_PER_DAY_CAP), True),
)

# Forbidden substrings for the recursive forbidden field/value scan
# (case-insensitive). These are scan-list string literals only -- the
# validator never imports, calls, or invokes any of the concepts they name.
# Note: bare safety-flag names (broker_calls_made, credentials_read,
# network_calls_made, order_action_requested, live_trading_allowed) are
# deliberately NOT included -- they are legitimate keys in this plan schema
# whose values must be exactly False.
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
    "broker_account_id",
    "env:",
    "os.environ",
    "submit_order",
    "place_order",
    "live_submit",
    "paper_trading_approved",
    "live_trading_approved",
    "approved_for_live_trading",
)

# Safety-flag keys that are legitimate plan fields when their value is
# exactly False. "credentials_read" contains the forbidden substring
# "credential" as part of its name, so these must be skipped during the
# key-name portion of the forbidden scan when value is exactly False.
# A value of True is blocked by the fixed-boolean check before the scan runs.
_SAFE_FALSE_KEYS: frozenset[str] = frozenset({
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
})

_ISO8601_PATTERN: re.Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------

class PaperOrderPlanStatus(str, Enum):
    NOT_PLANNED               = "NOT_PLANNED"
    PLAN_DRAFT                = "PLAN_DRAFT"
    PLAN_READY_FOR_SAFETY_GATE = "PLAN_READY_FOR_SAFETY_GATE"
    PLAN_REJECTED_SCHEMA      = "PLAN_REJECTED_SCHEMA"
    PLAN_BLOCKED_PROVENANCE   = "PLAN_BLOCKED_PROVENANCE"
    PLAN_BLOCKED_RISK         = "PLAN_BLOCKED_RISK"
    PLAN_BLOCKED_SAFETY       = "PLAN_BLOCKED_SAFETY"
    PLAN_EXPIRED              = "PLAN_EXPIRED"


_PASS_STATUS: frozenset[PaperOrderPlanStatus] = frozenset({
    PaperOrderPlanStatus.PLAN_READY_FOR_SAFETY_GATE,
})


@dataclass(frozen=True)
class PaperOrderPlanValidationResult:
    """Immutable result of validate_paper_order_plan().

    result is "PASS" only when every shape/scope criterion passes AND the
    plan's declared plan_status is PLAN_READY_FOR_SAFETY_GATE. result is
    "BLOCKED" for every other outcome.

    PASS does not approve paper or live trading, and does not constitute
    order submission -- it only confirms the plan's shape matches the
    reviewed S26 POP/1.0 schema and is eligible for future safety-gate
    review. All safety flags are always False.
    """
    result: str
    blocker: str | None
    plan_id: str | None
    candidate_id: str | None
    run_id: str | None
    status: PaperOrderPlanStatus
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


def _is_nonempty_dict(v: Any) -> bool:
    return isinstance(v, dict) and len(v) > 0


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
            # Exempt the five safety-flag keys when their value is exactly
            # False -- "credentials_read" contains "credential" as a
            # substring, but it is a legitimate plan field in this context.
            if isinstance(k, str) and k in _SAFE_FALSE_KEYS and v is False:
                continue
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
    plan_id: str | None,
    candidate_id: str | None,
    run_id: str | None,
    status: PaperOrderPlanStatus,
    checked: list[str],
    failed: tuple[str, ...],
) -> PaperOrderPlanValidationResult:
    return PaperOrderPlanValidationResult(
        result=result,
        blocker=blocker,
        plan_id=plan_id,
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

def validate_paper_order_plan(
    plan_dict: dict[str, Any],
) -> PaperOrderPlanValidationResult:
    """Validate an already-loaded paper order plan dictionary.

    Parameters
    ----------
    plan_dict:
        Already-loaded plain dict describing a candidate paper order plan.
        Expected plan_schema_version "POP/1.0".

    Returns
    -------
    PaperOrderPlanValidationResult
        result="PASS" only when every shape/scope criterion passes and the
        declared plan_status is PLAN_READY_FOR_SAFETY_GATE.
        result="BLOCKED" for every other outcome.
        All safety flags on the result are always False.

    Notes
    -----
    Pure function: same inputs -> same output. Performs no file I/O, no
    network access, no environment variable reads, no broker/API calls, no
    credential access, and no order action. PASS means only that the plan's
    shape matches the reviewed S26 POP/1.0 schema and is eligible for future
    safety-gate review -- it is not, and can never be, order submission
    approval or paper/live trading approval. Live trading remains blocked.
    """
    checked: list[str] = []
    failed: list[str] = []

    plan_id: str | None = _get(plan_dict, "plan_id")
    if not _is_nonempty_str(plan_id):
        plan_id = None
    candidate_id: str | None = _get(plan_dict, "candidate_id")
    if not _is_nonempty_str(candidate_id):
        candidate_id = None
    run_id: str | None = _get(plan_dict, "run_id")
    if not _is_nonempty_str(run_id):
        run_id = None

    # -----------------------------------------------------------------------
    # 1. Schema version and plan type
    # -----------------------------------------------------------------------
    checked.append("schema.plan")
    if _get(plan_dict, "plan_schema_version") not in _SUPPORTED_PLAN_SCHEMA_VERSIONS:
        failed.append("schema.plan")

    checked.append("plan.type")
    if _get(plan_dict, "plan_type") != _REQUIRED_PLAN_TYPE:
        failed.append("plan.type")

    # -----------------------------------------------------------------------
    # 2. plan_id (provenance bucket)
    # -----------------------------------------------------------------------
    checked.append("plan.plan_id")
    if not _is_nonempty_str(_get(plan_dict, "plan_id")):
        failed.append("plan.plan_id")

    # -----------------------------------------------------------------------
    # 3. Approval scope (provenance bucket)
    # -----------------------------------------------------------------------
    checked.append("approval.scope")
    if _get(plan_dict, "approval_scope") != _REQUIRED_APPROVAL_SCOPE:
        failed.append("approval.scope")

    # -----------------------------------------------------------------------
    # 4. Fixed boolean safety fields
    # -----------------------------------------------------------------------
    for field, required_value, criterion in _FIXED_BOOL_FIELDS:
        checked.append(criterion)
        value = _get(plan_dict, field)
        if not isinstance(value, bool) or value is not required_value:
            failed.append(criterion)

    # -----------------------------------------------------------------------
    # 5. Provenance / evidence non-empty string fields
    # -----------------------------------------------------------------------
    for field, criterion in _PROVENANCE_FIELDS.items():
        checked.append(criterion)
        if not _is_nonempty_str(_get(plan_dict, field)):
            failed.append(criterion)

    # -----------------------------------------------------------------------
    # 6. Datetime fields: ISO-8601-like, expires_at after generated_at
    # -----------------------------------------------------------------------
    checked.append("time.generated_at_utc")
    gen_raw = _get(plan_dict, "generated_at_utc")
    gen_ok = _is_iso8601_like_utc_string(gen_raw)
    gen_dt: datetime | None = None
    if not gen_ok:
        failed.append("time.generated_at_utc")
    else:
        gen_dt = _parse_iso8601(gen_raw)
        if gen_dt is None:
            failed.append("time.generated_at_utc")
            gen_ok = False

    checked.append("time.expires_at_utc")
    exp_raw = _get(plan_dict, "expires_at_utc")
    exp_ok = _is_iso8601_like_utc_string(exp_raw)
    exp_dt: datetime | None = None
    if not exp_ok:
        failed.append("time.expires_at_utc")
    else:
        exp_dt = _parse_iso8601(exp_raw)
        if exp_dt is None:
            failed.append("time.expires_at_utc")
            exp_ok = False

    if (
        gen_ok and exp_ok
        and gen_dt is not None and exp_dt is not None
        and not (exp_dt > gen_dt)
    ):
        failed.append("time.expires_at_utc")

    # -----------------------------------------------------------------------
    # 7. Strategy identity fields (provenance bucket)
    # -----------------------------------------------------------------------
    for field, criterion in _STRATEGY_FIELDS.items():
        checked.append(criterion)
        if not _is_nonempty_str(_get(plan_dict, field)):
            failed.append(criterion)

    # -----------------------------------------------------------------------
    # 8. Order intent fields
    #    side/order_type/time_in_force/allowed_session -> rejected_schema
    #    quantity/notional/limit_price -> risk
    # -----------------------------------------------------------------------
    checked.append("order.side")
    side_val = _get(plan_dict, "side")
    if side_val not in _ALLOWED_SIDES:
        failed.append("order.side")

    checked.append("order.order_type")
    order_type_val = _get(plan_dict, "order_type")
    if order_type_val not in _ALLOWED_ORDER_TYPES:
        failed.append("order.order_type")

    checked.append("order.quantity")
    if not _finite_positive(_get(plan_dict, "quantity")):
        failed.append("order.quantity")

    checked.append("order.notional")
    notional_val = _get(plan_dict, "notional")
    if not _finite_positive(notional_val):
        failed.append("order.notional")

    checked.append("order.limit_price")
    limit_price_val = _get(plan_dict, "limit_price")
    if order_type_val == "limit":
        if not _finite_positive(limit_price_val):
            failed.append("order.limit_price")
    elif order_type_val == "market":
        if limit_price_val is not None:
            failed.append("order.limit_price")
    # If order_type is already invalid, do not double-fail limit_price.

    checked.append("order.time_in_force")
    if _get(plan_dict, "time_in_force") not in _ALLOWED_TIME_IN_FORCE:
        failed.append("order.time_in_force")

    checked.append("order.allowed_session")
    if _get(plan_dict, "allowed_session") not in _ALLOWED_SESSIONS:
        failed.append("order.allowed_session")

    # -----------------------------------------------------------------------
    # 9. Narrative / snapshot fields (provenance bucket)
    # -----------------------------------------------------------------------
    checked.append("order.rationale")
    if not _is_nonempty_str(_get(plan_dict, "rationale")):
        failed.append("order.rationale")

    checked.append("order.signal_snapshot")
    if not _is_nonempty_dict(_get(plan_dict, "signal_snapshot")):
        failed.append("order.signal_snapshot")

    checked.append("risk.snapshot")
    risk_snap = _get(plan_dict, "risk_snapshot")
    if not _is_nonempty_dict(risk_snap):
        failed.append("risk.snapshot")
    risk_snap_dict: dict[str, Any] = risk_snap if isinstance(risk_snap, dict) else {}

    # -----------------------------------------------------------------------
    # 10. Risk snapshot field checks
    # -----------------------------------------------------------------------
    for snap_field, criterion, cap, is_int in _RISK_SNAPSHOT_CHECKS:
        checked.append(criterion)
        v = risk_snap_dict.get(snap_field)
        if is_int:
            if not _positive_int(v) or int(v) > int(cap):  # type: ignore[arg-type]
                failed.append(criterion)
        else:
            if not _finite_positive(v):
                failed.append(criterion)
            elif cap is not None and float(v) > cap:
                failed.append(criterion)

    # Optional: notional <= max_notional_per_position (if key present in snapshot)
    checked.append("risk.max_notional_per_position")
    mnpp = risk_snap_dict.get("max_notional_per_position")
    if mnpp is not None:
        if not _finite_positive(notional_val) or not _finite_positive(mnpp):
            failed.append("risk.max_notional_per_position")
        elif float(notional_val) > float(mnpp):
            failed.append("risk.max_notional_per_position")

    # -----------------------------------------------------------------------
    # 11. Plan status
    # -----------------------------------------------------------------------
    checked.append("plan.status")
    status_raw = _get(plan_dict, "plan_status")
    declared_status: PaperOrderPlanStatus | None = None
    try:
        declared_status = PaperOrderPlanStatus(status_raw)
    except (ValueError, KeyError, TypeError):
        failed.append("plan.status")

    # -----------------------------------------------------------------------
    # 12. Notes field (provenance bucket)
    # -----------------------------------------------------------------------
    checked.append("plan.notes")
    if not _is_nonempty_str(_get(plan_dict, "notes")):
        failed.append("plan.notes")

    # -----------------------------------------------------------------------
    # 13. Forbidden field/value scan (safety bucket)
    # -----------------------------------------------------------------------
    checked.append("forbidden.scan")
    if _scan_forbidden(plan_dict):
        failed.append("forbidden.scan")

    # -----------------------------------------------------------------------
    # 14. Classify by highest-priority failing bucket (fail-closed order)
    # -----------------------------------------------------------------------
    provenance_criteria = {
        "plan.plan_id",
        "approval.scope",
        "provenance.candidate_id",
        "provenance.run_id",
        "provenance.source_git_sha",
        "evidence.approval_artifact_hash",
        "evidence.paper_config_hash",
        "evidence.simulation_result_hash",
        "time.generated_at_utc",
        "time.expires_at_utc",
        "strategy.symbol",
        "strategy.interval",
        "strategy.strategy_family",
        "strategy.holding_horizon",
        "order.rationale",
        "order.signal_snapshot",
        "risk.snapshot",
        "plan.notes",
    }
    safety_criteria = {
        "safety.dry_run_required",
        "safety.human_confirmation_required",
        "safety.kill_switch_required",
        "safety.safety_gate_required",
        "safety.broker_calls_made",
        "safety.credentials_read",
        "safety.network_calls_made",
        "safety.order_action_requested",
        "safety.live_trading_allowed",
        "forbidden.scan",
    }
    risk_criteria = {
        "order.quantity",
        "order.notional",
        "order.limit_price",
        "risk.max_position_fraction",
        "risk.max_daily_loss",
        "risk.max_drawdown_stop",
        "risk.max_orders_per_day",
        "risk.max_notional_per_position",
    }
    rejected_schema_criteria = {
        "schema.plan",
        "plan.type",
        "order.side",
        "order.order_type",
        "order.time_in_force",
        "order.allowed_session",
        "plan.status",
    }

    failed_tuple = tuple(failed)
    failed_set = set(failed)

    if failed_set & provenance_criteria:
        return _result(
            result="BLOCKED",
            blocker="provenance/identity check failed: " + ", ".join(
                c for c in failed if c in provenance_criteria
            ),
            plan_id=plan_id,
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperOrderPlanStatus.PLAN_BLOCKED_PROVENANCE,
            checked=checked,
            failed=failed_tuple,
        )

    if failed_set & safety_criteria:
        return _result(
            result="BLOCKED",
            blocker="safety invariant violated: " + ", ".join(
                c for c in failed if c in safety_criteria
            ),
            plan_id=plan_id,
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperOrderPlanStatus.PLAN_BLOCKED_SAFETY,
            checked=checked,
            failed=failed_tuple,
        )

    if failed_set & risk_criteria:
        return _result(
            result="BLOCKED",
            blocker="risk/order constraint violated: " + ", ".join(
                c for c in failed if c in risk_criteria
            ),
            plan_id=plan_id,
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperOrderPlanStatus.PLAN_BLOCKED_RISK,
            checked=checked,
            failed=failed_tuple,
        )

    if failed_set & rejected_schema_criteria:
        return _result(
            result="BLOCKED",
            blocker="schema/structure rejected: " + ", ".join(
                c for c in failed if c in rejected_schema_criteria
            ),
            plan_id=plan_id,
            candidate_id=candidate_id,
            run_id=run_id,
            status=PaperOrderPlanStatus.PLAN_REJECTED_SCHEMA,
            checked=checked,
            failed=failed_tuple,
        )

    # -----------------------------------------------------------------------
    # 15. Every shape/scope criterion passed -- classify by declared status.
    #     PASS only for PLAN_READY_FOR_SAFETY_GATE; every other declared
    #     status is a structurally valid shape that is not ready for the
    #     safety gate -- fail closed to BLOCKED.
    # -----------------------------------------------------------------------
    assert declared_status is not None  # guaranteed: plan.status passed

    if declared_status in _PASS_STATUS:
        return _result(
            result="PASS",
            blocker=None,
            plan_id=plan_id,
            candidate_id=candidate_id,
            run_id=run_id,
            status=declared_status,
            checked=checked,
            failed=(),
        )

    return _result(
        result="BLOCKED",
        blocker=f"plan not ready: plan_status={declared_status.value}",
        plan_id=plan_id,
        candidate_id=candidate_id,
        run_id=run_id,
        status=declared_status,
        checked=checked,
        failed=(),
    )
