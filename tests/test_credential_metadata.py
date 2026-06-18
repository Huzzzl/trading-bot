"""Tests for src/broker/credential_metadata.py — S43."""

from __future__ import annotations

import copy
import dataclasses
import inspect
import textwrap

import pytest

from src.broker.credential_metadata import (
    CredentialMetadataStatus,
    CredentialMetadataValidationResult,
    validate_credential_metadata,
)

Status = CredentialMetadataStatus

_NOW = "2026-06-01T00:00:00Z"
_FUTURE = "2027-01-01T00:00:00Z"
_PAST = "2025-01-01T00:00:00Z"


def _valid_metadata() -> dict:
    return {
        "profile_name": "paper-dev",
        "declared_environment": "paper",
        "expires_at_utc": _FUTURE,
        "rotation_required": False,
        "secret_material_present": False,
    }


def _run(metadata=None, *, expected_environment="paper", now_utc=_NOW):
    if metadata is None:
        metadata = _valid_metadata()
    return validate_credential_metadata(
        metadata, expected_environment=expected_environment, now_utc=now_utc,
    )


def _assert_safety_flags(r: CredentialMetadataValidationResult) -> None:
    assert r.broker_calls_made is False
    assert r.credentials_read is False
    assert r.network_calls_made is False
    assert r.order_action_requested is False
    assert r.live_trading_allowed is False


class TestValidPass:
    def test_valid_paper_metadata_passes(self):
        r = _run()
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER
        assert r.result == "PASS"
        assert r.blocker is None

    def test_result_is_frozen(self):
        r = _run()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = Status.BLOCKED_SCHEMA  # type: ignore[misc]

    def test_safety_flags_on_pass(self):
        _assert_safety_flags(_run())

    def test_future_expiry_passes(self):
        r = _run()
        assert r.expires_at_utc == _FUTURE
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER


class TestSafetyFlagsOnBlocked:
    @pytest.mark.parametrize("env", ["live", "unknown"])
    def test_safety_flags_on_blocked_environment(self, env):
        _assert_safety_flags(_run(expected_environment=env))

    def test_safety_flags_on_blocked_schema(self):
        _assert_safety_flags(_run("not-a-dict"))

    def test_safety_flags_on_blocked_profile(self):
        m = _valid_metadata()
        m["profile_name"] = ""
        _assert_safety_flags(_run(m))

    def test_safety_flags_on_blocked_expiry(self):
        m = _valid_metadata()
        m["expires_at_utc"] = _PAST
        _assert_safety_flags(_run(m))

    def test_safety_flags_on_blocked_rotation(self):
        m = _valid_metadata()
        m["rotation_required"] = True
        _assert_safety_flags(_run(m))

    def test_safety_flags_on_blocked_secret_material(self):
        m = _valid_metadata()
        m["secret_material_present"] = True
        _assert_safety_flags(_run(m))


class TestInputImmutability:
    def test_metadata_not_mutated(self):
        m = _valid_metadata()
        original = copy.deepcopy(m)
        _run(m)
        assert m == original

    def test_deterministic_output(self):
        r1 = _run()
        r2 = _run()
        assert r1 == r2


class TestSchemaValidation:
    def test_non_dict_blocks(self):
        r = _run("not-a-dict")
        assert r.status is Status.BLOCKED_SCHEMA
        assert "input.schema" in r.criteria_failed

    @pytest.mark.parametrize("key", [
        "profile_name",
        "declared_environment",
        "expires_at_utc",
        "rotation_required",
        "secret_material_present",
    ])
    def test_missing_required_key_blocks(self, key):
        m = _valid_metadata()
        del m[key]
        r = _run(m)
        assert r.status is Status.BLOCKED_SCHEMA
        assert "metadata.required_keys" in r.criteria_failed


class TestProfileName:
    def test_empty_profile_blocks(self):
        m = _valid_metadata()
        m["profile_name"] = ""
        r = _run(m)
        assert r.status is Status.BLOCKED_PROFILE

    def test_whitespace_profile_blocks(self):
        m = _valid_metadata()
        m["profile_name"] = "   "
        r = _run(m)
        assert r.status is Status.BLOCKED_PROFILE

    def test_non_string_profile_blocks(self):
        m = _valid_metadata()
        m["profile_name"] = 42
        r = _run(m)
        assert r.status is Status.BLOCKED_PROFILE


class TestEnvironment:
    def test_expected_live_blocks(self):
        r = _run(expected_environment="live")
        assert r.status is Status.BLOCKED_ENVIRONMENT

    def test_expected_unknown_blocks(self):
        r = _run(expected_environment="unknown")
        assert r.status is Status.BLOCKED_ENVIRONMENT

    def test_declared_live_blocks(self):
        m = _valid_metadata()
        m["declared_environment"] = "live"
        r = _run(m)
        assert r.status is Status.BLOCKED_ENVIRONMENT

    def test_declared_unknown_blocks(self):
        m = _valid_metadata()
        m["declared_environment"] = "unknown"
        r = _run(m)
        assert r.status is Status.BLOCKED_ENVIRONMENT

    def test_declared_empty_blocks(self):
        m = _valid_metadata()
        m["declared_environment"] = ""
        r = _run(m)
        assert r.status is Status.BLOCKED_ENVIRONMENT

    def test_declared_non_string_blocks(self):
        m = _valid_metadata()
        m["declared_environment"] = 123
        r = _run(m)
        assert r.status is Status.BLOCKED_ENVIRONMENT


class TestExpiry:
    def test_invalid_now_utc_blocks(self):
        r = _run(now_utc="not-a-date")
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.not_expired" in r.criteria_failed

    def test_invalid_expires_at_utc_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "not-a-date"
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.expiry_format" in r.criteria_failed

    def test_expired_timestamp_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = _PAST
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY

    def test_equal_expiry_and_now_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = _NOW
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY

    def test_valid_z_suffix(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2027-01-01T00:00:00Z"
        r = _run(m, now_utc="2026-01-01T00:00:00Z")
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER

    def test_valid_plus_zero_offset(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2027-01-01T00:00:00+00:00"
        r = _run(m, now_utc="2026-01-01T00:00:00+00:00")
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER

    def test_future_expiry_parsed_datetime(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2026-06-01T00:00:01Z"
        r = _run(m, now_utc="2026-06-01T00:00:00Z")
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER

    @pytest.mark.parametrize("bad_ts", [
        "2026-13-01T00:00:00Z",
        "2026-01-32T00:00:00Z",
        "2026-01-01T25:00:00Z",
        "2026-01-01T00:61:00Z",
        "2026-01-01T00:00:61Z",
    ])
    def test_invalid_date_time_components_block(self, bad_ts):
        m = _valid_metadata()
        m["expires_at_utc"] = bad_ts
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.expiry_format" in r.criteria_failed

    def test_trailing_garbage_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2027-01-01T00:00:00Z extra"
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY

    def test_missing_timezone_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2027-01-01T00:00:00"
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.expiry_format" in r.criteria_failed

    def test_non_utc_positive_offset_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2027-01-01T00:00:00+05:00"
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.expiry_format" in r.criteria_failed

    def test_non_utc_negative_offset_blocks(self):
        m = _valid_metadata()
        m["expires_at_utc"] = "2027-01-01T00:00:00-03:00"
        r = _run(m)
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.expiry_format" in r.criteria_failed

    def test_naive_now_utc_blocks(self):
        m = _valid_metadata()
        r = _run(m, now_utc="2026-01-01T00:00:00")
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.not_expired" in r.criteria_failed

    def test_non_utc_now_utc_blocks(self):
        m = _valid_metadata()
        r = _run(m, now_utc="2026-01-01T00:00:00+05:00")
        assert r.status is Status.BLOCKED_EXPIRY
        assert "metadata.not_expired" in r.criteria_failed


class TestRotation:
    def test_rotation_true_blocks(self):
        m = _valid_metadata()
        m["rotation_required"] = True
        r = _run(m)
        assert r.status is Status.BLOCKED_ROTATION

    def test_rotation_none_blocks(self):
        m = _valid_metadata()
        m["rotation_required"] = None
        r = _run(m)
        assert r.status is Status.BLOCKED_ROTATION

    def test_rotation_non_bool_blocks(self):
        m = _valid_metadata()
        m["rotation_required"] = "no"
        r = _run(m)
        assert r.status is Status.BLOCKED_ROTATION


class TestSecretMaterial:
    def test_secret_material_true_blocks(self):
        m = _valid_metadata()
        m["secret_material_present"] = True
        r = _run(m)
        assert r.status is Status.BLOCKED_SECRET_MATERIAL

    def test_secret_material_none_blocks(self):
        m = _valid_metadata()
        m["secret_material_present"] = None
        r = _run(m)
        assert r.status is Status.BLOCKED_SECRET_MATERIAL

    def test_secret_material_non_bool_blocks(self):
        m = _valid_metadata()
        m["secret_material_present"] = "false"
        r = _run(m)
        assert r.status is Status.BLOCKED_SECRET_MATERIAL

    def test_secret_material_false_is_allowed(self):
        r = _run()
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER


class TestForbiddenFields:
    @pytest.mark.parametrize("fragment_parts,sep", [
        (("api", "key"), "_"),
        (("api", "key"), ""),
        (("sec", "ret"), ""),
        (("tok", "en"), ""),
        (("pass", "word"), ""),
        (("authori", "zation"), ""),
        (("credential", "value"), "_"),
        (("private", "key"), "_"),
        (("account", "id"), "_"),
        (("account", "number"), "_"),
        (("end", "point"), ""),
        (("base", "url"), "_"),
        (("live", "url"), "_"),
        (("paper", "url"), "_"),
        (("head", "ers"), ""),
        (("ses", "sion"), ""),
    ])
    def test_forbidden_field_name_blocks(self, fragment_parts, sep):
        forbidden_key = sep.join(fragment_parts)
        m = _valid_metadata()
        m[forbidden_key] = "harmless"
        r = _run(m)
        assert r.status is Status.BLOCKED_FORBIDDEN_FIELDS

    def test_forbidden_nested_key_blocks(self):
        m = _valid_metadata()
        nested_key = "api" + "_" + "key"
        m["extra"] = {nested_key: "value"}
        r = _run(m)
        assert r.status is Status.BLOCKED_FORBIDDEN_FIELDS

    def test_forbidden_nested_string_value_blocks(self):
        m = _valid_metadata()
        forbidden_val = "my_" + "sec" + "ret" + "_data"
        m["extra"] = {"safe_key": forbidden_val}
        r = _run(m)
        assert r.status is Status.BLOCKED_FORBIDDEN_FIELDS

    def test_harmless_extra_allowed(self):
        m = _valid_metadata()
        m["notes"] = "just a note"
        r = _run(m)
        assert r.status is Status.CREDENTIAL_METADATA_READY_PAPER


class TestCriteriaOrder:
    def test_exact_criteria_order_on_pass(self):
        r = _run()
        expected = (
            "input.schema",
            "expected_environment.paper_only",
            "metadata.required_keys",
            "metadata.profile_name",
            "metadata.declared_environment",
            "metadata.expiry_format",
            "metadata.not_expired",
            "metadata.rotation_required_false",
            "metadata.secret_material_absent",
            "metadata.forbidden_fields",
            "metadata.safety_flags",
        )
        assert r.criteria_checked == expected

    def test_failure_stops_at_expected_criterion(self):
        r = _run("not-a-dict")
        assert r.criteria_checked == ("input.schema",)
        assert r.criteria_failed == ("input.schema",)


class TestResultContents:
    def test_result_contains_no_raw_input_dict(self):
        m = _valid_metadata()
        r = _run(m)
        for field in dataclasses.fields(r):
            val = getattr(r, field.name)
            assert not isinstance(val, dict), f"{field.name} is a dict"

    def test_blocker_text_contains_no_secret_values(self):
        m = _valid_metadata()
        forbidden_key = "api" + "_" + "key"
        m[forbidden_key] = "actual" + "_" + "sec" + "ret" + "_" + "value"
        r = _run(m)
        assert r.blocker is not None
        blocker_lower = r.blocker.lower()
        assert ("sec" + "ret") not in blocker_lower or "material" in blocker_lower
        assert ("api" + "_" + "key") not in blocker_lower


class TestSourceHygiene:
    def _get_source(self):
        import src.broker.credential_metadata as mod
        return inspect.getsource(mod)

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
        source = self._get_source()
        assert pattern not in source, f"Found forbidden pattern: {pattern}"
