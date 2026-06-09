"""
research/paper_order_safety_gate.py
-------------------------------------
S28: Pure offline paper order safety gate.

Evaluates an already-loaded paper trading approval artifact dict and an
already-loaded paper order plan dict together with an in-memory current
state snapshot. Decides whether the order plan may proceed to a future
dry-run/no-submit rendering step.

This is NOT paper trading. This is NOT order submission. This is NOT broker
integration. Gate PASS means only that the plan passed shape, provenance,
risk, and state checks for offline safety review -- it authorises only a
future dry-run/no-submit rendering step.

All safety flags on the result are always False: the gate makes no broker,
credential, network, order, or live/paper trading calls of any kind.

Aggregate gate priority (highest first):
  BLOCKED_APPROVAL          -- approval artifact failed validation
  BLOCKED_PLAN              -- order plan failed validation
  ERROR_GATE                -- current_state missing or structurally invalid
  BLOCKED_SAFETY            -- fixed safety values violated (safety.fixed_flags)
  BLOCKED_PROVENANCE        -- provenance/identity mismatch; symbol/interval/
                              strategy_family not in approval allowlists
  BLOCKED_KILL_SWITCH       -- kill switch not open
  BLOCKED_RISK_LIMIT        -- notional/fraction/order-count/loss/drawdown;
                              order_type/session not in allowlists
  BLOCKED_DUPLICATE         -- plan_id already processed
  BLOCKED_POSITION_CONFLICT -- same symbol+candidate_id already open/pending
  PASS_DRY_RUN_ONLY         -- all 22 checks passed; dry-run rendering only
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.research.paper_approval_validator import (
    PaperApprovalValidationResult,  # noqa: F401 (re-export for type hints)
    validate_paper_approval_artifact,
)
from src.research.paper_order_plan_validator import (
    PaperOrderPlanValidationResult,  # noqa: F401
    validate_paper_order_plan,
)

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

_STATE_FIELDS: tuple[tuple[str, type], ...] = (
    ("kill_switch_open",          bool),
    ("current_daily_order_count", int),
    ("current_daily_pnl",         float),
    ("current_drawdown",          float),
    ("open_positions",            list),
    ("processed_plan_ids",        list),
)

_CONFLICTING_STATUSES: frozenset[str] = frozenset({"OPEN", "PENDING"})

_SAFETY_BUCKET: frozenset[str] = frozenset({"safety.fixed_flags"})
_PROVENANCE_BUCKET: frozenset[str] = frozenset({
    "provenance.candidate_id",
    "provenance.run_id",
    "provenance.approval_artifact_hash",
    "provenance.paper_config_hash",
    "provenance.simulation_result_hash",
    "allowlist.symbol",
    "allowlist.interval",
    "allowlist.strategy_family",
})
_KILL_SWITCH_BUCKET: frozenset[str] = frozenset({"kill_switch.open"})
_RISK_BUCKET: frozenset[str] = frozenset({
    "allowlist.order_type",
    "allowlist.session",
    "risk.notional",
    "risk.position_fraction",
    "risk.daily_order_count",
    "risk.daily_loss",
    "risk.drawdown",
})
_DUPLICATE_BUCKET: frozenset[str] = frozenset({"duplicate.plan_id"})
_CONFLICT_BUCKET: frozenset[str] = frozenset({"position.conflict"})


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------

class PaperOrderSafetyGateStatus(str, Enum):
    NOT_EVALUATED             = "NOT_EVALUATED"
    PASS_DRY_RUN_ONLY         = "PASS_DRY_RUN_ONLY"
    BLOCKED_APPROVAL          = "BLOCKED_APPROVAL"
    BLOCKED_PLAN              = "BLOCKED_PLAN"
    BLOCKED_PROVENANCE        = "BLOCKED_PROVENANCE"
    BLOCKED_KILL_SWITCH       = "BLOCKED_KILL_SWITCH"
    BLOCKED_RISK_LIMIT        = "BLOCKED_RISK_LIMIT"
    BLOCKED_DUPLICATE         = "BLOCKED_DUPLICATE"
    BLOCKED_POSITION_CONFLICT = "BLOCKED_POSITION_CONFLICT"
    BLOCKED_SAFETY            = "BLOCKED_SAFETY"
    ERROR_GATE                = "ERROR_GATE"


@dataclass(frozen=True)
class PaperOrderSafetyGateResult:
    """Immutable result of evaluate_paper_order_safety_gate().

    gate_status=PASS_DRY_RUN_ONLY when all 22 checks pass. All other
    gate_status values are blocked or error states.

    PASS_DRY_RUN_ONLY authorises only a future dry-run/no-submit rendering
    step. It does NOT approve paper trading, live trading, or order
    submission of any kind. All safety flags are always False.
    """
    result: str
    blocker: str | None
    gate_status: PaperOrderSafetyGateStatus
    plan_id: str | None
    candidate_id: str | None
    run_id: str | None
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]
    approved_notional: float | None
    remaining_daily_order_capacity: int | None
    projected_daily_loss_after_order: float | None
    projected_drawdown_after_order: float | None
    dry_run_required: bool
    human_confirmation_required: bool
    kill_switch_required: bool
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


def _any_safety_flag_true(obj: Any) -> bool:
    return any(getattr(obj, flag, False) for flag in _SAFETY_FLAGS)


def _make_result(
    *,
    result: str,
    blocker: str | None,
    gate_status: PaperOrderSafetyGateStatus,
    plan_id: str | None,
    candidate_id: str | None,
    run_id: str | None,
    passed: list[str],
    failed: list[str],
    approved_notional: float | None,
    remaining_daily_order_capacity: int | None,
    projected_daily_loss_after_order: float | None,
    projected_drawdown_after_order: float | None,
) -> PaperOrderSafetyGateResult:
    return PaperOrderSafetyGateResult(
        result=result,
        blocker=blocker,
        gate_status=gate_status,
        plan_id=plan_id,
        candidate_id=candidate_id,
        run_id=run_id,
        checks_passed=tuple(passed),
        checks_failed=tuple(failed),
        approved_notional=approved_notional,
        remaining_daily_order_capacity=remaining_daily_order_capacity,
        projected_daily_loss_after_order=projected_daily_loss_after_order,
        projected_drawdown_after_order=projected_drawdown_after_order,
        dry_run_required=True,
        human_confirmation_required=True,
        kill_switch_required=True,
        **_RESULT_FLAGS,
    )


def _validate_state(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    for field, expected_type in _STATE_FIELDS:
        if field not in state:
            return False
        val = state[field]
        if expected_type is bool:
            if not isinstance(val, bool):
                return False
        elif expected_type is int:
            if not isinstance(val, int) or isinstance(val, bool):
                return False
        elif expected_type is float:
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                return False
        elif not isinstance(val, expected_type):
            return False
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate_paper_order_safety_gate(
    approval_artifact: dict,
    order_plan: dict,
    *,
    current_state: dict,
    _approval_validator=None,
    _plan_validator=None,
) -> PaperOrderSafetyGateResult:
    """Evaluate a paper order plan against an approval artifact and state.

    Parameters
    ----------
    approval_artifact:
        Already-loaded PTA/1.0 approval artifact dict.
    order_plan:
        Already-loaded POP/1.0 paper order plan dict.
    current_state:
        In-memory current state snapshot with required keys:
        kill_switch_open, current_daily_order_count, current_daily_pnl,
        current_drawdown, open_positions, processed_plan_ids.
    _approval_validator:
        Injectable approval validator for testing (default: real validator).
    _plan_validator:
        Injectable plan validator for testing (default: real validator).

    Returns
    -------
    PaperOrderSafetyGateResult
        gate_status=PASS_DRY_RUN_ONLY when all 22 checks pass.
        All safety flags on the result are always False.

    Notes
    -----
    Pure function: same inputs -> same output. Performs no file I/O, no
    network access, no environment variable reads, no broker/API calls, no
    credential access, and no order action. PASS_DRY_RUN_ONLY authorises only
    a future dry-run/no-submit rendering step -- it is not, and can never be,
    paper or live trading approval or order submission approval.
    """
    approval_fn = (
        validate_paper_approval_artifact
        if _approval_validator is None
        else _approval_validator
    )
    plan_fn = (
        validate_paper_order_plan
        if _plan_validator is None
        else _plan_validator
    )

    passed: list[str] = []
    early_failed: list[str] = []

    # Tentative identity from raw inputs (refined after validators pass)
    plan_id: str | None = _get(order_plan, "plan_id")
    candidate_id: str | None = _get(approval_artifact, "candidate_id")
    run_id: str | None = _get(approval_artifact, "run_id")

    # -----------------------------------------------------------------------
    # Gate 1 -- Approval validator (early exit on failure)
    # -----------------------------------------------------------------------
    try:
        approval_result = approval_fn(approval_artifact)
    except Exception as exc:
        early_failed.append("approval.validator")
        return _make_result(
            result="BLOCKED",
            blocker=f"approval validator raised: {exc}",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_APPROVAL,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=None, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    if _any_safety_flag_true(approval_result):
        early_failed.append("approval.validator")
        return _make_result(
            result="BLOCKED",
            blocker="approval validator returned safety flag True",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_SAFETY,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=None, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    if approval_result.result != "PASS":
        early_failed.append("approval.validator")
        return _make_result(
            result="BLOCKED",
            blocker=f"approval artifact blocked: {approval_result.blocker}",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_APPROVAL,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=None, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    passed.append("approval.validator")
    candidate_id = approval_result.candidate_id or candidate_id
    run_id = approval_result.run_id or run_id
    approved_notional: float = float(approval_artifact.get("max_notional_per_position", 0.0))

    # -----------------------------------------------------------------------
    # Gate 2 -- Plan validator (early exit on failure)
    # -----------------------------------------------------------------------
    try:
        plan_result = plan_fn(order_plan)
    except Exception as exc:
        early_failed.append("plan.validator")
        return _make_result(
            result="BLOCKED",
            blocker=f"plan validator raised: {exc}",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_PLAN,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=approved_notional, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    if _any_safety_flag_true(plan_result):
        early_failed.append("plan.validator")
        return _make_result(
            result="BLOCKED",
            blocker="plan validator returned safety flag True",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_SAFETY,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=approved_notional, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    if plan_result.result != "PASS":
        early_failed.append("plan.validator")
        return _make_result(
            result="BLOCKED",
            blocker=f"order plan blocked: {plan_result.blocker}",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_PLAN,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=approved_notional, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    passed.append("plan.validator")
    plan_id = plan_result.plan_id or plan_id

    # -----------------------------------------------------------------------
    # Gate 3 -- State schema (early exit on failure)
    # -----------------------------------------------------------------------
    if not _validate_state(current_state):
        early_failed.append("state.schema")
        return _make_result(
            result="ERROR",
            blocker="current_state missing required fields or wrong types",
            gate_status=PaperOrderSafetyGateStatus.ERROR_GATE,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=early_failed,
            approved_notional=approved_notional, remaining_daily_order_capacity=None,
            projected_daily_loss_after_order=None, projected_drawdown_after_order=None,
        )

    passed.append("state.schema")

    # Unpack state
    kill_switch_open: bool   = current_state["kill_switch_open"]
    daily_order_count: int   = current_state["current_daily_order_count"]
    daily_pnl: float         = float(current_state["current_daily_pnl"])
    drawdown: float          = float(current_state["current_drawdown"])
    open_positions: list     = current_state["open_positions"]
    processed_plan_ids: list = current_state["processed_plan_ids"]

    # Unpack from validated approval artifact
    max_notional: float       = float(approval_artifact.get("max_notional_per_position", 0.0))
    max_orders: int           = int(approval_artifact.get("max_orders_per_day", 0))
    max_daily_loss: float     = float(approval_artifact.get("max_daily_loss", 0.0))
    max_drawdown_stop: float  = float(approval_artifact.get("max_drawdown_stop", 0.0))
    approval_max_pf: float    = float(approval_artifact.get("max_position_fraction", 0.0))
    allowed_symbols: list     = list(approval_artifact.get("allowed_symbols") or [])
    allowed_intervals: list   = list(approval_artifact.get("allowed_intervals") or [])
    allowed_families: list    = list(approval_artifact.get("allowed_strategy_families") or [])
    allowed_order_types: list = list(approval_artifact.get("allowed_order_types") or [])
    allowed_session: str      = str(approval_artifact.get("allowed_session") or "")

    # Unpack from validated order plan
    notional: float           = float(order_plan.get("notional", 0.0))
    symbol: str               = str(order_plan.get("symbol") or "")
    interval: str             = str(order_plan.get("interval") or "")
    strategy_family: str      = str(order_plan.get("strategy_family") or "")
    order_type: str           = str(order_plan.get("order_type") or "")
    plan_session: str         = str(order_plan.get("allowed_session") or "")
    plan_candidate_id         = order_plan.get("candidate_id")
    plan_run_id               = order_plan.get("run_id")
    plan_id_val               = order_plan.get("plan_id")

    risk_snap: dict           = order_plan.get("risk_snapshot") or {}
    plan_max_pf: float        = float(risk_snap.get("max_position_fraction") or 0.0)

    # Compute projected risk values
    remaining_capacity: int   = max(0, max_orders - daily_order_count)
    proj_daily_loss: float    = max(0.0, -daily_pnl) + notional
    proj_drawdown: float      = drawdown + (notional / max_notional if max_notional > 0 else 0.0)

    # -----------------------------------------------------------------------
    # Checks 4–22: run all 19, accumulate, then classify by bucket priority
    # -----------------------------------------------------------------------
    gate_failed: list[str] = []

    def _chk(name: str, ok: bool) -> None:
        if ok:
            passed.append(name)
        else:
            gate_failed.append(name)

    # Provenance: candidate_id must match
    _chk("provenance.candidate_id", plan_candidate_id == candidate_id)

    # Provenance: run_id must match
    _chk("provenance.run_id", plan_run_id == run_id)

    # Provenance: approval_artifact_hash (only checked when artifact_hash present)
    art_hash = approval_artifact.get("artifact_hash")
    if art_hash is not None:
        _chk("provenance.approval_artifact_hash",
             order_plan.get("approval_artifact_hash") == art_hash)
    else:
        _chk("provenance.approval_artifact_hash", True)

    # Provenance: paper_config_hash must match
    _chk(
        "provenance.paper_config_hash",
        order_plan.get("paper_config_hash") == approval_artifact.get("paper_config_hash"),
    )

    # Provenance: simulation_result_hash must match
    _chk(
        "provenance.simulation_result_hash",
        order_plan.get("simulation_result_hash") == approval_artifact.get("simulation_result_hash"),
    )

    # Kill switch: plan must require it AND state must have it open
    _chk(
        "kill_switch.open",
        order_plan.get("kill_switch_required") is True and kill_switch_open,
    )

    # Allowlist: symbol (→ BLOCKED_PROVENANCE)
    _chk("allowlist.symbol", symbol in allowed_symbols)

    # Allowlist: interval (→ BLOCKED_PROVENANCE)
    _chk("allowlist.interval", interval in allowed_intervals)

    # Allowlist: strategy_family (→ BLOCKED_PROVENANCE)
    _chk("allowlist.strategy_family", strategy_family in allowed_families)

    # Allowlist: order_type (→ BLOCKED_RISK_LIMIT)
    _chk("allowlist.order_type", order_type in allowed_order_types)

    # Allowlist: session (→ BLOCKED_RISK_LIMIT)
    _chk("allowlist.session", plan_session == allowed_session)

    # Risk: notional <= max_notional_per_position
    _chk("risk.notional", notional <= max_notional)

    # Risk: risk_snapshot max_position_fraction <= approval max_position_fraction
    _chk("risk.position_fraction", plan_max_pf <= approval_max_pf)

    # Risk: current_daily_order_count < max_orders_per_day
    _chk("risk.daily_order_count", daily_order_count < max_orders)

    # Risk: projected daily loss <= max_daily_loss
    _chk("risk.daily_loss", proj_daily_loss <= max_daily_loss)

    # Risk: projected drawdown <= max_drawdown_stop
    _chk("risk.drawdown", proj_drawdown <= max_drawdown_stop)

    # Duplicate: plan_id already in processed list
    _chk("duplicate.plan_id", plan_id_val not in processed_plan_ids)

    # Position conflict: same symbol + candidate_id with status OPEN or PENDING
    has_conflict = any(
        isinstance(pos, dict)
        and pos.get("symbol") == symbol
        and pos.get("candidate_id") == candidate_id
        and pos.get("status") in _CONFLICTING_STATUSES
        for pos in open_positions
    )
    _chk("position.conflict", not has_conflict)

    # Safety fixed flags: defense-in-depth re-check of all required safety values
    safety_ok = (
        order_plan.get("dry_run_required") is True
        and order_plan.get("human_confirmation_required") is True
        and order_plan.get("kill_switch_required") is True
        and order_plan.get("safety_gate_required") is True
        and order_plan.get("broker_calls_made") is False
        and order_plan.get("credentials_read") is False
        and order_plan.get("network_calls_made") is False
        and order_plan.get("order_action_requested") is False
        and order_plan.get("live_trading_allowed") is False
        and approval_artifact.get("live_trading_approved") is False
        and approval_artifact.get("live_order_submission_approved") is False
    )
    _chk("safety.fixed_flags", safety_ok)

    # -----------------------------------------------------------------------
    # Classify by bucket priority (highest first)
    # -----------------------------------------------------------------------
    failed_set = set(gate_failed)

    if failed_set & _SAFETY_BUCKET:
        return _make_result(
            result="BLOCKED",
            blocker="safety invariant violated: " + ", ".join(
                c for c in gate_failed if c in _SAFETY_BUCKET
            ),
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_SAFETY,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=gate_failed,
            approved_notional=approved_notional,
            remaining_daily_order_capacity=remaining_capacity,
            projected_daily_loss_after_order=proj_daily_loss,
            projected_drawdown_after_order=proj_drawdown,
        )

    if failed_set & _PROVENANCE_BUCKET:
        return _make_result(
            result="BLOCKED",
            blocker="provenance/identity check failed: " + ", ".join(
                c for c in gate_failed if c in _PROVENANCE_BUCKET
            ),
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_PROVENANCE,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=gate_failed,
            approved_notional=approved_notional,
            remaining_daily_order_capacity=remaining_capacity,
            projected_daily_loss_after_order=proj_daily_loss,
            projected_drawdown_after_order=proj_drawdown,
        )

    if failed_set & _KILL_SWITCH_BUCKET:
        return _make_result(
            result="BLOCKED",
            blocker="kill switch not open",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_KILL_SWITCH,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=gate_failed,
            approved_notional=approved_notional,
            remaining_daily_order_capacity=remaining_capacity,
            projected_daily_loss_after_order=proj_daily_loss,
            projected_drawdown_after_order=proj_drawdown,
        )

    if failed_set & _RISK_BUCKET:
        return _make_result(
            result="BLOCKED",
            blocker="risk/allowlist check failed: " + ", ".join(
                c for c in gate_failed if c in _RISK_BUCKET
            ),
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_RISK_LIMIT,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=gate_failed,
            approved_notional=approved_notional,
            remaining_daily_order_capacity=remaining_capacity,
            projected_daily_loss_after_order=proj_daily_loss,
            projected_drawdown_after_order=proj_drawdown,
        )

    if failed_set & _DUPLICATE_BUCKET:
        return _make_result(
            result="BLOCKED",
            blocker="duplicate plan_id already processed",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_DUPLICATE,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=gate_failed,
            approved_notional=approved_notional,
            remaining_daily_order_capacity=remaining_capacity,
            projected_daily_loss_after_order=proj_daily_loss,
            projected_drawdown_after_order=proj_drawdown,
        )

    if failed_set & _CONFLICT_BUCKET:
        return _make_result(
            result="BLOCKED",
            blocker="position conflict: same symbol+candidate_id already open/pending",
            gate_status=PaperOrderSafetyGateStatus.BLOCKED_POSITION_CONFLICT,
            plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
            passed=passed, failed=gate_failed,
            approved_notional=approved_notional,
            remaining_daily_order_capacity=remaining_capacity,
            projected_daily_loss_after_order=proj_daily_loss,
            projected_drawdown_after_order=proj_drawdown,
        )

    # All 22 checks passed
    return _make_result(
        result="PASS",
        blocker=None,
        gate_status=PaperOrderSafetyGateStatus.PASS_DRY_RUN_ONLY,
        plan_id=plan_id, candidate_id=candidate_id, run_id=run_id,
        passed=passed, failed=gate_failed,
        approved_notional=approved_notional,
        remaining_daily_order_capacity=remaining_capacity,
        projected_daily_loss_after_order=proj_daily_loss,
        projected_drawdown_after_order=proj_drawdown,
    )
