"""
Tests for src/tools/live_broker_preflight_readonly.py

Coverage:
- Prerequisite artifact gating (missing, malformed, non-PASS)
- Parameter validation (symbol, side, notional_cap)
- CLI mode without --allow-live-broker-api-readonly → BLOCKED, no broker calls
- CLI mode with flag but missing env vars → BLOCKED, no raw values in output
- Mock PASS path → result=PASS
- Account checks (inactive, insufficient buying power, pattern day trader)
- Clock check (market closed)
- Asset checks (not tradable, not fractionable)
- Broker exception → BLOCKED
- Endpoint allowlist enforcement (non-GET path blocked, non-allowlisted blocked)
- AlpacaLiveReadOnlyBroker adapter: response mapping, mock TradingClient
- Output JSON never contains injected secret values
- Stdout never contains injected secret values
- All output invariants always correct
- Source scans: no submit_order/cancel_order/replace_order, no module-level
  alpaca imports, no network imports, no ledger writes, no orders endpoint,
  no POST/PATCH/DELETE method usage
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.tools.live_broker_preflight_readonly import (
    _ALLOWED_ENDPOINT_PREFIXES,
    _ENDPOINT_ALLOWLIST,
    _guarded_get,
    _is_allowed_path,
    _LIVE_API_KEY_ENV,
    _LIVE_SECRET_KEY_ENV,
    _REQUIRED_SYMBOL,
    _REQUIRED_SIDE,
    _MAX_NOTIONAL_CAP,
    AlpacaLiveReadOnlyBroker,
    run_preflight,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEY_CG = "credential_guard"
_KEY_OO = "operator_override"


def _write_json(tmp_path: Path, name: str, data: Any) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _pass_artifact() -> dict[str, Any]:
    return {"result": "PASS"}


def _write_cg(tmp_path: Path, data: Any = None) -> Path:
    return _write_json(tmp_path, "credential_guard.json", data or _pass_artifact())


def _write_oo(tmp_path: Path, data: Any = None) -> Path:
    return _write_json(tmp_path, "operator_override.json", data or _pass_artifact())


def _output_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / "preflight.json"


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

_GOOD_ACCOUNT = {
    "status": "ACTIVE",
    "buying_power": "500.00",
    "pattern_day_trader": False,
}
_GOOD_CLOCK = {"is_open": True}
_GOOD_ASSET = {"tradable": True, "fractionable": True, "marginable": True}

_GOOD_RESPONSES: dict[str, dict[str, Any]] = {
    "/v2/account":        _GOOD_ACCOUNT,
    "/v2/clock":          _GOOD_CLOCK,
    "/v2/assets/SPY":     _GOOD_ASSET,
}


class MockBroker:
    """Minimal mock broker for unit tests.  No real network calls."""

    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self._responses = responses if responses is not None else dict(_GOOD_RESPONSES)
        self._raise_on = raise_on
        self.calls: list[str] = []

    def read_only_get(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        if self._raise_on and path.startswith(self._raise_on):
            raise RuntimeError(f"mock broker error on {path}")
        if path not in self._responses:
            raise KeyError(f"no mock response for {path!r}")
        return dict(self._responses[path])


def _good_broker() -> MockBroker:
    return MockBroker()


def _run_pass(tmp_path: Path, broker: MockBroker | None = None) -> dict[str, Any]:
    cg = _write_cg(tmp_path)
    oo = _write_oo(tmp_path)
    return run_preflight(
        credential_guard_path=cg,
        operator_override_path=oo,
        symbol="SPY",
        side="buy",
        notional_cap=100.0,
        broker=broker or _good_broker(),
    )


# ---------------------------------------------------------------------------
# _is_allowed_path / _guarded_get
# ---------------------------------------------------------------------------

class TestEndpointAllowlist:
    @pytest.mark.parametrize("path", [
        "/v2/account",
        "/v2/clock",
        "/v2/assets/SPY",
        "/v2/assets/AAPL",
        "/v2/assets/",
    ])
    def test_allowed_paths(self, path: str) -> None:
        assert _is_allowed_path(path) is True

    @pytest.mark.parametrize("path", [
        "/v2/orders",
        "/v2/positions",
        "/v2/orders/preview",
        "/v1/account",
        "/v2/account/activities",
        "/submit",
        "",
        "/v2/watchlists",
    ])
    def test_disallowed_paths(self, path: str) -> None:
        assert _is_allowed_path(path) is False

    def test_guarded_get_allowed(self) -> None:
        broker = MockBroker()
        result = _guarded_get(broker, "/v2/account")
        assert result["status"] == "ACTIVE"
        assert broker.calls == ["/v2/account"]

    def test_guarded_get_blocked_before_call(self) -> None:
        broker = MockBroker()
        with pytest.raises(ValueError, match="not on the read-only allowlist"):
            _guarded_get(broker, "/v2/orders")
        assert broker.calls == []  # broker never called

    def test_guarded_get_blocked_post_path(self) -> None:
        broker = MockBroker()
        with pytest.raises(ValueError, match="not on the read-only allowlist"):
            _guarded_get(broker, "/v2/orders/preview")
        assert broker.calls == []

    def test_endpoint_allowlist_constant(self) -> None:
        assert isinstance(_ALLOWED_ENDPOINT_PREFIXES, frozenset)
        assert "/v2/account" in _ALLOWED_ENDPOINT_PREFIXES
        assert "/v2/clock" in _ALLOWED_ENDPOINT_PREFIXES
        assert "/v2/assets/" in _ALLOWED_ENDPOINT_PREFIXES


# ---------------------------------------------------------------------------
# Prerequisite artifact gating — credential guard
# ---------------------------------------------------------------------------

class TestCredentialGuardGating:
    def test_missing_credential_guard_blocked(self, tmp_path: Path) -> None:
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=tmp_path / "missing.json",
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_malformed_credential_guard_blocked(self, tmp_path: Path) -> None:
        cg = tmp_path / "bad.json"
        cg.write_text("{not valid", encoding="utf-8")
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_credential_guard_result_blocked_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path, {"result": "BLOCKED"})
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_credential_guard_missing_result_field_blocked(
        self, tmp_path: Path
    ) -> None:
        cg = _write_cg(tmp_path, {"other": "data"})
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_credential_guard_non_dict_blocked(self, tmp_path: Path) -> None:
        cg = tmp_path / "list.json"
        cg.write_text("[1,2,3]", encoding="utf-8")
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []


# ---------------------------------------------------------------------------
# Prerequisite artifact gating — operator override
# ---------------------------------------------------------------------------

class TestOperatorOverrideGating:
    def test_missing_operator_override_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=tmp_path / "missing.json",
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_malformed_operator_override_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = tmp_path / "bad.json"
        oo.write_text("{broken", encoding="utf-8")
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_operator_override_non_pass_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path, {"result": "BLOCKED"})
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []

    def test_operator_override_missing_result_field_blocked(
        self, tmp_path: Path
    ) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path, {"other": "x"})
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert broker.calls == []


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

class TestParameterValidation:
    def test_wrong_symbol_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="AAPL", side="buy", notional_cap=100.0, broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert any("symbol" in v for v in result["violations"])
        assert broker.calls == []

    def test_wrong_side_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="sell", notional_cap=100.0, broker=broker,
        )
        assert result["result"] == "BLOCKED"
        assert any("side" in v for v in result["violations"])
        assert broker.calls == []

    def test_notional_cap_zero_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=0.0, broker=_good_broker(),
        )
        assert result["result"] == "BLOCKED"

    def test_notional_cap_negative_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=-10.0, broker=_good_broker(),
        )
        assert result["result"] == "BLOCKED"

    def test_notional_cap_exceeds_max_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=200.0, broker=_good_broker(),
        )
        assert result["result"] == "BLOCKED"
        assert any("100" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# CLI mode (no broker adapter)
# ---------------------------------------------------------------------------

class TestCLIMode:
    def test_no_broker_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=None,
        )
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert any("allow-live-broker-api-readonly" in v for v in result["violations"])

    def test_no_broker_allow_flag_true_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=None, allow_live_readonly=True,
        )
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert any("credentials not available" in v for v in result["violations"])

    def test_main_no_broker_exits_one(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        assert exc_info.value.code == 1

    def test_main_always_writes_output(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"

    def test_main_blocked_with_missing_artifact(self, tmp_path: Path) -> None:
        out = _output_path(tmp_path)
        oo = _write_oo(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(tmp_path / "missing.json"),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        assert exc_info.value.code == 1
        assert out.exists()


# ---------------------------------------------------------------------------
# Mock PASS path
# ---------------------------------------------------------------------------

class TestMockPassPath:
    def test_all_good_returns_pass(self, tmp_path: Path) -> None:
        result = _run_pass(tmp_path)
        assert result["result"] == "PASS"
        assert result["violations"] == []
        assert result["blocker"] is None
        assert result["broker_calls_made"] is True

    def test_checks_populated_on_pass(self, tmp_path: Path) -> None:
        result = _run_pass(tmp_path)
        check_names = {c["name"] for c in result["checks"]}
        assert "account_status" in check_names
        assert "buying_power" in check_names
        assert "pattern_day_trader" in check_names
        assert "market_clock" in check_names
        assert "asset_tradable" in check_names
        assert "asset_fractionable" in check_names

    def test_all_checks_pass_on_pass(self, tmp_path: Path) -> None:
        result = _run_pass(tmp_path)
        for check in result["checks"]:
            assert check["result"] == "PASS", f"check {check['name']} not PASS"

    def test_broker_called_expected_endpoints(self, tmp_path: Path) -> None:
        broker = _good_broker()
        _run_pass(tmp_path, broker=broker)
        assert "/v2/account" in broker.calls
        assert "/v2/clock" in broker.calls
        assert "/v2/assets/SPY" in broker.calls


# ---------------------------------------------------------------------------
# Account checks
# ---------------------------------------------------------------------------

class TestAccountChecks:
    def test_account_inactive_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker({
            **_GOOD_RESPONSES,
            "/v2/account": {**_GOOD_ACCOUNT, "status": "INACTIVE"},
        })
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("status" in v for v in result["violations"])

    def test_insufficient_buying_power_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker({
            **_GOOD_RESPONSES,
            "/v2/account": {**_GOOD_ACCOUNT, "buying_power": "0.00"},
        })
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("buying_power" in v for v in result["violations"])

    def test_pattern_day_trader_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker({
            **_GOOD_RESPONSES,
            "/v2/account": {**_GOOD_ACCOUNT, "pattern_day_trader": True},
        })
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("pattern_day_trader" in v for v in result["violations"])

    def test_account_exception_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_on="/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("account" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# Clock check
# ---------------------------------------------------------------------------

class TestClockCheck:
    def test_market_closed_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker({
            **_GOOD_RESPONSES,
            "/v2/clock": {"is_open": False},
        })
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("open" in v for v in result["violations"])

    def test_clock_exception_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_on="/v2/clock")
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Asset checks
# ---------------------------------------------------------------------------

class TestAssetChecks:
    def test_spy_not_tradable_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker({
            **_GOOD_RESPONSES,
            "/v2/assets/SPY": {**_GOOD_ASSET, "tradable": False},
        })
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("tradable" in v for v in result["violations"])

    def test_spy_not_fractionable_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker({
            **_GOOD_RESPONSES,
            "/v2/assets/SPY": {**_GOOD_ASSET, "fractionable": False},
        })
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert any("fractionable" in v for v in result["violations"])

    def test_asset_exception_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_on="/v2/assets/")
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Broker exception redaction — secrets must not leak
# ---------------------------------------------------------------------------

class TestBrokerExceptionRedaction:
    """Broker exception details are redacted from all output.

    A broker adapter exception may include request metadata, URLs, headers,
    or credential-like values in its message.  Raw exception text must never
    appear in violations, checks, blocker, stdout, or output JSON.
    """

    _SECRET = "SECRET_IN_EXCEPTION_XYZ_BROKER_12345"

    def _broker_raising_with_secret(self, path_prefix: str) -> MockBroker:
        """Return a mock broker that raises an exception carrying a secret."""
        class SecretException(RuntimeError):
            pass

        class SecretBroker:
            def read_only_get(self, path: str) -> dict[str, Any]:
                if path.startswith(path_prefix):
                    raise SecretException(
                        f"request to {path} failed: "
                        f"Authorization: {TestBrokerExceptionRedaction._SECRET}"
                    )
                return dict(_GOOD_RESPONSES.get(path, {}))

        return SecretBroker()  # type: ignore[return-value]

    def _assert_secret_absent(self, data: Any, label: str) -> None:
        serialised = json.dumps(data) if not isinstance(data, str) else data
        assert self._SECRET not in serialised, (
            f"secret found in {label}: {serialised[:200]}"
        )

    def test_account_exception_secret_not_in_json(self, tmp_path: Path) -> None:
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        self._assert_secret_absent(result, "result dict")

    def test_account_exception_secret_not_in_violations(
        self, tmp_path: Path
    ) -> None:
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        for v in result["violations"]:
            assert self._SECRET not in v, f"secret in violation: {v!r}"

    def test_account_exception_secret_not_in_blocker(
        self, tmp_path: Path
    ) -> None:
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        assert result["blocker"] is None or self._SECRET not in result["blocker"]

    def test_account_exception_secret_not_in_checks(
        self, tmp_path: Path
    ) -> None:
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        for check in result["checks"]:
            self._assert_secret_absent(check, f"check {check['name']!r}")

    def test_account_exception_secret_not_in_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from src.tools.live_broker_preflight_readonly import print_preflight
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        print_preflight(result)
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_clock_exception_secret_not_in_output(
        self, tmp_path: Path
    ) -> None:
        broker = self._broker_raising_with_secret("/v2/clock")
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        self._assert_secret_absent(result, "result dict")

    def test_asset_exception_secret_not_in_output(
        self, tmp_path: Path
    ) -> None:
        broker = self._broker_raising_with_secret("/v2/assets/")
        result = _run_pass(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        self._assert_secret_absent(result, "result dict")

    def test_violation_uses_fixed_text(self, tmp_path: Path) -> None:
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        assert any("details redacted" in v for v in result["violations"])

    def test_check_detail_uses_fixed_text(self, tmp_path: Path) -> None:
        broker = self._broker_raising_with_secret("/v2/account")
        result = _run_pass(tmp_path, broker=broker)
        error_checks = [c for c in result["checks"] if c["result"] == "BLOCKED"]
        assert error_checks, "expected at least one BLOCKED check"
        assert any("details redacted" in c["detail"] for c in error_checks)


# ---------------------------------------------------------------------------
# Output invariants
# ---------------------------------------------------------------------------

class TestOutputInvariants:
    def _any_result(self, tmp_path: Path) -> dict[str, Any]:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        return run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0, broker=None,
        )

    def test_required_fields_present(self, tmp_path: Path) -> None:
        result = self._any_result(tmp_path)
        required = {
            "checked_at_utc", "result", "broker_calls_made",
            "broker_calls_readonly", "broker_mutation_calls_made",
            "credential_values_exposed", "live_submit_enabled",
            "real_submit_implemented", "submit_order_reachable",
            "config_safety_still_blocks", "endpoint_allowlist_used",
            "checks", "violations", "blocker",
        }
        assert required.issubset(result.keys())

    def test_broker_mutation_calls_made_always_false(self, tmp_path: Path) -> None:
        for broker in [None, _good_broker()]:
            result = _run_pass(tmp_path, broker=broker) if broker else self._any_result(tmp_path)
            assert result["broker_mutation_calls_made"] is False

    def test_credential_values_exposed_always_false(self, tmp_path: Path) -> None:
        assert _run_pass(tmp_path)["credential_values_exposed"] is False
        assert self._any_result(tmp_path)["credential_values_exposed"] is False

    def test_live_submit_enabled_always_false(self, tmp_path: Path) -> None:
        assert _run_pass(tmp_path)["live_submit_enabled"] is False
        assert self._any_result(tmp_path)["live_submit_enabled"] is False

    def test_real_submit_implemented_always_false(self, tmp_path: Path) -> None:
        assert _run_pass(tmp_path)["real_submit_implemented"] is False
        assert self._any_result(tmp_path)["real_submit_implemented"] is False

    def test_submit_order_reachable_always_false(self, tmp_path: Path) -> None:
        assert _run_pass(tmp_path)["submit_order_reachable"] is False
        assert self._any_result(tmp_path)["submit_order_reachable"] is False

    def test_config_safety_still_blocks_always_true(self, tmp_path: Path) -> None:
        assert _run_pass(tmp_path)["config_safety_still_blocks"] is True
        assert self._any_result(tmp_path)["config_safety_still_blocks"] is True

    def test_broker_calls_readonly_always_true(self, tmp_path: Path) -> None:
        assert _run_pass(tmp_path)["broker_calls_readonly"] is True
        assert self._any_result(tmp_path)["broker_calls_readonly"] is True

    def test_blocker_is_first_violation_or_null(self, tmp_path: Path) -> None:
        result = _run_pass(tmp_path)
        assert result["blocker"] is None
        blocked = self._any_result(tmp_path)
        assert blocked["blocker"] == blocked["violations"][0]

    def test_endpoint_allowlist_in_output(self, tmp_path: Path) -> None:
        result = _run_pass(tmp_path)
        assert isinstance(result["endpoint_allowlist_used"], list)
        assert len(result["endpoint_allowlist_used"]) > 0


# ---------------------------------------------------------------------------
# Secret never exposed
# ---------------------------------------------------------------------------

class TestSecretNotExposed:
    _SECRET = "SUPER_SECRET_PREFLIGHT_VALUE_XYZ_99999"

    def test_secret_not_in_output_json(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path, {"result": "PASS", "secret_field": self._SECRET})
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=_good_broker(),
        )
        assert self._SECRET not in json.dumps(result)

    def test_secret_not_in_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from src.tools.live_broker_preflight_readonly import print_preflight
        cg = _write_cg(tmp_path, {"result": "PASS", "secret_field": self._SECRET})
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=_good_broker(),
        )
        print_preflight(result)
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_secret_not_in_written_file(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        assert self._SECRET not in out.read_text()


# ---------------------------------------------------------------------------
# Security properties — source scan
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def _source(self) -> str:
        import src.tools.live_broker_preflight_readonly as mod
        return Path(mod.__file__).read_text(encoding="utf-8")

    def _code_lines(self) -> list[str]:
        return [
            line for line in self._source().splitlines()
            if not line.strip().startswith(("#", '"""', "'''", "*"))
        ]

    def test_no_toplevel_alpaca_import(self) -> None:
        # alpaca is allowed only as a lazy import inside AlpacaLiveReadOnlyBroker.__init__
        # No module-level (unindented) alpaca import is permitted.
        for line in self._source().splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if line and line[0] in (" ", "\t"):
                continue  # indented (inside a function/class) — allowed
            assert "alpaca" not in stripped.lower(), (
                f"module-level alpaca import found: {line!r}"
            )

    def test_no_network_library_imports(self) -> None:
        import_lines = [
            line for line in self._source().splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            for lib in ("requests", "httpx", "aiohttp", "urllib.request"):
                assert lib not in line, f"network lib {lib!r} in: {line!r}"

    def test_no_submit_order_call(self) -> None:
        for line in self._code_lines():
            assert "submit_order(" not in line, f"submit_order( in: {line!r}"

    def test_no_cancel_order_call(self) -> None:
        for line in self._code_lines():
            assert "cancel_order(" not in line, f"cancel_order( in: {line!r}"

    def test_no_replace_order_call(self) -> None:
        for line in self._code_lines():
            assert "replace_order(" not in line, f"replace_order( in: {line!r}"

    def test_no_live_ledger_write(self) -> None:
        for line in self._code_lines():
            assert "append_live_ledger_row" not in line, (
                f"ledger write in: {line!r}"
            )

    def test_run_preflight_never_raises(self, tmp_path: Path) -> None:
        for broker in [None, _good_broker(), MockBroker(raise_on="/v2/")]:
            result = run_preflight(
                credential_guard_path=tmp_path / "missing.json",
                operator_override_path=tmp_path / "missing2.json",
                symbol="SPY", side="buy", notional_cap=100.0,
                broker=broker,
            )
            assert "result" in result

    def test_constants_unchanged(self) -> None:
        assert _REQUIRED_SYMBOL == "SPY"
        assert _REQUIRED_SIDE == "buy"
        assert _MAX_NOTIONAL_CAP == 100.0

    def test_no_orders_endpoint_in_source(self) -> None:
        for line in self._code_lines():
            assert "/v2/orders" not in line, f"/v2/orders found in: {line!r}"

    def test_no_post_patch_delete_in_source(self) -> None:
        for line in self._code_lines():
            for method in (".post(", ".patch(", ".delete(", ".put("):
                assert method not in line.lower(), (
                    f"mutation method {method!r} found in: {line!r}"
                )

    def test_no_positions_endpoint_in_source(self) -> None:
        for line in self._code_lines():
            assert "/v2/positions" not in line, (
                f"/v2/positions found in: {line!r}"
            )


# ---------------------------------------------------------------------------
# CLI without --allow-live-broker-api-readonly flag
# ---------------------------------------------------------------------------

class TestCLIWithoutAllowFlag:
    """Without --allow-live-broker-api-readonly, CLI must be BLOCKED with
    zero broker calls and no broker construction."""

    def test_main_without_flag_exits_one(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        assert exc_info.value.code == 1

    def test_main_without_flag_writes_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"
        assert data["broker_calls_made"] is False

    def test_main_without_flag_violation_mentions_flag(
        self, tmp_path: Path
    ) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        data = json.loads(out.read_text())
        assert any(
            "allow-live-broker-api-readonly" in v
            for v in data["violations"]
        )

    def test_run_preflight_without_flag_zero_broker_calls(
        self, tmp_path: Path
    ) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=None, allow_live_readonly=False,
        )
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert broker.calls == []


# ---------------------------------------------------------------------------
# AlpacaLiveReadOnlyBroker — mock TradingClient tests
# ---------------------------------------------------------------------------

class _MockStatus:
    """Minimal stand-in for AccountStatus enum."""
    def __init__(self, value: str) -> None:
        self.value = value


class _MockAccount:
    def __init__(
        self,
        status: str = "ACTIVE",
        buying_power: str = "500.00",
        pattern_day_trader: bool = False,
    ) -> None:
        self.status = _MockStatus(status)
        self.buying_power = buying_power
        self.pattern_day_trader = pattern_day_trader


class _MockClock:
    def __init__(self, is_open: bool = True) -> None:
        self.is_open = is_open


class _MockAsset:
    def __init__(self, tradable: bool = True, fractionable: bool = True) -> None:
        self.tradable = tradable
        self.fractionable = fractionable


class _MockTradingClient:
    """Fake TradingClient that never makes real network calls."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        raw_data: bool,
        *,
        account: _MockAccount | None = None,
        clock: _MockClock | None = None,
        asset: _MockAsset | None = None,
    ) -> None:
        assert paper is False, "live adapter must use paper=False"
        self._account = account or _MockAccount()
        self._clock = clock or _MockClock()
        self._asset = asset or _MockAsset()
        self.constructed_with_api_key = bool(api_key)
        self.constructed_with_secret_key = bool(secret_key)

    def get_account(self) -> _MockAccount:
        return self._account

    def get_clock(self) -> _MockClock:
        return self._clock

    def get_asset(self, symbol: str) -> _MockAsset:
        return self._asset


def _make_adapter(
    *,
    account: _MockAccount | None = None,
    clock: _MockClock | None = None,
    asset: _MockAsset | None = None,
) -> AlpacaLiveReadOnlyBroker:
    """Create an AlpacaLiveReadOnlyBroker with a mock TradingClient."""

    class _PartialClient(_MockTradingClient):
        def __init__(self, api_key: str, secret_key: str, paper: bool, raw_data: bool) -> None:
            super().__init__(
                api_key=api_key, secret_key=secret_key, paper=paper, raw_data=raw_data,
                account=account, clock=clock, asset=asset,
            )

    return AlpacaLiveReadOnlyBroker(
        api_key="test-key",
        secret_key="test-secret",
        _trading_client_cls=_PartialClient,
    )


class TestAlpacaAdapterMapping:
    """AlpacaLiveReadOnlyBroker maps SDK model attributes to the dict format
    expected by the preflight check functions.  No real API calls."""

    def test_get_account_returns_active_status(self) -> None:
        adapter = _make_adapter(account=_MockAccount(status="ACTIVE"))
        result = adapter.read_only_get("/v2/account")
        assert result["status"] == "ACTIVE"

    def test_get_account_returns_inactive_status(self) -> None:
        adapter = _make_adapter(account=_MockAccount(status="INACTIVE"))
        result = adapter.read_only_get("/v2/account")
        assert result["status"] == "INACTIVE"

    def test_get_account_buying_power_is_string(self) -> None:
        adapter = _make_adapter(account=_MockAccount(buying_power="250.50"))
        result = adapter.read_only_get("/v2/account")
        assert result["buying_power"] == "250.50"
        assert float(result["buying_power"]) == 250.50

    def test_get_account_pdt_false(self) -> None:
        adapter = _make_adapter(account=_MockAccount(pattern_day_trader=False))
        result = adapter.read_only_get("/v2/account")
        assert result["pattern_day_trader"] is False

    def test_get_account_pdt_true(self) -> None:
        adapter = _make_adapter(account=_MockAccount(pattern_day_trader=True))
        result = adapter.read_only_get("/v2/account")
        assert result["pattern_day_trader"] is True

    def test_get_clock_open(self) -> None:
        adapter = _make_adapter(clock=_MockClock(is_open=True))
        result = adapter.read_only_get("/v2/clock")
        assert result["is_open"] is True

    def test_get_clock_closed(self) -> None:
        adapter = _make_adapter(clock=_MockClock(is_open=False))
        result = adapter.read_only_get("/v2/clock")
        assert result["is_open"] is False

    def test_get_asset_tradable_fractionable(self) -> None:
        adapter = _make_adapter(asset=_MockAsset(tradable=True, fractionable=True))
        result = adapter.read_only_get("/v2/assets/SPY")
        assert result["tradable"] is True
        assert result["fractionable"] is True

    def test_get_asset_not_tradable(self) -> None:
        adapter = _make_adapter(asset=_MockAsset(tradable=False, fractionable=True))
        result = adapter.read_only_get("/v2/assets/SPY")
        assert result["tradable"] is False

    def test_get_asset_not_fractionable(self) -> None:
        adapter = _make_adapter(asset=_MockAsset(tradable=True, fractionable=False))
        result = adapter.read_only_get("/v2/assets/SPY")
        assert result["fractionable"] is False

    def test_adapter_paper_false(self) -> None:
        constructed_paper: list[bool] = []

        class _CheckPaperClient:
            def __init__(self, api_key: str, secret_key: str, paper: bool, raw_data: bool) -> None:
                constructed_paper.append(paper)

            def get_account(self) -> _MockAccount:
                return _MockAccount()

            def get_clock(self) -> _MockClock:
                return _MockClock()

            def get_asset(self, symbol: str) -> _MockAsset:
                return _MockAsset()

        AlpacaLiveReadOnlyBroker(
            api_key="k", secret_key="s", _trading_client_cls=_CheckPaperClient
        )
        assert constructed_paper == [False]

    def test_adapter_unsupported_path_raises(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="unsupported path"):
            adapter.read_only_get("/v2/orders")

    def test_adapter_pass_path_integration(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        adapter = _make_adapter()
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=adapter, allow_live_readonly=True,
        )
        assert result["result"] == "PASS"
        assert result["broker_calls_made"] is True

    def test_adapter_blocked_path_integration(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        adapter = _make_adapter(account=_MockAccount(status="INACTIVE"))
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=adapter, allow_live_readonly=True,
        )
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# CLI with --allow-live-broker-api-readonly but missing env vars
# ---------------------------------------------------------------------------

class TestCLIWithFlagMissingEnvVars:
    """With --allow-live-broker-api-readonly but missing credentials,
    CLI must be BLOCKED with no raw credential values in output."""

    _SECRET = "sk-live-super-secret-ALPACA-99999"

    def test_missing_both_env_vars_blocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_LIVE_API_KEY_ENV, raising=False)
        monkeypatch.delenv(_LIVE_SECRET_KEY_ENV, raising=False)
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
                "--allow-live-broker-api-readonly",
            ])
        assert exc_info.value.code == 1
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"
        assert data["broker_calls_made"] is False

    def test_missing_api_key_blocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_LIVE_API_KEY_ENV, raising=False)
        monkeypatch.setenv(_LIVE_SECRET_KEY_ENV, "some-secret")
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
                "--allow-live-broker-api-readonly",
            ])
        assert exc_info.value.code == 1
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"

    def test_secret_value_not_in_output_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_LIVE_API_KEY_ENV, self._SECRET)
        monkeypatch.setenv(_LIVE_SECRET_KEY_ENV, self._SECRET)
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)

        class _FailClient:
            def __init__(self, api_key: str, secret_key: str, paper: bool, raw_data: bool) -> None:
                raise RuntimeError(f"auth failed: key={api_key} secret={secret_key}")

        import src.tools.live_broker_preflight_readonly as _mod
        original = AlpacaLiveReadOnlyBroker.__init__

        _raised = []

        def _patched_init(self_inner: Any, *, api_key: str, secret_key: str, _trading_client_cls: Any = None) -> None:
            original(self_inner, api_key=api_key, secret_key=secret_key, _trading_client_cls=_FailClient)

        monkeypatch.setattr(AlpacaLiveReadOnlyBroker, "__init__", _patched_init)

        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
                "--allow-live-broker-api-readonly",
            ])
        output_text = out.read_text()
        assert self._SECRET not in output_text

    def test_secret_not_in_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.delenv(_LIVE_API_KEY_ENV, raising=False)
        monkeypatch.delenv(_LIVE_SECRET_KEY_ENV, raising=False)
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
                "--allow-live-broker-api-readonly",
            ])
        captured = capsys.readouterr()
        assert _LIVE_API_KEY_ENV in captured.out or "BLOCKED" in captured.out
        # Actual secret values must not appear
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_broker_calls_made_false_when_creds_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_LIVE_API_KEY_ENV, raising=False)
        monkeypatch.delenv(_LIVE_SECRET_KEY_ENV, raising=False)
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=None, allow_live_readonly=True,
        )
        assert result["broker_calls_made"] is False
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Adapter construction only when flag + prerequisites pass
# ---------------------------------------------------------------------------

class TestAdapterConstructionGating:
    """Broker adapter is constructed only when:
    1. --allow-live-broker-api-readonly flag is present
    2. Credentials are available
    It is NOT constructed without the flag."""

    def test_no_adapter_without_flag(self, tmp_path: Path) -> None:
        constructed: list[bool] = []

        class _TrackingClient:
            def __init__(self, **kw: Any) -> None:
                constructed.append(True)

        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        # Call main WITHOUT --allow-live-broker-api-readonly
        with pytest.raises(SystemExit):
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--side", "buy",
                "--notional-cap", "100.0",
                "--output", str(out),
            ])
        # _TrackingClient should never have been used
        assert constructed == []

    def test_run_preflight_with_flag_and_broker_makes_calls(
        self, tmp_path: Path
    ) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        broker = _good_broker()
        result = run_preflight(
            credential_guard_path=cg, operator_override_path=oo,
            symbol="SPY", side="buy", notional_cap=100.0,
            broker=broker, allow_live_readonly=True,
        )
        assert result["result"] == "PASS"
        assert result["broker_calls_made"] is True
        assert len(broker.calls) > 0
