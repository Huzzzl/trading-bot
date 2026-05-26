"""
Tests for manual_position_status_checker_readonly mock-only core.

All tests use injected mock brokers — no real Alpaca calls in any test.
No credentials are read. No orders are submitted, cancelled, or replaced.
No config is mutated. No live ledger is written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.tools.manual_position_status_checker_readonly import (
    _REQUIRED_SYMBOL,
    main,
    run_status_check,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SECRET = "sk-live-super-secret-key-abc123"
_PASS_ARTIFACT: dict[str, Any] = {"result": "PASS"}


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _run(
    tmp_path: Path,
    *,
    cg_data: dict | None = None,
    oo_data: dict | None = None,
    cg_missing: bool = False,
    oo_missing: bool = False,
    symbol: str = _REQUIRED_SYMBOL,
    broker: Any = None,
) -> dict[str, Any]:
    cg_path = tmp_path / "cg.json"
    oo_path = tmp_path / "oo.json"
    if not cg_missing:
        _write_json(cg_path, cg_data if cg_data is not None else _PASS_ARTIFACT)
    if not oo_missing:
        _write_json(oo_path, oo_data if oo_data is not None else _PASS_ARTIFACT)
    return run_status_check(
        credential_guard_path=cg_path,
        operator_override_path=oo_path,
        symbol=symbol,
        broker=broker,
    )


# ---------------------------------------------------------------------------
# Mock broker classes
# ---------------------------------------------------------------------------

class _FakeBroker:
    """Basic fake broker without market session support."""

    def __init__(self, position: Any = None, orders: list | None = None) -> None:
        self._position = position
        self._orders: list = orders if orders is not None else []

    def get_position(self, symbol: str) -> Any:
        return self._position

    def get_open_orders(self, symbol: str) -> list:
        return self._orders


class _FakeBrokerWithSession(_FakeBroker):
    """Fake broker that also implements get_market_session_status."""

    def __init__(
        self,
        position: Any = None,
        orders: list | None = None,
        market_session: str | None = None,
    ) -> None:
        super().__init__(position, orders)
        self._market_session = market_session

    def get_market_session_status(self) -> str | None:
        return self._market_session


# ---------------------------------------------------------------------------
# TestArtifactGates
# ---------------------------------------------------------------------------

class TestArtifactGates:
    def test_missing_credential_guard_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, cg_missing=True)
        assert r["result"] == "BLOCKED"
        assert r["broker_calls_made"] is False

    def test_non_pass_credential_guard_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, cg_data={"result": "FAIL"})
        assert r["result"] == "BLOCKED"
        assert any("credential_guard" in v for v in r["violations"])

    def test_credential_guard_malformed_json_blocked(self, tmp_path: Path) -> None:
        cg_path = tmp_path / "cg.json"
        cg_path.write_text("not-valid-json", encoding="utf-8")
        oo_path = tmp_path / "oo.json"
        _write_json(oo_path, _PASS_ARTIFACT)
        r = run_status_check(
            credential_guard_path=cg_path,
            operator_override_path=oo_path,
            symbol=_REQUIRED_SYMBOL,
            broker=None,
        )
        assert r["result"] == "BLOCKED"

    def test_missing_operator_override_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, oo_missing=True)
        assert r["result"] == "BLOCKED"

    def test_non_pass_operator_override_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, oo_data={"result": "FAIL"})
        assert r["result"] == "BLOCKED"
        assert any("operator_override" in v for v in r["violations"])

    def test_operator_override_malformed_json_blocked(self, tmp_path: Path) -> None:
        cg_path = tmp_path / "cg.json"
        _write_json(cg_path, _PASS_ARTIFACT)
        oo_path = tmp_path / "oo.json"
        oo_path.write_text("{bad json}", encoding="utf-8")
        r = run_status_check(
            credential_guard_path=cg_path,
            operator_override_path=oo_path,
            symbol=_REQUIRED_SYMBOL,
            broker=None,
        )
        assert r["result"] == "BLOCKED"

    def test_both_artifacts_missing_two_violations(self, tmp_path: Path) -> None:
        r = _run(tmp_path, cg_missing=True, oo_missing=True)
        assert r["result"] == "BLOCKED"
        assert len(r["violations"]) >= 2


# ---------------------------------------------------------------------------
# TestSymbolValidation
# ---------------------------------------------------------------------------

class TestSymbolValidation:
    def test_wrong_symbol_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, symbol="AAPL")
        assert r["result"] == "BLOCKED"
        assert any("SPY" in v for v in r["violations"])

    def test_lowercase_spy_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, symbol="spy")
        assert r["result"] == "BLOCKED"

    def test_whitespace_prefixed_symbol_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, symbol=" SPY")
        assert r["result"] == "BLOCKED"

    def test_empty_symbol_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, symbol="")
        assert r["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestInputSecretRedaction
# ---------------------------------------------------------------------------

class TestInputSecretRedaction:
    def test_secret_in_cg_result_absent_from_output(self, tmp_path: Path) -> None:
        r = _run(tmp_path, cg_data={"result": _SECRET})
        assert _SECRET not in json.dumps(r)

    def test_secret_in_cg_result_absent_from_blocker(self, tmp_path: Path) -> None:
        r = _run(tmp_path, cg_data={"result": _SECRET})
        assert _SECRET not in (r["blocker"] or "")

    def test_secret_in_oo_result_absent_from_output(self, tmp_path: Path) -> None:
        r = _run(tmp_path, oo_data={"result": _SECRET})
        assert _SECRET not in json.dumps(r)

    def test_secret_in_oo_result_absent_from_blocker(self, tmp_path: Path) -> None:
        r = _run(tmp_path, oo_data={"result": _SECRET})
        assert _SECRET not in (r["blocker"] or "")

    def test_secret_in_symbol_absent_from_output(self, tmp_path: Path) -> None:
        r = _run(tmp_path, symbol=_SECRET)
        assert _SECRET not in json.dumps(r)

    def test_secret_in_symbol_field_is_invalid(self, tmp_path: Path) -> None:
        r = _run(tmp_path, symbol=_SECRET)
        assert r["symbol"] == "(invalid)"

    def test_secret_in_cg_result_absent_from_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _run(tmp_path, cg_data={"result": _SECRET})
        captured = capsys.readouterr()
        assert _SECRET not in captured.out
        assert _SECRET not in captured.err

    def test_secret_in_oo_result_absent_from_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _run(tmp_path, oo_data={"result": _SECRET})
        captured = capsys.readouterr()
        assert _SECRET not in captured.out
        assert _SECRET not in captured.err

    def test_secret_in_symbol_absent_from_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _run(tmp_path, symbol=_SECRET)
        captured = capsys.readouterr()
        assert _SECRET not in captured.out
        assert _SECRET not in captured.err


# ---------------------------------------------------------------------------
# TestBrokerNone
# ---------------------------------------------------------------------------

class TestBrokerNone:
    def test_broker_none_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["result"] == "BLOCKED"

    def test_broker_none_blocker_message(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["blocker"] == "real broker adapter not implemented"

    def test_broker_none_no_broker_calls(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["broker_calls_made"] is False
        assert r["broker_calls_readonly"] is False

    def test_broker_none_credentials_not_read(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["credentials_read"] is False

    def test_broker_none_position_null(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["position_observed"] is None

    def test_broker_none_open_order_null(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["open_order_observed"] is None

    def test_broker_none_market_session_null(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["market_session_status"] is None


# ---------------------------------------------------------------------------
# TestHappyPath
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_no_position_no_orders_pass(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker(position=None, orders=[]))
        assert r["result"] == "PASS"
        assert r["position_observed"] is False
        assert r["open_order_observed"] is False

    def test_position_exists_no_orders(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker(position={"position_exists": True}, orders=[]))
        assert r["result"] == "PASS"
        assert r["position_observed"] is True
        assert r["open_order_observed"] is False

    def test_no_position_orders_exist(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker(position=None, orders=[{}]))
        assert r["result"] == "PASS"
        assert r["position_observed"] is False
        assert r["open_order_observed"] is True

    def test_position_and_orders_exist(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker(position={"p": True}, orders=[{}, {}]))
        assert r["result"] == "PASS"
        assert r["position_observed"] is True
        assert r["open_order_observed"] is True

    def test_pass_broker_calls_made(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        assert r["broker_calls_made"] is True
        assert r["broker_calls_readonly"] is True

    def test_pass_violations_empty(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        assert r["violations"] == []

    def test_pass_blocker_null(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        assert r["blocker"] is None

    def test_market_session_null_when_broker_has_no_method(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        assert r["market_session_status"] is None


# ---------------------------------------------------------------------------
# TestMarketSessionStatus
# ---------------------------------------------------------------------------

class TestMarketSessionStatus:
    def test_market_session_open(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="open"))
        assert r["result"] == "PASS"
        assert r["market_session_status"] == "open"

    def test_market_session_closed(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="closed"))
        assert r["result"] == "PASS"
        assert r["market_session_status"] == "closed"

    def test_market_session_pre_market(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="pre_market"))
        assert r["result"] == "PASS"
        assert r["market_session_status"] == "pre_market"

    def test_market_session_after_hours(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="after_hours"))
        assert r["result"] == "PASS"
        assert r["market_session_status"] == "after_hours"

    def test_market_session_method_returns_none(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=None))
        assert r["result"] == "PASS"
        assert r["market_session_status"] is None

    def test_market_session_no_method_null(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        assert r["market_session_status"] is None


# ---------------------------------------------------------------------------
# TestMarketSessionStatusRedaction
# ---------------------------------------------------------------------------

class TestMarketSessionStatusRedaction:
    def test_invalid_value_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="OPEN"))
        assert r["result"] == "BLOCKED"
        assert r["blocker"] == "market session status invalid"

    def test_invalid_value_market_session_null_in_output(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="OPEN"))
        assert r["market_session_status"] is None

    def test_secret_value_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=_SECRET))
        assert r["result"] == "BLOCKED"

    def test_secret_value_absent_from_output_json(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=_SECRET))
        assert _SECRET not in json.dumps(r)

    def test_secret_value_absent_from_violations(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=_SECRET))
        for v in r["violations"]:
            assert _SECRET not in v

    def test_secret_value_absent_from_blocker(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=_SECRET))
        assert _SECRET not in (r["blocker"] or "")

    def test_secret_value_absent_from_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        _run(tmp_path, broker=_FakeBrokerWithSession(market_session=_SECRET))
        captured = capsys.readouterr()
        assert _SECRET not in captured.out
        assert _SECRET not in captured.err

    def test_whitespace_variant_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="open "))
        assert r["result"] == "BLOCKED"
        assert r["market_session_status"] is None

    def test_uppercase_variant_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session="Open"))
        assert r["result"] == "BLOCKED"

    def test_all_caps_variant_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=" OPEN"))
        assert r["result"] == "BLOCKED"

    def test_invalid_raw_value_not_echoed_in_output(self, tmp_path: Path) -> None:
        bad_value = "unexpected-session-value-xyz"
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=bad_value))
        assert bad_value not in json.dumps(r)

    def test_invalid_raw_value_not_echoed_in_violations(self, tmp_path: Path) -> None:
        bad_value = "unexpected-session-value-xyz"
        r = _run(tmp_path, broker=_FakeBrokerWithSession(market_session=bad_value))
        for v in r["violations"]:
            assert bad_value not in v


# ---------------------------------------------------------------------------
# TestBrokerException
# ---------------------------------------------------------------------------

class TestBrokerException:
    def test_get_position_raises_blocked(self, tmp_path: Path) -> None:
        class _Broker(_FakeBroker):
            def get_position(self, symbol: str) -> None:
                raise RuntimeError("broker position error")
        r = _run(tmp_path, broker=_Broker())
        assert r["result"] == "BLOCKED"
        assert r["broker_calls_made"] is True

    def test_get_position_raises_secret_absent_from_output(self, tmp_path: Path) -> None:
        class _Broker(_FakeBroker):
            def get_position(self, symbol: str) -> None:
                raise RuntimeError(f"error: {_SECRET}")
        r = _run(tmp_path, broker=_Broker())
        assert _SECRET not in json.dumps(r)

    def test_get_position_raises_secret_absent_from_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        class _Broker(_FakeBroker):
            def get_position(self, symbol: str) -> None:
                raise RuntimeError(f"error: {_SECRET}")
        _run(tmp_path, broker=_Broker())
        captured = capsys.readouterr()
        assert _SECRET not in captured.out
        assert _SECRET not in captured.err

    def test_get_open_orders_raises_blocked(self, tmp_path: Path) -> None:
        class _Broker(_FakeBroker):
            def get_open_orders(self, symbol: str) -> list:
                raise RuntimeError("orders error")
        r = _run(tmp_path, broker=_Broker(position={"p": True}))
        assert r["result"] == "BLOCKED"
        assert r["position_observed"] is True
        assert r["open_order_observed"] is None

    def test_get_open_orders_raises_secret_absent(self, tmp_path: Path) -> None:
        class _Broker(_FakeBroker):
            def get_open_orders(self, symbol: str) -> list:
                raise RuntimeError(f"error: {_SECRET}")
        r = _run(tmp_path, broker=_Broker())
        assert _SECRET not in json.dumps(r)

    def test_get_market_session_raises_blocked(self, tmp_path: Path) -> None:
        class _Broker(_FakeBrokerWithSession):
            def get_market_session_status(self) -> None:
                raise RuntimeError("session error")
        r = _run(tmp_path, broker=_Broker())
        assert r["result"] == "BLOCKED"
        assert r["market_session_status"] is None

    def test_get_market_session_raises_secret_absent(self, tmp_path: Path) -> None:
        class _Broker(_FakeBrokerWithSession):
            def get_market_session_status(self) -> None:
                raise RuntimeError(f"error: {_SECRET}")
        r = _run(tmp_path, broker=_Broker())
        assert _SECRET not in json.dumps(r)


# ---------------------------------------------------------------------------
# TestOutputInvariants
# ---------------------------------------------------------------------------

class TestOutputInvariants:
    _FALSE_FIELDS = [
        "broker_mutation_calls_made",
        "credential_values_exposed",
        "live_submit_enabled",
        "submit_order_reachable",
        "cancel_order_reachable",
        "replace_order_reachable",
        "close_position_reachable",
        "position_decision_made",
        "raw_broker_response_included",
    ]
    _TRUE_FIELDS = [
        "broker_ids_redacted",
        "account_identifiers_redacted",
    ]

    def test_invariants_on_gate_failure(self, tmp_path: Path) -> None:
        r = _run(tmp_path, cg_missing=True)
        for field in self._FALSE_FIELDS:
            assert r[field] is False, f"{field} must be False on gate failure"
        for field in self._TRUE_FIELDS:
            assert r[field] is True, f"{field} must be True on gate failure"

    def test_invariants_on_broker_none(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        for field in self._FALSE_FIELDS:
            assert r[field] is False, f"{field} must be False when broker=None"
        for field in self._TRUE_FIELDS:
            assert r[field] is True, f"{field} must be True when broker=None"

    def test_invariants_on_pass(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        for field in self._FALSE_FIELDS:
            assert r[field] is False, f"{field} must be False on PASS"
        for field in self._TRUE_FIELDS:
            assert r[field] is True, f"{field} must be True on PASS"

    def test_broker_calls_readonly_mirrors_false_on_blocked(self, tmp_path: Path) -> None:
        r = _run(tmp_path)
        assert r["broker_calls_readonly"] is False
        assert r["broker_calls_readonly"] == r["broker_calls_made"]

    def test_broker_calls_readonly_mirrors_true_on_pass(self, tmp_path: Path) -> None:
        r = _run(tmp_path, broker=_FakeBroker())
        assert r["broker_calls_readonly"] is True
        assert r["broker_calls_readonly"] == r["broker_calls_made"]


# ---------------------------------------------------------------------------
# TestOutputAlwaysWritten (CLI)
# ---------------------------------------------------------------------------

class TestOutputAlwaysWritten:
    def test_output_written_on_gate_failure(self, tmp_path: Path) -> None:
        out_path = tmp_path / "out.json"
        argv = [
            "--credential-guard", str(tmp_path / "missing.json"),
            "--operator-override", str(tmp_path / "missing2.json"),
            "--symbol", "SPY",
            "--output", str(out_path),
        ]
        with pytest.raises(SystemExit):
            main(argv)
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["result"] == "BLOCKED"

    def test_output_written_on_broker_none(self, tmp_path: Path) -> None:
        cg_path = tmp_path / "cg.json"
        oo_path = tmp_path / "oo.json"
        _write_json(cg_path, _PASS_ARTIFACT)
        _write_json(oo_path, _PASS_ARTIFACT)
        out_path = tmp_path / "out.json"
        argv = [
            "--credential-guard", str(cg_path),
            "--operator-override", str(oo_path),
            "--symbol", "SPY",
            "--output", str(out_path),
        ]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["result"] == "BLOCKED"
        assert data["blocker"] == "real broker adapter not implemented"

    def test_cli_exit_1_on_blocked(self, tmp_path: Path) -> None:
        cg_path = tmp_path / "cg.json"
        oo_path = tmp_path / "oo.json"
        _write_json(cg_path, _PASS_ARTIFACT)
        _write_json(oo_path, _PASS_ARTIFACT)
        out_path = tmp_path / "out.json"
        argv = [
            "--credential-guard", str(cg_path),
            "--operator-override", str(oo_path),
            "--symbol", "SPY",
            "--output", str(out_path),
        ]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# TestNeverRaises
# ---------------------------------------------------------------------------

class TestNeverRaises:
    def test_never_raises_on_get_position_exception(self, tmp_path: Path) -> None:
        class _Broker(_FakeBroker):
            def get_position(self, symbol: str) -> None:
                raise RuntimeError("unexpected error")
        result = _run(tmp_path, broker=_Broker())
        assert result["result"] == "BLOCKED"

    def test_never_raises_on_missing_artifact(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_missing=True)
        assert result["result"] == "BLOCKED"

    def test_never_raises_on_malformed_artifact(self, tmp_path: Path) -> None:
        cg_path = tmp_path / "cg.json"
        cg_path.write_text("}{bad}", encoding="utf-8")
        oo_path = tmp_path / "oo.json"
        _write_json(oo_path, _PASS_ARTIFACT)
        result = run_status_check(
            credential_guard_path=cg_path,
            operator_override_path=oo_path,
            symbol=_REQUIRED_SYMBOL,
            broker=None,
        )
        assert result["result"] == "BLOCKED"

    def test_never_raises_on_get_market_session_exception(self, tmp_path: Path) -> None:
        class _Broker(_FakeBrokerWithSession):
            def get_market_session_status(self) -> None:
                raise ValueError("session check failed")
        result = _run(tmp_path, broker=_Broker())
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestNoRawIds
# ---------------------------------------------------------------------------

class TestNoRawIds:
    def test_position_broker_dict_values_not_in_output(self, tmp_path: Path) -> None:
        broker = _FakeBroker(
            position={"position_id": "POS-123456", "account_id": "ACC-789", "qty": "10"},
            orders=[],
        )
        r = _run(tmp_path, broker=broker)
        output_text = json.dumps(r)
        assert "POS-123456" not in output_text
        assert "ACC-789" not in output_text
        assert "qty" not in output_text

    def test_order_broker_dict_values_not_in_output(self, tmp_path: Path) -> None:
        broker = _FakeBroker(
            position=None,
            orders=[{"order_id": "ORD-999", "filled_qty": "5", "limit_price": "100.0"}],
        )
        r = _run(tmp_path, broker=broker)
        output_text = json.dumps(r)
        assert "ORD-999" not in output_text
        assert "filled_qty" not in output_text
        assert "limit_price" not in output_text


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

def _non_comment_lines() -> list[str]:
    src = (
        Path(__file__).parent.parent
        / "src" / "tools" / "manual_position_status_checker_readonly.py"
    )
    lines = []
    for line in src.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_part = line.split("#")[0]
        if code_part.strip():
            lines.append(code_part)
    return lines


class TestSourceScans:
    def test_no_alpaca_import(self) -> None:
        src_path = (
            Path(__file__).parent.parent
            / "src" / "tools" / "manual_position_status_checker_readonly.py"
        )
        for line in src_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "import alpaca" not in stripped, f"alpaca import found: {line!r}"
            assert "from alpaca" not in stripped, f"alpaca import found: {line!r}"

    def test_no_requests_import(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "import requests" not in line, f"requests import found: {line!r}"

    def test_no_httpx_import(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "import httpx" not in line, f"httpx import found: {line!r}"

    def test_no_aiohttp_import(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "import aiohttp" not in line, f"aiohttp import found: {line!r}"

    def test_no_urllib_request_import(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            stripped = line.strip()
            assert not (stripped.startswith("import urllib") or stripped.startswith("from urllib")), (
                f"urllib import found: {line!r}"
            )

    def test_no_os_environ(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "os.environ.get(" not in line, f"os.environ.get( found: {line!r}"
            assert "os.environ[" not in line, f"os.environ[ found: {line!r}"

    def test_no_submit_order_call(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "submit_order(" not in line, f"submit_order( found: {line!r}"

    def test_no_cancel_order_call(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "cancel_order(" not in line, f"cancel_order( found: {line!r}"

    def test_no_replace_order_call(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "replace_order(" not in line, f"replace_order( found: {line!r}"

    def test_no_close_position_call(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "close_position(" not in line, f"close_position( found: {line!r}"

    def test_no_close_all_positions_call(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert "close_all_positions(" not in line, f"close_all_positions( found: {line!r}"

    def test_no_post_mutation_marker(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert ".post(" not in line.lower(), f"POST mutation found: {line!r}"

    def test_no_patch_mutation_marker(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert ".patch(" not in line.lower(), f"PATCH mutation found: {line!r}"

    def test_no_delete_mutation_marker(self) -> None:
        lines = _non_comment_lines()
        for line in lines:
            assert ".delete(" not in line.lower(), f"DELETE mutation found: {line!r}"
