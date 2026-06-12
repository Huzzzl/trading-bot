"""
research/paper_dry_run_preview.py
----------------------------------
S36: Pure offline dry-run/no-submit preview renderer.

Converts an already-loaded POP/1.0 paper order plan dict plus already-loaded
safety-gate and lifecycle snapshot dicts into a display-only, in-memory
preview dict. The preview exists so a human can read what WOULD be rendered
in a future dry-run step -- it is never acted on by this module.

This is NOT an order. This is NOT a broker order payload. This is NOT order
submission. This is NOT broker integration. This is NOT paper trading
execution. This is NOT live trading. This is NOT persistence. This module
never calls the planner, validator, safety gate, lifecycle, audit ledger,
broker, runtime, execution, network, environment, or file system, and it
writes nothing to disk.

Pure function: same input -> same output; inputs are never mutated; the
returned preview is a plain in-memory dict only. All safety flags on every
result are always False. The renderer fails closed: any failed check
releases no preview (preview=None). PREVIEW_RENDERED is never order
approval or paper/live trading approval. S28 PASS_DRY_RUN_ONLY remains only
offline clearance for a future dry-run/no-submit rendering step. Paper
trading remains not approved; live trading remains blocked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

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

_REQUIRED_PLAN_SCHEMA_VERSION: str = "POP/1.0"
_REQUIRED_PLAN_TYPE: str = "PAPER_ORDER_PLAN"
_REQUIRED_PLAN_STATUS: str = "PLAN_READY_FOR_SAFETY_GATE"
_REQUIRED_GATE_STATUS: str = "PASS_DRY_RUN_ONLY"
_REQUIRED_LIFECYCLE_STATUS: str = "GATE_PASSED_DRY_RUN_ONLY"

_ALLOWED_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
_ALLOWED_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})

# Required True booleans on the plan.
_PLAN_REQUIRED_TRUE: tuple[str, ...] = (
    "dry_run_required",
    "human_confirmation_required",
    "kill_switch_required",
    "safety_gate_required",
)

# Required True booleans on the gate snapshot.
_GATE_REQUIRED_TRUE: tuple[str, ...] = (
    "dry_run_required",
    "human_confirmation_required",
    "kill_switch_required",
)

_PREVIEW_NOTES: str = (
    "display-only dry-run/no-submit preview; this is not an order, not a "
    "broker payload, and cannot be submitted; paper trading remains not "
    "approved; live trading remains blocked"
)


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------

class PaperDryRunPreviewStatus(str, Enum):
    NOT_RENDERED      = "NOT_RENDERED"
    PREVIEW_RENDERED  = "PREVIEW_RENDERED"
    BLOCKED_PLAN      = "BLOCKED_PLAN"
    BLOCKED_GATE      = "BLOCKED_GATE"
    BLOCKED_LIFECYCLE = "BLOCKED_LIFECYCLE"
    BLOCKED_SAFETY    = "BLOCKED_SAFETY"
    ERROR_RENDERER    = "ERROR_RENDERER"


@dataclass(frozen=True)
class PaperDryRunPreviewResult:
    """Immutable result of render_paper_dry_run_preview().

    preview is the rendered display-only dict only when preview_status is
    PREVIEW_RENDERED (fail closed). The preview is an in-memory display
    record only -- it is not an order, not a broker payload, and
    PREVIEW_RENDERED is never order approval or paper/live trading
    approval. All safety flags are always False.
    """
    result: str
    blocker: str | None
    preview_status: PaperDryRunPreviewStatus
    preview: dict | None
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


def _finite_positive(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f > 0.0
    except (TypeError, ValueError):
        return False


def _safety_flags_clean(d: Any) -> bool:
    """All five safety flags must be present and exactly False."""
    return all(_get(d, flag) is False for flag in _SAFETY_FLAGS)


def _make_result(
    *,
    result: str,
    blocker: str | None,
    preview_status: PaperDryRunPreviewStatus,
    preview: dict | None,
    checked: list[str],
    failed: list[str],
) -> PaperDryRunPreviewResult:
    return PaperDryRunPreviewResult(
        result=result,
        blocker=blocker,
        preview_status=preview_status,
        preview=preview,
        criteria_checked=tuple(checked),
        criteria_failed=tuple(failed),
        **_RESULT_FLAGS,
    )


def _blocked(
    preview_status: PaperDryRunPreviewStatus,
    blocker: str,
    checked: list[str],
    failed: list[str],
) -> PaperDryRunPreviewResult:
    return _make_result(
        result="BLOCKED",
        blocker=blocker,
        preview_status=preview_status,
        preview=None,
        checked=checked,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# Stage checks
# ---------------------------------------------------------------------------

def _check_plan(plan: Any, checked: list[str], failed: list[str]) -> None:
    """Local structural checks on the order plan (criteria plan.*)."""

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    _chk(
        "plan.schema",
        isinstance(plan, dict)
        and _get(plan, "plan_schema_version") == _REQUIRED_PLAN_SCHEMA_VERSION
        and _get(plan, "plan_type") == _REQUIRED_PLAN_TYPE
        and _get(plan, "plan_status") == _REQUIRED_PLAN_STATUS,
    )
    _chk(
        "plan.identity",
        _is_nonempty_str(_get(plan, "plan_id"))
        and _is_nonempty_str(_get(plan, "candidate_id"))
        and _is_nonempty_str(_get(plan, "run_id"))
        and _is_nonempty_str(_get(plan, "symbol")),
    )
    order_type = _get(plan, "order_type")
    _chk(
        "plan.intent",
        _get(plan, "side") in _ALLOWED_SIDES
        and order_type in _ALLOWED_ORDER_TYPES,
    )
    limit_price = _get(plan, "limit_price")
    if order_type == "limit":
        limit_price_ok = _finite_positive(limit_price)
    else:
        limit_price_ok = limit_price is None
    _chk(
        "plan.sizing",
        _finite_positive(_get(plan, "quantity"))
        and _finite_positive(_get(plan, "notional"))
        and limit_price_ok,
    )
    _chk(
        "plan.fixed_booleans",
        all(_get(plan, field) is True for field in _PLAN_REQUIRED_TRUE),
    )
    _chk("plan.safety_flags", _safety_flags_clean(plan))


def _check_gate(
    gate: Any, plan: dict, checked: list[str], failed: list[str]
) -> None:
    """Local structural checks on the gate snapshot (criteria gate.*)."""

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    _chk("gate.schema", isinstance(gate, dict))
    _chk(
        "gate.status",
        _get(gate, "result") == "PASS"
        and _get(gate, "gate_status") == _REQUIRED_GATE_STATUS,
    )
    _chk(
        "gate.identity",
        _get(gate, "plan_id") == plan.get("plan_id")
        and _get(gate, "candidate_id") == plan.get("candidate_id")
        and _get(gate, "run_id") == plan.get("run_id"),
    )
    _chk(
        "gate.fixed_booleans",
        all(_get(gate, field) is True for field in _GATE_REQUIRED_TRUE),
    )
    _chk("gate.safety_flags", _safety_flags_clean(gate))


def _check_lifecycle(
    lifecycle: Any, plan: dict, checked: list[str], failed: list[str]
) -> None:
    """Local structural checks on the lifecycle snapshot (criteria
    lifecycle.*)."""

    def _chk(name: str, ok: bool) -> None:
        checked.append(name)
        if not ok:
            failed.append(name)

    _chk("lifecycle.schema", isinstance(lifecycle, dict))
    _chk(
        "lifecycle.status",
        _get(lifecycle, "status") == _REQUIRED_LIFECYCLE_STATUS,
    )
    _chk(
        "lifecycle.identity",
        _is_nonempty_str(_get(lifecycle, "lifecycle_id"))
        and _get(lifecycle, "plan_id") == plan.get("plan_id")
        and _get(lifecycle, "candidate_id") == plan.get("candidate_id")
        and _get(lifecycle, "run_id") == plan.get("run_id"),
    )
    _chk("lifecycle.safety_flags", _safety_flags_clean(lifecycle))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_paper_dry_run_preview(
    order_plan: dict,
    *,
    gate_snapshot: dict,
    lifecycle_snapshot: dict,
    preview_id: str,
    rendered_at_utc: str,
) -> PaperDryRunPreviewResult:
    """Render a display-only dry-run/no-submit preview dict.

    Parameters
    ----------
    order_plan:
        Already-loaded POP/1.0 paper order plan dict. Checked locally for
        schema/identity/intent/sizing/fixed-boolean/safety-flag values.
    gate_snapshot:
        Already-loaded dict summarising the S28 gate outcome. Must report
        result="PASS" with gate_status=PASS_DRY_RUN_ONLY and match the plan
        identity. The gate itself is never called here.
    lifecycle_snapshot:
        Already-loaded dict summarising the S32 lifecycle state. Must report
        status=GATE_PASSED_DRY_RUN_ONLY and match the plan identity. The
        lifecycle is never called here.
    preview_id / rendered_at_utc:
        Provenance values copied verbatim into the preview; both must be
        non-empty strings.

    Returns
    -------
    PaperDryRunPreviewResult
        preview_status=PREVIEW_RENDERED and result="PASS" only when every
        check passed; the preview field is populated only in that case
        (fail closed). All safety flags on the result are always False.

    Notes
    -----
    Pure function: same inputs -> same output; inputs are never mutated.
    Performs no file I/O, no persistence, no network access, no environment
    variable reads, no broker/API calls, no credential access, and no order
    action of any kind. The rendered preview is a display-only in-memory
    dict -- it is not an order, not a broker payload, and cannot be
    submitted by this module. PREVIEW_RENDERED is never order approval or
    paper/live trading approval. Paper trading remains not approved; live
    trading remains blocked.
    """
    checked: list[str] = []
    failed: list[str] = []

    try:
        # -------------------------------------------------------------------
        # Stage 1 -- order plan
        # -------------------------------------------------------------------
        _check_plan(order_plan, checked, failed)
        if failed:
            # A safety-flag violation on an otherwise well-formed dict is
            # classified BLOCKED_SAFETY; a non-dict/schema failure is a
            # structural block.
            if "plan.safety_flags" in failed and "plan.schema" not in failed:
                return _blocked(
                    PaperDryRunPreviewStatus.BLOCKED_SAFETY,
                    "plan safety flags violated: " + ", ".join(failed),
                    checked, failed,
                )
            return _blocked(
                PaperDryRunPreviewStatus.BLOCKED_PLAN,
                "order plan failed checks: " + ", ".join(failed),
                checked, failed,
            )

        # -------------------------------------------------------------------
        # Stage 2 -- gate snapshot
        # -------------------------------------------------------------------
        _check_gate(gate_snapshot, order_plan, checked, failed)
        if failed:
            if "gate.safety_flags" in failed and "gate.schema" not in failed:
                return _blocked(
                    PaperDryRunPreviewStatus.BLOCKED_SAFETY,
                    "gate safety flags violated: " + ", ".join(failed),
                    checked, failed,
                )
            return _blocked(
                PaperDryRunPreviewStatus.BLOCKED_GATE,
                "gate snapshot failed checks: " + ", ".join(failed),
                checked, failed,
            )

        # -------------------------------------------------------------------
        # Stage 3 -- lifecycle snapshot
        # -------------------------------------------------------------------
        _check_lifecycle(lifecycle_snapshot, order_plan, checked, failed)
        if failed:
            if (
                "lifecycle.safety_flags" in failed
                and "lifecycle.schema" not in failed
            ):
                return _blocked(
                    PaperDryRunPreviewStatus.BLOCKED_SAFETY,
                    "lifecycle safety flags violated: " + ", ".join(failed),
                    checked, failed,
                )
            return _blocked(
                PaperDryRunPreviewStatus.BLOCKED_LIFECYCLE,
                "lifecycle snapshot failed checks: " + ", ".join(failed),
                checked, failed,
            )

        # -------------------------------------------------------------------
        # Stage 4 -- preview identity
        # -------------------------------------------------------------------
        checked.append("preview.identity")
        if not (_is_nonempty_str(preview_id) and _is_nonempty_str(rendered_at_utc)):
            failed.append("preview.identity")
            return _blocked(
                PaperDryRunPreviewStatus.BLOCKED_PLAN,
                "preview_id and rendered_at_utc must be non-empty strings",
                checked, failed,
            )

        # -------------------------------------------------------------------
        # Stage 5 -- render the display-only preview dict
        # -------------------------------------------------------------------
        checked.append("preview.rendered")
        preview = {
            "preview_schema_version":       "PDRP/1.0",
            "preview_type":                 "PAPER_DRY_RUN_NO_SUBMIT_PREVIEW",
            "preview_id":                   preview_id,
            "rendered_at_utc":              rendered_at_utc,
            "preview_status":               "PREVIEW_RENDERED",
            "display_only":                 True,
            "no_submit":                    True,
            "broker_payload_created":       False,
            "plan_id":                      order_plan["plan_id"],
            "candidate_id":                 order_plan["candidate_id"],
            "run_id":                       order_plan["run_id"],
            "lifecycle_id":                 lifecycle_snapshot["lifecycle_id"],
            "symbol":                       order_plan["symbol"],
            "side":                         order_plan["side"],
            "order_type":                   order_plan["order_type"],
            "quantity":                     order_plan["quantity"],
            "notional":                     order_plan["notional"],
            "limit_price":                  order_plan.get("limit_price"),
            "time_in_force":                order_plan.get("time_in_force"),
            "allowed_session":              order_plan.get("allowed_session"),
            "rationale":                    order_plan.get("rationale"),
            "gate_status":                  gate_snapshot["gate_status"],
            "lifecycle_status":             lifecycle_snapshot["status"],
            "dry_run_required":             True,
            "human_confirmation_required":  True,
            "kill_switch_required":         True,
            "broker_calls_made":            False,
            "credentials_read":             False,
            "network_calls_made":           False,
            "order_action_requested":       False,
            "live_trading_allowed":         False,
            "notes":                        _PREVIEW_NOTES,
        }
        return _make_result(
            result="PASS",
            blocker=None,
            preview_status=PaperDryRunPreviewStatus.PREVIEW_RENDERED,
            preview=preview,
            checked=checked,
            failed=failed,
        )
    except Exception as exc:
        return _make_result(
            result="ERROR",
            blocker=f"renderer raised: {exc}",
            preview_status=PaperDryRunPreviewStatus.ERROR_RENDERER,
            preview=None,
            checked=checked,
            failed=failed,
        )
