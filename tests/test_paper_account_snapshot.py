"""Tests for pure offline paper account snapshot reader — S45."""

from __future__ import annotations

import copy
import dataclasses
import inspect
import math

import pytest

from src.broker.paper_account_snapshot import (
    PaperAccountSnapshotResult,
    PaperAccountSnapshotStatus,
    read_fake_paper_account_snapshot,
)
from src.broker.fake_paper_adapter import create_fake_paper_account_snapshot

_NOW = "2026-06-01T00:01:00Z"
_REQUEST_ID = "req-test-001"

PASS = PaperAccountSnapshotStatus


def _snapshot(**overrides):
    return create_fake_paper_account_snapshot(**overrides)


def _read(snapshot=None, **kwargs):
    if snapshot is None:
        snapshot = _snapshot()
    defaults = dict(
        expected_environment="paper",
        credential_environment="paper",
        adapter_environment="paper",
        request_id=_REQUEST_ID,
        requested_at_utc=_NOW,
        max_age_seconds=300,
    )
    defaults.update(kwargs)
    return read_fake_paper_account_snapshot(snapshot, **defaults)


def _assert_all_safety_flags_false(*results):
    for r in results:
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


class TestPaperAccountSnapshotStatusEnum:
    def test_has_expected_members(self):
        names = {m.name for m in PaperAccountSnapshotStatus}
        expected = {
            "NOT_READ",
            "SNAPSHOT_READY_PAPER",
            "BLOCKED_SCHEMA",
            "BLOCKED_ENVIRONMENT",
            "BLOCKED_ACCOUNT_STATUS",
            "BLOCKED_STALE_RESPONSE",
            "BLOCKED_SAFETY",
        }
        assert names == expected

    def test_member_count(self):
        assert len(PaperAccountSnapshotStatus) == 7

    def test_is_str_enum(self):
        for m in PaperAccountSnapshotStatus:
            assert isinstance(m, str)
            assert m.value == m.name


class TestPaperAccountSnapshotResultDataclass:
    def test_is_frozen(self):
        r = _read()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.result = "TAMPERED"  # type: ignore[misc]

    def test_has_expected_fields(self):
        field_names = {f.name for f in dataclasses.fields(PaperAccountSnapshotResult)}
        expected = {
            "result", "status", "blocker", "environment", "account_status",
            "cash", "buying_power", "equity", "positions", "open_orders",
            "market_clock", "broker_timestamp", "request_id",
            "criteria_checked", "criteria_failed",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        }
        assert field_names == expected

    def test_field_count(self):
        assert len(dataclasses.fields(PaperAccountSnapshotResult)) == 20


class TestValidPaperSnapshotPasses:
    def test_valid_snapshot_passes(self):
        r = _read()
        assert r.result == "PASS"
        assert r.status is PASS.SNAPSHOT_READY_PAPER
        assert r.blocker is None

    def test_pass_environment_is_paper(self):
        r = _read()
        assert r.environment == "paper"

    def test_pass_account_status_is_active(self):
        r = _read()
        assert r.account_status == "active"

    def test_pass_financial_values(self):
        r = _read()
        assert r.cash == 100000.0
        assert r.buying_power == 100000.0
        assert r.equity == 100000.0

    def test_pass_positions_as_tuple(self):
        r = _read()
        assert isinstance(r.positions, tuple)

    def test_pass_open_orders_as_tuple(self):
        r = _read()
        assert isinstance(r.open_orders, tuple)

    def test_pass_market_clock_is_dict(self):
        r = _read()
        assert isinstance(r.market_clock, dict)

    def test_pass_broker_timestamp(self):
        r = _read()
        assert r.broker_timestamp == "2026-06-01T00:00:00Z"

    def test_pass_request_id(self):
        r = _read()
        assert r.request_id == _REQUEST_ID

    def test_pass_criteria_checked(self):
        r = _read()
        assert len(r.criteria_checked) >= 10
        assert r.criteria_failed == ()

    def test_pass_safety_flags(self):
        r = _read()
        _assert_all_safety_flags_false(r)

    def test_custom_financial_values(self):
        snap = _snapshot(cash=500.0, buying_power=400.0, equity=600.0)
        r = _read(snap)
        assert r.result == "PASS"
        assert r.cash == 500.0
        assert r.buying_power == 400.0
        assert r.equity == 600.0

    def test_zero_financial_values_pass(self):
        snap = _snapshot(cash=0.0, buying_power=0.0, equity=0.0)
        r = _read(snap)
        assert r.result == "PASS"

    def test_positions_with_items(self):
        snap = _snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _read(snap)
        assert r.result == "PASS"
        assert len(r.positions) == 1
        assert r.positions[0]["symbol"] == "SPY"


class TestEnvironmentBlocks:
    @pytest.mark.parametrize("env_field,env_value", [
        ("expected_environment", "live"),
        ("credential_environment", "live"),
        ("adapter_environment", "live"),
    ])
    def test_live_environment_blocks(self, env_field, env_value):
        r = _read(**{env_field: env_value})
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ENVIRONMENT

    def test_live_broker_reported_environment_blocks(self):
        snap = _snapshot(broker_reported_environment="live")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ENVIRONMENT

    @pytest.mark.parametrize("env", ["sandbox", "demo", "staging", "PAPER"])
    def test_ambiguous_environment_blocks(self, env):
        r = _read(expected_environment=env)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ENVIRONMENT

    @pytest.mark.parametrize("env", ["sandbox", "demo", "staging", "PAPER"])
    def test_ambiguous_broker_reported_blocks(self, env):
        snap = _snapshot(broker_reported_environment=env)
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ENVIRONMENT

    def test_environment_block_safety_flags(self):
        r = _read(expected_environment="live")
        _assert_all_safety_flags_false(r)


class TestAccountStatusBlocks:
    @pytest.mark.parametrize("status", ["inactive", "disabled", "closed", "suspended"])
    def test_non_active_status_blocks(self, status):
        snap = _snapshot(account_status=status)
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ACCOUNT_STATUS

    def test_empty_account_status_blocks(self):
        snap = _snapshot(account_status="")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ACCOUNT_STATUS

    def test_case_sensitive_active(self):
        snap = _snapshot(account_status="Active")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_ACCOUNT_STATUS

    def test_account_status_block_safety_flags(self):
        snap = _snapshot(account_status="disabled")
        r = _read(snap)
        _assert_all_safety_flags_false(r)


class TestMalformedOrMissingFieldsBlock:
    @pytest.mark.parametrize("missing_field", [
        "broker_reported_environment",
        "account_status",
        "cash",
        "buying_power",
        "equity",
        "positions",
        "open_orders",
        "market_clock",
        "broker_timestamp",
    ])
    def test_missing_required_field_blocks(self, missing_field):
        snap = _snapshot()
        del snap[missing_field]
        r = _read(snap)
        assert r.result == "BLOCKED"

    def test_non_dict_snapshot_blocks(self):
        r = read_fake_paper_account_snapshot(
            "not-a-dict",  # type: ignore[arg-type]
            expected_environment="paper",
            credential_environment="paper",
            adapter_environment="paper",
            request_id=_REQUEST_ID,
            requested_at_utc=_NOW,
            max_age_seconds=300,
        )
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_positions_not_list_blocks(self):
        snap = _snapshot()
        snap["positions"] = "not-a-list"
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_open_orders_not_list_blocks(self):
        snap = _snapshot()
        snap["open_orders"] = "not-a-list"
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_market_clock_not_dict_blocks(self):
        snap = _snapshot()
        snap["market_clock"] = "not-a-dict"
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA


class TestFinancialValueBlocks:
    @pytest.mark.parametrize("field", ["cash", "buying_power", "equity"])
    def test_negative_value_blocks(self, field):
        snap = _snapshot(**{field: -1.0})
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", ["cash", "buying_power", "equity"])
    def test_nan_value_blocks(self, field):
        snap = _snapshot(**{field: float("nan")})
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", ["cash", "buying_power", "equity"])
    def test_inf_value_blocks(self, field):
        snap = _snapshot(**{field: float("inf")})
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", ["cash", "buying_power", "equity"])
    def test_negative_inf_value_blocks(self, field):
        snap = _snapshot(**{field: float("-inf")})
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", ["cash", "buying_power", "equity"])
    def test_string_value_blocks(self, field):
        snap = _snapshot()
        snap[field] = "not-a-number"
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", ["cash", "buying_power", "equity"])
    def test_bool_value_blocks(self, field):
        snap = _snapshot()
        snap[field] = True
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_financial_block_safety_flags(self):
        snap = _snapshot(cash=-1.0)
        r = _read(snap)
        _assert_all_safety_flags_false(r)


class TestStaleResponseBlocks:
    def test_stale_snapshot_blocks(self):
        snap = _snapshot(broker_timestamp="2026-05-31T23:50:00Z")
        r = _read(snap, max_age_seconds=60)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_STALE_RESPONSE

    def test_exactly_max_age_passes(self):
        snap = _snapshot(broker_timestamp="2026-05-31T23:56:00Z")
        r = _read(snap, requested_at_utc="2026-06-01T00:01:00Z", max_age_seconds=300)
        assert r.result == "PASS"

    def test_one_second_over_max_age_blocks(self):
        snap = _snapshot(broker_timestamp="2026-05-31T23:55:59Z")
        r = _read(snap, requested_at_utc="2026-06-01T00:01:00Z", max_age_seconds=300)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_STALE_RESPONSE

    def test_stale_block_safety_flags(self):
        snap = _snapshot(broker_timestamp="2020-01-01T00:00:00Z")
        r = _read(snap, max_age_seconds=60)
        _assert_all_safety_flags_false(r)


class TestFutureBrokerTimestampBlocks:
    def test_future_broker_timestamp_blocks(self):
        snap = _snapshot(broker_timestamp="2026-06-01T00:02:00Z")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_STALE_RESPONSE

    def test_equal_timestamps_passes(self):
        snap = _snapshot(broker_timestamp="2026-06-01T00:01:00Z")
        r = _read(snap)
        assert r.result == "PASS"

    def test_future_block_safety_flags(self):
        snap = _snapshot(broker_timestamp="2030-01-01T00:00:00Z")
        r = _read(snap)
        _assert_all_safety_flags_false(r)


class TestInvalidTimezoneBlocks:
    def test_invalid_broker_timestamp_format_blocks(self):
        snap = _snapshot(broker_timestamp="not-a-date")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_naive_broker_timestamp_blocks(self):
        snap = _snapshot(broker_timestamp="2026-06-01T00:00:00")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_non_utc_broker_timestamp_blocks(self):
        snap = _snapshot(broker_timestamp="2026-06-01T00:00:00+05:00")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_invalid_requested_at_utc_blocks(self):
        snap = _snapshot()
        r = _read(snap, requested_at_utc="not-a-date")
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_naive_requested_at_utc_blocks(self):
        snap = _snapshot()
        r = _read(snap, requested_at_utc="2026-06-01T00:01:00")
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_non_utc_requested_at_utc_blocks(self):
        snap = _snapshot()
        r = _read(snap, requested_at_utc="2026-06-01T00:01:00+05:00")
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_timezone_block_safety_flags(self):
        snap = _snapshot(broker_timestamp="not-a-date")
        r = _read(snap)
        _assert_all_safety_flags_false(r)


class TestEmptyRequestIdBlocks:
    def test_empty_request_id_blocks(self):
        r = _read(request_id="")
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_whitespace_only_request_id_blocks(self):
        r = _read(request_id="   ")
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_none_request_id_blocks(self):
        r = _read(request_id=None)  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_request_id_block_safety_flags(self):
        r = _read(request_id="")
        _assert_all_safety_flags_false(r)


class TestMaxAgeSecondsValidation:
    def test_none_blocks(self):
        r = _read(max_age_seconds=None)  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_string_blocks(self):
        r = _read(max_age_seconds="60")  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_float_blocks(self):
        r = _read(max_age_seconds=60.0)  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_bool_blocks(self):
        r = _read(max_age_seconds=True)  # type: ignore[arg-type]
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_negative_blocks(self):
        r = _read(max_age_seconds=-1)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    def test_zero_valid_when_timestamps_equal(self):
        snap = _snapshot(broker_timestamp="2026-06-01T00:01:00Z")
        r = _read(snap, requested_at_utc="2026-06-01T00:01:00Z", max_age_seconds=0)
        assert r.result == "PASS"

    @pytest.mark.parametrize("invalid_value", [None, "60", 60.0, True, -1])
    def test_invalid_values_return_blocked_schema(self, invalid_value):
        r = _read(max_age_seconds=invalid_value)
        assert r.result == "BLOCKED"
        assert r.status is PASS.BLOCKED_SCHEMA

    @pytest.mark.parametrize("invalid_value", [None, "60", 60.0, True, -1])
    def test_safety_flags_false_on_invalid(self, invalid_value):
        r = _read(max_age_seconds=invalid_value)
        _assert_all_safety_flags_false(r)

    def test_input_not_mutated(self):
        snap = _snapshot()
        original = copy.deepcopy(snap)
        _read(snap, max_age_seconds=None)  # type: ignore[arg-type]
        assert snap == original


class TestInputImmutability:
    def test_snapshot_not_mutated(self):
        snap = _snapshot(positions=[{"symbol": "SPY"}])
        original = copy.deepcopy(snap)
        _read(snap)
        assert snap == original

    def test_positions_list_not_mutated(self):
        positions = [{"symbol": "SPY"}]
        snap = _snapshot(positions=positions)
        _read(snap)
        assert positions == [{"symbol": "SPY"}]

    def test_open_orders_list_not_mutated(self):
        orders = [{"id": "ord-1"}]
        snap = _snapshot(open_orders=orders)
        _read(snap)
        assert orders == [{"id": "ord-1"}]

    def test_market_clock_not_mutated(self):
        clock = {"is_open": True, "next_open": "2026-06-02T09:30:00Z"}
        snap = _snapshot(market_clock=clock)
        _read(snap)
        assert clock == {"is_open": True, "next_open": "2026-06-02T09:30:00Z"}


class TestDeterministicOutput:
    def test_pass_deterministic(self):
        r1 = _read()
        r2 = _read()
        assert r1 == r2

    def test_blocked_deterministic(self):
        r1 = _read(expected_environment="live")
        r2 = _read(expected_environment="live")
        assert r1 == r2

    def test_full_chain_deterministic(self):
        snap = _snapshot(cash=50000.0, positions=[{"symbol": "QQQ"}])
        r1 = _read(snap)
        snap2 = _snapshot(cash=50000.0, positions=[{"symbol": "QQQ"}])
        r2 = _read(snap2)
        assert r1 == r2


class TestPositionsAndOpenOrdersAsTuples:
    def test_positions_returned_as_tuple(self):
        snap = _snapshot(positions=[{"a": 1}, {"b": 2}])
        r = _read(snap)
        assert isinstance(r.positions, tuple)
        assert len(r.positions) == 2

    def test_open_orders_returned_as_tuple(self):
        snap = _snapshot(open_orders=[{"id": "o1"}, {"id": "o2"}])
        r = _read(snap)
        assert isinstance(r.open_orders, tuple)
        assert len(r.open_orders) == 2

    def test_empty_positions_is_empty_tuple(self):
        r = _read()
        assert r.positions == ()

    def test_empty_open_orders_is_empty_tuple(self):
        r = _read()
        assert r.open_orders == ()

    def test_tuple_positions_input_also_works(self):
        snap = _snapshot()
        snap["positions"] = ({"symbol": "SPY"},)
        r = _read(snap)
        assert r.result == "PASS"
        assert isinstance(r.positions, tuple)


class TestRawInputDictNotReturned:
    def test_result_does_not_contain_raw_snapshot(self):
        snap = _snapshot()
        r = _read(snap)
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "snapshot" not in field_names
        assert "raw_snapshot" not in field_names
        assert "input_snapshot" not in field_names

    def test_market_clock_is_deep_copy(self):
        clock = {"is_open": True}
        snap = _snapshot(market_clock=clock)
        r = _read(snap)
        assert r.market_clock == {"is_open": True}
        snap["market_clock"]["tampered"] = True
        assert "tampered" not in r.market_clock

    def test_positions_are_deep_copy(self):
        snap = _snapshot(positions=[{"symbol": "SPY", "qty": 10}])
        r = _read(snap)
        snap["positions"][0]["qty"] = 999
        assert r.positions[0]["qty"] == 10


class TestSafetyFlagsAlwaysFalse:
    def test_pass_safety_flags(self):
        r = _read()
        _assert_all_safety_flags_false(r)

    def test_blocked_environment_safety_flags(self):
        r = _read(expected_environment="live")
        _assert_all_safety_flags_false(r)

    def test_blocked_account_status_safety_flags(self):
        snap = _snapshot(account_status="disabled")
        r = _read(snap)
        _assert_all_safety_flags_false(r)

    def test_blocked_schema_safety_flags(self):
        snap = _snapshot()
        del snap["cash"]
        r = _read(snap)
        _assert_all_safety_flags_false(r)

    def test_blocked_stale_safety_flags(self):
        snap = _snapshot(broker_timestamp="2020-01-01T00:00:00Z")
        r = _read(snap, max_age_seconds=60)
        _assert_all_safety_flags_false(r)

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_pass(self, field):
        r = _read()
        assert getattr(r, field) is False

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_blocked(self, field):
        r = _read(expected_environment="live")
        assert getattr(r, field) is False


class TestNoForbiddenBehavior:
    _MODULES = [
        "src.broker.paper_account_snapshot",
        "src.broker.fake_paper_adapter",
    ]

    _FORBIDDEN_PATTERNS = [
        "os.environ",
        "getenv",
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

    @pytest.mark.parametrize("mod_name", _MODULES)
    @pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
    def test_no_forbidden_pattern_in_source(self, mod_name, pattern):
        import importlib
        mod = importlib.import_module(mod_name)
        source = inspect.getsource(mod)
        assert pattern not in source, f"Found forbidden pattern '{pattern}' in {mod_name}"

    def test_snapshot_module_has_no_open_call(self):
        import src.broker.paper_account_snapshot as mod
        source = inspect.getsource(mod)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "open(" not in stripped or "fromisoformat" in stripped, (
                f"Found 'open(' in non-comment line: {stripped}"
            )

    def test_snapshot_module_has_no_path_call(self):
        import src.broker.paper_account_snapshot as mod
        source = inspect.getsource(mod)
        assert "Path(" not in source


class TestNoAutomaticChainEntry:
    def test_pass_result_has_no_planner_fields(self):
        r = _read()
        field_names = {f.name for f in dataclasses.fields(r)}
        chain_fields = {"plan", "plan_id", "gate", "lifecycle", "preview", "ledger"}
        assert chain_fields.isdisjoint(field_names)

    def test_pass_does_not_import_planner(self):
        import src.broker.paper_account_snapshot as mod
        source = inspect.getsource(mod)
        assert "paper_order_planner" not in source
        assert "paper_order_safety_gate" not in source
        assert "paper_order_lifecycle" not in source
        assert "paper_dry_run_preview" not in source
        assert "paper_audit_ledger" not in source


class TestFakeSnapshotHelper:
    def test_returns_dict(self):
        snap = _snapshot()
        assert isinstance(snap, dict)

    def test_default_values(self):
        snap = _snapshot()
        assert snap["broker_reported_environment"] == "paper"
        assert snap["account_status"] == "active"
        assert snap["cash"] == 100000.0
        assert snap["buying_power"] == 100000.0
        assert snap["equity"] == 100000.0
        assert snap["positions"] == []
        assert snap["open_orders"] == []
        assert isinstance(snap["market_clock"], dict)
        assert snap["broker_timestamp"] == "2026-06-01T00:00:00Z"

    def test_custom_values(self):
        snap = _snapshot(
            cash=50.0,
            buying_power=40.0,
            equity=60.0,
            positions=[{"s": "SPY"}],
            open_orders=[{"id": "o1"}],
            market_clock={"is_open": False},
            broker_timestamp="2026-05-30T12:00:00Z",
        )
        assert snap["cash"] == 50.0
        assert snap["buying_power"] == 40.0
        assert snap["equity"] == 60.0
        assert snap["positions"] == [{"s": "SPY"}]
        assert snap["open_orders"] == [{"id": "o1"}]
        assert snap["market_clock"] == {"is_open": False}
        assert snap["broker_timestamp"] == "2026-05-30T12:00:00Z"

    def test_has_no_connect_method(self):
        snap = _snapshot()
        assert not hasattr(snap, "connect")
        assert not hasattr(snap, "request")

    def test_has_no_credential_fields(self):
        snap = _snapshot()
        forbidden_fragments = ["api" + "_" + "key", "sec" + "ret", "tok" + "en", "pass" + "word"]
        for key in snap:
            for frag in forbidden_fragments:
                assert frag not in key.lower(), f"Forbidden fragment '{frag}' in key '{key}'"

    def test_has_no_url_or_endpoint_fields(self):
        snap = _snapshot()
        for key in snap:
            assert "url" not in key.lower()
            assert "end" + "point" not in key.lower()

    def test_has_no_real_account_identifiers(self):
        snap = _snapshot()
        for key in snap:
            assert "account" + "_" + "id" not in key.lower()
            assert "account" + "_" + "number" not in key.lower()
