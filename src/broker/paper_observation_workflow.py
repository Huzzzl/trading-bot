from __future__ import annotations

import dataclasses
from enum import Enum

from src.broker.credential_metadata import (
    CredentialMetadataStatus,
    validate_credential_metadata,
)
from src.broker.account_environment_guard import (
    AccountEnvironmentStatus,
    verify_account_environment,
)
from src.broker.paper_account_snapshot import (
    PaperAccountSnapshotStatus,
    read_fake_paper_account_snapshot,
)
from src.broker.paper_snapshot_reconciliation import (
    PaperSnapshotReconciliationStatus,
    reconcile_paper_account_snapshot,
)
from src.broker.paper_reconciliation_report import (
    PaperReconciliationReportStatus,
    render_paper_reconciliation_report,
)


class PaperObservationWorkflowStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    OBSERVATION_READY_NO_DIFFERENCE = "OBSERVATION_READY_NO_DIFFERENCE"
    OBSERVATION_READY_DIFFERENCE_FOUND = "OBSERVATION_READY_DIFFERENCE_FOUND"
    BLOCKED_CREDENTIAL = "BLOCKED_CREDENTIAL"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_SNAPSHOT = "BLOCKED_SNAPSHOT"
    BLOCKED_RECONCILIATION = "BLOCKED_RECONCILIATION"
    BLOCKED_REPORT = "BLOCKED_REPORT"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"


@dataclasses.dataclass(frozen=True)
class PaperObservationWorkflowResult:
    result: str
    status: PaperObservationWorkflowStatus
    blocker: str | None
    request_id: str | None
    credential_status: CredentialMetadataStatus | None
    environment_status: AccountEnvironmentStatus | None
    snapshot_status: PaperAccountSnapshotStatus | None
    reconciliation_status: PaperSnapshotReconciliationStatus | None
    report_status: PaperReconciliationReportStatus | None
    summary: str | None
    financial_lines: tuple[str, ...] | None
    position_lines: tuple[str, ...] | None
    open_order_lines: tuple[str, ...] | None
    stages_checked: tuple[str, ...]
    stages_failed: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


def _all_child_flags_false(child) -> bool:
    return (
        child.broker_calls_made is False
        and child.credentials_read is False
        and child.network_calls_made is False
        and child.order_action_requested is False
        and child.live_trading_allowed is False
    )


def _blocked(
    status: PaperObservationWorkflowStatus,
    blocker: str,
    checked: list[str],
    failed_stage: str,
    *,
    request_id: str | None = None,
    credential_status=None,
    environment_status=None,
    snapshot_status=None,
    reconciliation_status=None,
    report_status=None,
) -> PaperObservationWorkflowResult:
    return PaperObservationWorkflowResult(
        result="BLOCKED",
        status=status,
        blocker=blocker,
        request_id=request_id,
        credential_status=credential_status,
        environment_status=environment_status,
        snapshot_status=snapshot_status,
        reconciliation_status=reconciliation_status,
        report_status=report_status,
        summary=None,
        financial_lines=None,
        position_lines=None,
        open_order_lines=None,
        stages_checked=tuple(checked),
        stages_failed=(failed_stage,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def run_fake_paper_observation_workflow(
    *,
    credential_metadata: dict,
    adapter_environment: str,
    broker_reported_environment: str,
    snapshot: dict,
    expected_cash,
    expected_buying_power,
    expected_equity,
    expected_positions,
    expected_open_orders,
    expected_environment: str,
    request_id: str,
    requested_at_utc: str,
    now_utc: str,
    max_age_seconds: int,
) -> PaperObservationWorkflowResult:
    checked: list[str] = []

    preserved_request_id: str | None = (
        request_id if _is_non_empty_str(request_id) else None
    )

    # 0. top_level.schema — guard against shapes that would crash sub-calls.
    checked.append("top_level.schema")
    if not isinstance(credential_metadata, dict):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SCHEMA,
            "credential_metadata is not a dict",
            checked,
            "top_level.schema",
            request_id=preserved_request_id,
        )
    if not isinstance(snapshot, dict):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SCHEMA,
            "snapshot is not a dict",
            checked,
            "top_level.schema",
            request_id=preserved_request_id,
        )
    if not isinstance(expected_environment, str):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SCHEMA,
            "expected_environment is not a string",
            checked,
            "top_level.schema",
            request_id=preserved_request_id,
        )
    if not isinstance(now_utc, str):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SCHEMA,
            "now_utc is not a string",
            checked,
            "top_level.schema",
            request_id=preserved_request_id,
        )

    # 1. credential
    checked.append("credential")
    cred_result = validate_credential_metadata(
        credential_metadata,
        expected_environment=expected_environment,
        now_utc=now_utc,
    )
    if not _all_child_flags_false(cred_result):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SAFETY,
            "credential validation result has a safety flag set",
            checked,
            "credential",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
        )
    if cred_result.result != "PASS":
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_CREDENTIAL,
            cred_result.blocker or "credential validation blocked",
            checked,
            "credential",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
        )

    credential_environment = cred_result.declared_environment or ""

    # 2. environment
    checked.append("environment")
    env_result = verify_account_environment(
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        broker_reported_environment=broker_reported_environment,
    )
    if not _all_child_flags_false(env_result):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SAFETY,
            "environment guard result has a safety flag set",
            checked,
            "environment",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
        )
    if env_result.result != "PASS":
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_ENVIRONMENT,
            env_result.blocker or "environment verification blocked",
            checked,
            "environment",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
        )

    # 3. snapshot
    checked.append("snapshot")
    snap_result = read_fake_paper_account_snapshot(
        snapshot,
        expected_environment=expected_environment,
        credential_environment=credential_environment,
        adapter_environment=adapter_environment,
        request_id=request_id,
        requested_at_utc=requested_at_utc,
        max_age_seconds=max_age_seconds,
    )
    if not _all_child_flags_false(snap_result):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SAFETY,
            "snapshot reader result has a safety flag set",
            checked,
            "snapshot",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
        )
    if snap_result.result != "PASS":
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SNAPSHOT,
            snap_result.blocker or "snapshot read blocked",
            checked,
            "snapshot",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
        )

    # 4. reconciliation
    checked.append("reconciliation")
    recon_result = reconcile_paper_account_snapshot(
        snap_result,
        expected_cash=expected_cash,
        expected_buying_power=expected_buying_power,
        expected_equity=expected_equity,
        expected_positions=expected_positions,
        expected_open_orders=expected_open_orders,
    )
    if not _all_child_flags_false(recon_result):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SAFETY,
            "reconciliation result has a safety flag set",
            checked,
            "reconciliation",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
            reconciliation_status=recon_result.status,
        )
    if recon_result.result != "PASS":
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_RECONCILIATION,
            recon_result.blocker or "reconciliation blocked",
            checked,
            "reconciliation",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
            reconciliation_status=recon_result.status,
        )

    # 5. report
    checked.append("report")
    report_result = render_paper_reconciliation_report(recon_result)
    if not _all_child_flags_false(report_result):
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_SAFETY,
            "report renderer result has a safety flag set",
            checked,
            "report",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
            reconciliation_status=recon_result.status,
            report_status=report_result.status,
        )
    if report_result.result != "PASS":
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_REPORT,
            report_result.blocker or "report rendering blocked",
            checked,
            "report",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
            reconciliation_status=recon_result.status,
            report_status=report_result.status,
        )

    if report_result.status is PaperReconciliationReportStatus.REPORT_READY_DIFFERENCE_FOUND:
        final_status = PaperObservationWorkflowStatus.OBSERVATION_READY_DIFFERENCE_FOUND
    elif report_result.status is PaperReconciliationReportStatus.REPORT_READY_NO_DIFFERENCE:
        final_status = PaperObservationWorkflowStatus.OBSERVATION_READY_NO_DIFFERENCE
    else:
        return _blocked(
            PaperObservationWorkflowStatus.BLOCKED_REPORT,
            "report renderer returned an unexpected status",
            checked,
            "report",
            request_id=preserved_request_id,
            credential_status=cred_result.status,
            environment_status=env_result.status,
            snapshot_status=snap_result.status,
            reconciliation_status=recon_result.status,
            report_status=report_result.status,
        )

    return PaperObservationWorkflowResult(
        result="PASS",
        status=final_status,
        blocker=None,
        request_id=preserved_request_id,
        credential_status=cred_result.status,
        environment_status=env_result.status,
        snapshot_status=snap_result.status,
        reconciliation_status=recon_result.status,
        report_status=report_result.status,
        summary=report_result.summary,
        financial_lines=report_result.financial_lines,
        position_lines=report_result.position_lines,
        open_order_lines=report_result.open_order_lines,
        stages_checked=tuple(checked),
        stages_failed=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
