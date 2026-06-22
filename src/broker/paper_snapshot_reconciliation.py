from __future__ import annotations

import copy
import dataclasses
import math
from enum import Enum

from src.broker.paper_account_snapshot import (
    PaperAccountSnapshotResult,
    PaperAccountSnapshotStatus,
)


class PaperSnapshotReconciliationStatus(str, Enum):
    NOT_RECONCILED = "NOT_RECONCILED"
    RECONCILED_NO_DIFFERENCE = "RECONCILED_NO_DIFFERENCE"
    RECONCILED_DIFFERENCE_FOUND = "RECONCILED_DIFFERENCE_FOUND"
    BLOCKED_SNAPSHOT = "BLOCKED_SNAPSHOT"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


@dataclasses.dataclass(frozen=True)
class PaperSnapshotReconciliationResult:
    result: str
    status: PaperSnapshotReconciliationStatus
    blocker: str | None
    request_id: str | None
    cash_difference: float | None
    buying_power_difference: float | None
    equity_difference: float | None
    position_differences: tuple | None
    open_order_differences: tuple | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


def _blocked(
    status: PaperSnapshotReconciliationStatus,
    blocker: str,
    checked: list[str],
    failed_criterion: str,
    *,
    request_id: str | None = None,
) -> PaperSnapshotReconciliationResult:
    return PaperSnapshotReconciliationResult(
        result="BLOCKED",
        status=status,
        blocker=blocker,
        request_id=request_id,
        cash_difference=None,
        buying_power_difference=None,
        equity_difference=None,
        position_differences=None,
        open_order_differences=None,
        criteria_checked=tuple(checked),
        criteria_failed=(failed_criterion,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _is_finite_non_negative(value: object) -> bool:
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, bool):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return value >= 0


def _position_key(item: object) -> str:
    if isinstance(item, dict):
        sym = item.get("symbol")
        if isinstance(sym, str):
            return sym
    return repr(item)


def _order_key(item: object) -> str:
    if isinstance(item, dict):
        oid = item.get("id")
        if isinstance(oid, str):
            return oid
    return repr(item)


def _diff_collection(
    actual: tuple,
    expected: tuple,
    key_fn,
) -> tuple:
    actual_map = {key_fn(item): item for item in actual}
    expected_map = {key_fn(item): item for item in expected}

    diffs: list = []

    for key in sorted(set(actual_map) | set(expected_map)):
        if key not in actual_map:
            diffs.append({
                "kind": "missing_from_snapshot",
                "key": key,
                "expected": copy.deepcopy(expected_map[key]),
            })
        elif key not in expected_map:
            diffs.append({
                "kind": "extra_in_snapshot",
                "key": key,
                "actual": copy.deepcopy(actual_map[key]),
            })
        elif actual_map[key] != expected_map[key]:
            diffs.append({
                "kind": "changed",
                "key": key,
                "actual": copy.deepcopy(actual_map[key]),
                "expected": copy.deepcopy(expected_map[key]),
            })

    return tuple(diffs)


def reconcile_paper_account_snapshot(
    snapshot_result: PaperAccountSnapshotResult,
    *,
    expected_cash: float,
    expected_buying_power: float,
    expected_equity: float,
    expected_positions,
    expected_open_orders,
) -> PaperSnapshotReconciliationResult:
    checked: list[str] = []

    # 1. input.snapshot_type
    checked.append("input.snapshot_type")
    if not isinstance(snapshot_result, PaperAccountSnapshotResult):
        return _blocked(
            PaperSnapshotReconciliationStatus.BLOCKED_SCHEMA,
            "snapshot_result is not a PaperAccountSnapshotResult",
            checked,
            "input.snapshot_type",
        )

    request_id = snapshot_result.request_id

    # 2. input.snapshot_pass
    checked.append("input.snapshot_pass")
    if snapshot_result.result != "PASS" or snapshot_result.status is not PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER:
        return _blocked(
            PaperSnapshotReconciliationStatus.BLOCKED_SNAPSHOT,
            "snapshot_result is not a SNAPSHOT_READY_PAPER PASS",
            checked,
            "input.snapshot_pass",
            request_id=request_id,
        )

    # 3. input.snapshot_safety_flags
    checked.append("input.snapshot_safety_flags")
    for flag in (
        snapshot_result.broker_calls_made,
        snapshot_result.credentials_read,
        snapshot_result.network_calls_made,
        snapshot_result.order_action_requested,
        snapshot_result.live_trading_allowed,
    ):
        if flag is not False:
            return _blocked(
                PaperSnapshotReconciliationStatus.BLOCKED_SAFETY,
                "snapshot_result has a safety flag set to True",
                checked,
                "input.snapshot_safety_flags",
                request_id=request_id,
            )

    # 4. input.expected_financial_values
    checked.append("input.expected_financial_values")
    for name, value in (
        ("expected_cash", expected_cash),
        ("expected_buying_power", expected_buying_power),
        ("expected_equity", expected_equity),
    ):
        if not _is_finite_non_negative(value):
            return _blocked(
                PaperSnapshotReconciliationStatus.BLOCKED_SCHEMA,
                f"{name} is not a finite non-negative number",
                checked,
                "input.expected_financial_values",
                request_id=request_id,
            )

    # 5. input.expected_collections
    checked.append("input.expected_collections")
    if not isinstance(expected_positions, (list, tuple)):
        return _blocked(
            PaperSnapshotReconciliationStatus.BLOCKED_SCHEMA,
            "expected_positions is not a list or tuple",
            checked,
            "input.expected_collections",
            request_id=request_id,
        )
    if not isinstance(expected_open_orders, (list, tuple)):
        return _blocked(
            PaperSnapshotReconciliationStatus.BLOCKED_SCHEMA,
            "expected_open_orders is not a list or tuple",
            checked,
            "input.expected_collections",
            request_id=request_id,
        )

    # 6. reconciliation.financial_values
    checked.append("reconciliation.financial_values")
    cash_diff = float(snapshot_result.cash) - float(expected_cash)
    bp_diff = float(snapshot_result.buying_power) - float(expected_buying_power)
    eq_diff = float(snapshot_result.equity) - float(expected_equity)

    # 7. reconciliation.positions
    checked.append("reconciliation.positions")
    actual_positions = snapshot_result.positions if snapshot_result.positions is not None else ()
    position_diffs = _diff_collection(
        tuple(actual_positions),
        tuple(expected_positions),
        _position_key,
    )

    # 8. reconciliation.open_orders
    checked.append("reconciliation.open_orders")
    actual_orders = snapshot_result.open_orders if snapshot_result.open_orders is not None else ()
    order_diffs = _diff_collection(
        tuple(actual_orders),
        tuple(expected_open_orders),
        _order_key,
    )

    # 9. reconciliation.safety_flags
    checked.append("reconciliation.safety_flags")

    has_difference = (
        cash_diff != 0
        or bp_diff != 0
        or eq_diff != 0
        or len(position_diffs) > 0
        or len(order_diffs) > 0
    )

    status = (
        PaperSnapshotReconciliationStatus.RECONCILED_DIFFERENCE_FOUND
        if has_difference
        else PaperSnapshotReconciliationStatus.RECONCILED_NO_DIFFERENCE
    )

    return PaperSnapshotReconciliationResult(
        result="PASS",
        status=status,
        blocker=None,
        request_id=request_id,
        cash_difference=cash_diff,
        buying_power_difference=bp_diff,
        equity_difference=eq_diff,
        position_differences=position_diffs,
        open_order_differences=order_diffs,
        criteria_checked=tuple(checked),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
