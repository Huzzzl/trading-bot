"""
research/paper_order_planner.py
--------------------------------
S30: Pure offline paper order planner.

Creates an in-memory POP/1.0 paper order plan dict from an already-loaded
PTA/1.0 approval artifact dict plus in-memory signal and sizing snapshot
dicts, then validates the generated plan with the existing S27
validate_paper_order_plan() function.

A paper order plan is not an order. This planner is NOT paper trading,
NOT broker integration, and NOT order submission of any kind. Planner PASS
(PLAN_CREATED) means only that an in-memory plan dict was created and
passed S27 plan-shape validation -- it is not, and can never be, order
approval or paper/live trading approval. Paper trading remains not
approved; live trading remains blocked; the planner fails closed: the
constructed plan is released only when every check and the S27 validation
pass.

All safety flags on the result are always False: the planner makes no
broker, credential, network, environment-variable, file, or order access
of any kind and persists nothing.

Planner status outcomes:
  BLOCKED_APPROVAL   -- approval artifact failed local structural checks
  BLOCKED_SAFETY     -- approval safety values violated, or the plan
                        validator returned a safety flag True
  BLOCKED_SIGNAL     -- signal snapshot invalid or outside approval
                        allowlists
  BLOCKED_SIZING     -- sizing snapshot invalid or above approval risk caps
  BLOCKED_VALIDATION -- generated plan failed S27 validation
  ERROR_PLANNER      -- plan validator raised an exception
  PLAN_CREATED       -- plan constructed and S27 validation passed
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.research.paper_order_plan_validator import validate_paper_order_plan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)
_RESULT_FLAGS: dict[str, bool] = {f: False for f in _SAFETY_FLAGS}

_REQUIRED_ARTIFACT_SCHEMA_VERSION: str = "PTA/1.0"
_REQUIRED_ARTIFACT_TYPE: str = "PAPER_TRADING_APPROVAL"
_REQUIRED_APPROVAL_SCOPE: str = "PAPER_TRADING_LIMITED_RUN_ONLY"
_REQUIRED_APPROVAL_STATUS: str = "APPROVED_FOR_LIMITED_PAPER_RUN"
_REQUIRED_SESSION: str = "regular"

_ALLOWED_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
_ALLOWED_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})

_MAX_POSITION_FRACTION_CAP: float = 0.10
_MAX_DRAWDOWN_STOP_CAP: float = 1.0
_MAX_ORDERS_PER_DAY_CAP: int = 10

_SIGNAL_STRING_FIELDS: tuple[str, ...] = (
    "symbol",
    "interval",
    "strategy_family",
    "rationale",
    "holding_horizon",
)

_SIZING_FINITE_POSITIVE_FIELDS: tuple[str, ...] = (
    "quantity",
    "notional",
    "max_position_fraction",
    "max_daily_loss",
    "max_drawdown_stop",
    "max_notional_per_position",
)

# Sizing-snapshot field -> approval-artifact cap field for the <= checks.
_SIZING_CAP_FIELDS: tuple[tuple[str, str], ...] = (
    ("notional",                  "max_notional_per_position"),
    ("max_position_fraction",     "max_position_fraction"),
    ("max_daily_loss",            "max_daily_loss"),
    ("max_drawdown_stop",         "max_drawdown_stop"),
    ("max_orders_per_day",        "max_orders_per_day"),
    ("max_notional_per_position", "max_notional_per_position"),
)


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------

class PaperOrderPlannerStatus(str, Enum):
    NOT_PLANNED        = "NOT_PLANNED"
    PLAN_CREATED       = "PLAN_CREATED"
    BLOCKED_APPROVAL   = "BLOCKED_APPROVAL"
    BLOCKED_SIGNAL     = "BLOCKED_SIGNAL"
    BLOCKED_SIZING     = "BLOCKED_SIZING"
    BLOCKED_VALIDATION = "BLOCKED_VALIDATION"
    BLOCKED_SAFETY     = "BLOCKED_SAFETY"
    ERROR_PLANNER      = "ERROR_PLANNER"


@dataclass(frozen=True)
class PaperOrderPlannerResult:
    """Immutable result of create_paper_order_plan().

    plan is the generated in-memory POP/1.0 dict only when planner_status
    is PLAN_CREATED (fail closed: a plan that did not pass every check and
    the S27 validation is never released). A generated plan is an in-memory
    paper order plan only -- it is not an order, and PLAN_CREATED is not
    order approval or paper/live trading approval. All safety flags are
    always False.
    """
    result: str
    blocker: str | None
    planner_status: PaperOrderPlannerStatus
    plan: dict | None
    plan_validation_result: object | None
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

def _get(d: Any, key: str) -> Any:
    if isinstance(d, dict):
        return d.get(key)
    return None


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_nonempty_str_list(v: Any) -> bool:
    return (
        isinstance(v, list)
        and len(v) > 0
        and all(_is_nonempty_str(item) for item in v)
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


def _finite_unit_interval(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and 0.0 <= f <= 1.0
    except (TypeError, ValueError):
        return False


def _make_result(
    *,
    result: str,
    blocker: str | None,
    planner_status: PaperOrderPlannerStatus,
    plan: dict | None,
    plan_validation_result: object | None,
    checked: list[str],
    failed: list[str],
) -> PaperOrderPlannerResult:
    return PaperOrderPlannerResult(
        result=result,
        blocker=blocker,
        planner_status=planner_status,
        plan=plan,
        plan_validation_result=plan_validation_result,
        criteria_checked=tuple(checked),
        criteria_failed=tuple(failed),
        **_RESULT_FLAGS,
    )


def _blocked(
    planner_status: PaperOrderPlannerStatus,
    blocker: str,
    checked: list[str],
    failed: list[str],
    plan_validation_result: object | None = None,
) -> PaperOrderPlannerResult:
    return _make_result(
        result="BLOCKED",
        blocker=blocker,
        planner_status=planner_status,
        plan=None,
        plan_validation_result=plan_validation_result,
        checked=checked,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# Stage checks
# ---------------------------------------------------------------------------

def _check_approval(approval: Any, checked: list[str], failed: list[str]) -> None:
    """Local structural/safety checks on the approval artifact (criteria
    approval.*). Deliberately simple and local -- the S25 validator is not
    called here; the full planner -> validator -> gate chain is exercised
    separately."""

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    _chk(
        "approval.schema",
        isinstance(approval, dict)
        and _get(approval, "artifact_schema_version") == _REQUIRED_ARTIFACT_SCHEMA_VERSION,
    )
    _chk(
        "approval.type",
        _get(approval, "approval_artifact_type") == _REQUIRED_ARTIFACT_TYPE,
    )
    _chk(
        "approval.scope",
        _get(approval, "approval_scope") == _REQUIRED_APPROVAL_SCOPE,
    )
    _chk(
        "approval.status",
        _get(approval, "approval_status") == _REQUIRED_APPROVAL_STATUS,
    )
    _chk(
        "approval.identity",
        _is_nonempty_str(_get(approval, "candidate_id"))
        and _is_nonempty_str(_get(approval, "run_id")),
    )
    _chk(
        "approval.evidence_hashes",
        _is_nonempty_str(_get(approval, "paper_config_hash"))
        and _is_nonempty_str(_get(approval, "simulation_result_hash"))
        and _is_nonempty_str(_get(approval, "artifact_hash")),
    )
    _chk(
        "approval.allowlists",
        _is_nonempty_str_list(_get(approval, "allowed_symbols"))
        and _is_nonempty_str_list(_get(approval, "allowed_intervals"))
        and _is_nonempty_str_list(_get(approval, "allowed_strategy_families"))
        and _is_nonempty_str_list(_get(approval, "allowed_order_types"))
        and _get(approval, "allowed_session") == _REQUIRED_SESSION,
    )
    mpf = _get(approval, "max_position_fraction")
    mds = _get(approval, "max_drawdown_stop")
    mopd = _get(approval, "max_orders_per_day")
    _chk(
        "approval.risk_limits",
        _finite_positive(_get(approval, "max_notional_per_position"))
        and _finite_positive(mpf) and float(mpf) <= _MAX_POSITION_FRACTION_CAP
        and _finite_positive(_get(approval, "max_daily_loss"))
        and _finite_positive(mds) and float(mds) <= _MAX_DRAWDOWN_STOP_CAP
        and _positive_int(mopd) and int(mopd) <= _MAX_ORDERS_PER_DAY_CAP,
    )
    _chk(
        "approval.safety_flags",
        _get(approval, "live_trading_approved") is False
        and _get(approval, "live_order_submission_approved") is False
        and _get(approval, "dry_run_required") is True
        and _get(approval, "human_confirmation_required") is True
        and _get(approval, "kill_switch_required") is True,
    )


def _check_signal(
    signal: Any, approval: dict, checked: list[str], failed: list[str]
) -> None:
    """Signal snapshot checks (criteria signal.*)."""

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    _chk(
        "signal.schema",
        isinstance(signal, dict)
        and all(_is_nonempty_str(_get(signal, f)) for f in _SIGNAL_STRING_FIELDS)
        and _is_nonempty_str(_get(signal, "side"))
        and _is_nonempty_str(_get(signal, "order_type"))
        and _finite_unit_interval(_get(signal, "confidence")),
    )
    _chk(
        "signal.allowlists",
        _get(signal, "symbol") in (approval.get("allowed_symbols") or [])
        and _get(signal, "interval") in (approval.get("allowed_intervals") or [])
        and _get(signal, "strategy_family")
        in (approval.get("allowed_strategy_families") or [])
        and _get(signal, "order_type") in (approval.get("allowed_order_types") or []),
    )
    _chk(
        "signal.intent",
        _get(signal, "side") in _ALLOWED_SIDES
        and _get(signal, "order_type") in _ALLOWED_ORDER_TYPES,
    )


def _check_sizing(
    sizing: Any, approval: dict, order_type: str, checked: list[str], failed: list[str]
) -> None:
    """Sizing snapshot checks (criteria sizing.*)."""

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    limit_price = _get(sizing, "limit_price")
    if order_type == "limit":
        limit_price_ok = _finite_positive(limit_price)
    else:
        limit_price_ok = limit_price is None

    _chk(
        "sizing.schema",
        isinstance(sizing, dict)
        and all(
            _finite_positive(_get(sizing, f)) for f in _SIZING_FINITE_POSITIVE_FIELDS
        )
        and _positive_int(_get(sizing, "max_orders_per_day"))
        and limit_price_ok,
    )

    caps_ok = True
    for sizing_field, approval_field in _SIZING_CAP_FIELDS:
        v = _get(sizing, sizing_field)
        cap = _get(approval, approval_field)
        try:
            if v is None or cap is None or float(v) > float(cap):
                caps_ok = False
                break
        except (TypeError, ValueError):
            caps_ok = False
            break
    _chk("sizing.risk_limits", caps_ok)


def _construct_plan(
    approval: dict,
    signal: dict,
    sizing: dict,
    *,
    generated_at_utc: str,
    expires_at_utc: str,
    plan_id: str,
    source_git_sha: str,
) -> dict:
    """Build the in-memory POP/1.0 plan dict. Exactly the S27-expected
    fields -- no extras. This dict is a paper order plan, not an order."""
    return {
        "plan_schema_version": "POP/1.0",
        "plan_type": "PAPER_ORDER_PLAN",
        "plan_id": plan_id,
        "candidate_id": approval["candidate_id"],
        "run_id": approval["run_id"],
        "source_git_sha": source_git_sha,
        "approval_artifact_hash": approval["artifact_hash"],
        "paper_config_hash": approval["paper_config_hash"],
        "simulation_result_hash": approval["simulation_result_hash"],
        "generated_at_utc": generated_at_utc,
        "expires_at_utc": expires_at_utc,
        "symbol": signal["symbol"],
        "interval": signal["interval"],
        "strategy_family": signal["strategy_family"],
        "holding_horizon": signal["holding_horizon"],
        "side": signal["side"],
        "order_type": signal["order_type"],
        "quantity": sizing["quantity"],
        "notional": sizing["notional"],
        "limit_price": sizing.get("limit_price"),
        "time_in_force": "day",
        "allowed_session": approval["allowed_session"],
        "rationale": signal["rationale"],
        "signal_snapshot": copy.deepcopy(signal),
        "risk_snapshot": copy.deepcopy(sizing),
        "approval_scope": "PAPER_TRADING_LIMITED_RUN_ONLY",
        "dry_run_required": True,
        "human_confirmation_required": True,
        "kill_switch_required": True,
        "safety_gate_required": True,
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
        "live_trading_allowed": False,
        "notes": "generated by pure offline paper order planner",
        "plan_status": "PLAN_READY_FOR_SAFETY_GATE",
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def create_paper_order_plan(
    approval_artifact: dict,
    *,
    signal_snapshot: dict,
    sizing_snapshot: dict,
    generated_at_utc: str,
    expires_at_utc: str,
    plan_id: str,
    source_git_sha: str,
    _plan_validator=None,
) -> PaperOrderPlannerResult:
    """Create and validate an in-memory POP/1.0 paper order plan.

    Parameters
    ----------
    approval_artifact:
        Already-loaded PTA/1.0 approval artifact dict. Checked locally for
        structure, allowlists, risk caps, and fixed safety values.
    signal_snapshot:
        In-memory dict describing the signal (symbol/interval/
        strategy_family/side/order_type/confidence/rationale/
        holding_horizon). Must fall inside the approval allowlists.
    sizing_snapshot:
        In-memory dict describing the proposed size (quantity/notional/
        limit_price plus risk-cap fields). Every cap must be at or below
        the corresponding approval cap.
    generated_at_utc / expires_at_utc / plan_id / source_git_sha:
        Provenance values copied verbatim into the plan; malformed values
        are caught by the S27 validation step (BLOCKED_VALIDATION).
    _plan_validator:
        Injectable plan validator for testing (default: the real S27
        validate_paper_order_plan).

    Returns
    -------
    PaperOrderPlannerResult
        planner_status=PLAN_CREATED and result="PASS" only when the plan
        was constructed and passed S27 validation. The plan field is
        populated only in that case (fail closed). All safety flags on the
        result are always False.

    Notes
    -----
    Pure function: same inputs -> same output. Performs no file I/O, no
    network access, no environment variable reads, no broker/API calls, no
    credential access, and no order action. The generated POP/1.0 plan is
    an in-memory paper order plan only -- it is not an order, cannot be
    submitted by this module, and PLAN_CREATED is never order approval or
    paper/live trading approval. Paper trading remains not approved; live
    trading remains blocked.
    """
    validator = (
        validate_paper_order_plan if _plan_validator is None else _plan_validator
    )

    checked: list[str] = []
    failed: list[str] = []

    # -----------------------------------------------------------------------
    # Stage 1 -- approval artifact (local structural/safety checks)
    # -----------------------------------------------------------------------
    _check_approval(approval_artifact, checked, failed)
    if failed:
        if "approval.safety_flags" in failed:
            return _blocked(
                PaperOrderPlannerStatus.BLOCKED_SAFETY,
                "approval safety values violated: " + ", ".join(failed),
                checked, failed,
            )
        return _blocked(
            PaperOrderPlannerStatus.BLOCKED_APPROVAL,
            "approval artifact failed structural checks: " + ", ".join(failed),
            checked, failed,
        )

    # -----------------------------------------------------------------------
    # Stage 2 -- signal snapshot
    # -----------------------------------------------------------------------
    _check_signal(signal_snapshot, approval_artifact, checked, failed)
    if failed:
        return _blocked(
            PaperOrderPlannerStatus.BLOCKED_SIGNAL,
            "signal snapshot failed checks: " + ", ".join(failed),
            checked, failed,
        )

    # -----------------------------------------------------------------------
    # Stage 3 -- sizing snapshot
    # -----------------------------------------------------------------------
    _check_sizing(
        sizing_snapshot, approval_artifact, signal_snapshot["order_type"],
        checked, failed,
    )
    if failed:
        return _blocked(
            PaperOrderPlannerStatus.BLOCKED_SIZING,
            "sizing snapshot failed checks: " + ", ".join(failed),
            checked, failed,
        )

    # -----------------------------------------------------------------------
    # Stage 4 -- construct the in-memory plan dict
    # -----------------------------------------------------------------------
    plan = _construct_plan(
        approval_artifact, signal_snapshot, sizing_snapshot,
        generated_at_utc=generated_at_utc,
        expires_at_utc=expires_at_utc,
        plan_id=plan_id,
        source_git_sha=source_git_sha,
    )
    checked.append("plan.constructed")

    # -----------------------------------------------------------------------
    # Stage 5 -- validate the generated plan with the S27 validator
    # -----------------------------------------------------------------------
    try:
        validation_result = validator(plan)
    except Exception as exc:
        checked.append("plan.validation")
        failed.append("plan.validation")
        return _make_result(
            result="ERROR",
            blocker=f"plan validator raised: {exc}",
            planner_status=PaperOrderPlannerStatus.ERROR_PLANNER,
            plan=None,
            plan_validation_result=None,
            checked=checked,
            failed=failed,
        )

    checked.append("plan.validation")
    validation_passed = getattr(validation_result, "result", None) == "PASS"
    if not validation_passed:
        failed.append("plan.validation")

    checked.append("safety.result_flags")
    validator_flags_clean = not any(
        getattr(validation_result, flag, False) for flag in _SAFETY_FLAGS
    )
    if not validator_flags_clean:
        failed.append("safety.result_flags")

    if not validator_flags_clean:
        return _blocked(
            PaperOrderPlannerStatus.BLOCKED_SAFETY,
            "plan validator returned safety flag True",
            checked, failed,
            plan_validation_result=validation_result,
        )

    if not validation_passed:
        return _blocked(
            PaperOrderPlannerStatus.BLOCKED_VALIDATION,
            "generated plan failed validation: "
            + str(getattr(validation_result, "blocker", None)),
            checked, failed,
            plan_validation_result=validation_result,
        )

    return _make_result(
        result="PASS",
        blocker=None,
        planner_status=PaperOrderPlannerStatus.PLAN_CREATED,
        plan=plan,
        plan_validation_result=validation_result,
        checked=checked,
        failed=failed,
    )
