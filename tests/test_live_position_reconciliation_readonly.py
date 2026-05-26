"""
Tests for src/tools/live_position_reconciliation_readonly.py

Coverage:
- Prerequisite artifact gating (missing, malformed, non-PASS)
- Symbol validation (exact match — wrong value, wrong case, whitespace)
- CLI / broker-None gate → BLOCKED, no broker call, no credential read
- Mock happy paths (no position/no order; position only; order only; both)
- Broker exception → BLOCKED, secret text absent from all output
- Output always written (PASS and BLOCKED paths)
- run_reconciliation never raises
- Output invariants (mutation/credential/submit/cancel/replace fields always
  false; broker_ids_redacted, account_identifiers_redacted always true)
- No raw IDs in output or stdout
- Source scans: no Alpaca/network imports, no os.environ, no mutation
  method names, no POST/PATCH/DELETE strings, no ledger writes
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.tools.live_position_reconciliation_readonly import (
    _REQUIRED_SYMBOL,
    _read_artifact,
    run_reconciliation,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: Path, name: str, data: Any) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _pass_artifact() -> dict[str, Any]:
    return {"result": "PASS"}


def _write_cg(tmp_path: Path, data: Any = None) -> Path:
    return _write_json(tmp_path, "cg.json", data if data is not None else _pass_artifact())


def _write_oo(tmp_path: Path, data: Any = None) -> Path:
    return _write_json(tmp_path, "oo.json", data if data is not None else _pass_artifact())


def _output_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / "recon.json"


def _run(
    tmp_path: Path,
    *,
    cg_data: Any = None,
    oo_data: Any = None,
    cg_missing: bool = False,
    oo_missing: bool = False,
    symbol: str = _REQUIRED_SYMBOL,
    broker: Any = None,
) -> dict[str, Any]:
    cg_path = (
        tmp_path / "missing_cg.json"
        if cg_missing
        else _write_cg(tmp_path, cg_data)
    )
    oo_path = (
        tmp_path / "missing_oo.json"
        if oo_missing
        else _write_oo(tmp_path, oo_data)
    )
    return run_reconciliation(
        credential_guard_path=cg_path,
        operator_override_path=oo_path,
        symbol=symbol,
        broker=broker,
    )


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

class MockPositionBroker:
    """Configurable mock broker for unit tests.  No network calls."""

    def __init__(
        self,
        *,
        position: dict[str, Any] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        raise_on_get_position: Exception | None = None,
        raise_on_get_open_orders: Exception | None = None,
    ) -> None:
        self._position = position
        self._open_orders = open_orders if open_orders is not None else []
        self._raise_on_get_position = raise_on_get_position
        self._raise_on_get_open_orders = raise_on_get_open_orders
        self.get_position_calls: list[str] = []
        self.get_open_orders_calls: list[str] = []

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        self.get_position_calls.append(symbol)
        if self._raise_on_get_position is not None:
            raise self._raise_on_get_position
        return self._position

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        self.get_open_orders_calls.append(symbol)
        if self._raise_on_get_open_orders is not None:
            raise self._raise_on_get_open_orders
        return self._open_orders


def _source_lines() -> list[str]:
    import src.tools.live_position_reconciliation_readonly as mod
    import inspect
    source = inspect.getsource(mod)
    return source.splitlines()


def _non_comment_lines() -> list[str]:
    return [
        line for line in _source_lines()
        if not line.strip().startswith("#")
    ]


def _source_text() -> str:
    import src.tools.live_position_reconciliation_readonly as mod
    import inspect
    return inspect.getsource(mod)


# ---------------------------------------------------------------------------
# TestArtifactGates
# ---------------------------------------------------------------------------

class TestArtifactGates:
    def test_missing_credential_guard_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_missing=True)
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert result["blocker"] is not None
        assert "credential_guard" in result["blocker"]

    def test_credential_guard_non_pass_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_data={"result": "BLOCKED"})
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert result["blocker"] == "credential_guard result must be PASS"

    def test_credential_guard_missing_result_field_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_data={"status": "ok"})
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False

    def test_missing_operator_override_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, oo_missing=True)
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert result["blocker"] is not None
        assert "operator_override" in result["blocker"]

    def test_operator_override_non_pass_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, oo_data={"result": "FAIL"})
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert result["blocker"] == "operator_override result must be PASS"

    def test_operator_override_missing_result_field_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, oo_data={})
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False

    def test_missing_cg_no_broker_call(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(position={"qty": "1"})
        result = _run(tmp_path, cg_missing=True, broker=broker)
        assert result["result"] == "BLOCKED"
        assert len(broker.get_position_calls) == 0
        assert len(broker.get_open_orders_calls) == 0


# ---------------------------------------------------------------------------
# TestSymbolValidation
# ---------------------------------------------------------------------------

class TestSymbolValidation:
    def test_wrong_symbol_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, symbol="AAPL")
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False
        assert result["blocker"] == "symbol must be exactly 'SPY'"

    def test_lowercase_symbol_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, symbol="spy")
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False

    def test_whitespace_symbol_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, symbol=" SPY")
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False

    def test_empty_symbol_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path, symbol="")
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is False


# ---------------------------------------------------------------------------
# TestBrokerNone
# ---------------------------------------------------------------------------

class TestInputSecretRedaction:
    _SECRET = "highly-sensitive-input-value-8k2p"

    def test_cg_result_secret_not_in_output(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_data={"result": self._SECRET})
        output_str = json.dumps(result)
        assert self._SECRET not in output_str

    def test_cg_result_secret_not_in_violations(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_data={"result": self._SECRET})
        assert self._SECRET not in " ".join(result["violations"])
        assert self._SECRET not in (result["blocker"] or "")

    def test_cg_result_secret_not_in_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(tmp_path, cg_data={"result": self._SECRET})
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_oo_result_secret_not_in_output(self, tmp_path: Path) -> None:
        result = _run(tmp_path, oo_data={"result": self._SECRET})
        output_str = json.dumps(result)
        assert self._SECRET not in output_str

    def test_oo_result_secret_not_in_violations(self, tmp_path: Path) -> None:
        result = _run(tmp_path, oo_data={"result": self._SECRET})
        assert self._SECRET not in " ".join(result["violations"])
        assert self._SECRET not in (result["blocker"] or "")

    def test_oo_result_secret_not_in_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(tmp_path, oo_data={"result": self._SECRET})
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_symbol_secret_not_in_output(self, tmp_path: Path) -> None:
        result = _run(tmp_path, symbol=self._SECRET)
        output_str = json.dumps(result)
        assert self._SECRET not in output_str
        assert result["symbol"] == "(invalid)"

    def test_symbol_secret_not_in_violations(self, tmp_path: Path) -> None:
        result = _run(tmp_path, symbol=self._SECRET)
        assert self._SECRET not in " ".join(result["violations"])
        assert self._SECRET not in (result["blocker"] or "")

    def test_symbol_secret_not_in_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(tmp_path, symbol=self._SECRET)
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err


# ---------------------------------------------------------------------------
# TestBrokerNone
# ---------------------------------------------------------------------------

class TestBrokerNone:
    def test_broker_none_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["result"] == "BLOCKED"
        assert result["blocker"] == "real broker adapter not implemented"

    def test_broker_none_no_broker_calls(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["broker_calls_made"] is False

    def test_broker_none_broker_calls_readonly_false(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["broker_calls_readonly"] is False

    def test_broker_none_credentials_not_read(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["credentials_read"] is False

    def test_broker_none_position_and_order_null(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["position_observed"] is None
        assert result["open_order_observed"] is None

    def test_cli_broker_none_blocked(self, tmp_path: Path) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--output", str(out),
            ])
        assert exc_info.value.code == 1
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"
        assert data["blocker"] == "real broker adapter not implemented"


# ---------------------------------------------------------------------------
# TestHappyPath
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_no_position_no_order(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(position=None, open_orders=[])
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "PASS"
        assert result["position_observed"] is False
        assert result["open_order_observed"] is False
        assert result["broker_calls_made"] is True

    def test_position_exists_no_order(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            position={"qty_redacted": True},
            open_orders=[],
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "PASS"
        assert result["position_observed"] is True
        assert result["open_order_observed"] is False

    def test_no_position_open_order_exists(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            position=None,
            open_orders=[{"id_redacted": True}],
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "PASS"
        assert result["position_observed"] is False
        assert result["open_order_observed"] is True

    def test_position_and_order_both_exist(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            position={"qty_redacted": True},
            open_orders=[{"id_redacted": True}],
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "PASS"
        assert result["position_observed"] is True
        assert result["open_order_observed"] is True

    def test_multiple_open_orders(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            position=None,
            open_orders=[{"x": 1}, {"x": 2}],
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "PASS"
        assert result["open_order_observed"] is True

    def test_broker_called_with_spy_symbol(self, tmp_path: Path) -> None:
        broker = MockPositionBroker()
        _run(tmp_path, broker=broker)
        assert broker.get_position_calls == [_REQUIRED_SYMBOL]
        assert broker.get_open_orders_calls == [_REQUIRED_SYMBOL]

    def test_pass_has_no_violations(self, tmp_path: Path) -> None:
        broker = MockPositionBroker()
        result = _run(tmp_path, broker=broker)
        assert result["violations"] == []
        assert result["blocker"] is None

    def test_broker_calls_readonly_true_on_pass(self, tmp_path: Path) -> None:
        broker = MockPositionBroker()
        result = _run(tmp_path, broker=broker)
        assert result["broker_calls_made"] is True
        assert result["broker_calls_readonly"] is True


# ---------------------------------------------------------------------------
# TestBrokerException
# ---------------------------------------------------------------------------

class TestBrokerException:
    _SECRET = "super-secret-api-key-7x9z"

    def test_get_position_exception_blocked(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_position=RuntimeError(self._SECRET)
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is True

    def test_get_position_exception_secret_not_in_output(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_position=RuntimeError(self._SECRET)
        )
        result = _run(tmp_path, broker=broker)
        output_str = json.dumps(result)
        assert self._SECRET not in output_str

    def test_get_position_exception_secret_not_in_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        broker = MockPositionBroker(
            raise_on_get_position=RuntimeError(self._SECRET)
        )
        _run(tmp_path, broker=broker)
        captured = capsys.readouterr()
        assert self._SECRET not in captured.out
        assert self._SECRET not in captured.err

    def test_get_position_exception_message_redacted(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_position=ValueError(self._SECRET)
        )
        result = _run(tmp_path, broker=broker)
        assert any("redacted" in v for v in result["violations"])

    def test_get_open_orders_exception_blocked(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_open_orders=RuntimeError(self._SECRET)
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert result["broker_calls_made"] is True

    def test_get_open_orders_exception_secret_not_in_output(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_open_orders=RuntimeError(self._SECRET)
        )
        result = _run(tmp_path, broker=broker)
        output_str = json.dumps(result)
        assert self._SECRET not in output_str

    def test_get_open_orders_exception_position_still_set(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            position={"qty_redacted": True},
            raise_on_get_open_orders=RuntimeError(self._SECRET),
        )
        result = _run(tmp_path, broker=broker)
        assert result["result"] == "BLOCKED"
        assert result["position_observed"] is True
        assert result["open_order_observed"] is None


# ---------------------------------------------------------------------------
# TestOutputInvariants
# ---------------------------------------------------------------------------

class TestOutputInvariants:
    def _assert_invariants(self, result: dict[str, Any]) -> None:
        assert result["broker_calls_readonly"] == result["broker_calls_made"]
        assert result["broker_mutation_calls_made"] is False
        assert result["credential_values_exposed"] is False
        assert result["credentials_read"] is False
        assert result["live_submit_enabled"] is False
        assert result["submit_order_reachable"] is False
        assert result["cancel_order_reachable"] is False
        assert result["replace_order_reachable"] is False
        assert result["broker_ids_redacted"] is True
        assert result["account_identifiers_redacted"] is True
        assert result["raw_broker_response_included"] is False

    def test_invariants_on_pass(self, tmp_path: Path) -> None:
        broker = MockPositionBroker()
        self._assert_invariants(_run(tmp_path, broker=broker))

    def test_invariants_on_blocked_no_broker(self, tmp_path: Path) -> None:
        self._assert_invariants(_run(tmp_path))

    def test_invariants_on_blocked_artifact(self, tmp_path: Path) -> None:
        self._assert_invariants(_run(tmp_path, cg_missing=True))

    def test_invariants_on_blocked_symbol(self, tmp_path: Path) -> None:
        self._assert_invariants(_run(tmp_path, symbol="AAPL"))

    def test_invariants_on_blocked_exception(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_position=RuntimeError("err")
        )
        self._assert_invariants(_run(tmp_path, broker=broker))


# ---------------------------------------------------------------------------
# TestOutputAlwaysWritten
# ---------------------------------------------------------------------------

class TestOutputAlwaysWritten:
    def _run_via_main(
        self, tmp_path: Path, *, cg_data: Any = None, oo_data: Any = None,
        symbol: str = _REQUIRED_SYMBOL,
    ) -> dict[str, Any]:
        cg = _write_cg(tmp_path, cg_data)
        oo = _write_oo(tmp_path, oo_data)
        out = _output_path(tmp_path)
        try:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", symbol,
                "--output", str(out),
            ])
        except SystemExit:
            pass
        assert out.exists(), "output file must be written even on BLOCKED"
        return json.loads(out.read_text())

    def test_output_written_on_blocked_no_broker(self, tmp_path: Path) -> None:
        data = self._run_via_main(tmp_path)
        assert data["result"] == "BLOCKED"

    def test_output_written_on_blocked_bad_artifact(self, tmp_path: Path) -> None:
        data = self._run_via_main(tmp_path, cg_data={"result": "BLOCKED"})
        assert data["result"] == "BLOCKED"

    def test_output_written_on_blocked_bad_symbol(self, tmp_path: Path) -> None:
        data = self._run_via_main(tmp_path, symbol="TSLA")
        assert data["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestNeverRaises
# ---------------------------------------------------------------------------

class TestNeverRaises:
    def test_does_not_raise_on_broker_exception(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_position=RuntimeError("boom")
        )
        result = _run(tmp_path, broker=broker)
        assert isinstance(result, dict)

    def test_does_not_raise_on_open_orders_exception(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            raise_on_get_open_orders=RuntimeError("boom")
        )
        result = _run(tmp_path, broker=broker)
        assert isinstance(result, dict)

    def test_does_not_raise_on_missing_artifacts(self, tmp_path: Path) -> None:
        result = _run(tmp_path, cg_missing=True, oo_missing=True)
        assert isinstance(result, dict)

    def test_does_not_raise_on_broker_none(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestNoRawIds
# ---------------------------------------------------------------------------

class TestNoRawIds:
    def test_pass_output_has_no_raw_ids(self, tmp_path: Path) -> None:
        broker = MockPositionBroker(
            position={"account_id": "ACC-12345", "order_id": "ORD-99999"},
            open_orders=[{"id": "ORD-11111", "fill_price": "499.50"}],
        )
        result = _run(tmp_path, broker=broker)
        output_str = json.dumps(result)
        assert "ACC-12345" not in output_str
        assert "ORD-99999" not in output_str
        assert "ORD-11111" not in output_str
        assert "499.50" not in output_str

    def test_pass_stdout_has_no_raw_ids(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        broker = MockPositionBroker(
            position={"account_id": "ACC-12345"},
        )
        run_reconciliation(
            credential_guard_path=cg,
            operator_override_path=oo,
            symbol="SPY",
            broker=broker,
        )
        captured = capsys.readouterr()
        assert "ACC-12345" not in captured.out


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

class TestSourceScans:
    def test_no_module_level_alpaca_import(self) -> None:
        for line in _source_lines():
            assert not line.startswith("from alpaca"), (
                f"Module-level alpaca import found: {line!r}"
            )
            assert not line.startswith("import alpaca"), (
                f"Module-level alpaca import found: {line!r}"
            )

    def test_no_requests_import(self) -> None:
        for line in _source_lines():
            assert "import requests" not in line

    def test_no_httpx_import(self) -> None:
        for line in _source_lines():
            assert "import httpx" not in line

    def test_no_aiohttp_import(self) -> None:
        for line in _source_lines():
            assert "import aiohttp" not in line

    def test_no_urllib_request_import(self) -> None:
        for line in _source_lines():
            assert "import urllib.request" not in line
            assert "from urllib.request" not in line

    def test_no_submit_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "submit_order(" not in line, (
                f"submit_order( found: {line!r}"
            )

    def test_no_cancel_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "cancel_order(" not in line, (
                f"cancel_order( found: {line!r}"
            )

    def test_no_replace_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "replace_order(" not in line, (
                f"replace_order( found: {line!r}"
            )

    def test_no_post_patch_delete_endpoints(self) -> None:
        mutation_markers = (
            '"/v2/orders"',
            ".post(",
            ".patch(",
            ".delete(",
        )
        for line in _non_comment_lines():
            for marker in mutation_markers:
                assert marker not in line, (
                    f"Mutation marker {marker!r} found in: {line!r}"
                )

    def test_no_ledger_write_call(self) -> None:
        for line in _non_comment_lines():
            assert "append_live_ledger_row" not in line
            assert "live_submit_ledger" not in line

    def test_no_module_level_os_environ(self) -> None:
        for line in _source_lines():
            if line.startswith("os.environ"):
                raise AssertionError(
                    f"Module-level os.environ call found: {line!r}"
                )
