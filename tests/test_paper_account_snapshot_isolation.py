"""Isolation tests proving the S43-S45 broker-observation boundary
remains isolated from the existing offline order-preparation chain — S46.

Tests-only. No production source modifications.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
from unittest.mock import patch

import pytest

from src.broker.credential_metadata import (
    CredentialMetadataStatus,
    validate_credential_metadata,
)
from src.broker.account_environment_guard import (
    AccountEnvironmentStatus,
    verify_account_environment,
)
from src.broker.fake_credential_provider import (
    create_fake_paper_credential_metadata,
)
from src.broker.fake_paper_adapter import (
    create_fake_paper_account_snapshot,
    create_fake_paper_adapter_metadata,
)
from src.broker.paper_account_snapshot import (
    PaperAccountSnapshotResult,
    PaperAccountSnapshotStatus,
    read_fake_paper_account_snapshot,
)


_NOW = "2026-06-01T00:01:00Z"
_LATER = "2026-06-01T00:02:00Z"
_REQUEST_ID = "req-iso-001"


_CHAIN_FUNCTIONS = [
    ("src.research.paper_order_planner", "create_paper_order_plan"),
    ("src.research.paper_order_plan_validator", "validate_paper_order_plan"),
    ("src.research.paper_order_safety_gate", "evaluate_paper_order_safety_gate"),
    ("src.research.paper_order_lifecycle", "create_lifecycle_from_plan"),
    ("src.research.paper_order_lifecycle", "apply_lifecycle_event"),
    ("src.research.paper_dry_run_preview", "render_paper_dry_run_preview"),
    ("src.research.paper_audit_ledger", "append_audit_entry"),
]


def _cred():
    return create_fake_paper_credential_metadata()


def _adapter():
    return create_fake_paper_adapter_metadata()


def _snap(**overrides):
    return create_fake_paper_account_snapshot(**overrides)


def _read(snapshot=None, **kwargs):
    if snapshot is None:
        snapshot = _snap()
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


class TestObservationBoundaryPasses:
    def test_valid_fake_credential_passes_s43_validation(self):
        cred = _cred()
        r = validate_credential_metadata(
            cred.metadata, expected_environment="paper", now_utc=_NOW,
        )
        assert r.result == "PASS"
        assert r.status is CredentialMetadataStatus.CREDENTIAL_METADATA_READY_PAPER

    def test_valid_fake_adapter_passes_s43_environment_guard(self):
        cred = _cred()
        adapter = _adapter()
        r = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment=adapter.adapter_environment,
            broker_reported_environment=adapter.broker_reported_environment,
        )
        assert r.result == "PASS"
        assert r.status is AccountEnvironmentStatus.VERIFIED_PAPER

    def test_valid_fake_snapshot_passes_s45_reader(self):
        r = _read()
        assert r.result == "PASS"
        assert r.status is PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER

    def test_full_observation_chain_safety_flags(self):
        cred = _cred()
        adapter = _adapter()
        cred_val = validate_credential_metadata(
            cred.metadata, expected_environment="paper", now_utc=_NOW,
        )
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment=adapter.adapter_environment,
            broker_reported_environment=adapter.broker_reported_environment,
        )
        snap_val = _read()
        _assert_all_safety_flags_false(cred, adapter, cred_val, env_val, snap_val)


class TestSnapshotDoesNotInvokeOrderChain:
    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_snapshot_pass_does_not_invoke_chain_function(self, module_name, func_name):
        full_target = f"{module_name}.{func_name}"
        with patch(full_target) as mocked:
            r = _read()
            assert r.result == "PASS"
            assert mocked.call_count == 0

    @pytest.mark.parametrize("module_name,func_name", _CHAIN_FUNCTIONS)
    def test_snapshot_blocked_does_not_invoke_chain_function(self, module_name, func_name):
        full_target = f"{module_name}.{func_name}"
        with patch(full_target) as mocked:
            r = _read(expected_environment="live")
            assert r.result == "BLOCKED"
            assert mocked.call_count == 0

    def test_snapshot_source_does_not_import_chain_modules(self):
        import src.broker.paper_account_snapshot as mod
        source = inspect.getsource(mod)
        for module_name, _ in _CHAIN_FUNCTIONS:
            short_name = module_name.split(".")[-1]
            assert short_name not in source, (
                f"snapshot module references {short_name}"
            )

    def test_fake_adapter_source_does_not_import_chain_modules(self):
        import src.broker.fake_paper_adapter as mod
        source = inspect.getsource(mod)
        for module_name, _ in _CHAIN_FUNCTIONS:
            short_name = module_name.split(".")[-1]
            assert short_name not in source, (
                f"fake adapter module references {short_name}"
            )

    def test_fake_credential_provider_source_does_not_import_chain_modules(self):
        import src.broker.fake_credential_provider as mod
        source = inspect.getsource(mod)
        for module_name, _ in _CHAIN_FUNCTIONS:
            short_name = module_name.split(".")[-1]
            assert short_name not in source


class TestSnapshotResultHasNoChainFields:
    _FORBIDDEN_FIELD_NAMES = {
        "approval", "approval_artifact", "approval_artifact_hash",
        "approved", "approval_status",
        "plan", "plan_id", "order_plan",
        "gate", "gate_status", "safety_gate",
        "lifecycle", "lifecycle_id", "lifecycle_status", "lifecycle_state",
        "preview", "preview_id", "preview_status",
        "ledger", "ledger_id", "audit_entry",
        "submit", "submission", "submitted",
        "execution", "executor", "executed",
        "order_action", "order_id", "client_order_id",
        "broker_payload",
        "advance_lifecycle", "apply_event",
    }

    def test_pass_result_has_no_forbidden_field_names(self):
        r = _read()
        field_names = {f.name for f in dataclasses.fields(r)}
        intersection = field_names & self._FORBIDDEN_FIELD_NAMES
        assert intersection == set(), (
            f"snapshot result has forbidden chain fields: {intersection}"
        )

    def test_blocked_result_has_no_forbidden_field_names(self):
        r = _read(expected_environment="live")
        field_names = {f.name for f in dataclasses.fields(r)}
        intersection = field_names & self._FORBIDDEN_FIELD_NAMES
        assert intersection == set()

    def test_pass_result_has_no_approval_status_value(self):
        r = _read()
        for f in dataclasses.fields(r):
            val = getattr(r, f.name)
            if isinstance(val, str):
                lower = val.lower()
                assert "approval" not in lower
                assert "approved" not in lower
                assert "_executed" not in lower
                assert "submitted" not in lower

    def test_pass_status_is_pure_observation(self):
        r = _read()
        assert r.status is PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER
        assert "APPROVED" not in r.status.value
        assert "APPROVAL" not in r.status.value
        assert "SUBMIT" not in r.status.value
        assert "EXECUTE" not in r.status.value
        assert "LIFECYCLE" not in r.status.value
        assert "PLAN" not in r.status.value
        assert "ORDER" not in r.status.value


class TestSnapshotCannotBeUsedAsApprovalOrPlan:
    def test_snapshot_result_is_not_pta_artifact_dict(self):
        r = _read()
        assert not isinstance(r, dict)

    def test_snapshot_result_has_no_pta_schema_version(self):
        r = _read()
        for f in dataclasses.fields(r):
            val = getattr(r, f.name)
            if isinstance(val, str):
                assert val != "PTA/1.0"
                assert val != "POP/1.0"
                assert val != "PDRP/1.0"

    def test_snapshot_pass_rejected_as_plan_input(self):
        from src.research.paper_order_plan_validator import (
            PaperOrderPlanStatus,
            validate_paper_order_plan,
        )
        r = _read()
        try:
            plan_result = validate_paper_order_plan(r)  # type: ignore[arg-type]
        except (TypeError, AttributeError):
            return
        assert plan_result.result != "PASS"

    def test_snapshot_pass_rejected_as_approval_input(self):
        from src.research.paper_approval_validator import (
            PaperApprovalStatus,
            validate_paper_approval_artifact,
        )
        r = _read()
        try:
            approval_result = validate_paper_approval_artifact(r)  # type: ignore[arg-type]
        except (TypeError, AttributeError):
            return
        assert approval_result.result != "PASS"

    def test_snapshot_as_dict_is_not_valid_plan(self):
        r = _read()
        from src.research.paper_order_plan_validator import validate_paper_order_plan
        snap_dict = {f.name: getattr(r, f.name) for f in dataclasses.fields(r)}
        plan_result = validate_paper_order_plan(snap_dict)
        assert plan_result.result != "PASS"

    def test_snapshot_as_dict_is_not_valid_approval(self):
        r = _read()
        from src.research.paper_approval_validator import (
            validate_paper_approval_artifact,
        )
        snap_dict = {f.name: getattr(r, f.name) for f in dataclasses.fields(r)}
        approval_result = validate_paper_approval_artifact(snap_dict)
        assert approval_result.result != "PASS"


class TestPositionsAndOpenOrdersAreObservationsOnly:
    def test_positions_field_is_immutable_tuple(self):
        snap = _snap(positions=[{"symbol": "SPY", "qty": 10}])
        r = _read(snap)
        assert isinstance(r.positions, tuple)

    def test_open_orders_field_is_immutable_tuple(self):
        snap = _snap(open_orders=[{"id": "ord-1"}])
        r = _read(snap)
        assert isinstance(r.open_orders, tuple)

    def test_positions_cannot_be_appended(self):
        r = _read()
        with pytest.raises(AttributeError):
            r.positions.append({"symbol": "BAD"})  # type: ignore[attr-defined]

    def test_open_orders_cannot_be_appended(self):
        r = _read()
        with pytest.raises(AttributeError):
            r.open_orders.append({"id": "BAD"})  # type: ignore[attr-defined]

    def test_positions_no_approval_field(self):
        snap = _snap(positions=[{"symbol": "SPY", "qty": 10}])
        r = _read(snap)
        for pos in r.positions:
            assert "approval" not in pos
            assert "submit" not in pos
            assert "client_order_id" not in pos

    def test_open_orders_no_approval_field(self):
        snap = _snap(open_orders=[{"id": "ord-1"}])
        r = _read(snap)
        for order in r.open_orders:
            assert "approval" not in order
            assert "submit" not in order


class TestBlockedPathsStopChain:
    @pytest.mark.parametrize("env_field,env_value", [
        ("expected_environment", "live"),
        ("credential_environment", "live"),
        ("adapter_environment", "live"),
    ])
    def test_blocked_environment_stops_observation_chain(self, env_field, env_value):
        r = _read(**{env_field: env_value})
        assert r.result == "BLOCKED"
        assert r.status is PaperAccountSnapshotStatus.BLOCKED_ENVIRONMENT

    def test_blocked_credential_stops_observation_chain(self):
        cred_result = create_fake_paper_credential_metadata(
            declared_environment="live",
        )
        assert cred_result.result == "BLOCKED"
        assert cred_result.metadata is None

    def test_blocked_snapshot_account_status_stops_chain(self):
        snap = _snap(account_status="disabled")
        r = _read(snap)
        assert r.result == "BLOCKED"
        assert r.status is PaperAccountSnapshotStatus.BLOCKED_ACCOUNT_STATUS

    def test_blocked_snapshot_safety_flags_remain_false(self):
        snap = _snap(account_status="disabled")
        r = _read(snap)
        _assert_all_safety_flags_false(r)

    def test_blocked_credential_path_does_not_proceed_to_snapshot(self):
        cred_result = create_fake_paper_credential_metadata(
            declared_environment="live",
        )
        assert cred_result.metadata is None
        snap = _snap()
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment="live",
            adapter_environment="paper",
            broker_reported_environment="paper",
        )
        assert env_val.result == "BLOCKED"
        r = _read(snap, credential_environment="live")
        assert r.result == "BLOCKED"


class TestNoAutomaticFallback:
    def test_blocked_environment_no_fallback_to_pass(self):
        r = _read(expected_environment="live")
        assert r.result == "BLOCKED"
        assert r.status is not PaperAccountSnapshotStatus.SNAPSHOT_READY_PAPER

    def test_blocked_credential_does_not_fall_back(self):
        cred_result = create_fake_paper_credential_metadata(
            declared_environment="sandbox",
        )
        assert cred_result.result == "BLOCKED"
        assert cred_result.metadata is None

    def test_blocked_adapter_does_not_fall_back(self):
        adapter_result = create_fake_paper_adapter_metadata(
            adapter_environment="live",
        )
        assert adapter_result.result == "BLOCKED"

    def test_blocked_snapshot_age_does_not_fall_back(self):
        snap = _snap(broker_timestamp="2020-01-01T00:00:00Z")
        r = _read(snap, max_age_seconds=60)
        assert r.result == "BLOCKED"
        assert r.status is PaperAccountSnapshotStatus.BLOCKED_STALE_RESPONSE


class TestSafetyFlagsAlwaysFalse:
    def test_all_observation_results_safety_flags_false(self):
        cred = _cred()
        adapter = _adapter()
        cred_val = validate_credential_metadata(
            cred.metadata, expected_environment="paper", now_utc=_NOW,
        )
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment=adapter.adapter_environment,
            broker_reported_environment=adapter.broker_reported_environment,
        )
        snap_val = _read()
        _assert_all_safety_flags_false(cred, adapter, cred_val, env_val, snap_val)

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_snapshot_pass(self, field):
        r = _read()
        assert getattr(r, field) is False

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_snapshot_blocked(self, field):
        r = _read(expected_environment="live")
        assert getattr(r, field) is False


class TestDeterministicAndImmutable:
    def test_snapshot_read_deterministic(self):
        r1 = _read()
        r2 = _read()
        assert r1 == r2

    def test_snapshot_read_blocked_deterministic(self):
        r1 = _read(expected_environment="live")
        r2 = _read(expected_environment="live")
        assert r1 == r2

    def test_snapshot_result_is_frozen(self):
        r = _read()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.result = "TAMPERED"  # type: ignore[misc]

    def test_snapshot_input_dict_not_mutated(self):
        snap = _snap(positions=[{"symbol": "SPY"}])
        original = copy.deepcopy(snap)
        _read(snap)
        assert snap == original

    def test_credential_metadata_dict_not_mutated(self):
        cred = _cred()
        original = copy.deepcopy(cred.metadata)
        validate_credential_metadata(
            cred.metadata, expected_environment="paper", now_utc=_NOW,
        )
        assert cred.metadata == original

    def test_environment_guard_does_not_mutate_adapter_result(self):
        adapter = _adapter()
        env_before = adapter.adapter_environment
        broker_before = adapter.broker_reported_environment
        verify_account_environment(
            expected_environment="paper",
            credential_environment="paper",
            adapter_environment=adapter.adapter_environment,
            broker_reported_environment=adapter.broker_reported_environment,
        )
        assert adapter.adapter_environment == env_before
        assert adapter.broker_reported_environment == broker_before


class TestSourceHygiene:
    _BOUNDARY_MODULES = [
        "src.broker.paper_account_snapshot",
        "src.broker.fake_paper_adapter",
        "src.broker.fake_credential_provider",
        "src.broker.credential_metadata",
        "src.broker.account_environment_guard",
    ]

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

    @pytest.mark.parametrize("mod_name", _BOUNDARY_MODULES)
    @pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
    def test_no_forbidden_pattern_in_observation_modules(self, mod_name, pattern):
        import importlib
        mod = importlib.import_module(mod_name)
        source = inspect.getsource(mod)
        assert pattern not in source, (
            f"Found forbidden pattern '{pattern}' in {mod_name}"
        )

    def test_no_runtime_main_wiring_in_observation_modules(self):
        import importlib
        for mod_name in self._BOUNDARY_MODULES:
            mod = importlib.import_module(mod_name)
            source = inspect.getsource(mod)
            assert "src.main" not in source
            assert "src.runtime" not in source
            assert "src.execution" not in source

    def test_no_files_written_during_full_chain(self, tmp_path):
        import builtins
        real_open = builtins.open
        opens: list = []

        def tracking_open(*args, **kwargs):
            if args and isinstance(args[0], str):
                if str(tmp_path) not in args[0] and "/proc/" not in args[0]:
                    opens.append(args[0])
            return real_open(*args, **kwargs)

        with patch.object(builtins, "open", tracking_open):
            cred = _cred()
            adapter = _adapter()
            validate_credential_metadata(
                cred.metadata, expected_environment="paper", now_utc=_NOW,
            )
            verify_account_environment(
                expected_environment="paper",
                credential_environment=cred.metadata["declared_environment"],
                adapter_environment=adapter.adapter_environment,
                broker_reported_environment=adapter.broker_reported_environment,
            )
            _read()

        write_opens = [p for p in opens if "site-packages" not in p]
        assert write_opens == [], f"unexpected file opens: {write_opens}"


class TestNoOrderActionMethods:
    _ALL_BOUNDARY_MODULES = [
        "src.broker.paper_account_snapshot",
        "src.broker.fake_paper_adapter",
        "src.broker.fake_credential_provider",
        "src.broker.credential_metadata",
        "src.broker.account_environment_guard",
    ]

    @pytest.mark.parametrize("mod_name", _ALL_BOUNDARY_MODULES)
    def test_module_has_no_order_action_function(self, mod_name):
        import importlib
        mod = importlib.import_module(mod_name)
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
            assert not hasattr(mod, name), (
                f"{mod_name} has forbidden function {name}"
            )

    def test_snapshot_result_has_no_action_method(self):
        r = _read()
        forbidden_methods = [
            "submit", "place", "cancel", "approve", "execute",
            "advance", "apply_event", "create_plan",
        ]
        for method in forbidden_methods:
            assert not hasattr(r, method) or not callable(getattr(r, method))

    def test_fake_snapshot_dict_has_no_action_keys(self):
        snap = _snap()
        for key in snap:
            assert "submit" not in key.lower()
            assert "place" not in key.lower()
            assert "approve" not in key.lower()
            assert "execute" not in key.lower()


class TestNoBrokerPayloadOrAccountIdentifier:
    def test_snapshot_result_has_no_account_id_field(self):
        r = _read()
        field_names = {f.name for f in dataclasses.fields(r)}
        assert "account" + "_" + "id" not in field_names
        assert "account" + "_" + "number" not in field_names
        assert "broker_payload" not in field_names

    def test_fake_snapshot_dict_has_no_account_id(self):
        snap = _snap()
        for key in snap:
            assert "account" + "_" + "id" not in key.lower()
            assert "account" + "_" + "number" not in key.lower()
            assert "broker_payload" not in key.lower()

    def test_snapshot_pass_market_clock_no_broker_payload_key(self):
        r = _read()
        if r.market_clock is not None:
            for key in r.market_clock:
                assert "broker_payload" not in key.lower()
                assert "submit" not in key.lower()
