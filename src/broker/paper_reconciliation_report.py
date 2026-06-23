from __future__ import annotations

import dataclasses
import math
from enum import Enum

from src.broker.paper_snapshot_reconciliation import (
    PaperSnapshotReconciliationResult,
    PaperSnapshotReconciliationStatus,
)


class PaperReconciliationReportStatus(str, Enum):
    NOT_RENDERED = "NOT_RENDERED"
    REPORT_READY_NO_DIFFERENCE = "REPORT_READY_NO_DIFFERENCE"
    REPORT_READY_DIFFERENCE_FOUND = "REPORT_READY_DIFFERENCE_FOUND"
    BLOCKED_RECONCILIATION = "BLOCKED_RECONCILIATION"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


@dataclasses.dataclass(frozen=True)
class PaperReconciliationReportResult:
    result: str
    status: PaperReconciliationReportStatus
    blocker: str | None
    request_id: str | None
    summary: str | None
    financial_lines: tuple[str, ...] | None
    position_lines: tuple[str, ...] | None
    open_order_lines: tuple[str, ...] | None
    criteria_checked: tuple[str, ...]
    criteria_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


_NOT_ORDER_SIGNAL_NOTICE = (
    "differences are observations only and are not order signals"
)


def _blocked(
    status: PaperReconciliationReportStatus,
    blocker: str,
    checked: list[str],
    failed_criterion: str,
    *,
    request_id: str | None = None,
) -> PaperReconciliationReportResult:
    return PaperReconciliationReportResult(
        result="BLOCKED",
        status=status,
        blocker=blocker,
        request_id=request_id,
        summary=None,
        financial_lines=None,
        position_lines=None,
        open_order_lines=None,
        criteria_checked=tuple(checked),
        criteria_failed=(failed_criterion,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return True


def _format_amount(value: float) -> str:
    if value > 0:
        return f"+{value:.2f}"
    return f"{value:.2f}"


def _format_diff_item(diff: dict, *, kind_label: str) -> str:
    key = diff.get("key", "")
    kind = diff.get("kind", "")
    if kind == "extra_in_snapshot":
        actual = diff.get("actual")
        return f"{kind_label} {key}: extra_in_snapshot actual={actual!r}"
    if kind == "missing_from_snapshot":
        expected = diff.get("expected")
        return f"{kind_label} {key}: missing_from_snapshot expected={expected!r}"
    if kind == "changed":
        actual = diff.get("actual")
        expected = diff.get("expected")
        return (
            f"{kind_label} {key}: changed "
            f"actual={actual!r} expected={expected!r}"
        )
    return f"{kind_label} {key}: {kind}"


def render_paper_reconciliation_report(
    reconciliation_result: PaperSnapshotReconciliationResult,
) -> PaperReconciliationReportResult:
    checked: list[str] = []

    # 1. input.reconciliation_type
    checked.append("input.reconciliation_type")
    if not isinstance(reconciliation_result, PaperSnapshotReconciliationResult):
        return _blocked(
            PaperReconciliationReportStatus.BLOCKED_SCHEMA,
            "reconciliation_result is not a PaperSnapshotReconciliationResult",
            checked,
            "input.reconciliation_type",
        )

    request_id = reconciliation_result.request_id

    # 2. input.reconciliation_pass
    checked.append("input.reconciliation_pass")
    accepted_statuses = {
        PaperSnapshotReconciliationStatus.RECONCILED_NO_DIFFERENCE,
        PaperSnapshotReconciliationStatus.RECONCILED_DIFFERENCE_FOUND,
    }
    if reconciliation_result.result != "PASS" or reconciliation_result.status not in accepted_statuses:
        return _blocked(
            PaperReconciliationReportStatus.BLOCKED_RECONCILIATION,
            "reconciliation_result is not a PASS RECONCILED_* result",
            checked,
            "input.reconciliation_pass",
            request_id=request_id if isinstance(request_id, str) else None,
        )

    # 3. input.reconciliation_safety_flags
    checked.append("input.reconciliation_safety_flags")
    for flag in (
        reconciliation_result.broker_calls_made,
        reconciliation_result.credentials_read,
        reconciliation_result.network_calls_made,
        reconciliation_result.order_action_requested,
        reconciliation_result.live_trading_allowed,
    ):
        if flag is not False:
            return _blocked(
                PaperReconciliationReportStatus.BLOCKED_SAFETY,
                "reconciliation_result has a safety flag set to True",
                checked,
                "input.reconciliation_safety_flags",
                request_id=request_id if isinstance(request_id, str) else None,
            )

    # 4. input.reconciliation_payload
    checked.append("input.reconciliation_payload")
    if not isinstance(request_id, str) or not request_id.strip():
        return _blocked(
            PaperReconciliationReportStatus.BLOCKED_SCHEMA,
            "reconciliation_result.request_id is not a non-empty string",
            checked,
            "input.reconciliation_payload",
            request_id=request_id if isinstance(request_id, str) else None,
        )
    for field_name, value in (
        ("cash_difference", reconciliation_result.cash_difference),
        ("buying_power_difference", reconciliation_result.buying_power_difference),
        ("equity_difference", reconciliation_result.equity_difference),
    ):
        if not _is_finite_number(value):
            return _blocked(
                PaperReconciliationReportStatus.BLOCKED_SCHEMA,
                f"reconciliation_result.{field_name} is not a finite number",
                checked,
                "input.reconciliation_payload",
                request_id=request_id,
            )
    for field_name, value in (
        ("position_differences", reconciliation_result.position_differences),
        ("open_order_differences", reconciliation_result.open_order_differences),
    ):
        if not isinstance(value, tuple):
            return _blocked(
                PaperReconciliationReportStatus.BLOCKED_SCHEMA,
                f"reconciliation_result.{field_name} is not a tuple",
                checked,
                "input.reconciliation_payload",
                request_id=request_id,
            )

    # 5. input.reconciliation_consistency
    checked.append("input.reconciliation_consistency")
    actual_has_difference = (
        float(reconciliation_result.cash_difference) != 0
        or float(reconciliation_result.buying_power_difference) != 0
        or float(reconciliation_result.equity_difference) != 0
        or len(reconciliation_result.position_differences) > 0
        or len(reconciliation_result.open_order_differences) > 0
    )
    declared_difference = (
        reconciliation_result.status
        is PaperSnapshotReconciliationStatus.RECONCILED_DIFFERENCE_FOUND
    )
    if declared_difference != actual_has_difference:
        return _blocked(
            PaperReconciliationReportStatus.BLOCKED_RECONCILIATION,
            "reconciliation_result.status does not match the payload",
            checked,
            "input.reconciliation_consistency",
            request_id=request_id,
        )

    # 6. rendering.financial_lines
    checked.append("rendering.financial_lines")
    financial_lines: list[str] = []
    for field_label, value in (
        ("cash_difference", reconciliation_result.cash_difference),
        ("buying_power_difference", reconciliation_result.buying_power_difference),
        ("equity_difference", reconciliation_result.equity_difference),
    ):
        financial_lines.append(f"{field_label}={_format_amount(float(value))}")

    # 7. rendering.position_lines
    checked.append("rendering.position_lines")
    position_lines: list[str] = []
    for diff in reconciliation_result.position_differences:
        if not isinstance(diff, dict):
            return _blocked(
                PaperReconciliationReportStatus.BLOCKED_SCHEMA,
                "position_differences contains a non-dict entry",
                checked,
                "rendering.position_lines",
                request_id=request_id,
            )
        position_lines.append(_format_diff_item(diff, kind_label="position"))
    position_lines.sort()

    # 8. rendering.open_order_lines
    checked.append("rendering.open_order_lines")
    open_order_lines: list[str] = []
    for diff in reconciliation_result.open_order_differences:
        if not isinstance(diff, dict):
            return _blocked(
                PaperReconciliationReportStatus.BLOCKED_SCHEMA,
                "open_order_differences contains a non-dict entry",
                checked,
                "rendering.open_order_lines",
                request_id=request_id,
            )
        open_order_lines.append(_format_diff_item(diff, kind_label="open_order"))
    open_order_lines.sort()

    # 9. rendering.summary
    checked.append("rendering.summary")
    has_difference = (
        reconciliation_result.status
        is PaperSnapshotReconciliationStatus.RECONCILED_DIFFERENCE_FOUND
    )
    if has_difference:
        status = PaperReconciliationReportStatus.REPORT_READY_DIFFERENCE_FOUND
        summary = (
            f"reconciliation request_id={request_id} observed differences; "
            f"{_NOT_ORDER_SIGNAL_NOTICE}"
        )
    else:
        status = PaperReconciliationReportStatus.REPORT_READY_NO_DIFFERENCE
        summary = (
            f"reconciliation request_id={request_id} observed no difference"
        )

    # 10. rendering.safety_flags
    checked.append("rendering.safety_flags")

    return PaperReconciliationReportResult(
        result="PASS",
        status=status,
        blocker=None,
        request_id=request_id,
        summary=summary,
        financial_lines=tuple(financial_lines),
        position_lines=tuple(position_lines),
        open_order_lines=tuple(open_order_lines),
        criteria_checked=tuple(checked),
        criteria_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
