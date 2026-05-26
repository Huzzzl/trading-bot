"""
Tests for src/tools/live_position_reconciliation_readonly.py

Coverage:
- Prerequisite artifact gating (missing, malformed, non-PASS)
- Symbol validation (exact match — wrong value, wrong case, whitespace)
- CLI without flag → BLOCKED ("readonly broker api flag not set"), no credential read
- Mock happy paths (no position/no order; position only; order only; both)
- Broker exception → BLOCKED, secret text absent from all output
- Output always written (PASS and BLOCKED paths)
- run_reconciliation never raises
- Output invariants (mutation/credential/submit/cancel/replace fields always
  false; broker_ids_redacted, account_identifiers_redacted always true)
- No raw IDs in output or stdout
- Source scans: no Alpaca/network imports at module level, no os.environ at
  module level, no mutation method names, no POST/PATCH/DELETE strings, no
  ledger writes, no close_position/close_all_positions calls
- Real adapter: flag gate, credential gate, construction gate, happy path,
  no-position signal (404), exception redaction, paper=False enforced
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
    _POSITION_NOT_FOUND_SIGNALS,
    _is_position_not_found,
    _build_alpaca_live_position_broker,
    AlpacaLivePositionBroker,
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
    allow_live_readonly: bool = False,
    _trading_client_cls: Any = None,
    _get_orders_request_cls: Any = None,
    _query_order_status_cls: Any = None,
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
        allow_live_readonly=allow_live_readonly,
        _trading_client_cls=_trading_client_cls,
        _get_orders_request_cls=_get_orders_request_cls,
        _query_order_status_cls=_query_order_status_cls,
    )


# ---------------------------------------------------------------------------
# Mock broker (for existing mock-path tests)
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


# ---------------------------------------------------------------------------
# Mock helpers for real adapter tests
# ---------------------------------------------------------------------------

class _FakePosition:
    pass


class _FakeOrder:
    pass


class _FakeTradingClient:
    def __init__(self, *, api_key: str, secret_key: str, paper: bool) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self._has_position: bool = True
        self._orders: list[_FakeOrder] = []
        self._raise_on_get_position: Exception | None = None
        self._raise_on_get_orders: Exception | None = None

    def get_open_position(self, symbol: str) -> _FakePosition:
        if self._raise_on_get_position is not None:
            raise self._raise_on_get_position
        if not self._has_position:
            raise Exception("404 position does not exist")
        return _FakePosition()

    def get_orders(self, *, filter: Any = None) -> list[_FakeOrder]:
        if self._raise_on_get_orders is not None:
            raise self._raise_on_get_orders
        return self._orders


class _TrackingClientCls:
    """Records every _FakeTradingClient instance created so tests can inspect them."""

    def __init__(self) -> None:
        self.instances: list[_FakeTradingClient] = []

    def __call__(self, *, api_key: str, secret_key: str, paper: bool) -> _FakeTradingClient:
        inst = _FakeTradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
        self.instances.append(inst)
        return inst


class _FakeGetOrdersRequest:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeQueryOrderStatus:
    OPEN = "open"


def _make_tracking_cls() -> _TrackingClientCls:
    return _TrackingClientCls()


# ---------------------------------------------------------------------------
# Source line helpers
# ---------------------------------------------------------------------------

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
# TestInputSecretRedaction
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
# TestBrokerNone  (no flag — BLOCKED at flag gate, not credential gate)
# ---------------------------------------------------------------------------

class TestBrokerNone:
    def test_broker_none_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["result"] == "BLOCKED"
        assert result["blocker"] == "readonly broker api flag not set"

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
        assert data["blocker"] == "readonly broker api flag not set"


# ---------------------------------------------------------------------------
# TestHappyPath  (mock broker injected)
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
# TestOutputInvariants  (mock broker paths only)
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

    def test_output_written_on_blocked_no_flag(self, tmp_path: Path) -> None:
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
# TestRealAdapterFlagAbsent
# ---------------------------------------------------------------------------

class TestRealAdapterFlagAbsent:
    """Without --allow-live-broker-api-readonly (allow_live_readonly=False),
    CLI/API must be BLOCKED before reading credentials or constructing client."""

    def test_no_flag_blocked(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["result"] == "BLOCKED"
        assert result["blocker"] == "readonly broker api flag not set"

    def test_no_flag_credentials_not_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        result = _run(tmp_path)
        assert result["credentials_read"] is False

    def test_no_flag_no_trading_client_constructed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        tracker = _make_tracking_cls()
        result = _run(tmp_path, _trading_client_cls=tracker)
        assert len(tracker.instances) == 0
        assert result["credentials_read"] is False

    def test_no_flag_broker_calls_not_made(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["broker_calls_made"] is False
        assert result["broker_calls_readonly"] is False

    def test_no_flag_position_and_order_null(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        assert result["position_observed"] is None
        assert result["open_order_observed"] is None


# ---------------------------------------------------------------------------
# TestRealAdapterGatesFail
# ---------------------------------------------------------------------------

class TestRealAdapterGatesFail:
    """Even with allow_live_readonly=True, early gates must block before credentials."""

    def test_missing_cg_with_flag_credentials_not_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        result = _run(tmp_path, cg_missing=True, allow_live_readonly=True)
        assert result["result"] == "BLOCKED"
        assert result["credentials_read"] is False

    def test_non_pass_oo_with_flag_credentials_not_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        result = _run(tmp_path, oo_data={"result": "FAIL"}, allow_live_readonly=True)
        assert result["result"] == "BLOCKED"
        assert result["credentials_read"] is False

    def test_bad_symbol_with_flag_credentials_not_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        result = _run(tmp_path, symbol="AAPL", allow_live_readonly=True)
        assert result["result"] == "BLOCKED"
        assert result["credentials_read"] is False

    def test_bad_symbol_with_flag_no_trading_client_constructed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        tracker = _make_tracking_cls()
        _run(tmp_path, symbol="AAPL", allow_live_readonly=True, _trading_client_cls=tracker)
        assert len(tracker.instances) == 0


# ---------------------------------------------------------------------------
# TestRealAdapterCredentialsMissing
# ---------------------------------------------------------------------------

class TestRealAdapterCredentialsMissing:
    """Flag present and gates pass, but env vars absent → BLOCKED."""

    def test_both_credentials_missing_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        result = _run(tmp_path, allow_live_readonly=True)
        assert result["result"] == "BLOCKED"
        assert result["blocker"] == "credentials not found in environment"

    def test_both_credentials_missing_credentials_read_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        result = _run(tmp_path, allow_live_readonly=True)
        assert result["credentials_read"] is True

    def test_only_api_key_missing_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        result = _run(tmp_path, allow_live_readonly=True)
        assert result["result"] == "BLOCKED"
        assert result["credentials_read"] is True

    def test_only_secret_key_missing_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        result = _run(tmp_path, allow_live_readonly=True)
        assert result["result"] == "BLOCKED"
        assert result["credentials_read"] is True


# ---------------------------------------------------------------------------
# TestRealAdapterConstruction
# ---------------------------------------------------------------------------

class TestRealAdapterConstruction:
    """Real adapter builds TradingClient with paper=False and injects credentials."""

    def test_paper_false_enforced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        tracker = _make_tracking_cls()
        _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert len(tracker.instances) == 1
        assert tracker.instances[0].paper is False

    def test_api_key_passed_to_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "my-api-key-xyz")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "my-secret-xyz")
        tracker = _make_tracking_cls()
        _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert tracker.instances[0].api_key == "my-api-key-xyz"

    def test_credentials_read_true_after_construction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_make_tracking_cls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["credentials_read"] is True

    def test_broker_calls_made_true_after_construction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_make_tracking_cls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["broker_calls_made"] is True

    def test_get_orders_request_uses_query_order_status_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GetOrdersRequest must receive QueryOrderStatus.OPEN, not OrderStatus."""
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        captured_requests: list[_FakeGetOrdersRequest] = []

        class _CapturingGetOrdersRequest(_FakeGetOrdersRequest):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                captured_requests.append(self)

        _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_make_tracking_cls(),
            _get_orders_request_cls=_CapturingGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert len(captured_requests) == 1
        assert captured_requests[0].kwargs.get("status") == _FakeQueryOrderStatus.OPEN
        assert captured_requests[0].kwargs.get("status") == "open"


# ---------------------------------------------------------------------------
# TestRealAdapterHappyPath
# ---------------------------------------------------------------------------

class TestRealAdapterHappyPath:
    """Real adapter (with injected fake client) — PASS paths."""

    def _run_real(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        tracker: _TrackingClientCls,
        has_position: bool = True,
        orders: list[_FakeOrder] | None = None,
    ) -> dict[str, Any]:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        if tracker.instances:
            tracker.instances[0]._has_position = has_position
            tracker.instances[0]._orders = orders or []
        return result

    def test_pass_result_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        tracker = _make_tracking_cls()
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["result"] == "PASS"

    def test_position_observed_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        tracker = _make_tracking_cls()
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["position_observed"] is True

    def test_open_order_observed_true_when_orders_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        class _WithOrdersClient(_FakeTradingClient):
            def get_orders(self, *, filter: Any = None) -> list[_FakeOrder]:
                return [_FakeOrder()]

        class _WithOrdersCls:
            def __call__(
                self, *, api_key: str, secret_key: str, paper: bool
            ) -> _WithOrdersClient:
                return _WithOrdersClient(
                    api_key=api_key, secret_key=secret_key, paper=paper
                )

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_WithOrdersCls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["open_order_observed"] is True

    def test_open_order_observed_false_when_no_orders(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        tracker = _make_tracking_cls()
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["open_order_observed"] is False

    def test_credentials_read_true_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_make_tracking_cls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["credentials_read"] is True

    def test_mutation_fields_false_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_make_tracking_cls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["broker_mutation_calls_made"] is False
        assert result["submit_order_reachable"] is False
        assert result["cancel_order_reachable"] is False
        assert result["replace_order_reachable"] is False
        assert result["live_submit_enabled"] is False


# ---------------------------------------------------------------------------
# TestRealAdapterNoPositionSignal
# ---------------------------------------------------------------------------

class TestRealAdapterNoPositionSignal:
    """Alpaca 404-style exceptions → position_observed=False, result=PASS."""

    def _run_with_position_exc(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        exc_msg: str,
    ) -> dict[str, Any]:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        tracker = _make_tracking_cls()
        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        if tracker.instances:
            tracker.instances[0]._raise_on_get_position = Exception(exc_msg)
        return _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=tracker,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )

    def test_404_signal_gives_no_position(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        class _NoPositionClient(_FakeTradingClient):
            def get_open_position(self, symbol: str) -> _FakePosition:
                raise Exception("404 position does not exist")

        class _NoPositionCls:
            def __call__(self, *, api_key: str, secret_key: str, paper: bool) -> _NoPositionClient:
                return _NoPositionClient(api_key=api_key, secret_key=secret_key, paper=paper)

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_NoPositionCls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["position_observed"] is False
        assert result["result"] == "PASS"

    def test_no_position_signal_passes(self, tmp_path: Path) -> None:
        assert _is_position_not_found(Exception("404"))
        assert _is_position_not_found(Exception("position does not exist"))
        assert _is_position_not_found(Exception("no position found"))

    def test_unrelated_exception_not_a_no_position_signal(self, tmp_path: Path) -> None:
        assert not _is_position_not_found(Exception("network timeout"))
        assert not _is_position_not_found(Exception("500 internal server error"))
        assert not _is_position_not_found(Exception("unauthorized"))


# ---------------------------------------------------------------------------
# TestRealAdapterExceptionRedaction
# ---------------------------------------------------------------------------

class TestRealAdapterExceptionRedaction:
    """Broker/construction exceptions must not leak secret text to output or stdout."""

    _SECRET = "very-secret-broker-token-9z3x"

    def test_construction_exception_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        def _raising_cls(**kwargs: Any) -> None:
            raise RuntimeError(self._SECRET)

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_raising_cls,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["result"] == "BLOCKED"

    def test_construction_exception_secret_not_in_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        def _raising_cls(**kwargs: Any) -> None:
            raise RuntimeError(self._SECRET)

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_raising_cls,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert self._SECRET not in json.dumps(result)

    def test_construction_exception_message_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        def _raising_cls(**kwargs: Any) -> None:
            raise RuntimeError(self._SECRET)

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_raising_cls,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert any("redacted" in v for v in result["violations"])

    def test_get_position_exception_secret_not_in_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        class _RaisingClient(_FakeTradingClient):
            def get_open_position(self, symbol: str) -> _FakePosition:
                raise RuntimeError(self._SECRET)

        class _RaisingCls:
            _SECRET = "very-secret-broker-token-9z3x"
            def __call__(self, *, api_key: str, secret_key: str, paper: bool) -> "_RaisingClient":
                return _RaisingClient(api_key=api_key, secret_key=secret_key, paper=paper)

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_RaisingCls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert self._SECRET not in json.dumps(result)
        assert result["result"] == "BLOCKED"

    def test_get_orders_exception_secret_not_in_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")
        SECRET = self._SECRET

        class _RaisingClient(_FakeTradingClient):
            def get_orders(self, *, filter: Any = None) -> list:
                raise RuntimeError(SECRET)

        class _RaisingCls:
            def __call__(self, *, api_key: str, secret_key: str, paper: bool) -> "_RaisingClient":
                return _RaisingClient(api_key=api_key, secret_key=secret_key, paper=paper)

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_RaisingCls(),
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert self._SECRET not in json.dumps(result)
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestRealAdapterBrokerConstructionFails
# ---------------------------------------------------------------------------

class TestRealAdapterBrokerConstructionFails:
    """TradingClient constructor raises → BLOCKED, credentials_read=True."""

    def test_construction_raises_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        def _fail(**kwargs: Any) -> None:
            raise ConnectionError("network failure")

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_fail,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["result"] == "BLOCKED"

    def test_construction_raises_credentials_read_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        def _fail(**kwargs: Any) -> None:
            raise ConnectionError("network failure")

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_fail,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["credentials_read"] is True

    def test_construction_raises_broker_calls_not_made(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "test-secret")

        def _fail(**kwargs: Any) -> None:
            raise ConnectionError("network failure")

        result = _run(
            tmp_path,
            allow_live_readonly=True,
            _trading_client_cls=_fail,
            _get_orders_request_cls=_FakeGetOrdersRequest,
            _query_order_status_cls=_FakeQueryOrderStatus,
        )
        assert result["broker_calls_made"] is False


# ---------------------------------------------------------------------------
# TestCLIRealAdapterFlag
# ---------------------------------------------------------------------------

class TestCLIRealAdapterFlag:
    """CLI tests for --allow-live-broker-api-readonly flag."""

    def test_cli_without_flag_blocked_flag_message(self, tmp_path: Path) -> None:
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
        data = json.loads(out.read_text())
        assert data["blocker"] == "readonly broker api flag not set"
        assert data["credentials_read"] is False

    def test_cli_with_flag_no_credentials_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--output", str(out),
                "--allow-live-broker-api-readonly",
            ])
        assert exc_info.value.code == 1
        data = json.loads(out.read_text())
        assert data["blocker"] == "credentials not found in environment"
        assert data["credentials_read"] is True

    def test_cli_output_always_written_with_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        cg = _write_cg(tmp_path)
        oo = _write_oo(tmp_path)
        out = _output_path(tmp_path)
        try:
            main([
                "--credential-guard", str(cg),
                "--operator-override", str(oo),
                "--symbol", "SPY",
                "--output", str(out),
                "--allow-live-broker-api-readonly",
            ])
        except SystemExit:
            pass
        assert out.exists()


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

    def test_no_close_position_call(self) -> None:
        for line in _non_comment_lines():
            assert "close_position(" not in line, (
                f"close_position( found: {line!r}"
            )

    def test_no_close_all_positions_call(self) -> None:
        for line in _non_comment_lines():
            assert "close_all_positions(" not in line, (
                f"close_all_positions( found: {line!r}"
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
