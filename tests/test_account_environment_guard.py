"""Tests for src/broker/account_environment_guard.py — S43."""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from src.broker.account_environment_guard import (
    AccountEnvironmentStatus,
    AccountEnvironmentVerificationResult,
    verify_account_environment,
)

Status = AccountEnvironmentStatus


def _all_paper():
    return dict(
        expected_environment="paper",
        credential_environment="paper",
        adapter_environment="paper",
        broker_reported_environment="paper",
    )


def _run(**overrides):
    kwargs = _all_paper()
    kwargs.update(overrides)
    return verify_account_environment(**kwargs)


def _assert_safety_flags(r: AccountEnvironmentVerificationResult) -> None:
    assert r.broker_calls_made is False
    assert r.credentials_read is False
    assert r.network_calls_made is False
    assert r.order_action_requested is False
    assert r.live_trading_allowed is False


class TestValidPass:
    def test_all_paper_passes(self):
        r = _run()
        assert r.status is Status.VERIFIED_PAPER
        assert r.result == "PASS"
        assert r.blocker is None

    def test_result_is_frozen(self):
        r = _run()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = Status.BLOCKED_SCHEMA  # type: ignore[misc]

    def test_safety_flags_on_pass(self):
        _assert_safety_flags(_run())

    def test_pass_result_contains_observation_metadata(self):
        r = _run()
        assert r.expected_environment == "paper"
        assert r.credential_environment == "paper"
        assert r.adapter_environment == "paper"
        assert r.broker_reported_environment == "paper"


class TestSafetyFlagsOnBlocked:
    @pytest.mark.parametrize("field", [
        "expected_environment",
        "credential_environment",
        "adapter_environment",
        "broker_reported_environment",
    ])
    def test_safety_flags_on_live(self, field):
        _assert_safety_flags(_run(**{field: "live"}))

    def test_safety_flags_on_schema_error(self):
        _assert_safety_flags(_run(expected_environment=""))

    def test_safety_flags_on_ambiguous(self):
        _assert_safety_flags(_run(broker_reported_environment="something"))


class TestDeterminism:
    def test_deterministic_output(self):
        r1 = _run()
        r2 = _run()
        assert r1 == r2

    def test_no_adapter_object(self):
        r = _run()
        for field in dataclasses.fields(r):
            val = getattr(r, field.name)
            assert not hasattr(val, "connect"), f"{field.name} has connect method"
            assert not hasattr(val, "request"), f"{field.name} has request method"


class TestEmptyValues:
    @pytest.mark.parametrize("field", [
        "expected_environment",
        "credential_environment",
        "adapter_environment",
        "broker_reported_environment",
    ])
    def test_empty_blocks(self, field):
        r = _run(**{field: ""})
        assert r.status is Status.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_environment",
        "credential_environment",
        "adapter_environment",
        "broker_reported_environment",
    ])
    def test_whitespace_blocks(self, field):
        r = _run(**{field: "   "})
        assert r.status is Status.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_environment",
        "credential_environment",
        "adapter_environment",
        "broker_reported_environment",
    ])
    def test_none_blocks(self, field):
        r = _run(**{field: None})
        assert r.status is Status.BLOCKED_SCHEMA

    @pytest.mark.parametrize("field", [
        "expected_environment",
        "credential_environment",
        "adapter_environment",
        "broker_reported_environment",
    ])
    def test_non_string_blocks(self, field):
        r = _run(**{field: 42})
        assert r.status is Status.BLOCKED_SCHEMA


class TestLiveBlocking:
    def test_expected_live_blocks(self):
        r = _run(expected_environment="live")
        assert r.status is Status.BLOCKED_LIVE_ENVIRONMENT

    def test_credential_live_blocks(self):
        r = _run(credential_environment="live")
        assert r.status is Status.BLOCKED_LIVE_ENVIRONMENT

    def test_adapter_live_blocks(self):
        r = _run(adapter_environment="live")
        assert r.status is Status.BLOCKED_LIVE_ENVIRONMENT

    def test_broker_reported_live_blocks(self):
        r = _run(broker_reported_environment="live")
        assert r.status is Status.BLOCKED_LIVE_ENVIRONMENT


class TestAmbiguousBlocking:
    def test_uppercase_paper_blocks_ambiguous(self):
        r = _run(expected_environment="PAPER")
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT

    @pytest.mark.parametrize("alias", ["sandbox", "demo", "test", "paper-trading"])
    def test_alias_blocks_ambiguous(self, alias):
        r = _run(expected_environment=alias)
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT

    def test_unknown_environment_blocks_ambiguous(self):
        r = _run(expected_environment="staging")
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT

    def test_ambiguous_broker_reported_blocks(self):
        r = _run(broker_reported_environment="maybe-paper")
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT

    def test_ambiguous_credential_blocks(self):
        r = _run(credential_environment="sandbox")
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT

    def test_ambiguous_adapter_blocks(self):
        r = _run(adapter_environment="demo")
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT


class TestNoFallback:
    def test_mismatched_broker_blocks(self):
        r = _run(broker_reported_environment="staging")
        assert r.status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT

    def test_no_fallback_from_live(self):
        r = _run(expected_environment="live")
        assert r.status is Status.BLOCKED_LIVE_ENVIRONMENT
        assert r.result == "BLOCKED"


class TestEnumReachability:
    def test_all_enum_members_documented(self):
        expected = {
            "NOT_VERIFIED",
            "VERIFIED_PAPER",
            "BLOCKED_SCHEMA",
            "BLOCKED_LIVE_ENVIRONMENT",
            "BLOCKED_AMBIGUOUS_ENVIRONMENT",
            "BLOCKED_SAFETY",
        }
        actual = {m.name for m in Status}
        assert actual == expected

    def test_verified_paper_reachable(self):
        assert _run().status is Status.VERIFIED_PAPER

    def test_blocked_schema_reachable(self):
        assert _run(expected_environment="").status is Status.BLOCKED_SCHEMA

    def test_blocked_live_reachable(self):
        assert _run(expected_environment="live").status is Status.BLOCKED_LIVE_ENVIRONMENT

    def test_blocked_ambiguous_reachable(self):
        assert _run(expected_environment="staging").status is Status.BLOCKED_AMBIGUOUS_ENVIRONMENT


class TestCriteriaOrder:
    def test_exact_criteria_order_on_pass(self):
        r = _run()
        expected = (
            "environment.schema",
            "environment.expected_paper",
            "environment.credential_paper",
            "environment.adapter_paper",
            "environment.broker_reported_paper",
            "environment.no_live",
            "environment.safety_flags",
        )
        assert r.criteria_checked == expected

    def test_fail_closed_first_failure(self):
        r = _run(expected_environment="live")
        assert r.criteria_checked == (
            "environment.schema",
            "environment.expected_paper",
        )
        assert r.criteria_failed == ("environment.expected_paper",)

    def test_ambiguous_stops_at_field_criterion(self):
        r = _run(credential_environment="sandbox")
        assert r.criteria_checked == (
            "environment.schema",
            "environment.expected_paper",
            "environment.credential_paper",
        )
        assert r.criteria_failed == ("environment.credential_paper",)


class TestSourceHygiene:
    def _get_source(self):
        import src.broker.account_environment_guard as mod
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
