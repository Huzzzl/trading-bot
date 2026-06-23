"""Tests for pure offline paper observation workflow coordinator — S49."""

from __future__ import annotations

import copy
import dataclasses
import inspect
from unittest.mock import patch

import pytest

from src.broker.credential_metadata import CredentialMetadataStatus
from src.broker.account_environment_guard import AccountEnvironmentStatus
from src.broker.paper_account_snapshot import PaperAccountSnapshotStatus
from src.broker.paper_snapshot_reconciliation import PaperSnapshotReconciliationStatus
from src.broker.paper_reconciliation_report import PaperReconciliationReportStatus
from src.broker.fake_credential_provider import create_fake_paper_credential_metadata
from src.broker.fake_paper_adapter import (
    create_fake_paper_account_snapshot,
    create_fake_paper_adapter_metadata,
)
from src.broker.paper_observation_workflow import (
    PaperObservationWorkflowResult,
    PaperObservationWorkflowStatus,
    run_fake_paper_observation_workflow,
)

_NOW = "2026-06-01T00:01:00Z"
_REQUEST_ID = "req-wf-001"

WS = PaperObservationWorkflowStatus


def _credential_metadata(**overrides) -> dict:
    cred = create_fake_paper_credential_metadata(**overrides)
    return cred.metadata if cred.metadata is not None else {}


def _adapter_env():
    a = create_fake_paper_adapter_metadata()
    return a.adapter_environment, a.broker_reported_environment


_SENTINEL = object()


def _run_workflow(**overrides) -> PaperObservationWorkflowResult:
    adapter_env, broker_reported_env = _adapter_env()
    snap = overrides.pop("snapshot", _SENTINEL)
    if snap is _SENTINEL:
        snap = create_fake_paper_account_snapshot()
    cred_meta = overrides.pop("credential_metadata", _SENTINEL)
    if cred_meta is _SENTINEL:
        cred_meta = _credential_metadata()
    defaults = dict(
        credential_metadata=cred_meta,
        adapter_environment=adapter_env,
        broker_reported_environment=broker_reported_env,
        snapshot=snap,
        expected_cash=100000.0,
        expected_buying_power=100000.0,
        expected_equity=100000.0,
        expected_positions=[],
        expected_open_orders=[],
        expected_environment="paper",
        request_id=_REQUEST_ID,
        requested_at_utc=_NOW,
        now_utc=_NOW,
        max_age_seconds=300,
    )
    defaults.update(overrides)
    return run_fake_paper_observation_workflow(**defaults)


def _assert_all_safety_flags_false(*results):
    for r in results:
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


_ORDER_CHAIN = [
    ("src.research.paper_order_planner", "create_paper_order_plan"),
    ("src.research.paper_order_plan_validator", "validate_paper_order_plan"),
    ("src.research.paper_order_safety_gate", "evaluate_paper_order_safety_gate"),
    ("src.research.paper_order_lifecycle", "create_lifecycle_from_plan"),
    ("src.research.paper_order_lifecycle", "apply_lifecycle_event"),
    ("src.research.paper_dry_run_preview", "render_paper_dry_run_preview"),
    ("src.research.paper_audit_ledger", "append_audit_entry"),
]


class TestWorkflowStatusEnum:
    def test_has_expected_members(self):
        names = {m.name for m in PaperObservationWorkflowStatus}
        assert names == {
            "NOT_RUN",
            "OBSERVATION_READY_NO_DIFFERENCE",
            "OBSERVATION_READY_DIFFERENCE_FOUND",
            "BLOCKED_CREDENTIAL",
            "BLOCKED_ENVIRONMENT",
            "BLOCKED_SNAPSHOT",
            "BLOCKED_RECONCILIATION",
            "BLOCKED_REPORT",
            "BLOCKED_SCHEMA",
            "BLOCKED_SAFETY",
        }

    def test_member_count(self):
        assert len(PaperObservationWorkflowStatus) == 10

    def test_is_str_enum(self):
        for m in PaperObservationWorkflowStatus:
            assert isinstance(m, str)
            assert m.value == m.name


class TestWorkflowResultDataclass:
    def test_is_frozen(self):
        r = _run_workflow()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.result = "TAMPERED"  # type: ignore[misc]

    def test_has_expected_fields(self):
        field_names = {f.name for f in dataclasses.fields(PaperObservationWorkflowResult)}
        expected = {
            "result", "status", "blocker", "request_id",
            "credential_status", "environment_status", "snapshot_status",
            "reconciliation_status", "report_status",
            "summary", "financial_lines", "position_lines", "open_order_lines",
            "stages_checked", "stages_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_field_count(self):
        assert len(dataclasses.fields(PaperObservationWorkflowResult)) == 20


class TestCompleteNoDifferenceWorkflowPasses:
    def test_pass_status(self):
        r = _run_workflow()
        assert r.result == "PASS"
        assert r.status is WS.OBSERVATION_READY_NO_DIFFERENCE
        assert r.blocker is None

    def test_request_id_preserved(self):
        r = _run_workflow()
        assert r.request_id == _REQUEST_ID

    def test_stage_statuses(self):
        r = _run_workflow()
        assert r.credential_status is CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER
        assert r.environment_status is AccountEnvironmentStatus.VERIFIED_PAPER
        assert r.snapshot_status is PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER
        assert r.reconciliation_status is PaperSnapshotReconciliationStatus.RECONCILED_NO_DIFFERENCE
        assert r.report_status is PaperReconciliationReportStatus.REPORT_READY_NO_DIFFERENCE

    def test_report_lines_present(self):
        r = _run_workflow()
        assert r.summary is not None
        assert isinstance(r.financial_lines, tuple)
        assert len(r.financial_lines) == 3
        assert r.position_lines == ()
        assert r.open_order_lines == ()

    def test_no_difference_summary(self):
        r = _run_workflow()
        assert "no difference" in r.summary.lower()

    def test_safety_flags_false(self):
        r = _run_workflow()
        _assert_all_safety_flags_false(r)

    def test_stages_checked_exact_order(self):
        r = _run_workflow()
        assert r.stages_checked == (
            "top_level.schema",
            "credential",
            "environment",
            "snapshot",
            "reconciliation",
            "report",
        )
        assert r.stages_failed == ()


class TestCompleteDifferenceFoundWorkflowPasses:
    def test_pass_with_difference(self):
        snap = create_fake_paper_account_snapshot(cash=150000.0)
        r = _run_workflow(snapshot=snap)
        assert r.result == "PASS"
        assert r.status is WS.OBSERVATION_READY_DIFFERENCE_FOUND

    def test_difference_report_status(self):
        snap = create_fake_paper_account_snapshot(cash=150000.0)
        r = _run_workflow(snapshot=snap)
        assert r.report_status is PaperReconciliationReportStatus.REPORT_READY_DIFFERENCE_FOUND
        assert r.reconciliation_status is PaperSnapshotReconciliationStatus.RECONCILED_DIFFERENCE_FOUND

    def test_position_difference_line(self):
        snap = create_fake_paper_account_snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _run_workflow(snapshot=snap, expected_positions=[])
        assert any("SPY" in line for line in r.position_lines)

    def test_open_order_difference_line(self):
        snap = create_fake_paper_account_snapshot(open_orders=[{"id": "ord-1"}])
        r = _run_workflow(snapshot=snap, expected_open_orders=[])
        assert any("ord-1" in line for line in r.open_order_lines)

    def test_difference_safety_flags(self):
        snap = create_fake_paper_account_snapshot(cash=200000.0)
        r = _run_workflow(snapshot=snap)
        _assert_all_safety_flags_false(r)


class TestReportSummaryRetainsNotOrderSignalNotice:
    def test_diff_summary_explicitly_not_order_signal(self):
        snap = create_fake_paper_account_snapshot(cash=200000.0)
        r = _run_workflow(snapshot=snap)
        assert r.summary is not None
        notice_present = (
            "are not order signals" in r.summary
            or "observations only" in r.summary
        )
        assert notice_present


class TestCredentialBlockStopsDownstream:
    def test_invalid_credential_blocks(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad_metadata)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_CREDENTIAL

    def test_credential_block_no_downstream_statuses(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad_metadata)
        assert r.credential_status is CredentialMetadataStatus.BLOCKED_ROTATION
        assert r.environment_status is None
        assert r.snapshot_status is None
        assert r.reconciliation_status is None
        assert r.report_status is None

    def test_credential_block_no_report_content(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad_metadata)
        assert r.summary is None
        assert r.financial_lines is None
        assert r.position_lines is None
        assert r.open_order_lines is None

    def test_credential_block_stages_failed(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad_metadata)
        assert r.stages_failed == ("credential",)
        assert "credential" in r.stages_checked
        assert "environment" not in r.stages_checked

    def test_credential_block_does_not_invoke_environment(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        with patch("src.broker.paper_observation_workflow.verify_account_environment") as m:
            _run_workflow(credential_metadata=bad_metadata)
            assert m.call_count == 0

    def test_credential_block_does_not_invoke_snapshot(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        with patch("src.broker.paper_observation_workflow.read_fake_paper_account_snapshot") as m:
            _run_workflow(credential_metadata=bad_metadata)
            assert m.call_count == 0

    def test_credential_block_does_not_invoke_reconciliation(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        with patch("src.broker.paper_observation_workflow.reconcile_paper_account_snapshot") as m:
            _run_workflow(credential_metadata=bad_metadata)
            assert m.call_count == 0

    def test_credential_block_does_not_invoke_report(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        with patch("src.broker.paper_observation_workflow.render_paper_reconciliation_report") as m:
            _run_workflow(credential_metadata=bad_metadata)
            assert m.call_count == 0

    def test_credential_block_safety_flags(self):
        bad_metadata = copy.deepcopy(_credential_metadata())
        bad_metadata["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad_metadata)
        _assert_all_safety_flags_false(r)


class TestEnvironmentBlockStopsDownstream:
    def test_live_adapter_environment_blocks(self):
        r = _run_workflow(adapter_environment="live")
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_ENVIRONMENT

    def test_environment_block_statuses(self):
        r = _run_workflow(adapter_environment="live")
        assert r.credential_status is CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER
        assert r.environment_status is AccountEnvironmentStatus.BLOCKED_LIVE_ENVIRONMENT
        assert r.snapshot_status is None
        assert r.reconciliation_status is None
        assert r.report_status is None

    def test_environment_block_stages(self):
        r = _run_workflow(adapter_environment="live")
        assert r.stages_failed == ("environment",)
        assert "environment" in r.stages_checked
        assert "snapshot" not in r.stages_checked

    def test_environment_block_does_not_invoke_snapshot(self):
        with patch("src.broker.paper_observation_workflow.read_fake_paper_account_snapshot") as m:
            _run_workflow(adapter_environment="live")
            assert m.call_count == 0

    def test_environment_block_does_not_invoke_reconciliation(self):
        with patch("src.broker.paper_observation_workflow.reconcile_paper_account_snapshot") as m:
            _run_workflow(adapter_environment="live")
            assert m.call_count == 0

    def test_environment_block_does_not_invoke_report(self):
        with patch("src.broker.paper_observation_workflow.render_paper_reconciliation_report") as m:
            _run_workflow(adapter_environment="live")
            assert m.call_count == 0

    def test_environment_block_safety_flags(self):
        r = _run_workflow(adapter_environment="live")
        _assert_all_safety_flags_false(r)


class TestSnapshotBlockStopsDownstream:
    def test_inactive_account_status_blocks(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        r = _run_workflow(snapshot=snap)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SNAPSHOT

    def test_snapshot_block_statuses(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        r = _run_workflow(snapshot=snap)
        assert r.credential_status is CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER
        assert r.environment_status is AccountEnvironmentStatus.VERIFIED_PAPER
        assert r.snapshot_status is PaperAccountSnapshotStatus.BLOCKED_ACCOUNT_STATUS
        assert r.reconciliation_status is None
        assert r.report_status is None

    def test_snapshot_block_stages(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        r = _run_workflow(snapshot=snap)
        assert r.stages_failed == ("snapshot",)
        assert "snapshot" in r.stages_checked
        assert "reconciliation" not in r.stages_checked

    def test_snapshot_block_does_not_invoke_reconciliation(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        with patch("src.broker.paper_observation_workflow.reconcile_paper_account_snapshot") as m:
            _run_workflow(snapshot=snap)
            assert m.call_count == 0

    def test_snapshot_block_does_not_invoke_report(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        with patch("src.broker.paper_observation_workflow.render_paper_reconciliation_report") as m:
            _run_workflow(snapshot=snap)
            assert m.call_count == 0

    def test_snapshot_block_safety_flags(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        r = _run_workflow(snapshot=snap)
        _assert_all_safety_flags_false(r)


class TestReconciliationBlockStopsReport:
    def test_invalid_expected_cash_blocks(self):
        r = _run_workflow(expected_cash=-1.0)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_RECONCILIATION

    def test_reconciliation_block_statuses(self):
        r = _run_workflow(expected_cash=-1.0)
        assert r.credential_status is CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER
        assert r.environment_status is AccountEnvironmentStatus.VERIFIED_PAPER
        assert r.snapshot_status is PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER
        assert r.reconciliation_status is PaperSnapshotReconciliationStatus.BLOCKED_SCHEMA
        assert r.report_status is None

    def test_reconciliation_block_stages(self):
        r = _run_workflow(expected_cash=-1.0)
        assert r.stages_failed == ("reconciliation",)
        assert "reconciliation" in r.stages_checked
        assert "report" not in r.stages_checked

    def test_reconciliation_block_does_not_invoke_report(self):
        with patch("src.broker.paper_observation_workflow.render_paper_reconciliation_report") as m:
            _run_workflow(expected_cash=-1.0)
            assert m.call_count == 0

    def test_reconciliation_block_safety_flags(self):
        r = _run_workflow(expected_cash=-1.0)
        _assert_all_safety_flags_false(r)


class TestReportBlockMapsCorrectly:
    def test_report_block_via_mocked_renderer(self):
        from src.broker.paper_reconciliation_report import (
            PaperReconciliationReportResult,
            PaperReconciliationReportStatus,
        )
        blocked_report = PaperReconciliationReportResult(
            result="BLOCKED",
            status=PaperReconciliationReportStatus.BLOCKED_SCHEMA,
            blocker="forced report block",
            request_id=_REQUEST_ID,
            summary=None,
            financial_lines=None,
            position_lines=None,
            open_order_lines=None,
            criteria_checked=("input.reconciliation_type",),
            criteria_failed=("input.reconciliation_type",),
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )
        with patch(
            "src.broker.paper_observation_workflow.render_paper_reconciliation_report",
            return_value=blocked_report,
        ):
            r = _run_workflow()
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_REPORT
        assert r.report_status is PaperReconciliationReportStatus.BLOCKED_SCHEMA
        assert r.stages_failed == ("report",)
        assert r.summary is None
        assert r.financial_lines is None

    def test_report_block_safety_flags(self):
        from src.broker.paper_reconciliation_report import (
            PaperReconciliationReportResult,
            PaperReconciliationReportStatus,
        )
        blocked_report = PaperReconciliationReportResult(
            result="BLOCKED",
            status=PaperReconciliationReportStatus.BLOCKED_RECONCILIATION,
            blocker="forced",
            request_id=_REQUEST_ID,
            summary=None,
            financial_lines=None,
            position_lines=None,
            open_order_lines=None,
            criteria_checked=(),
            criteria_failed=(),
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )
        with patch(
            "src.broker.paper_observation_workflow.render_paper_reconciliation_report",
            return_value=blocked_report,
        ):
            r = _run_workflow()
        _assert_all_safety_flags_false(r)


class TestExactStageOrder:
    def test_pass_stages_in_exact_order(self):
        r = _run_workflow()
        assert r.stages_checked == (
            "top_level.schema", "credential", "environment",
            "snapshot", "reconciliation", "report",
        )

    def test_credential_block_stops_after_credential(self):
        bad = copy.deepcopy(_credential_metadata())
        bad["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad)
        assert r.stages_checked == ("top_level.schema", "credential")

    def test_environment_block_stops_after_environment(self):
        r = _run_workflow(adapter_environment="live")
        assert r.stages_checked == ("top_level.schema", "credential", "environment")

    def test_snapshot_block_stops_after_snapshot(self):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        r = _run_workflow(snapshot=snap)
        assert r.stages_checked == (
            "top_level.schema", "credential", "environment", "snapshot",
        )

    def test_reconciliation_block_stops_after_reconciliation(self):
        r = _run_workflow(expected_cash=-1.0)
        assert r.stages_checked == (
            "top_level.schema", "credential", "environment", "snapshot",
            "reconciliation",
        )


class TestMalformedTopLevelInputs:
    @pytest.mark.parametrize("bad", [None, "x", 42, ["k", "v"]])
    def test_non_dict_credential_metadata_blocks(self, bad):
        r = _run_workflow(credential_metadata=bad)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("bad", [None, "x", 42])
    def test_non_dict_snapshot_blocks(self, bad):
        r = _run_workflow(snapshot=bad)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("bad", [None, 42, ["paper"]])
    def test_non_string_expected_environment_blocks(self, bad):
        r = _run_workflow(expected_environment=bad)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("bad", [None, 42, ["x"]])
    def test_non_string_now_utc_blocks(self, bad):
        r = _run_workflow(now_utc=bad)
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SCHEMA

    def test_top_level_block_does_not_invoke_any_stage(self):
        targets = [
            "src.broker.paper_observation_workflow.validate_credential_metadata",
            "src.broker.paper_observation_workflow.verify_account_environment",
            "src.broker.paper_observation_workflow.read_fake_paper_account_snapshot",
            "src.broker.paper_observation_workflow.reconcile_paper_account_snapshot",
            "src.broker.paper_observation_workflow.render_paper_reconciliation_report",
        ]
        for target in targets:
            with patch(target) as m:
                _run_workflow(snapshot=None)
                assert m.call_count == 0

    def test_top_level_block_never_raises(self):
        for kwargs in [
            {"credential_metadata": None},
            {"snapshot": None},
            {"expected_environment": None},
            {"now_utc": None},
            {"credential_metadata": "x"},
            {"snapshot": 42},
        ]:
            r = _run_workflow(**kwargs)
            assert r.result == "BLOCKED"
            assert r.status is WS.BLOCKED_SCHEMA

    def test_top_level_block_safety_flags(self):
        r = _run_workflow(snapshot=None)
        _assert_all_safety_flags_false(r)

    def test_top_level_block_preserves_valid_request_id(self):
        r = _run_workflow(snapshot=None, request_id="req-x")
        assert r.request_id == "req-x"

    def test_top_level_block_drops_invalid_request_id(self):
        r = _run_workflow(snapshot=None, request_id="")
        assert r.request_id is None

    def test_top_level_block_drops_none_request_id(self):
        r = _run_workflow(snapshot=None, request_id=None)
        assert r.request_id is None

    def test_invalid_request_id_blocks_at_snapshot(self):
        r = _run_workflow(request_id="")
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SNAPSHOT


class TestTamperedChildSafetyFlagsBlock:
    def test_tampered_credential_safety_flag_blocks(self):
        from src.broker.credential_metadata import (
            CredentialMetadataValidationResult,
        )
        tampered = CredentialMetadataValidationResult(
            result="PASS",
            status=CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER,
            blocker=None,
            profile_name="x",
            declared_environment="paper",
            expires_at_utc="2099-01-01T00:00:00Z",
            rotation_required=False,
            criteria_checked=(),
            criteria_failed=(),
            broker_calls_made=True,  # tampered
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )
        with patch(
            "src.broker.paper_observation_workflow.validate_credential_metadata",
            return_value=tampered,
        ):
            r = _run_workflow()
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SAFETY
        assert r.stages_failed == ("credential",)
        _assert_all_safety_flags_false(r)

    def test_tampered_environment_safety_flag_blocks(self):
        from src.broker.account_environment_guard import (
            AccountEnvironmentVerificationResult,
        )
        tampered = AccountEnvironmentVerificationResult(
            result="PASS",
            status=AccountEnvironmentStatus.VERIFIED_PAPER,
            blocker=None,
            expected_environment="paper",
            credential_environment="paper",
            adapter_environment="paper",
            broker_reported_environment="paper",
            criteria_checked=(),
            criteria_failed=(),
            broker_calls_made=False,
            credentials_read=True,  # tampered
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )
        with patch(
            "src.broker.paper_observation_workflow.verify_account_environment",
            return_value=tampered,
        ):
            r = _run_workflow()
        assert r.result == "BLOCKED"
        assert r.status is WS.BLOCKED_SAFETY
        assert r.stages_failed == ("environment",)

    def test_tampered_snapshot_safety_flag_blocks(self):
        from src.broker.paper_account_snapshot import PaperAccountSnapshotResult
        tampered = PaperAccountSnapshotResult(
            result="PASS",
            status=PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER,
            blocker=None,
            environment="paper",
            account_status="active",
            cash=100000.0,
            buying_power=100000.0,
            equity=100000.0,
            positions=(),
            open_orders=(),
            market_clock={"is_open": True},
            broker_timestamp="2026-06-01T00:00:00Z",
            request_id=_REQUEST_ID,
            criteria_checked=(),
            criteria_failed=(),
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=True,  # tampered
            order_action_requested=False,
            live_trading_allowed=False,
        )
        with patch(
            "src.broker.paper_observation_workflow.read_fake_paper_account_snapshot",
            return_value=tampered,
        ):
            r = _run_workflow()
        assert r.status is WS.BLOCKED_SAFETY
        assert r.stages_failed == ("snapshot",)


class TestDeterministicOutputs:
    def test_no_difference_deterministic(self):
        r1 = _run_workflow()
        r2 = _run_workflow()
        assert r1 == r2

    def test_difference_deterministic(self):
        snap1 = create_fake_paper_account_snapshot(cash=150000.0)
        snap2 = create_fake_paper_account_snapshot(cash=150000.0)
        r1 = _run_workflow(snapshot=snap1)
        r2 = _run_workflow(snapshot=snap2)
        assert r1 == r2

    def test_credential_block_deterministic(self):
        bad = copy.deepcopy(_credential_metadata())
        bad["rotation_required"] = True
        r1 = _run_workflow(credential_metadata=bad)
        r2 = _run_workflow(credential_metadata=copy.deepcopy(bad))
        assert r1 == r2

    def test_top_level_block_deterministic(self):
        r1 = _run_workflow(snapshot=None)
        r2 = _run_workflow(snapshot=None)
        assert r1 == r2


class TestInputImmutability:
    def test_credential_metadata_not_mutated(self):
        cred = _credential_metadata()
        original = copy.deepcopy(cred)
        _run_workflow(credential_metadata=cred)
        assert cred == original

    def test_snapshot_dict_not_mutated(self):
        snap = create_fake_paper_account_snapshot(positions=[{"symbol": "SPY"}])
        original = copy.deepcopy(snap)
        _run_workflow(snapshot=snap)
        assert snap == original

    def test_expected_positions_list_not_mutated(self):
        positions = [{"symbol": "SPY", "qty": 10}]
        original = copy.deepcopy(positions)
        _run_workflow(expected_positions=positions)
        assert positions == original

    def test_expected_open_orders_list_not_mutated(self):
        orders = [{"id": "ord-1"}]
        original = copy.deepcopy(orders)
        _run_workflow(expected_open_orders=orders)
        assert orders == original


class TestRawChildObjectsNotRetained:
    def test_result_has_no_credential_field(self):
        r = _run_workflow()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "credential_result" not in field_names
        assert "credential_metadata" not in field_names

    def test_result_has_no_snapshot_field(self):
        r = _run_workflow()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "snapshot_result" not in field_names
        assert "snapshot" not in field_names

    def test_result_has_no_reconciliation_field(self):
        r = _run_workflow()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "reconciliation_result" not in field_names
        assert "reconciliation" not in field_names

    def test_result_has_no_report_field(self):
        r = _run_workflow()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "report_result" not in field_names
        assert "report" not in field_names

    def test_result_no_child_dataclass_types(self):
        from src.broker.credential_metadata import (
            CredentialMetadataValidationResult,
        )
        from src.broker.account_environment_guard import (
            AccountEnvironmentVerificationResult,
        )
        from src.broker.paper_account_snapshot import PaperAccountSnapshotResult
        from src.broker.paper_snapshot_reconciliation import (
            PaperSnapshotReconciliationResult,
        )
        from src.broker.paper_reconciliation_report import (
            PaperReconciliationReportResult,
        )
        forbidden_types = (
            CredentialMetadataValidationResult,
            AccountEnvironmentVerificationResult,
            PaperAccountSnapshotResult,
            PaperSnapshotReconciliationResult,
            PaperReconciliationReportResult,
        )
        r = _run_workflow()
        for f in dataclasses.fields(r):
            val = getattr(r, f.name)
            assert not isinstance(val, forbidden_types)


class TestImmutableOutputTuples:
    def test_result_is_frozen(self):
        r = _run_workflow()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = WS.NOT_RUN  # type: ignore[misc]

    def test_stages_checked_is_tuple(self):
        r = _run_workflow()
        assert isinstance(r.stages_checked, tuple)
        with pytest.raises(AttributeError):
            r.stages_checked.append("x")  # type: ignore[attr-defined]

    def test_stages_failed_is_tuple(self):
        r = _run_workflow(adapter_environment="live")
        assert isinstance(r.stages_failed, tuple)

    def test_financial_lines_is_tuple_on_pass(self):
        r = _run_workflow()
        assert isinstance(r.financial_lines, tuple)
        with pytest.raises(AttributeError):
            r.financial_lines.append("x")  # type: ignore[attr-defined]

    def test_position_lines_is_tuple_on_pass(self):
        snap = create_fake_paper_account_snapshot(positions=[{"symbol": "SPY"}])
        r = _run_workflow(snapshot=snap, expected_positions=[])
        assert isinstance(r.position_lines, tuple)


class TestSafetyFlagsAlwaysFalse:
    @pytest.mark.parametrize("flag", [
        "broker_calls_made", "credentials_read", "network_calls_made",
        "order_action_requested", "live_trading_allowed",
    ])
    def test_flag_false_on_pass_no_diff(self, flag):
        r = _run_workflow()
        assert getattr(r, flag) is False

    @pytest.mark.parametrize("flag", [
        "broker_calls_made", "credentials_read", "network_calls_made",
        "order_action_requested", "live_trading_allowed",
    ])
    def test_flag_false_on_pass_diff(self, flag):
        snap = create_fake_paper_account_snapshot(cash=150000.0)
        r = _run_workflow(snapshot=snap)
        assert getattr(r, flag) is False

    @pytest.mark.parametrize("flag", [
        "broker_calls_made", "credentials_read", "network_calls_made",
        "order_action_requested", "live_trading_allowed",
    ])
    def test_flag_false_on_credential_block(self, flag):
        bad = copy.deepcopy(_credential_metadata())
        bad["rotation_required"] = True
        r = _run_workflow(credential_metadata=bad)
        assert getattr(r, flag) is False

    @pytest.mark.parametrize("flag", [
        "broker_calls_made", "credentials_read", "network_calls_made",
        "order_action_requested", "live_trading_allowed",
    ])
    def test_flag_false_on_environment_block(self, flag):
        r = _run_workflow(adapter_environment="live")
        assert getattr(r, flag) is False

    @pytest.mark.parametrize("flag", [
        "broker_calls_made", "credentials_read", "network_calls_made",
        "order_action_requested", "live_trading_allowed",
    ])
    def test_flag_false_on_snapshot_block(self, flag):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        r = _run_workflow(snapshot=snap)
        assert getattr(r, flag) is False

    @pytest.mark.parametrize("flag", [
        "broker_calls_made", "credentials_read", "network_calls_made",
        "order_action_requested", "live_trading_allowed",
    ])
    def test_flag_false_on_reconciliation_block(self, flag):
        r = _run_workflow(expected_cash=-1.0)
        assert getattr(r, flag) is False


class TestOrderChainNeverInvoked:
    @pytest.mark.parametrize("module_name,func_name", _ORDER_CHAIN)
    def test_no_diff_pass_does_not_invoke_chain(self, module_name, func_name):
        with patch(f"{module_name}.{func_name}") as m:
            r = _run_workflow()
            assert r.result == "PASS"
            assert m.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _ORDER_CHAIN)
    def test_diff_found_pass_does_not_invoke_chain(self, module_name, func_name):
        snap = create_fake_paper_account_snapshot(cash=200000.0)
        with patch(f"{module_name}.{func_name}") as m:
            r = _run_workflow(snapshot=snap)
            assert r.result == "PASS"
            assert r.status is WS.OBSERVATION_READY_DIFFERENCE_FOUND
            assert m.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _ORDER_CHAIN)
    def test_credential_block_does_not_invoke_chain(self, module_name, func_name):
        bad = copy.deepcopy(_credential_metadata())
        bad["rotation_required"] = True
        with patch(f"{module_name}.{func_name}") as m:
            r = _run_workflow(credential_metadata=bad)
            assert r.result == "BLOCKED"
            assert m.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _ORDER_CHAIN)
    def test_environment_block_does_not_invoke_chain(self, module_name, func_name):
        with patch(f"{module_name}.{func_name}") as m:
            r = _run_workflow(adapter_environment="live")
            assert r.result == "BLOCKED"
            assert m.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _ORDER_CHAIN)
    def test_snapshot_block_does_not_invoke_chain(self, module_name, func_name):
        snap = create_fake_paper_account_snapshot(account_status="disabled")
        with patch(f"{module_name}.{func_name}") as m:
            r = _run_workflow(snapshot=snap)
            assert r.result == "BLOCKED"
            assert m.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _ORDER_CHAIN)
    def test_reconciliation_block_does_not_invoke_chain(self, module_name, func_name):
        with patch(f"{module_name}.{func_name}") as m:
            r = _run_workflow(expected_cash=-1.0)
            assert r.result == "BLOCKED"
            assert m.call_count == 0

    def test_workflow_source_does_not_import_chain_modules(self):
        import src.broker.paper_observation_workflow as mod
        source = inspect.getsource(mod)
        for module_name, _ in _ORDER_CHAIN:
            short_name = module_name.split(".")[-1]
            assert short_name not in source, (
                f"workflow module references {short_name}"
            )


class TestSourceHygiene:
    _FORBIDDEN_PATTERNS = [
        "os.environ",
        "getenv",
        "open(",
        "Path(",
        "read_text",
        "write_text",
        "requests",
        "urllib",
        "aiohttp",
        "socket",
        "subprocess",
        "logging",
        "log" + "ger",
        "json.dump",
        "json.dumps",
        "Alpaca" + "Broker" + "Adapter",
        "submit" + "_" + "order",
        "place" + "_" + "order",
        "cancel" + "_" + "order",
        "replace" + "_" + "order",
        "modify" + "_" + "order",
        "close" + "_" + "position",
        "liquidate",
        "current_state",
        "paper_order_planner",
        "paper_order_plan_validator",
        "paper_order_safety_gate",
        "paper_order_lifecycle",
        "paper_dry_run_preview",
        "paper_audit_ledger",
        "paper_approval_validator",
        "fake_credential_provider",
        "Alpaca",
    ]

    @pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
    def test_no_forbidden_pattern_in_source(self, pattern):
        import src.broker.paper_observation_workflow as mod
        source = inspect.getsource(mod)
        assert pattern not in source, f"Found forbidden pattern '{pattern}'"

    def test_no_runtime_main_wiring(self):
        import src.broker.paper_observation_workflow as mod
        source = inspect.getsource(mod)
        assert "src.main" not in source
        assert "src.runtime" not in source
        assert "src.execution" not in source

    def test_src_imports_limited_to_broker_subpackage(self):
        import src.broker.paper_observation_workflow as mod
        source = inspect.getsource(mod)
        src_lines = [
            line for line in source.splitlines()
            if line.startswith("from src.") or line.startswith("import src.")
        ]
        for line in src_lines:
            assert "src.broker." in line

    def test_no_order_action_function_on_module(self):
        import src.broker.paper_observation_workflow as mod
        forbidden = [
            "submit" + "_" + "order",
            "place" + "_" + "order",
            "cancel" + "_" + "order",
            "advance" + "_" + "lifecycle",
            "approve" + "_" + "order",
            "append" + "_" + "audit_entry",
            "create" + "_" + "paper_order_plan",
            "evaluate" + "_" + "paper_order_safety_gate",
            "render" + "_" + "paper_dry_run_preview",
        ]
        for name in forbidden:
            assert not hasattr(mod, name)

    def test_result_has_no_forbidden_field_names(self):
        r = _run_workflow()
        forbidden = {
            "approval", "approval_artifact", "approved",
            "plan", "plan_id", "order_plan",
            "gate", "gate_status", "safety_gate",
            "lifecycle", "lifecycle_id", "lifecycle_status",
            "preview", "preview_id",
            "ledger", "ledger_id", "audit_entry",
            "submit", "submitted", "submission",
            "executor", "executed",
            "order_action", "order_id", "client_order_id",
            "broker_payload",
            "current_state",
            "account_id", "account_number",
        }
        field_names = {f.name for f in dataclasses.fields(r)}
        assert field_names & forbidden == set()

    def test_status_value_contains_no_chain_substrings(self):
        for s in PaperObservationWorkflowStatus:
            v = s.value
            assert "APPROVED" not in v
            assert "SUBMIT" not in v
            assert "EXECUTE" not in v
            assert "LIFECYCLE" not in v
            assert "PLAN" not in v
            assert "ORDER" not in v
