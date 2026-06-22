"""Tests for pure offline paper snapshot reconciliation — S47."""

from __future__ import annotations

import copy
import dataclasses
import inspect

import pytest

from src.broker.fake_paper_adapter import create_fake_paper_account_snapshot
from src.broker.paper_account_snapshot import (
    PaperAccountSnapshotResult,
    PaperAccountSnapshotStatus,
    read_fake_paper_account_snapshot,
)
from src.broker.paper_snapshot_reconciliation import (
    PaperSnapshotReconciliationResult,
    PaperSnapshotReconciliationStatus,
    reconcile_paper_account_snapshot,
)

_NOW = "2026-06-01T00:01:00Z"
_REQUEST_ID = "req-rec-001"

RS = PaperSnapshotReconciliationStatus


def _pass_snapshot(**snap_overrides) -> PaperAccountSnapshotResult:
    snap = create_fake_paper_account_snapshot(**snap_overrides)
    return read_fake_paper_account_snapshot(
        snap,
        expected_environment="paper",
        credential_environment="paper",
        adapter_environment="paper",
        request_id=_REQUEST_ID,
        requested_at_utc=_NOW,
        max_age_seconds=300,
    )


_SENTINEL = object()


def _reconcile(snapshot=_SENTINEL, **kwargs):
    if snapshot is _SENTINEL:
        snapshot = _pass_snapshot()
    defaults = dict(
        expected_cash=100000.0,
        expected_buying_power=100000.0,
        expected_equity=100000.0,
        expected_positions=[],
        expected_open_orders=[],
    )
    defaults.update(kwargs)
    return reconcile_paper_account_snapshot(snapshot, **defaults)


def _assert_all_safety_flags_false(*results):
    for r in results:
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


class TestReconciliationStatusEnum:
    def test_has_expected_members(self):
        names = {m.name for m in PaperSnapshotReconciliationStatus}
        assert names == {
            "NOT_RECONCILED",
            "RECONCILED_NO_DIFFERENCE",
            "RECONCILED_DIFFERENCE_FOUND",
            "BLOCKED_SNAPSHOT",
            "BLOCKED_SCHEMA",
            "BLOCKED_SAFETY",
        }

    def test_member_count(self):
        assert len(PaperSnapshotReconciliationStatus) == 6

    def test_is_str_enum(self):
        for m in PaperSnapshotReconciliationStatus:
            assert isinstance(m, str)
            assert m.value == m.name


class TestReconciliationResultDataclass:
    def test_is_frozen(self):
        r = _reconcile()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.result = "TAMPERED"  # type: ignore[misc]

    def test_has_expected_fields(self):
        field_names = {f.name for f in dataclasses.fields(PaperSnapshotReconciliationResult)}
        expected = {
            "result", "status", "blocker", "request_id",
            "cash_difference", "buying_power_difference", "equity_difference",
            "position_differences", "open_order_differences",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_field_count(self):
        assert len(dataclasses.fields(PaperSnapshotReconciliationResult)) == 16


class TestExactMatchReconciledNoDifference:
    def test_exact_match_no_difference(self):
        r = _reconcile()
        assert r.result == "PASS"
        assert r.status is RS.RECONCILED_NO_DIFFERENCE
        assert r.blocker is None

    def test_exact_match_zero_differences(self):
        r = _reconcile()
        assert r.cash_difference == 0.0
        assert r.buying_power_difference == 0.0
        assert r.equity_difference == 0.0
        assert r.position_differences == ()
        assert r.open_order_differences == ()

    def test_exact_match_with_positions(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _reconcile(snap, expected_positions=[{"symbol": "SPY", "qty": 10}])
        assert r.status is RS.RECONCILED_NO_DIFFERENCE
        assert r.position_differences == ()

    def test_exact_match_with_open_orders(self):
        snap = _pass_snapshot(open_orders=[{"id": "ord-1", "side": "buy"}])
        r = _reconcile(snap, expected_open_orders=[{"id": "ord-1", "side": "buy"}])
        assert r.status is RS.RECONCILED_NO_DIFFERENCE
        assert r.open_order_differences == ()

    def test_exact_match_request_id_preserved(self):
        r = _reconcile()
        assert r.request_id == _REQUEST_ID

    def test_exact_match_criteria_checked(self):
        r = _reconcile()
        assert "input.snapshot_type" in r.criteria_checked
        assert "input.snapshot_pass" in r.criteria_checked
        assert "reconciliation.financial_values" in r.criteria_checked
        assert "reconciliation.positions" in r.criteria_checked
        assert "reconciliation.open_orders" in r.criteria_checked
        assert r.criteria_failed == ()

    def test_exact_match_safety_flags(self):
        r = _reconcile()
        _assert_all_safety_flags_false(r)


class TestFinancialValueDifferences:
    def test_cash_difference_positive(self):
        snap = _pass_snapshot(cash=150000.0)
        r = _reconcile(snap, expected_cash=100000.0)
        assert r.result == "PASS"
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.cash_difference == 50000.0

    def test_cash_difference_negative(self):
        snap = _pass_snapshot(cash=50000.0)
        r = _reconcile(snap, expected_cash=100000.0)
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.cash_difference == -50000.0

    def test_buying_power_difference(self):
        snap = _pass_snapshot(buying_power=80000.0)
        r = _reconcile(snap, expected_buying_power=100000.0)
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.buying_power_difference == -20000.0

    def test_equity_difference(self):
        snap = _pass_snapshot(equity=120000.0)
        r = _reconcile(snap, expected_equity=100000.0)
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.equity_difference == 20000.0

    def test_all_three_differences(self):
        snap = _pass_snapshot(cash=110000.0, buying_power=90000.0, equity=130000.0)
        r = _reconcile(
            snap,
            expected_cash=100000.0,
            expected_buying_power=100000.0,
            expected_equity=100000.0,
        )
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.cash_difference == 10000.0
        assert r.buying_power_difference == -10000.0
        assert r.equity_difference == 30000.0

    def test_zero_diff_when_equal(self):
        snap = _pass_snapshot(cash=42.5, buying_power=42.5, equity=42.5)
        r = _reconcile(
            snap,
            expected_cash=42.5,
            expected_buying_power=42.5,
            expected_equity=42.5,
            expected_positions=[],
            expected_open_orders=[],
        )
        assert r.status is RS.RECONCILED_NO_DIFFERENCE

    def test_financial_diff_safety_flags(self):
        snap = _pass_snapshot(cash=150000.0)
        r = _reconcile(snap, expected_cash=100000.0)
        _assert_all_safety_flags_false(r)


class TestPositionDifferences:
    def test_position_added_in_snapshot(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _reconcile(snap, expected_positions=[])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert len(r.position_differences) == 1
        assert r.position_differences[0]["kind"] == "extra_in_snapshot"
        assert r.position_differences[0]["key"] == "SPY"

    def test_position_missing_from_snapshot(self):
        snap = _pass_snapshot(positions=[])
        r = _reconcile(snap, expected_positions=[{"symbol": "SPY", "qty": 10}])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert len(r.position_differences) == 1
        assert r.position_differences[0]["kind"] == "missing_from_snapshot"
        assert r.position_differences[0]["key"] == "SPY"

    def test_position_changed(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY", "qty": 15}])
        r = _reconcile(snap, expected_positions=[{"symbol": "SPY", "qty": 10}])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert len(r.position_differences) == 1
        assert r.position_differences[0]["kind"] == "changed"
        assert r.position_differences[0]["key"] == "SPY"
        assert r.position_differences[0]["actual"] == {"symbol": "SPY", "qty": 15}
        assert r.position_differences[0]["expected"] == {"symbol": "SPY", "qty": 10}

    def test_multiple_position_differences(self):
        snap = _pass_snapshot(positions=[
            {"symbol": "SPY", "qty": 10},
            {"symbol": "QQQ", "qty": 5},
        ])
        r = _reconcile(snap, expected_positions=[
            {"symbol": "SPY", "qty": 10},
            {"symbol": "IWM", "qty": 8},
        ])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        keys = {d["key"] for d in r.position_differences}
        assert keys == {"QQQ", "IWM"}

    def test_position_difference_is_tuple(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _reconcile(snap, expected_positions=[])
        assert isinstance(r.position_differences, tuple)


class TestOpenOrderDifferences:
    def test_order_added_in_snapshot(self):
        snap = _pass_snapshot(open_orders=[{"id": "ord-1", "side": "buy"}])
        r = _reconcile(snap, expected_open_orders=[])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.open_order_differences[0]["kind"] == "extra_in_snapshot"
        assert r.open_order_differences[0]["key"] == "ord-1"

    def test_order_missing_from_snapshot(self):
        snap = _pass_snapshot(open_orders=[])
        r = _reconcile(snap, expected_open_orders=[{"id": "ord-1", "side": "buy"}])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.open_order_differences[0]["kind"] == "missing_from_snapshot"
        assert r.open_order_differences[0]["key"] == "ord-1"

    def test_order_changed(self):
        snap = _pass_snapshot(open_orders=[{"id": "ord-1", "side": "buy", "qty": 20}])
        r = _reconcile(snap, expected_open_orders=[{"id": "ord-1", "side": "buy", "qty": 10}])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.open_order_differences[0]["kind"] == "changed"
        assert r.open_order_differences[0]["key"] == "ord-1"

    def test_open_order_difference_is_tuple(self):
        snap = _pass_snapshot(open_orders=[{"id": "ord-1"}])
        r = _reconcile(snap, expected_open_orders=[])
        assert isinstance(r.open_order_differences, tuple)


class TestMultipleDifferenceTypesCombined:
    def test_financial_and_position_difference(self):
        snap = _pass_snapshot(cash=150000.0, positions=[{"symbol": "SPY", "qty": 10}])
        r = _reconcile(snap, expected_cash=100000.0, expected_positions=[])
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.cash_difference == 50000.0
        assert len(r.position_differences) == 1

    def test_all_difference_types_at_once(self):
        snap = _pass_snapshot(
            cash=110000.0,
            buying_power=90000.0,
            equity=120000.0,
            positions=[{"symbol": "SPY", "qty": 10}],
            open_orders=[{"id": "ord-1"}],
        )
        r = _reconcile(
            snap,
            expected_cash=100000.0,
            expected_buying_power=100000.0,
            expected_equity=100000.0,
            expected_positions=[],
            expected_open_orders=[],
        )
        assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
        assert r.cash_difference == 10000.0
        assert r.buying_power_difference == -10000.0
        assert r.equity_difference == 20000.0
        assert len(r.position_differences) == 1
        assert len(r.open_order_differences) == 1


class TestBlockedSnapshotRejected:
    def test_non_pass_snapshot_blocks(self):
        snap = _pass_snapshot(account_status="disabled")
        assert snap.result == "BLOCKED"
        r = _reconcile(snap)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SNAPSHOT

    def test_blocked_environment_snapshot_blocks(self):
        snap = read_fake_paper_account_snapshot(
            create_fake_paper_account_snapshot(),
            expected_environment="live",
            credential_environment="paper",
            adapter_environment="paper",
            request_id=_REQUEST_ID,
            requested_at_utc=_NOW,
            max_age_seconds=300,
        )
        assert snap.result == "BLOCKED"
        r = _reconcile(snap)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SNAPSHOT

    def test_blocked_snapshot_safety_flags(self):
        snap = _pass_snapshot(account_status="disabled")
        r = _reconcile(snap)
        _assert_all_safety_flags_false(r)

    def test_blocked_snapshot_no_differences_returned(self):
        snap = _pass_snapshot(account_status="disabled")
        r = _reconcile(snap)
        assert r.cash_difference is None
        assert r.position_differences is None

    def test_snapshot_with_safety_flag_set_blocks(self):
        good = _pass_snapshot()
        tampered = dataclasses.replace(good, broker_calls_made=True)
        r = _reconcile(tampered)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SAFETY

    @pytest.mark.parametrize("flag", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_safety_flag_set_blocks(self, flag):
        good = _pass_snapshot()
        tampered = dataclasses.replace(good, **{flag: True})
        r = _reconcile(tampered)
        assert r.status is RS.BLOCKED_SAFETY


class TestMalformedInputsRejected:
    def test_non_snapshot_type_blocks(self):
        r = _reconcile({"not": "a snapshot"})  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_none_snapshot_blocks(self):
        r = _reconcile(None)  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_negative_expected_value_blocks(self, field):
        r = _reconcile(**{field: -1.0})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_nan_expected_value_blocks(self, field):
        r = _reconcile(**{field: float("nan")})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_inf_expected_value_blocks(self, field):
        r = _reconcile(**{field: float("inf")})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_neg_inf_expected_value_blocks(self, field):
        r = _reconcile(**{field: float("-inf")})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_string_expected_value_blocks(self, field):
        r = _reconcile(**{field: "100000"})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_bool_expected_value_blocks(self, field):
        r = _reconcile(**{field: True})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_cash",
        "expected_buying_power",
        "expected_equity",
    ])
    def test_none_expected_value_blocks(self, field):
        r = _reconcile(**{field: None})
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_zero_expected_value_passes(self):
        snap = _pass_snapshot(cash=0.0)
        r = _reconcile(
            snap,
            expected_cash=0.0,
            expected_buying_power=100000.0,
            expected_equity=100000.0,
        )
        assert r.result == "PASS"

    @pytest.mark.parametrize("value", [
        "not-a-list",
        None,
        42,
        {"key": "value"},
    ])
    def test_non_collection_expected_positions_blocks(self, value):
        r = _reconcile(expected_positions=value)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("value", [
        "not-a-list",
        None,
        42,
        {"key": "value"},
    ])
    def test_non_collection_expected_open_orders_blocks(self, value):
        r = _reconcile(expected_open_orders=value)
        assert r.result == "BLOCKED"
        assert r.status is RS.BLOCKED_SCHEMA

    def test_malformed_input_safety_flags(self):
        r = _reconcile(expected_cash=-1.0)
        _assert_all_safety_flags_false(r)

    def test_tuple_expected_positions_pass(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY"}])
        r = _reconcile(snap, expected_positions=({"symbol": "SPY"},))
        assert r.result == "PASS"
        assert r.status is RS.RECONCILED_NO_DIFFERENCE


class TestDeterministicOutput:
    def test_no_difference_deterministic(self):
        r1 = _reconcile()
        r2 = _reconcile()
        assert r1 == r2

    def test_difference_deterministic(self):
        snap1 = _pass_snapshot(cash=150000.0)
        snap2 = _pass_snapshot(cash=150000.0)
        r1 = _reconcile(snap1, expected_cash=100000.0)
        r2 = _reconcile(snap2, expected_cash=100000.0)
        assert r1 == r2

    def test_blocked_deterministic(self):
        r1 = _reconcile(expected_cash=-1.0)
        r2 = _reconcile(expected_cash=-1.0)
        assert r1 == r2

    def test_position_difference_order_deterministic(self):
        snap = _pass_snapshot(positions=[
            {"symbol": "QQQ", "qty": 5},
            {"symbol": "SPY", "qty": 10},
            {"symbol": "IWM", "qty": 8},
        ])
        r1 = _reconcile(snap, expected_positions=[])
        r2 = _reconcile(snap, expected_positions=[])
        assert r1.position_differences == r2.position_differences
        keys = [d["key"] for d in r1.position_differences]
        assert keys == sorted(keys)


class TestImmutableOutput:
    def test_result_is_frozen(self):
        r = _reconcile()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = RS.NOT_RECONCILED  # type: ignore[misc]

    def test_position_differences_is_tuple(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY"}])
        r = _reconcile(snap, expected_positions=[])
        assert isinstance(r.position_differences, tuple)
        with pytest.raises(AttributeError):
            r.position_differences.append({})  # type: ignore[attr-defined]

    def test_open_order_differences_is_tuple(self):
        snap = _pass_snapshot(open_orders=[{"id": "o1"}])
        r = _reconcile(snap, expected_open_orders=[])
        assert isinstance(r.open_order_differences, tuple)
        with pytest.raises(AttributeError):
            r.open_order_differences.append({})  # type: ignore[attr-defined]

    def test_criteria_checked_is_tuple(self):
        r = _reconcile()
        assert isinstance(r.criteria_checked, tuple)


class TestInputNotMutated:
    def test_expected_positions_list_not_mutated(self):
        positions = [{"symbol": "SPY", "qty": 10}]
        original = copy.deepcopy(positions)
        _reconcile(expected_positions=positions)
        assert positions == original

    def test_expected_open_orders_list_not_mutated(self):
        orders = [{"id": "ord-1"}]
        original = copy.deepcopy(orders)
        _reconcile(expected_open_orders=orders)
        assert orders == original

    def test_snapshot_positions_not_mutated_via_diff(self):
        snap = _pass_snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _reconcile(snap, expected_positions=[])
        original_actual = r.position_differences[0]["actual"]
        original_actual["qty"] = 999
        assert snap.positions[0]["qty"] == 10

    def test_expected_position_in_diff_is_deep_copy(self):
        expected_positions = [{"symbol": "SPY", "qty": 10}]
        snap = _pass_snapshot(positions=[])
        r = _reconcile(snap, expected_positions=expected_positions)
        r.position_differences[0]["expected"]["qty"] = 999
        assert expected_positions[0]["qty"] == 10


class TestAllSafetyFlagsFalse:
    def test_pass_no_difference_safety_flags(self):
        r = _reconcile()
        _assert_all_safety_flags_false(r)

    def test_pass_difference_safety_flags(self):
        snap = _pass_snapshot(cash=200000.0)
        r = _reconcile(snap, expected_cash=100000.0)
        _assert_all_safety_flags_false(r)

    def test_blocked_snapshot_safety_flags(self):
        snap = _pass_snapshot(account_status="disabled")
        r = _reconcile(snap)
        _assert_all_safety_flags_false(r)

    def test_blocked_schema_safety_flags(self):
        r = _reconcile(expected_cash=-1.0)
        _assert_all_safety_flags_false(r)

    def test_blocked_safety_flag_safety_flags(self):
        good = _pass_snapshot()
        tampered = dataclasses.replace(good, credentials_read=True)
        r = _reconcile(tampered)
        _assert_all_safety_flags_false(r)

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_pass(self, field):
        r = _reconcile()
        assert getattr(r, field) is False

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_blocked(self, field):
        r = _reconcile(expected_cash=-1.0)
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
    def test_reconcile_pass_no_difference_does_not_invoke(self, module_name, func_name):
        from unittest.mock import patch
        with patch(f"{module_name}.{func_name}") as mocked:
            r = _reconcile()
            assert r.result == "PASS"
            assert mocked.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_reconcile_pass_difference_found_does_not_invoke(self, module_name, func_name):
        from unittest.mock import patch
        snap = _pass_snapshot(cash=200000.0)
        with patch(f"{module_name}.{func_name}") as mocked:
            r = _reconcile(snap, expected_cash=100000.0)
            assert r.result == "PASS"
            assert r.status is RS.RECONCILED_DIFFERENCE_FOUND
            assert mocked.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_reconcile_blocked_does_not_invoke(self, module_name, func_name):
        from unittest.mock import patch
        with patch(f"{module_name}.{func_name}") as mocked:
            r = _reconcile(expected_cash=-1.0)
            assert r.result == "BLOCKED"
            assert mocked.call_count == 0

    def test_module_does_not_import_chain_modules(self):
        import src.broker.paper_snapshot_reconciliation as mod
        source = inspect.getsource(mod)
        for module_name, _ in self._CHAIN_FUNCTIONS:
            short_name = module_name.split(".")[-1]
            assert short_name not in source


class TestResultHasNoChainFields:
    _FORBIDDEN_FIELD_NAMES = {
        "approval", "approval_artifact", "approval_artifact_hash",
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

    def test_pass_result_has_no_forbidden_field_names(self):
        r = _reconcile()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert field_names & self._FORBIDDEN_FIELD_NAMES == set()

    def test_blocked_result_has_no_forbidden_field_names(self):
        r = _reconcile(expected_cash=-1.0)
        field_names = {f.name for f in dataclasses.fields(r)}
        assert field_names & self._FORBIDDEN_FIELD_NAMES == set()

    def test_status_value_contains_no_action_words(self):
        r = _reconcile()
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
        "Alpaca" + "Broker" + "Adapter",
        "submit" + "_" + "order",
        "place" + "_" + "order",
        "cancel" + "_" + "order",
        "replace" + "_" + "order",
        "modify" + "_" + "order",
        "close" + "_" + "position",
        "liquidate",
    ]

    @pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
    def test_no_forbidden_pattern_in_source(self, pattern):
        import src.broker.paper_snapshot_reconciliation as mod
        source = inspect.getsource(mod)
        assert pattern not in source, f"Found forbidden pattern '{pattern}'"

    def test_no_runtime_main_wiring(self):
        import src.broker.paper_snapshot_reconciliation as mod
        source = inspect.getsource(mod)
        assert "src.main" not in source
        assert "src.runtime" not in source
        assert "src.execution" not in source

    def test_no_current_state_reference(self):
        import src.broker.paper_snapshot_reconciliation as mod
        source = inspect.getsource(mod)
        assert "current_state" not in source

    def test_only_imports_snapshot_module_from_src(self):
        import src.broker.paper_snapshot_reconciliation as mod
        source = inspect.getsource(mod)
        src_lines = [
            line for line in source.splitlines()
            if line.startswith("from src.") or line.startswith("import src.")
        ]
        for line in src_lines:
            assert "paper_account_snapshot" in line, (
                f"unexpected src import: {line}"
            )


class TestNoOrderActionMethod:
    def test_no_order_action_function_on_module(self):
        import src.broker.paper_snapshot_reconciliation as mod
        forbidden = [
            "submit" + "_" + "order",
            "place" + "_" + "order",
            "cancel" + "_" + "order",
            "replace" + "_" + "order",
            "modify" + "_" + "order",
            "close" + "_" + "position",
            "liquidate",
            "advance" + "_" + "lifecycle",
            "approve" + "_" + "order",
        ]
        for name in forbidden:
            assert not hasattr(mod, name)

    def test_result_has_no_action_method(self):
        r = _reconcile()
        for method in ["submit", "place", "cancel", "approve", "execute", "advance"]:
            if hasattr(r, method):
                assert not callable(getattr(r, method))


class TestNoBrokerPayloadOrAccountIdentifier:
    def test_result_has_no_account_id_field(self):
        r = _reconcile()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "account" + "_" + "id" not in field_names
        assert "account" + "_" + "number" not in field_names
        assert "broker_payload" not in field_names
