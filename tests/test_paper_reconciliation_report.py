"""Tests for pure offline paper reconciliation report renderer — S48."""

from __future__ import annotations

import copy
import dataclasses
import inspect

import pytest

from src.broker.fake_paper_adapter import create_fake_paper_account_snapshot
from src.broker.paper_account_snapshot import read_fake_paper_account_snapshot
from src.broker.paper_snapshot_reconciliation import (
    PaperSnapshotReconciliationResult,
    PaperSnapshotReconciliationStatus,
    reconcile_paper_account_snapshot,
)
from src.broker.paper_reconciliation_report import (
    PaperReconciliationReportResult,
    PaperReconciliationReportStatus,
    render_paper_reconciliation_report,
)

_NOW = "2026-06-01T00:01:00Z"
_REQUEST_ID = "req-rep-001"

RS = PaperReconciliationReportStatus
ReconS = PaperSnapshotReconciliationStatus


def _pass_snapshot(**overrides):
    snap = create_fake_paper_account_snapshot(**overrides)
    return read_fake_paper_account_snapshot(
        snap,
        expected_environment="paper",
        credential_environment="paper",
        adapter_environment="paper",
        request_id=_REQUEST_ID,
        requested_at_utc=_NOW,
        max_age_seconds=300,
    )


def _no_diff_reconciliation() -> PaperSnapshotReconciliationResult:
    return reconcile_paper_account_snapshot(
        _pass_snapshot(),
        expected_cash=100000.0,
        expected_buying_power=100000.0,
        expected_equity=100000.0,
        expected_positions=[],
        expected_open_orders=[],
    )


def _diff_reconciliation(**kwargs) -> PaperSnapshotReconciliationResult:
    snap_kwargs = kwargs.pop("snap_kwargs", {})
    snap = _pass_snapshot(**snap_kwargs)
    defaults = dict(
        expected_cash=100000.0,
        expected_buying_power=100000.0,
        expected_equity=100000.0,
        expected_positions=[],
        expected_open_orders=[],
    )
    defaults.update(kwargs)
    return reconcile_paper_account_snapshot(snap, **defaults)


def _blocked_reconciliation() -> PaperSnapshotReconciliationResult:
    return reconcile_paper_account_snapshot(
        _pass_snapshot(),
        expected_cash=-1.0,
        expected_buying_power=100000.0,
        expected_equity=100000.0,
        expected_positions=[],
        expected_open_orders=[],
    )


def _assert_all_safety_flags_false(*results):
    for r in results:
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


class TestReportStatusEnum:
    def test_has_expected_members(self):
        names = {m.name for m in PaperReconciliationReportStatus}
        assert names == {
            "NOT_RENDERED",
            "REPORT_READY_NO_DIFFERENCE",
            "REPORT_READY_DIFFERENCE_FOUND",
            "BLOCKED_RECONCILIATION",
            "BLOCKED_SCHEMA",
            "BLOCKED_SAFETY",
        }

    def test_member_count(self):
        assert len(PaperReconciliationReportStatus) == 6

    def test_is_str_enum(self):
        for m in PaperReconciliationReportStatus:
            assert isinstance(m, str)
            assert m.value == m.name


class TestReportResultDataclass:
    def test_is_frozen(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.result = "TAMPERED"  # type: ignore[misc]

    def test_has_expected_fields(self):
        field_names = {f.name for f in dataclasses.fields(PaperReconciliationReportResult)}
        expected = {
            "result", "status", "blocker", "request_id", "summary",
            "financial_lines", "position_lines", "open_order_lines",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_field_count(self):
        assert len(dataclasses.fields(PaperReconciliationReportResult)) == 15


class TestNoDifferenceReport:
    def test_no_difference_report_status(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.result == "PASS"
        assert r.status is RS.REPORT_READY_NO_DIFFERENCE
        assert r.blocker is None

    def test_no_difference_summary(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.summary is not None
        assert "no difference" in r.summary.lower()

    def test_no_difference_request_id_preserved(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.request_id == _REQUEST_ID

    def test_no_difference_financial_lines_present(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.financial_lines is not None
        assert len(r.financial_lines) == 3
        for line in r.financial_lines:
            assert "+0.00" in line or "0.00" in line

    def test_no_difference_position_lines_empty(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.position_lines == ()

    def test_no_difference_open_order_lines_empty(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.open_order_lines == ()

    def test_no_difference_safety_flags(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        _assert_all_safety_flags_false(r)

    def test_no_difference_criteria_checked(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert "input.reconciliation_type" in r.criteria_checked
        assert "input.reconciliation_pass" in r.criteria_checked
        assert "rendering.summary" in r.criteria_checked
        assert r.criteria_failed == ()


class TestFinancialDifferenceLines:
    def test_cash_difference_line(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 150000.0})
        r = render_paper_reconciliation_report(recon)
        assert r.status is RS.REPORT_READY_DIFFERENCE_FOUND
        assert any("cash_difference=+50000.00" in line for line in r.financial_lines)

    def test_negative_cash_difference_line(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 50000.0})
        r = render_paper_reconciliation_report(recon)
        assert any("cash_difference=-50000.00" in line for line in r.financial_lines)

    def test_buying_power_difference_line(self):
        recon = _diff_reconciliation(snap_kwargs={"buying_power": 80000.0})
        r = render_paper_reconciliation_report(recon)
        assert any("buying_power_difference=-20000.00" in line for line in r.financial_lines)

    def test_equity_difference_line(self):
        recon = _diff_reconciliation(snap_kwargs={"equity": 130000.0})
        r = render_paper_reconciliation_report(recon)
        assert any("equity_difference=+30000.00" in line for line in r.financial_lines)

    def test_financial_lines_always_three(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 120000.0})
        r = render_paper_reconciliation_report(recon)
        assert len(r.financial_lines) == 3


class TestPositionDifferenceLines:
    def test_position_added(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [{"symbol": "SPY", "qty": 10}]},
            expected_positions=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert len(r.position_lines) == 1
        assert "SPY" in r.position_lines[0]
        assert "extra_in_snapshot" in r.position_lines[0]

    def test_position_missing(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": []},
            expected_positions=[{"symbol": "QQQ", "qty": 5}],
        )
        r = render_paper_reconciliation_report(recon)
        assert len(r.position_lines) == 1
        assert "QQQ" in r.position_lines[0]
        assert "missing_from_snapshot" in r.position_lines[0]

    def test_position_changed(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [{"symbol": "SPY", "qty": 20}]},
            expected_positions=[{"symbol": "SPY", "qty": 10}],
        )
        r = render_paper_reconciliation_report(recon)
        assert len(r.position_lines) == 1
        assert "SPY" in r.position_lines[0]
        assert "changed" in r.position_lines[0]

    def test_position_label_prefix(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [{"symbol": "SPY"}]},
            expected_positions=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert r.position_lines[0].startswith("position ")


class TestOpenOrderDifferenceLines:
    def test_order_added(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": [{"id": "ord-1", "side": "buy"}]},
            expected_open_orders=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert len(r.open_order_lines) == 1
        assert "ord-1" in r.open_order_lines[0]
        assert "extra_in_snapshot" in r.open_order_lines[0]

    def test_order_missing(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": []},
            expected_open_orders=[{"id": "ord-2"}],
        )
        r = render_paper_reconciliation_report(recon)
        assert "ord-2" in r.open_order_lines[0]
        assert "missing_from_snapshot" in r.open_order_lines[0]

    def test_order_changed(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": [{"id": "o3", "qty": 20}]},
            expected_open_orders=[{"id": "o3", "qty": 10}],
        )
        r = render_paper_reconciliation_report(recon)
        assert "o3" in r.open_order_lines[0]
        assert "changed" in r.open_order_lines[0]

    def test_order_label_prefix(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": [{"id": "o1"}]},
            expected_open_orders=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert r.open_order_lines[0].startswith("open_order ")


class TestMultipleDifferences:
    def test_all_difference_types_rendered(self):
        recon = _diff_reconciliation(
            snap_kwargs={
                "cash": 120000.0,
                "buying_power": 80000.0,
                "equity": 110000.0,
                "positions": [{"symbol": "SPY", "qty": 10}],
                "open_orders": [{"id": "ord-1"}],
            },
        )
        r = render_paper_reconciliation_report(recon)
        assert r.status is RS.REPORT_READY_DIFFERENCE_FOUND
        assert len(r.financial_lines) == 3
        assert len(r.position_lines) == 1
        assert len(r.open_order_lines) == 1

    def test_difference_summary_explicitly_not_order_signal(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 150000.0})
        r = render_paper_reconciliation_report(recon)
        assert "not order signal" in r.summary.lower() or \
            "are not order signals" in r.summary.lower() or \
            "observations only" in r.summary.lower()


class TestDeterministicOrdering:
    def test_position_lines_sorted(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [
                {"symbol": "QQQ"}, {"symbol": "SPY"}, {"symbol": "IWM"},
            ]},
            expected_positions=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert list(r.position_lines) == sorted(r.position_lines)

    def test_open_order_lines_sorted(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": [
                {"id": "z-1"}, {"id": "a-1"}, {"id": "m-1"},
            ]},
            expected_open_orders=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert list(r.open_order_lines) == sorted(r.open_order_lines)

    def test_no_difference_deterministic(self):
        r1 = render_paper_reconciliation_report(_no_diff_reconciliation())
        r2 = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r1 == r2

    def test_difference_deterministic(self):
        recon1 = _diff_reconciliation(snap_kwargs={"cash": 150000.0})
        recon2 = _diff_reconciliation(snap_kwargs={"cash": 150000.0})
        r1 = render_paper_reconciliation_report(recon1)
        r2 = render_paper_reconciliation_report(recon2)
        assert r1 == r2

    def test_blocked_deterministic(self):
        recon = _blocked_reconciliation()
        r1 = render_paper_reconciliation_report(recon)
        r2 = render_paper_reconciliation_report(recon)
        assert r1 == r2


class TestImmutableTuples:
    def test_result_is_frozen(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = RS.NOT_RENDERED  # type: ignore[misc]

    def test_financial_lines_is_tuple(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert isinstance(r.financial_lines, tuple)
        with pytest.raises(AttributeError):
            r.financial_lines.append("x")  # type: ignore[attr-defined]

    def test_position_lines_is_tuple(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [{"symbol": "SPY"}]},
            expected_positions=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert isinstance(r.position_lines, tuple)
        with pytest.raises(AttributeError):
            r.position_lines.append("x")  # type: ignore[attr-defined]

    def test_open_order_lines_is_tuple(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": [{"id": "o1"}]},
            expected_open_orders=[],
        )
        r = render_paper_reconciliation_report(recon)
        assert isinstance(r.open_order_lines, tuple)
        with pytest.raises(AttributeError):
            r.open_order_lines.append("x")  # type: ignore[attr-defined]

    def test_criteria_checked_is_tuple(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert isinstance(r.criteria_checked, tuple)


class TestMalformedReconciliationPayloads:
    def test_none_input_blocks(self):
        r = render_paper_reconciliation_report(None)  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_non_reconciliation_type_blocks(self):
        r = render_paper_reconciliation_report({"not": "a recon"})  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("bad_value", [None, "", "   ", "\t"])
    def test_empty_or_none_request_id_blocks(self, bad_value):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, request_id=bad_value)
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("bad_value", [42, 3.14, True, {"id": "x"}, ["x"]])
    def test_non_string_request_id_blocks(self, bad_value):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, request_id=bad_value)
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "cash_difference", "buying_power_difference", "equity_difference",
    ])
    @pytest.mark.parametrize("bad_value", [
        None, "100", True, False, float("nan"), float("inf"), float("-inf"),
    ])
    def test_invalid_financial_diff_blocks(self, field, bad_value):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, **{field: bad_value})
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "cash_difference", "buying_power_difference", "equity_difference",
    ])
    @pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf")])
    def test_invalid_financial_diff_safety_flags(self, field, bad_value):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, **{field: bad_value})
        r = render_paper_reconciliation_report(tampered)
        _assert_all_safety_flags_false(r)

    @pytest.mark.parametrize("field", ["position_differences", "open_order_differences"])
    @pytest.mark.parametrize("bad_value", [None, "x", 42, [], {"k": "v"}])
    def test_invalid_diff_collection_blocks(self, field, bad_value):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, **{field: bad_value})
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_negative_financial_diff_passes(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 50000.0})
        r = render_paper_reconciliation_report(recon)
        assert r.result == "PASS"

    def test_zero_financial_diff_passes(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert r.result == "PASS"

    def test_non_dict_position_diff_blocks(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [{"symbol": "SPY"}]},
            expected_positions=[],
        )
        tampered = dataclasses.replace(recon, position_differences=("not-a-dict",))
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_non_dict_open_order_diff_blocks(self):
        recon = _diff_reconciliation(
            snap_kwargs={"open_orders": [{"id": "o1"}]},
            expected_open_orders=[],
        )
        tampered = dataclasses.replace(recon, open_order_differences=("not-a-dict",))
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_malformed_input_never_raises(self):
        recon = _no_diff_reconciliation()
        for tamper_kwargs in [
            {"request_id": None},
            {"request_id": ""},
            {"cash_difference": None},
            {"cash_difference": float("nan")},
            {"buying_power_difference": float("inf")},
            {"equity_difference": "100"},
            {"position_differences": None},
            {"open_order_differences": "x"},
            {"position_differences": ("not-a-dict",)},
        ]:
            tampered = dataclasses.replace(recon, **tamper_kwargs)
            r = render_paper_reconciliation_report(tampered)
            assert r.result == "BLOCKED"


class TestReconciliationStatusPayloadConsistency:
    @staticmethod
    def _zero_diff_recon() -> PaperSnapshotReconciliationResult:
        return _no_diff_reconciliation()

    @staticmethod
    def _real_diff_recon(**overrides) -> PaperSnapshotReconciliationResult:
        return _diff_reconciliation(**overrides)

    def test_no_difference_with_nonzero_cash_blocks(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, cash_difference=50.0)
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    def test_no_difference_with_nonzero_buying_power_blocks(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, buying_power_difference=-100.0)
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    def test_no_difference_with_nonzero_equity_blocks(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, equity_difference=25.5)
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    def test_no_difference_with_nonempty_positions_blocks(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(
            recon,
            position_differences=({"kind": "extra_in_snapshot", "key": "SPY", "actual": {"symbol": "SPY"}},),
        )
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    def test_no_difference_with_nonempty_open_orders_blocks(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(
            recon,
            open_order_differences=({"kind": "extra_in_snapshot", "key": "o1", "actual": {"id": "o1"}},),
        )
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    def test_difference_found_with_all_zero_empty_payload_blocks(self):
        recon = self._real_diff_recon(snap_kwargs={"cash": 200000.0})
        tampered = dataclasses.replace(
            recon,
            cash_difference=0.0,
            buying_power_difference=0.0,
            equity_difference=0.0,
            position_differences=(),
            open_order_differences=(),
        )
        assert tampered.status is ReconS.RECONCILED_DIFFERENCE_FOUND
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    @pytest.mark.parametrize("tamper_kwargs", [
        {"cash_difference": 1.0},
        {"buying_power_difference": -1.0},
        {"equity_difference": 0.01},
    ])
    def test_consistency_mismatch_deterministic(self, tamper_kwargs):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, **tamper_kwargs)
        r1 = render_paper_reconciliation_report(tampered)
        r2 = render_paper_reconciliation_report(tampered)
        assert r1 == r2

    def test_consistency_mismatch_no_report_content(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, cash_difference=50.0)
        r = render_paper_reconciliation_report(tampered)
        assert r.summary is None
        assert r.financial_lines is None
        assert r.position_lines is None
        assert r.open_order_lines is None

    def test_consistency_mismatch_safety_flags(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, cash_difference=50.0)
        r = render_paper_reconciliation_report(tampered)
        _assert_all_safety_flags_false(r)

    def test_consistency_mismatch_criterion_in_checked(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, equity_difference=10.0)
        r = render_paper_reconciliation_report(tampered)
        assert "input.reconciliation_consistency" in r.criteria_checked
        assert r.criteria_failed == ("input.reconciliation_consistency",)

    @pytest.mark.parametrize("module_name,func_name", [
        ("src.research.paper_order_planner", "create_paper_order_plan"),
        ("src.research.paper_order_plan_validator", "validate_paper_order_plan"),
        ("src.research.paper_order_safety_gate", "evaluate_paper_order_safety_gate"),
        ("src.research.paper_order_lifecycle", "create_lifecycle_from_plan"),
        ("src.research.paper_order_lifecycle", "apply_lifecycle_event"),
        ("src.research.paper_dry_run_preview", "render_paper_dry_run_preview"),
        ("src.research.paper_audit_ledger", "append_audit_entry"),
    ])
    def test_consistency_mismatch_does_not_invoke_chain(self, module_name, func_name):
        from unittest.mock import patch
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, cash_difference=50.0)
        with patch(f"{module_name}.{func_name}") as mocked:
            r = render_paper_reconciliation_report(tampered)
            assert r.result == "BLOCKED"
            assert mocked.call_count == 0

    def test_difference_found_with_zero_payload_does_not_invoke_chain(self):
        from unittest.mock import patch
        recon = self._real_diff_recon(snap_kwargs={"cash": 200000.0})
        tampered = dataclasses.replace(
            recon,
            cash_difference=0.0,
            buying_power_difference=0.0,
            equity_difference=0.0,
            position_differences=(),
            open_order_differences=(),
        )
        for target in [
            "src.research.paper_order_planner.create_paper_order_plan",
            "src.research.paper_order_plan_validator.validate_paper_order_plan",
            "src.research.paper_order_safety_gate.evaluate_paper_order_safety_gate",
            "src.research.paper_order_lifecycle.create_lifecycle_from_plan",
            "src.research.paper_order_lifecycle.apply_lifecycle_event",
            "src.research.paper_dry_run_preview.render_paper_dry_run_preview",
            "src.research.paper_audit_ledger.append_audit_entry",
        ]:
            with patch(target) as mocked:
                r = render_paper_reconciliation_report(tampered)
                assert r.result == "BLOCKED"
                assert mocked.call_count == 0

    def test_consistency_mismatch_never_silently_correct(self):
        recon = self._zero_diff_recon()
        tampered = dataclasses.replace(recon, cash_difference=50.0)
        r = render_paper_reconciliation_report(tampered)
        assert r.status is RS.BLOCKED_RECONCILIATION
        assert r.status is not RS.REPORT_READY_DIFFERENCE_FOUND
        assert r.status is not RS.REPORT_READY_NO_DIFFERENCE


class TestBlockedReconciliationRejected:
    def test_blocked_reconciliation_blocks(self):
        recon = _blocked_reconciliation()
        assert recon.result == "BLOCKED"
        r = render_paper_reconciliation_report(recon)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_RECONCILIATION

    def test_blocked_reconciliation_no_report_content(self):
        recon = _blocked_reconciliation()
        r = render_paper_reconciliation_report(recon)
        assert r.summary is None
        assert r.financial_lines is None
        assert r.position_lines is None
        assert r.open_order_lines is None

    def test_blocked_reconciliation_safety_flags(self):
        recon = _blocked_reconciliation()
        r = render_paper_reconciliation_report(recon)
        _assert_all_safety_flags_false(r)


class TestTamperedSafetyFlagsRejected:
    @pytest.mark.parametrize("flag", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_tampered_flag_blocks(self, flag):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, **{flag: True})
        r = render_paper_reconciliation_report(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SAFETY

    def test_tampered_safety_flag_result_no_content(self):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, credentials_read=True)
        r = render_paper_reconciliation_report(tampered)
        assert r.summary is None
        assert r.financial_lines is None

    def test_tampered_safety_flag_result_flags_still_false(self):
        recon = _no_diff_reconciliation()
        tampered = dataclasses.replace(recon, credentials_read=True)
        r = render_paper_reconciliation_report(tampered)
        _assert_all_safety_flags_false(r)


class TestInputNotMutated:
    def test_reconciliation_input_not_mutated(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 150000.0})
        original = copy.deepcopy(recon)
        render_paper_reconciliation_report(recon)
        assert recon == original

    def test_position_differences_input_not_mutated_in_diff(self):
        recon = _diff_reconciliation(
            snap_kwargs={"positions": [{"symbol": "SPY", "qty": 10}]},
            expected_positions=[],
        )
        original_diffs = copy.deepcopy(recon.position_differences)
        render_paper_reconciliation_report(recon)
        assert recon.position_differences == original_diffs


class TestRawObjectNotRetained:
    def test_result_does_not_contain_reconciliation(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "reconciliation_result" not in field_names
        assert "reconciliation" not in field_names
        assert "input" not in field_names
        assert "raw" not in field_names

    def test_result_fields_are_only_value_types(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        for f in dataclasses.fields(r):
            val = getattr(r, f.name)
            assert not isinstance(val, PaperSnapshotReconciliationResult)


class TestAllSafetyFlagsFalse:
    def test_no_difference_safety_flags(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        _assert_all_safety_flags_false(r)

    def test_difference_safety_flags(self):
        recon = _diff_reconciliation(snap_kwargs={"cash": 150000.0})
        r = render_paper_reconciliation_report(recon)
        _assert_all_safety_flags_false(r)

    def test_blocked_recon_safety_flags(self):
        r = render_paper_reconciliation_report(_blocked_reconciliation())
        _assert_all_safety_flags_false(r)

    def test_blocked_schema_safety_flags(self):
        r = render_paper_reconciliation_report(None)  # type: ignore[arg-type]
        _assert_all_safety_flags_false(r)

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_pass(self, field):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        assert getattr(r, field) is False

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_blocked(self, field):
        r = render_paper_reconciliation_report(_blocked_reconciliation())
        assert getattr(r, field) is False


class TestNoOrderChainInvocation:
    _CHAIN_FUNCTIONS = [
        ("src.research.paper_order_planner", "create_paper_order_plan"),
        ("src.research.paper_order_plan_validator", "validate_paper_order_plan"),
        ("src.research.paper_order_safety_gate", "evaluate_paper_order_safety_gate"),
        ("src.research.paper_order_lifecycle", "create_lifecycle_from_plan"),
        ("src.research.paper_order_lifecycle", "apply_lifecycle_event"),
        ("src.research.paper_dry_run_preview", "render_paper_dry_run_preview"),
        ("src.research.paper_audit_ledger", "append_audit_entry"),
    ]

    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_no_diff_does_not_invoke_chain(self, module_name, func_name):
        from unittest.mock import patch
        with patch(f"{module_name}.{func_name}") as mocked:
            r = render_paper_reconciliation_report(_no_diff_reconciliation())
            assert r.result == "PASS"
            assert mocked.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_diff_found_does_not_invoke_chain(self, module_name, func_name):
        from unittest.mock import patch
        recon = _diff_reconciliation(snap_kwargs={"cash": 200000.0})
        with patch(f"{module_name}.{func_name}") as mocked:
            r = render_paper_reconciliation_report(recon)
            assert r.result == "PASS"
            assert r.status is RS.REPORT_READY_DIFFERENCE_FOUND
            assert mocked.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_blocked_does_not_invoke_chain(self, module_name, func_name):
        from unittest.mock import patch
        with patch(f"{module_name}.{func_name}") as mocked:
            r = render_paper_reconciliation_report(_blocked_reconciliation())
            assert r.result == "BLOCKED"
            assert mocked.call_count == 0

    def test_module_does_not_import_chain_modules(self):
        import src.broker.paper_reconciliation_report as mod
        source = inspect.getsource(mod)
        for module_name, _ in self._CHAIN_FUNCTIONS:
            short_name = module_name.split(".")[-1]
            assert short_name not in source, (
                f"report module references {short_name}"
            )


class TestNoChainOrApprovalFields:
    _FORBIDDEN_FIELD_NAMES = {
        "approval", "approval_artifact",
        "approved", "approval_status",
        "plan", "plan_id", "order_plan",
        "gate", "gate_status", "safety_gate",
        "lifecycle", "lifecycle_id", "lifecycle_status",
        "preview", "preview_id",
        "ledger", "ledger_id", "audit_entry",
        "submit", "submission", "submitted",
        "execution", "executor", "executed",
        "order_action", "order_id", "client_order_id",
        "broker_payload",
        "current_state",
        "account_id", "account_number",
    }

    def test_pass_result_has_no_forbidden_fields(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        field_names = {f.name for f in dataclasses.fields(r)}
        assert field_names & self._FORBIDDEN_FIELD_NAMES == set()

    def test_status_value_has_no_action_words(self):
        r = render_paper_reconciliation_report(_no_diff_reconciliation())
        v = r.status.value
        assert "APPROVED" not in v
        assert "SUBMIT" not in v
        assert "EXECUTE" not in v
        assert "LIFECYCLE" not in v
        assert "PLAN" not in v
        assert "ORDER" not in v


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
    ]

    @pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
    def test_no_forbidden_pattern_in_source(self, pattern):
        import src.broker.paper_reconciliation_report as mod
        source = inspect.getsource(mod)
        assert pattern not in source, f"Found forbidden pattern '{pattern}'"

    def test_no_runtime_main_wiring(self):
        import src.broker.paper_reconciliation_report as mod
        source = inspect.getsource(mod)
        assert "src.main" not in source
        assert "src.runtime" not in source
        assert "src.execution" not in source

    def test_only_imports_reconciliation_from_src(self):
        import src.broker.paper_reconciliation_report as mod
        source = inspect.getsource(mod)
        src_lines = [
            line for line in source.splitlines()
            if line.startswith("from src.") or line.startswith("import src.")
        ]
        for line in src_lines:
            assert "paper_snapshot_reconciliation" in line, (
                f"unexpected src import: {line}"
            )

    def test_no_order_action_function_on_module(self):
        import src.broker.paper_reconciliation_report as mod
        forbidden = [
            "submit" + "_" + "order",
            "place" + "_" + "order",
            "cancel" + "_" + "order",
            "advance" + "_" + "lifecycle",
            "approve" + "_" + "order",
            "append" + "_" + "audit_entry",
        ]
        for name in forbidden:
            assert not hasattr(mod, name)
