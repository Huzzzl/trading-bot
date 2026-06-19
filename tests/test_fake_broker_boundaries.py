"""Integration tests for fake credential provider and fake paper adapter
wired through S43 credential metadata validator and account environment guard — S44."""

from __future__ import annotations

import copy
import dataclasses
import inspect

import pytest

from src.broker.fake_credential_provider import (
    FakeCredentialProviderResult,
    create_fake_paper_credential_metadata,
)
from src.broker.fake_paper_adapter import (
    FakePaperAdapterResult,
    create_fake_paper_adapter_metadata,
)
from src.broker.credential_metadata import (
    CredentialMetadataStatus,
    CredentialMetadataValidationResult,
    validate_credential_metadata,
)
from src.broker.account_environment_guard import (
    AccountEnvironmentStatus,
    AccountEnvironmentVerificationResult,
    verify_account_environment,
)

_NOW = "2026-06-01T00:00:00Z"

CMS = CredentialMetadataStatus
AES = AccountEnvironmentStatus


def _credential_result(**overrides):
    return create_fake_paper_credential_metadata(**overrides)


def _adapter_result(**overrides):
    return create_fake_paper_adapter_metadata(**overrides)


def _validate_cred(cred_result: FakeCredentialProviderResult, *, now_utc=_NOW):
    return validate_credential_metadata(
        cred_result.metadata,
        expected_environment="paper",
        now_utc=now_utc,
    )


def _verify_env(cred_result: FakeCredentialProviderResult, adapter_result: FakePaperAdapterResult):
    return verify_account_environment(
        expected_environment="paper",
        credential_environment=cred_result.metadata["declared_environment"],
        adapter_environment=adapter_result.adapter_environment,
        broker_reported_environment=adapter_result.broker_reported_environment,
    )


def _run_full_chain(*, cred_overrides=None, adapter_overrides=None, now_utc=_NOW):
    cred = _credential_result(**(cred_overrides or {}))
    adapter = _adapter_result(**(adapter_overrides or {}))
    cred_val = _validate_cred(cred, now_utc=now_utc)
    env_val = _verify_env(cred, adapter)
    return cred, adapter, cred_val, env_val


def _assert_all_safety_flags_false(*results):
    for r in results:
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


class TestFullChainPass:
    def test_valid_paper_credential_and_adapter_passes(self):
        cred, adapter, cred_val, env_val = _run_full_chain()
        assert cred.result == "PASS"
        assert adapter.result == "PASS"
        assert cred_val.status is CMS.CREDENTIAL_METADATA_READY_PAPER
        assert cred_val.result == "PASS"
        assert env_val.status is AES.VERIFIED_PAPER
        assert env_val.result == "PASS"

    def test_all_safety_flags_false_on_pass(self):
        cred, adapter, cred_val, env_val = _run_full_chain()
        _assert_all_safety_flags_false(cred, adapter, cred_val, env_val)

    def test_pass_metadata_contains_expected_fields(self):
        cred, _, cred_val, _ = _run_full_chain()
        m = cred.metadata
        assert m["profile_name"] == "fake-paper-dev"
        assert m["declared_environment"] == "paper"
        assert m["rotation_required"] is False
        assert m["secret_material_present"] is False
        assert cred_val.profile_name == "fake-paper-dev"
        assert cred_val.declared_environment == "paper"

    def test_pass_environment_contains_expected_fields(self):
        _, adapter, _, env_val = _run_full_chain()
        assert adapter.adapter_environment == "paper"
        assert adapter.broker_reported_environment == "paper"
        assert env_val.expected_environment == "paper"
        assert env_val.credential_environment == "paper"
        assert env_val.adapter_environment == "paper"
        assert env_val.broker_reported_environment == "paper"


class TestLiveCredentialEnvironmentBlocks:
    def test_live_declared_environment_blocks_credential_validator(self):
        cred = _credential_result(declared_environment="live")
        assert cred.result == "BLOCKED"
        assert cred.metadata is None

    def test_live_declared_environment_blocks_via_extra_fields(self):
        cred = _credential_result()
        cred_metadata = copy.deepcopy(cred.metadata)
        cred_metadata["declared_environment"] = "live"
        r = validate_credential_metadata(
            cred_metadata, expected_environment="paper", now_utc=_NOW,
        )
        assert r.status is CMS.BLOCKED_ENVIRONMENT

    def test_live_expected_environment_blocks(self):
        cred = _credential_result()
        r = validate_credential_metadata(
            cred.metadata, expected_environment="live", now_utc=_NOW,
        )
        assert r.status is CMS.BLOCKED_ENVIRONMENT


class TestLiveAdapterEnvironmentBlocks:
    def test_live_adapter_environment_blocks(self):
        cred = _credential_result()
        adapter = _adapter_result(adapter_environment="live")
        assert adapter.result == "BLOCKED"
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment="live",
            broker_reported_environment="paper",
        )
        assert env_val.status is AES.BLOCKED_LIVE_ENVIRONMENT

    def test_live_broker_reported_environment_blocks(self):
        cred = _credential_result()
        adapter = _adapter_result(broker_reported_environment="live")
        assert adapter.result == "BLOCKED"
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment="paper",
            broker_reported_environment="live",
        )
        assert env_val.status is AES.BLOCKED_LIVE_ENVIRONMENT


class TestAmbiguousEnvironmentBlocks:
    @pytest.mark.parametrize("env", ["sandbox", "demo", "staging", "PAPER"])
    def test_ambiguous_adapter_environment_blocks(self, env):
        cred = _credential_result()
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment=env,
            broker_reported_environment="paper",
        )
        assert env_val.status is AES.BLOCKED_AMBIGUOUS_ENVIRONMENT

    @pytest.mark.parametrize("env", ["sandbox", "demo", "staging", "PAPER"])
    def test_ambiguous_broker_reported_blocks(self, env):
        cred = _credential_result()
        env_val = verify_account_environment(
            expected_environment="paper",
            credential_environment=cred.metadata["declared_environment"],
            adapter_environment="paper",
            broker_reported_environment=env,
        )
        assert env_val.status is AES.BLOCKED_AMBIGUOUS_ENVIRONMENT

    @pytest.mark.parametrize("env", ["sandbox", "demo", "staging", "unknown"])
    def test_ambiguous_credential_environment_blocks(self, env):
        cred = _credential_result()
        cred_metadata = copy.deepcopy(cred.metadata)
        cred_metadata["declared_environment"] = env
        r = validate_credential_metadata(
            cred_metadata, expected_environment="paper", now_utc=_NOW,
        )
        assert r.status is CMS.BLOCKED_ENVIRONMENT


class TestExpiredMetadataBlocks:
    def test_expired_credential_blocks(self):
        cred = _credential_result(expires_at_utc="2020-01-01T00:00:00Z")
        r = _validate_cred(cred)
        assert r.status is CMS.BLOCKED_EXPIRY

    def test_equal_expiry_and_now_blocks(self):
        cred = _credential_result(expires_at_utc=_NOW)
        r = _validate_cred(cred)
        assert r.status is CMS.BLOCKED_EXPIRY

    def test_invalid_expiry_format_blocks(self):
        cred = _credential_result(expires_at_utc="not-a-date")
        r = _validate_cred(cred)
        assert r.status is CMS.BLOCKED_EXPIRY


class TestRotationRequiredBlocks:
    def test_rotation_true_blocks(self):
        cred = _credential_result(rotation_required=True)
        r = _validate_cred(cred)
        assert r.status is CMS.BLOCKED_ROTATION

    def test_safety_flags_on_rotation_block(self):
        cred = _credential_result(rotation_required=True)
        r = _validate_cred(cred)
        _assert_all_safety_flags_false(r)


class TestForbiddenCredentialFieldsBlocks:
    def test_forbidden_key_in_extra_fields_blocks(self):
        forbidden_key = "api" + "_" + "key"
        cred = _credential_result(extra_fields={forbidden_key: "harmless"})
        r = _validate_cred(cred)
        assert r.status is CMS.BLOCKED_FORBIDDEN_FIELDS

    def test_forbidden_value_in_extra_fields_blocks(self):
        forbidden_val = "my_" + "sec" + "ret" + "_data"
        cred = _credential_result(extra_fields={"notes": forbidden_val})
        r = _validate_cred(cred)
        assert r.status is CMS.BLOCKED_FORBIDDEN_FIELDS

    def test_harmless_extra_field_passes(self):
        cred = _credential_result(extra_fields={"notes": "just a note"})
        r = _validate_cred(cred)
        assert r.status is CMS.CREDENTIAL_METADATA_READY_PAPER


class TestInputImmutability:
    def test_credential_metadata_not_mutated(self):
        cred = _credential_result()
        original = copy.deepcopy(cred.metadata)
        _validate_cred(cred)
        assert cred.metadata == original

    def test_adapter_result_not_mutated(self):
        adapter = _adapter_result()
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

    def test_frozen_credential_provider_result(self):
        cred = _credential_result()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cred.result = "TAMPERED"  # type: ignore[misc]

    def test_frozen_adapter_result(self):
        adapter = _adapter_result()
        with pytest.raises(dataclasses.FrozenInstanceError):
            adapter.result = "TAMPERED"  # type: ignore[misc]


class TestDeterministicOutputs:
    def test_credential_validator_deterministic(self):
        cred = _credential_result()
        r1 = _validate_cred(cred)
        r2 = _validate_cred(cred)
        assert r1 == r2

    def test_environment_guard_deterministic(self):
        cred = _credential_result()
        adapter = _adapter_result()
        r1 = _verify_env(cred, adapter)
        r2 = _verify_env(cred, adapter)
        assert r1 == r2

    def test_full_chain_deterministic(self):
        _, _, cred_val1, env_val1 = _run_full_chain()
        _, _, cred_val2, env_val2 = _run_full_chain()
        assert cred_val1 == cred_val2
        assert env_val1 == env_val2


class TestSafetyFlagsAlwaysFalse:
    def test_pass_chain_safety_flags(self):
        cred, adapter, cred_val, env_val = _run_full_chain()
        _assert_all_safety_flags_false(cred, adapter, cred_val, env_val)

    def test_blocked_credential_safety_flags(self):
        cred = _credential_result(rotation_required=True)
        r = _validate_cred(cred)
        _assert_all_safety_flags_false(cred, r)

    def test_blocked_environment_safety_flags(self):
        adapter = _adapter_result(adapter_environment="live")
        _assert_all_safety_flags_false(adapter)

    def test_blocked_chain_safety_flags(self):
        cred = _credential_result(expires_at_utc="2020-01-01T00:00:00Z")
        adapter = _adapter_result()
        cred_val = _validate_cred(cred)
        env_val = _verify_env(cred, adapter)
        _assert_all_safety_flags_false(cred, adapter, cred_val, env_val)

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_credential_result(self, field):
        cred = _credential_result()
        assert getattr(cred, field) is False

    @pytest.mark.parametrize("field", [
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ])
    def test_individual_flag_false_on_adapter_result(self, field):
        adapter = _adapter_result()
        assert getattr(adapter, field) is False


class TestNoFallback:
    def test_credential_provider_refuses_live(self):
        cred = _credential_result(declared_environment="live")
        assert cred.result == "BLOCKED"
        assert cred.metadata is None

    def test_adapter_refuses_live_adapter(self):
        adapter = _adapter_result(adapter_environment="live")
        assert adapter.result == "BLOCKED"

    def test_adapter_refuses_live_broker_reported(self):
        adapter = _adapter_result(broker_reported_environment="live")
        assert adapter.result == "BLOCKED"

    def test_no_fallback_from_blocked_credential(self):
        cred = _credential_result(declared_environment="live")
        assert cred.result == "BLOCKED"
        assert cred.blocker is not None


class TestNoAdapterConstructionOutsideFakeClasses:
    def test_fake_credential_has_no_connect_method(self):
        cred = _credential_result()
        assert not hasattr(cred, "connect")
        assert not hasattr(cred, "request")

    def test_fake_adapter_has_no_connect_method(self):
        adapter = _adapter_result()
        assert not hasattr(adapter, "connect")
        assert not hasattr(adapter, "request")

    def test_credential_result_has_no_adapter_fields(self):
        cred = _credential_result()
        field_names = {f.name for f in dataclasses.fields(cred)}
        assert "adapter" not in field_names
        assert "broker" not in field_names
        assert "client" not in field_names

    def test_adapter_result_has_no_credential_fields(self):
        adapter = _adapter_result()
        field_names = {f.name for f in dataclasses.fields(adapter)}
        forbidden_fragments = ["api" + "_" + "key", "sec" + "ret", "tok" + "en", "pass" + "word"]
        for name in field_names:
            for frag in forbidden_fragments:
                assert frag not in name.lower()


class TestNoOrderActionCapability:
    def test_credential_provider_has_no_order_methods(self):
        import src.broker.fake_credential_provider as mod
        source = inspect.getsource(mod)
        forbidden = [
            "submit" + "_" + "order",
            "place" + "_" + "order",
            "cancel" + "_" + "order",
            "replace" + "_" + "order",
            "modify" + "_" + "order",
            "close" + "_" + "position",
            "liquidate",
        ]
        for pattern in forbidden:
            assert pattern not in source

    def test_adapter_has_no_order_methods(self):
        import src.broker.fake_paper_adapter as mod
        source = inspect.getsource(mod)
        forbidden = [
            "submit" + "_" + "order",
            "place" + "_" + "order",
            "cancel" + "_" + "order",
            "replace" + "_" + "order",
            "modify" + "_" + "order",
            "close" + "_" + "position",
            "liquidate",
        ]
        for pattern in forbidden:
            assert pattern not in source


class TestSourceHygiene:
    _MODULES = [
        "src.broker.fake_credential_provider",
        "src.broker.fake_paper_adapter",
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

    @pytest.mark.parametrize("mod_name", _MODULES)
    @pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
    def test_no_forbidden_pattern_in_source(self, mod_name, pattern):
        import importlib
        mod = importlib.import_module(mod_name)
        source = inspect.getsource(mod)
        assert pattern not in source, f"Found forbidden pattern '{pattern}' in {mod_name}"
