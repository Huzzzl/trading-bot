"""Tests for src/tools/live_single_manual_submit.py"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.tools.live_single_manual_submit import (
    LEDGER_COLUMNS,
    run_submit,
)


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

class MockBroker:
    """Minimal injectable mock broker for submit tests."""

    def __init__(self, raise_exc: BaseException | None = None) -> None:
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []
        self.raise_exc = raise_exc

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        notional: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.calls.append(
            {
                "symbol":          symbol,
                "side":            side,
                "order_type":      order_type,
                "notional":        notional,
                "client_order_id": client_order_id,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"id": "mock-order-12345", "status": "accepted"}


class LedgerCaptureBroker:
    """Mock broker that snapshots ledger state during submit_order call."""

    def __init__(self, ledger_path: Path) -> None:
        self.ledger_path   = ledger_path
        self.rows_at_call: list[dict[str, str]] = []

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        notional: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        with self.ledger_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            self.rows_at_call = [dict(row) for row in reader]
        return {"id": "mock-order"}


# ---------------------------------------------------------------------------
# Injectable mock classes for AlpacaLiveSubmitBroker tests
# ---------------------------------------------------------------------------

class _FakeOrderSide:
    BUY = "FAKE_BUY"


class _FakeTimeInForce:
    DAY = "FAKE_DAY"


class _FakeOrderRequest:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeTradingClient:
    def __init__(self, *, api_key: str, secret_key: str, paper: bool) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.submit_calls: list[Any] = []
        self.raise_exc: BaseException | None = None

    def submit_order(self, *, order_data: Any) -> Any:
        self.submit_calls.append(order_data)
        if self.raise_exc is not None:
            raise self.raise_exc

        class _Resp:
            id = "fake-order-id"

        return _Resp()


class _TrackingClientCls:
    """Callable that acts as TradingClient class; stores created instances."""

    def __init__(self, raise_exc: BaseException | None = None) -> None:
        self.instances: list[_FakeTradingClient] = []
        self._raise_exc = raise_exc

    def __call__(self, *, api_key: str, secret_key: str, paper: bool) -> _FakeTradingClient:
        client = _FakeTradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
        client.raise_exc = self._raise_exc
        self.instances.append(client)
        return client


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _write_pass(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"result": "PASS"}), encoding="utf-8")


def _write_prereqs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    cg = tmp_path / "cg.json"
    oo = tmp_path / "oo.json"
    bp = tmp_path / "bp.json"
    sa = tmp_path / "sa.json"
    for p in (cg, oo, bp, sa):
        _write_pass(p)
    return cg, oo, bp, sa


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    cfg: dict[str, Any] = {
        "live_trading_enabled":    True,
        "live_submit_dry_run":     False,
        "live_kill_switch_enabled": False,
    }
    if overrides:
        cfg.update(overrides)
    p = tmp_path / "local_config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def _run(
    tmp_path: Path,
    *,
    broker: Any = None,
    symbol: str = "SPY",
    side: str = "buy",
    order_type: str = "market",
    notional_cap: Any = 100.0,
    cg_result: str | None = "PASS",
    oo_result: str | None = "PASS",
    bp_result: str | None = "PASS",
    sa_result: str | None = "PASS",
    config_overrides: dict | None = None,
    missing_config: bool = False,
    allow_real_live_submit: bool = False,
    _trading_client_cls: Any = None,
    _market_order_request_cls: Any = None,
    _order_side_cls: Any = None,
    _time_in_force_cls: Any = None,
) -> dict[str, Any]:
    """Build standard inputs, apply overrides, and call run_submit()."""
    cg, oo, bp, sa = _write_prereqs(tmp_path)

    if cg_result is None:
        cg.unlink()
    elif cg_result != "PASS":
        cg.write_text(json.dumps({"result": cg_result}), encoding="utf-8")

    if oo_result is None:
        oo.unlink()
    elif oo_result != "PASS":
        oo.write_text(json.dumps({"result": oo_result}), encoding="utf-8")

    if bp_result is None:
        bp.unlink()
    elif bp_result != "PASS":
        bp.write_text(json.dumps({"result": bp_result}), encoding="utf-8")

    if sa_result is None:
        sa.unlink()
    elif sa_result != "PASS":
        sa.write_text(json.dumps({"result": sa_result}), encoding="utf-8")

    if missing_config:
        cfg_path = tmp_path / "nonexistent_config.yaml"
    else:
        cfg_path = _write_config(tmp_path, overrides=config_overrides)

    ledger = tmp_path / "ledger.csv"
    return run_submit(
        credential_guard_path=cg,
        operator_override_path=oo,
        broker_preflight_path=bp,
        submit_approval_path=sa,
        local_operator_config_path=cfg_path,
        symbol=symbol,
        side=side,
        order_type=order_type,
        notional_cap=notional_cap,
        ledger_path=ledger,
        broker=broker,
        allow_real_live_submit=allow_real_live_submit,
        _trading_client_cls=_trading_client_cls,
        _market_order_request_cls=_market_order_request_cls,
        _order_side_cls=_order_side_cls,
        _time_in_force_cls=_time_in_force_cls,
    )


def _submitted(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    broker = kwargs.pop("broker", MockBroker())
    return _run(tmp_path, broker=broker, **kwargs)


# ---------------------------------------------------------------------------
# TestHappyPath
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_result_submitted(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["result"] == "SUBMITTED"

    def test_order_submitted_true(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["order_submitted"] is True

    def test_submit_order_called_true(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["submit_order_called"] is True

    def test_broker_mutation_true(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["broker_mutation_calls_made"] is True

    def test_live_submit_enabled_true(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["live_submit_enabled"] is True

    def test_config_safety_overridden_true(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["config_safety_overridden_by_local_operator_config"] is True

    def test_live_ledger_written_true(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["live_ledger_written"] is True

    def test_client_order_id_not_none(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["client_order_id"] is not None

    def test_client_order_id_format(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert isinstance(out["client_order_id"], str)
        assert out["client_order_id"].startswith("LIVE-SPY-BUY-")

    def test_submitted_at_utc_not_none(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["submitted_at_utc"] is not None
        assert isinstance(out["submitted_at_utc"], str)

    def test_broker_order_id_redacted(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["broker_order_id_redacted"] == "<redacted>"

    def test_violations_empty(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["violations"] == []

    def test_blocker_none(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["blocker"] is None

    def test_symbol_in_output(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["symbol"] == "SPY"

    def test_side_in_output(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["side"] == "buy"

    def test_order_type_in_output(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["order_type"] == "market"

    def test_notional_cap_in_output(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["notional_cap"] == 100.0

    def test_submit_called_with_correct_args(self, tmp_path: Path) -> None:
        broker = MockBroker()
        _run(tmp_path, broker=broker)
        assert broker.call_count == 1
        call = broker.calls[0]
        assert call["symbol"] == "SPY"
        assert call["side"] == "buy"
        assert call["order_type"] == "market"
        assert call["notional"] == 100.0

    def test_submit_called_with_client_order_id(self, tmp_path: Path) -> None:
        broker = MockBroker()
        out = _run(tmp_path, broker=broker)
        call = broker.calls[0]
        assert call["client_order_id"] == out["client_order_id"]
        assert isinstance(call["client_order_id"], str)
        assert call["client_order_id"].startswith("LIVE-SPY-BUY-")

    def test_all_required_fields_present(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        required = {
            "checked_at_utc", "result", "order_submitted", "broker_mutation_calls_made",
            "submit_order_called", "cancel_order_called", "replace_order_called",
            "live_submit_enabled", "automated_trading_enabled", "recurring_trading_enabled",
            "config_safety_overridden_by_local_operator_config", "symbol", "side",
            "order_type", "notional_cap", "client_order_id", "submitted_at_utc",
            "broker_order_id_redacted", "credential_values_exposed", "live_ledger_written",
            "violations", "blocker",
        }
        assert required.issubset(out.keys())


# ---------------------------------------------------------------------------
# TestBrokerNone
# ---------------------------------------------------------------------------

class TestBrokerNone:
    def test_result_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["result"] == "BLOCKED"

    def test_blocker_message(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["blocker"] == "real live submit adapter not implemented"

    def test_violations_contain_blocker(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert "real live submit adapter not implemented" in out["violations"]

    def test_order_submitted_false(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["order_submitted"] is False

    def test_submit_order_not_called(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["submit_order_called"] is False

    def test_broker_mutation_false(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["broker_mutation_calls_made"] is False

    def test_ledger_not_written(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["live_ledger_written"] is False

    def test_no_ledger_file_created(self, tmp_path: Path) -> None:
        _run(tmp_path, broker=None)
        assert not (tmp_path / "ledger.csv").exists()

    def test_live_submit_enabled_true(self, tmp_path: Path) -> None:
        # Config is valid, so live_submit_enabled=True even though broker=None
        out = _run(tmp_path, broker=None)
        assert out["live_submit_enabled"] is True

    def test_config_safety_overridden_true(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["config_safety_overridden_by_local_operator_config"] is True

    def test_client_order_id_none(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["client_order_id"] is None

    def test_submitted_at_utc_none(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["submitted_at_utc"] is None

    def test_broker_order_id_redacted_none(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["broker_order_id_redacted"] is None


# ---------------------------------------------------------------------------
# TestBrokerException
# ---------------------------------------------------------------------------

class TestBrokerException:
    def test_result_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        assert out["result"] == "BLOCKED"

    def test_blocker_message(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        assert out["blocker"] == "broker exception during submit (details redacted)"

    def test_order_submitted_false(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        assert out["order_submitted"] is False

    def test_submit_order_called_true(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        assert out["submit_order_called"] is True

    def test_broker_mutation_false(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        assert out["broker_mutation_calls_made"] is False

    def test_ledger_written_true(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        assert out["live_ledger_written"] is True

    def test_ledger_file_exists(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        _run(tmp_path, broker=broker)
        assert (tmp_path / "ledger.csv").exists()

    def test_ledger_row_status_exception(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("some error"))
        out = _run(tmp_path, broker=broker)
        ledger = tmp_path / "ledger.csv"
        with ledger.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert rows[0]["status"] == "exception"
        assert rows[0]["error"] == "details redacted"
        assert rows[0]["client_order_id"] == out["client_order_id"]

    def test_exception_text_not_in_blocker(self, tmp_path: Path) -> None:
        secret = "sk-SECRET-CREDENTIAL-12345"
        broker = MockBroker(raise_exc=RuntimeError(secret))
        out = _run(tmp_path, broker=broker)
        assert secret not in (out["blocker"] or "")

    def test_exception_text_not_in_violations(self, tmp_path: Path) -> None:
        secret = "sk-SECRET-CREDENTIAL-12345"
        broker = MockBroker(raise_exc=RuntimeError(secret))
        out = _run(tmp_path, broker=broker)
        assert all(secret not in v for v in out["violations"])

    def test_exception_text_not_in_output_json(self, tmp_path: Path) -> None:
        secret = "sk-SECRET-CREDENTIAL-12345"
        broker = MockBroker(raise_exc=RuntimeError(secret))
        out = _run(tmp_path, broker=broker)
        assert secret not in json.dumps(out)

    def test_submitted_at_utc_none(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        out = _run(tmp_path, broker=broker)
        assert out["submitted_at_utc"] is None

    def test_broker_order_id_redacted_none(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        out = _run(tmp_path, broker=broker)
        assert out["broker_order_id_redacted"] is None


# ---------------------------------------------------------------------------
# TestPrerequisiteArtifacts
# ---------------------------------------------------------------------------

_PREREQ_PARAMS = [
    ("cg_result", "credential_guard"),
    ("oo_result", "operator_override"),
    ("bp_result", "broker_preflight"),
    ("sa_result", "submit_approval"),
]


class TestPrerequisiteArtifactsMissing:
    @pytest.mark.parametrize("kwarg,label", _PREREQ_PARAMS)
    def test_missing_artifact_blocked(
        self, tmp_path: Path, kwarg: str, label: str
    ) -> None:
        out = _run(tmp_path, broker=MockBroker(), **{kwarg: None})
        assert out["result"] == "BLOCKED"
        assert any(label in v for v in out["violations"])

    @pytest.mark.parametrize("kwarg,label", _PREREQ_PARAMS)
    def test_missing_artifact_no_submit(
        self, tmp_path: Path, kwarg: str, label: str
    ) -> None:
        broker = MockBroker()
        _run(tmp_path, broker=broker, **{kwarg: None})
        assert broker.call_count == 0

    @pytest.mark.parametrize("kwarg,label", _PREREQ_PARAMS)
    def test_non_pass_artifact_blocked(
        self, tmp_path: Path, kwarg: str, label: str
    ) -> None:
        out = _run(tmp_path, broker=MockBroker(), **{kwarg: "BLOCKED"})
        assert out["result"] == "BLOCKED"
        assert any(label in v for v in out["violations"])

    @pytest.mark.parametrize("kwarg,label", _PREREQ_PARAMS)
    def test_non_pass_artifact_no_submit(
        self, tmp_path: Path, kwarg: str, label: str
    ) -> None:
        broker = MockBroker()
        _run(tmp_path, broker=broker, **{kwarg: "BLOCKED"})
        assert broker.call_count == 0

    @pytest.mark.parametrize("kwarg,label", _PREREQ_PARAMS)
    def test_malformed_json_blocked(
        self, tmp_path: Path, kwarg: str, label: str
    ) -> None:
        cg, oo, bp, sa = _write_prereqs(tmp_path)
        targets = {
            "cg_result": cg,
            "oo_result": oo,
            "bp_result": bp,
            "sa_result": sa,
        }
        targets[kwarg].write_text("not-json", encoding="utf-8")
        cfg = _write_config(tmp_path)
        out = run_submit(
            credential_guard_path=cg,
            operator_override_path=oo,
            broker_preflight_path=bp,
            submit_approval_path=sa,
            local_operator_config_path=cfg,
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=tmp_path / "ledger.csv",
            broker=MockBroker(),
        )
        assert out["result"] == "BLOCKED"
        assert any(label in v for v in out["violations"])

    def test_no_ledger_written_on_missing_prereq(self, tmp_path: Path) -> None:
        _run(tmp_path, broker=MockBroker(), cg_result=None)
        assert not (tmp_path / "ledger.csv").exists()


# ---------------------------------------------------------------------------
# TestParamValidation
# ---------------------------------------------------------------------------

class TestParamValidation:
    def test_wrong_symbol_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), symbol="AAPL")
        assert out["result"] == "BLOCKED"
        assert any("symbol" in v for v in out["violations"])

    def test_wrong_symbol_case_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), symbol="spy")
        assert out["result"] == "BLOCKED"

    def test_wrong_symbol_not_in_output(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), symbol="AAPL")
        assert out["symbol"] is None

    def test_wrong_symbol_not_echoed_in_violations(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), symbol="AAPL")
        assert all("AAPL" not in v for v in out["violations"])

    def test_wrong_side_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), side="sell")
        assert out["result"] == "BLOCKED"
        assert any("side" in v for v in out["violations"])

    def test_wrong_side_case_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), side="BUY")
        assert out["result"] == "BLOCKED"

    def test_wrong_side_not_in_output(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), side="sell")
        assert out["side"] is None

    def test_wrong_order_type_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), order_type="limit")
        assert out["result"] == "BLOCKED"
        assert any("order_type" in v for v in out["violations"])

    def test_wrong_order_type_case_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), order_type="Market")
        assert out["result"] == "BLOCKED"

    def test_wrong_order_type_not_in_output(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), order_type="limit")
        assert out["order_type"] is None

    def test_notional_zero_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=0.0)
        assert out["result"] == "BLOCKED"

    def test_notional_negative_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=-1.0)
        assert out["result"] == "BLOCKED"

    def test_notional_over_max_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=200.0)
        assert out["result"] == "BLOCKED"

    def test_notional_bool_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=True)
        assert out["result"] == "BLOCKED"
        assert any("boolean" in v for v in out["violations"])

    def test_notional_string_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap="100")
        assert out["result"] == "BLOCKED"

    def test_notional_invalid_out_none(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=-5.0)
        assert out["notional_cap"] is None

    def test_notional_boundary_100_passes(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=100.0)
        assert out["result"] == "SUBMITTED"

    def test_notional_boundary_just_over_max_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=100.01)
        assert out["result"] == "BLOCKED"

    def test_notional_small_positive_passes(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), notional_cap=0.01)
        assert out["result"] == "SUBMITTED"

    def test_no_submit_on_wrong_symbol(self, tmp_path: Path) -> None:
        broker = MockBroker()
        _run(tmp_path, broker=broker, symbol="AAPL")
        assert broker.call_count == 0


# ---------------------------------------------------------------------------
# TestLocalConfig
# ---------------------------------------------------------------------------

class TestLocalConfig:
    def test_missing_config_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), missing_config=True)
        assert out["result"] == "BLOCKED"
        assert any("local operator config" in v for v in out["violations"])

    def test_missing_config_live_submit_disabled(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), missing_config=True)
        assert out["live_submit_enabled"] is False
        assert out["config_safety_overridden_by_local_operator_config"] is False

    def test_trading_enabled_false_blocked(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path, broker=MockBroker(),
            config_overrides={"live_trading_enabled": False},
        )
        assert out["result"] == "BLOCKED"

    def test_trading_enabled_string_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_config.yaml"
        p.write_text(
            'live_trading_enabled: "true"\n'
            "live_submit_dry_run: false\n"
            "live_kill_switch_enabled: false\n",
            encoding="utf-8",
        )
        cfg = _write_prereqs(tmp_path)
        cg, oo, bp, sa = cfg
        out = run_submit(
            credential_guard_path=cg,
            operator_override_path=oo,
            broker_preflight_path=bp,
            submit_approval_path=sa,
            local_operator_config_path=p,
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=tmp_path / "ledger.csv",
            broker=MockBroker(),
        )
        assert out["result"] == "BLOCKED"

    def test_dry_run_true_blocked(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path, broker=MockBroker(),
            config_overrides={"live_submit_dry_run": True},
        )
        assert out["result"] == "BLOCKED"

    def test_dry_run_string_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_config2.yaml"
        p.write_text(
            "live_trading_enabled: true\n"
            'live_submit_dry_run: "false"\n'
            "live_kill_switch_enabled: false\n",
            encoding="utf-8",
        )
        cg, oo, bp, sa = _write_prereqs(tmp_path)
        out = run_submit(
            credential_guard_path=cg,
            operator_override_path=oo,
            broker_preflight_path=bp,
            submit_approval_path=sa,
            local_operator_config_path=p,
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=tmp_path / "ledger.csv",
            broker=MockBroker(),
        )
        assert out["result"] == "BLOCKED"

    def test_kill_switch_true_blocked(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path, broker=MockBroker(),
            config_overrides={"live_kill_switch_enabled": True},
        )
        assert out["result"] == "BLOCKED"

    def test_kill_switch_string_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_config3.yaml"
        p.write_text(
            "live_trading_enabled: true\n"
            "live_submit_dry_run: false\n"
            'live_kill_switch_enabled: "false"\n',
            encoding="utf-8",
        )
        cg, oo, bp, sa = _write_prereqs(tmp_path)
        out = run_submit(
            credential_guard_path=cg,
            operator_override_path=oo,
            broker_preflight_path=bp,
            submit_approval_path=sa,
            local_operator_config_path=p,
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=tmp_path / "ledger.csv",
            broker=MockBroker(),
        )
        assert out["result"] == "BLOCKED"

    def test_all_flags_wrong_three_violations(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path, broker=MockBroker(),
            config_overrides={
                "live_trading_enabled":    False,
                "live_submit_dry_run":     True,
                "live_kill_switch_enabled": True,
            },
        )
        config_violations = [v for v in out["violations"] if "local operator config" in v]
        assert len(config_violations) == 3

    def test_malformed_yaml_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "malformed.yaml"
        p.write_text("{not: valid: yaml:", encoding="utf-8")
        cg, oo, bp, sa = _write_prereqs(tmp_path)
        out = run_submit(
            credential_guard_path=cg,
            operator_override_path=oo,
            broker_preflight_path=bp,
            submit_approval_path=sa,
            local_operator_config_path=p,
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=tmp_path / "ledger.csv",
            broker=MockBroker(),
        )
        assert out["result"] == "BLOCKED"

    def test_config_safety_overridden_false_on_wrong_config(self, tmp_path: Path) -> None:
        out = _run(
            tmp_path, broker=MockBroker(),
            config_overrides={"live_trading_enabled": False},
        )
        assert out["config_safety_overridden_by_local_operator_config"] is False
        assert out["live_submit_enabled"] is False


# ---------------------------------------------------------------------------
# TestAlwaysFalseInvariants
# ---------------------------------------------------------------------------

_ALWAYS_FALSE_FIELDS = [
    "cancel_order_called",
    "replace_order_called",
    "automated_trading_enabled",
    "recurring_trading_enabled",
    "credential_values_exposed",
]


@pytest.mark.parametrize("field", _ALWAYS_FALSE_FIELDS)
class TestAlwaysFalseInvariants:
    def test_pre_gate_blocked(self, tmp_path: Path, field: str) -> None:
        out = _run(tmp_path, broker=MockBroker(), cg_result=None)
        assert out[field] is False

    def test_broker_none_blocked(self, tmp_path: Path, field: str) -> None:
        out = _run(tmp_path, broker=None)
        assert out[field] is False

    def test_submitted(self, tmp_path: Path, field: str) -> None:
        out = _submitted(tmp_path)
        assert out[field] is False

    def test_broker_exception(self, tmp_path: Path, field: str) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        out = _run(tmp_path, broker=broker)
        assert out[field] is False


# ---------------------------------------------------------------------------
# TestStateTable
# ---------------------------------------------------------------------------

class TestStateTable:
    def test_pre_gate_blocked_state(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), symbol="AAPL")
        assert out["order_submitted"]            is False
        assert out["submit_order_called"]        is False
        assert out["broker_mutation_calls_made"] is False

    def test_broker_none_state(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["order_submitted"]            is False
        assert out["submit_order_called"]        is False
        assert out["broker_mutation_calls_made"] is False

    def test_submitted_state(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["order_submitted"]            is True
        assert out["submit_order_called"]        is True
        assert out["broker_mutation_calls_made"] is True

    def test_exception_state(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        out = _run(tmp_path, broker=broker)
        assert out["order_submitted"]            is False
        assert out["submit_order_called"]        is True
        assert out["broker_mutation_calls_made"] is False


# ---------------------------------------------------------------------------
# TestLedger
# ---------------------------------------------------------------------------

class TestLedger:
    def test_ledger_written_on_submitted(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["live_ledger_written"] is True
        assert (tmp_path / "ledger.csv").exists()

    def test_ledger_row_status_submitted(self, tmp_path: Path) -> None:
        _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert rows[0]["status"] == "submitted"

    def test_ledger_row_symbol(self, tmp_path: Path) -> None:
        _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["symbol"] == "SPY"

    def test_ledger_row_side(self, tmp_path: Path) -> None:
        _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["side"] == "buy"

    def test_ledger_row_order_type(self, tmp_path: Path) -> None:
        _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["order_type"] == "market"

    def test_ledger_row_broker_order_id_redacted(self, tmp_path: Path) -> None:
        _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["broker_order_id"] == "<redacted>"

    def test_ledger_client_order_id_matches_output(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["client_order_id"] == out["client_order_id"]

    def test_ledger_schema_columns(self, tmp_path: Path) -> None:
        _submitted(tmp_path)
        with (tmp_path / "ledger.csv").open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            assert list(reader.fieldnames or []) == LEDGER_COLUMNS

    def test_ledger_not_written_on_pre_gate_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), cg_result=None)
        assert out["live_ledger_written"] is False
        assert not (tmp_path / "ledger.csv").exists()

    def test_ledger_pre_submit_row_exists_during_submit(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.csv"
        broker = LedgerCaptureBroker(ledger_path=ledger)
        cg, oo, bp, sa = _write_prereqs(tmp_path)
        cfg = _write_config(tmp_path)
        run_submit(
            credential_guard_path=cg,
            operator_override_path=oo,
            broker_preflight_path=bp,
            submit_approval_path=sa,
            local_operator_config_path=cfg,
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=ledger,
            broker=broker,
        )
        assert len(broker.rows_at_call) == 1
        assert broker.rows_at_call[0]["status"] == "attempting"


# ---------------------------------------------------------------------------
# TestClientOrderId
# ---------------------------------------------------------------------------

class TestClientOrderId:
    def test_format_live_spy_buy_prefix(self, tmp_path: Path) -> None:
        out = _submitted(tmp_path)
        assert out["client_order_id"].startswith("LIVE-SPY-BUY-")

    def test_unique_across_runs(self, tmp_path: Path) -> None:
        p1 = tmp_path / "run1"
        p2 = tmp_path / "run2"
        p1.mkdir()
        p2.mkdir()
        out1 = _submitted(p1)
        out2 = _submitted(p2)
        assert out1["client_order_id"] != out2["client_order_id"]

    def test_none_on_pre_gate_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), cg_result=None)
        assert out["client_order_id"] is None

    def test_none_on_broker_none(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert out["client_order_id"] is None

    def test_not_none_on_exception(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        out = _run(tmp_path, broker=broker)
        assert out["client_order_id"] is not None


# ---------------------------------------------------------------------------
# TestSubmitOnce
# ---------------------------------------------------------------------------

class TestSubmitOnce:
    def test_called_exactly_once_on_submitted(self, tmp_path: Path) -> None:
        broker = MockBroker()
        _run(tmp_path, broker=broker)
        assert broker.call_count == 1

    def test_called_exactly_once_on_exception(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        _run(tmp_path, broker=broker)
        assert broker.call_count == 1

    def test_not_called_on_pre_gate_blocked(self, tmp_path: Path) -> None:
        broker = MockBroker()
        _run(tmp_path, broker=broker, symbol="AAPL")
        assert broker.call_count == 0

    def test_not_called_on_broker_none(self, tmp_path: Path) -> None:
        # broker=None: no submit_order callable
        out = _run(tmp_path, broker=None)
        assert out["submit_order_called"] is False


# ---------------------------------------------------------------------------
# TestCLI
# ---------------------------------------------------------------------------

class TestCLI:
    def _invoke_main(self, tmp_path: Path, extra_args: list[str] | None = None) -> int:
        from src.tools.live_single_manual_submit import main

        cg, oo, bp, sa = _write_prereqs(tmp_path)
        cfg = _write_config(tmp_path)
        out = tmp_path / "output.json"

        argv = [
            "--credential-guard", str(cg),
            "--operator-override", str(oo),
            "--broker-preflight", str(bp),
            "--submit-approval", str(sa),
            "--local-operator-config", str(cfg),
            "--symbol", "SPY",
            "--side", "buy",
            "--order-type", "market",
            "--notional-cap", "100.0",
            "--ledger", str(tmp_path / "ledger.csv"),
            "--output", str(out),
        ]
        if extra_args:
            argv.extend(extra_args)

        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        return exc_info.value.code  # type: ignore[return-value]

    def test_cli_exits_nonzero(self, tmp_path: Path) -> None:
        code = self._invoke_main(tmp_path)
        assert code != 0

    def test_cli_writes_output_file(self, tmp_path: Path) -> None:
        self._invoke_main(tmp_path)
        out = tmp_path / "output.json"
        assert out.exists()

    def test_cli_output_result_blocked(self, tmp_path: Path) -> None:
        self._invoke_main(tmp_path)
        out = tmp_path / "output.json"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["result"] == "BLOCKED"

    def test_cli_output_blocker_message(self, tmp_path: Path) -> None:
        self._invoke_main(tmp_path)
        out = tmp_path / "output.json"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["blocker"] == "real live submit adapter not implemented"

    def test_cli_no_ledger_file(self, tmp_path: Path) -> None:
        self._invoke_main(tmp_path)
        assert not (tmp_path / "ledger.csv").exists()


# ---------------------------------------------------------------------------
# TestCLIRealFlag
# ---------------------------------------------------------------------------

class TestCLIRealFlag:
    """CLI tests for --allow-real-live-submit-once flag."""

    def _invoke_with_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[int, dict[str, Any]]:
        from src.tools.live_single_manual_submit import main

        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)

        cg, oo, bp, sa = _write_prereqs(tmp_path)
        cfg = _write_config(tmp_path)
        out_path = tmp_path / "output.json"

        argv = [
            "--credential-guard", str(cg),
            "--operator-override", str(oo),
            "--broker-preflight", str(bp),
            "--submit-approval", str(sa),
            "--local-operator-config", str(cfg),
            "--symbol", "SPY",
            "--side", "buy",
            "--order-type", "market",
            "--notional-cap", "100.0",
            "--ledger", str(tmp_path / "ledger.csv"),
            "--output", str(out_path),
            "--allow-real-live-submit-once",
        ]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return exc_info.value.code, data  # type: ignore[return-value]

    def test_blocked_without_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        code, data = self._invoke_with_flag(tmp_path, monkeypatch)
        assert code != 0
        assert data["result"] == "BLOCKED"

    def test_blocker_credentials_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, data = self._invoke_with_flag(tmp_path, monkeypatch)
        assert data["blocker"] is not None
        assert "credentials" in data["blocker"]

    def test_without_flag_blocker_not_implemented(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.live_single_manual_submit import main

        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")

        cg, oo, bp, sa = _write_prereqs(tmp_path)
        cfg = _write_config(tmp_path)
        out_path = tmp_path / "output.json"

        argv = [
            "--credential-guard", str(cg),
            "--operator-override", str(oo),
            "--broker-preflight", str(bp),
            "--submit-approval", str(sa),
            "--local-operator-config", str(cfg),
            "--symbol", "SPY", "--side", "buy", "--order-type", "market",
            "--notional-cap", "100.0",
            "--ledger", str(tmp_path / "ledger.csv"),
            "--output", str(out_path),
        ]
        with pytest.raises(SystemExit):
            main(argv)
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["blocker"] == "real live submit adapter not implemented"


# ---------------------------------------------------------------------------
# TestNoCredentialLeakage
# ---------------------------------------------------------------------------

class TestNoCredentialLeakage:
    def test_exception_secret_not_in_output_json(self, tmp_path: Path) -> None:
        secret = "sk-live-secret-SHOULD-NOT-APPEAR-12345abcdef"
        broker = MockBroker(raise_exc=RuntimeError(f"connection failed: {secret}"))
        out = _run(tmp_path, broker=broker)
        assert secret not in json.dumps(out)

    def test_exception_secret_not_in_violations(self, tmp_path: Path) -> None:
        secret = "sk-live-secret-SHOULD-NOT-APPEAR-12345abcdef"
        broker = MockBroker(raise_exc=ValueError(f"auth error: api_key={secret}"))
        out = _run(tmp_path, broker=broker)
        for v in out["violations"]:
            assert secret not in v

    def test_exception_secret_not_in_blocker(self, tmp_path: Path) -> None:
        secret = "sk-live-secret-SHOULD-NOT-APPEAR-12345abcdef"
        broker = MockBroker(raise_exc=Exception(f"forbidden: {secret}"))
        out = _run(tmp_path, broker=broker)
        assert secret not in (out["blocker"] or "")

    def test_credential_values_exposed_always_false(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("err"))
        out = _run(tmp_path, broker=broker)
        assert out["credential_values_exposed"] is False


# ---------------------------------------------------------------------------
# TestRawValueRedaction
# ---------------------------------------------------------------------------

class TestRawValueRedaction:
    def test_bad_symbol_not_echoed_in_violations(self, tmp_path: Path) -> None:
        bad = "INJECT_SYMBOL_VALUE"
        out = _run(tmp_path, broker=MockBroker(), symbol=bad)
        for v in out["violations"]:
            assert bad not in v

    def test_bad_symbol_not_in_output_json(self, tmp_path: Path) -> None:
        bad = "INJECT_SYMBOL_VALUE"
        out = _run(tmp_path, broker=MockBroker(), symbol=bad)
        assert bad not in json.dumps(out)

    def test_bad_side_not_echoed_in_violations(self, tmp_path: Path) -> None:
        bad = "INJECT_SIDE_VALUE"
        out = _run(tmp_path, broker=MockBroker(), side=bad)
        for v in out["violations"]:
            assert bad not in v

    def test_bad_order_type_not_echoed_in_violations(self, tmp_path: Path) -> None:
        bad = "INJECT_TYPE_VALUE"
        out = _run(tmp_path, broker=MockBroker(), order_type=bad)
        for v in out["violations"]:
            assert bad not in v

    def test_bad_symbol_null_in_output_symbol_field(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), symbol="AAPL")
        assert out["symbol"] is None

    def test_bad_side_null_in_output_side_field(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), side="sell")
        assert out["side"] is None

    def test_bad_order_type_null_in_output(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=MockBroker(), order_type="limit")
        assert out["order_type"] is None


# ---------------------------------------------------------------------------
# TestRunSubmitNeverRaises
# ---------------------------------------------------------------------------

class TestRunSubmitNeverRaises:
    def test_no_raise_on_all_missing_files(self, tmp_path: Path) -> None:
        out = run_submit(
            credential_guard_path=tmp_path / "no.json",
            operator_override_path=tmp_path / "no.json",
            broker_preflight_path=tmp_path / "no.json",
            submit_approval_path=tmp_path / "no.json",
            local_operator_config_path=tmp_path / "no.yaml",
            symbol="SPY", side="buy", order_type="market",
            notional_cap=100.0,
            ledger_path=tmp_path / "ledger.csv",
            broker=None,
        )
        assert out["result"] == "BLOCKED"

    def test_no_raise_on_broker_exception(self, tmp_path: Path) -> None:
        broker = MockBroker(raise_exc=RuntimeError("crash"))
        out = _run(tmp_path, broker=broker)
        assert out["result"] == "BLOCKED"

    def test_no_raise_on_none_broker(self, tmp_path: Path) -> None:
        out = _run(tmp_path, broker=None)
        assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# TestOutputStructure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    _REQUIRED_FIELDS = [
        "checked_at_utc", "result", "order_submitted", "broker_mutation_calls_made",
        "submit_order_called", "cancel_order_called", "replace_order_called",
        "live_submit_enabled", "automated_trading_enabled", "recurring_trading_enabled",
        "config_safety_overridden_by_local_operator_config", "symbol", "side",
        "order_type", "notional_cap", "client_order_id", "submitted_at_utc",
        "broker_order_id_redacted", "credential_values_exposed", "live_ledger_written",
        "violations", "blocker",
    ]

    def _all_scenarios(self, tmp_path: Path) -> list[dict]:
        results = []
        # SUBMITTED
        p1 = tmp_path / "s1"
        p1.mkdir()
        results.append(_submitted(p1))
        # BLOCKED (broker=None)
        p2 = tmp_path / "s2"
        p2.mkdir()
        results.append(_run(p2, broker=None))
        # BLOCKED (pre-gate)
        p3 = tmp_path / "s3"
        p3.mkdir()
        results.append(_run(p3, broker=MockBroker(), cg_result=None))
        # BLOCKED (exception)
        p4 = tmp_path / "s4"
        p4.mkdir()
        results.append(_run(p4, broker=MockBroker(raise_exc=RuntimeError("err"))))
        return results

    @pytest.mark.parametrize("field", _REQUIRED_FIELDS)
    def test_field_present_in_all_scenarios(
        self, tmp_path: Path, field: str
    ) -> None:
        for out in self._all_scenarios(tmp_path):
            assert field in out, f"Field '{field}' missing from output"

    def test_violations_is_list(self, tmp_path: Path) -> None:
        for out in self._all_scenarios(tmp_path):
            assert isinstance(out["violations"], list)

    def test_checked_at_utc_is_string(self, tmp_path: Path) -> None:
        for out in self._all_scenarios(tmp_path):
            assert isinstance(out["checked_at_utc"], str)


# ---------------------------------------------------------------------------
# TestRealAdapterFlagAbsent
# ---------------------------------------------------------------------------

class TestRealAdapterFlagAbsent:
    """Without --allow-real-live-submit-once, broker=None is always BLOCKED."""

    def test_result_blocked(self, tmp_path: Path) -> None:
        out = _run(tmp_path, allow_real_live_submit=False)
        assert out["result"] == "BLOCKED"

    def test_blocker_not_implemented(self, tmp_path: Path) -> None:
        out = _run(tmp_path, allow_real_live_submit=False)
        assert out["blocker"] == "real live submit adapter not implemented"

    def test_no_trading_client_constructed(self, tmp_path: Path) -> None:
        factory = _TrackingClientCls()
        _run(tmp_path, allow_real_live_submit=False, _trading_client_cls=factory)
        assert len(factory.instances) == 0

    def test_no_ledger(self, tmp_path: Path) -> None:
        _run(tmp_path, allow_real_live_submit=False)
        assert not (tmp_path / "ledger.csv").exists()

    def test_submit_order_not_called(self, tmp_path: Path) -> None:
        out = _run(tmp_path, allow_real_live_submit=False)
        assert out["submit_order_called"] is False

    def test_order_submitted_false(self, tmp_path: Path) -> None:
        out = _run(tmp_path, allow_real_live_submit=False)
        assert out["order_submitted"] is False

    def test_broker_mutation_false(self, tmp_path: Path) -> None:
        out = _run(tmp_path, allow_real_live_submit=False)
        assert out["broker_mutation_calls_made"] is False


# ---------------------------------------------------------------------------
# TestRealAdapterMissingCredentials
# ---------------------------------------------------------------------------

class TestRealAdapterMissingCredentials:
    """With flag but missing env credentials → BLOCKED, no TradingClient."""

    def test_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        out = _run(tmp_path, allow_real_live_submit=True)
        assert out["result"] == "BLOCKED"

    def test_blocker_contains_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        out = _run(tmp_path, allow_real_live_submit=True)
        assert out["blocker"] is not None
        assert "credentials" in out["blocker"]

    def test_no_trading_client_constructed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        factory = _TrackingClientCls()
        _run(tmp_path, allow_real_live_submit=True, _trading_client_cls=factory)
        assert len(factory.instances) == 0

    def test_submit_order_not_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        out = _run(tmp_path, allow_real_live_submit=True)
        assert out["submit_order_called"] is False

    def test_no_ledger_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        out = _run(tmp_path, allow_real_live_submit=True)
        assert out["live_ledger_written"] is False

    def test_missing_only_api_key_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_LIVE_API_KEY", raising=False)
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "has-secret-only")
        out = _run(tmp_path, allow_real_live_submit=True)
        assert out["result"] == "BLOCKED"

    def test_missing_only_secret_key_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "has-api-only")
        monkeypatch.delenv("ALPACA_LIVE_SECRET_KEY", raising=False)
        out = _run(tmp_path, allow_real_live_submit=True)
        assert out["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestRealAdapterGatesFail
# ---------------------------------------------------------------------------

class TestRealAdapterGatesFail:
    """With flag but pre-gates fail → BLOCKED before TradingClient construction."""

    def test_blocked_on_bad_symbol(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        factory = _TrackingClientCls()
        out = _run(
            tmp_path,
            allow_real_live_submit=True,
            symbol="AAPL",
            _trading_client_cls=factory,
        )
        assert out["result"] == "BLOCKED"
        assert len(factory.instances) == 0

    def test_blocked_on_missing_prereq(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        factory = _TrackingClientCls()
        out = _run(
            tmp_path,
            allow_real_live_submit=True,
            cg_result=None,
            _trading_client_cls=factory,
        )
        assert out["result"] == "BLOCKED"
        assert len(factory.instances) == 0

    def test_blocked_on_config_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        factory = _TrackingClientCls()
        out = _run(
            tmp_path,
            allow_real_live_submit=True,
            config_overrides={"live_trading_enabled": False},
            _trading_client_cls=factory,
        )
        assert out["result"] == "BLOCKED"
        assert len(factory.instances) == 0

    def test_no_submit_on_bad_notional(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret")
        factory = _TrackingClientCls()
        out = _run(
            tmp_path,
            allow_real_live_submit=True,
            notional_cap=200.0,
            _trading_client_cls=factory,
        )
        assert out["result"] == "BLOCKED"
        assert len(factory.instances) == 0


# ---------------------------------------------------------------------------
# TestRealAdapterHappyPath
# ---------------------------------------------------------------------------

class TestRealAdapterHappyPath:
    """With flag, all gates pass, mocked TradingClient → SUBMITTED."""

    def _run_with_mocks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        factory: _TrackingClientCls | None = None,
    ) -> tuple[dict[str, Any], _TrackingClientCls]:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-api-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret-key")
        if factory is None:
            factory = _TrackingClientCls()
        out = _run(
            tmp_path,
            allow_real_live_submit=True,
            _trading_client_cls=factory,
            _market_order_request_cls=_FakeOrderRequest,
            _order_side_cls=_FakeOrderSide,
            _time_in_force_cls=_FakeTimeInForce,
        )
        return out, factory

    def test_result_submitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["result"] == "SUBMITTED"

    def test_trading_client_paper_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, factory = self._run_with_mocks(tmp_path, monkeypatch)
        assert len(factory.instances) == 1
        assert factory.instances[0].paper is False

    def test_trading_client_constructed_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, factory = self._run_with_mocks(tmp_path, monkeypatch)
        assert len(factory.instances) == 1

    def test_submit_order_called_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, factory = self._run_with_mocks(tmp_path, monkeypatch)
        assert len(factory.instances[0].submit_calls) == 1

    def test_order_submitted_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["order_submitted"] is True

    def test_broker_mutation_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["broker_mutation_calls_made"] is True

    def test_broker_order_id_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["broker_order_id_redacted"] == "<redacted>"

    def test_ledger_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["live_ledger_written"] is True

    def test_no_violations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["violations"] == []

    def test_client_order_id_not_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["client_order_id"] is not None
        assert out["client_order_id"].startswith("LIVE-SPY-BUY-")

    def test_submitted_at_utc_not_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, _ = self._run_with_mocks(tmp_path, monkeypatch)
        assert out["submitted_at_utc"] is not None


# ---------------------------------------------------------------------------
# TestRealAdapterOrderRequest
# ---------------------------------------------------------------------------

class TestRealAdapterOrderRequest:
    """Verify the order request is constructed with correct parameters."""

    def _run_with_request_capture(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        notional_cap: float = 100.0,
    ) -> tuple[dict[str, Any], list[_FakeOrderRequest]]:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-api-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret-key")

        captured: list[_FakeOrderRequest] = []

        class _CapturingClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            def submit_order(self, *, order_data: Any) -> Any:
                captured.append(order_data)

                class _Resp:
                    id = "mock-id"

                return _Resp()

        out = _run(
            tmp_path,
            allow_real_live_submit=True,
            notional_cap=notional_cap,
            _trading_client_cls=_CapturingClient,
            _market_order_request_cls=_FakeOrderRequest,
            _order_side_cls=_FakeOrderSide,
            _time_in_force_cls=_FakeTimeInForce,
        )
        return out, captured

    def test_symbol_is_spy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, reqs = self._run_with_request_capture(tmp_path, monkeypatch)
        assert len(reqs) == 1
        assert reqs[0].kwargs["symbol"] == "SPY"

    def test_notional_is_correct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, reqs = self._run_with_request_capture(tmp_path, monkeypatch, notional_cap=75.0)
        assert reqs[0].kwargs["notional"] == 75.0

    def test_side_is_buy_enum(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, reqs = self._run_with_request_capture(tmp_path, monkeypatch)
        assert reqs[0].kwargs["side"] == _FakeOrderSide.BUY

    def test_time_in_force_is_day_enum(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, reqs = self._run_with_request_capture(tmp_path, monkeypatch)
        assert reqs[0].kwargs["time_in_force"] == _FakeTimeInForce.DAY

    def test_client_order_id_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, reqs = self._run_with_request_capture(tmp_path, monkeypatch)
        assert "client_order_id" in reqs[0].kwargs
        assert reqs[0].kwargs["client_order_id"] == out["client_order_id"]

    def test_client_order_id_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, reqs = self._run_with_request_capture(tmp_path, monkeypatch)
        assert reqs[0].kwargs["client_order_id"].startswith("LIVE-SPY-BUY-")

    def test_exactly_one_request_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, reqs = self._run_with_request_capture(tmp_path, monkeypatch)
        assert len(reqs) == 1


# ---------------------------------------------------------------------------
# TestRealAdapterExceptionRedaction
# ---------------------------------------------------------------------------

class TestRealAdapterExceptionRedaction:
    """Exception from real broker: secret text must not appear in any output."""

    def _run_with_exc(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        secret: str,
    ) -> dict[str, Any]:
        monkeypatch.setenv("ALPACA_LIVE_API_KEY", "fake-api-key")
        monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "fake-secret-key")
        factory = _TrackingClientCls(raise_exc=RuntimeError(secret))
        return _run(
            tmp_path,
            allow_real_live_submit=True,
            _trading_client_cls=factory,
            _market_order_request_cls=_FakeOrderRequest,
            _order_side_cls=_FakeOrderSide,
            _time_in_force_cls=_FakeTimeInForce,
        )

    def test_blocked_on_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run_with_exc(tmp_path, monkeypatch, "some-error")
        assert out["result"] == "BLOCKED"

    def test_secret_not_in_blocker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "sk-REAL-SECRET-SHOULD-NOT-APPEAR"
        out = self._run_with_exc(tmp_path, monkeypatch, secret)
        assert secret not in (out["blocker"] or "")

    def test_secret_not_in_violations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "sk-REAL-SECRET-SHOULD-NOT-APPEAR"
        out = self._run_with_exc(tmp_path, monkeypatch, secret)
        for v in out["violations"]:
            assert secret not in v

    def test_secret_not_in_output_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "sk-REAL-SECRET-SHOULD-NOT-APPEAR"
        out = self._run_with_exc(tmp_path, monkeypatch, secret)
        assert secret not in json.dumps(out)

    def test_submit_order_called_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run_with_exc(tmp_path, monkeypatch, "err")
        assert out["submit_order_called"] is True

    def test_broker_mutation_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run_with_exc(tmp_path, monkeypatch, "err")
        assert out["broker_mutation_calls_made"] is False

    def test_ledger_row_status_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run_with_exc(tmp_path, monkeypatch, "err")
        ledger = tmp_path / "ledger.csv"
        assert ledger.exists()
        with ledger.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["status"] == "exception"
        assert rows[0]["error"] == "details redacted"

    def test_blocker_is_generic_redacted_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run_with_exc(tmp_path, monkeypatch, "highly-specific-error-text")
        assert out["blocker"] == "broker exception during submit (details redacted)"


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

def _source_text() -> str:
    return (
        Path("src/tools/live_single_manual_submit.py")
        .read_text(encoding="utf-8")
    )


def _source_lines() -> list[str]:
    return _source_text().splitlines()


def _non_comment_lines() -> list[str]:
    return [ln for ln in _source_lines() if not ln.strip().startswith("#")]


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

    def test_no_cancel_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "cancel_order(" not in line, f"cancel_order( found: {line!r}"

    def test_no_replace_order_call(self) -> None:
        for line in _non_comment_lines():
            assert "replace_order(" not in line, f"replace_order( found: {line!r}"

    def test_no_append_live_ledger_row(self) -> None:
        for line in _non_comment_lines():
            assert "append_live_ledger_row" not in line

    def test_no_retry_loop(self) -> None:
        src = _source_text()
        assert "while True" not in src
        assert "for _ in range" not in src

    def test_no_module_level_os_environ_get(self) -> None:
        for line in _source_lines():
            if line.startswith("os.environ"):
                raise AssertionError(
                    f"Module-level os.environ call found: {line!r}"
                )


# ---------------------------------------------------------------------------
# TestLedgerColumns
# ---------------------------------------------------------------------------

class TestLedgerColumns:
    def test_ledger_columns_defined(self) -> None:
        assert isinstance(LEDGER_COLUMNS, list)
        assert len(LEDGER_COLUMNS) > 0

    def test_ledger_columns_include_required(self) -> None:
        required = {
            "timestamp_utc", "client_order_id", "symbol", "side",
            "order_type", "notional_cap", "status", "broker_order_id", "error",
        }
        assert required.issubset(set(LEDGER_COLUMNS))
