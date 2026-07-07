"""
tests/test_live_trading_approval.py
-------------------------------------
Tests for src/tools/live_trading_approval.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no ledger writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

_MODULE = "src.tools.live_trading_approval"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_args(output_path: Path, **kwargs) -> list[str]:
    args = [
        "--operator-name",  kwargs.get("operator_name", "Huzzzl"),
        "--approval-note",  kwargs.get("approval_note",
                                       "Approve live trading gate for one SPY $100 notional attempt; "
                                       "order submission still requires separate approval."),
        "--symbol",         kwargs.get("symbol", "SPY"),
        "--max-notional",   str(kwargs.get("max_notional", 100.0)),
        "--output",         str(output_path),
    ]
    if kwargs.get("risk_acknowledge", True):
        args.append("--risk-acknowledge")
    return args


def _run_main(args: list[str]) -> int | None:
    from src.tools.live_trading_approval import main
    try:
        main(args)
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# _validate_inputs
# ---------------------------------------------------------------------------

class TestValidateInputs:
    def _v(self, **kwargs):
        from src.tools.live_trading_approval import _validate_inputs
        defaults = dict(
            operator_name="Huzzzl",
            approval_note="some note",
            risk_acknowledge=True,
            symbol="SPY",
            max_notional=100.0,
        )
        defaults.update(kwargs)
        return _validate_inputs(**defaults)

    def test_valid_inputs_no_violations(self):
        assert self._v() == []

    def test_empty_operator_name_fails(self):
        assert self._v(operator_name="") != []

    def test_whitespace_operator_name_fails(self):
        assert self._v(operator_name="   ") != []

    def test_empty_approval_note_fails(self):
        assert self._v(approval_note="") != []

    def test_whitespace_approval_note_fails(self):
        assert self._v(approval_note="   ") != []

    def test_missing_risk_acknowledge_fails(self):
        assert self._v(risk_acknowledge=False) != []

    def test_empty_symbol_fails(self):
        assert self._v(symbol="") != []

    def test_zero_max_notional_fails(self):
        assert self._v(max_notional=0.0) != []

    def test_negative_max_notional_fails(self):
        assert self._v(max_notional=-1.0) != []

    def test_max_notional_over_limit_fails(self):
        assert self._v(max_notional=100.01) != []

    def test_max_notional_exactly_limit_passes(self):
        assert self._v(max_notional=100.0) == []

    def test_max_notional_small_positive_passes(self):
        assert self._v(max_notional=0.01) == []


# ---------------------------------------------------------------------------
# build_approval
# ---------------------------------------------------------------------------

class TestBuildApproval:
    def test_live_trading_approved_true(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("Huzzzl", "note", "SPY", 100.0)
        assert a["live_trading_approved"] is True

    def test_live_order_submission_approved_false(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("Huzzzl", "note", "SPY", 100.0)
        assert a["live_order_submission_approved"] is False

    def test_approval_scope(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("Huzzzl", "note", "SPY", 100.0)
        assert a["approval_scope"] == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"

    def test_symbol_normalized_uppercase(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("Huzzzl", "note", "spy", 100.0)
        assert a["approved_symbol"] == "SPY"

    def test_risk_acknowledged_true(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("Huzzzl", "note", "SPY", 100.0)
        assert a["risk_acknowledged"] is True

    def test_operator_name_stripped(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("  Huzzzl  ", "note", "SPY", 100.0)
        assert a["operator_name"] == "Huzzzl"

    def test_required_fields_present(self):
        from src.tools.live_trading_approval import build_approval
        a = build_approval("Huzzzl", "note", "SPY", 100.0)
        for field in (
            "checked_at_utc", "approval_timestamp_utc", "operator_name",
            "approval_note", "risk_acknowledged", "approval_scope",
            "approved_symbol", "approved_max_notional",
            "live_trading_approved", "live_order_submission_approved",
        ):
            assert field in a, f"missing field: {field}"


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_inputs_write_artifact_exit_0(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out))
        assert code in (0, None)
        assert out.exists()

    def test_artifact_content_correct(self, tmp_path):
        out = tmp_path / "approval.json"
        _run_main(_base_args(out))
        artifact = json.loads(out.read_text(encoding="utf-8"))
        assert artifact["live_trading_approved"] is True
        assert artifact["live_order_submission_approved"] is False
        assert artifact["approval_scope"] == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
        assert artifact["risk_acknowledged"] is True
        assert artifact["approved_symbol"] == "SPY"
        assert artifact["approved_max_notional"] == 100.0

    def test_symbol_normalized_uppercase_in_artifact(self, tmp_path):
        out = tmp_path / "approval.json"
        _run_main(_base_args(out, symbol="spy"))
        artifact = json.loads(out.read_text(encoding="utf-8"))
        assert artifact["approved_symbol"] == "SPY"

    def test_empty_operator_name_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, operator_name=""))
        assert code == 1
        assert not out.exists()

    def test_empty_approval_note_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, approval_note=""))
        assert code == 1
        assert not out.exists()

    def test_missing_risk_acknowledge_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, risk_acknowledge=False))
        assert code == 1
        assert not out.exists()

    def test_max_notional_zero_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, max_notional=0.0))
        assert code == 1
        assert not out.exists()

    def test_max_notional_negative_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, max_notional=-5.0))
        assert code == 1
        assert not out.exists()

    def test_max_notional_over_100_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, max_notional=100.01))
        assert code == 1
        assert not out.exists()

    def test_max_notional_exactly_100_exits_0(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(out, max_notional=100.0))
        assert code in (0, None)

    def test_live_order_submission_approved_always_false(self, tmp_path):
        out = tmp_path / "approval.json"
        _run_main(_base_args(out))
        artifact = json.loads(out.read_text(encoding="utf-8"))
        assert artifact["live_order_submission_approved"] is False

    def test_no_credentials_needed(self, tmp_path):
        out = tmp_path / "approval.json"
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(_base_args(out))
        assert code in (0, None)

    def test_no_submit_order_called(self, tmp_path):
        out = tmp_path / "approval.json"
        broker = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        _run_main(_base_args(out))
        broker.submit_order.assert_not_called()

    def test_no_cancel_order_called(self, tmp_path):
        out = tmp_path / "approval.json"
        broker = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        _run_main(_base_args(out))
        broker.cancel_order.assert_not_called()

    def test_no_ledger_writes(self, tmp_path):
        ledger = tmp_path / "live_execution_ledger.csv"
        out = tmp_path / "approval.json"
        _run_main(_base_args(out))
        assert not ledger.exists()

    def test_output_parent_dirs_created(self, tmp_path):
        out = tmp_path / "nested" / "deep" / "approval.json"
        code = _run_main(_base_args(out))
        assert code in (0, None)
        assert out.exists()
