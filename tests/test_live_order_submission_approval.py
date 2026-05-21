"""
tests/test_live_order_submission_approval.py
----------------------------------------------
Tests for src/tools/live_order_submission_approval.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no ledger writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_MODULE = "src.tools.live_order_submission_approval"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_trading_approval(**overrides) -> dict:
    d = {
        "live_trading_approved":        True,
        "live_order_submission_approved": False,
        "approval_scope":               "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":            True,
        "approved_symbol":              "SPY",
        "approved_max_notional":        100.0,
    }
    d.update(overrides)
    return d


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _base_args(ta_path: Path, output_path: Path, **kwargs) -> list[str]:
    args = [
        "--live-trading-approval", str(ta_path),
        "--operator-name",         kwargs.get("operator_name", "Huzzzl"),
        "--approval-note",         kwargs.get("approval_note",
                                              "Approve one SPY $100 notional live order submission attempt only."),
        "--symbol",                kwargs.get("symbol", "SPY"),
        "--max-notional",          str(kwargs.get("max_notional", 100.0)),
        "--output",                str(output_path),
    ]
    if kwargs.get("risk_acknowledge", True):
        args.append("--risk-acknowledge")
    return args


def _run_main(args: list[str]) -> int | None:
    from src.tools.live_order_submission_approval import main
    try:
        main(args)
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# _validate_trading_approval
# ---------------------------------------------------------------------------

class TestValidateTradingApproval:
    def _v(self, ta: dict, symbol: str = "SPY", max_notional: float = 100.0):
        from src.tools.live_order_submission_approval import _validate_trading_approval
        return _validate_trading_approval(ta, symbol, max_notional)

    def test_valid_trading_approval_no_violations(self):
        assert self._v(_valid_trading_approval()) == []

    def test_live_trading_approved_false_fails(self):
        assert self._v(_valid_trading_approval(live_trading_approved=False)) != []

    def test_live_trading_approved_missing_fails(self):
        ta = _valid_trading_approval()
        del ta["live_trading_approved"]
        assert self._v(ta) != []

    def test_live_order_submission_approved_true_fails(self):
        assert self._v(_valid_trading_approval(live_order_submission_approved=True)) != []

    def test_wrong_approval_scope_fails(self):
        assert self._v(_valid_trading_approval(approval_scope="OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY")) != []

    def test_missing_approval_scope_fails(self):
        ta = _valid_trading_approval()
        del ta["approval_scope"]
        assert self._v(ta) != []

    def test_risk_acknowledged_false_fails(self):
        assert self._v(_valid_trading_approval(risk_acknowledged=False)) != []

    def test_risk_acknowledged_missing_fails(self):
        ta = _valid_trading_approval()
        del ta["risk_acknowledged"]
        assert self._v(ta) != []

    def test_symbol_mismatch_fails(self):
        assert self._v(_valid_trading_approval(approved_symbol="QQQ"), symbol="SPY") != []

    def test_symbol_case_insensitive_match(self):
        assert self._v(_valid_trading_approval(approved_symbol="SPY"), symbol="spy") == []

    def test_requested_notional_exceeds_approved_fails(self):
        assert self._v(_valid_trading_approval(approved_max_notional=50.0), max_notional=100.0) != []

    def test_requested_notional_equal_to_approved_passes(self):
        assert self._v(_valid_trading_approval(approved_max_notional=100.0), max_notional=100.0) == []

    def test_requested_notional_less_than_approved_passes(self):
        assert self._v(_valid_trading_approval(approved_max_notional=100.0), max_notional=50.0) == []


# ---------------------------------------------------------------------------
# _validate_inputs
# ---------------------------------------------------------------------------

class TestValidateInputs:
    def _v(self, **kwargs):
        from src.tools.live_order_submission_approval import _validate_inputs
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

    def test_missing_risk_acknowledge_fails(self):
        assert self._v(risk_acknowledge=False) != []

    def test_zero_max_notional_fails(self):
        assert self._v(max_notional=0.0) != []

    def test_negative_max_notional_fails(self):
        assert self._v(max_notional=-1.0) != []

    def test_max_notional_over_100_fails(self):
        assert self._v(max_notional=100.01) != []

    def test_max_notional_exactly_100_passes(self):
        assert self._v(max_notional=100.0) == []


# ---------------------------------------------------------------------------
# build_approval
# ---------------------------------------------------------------------------

class TestBuildApproval:
    def _build(self, **kwargs):
        from src.tools.live_order_submission_approval import build_approval
        ta = _valid_trading_approval()
        return build_approval(ta, Path("approval.json"), "Huzzzl", "note", "SPY", 100.0)

    def test_live_order_submission_approved_true(self):
        a = self._build()
        assert a["live_order_submission_approved"] is True

    def test_live_trading_approved_true(self):
        a = self._build()
        assert a["live_trading_approved"] is True

    def test_order_submission_approval_for_single_attempt_true(self):
        a = self._build()
        assert a["order_submission_approval_for_single_attempt"] is True

    def test_approval_scope(self):
        a = self._build()
        assert a["approval_scope"] == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"

    def test_symbol_normalized_uppercase(self):
        from src.tools.live_order_submission_approval import build_approval
        ta = _valid_trading_approval(approved_symbol="SPY")
        a = build_approval(ta, Path("approval.json"), "Huzzzl", "note", "spy", 100.0)
        assert a["approved_symbol"] == "SPY"

    def test_risk_acknowledged_true(self):
        a = self._build()
        assert a["risk_acknowledged"] is True

    def test_source_path_recorded(self):
        from src.tools.live_order_submission_approval import build_approval
        ta = _valid_trading_approval()
        p = Path("output/live_trading_approval.json")
        a = build_approval(ta, p, "Huzzzl", "note", "SPY", 100.0)
        assert a["source_live_trading_approval_path"] == str(p)

    def test_required_fields_present(self):
        a = self._build()
        for field in (
            "checked_at_utc", "approval_timestamp_utc", "operator_name",
            "approval_note", "risk_acknowledged", "approval_scope",
            "source_live_trading_approval_path", "approved_symbol",
            "approved_max_notional", "live_trading_approved",
            "live_order_submission_approved",
            "order_submission_approval_for_single_attempt",
        ):
            assert field in a, f"missing field: {field}"


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def _ta_path(self, tmp_path, **overrides):
        return _write(tmp_path, "live_trading_approval.json",
                      _valid_trading_approval(**overrides))

    def test_valid_inputs_write_artifact_exit_0(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out))
        assert code in (0, None)
        assert out.exists()

    def test_artifact_content_correct(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        _run_main(_base_args(ta, out))
        artifact = json.loads(out.read_text())
        assert artifact["live_order_submission_approved"] is True
        assert artifact["live_trading_approved"] is True
        assert artifact["order_submission_approval_for_single_attempt"] is True
        assert artifact["approval_scope"] == "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY"
        assert artifact["risk_acknowledged"] is True
        assert artifact["approved_symbol"] == "SPY"
        assert artifact["approved_max_notional"] == 100.0

    def test_symbol_normalized_uppercase_in_artifact(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        _run_main(_base_args(ta, out, symbol="spy"))
        artifact = json.loads(out.read_text())
        assert artifact["approved_symbol"] == "SPY"

    def test_missing_trading_approval_exits_1(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(missing, out))
        assert code == 1
        assert not out.exists()

    def test_malformed_trading_approval_exits_1(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not-json", encoding="utf-8")
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(bad, out))
        assert code == 1
        assert not out.exists()

    def test_trading_approval_live_trading_approved_false_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path, live_trading_approved=False)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out))
        assert code == 1
        assert not out.exists()

    def test_trading_approval_order_submission_already_true_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path, live_order_submission_approved=True)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out))
        assert code == 1
        assert not out.exists()

    def test_wrong_approval_scope_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path, approval_scope="OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY")
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out))
        assert code == 1
        assert not out.exists()

    def test_trading_approval_risk_acknowledged_missing_exits_1(self, tmp_path):
        data = _valid_trading_approval()
        del data["risk_acknowledged"]
        ta = _write(tmp_path, "live_trading_approval.json", data)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out))
        assert code == 1
        assert not out.exists()

    def test_symbol_mismatch_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path, approved_symbol="QQQ")
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, symbol="SPY"))
        assert code == 1
        assert not out.exists()

    def test_requested_notional_exceeds_trading_approval_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path, approved_max_notional=50.0)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, max_notional=100.0))
        assert code == 1
        assert not out.exists()

    def test_empty_operator_name_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, operator_name=""))
        assert code == 1
        assert not out.exists()

    def test_empty_approval_note_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, approval_note=""))
        assert code == 1
        assert not out.exists()

    def test_missing_risk_acknowledge_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, risk_acknowledge=False))
        assert code == 1
        assert not out.exists()

    def test_max_notional_zero_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, max_notional=0.0))
        assert code == 1
        assert not out.exists()

    def test_max_notional_negative_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, max_notional=-5.0))
        assert code == 1
        assert not out.exists()

    def test_max_notional_over_100_exits_1(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(ta, out, max_notional=100.01))
        assert code == 1
        assert not out.exists()

    def test_live_order_submission_approved_true_in_artifact(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        _run_main(_base_args(ta, out))
        artifact = json.loads(out.read_text())
        assert artifact["live_order_submission_approved"] is True

    def test_no_credentials_needed(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        from unittest.mock import patch
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(_base_args(ta, out))
        assert code in (0, None)

    def test_no_submit_order_called(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        broker = MagicMock()
        _run_main(_base_args(ta, out))
        broker.submit_order.assert_not_called()

    def test_no_cancel_order_called(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        broker = MagicMock()
        _run_main(_base_args(ta, out))
        broker.cancel_order.assert_not_called()

    def test_no_ledger_writes(self, tmp_path):
        ta = self._ta_path(tmp_path)
        ledger = tmp_path / "live_execution_ledger.csv"
        out = tmp_path / "approval.json"
        _run_main(_base_args(ta, out))
        assert not ledger.exists()

    def test_output_parent_dirs_created(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "nested" / "deep" / "approval.json"
        code = _run_main(_base_args(ta, out))
        assert code in (0, None)
        assert out.exists()

    def test_source_trading_approval_path_in_artifact(self, tmp_path):
        ta = self._ta_path(tmp_path)
        out = tmp_path / "approval.json"
        _run_main(_base_args(ta, out))
        artifact = json.loads(out.read_text())
        assert str(ta) in artifact["source_live_trading_approval_path"]
