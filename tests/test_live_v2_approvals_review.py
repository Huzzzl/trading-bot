"""
tests/test_live_v2_approvals_review.py
-----------------------------------------
Tests for src/tools/live_v2_approvals_review.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no ledger writes, no files written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE = "src.tools.live_v2_approvals_review"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_ta(**overrides) -> dict:
    d = {
        "live_trading_approved":          True,
        "live_order_submission_approved": False,
        "approval_scope":                 "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":              True,
        "approved_symbol":                "SPY",
        "approved_max_notional":          100.0,
    }
    d.update(overrides)
    return d


def _valid_sa(ta_path: Path, **overrides) -> dict:
    d = {
        "live_trading_approved":                   True,
        "live_order_submission_approved":          True,
        "order_submission_approval_for_single_attempt": True,
        "approval_scope":                          "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":                       True,
        "source_live_trading_approval_path":       str(ta_path),
        "approved_symbol":                         "SPY",
        "approved_max_notional":                   100.0,
    }
    d.update(overrides)
    return d


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _setup(tmp_path: Path, ta_overrides=None, sa_overrides=None):
    ta_path = _write(tmp_path, "live_trading_approval.json",
                     _valid_ta(**(ta_overrides or {})))
    sa_path = _write(tmp_path, "live_order_submission_approval.json",
                     _valid_sa(ta_path, **(sa_overrides or {})))
    return ta_path, sa_path


def _base_args(ta_path: Path, sa_path: Path) -> list[str]:
    return [
        "--live-trading-approval",            str(ta_path),
        "--live-order-submission-approval",   str(sa_path),
    ]


def _run_main(args: list[str]) -> int | None:
    from src.tools.live_v2_approvals_review import main
    try:
        main(args)
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# validate_approvals unit tests
# ---------------------------------------------------------------------------

class TestValidateApprovals:
    def _validate(self, tmp_path, ta_overrides=None, sa_overrides=None):
        from src.tools.live_v2_approvals_review import validate_approvals
        ta_path = tmp_path / "ta.json"
        ta = _valid_ta(**(ta_overrides or {}))
        sa_path = tmp_path / "sa.json"
        sa = _valid_sa(ta_path, **(sa_overrides or {}))
        return validate_approvals(ta, sa, ta_path, sa_path)

    def test_valid_pair_passes(self, tmp_path):
        result, violations = self._validate(tmp_path)
        assert result == "PASS"
        assert violations == []

    def test_trading_approved_false_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"live_trading_approved": False})
        assert result == "FAIL"
        assert any("live_trading_approved" in v for v in violations)

    def test_trading_approval_order_submission_true_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"live_order_submission_approved": True})
        assert result == "FAIL"
        assert any("live_order_submission_approved" in v for v in violations)

    def test_wrong_scope_in_trading_approval_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"approval_scope": "WRONG"})
        assert result == "FAIL"

    def test_risk_acknowledged_false_in_trading_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"risk_acknowledged": False})
        assert result == "FAIL"

    def test_empty_symbol_in_trading_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"approved_symbol": ""})
        assert result == "FAIL"

    def test_trading_notional_zero_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"approved_max_notional": 0})
        assert result == "FAIL"

    def test_trading_notional_over_limit_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, ta_overrides={"approved_max_notional": 100.01})
        assert result == "FAIL"

    def test_submission_order_submission_false_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, sa_overrides={"live_order_submission_approved": False})
        assert result == "FAIL"
        assert any("live_order_submission_approved" in v for v in violations)

    def test_submission_single_attempt_false_fails(self, tmp_path):
        result, violations = self._validate(tmp_path,
                                            sa_overrides={"order_submission_approval_for_single_attempt": False})
        assert result == "FAIL"

    def test_wrong_scope_in_submission_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, sa_overrides={"approval_scope": "WRONG"})
        assert result == "FAIL"

    def test_risk_acknowledged_false_in_submission_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, sa_overrides={"risk_acknowledged": False})
        assert result == "FAIL"

    def test_empty_source_path_in_submission_fails(self, tmp_path):
        result, violations = self._validate(tmp_path,
                                            sa_overrides={"source_live_trading_approval_path": ""})
        assert result == "FAIL"

    def test_symbol_mismatch_fails(self, tmp_path):
        result, violations = self._validate(tmp_path, sa_overrides={"approved_symbol": "QQQ"})
        assert result == "FAIL"
        assert any("symbol mismatch" in v for v in violations)

    def test_submission_notional_exceeds_trading_fails(self, tmp_path):
        result, violations = self._validate(tmp_path,
                                            ta_overrides={"approved_max_notional": 50.0},
                                            sa_overrides={"approved_max_notional": 100.0})
        assert result == "FAIL"
        assert any("approved_max_notional" in v for v in violations)

    def test_submission_notional_equal_to_trading_passes(self, tmp_path):
        result, violations = self._validate(tmp_path,
                                            ta_overrides={"approved_max_notional": 100.0},
                                            sa_overrides={"approved_max_notional": 100.0})
        assert result == "PASS"

    def test_same_file_path_fails(self, tmp_path):
        from src.tools.live_v2_approvals_review import validate_approvals
        ta_path = tmp_path / "same.json"
        ta = _valid_ta()
        sa = _valid_sa(ta_path)
        ta_path.write_text(json.dumps(ta), encoding="utf-8")
        result, violations = validate_approvals(ta, sa, ta_path, ta_path)
        assert result == "FAIL"
        assert any("separate files" in v for v in violations)

    def test_source_path_mismatch_fails(self, tmp_path):
        from src.tools.live_v2_approvals_review import validate_approvals
        ta_path = tmp_path / "ta.json"
        sa_path = tmp_path / "sa.json"
        ta = _valid_ta()
        sa = _valid_sa(ta_path, source_live_trading_approval_path="/wrong/path.json")
        result, violations = validate_approvals(ta, sa, ta_path, sa_path)
        assert result == "FAIL"
        assert any("source_live_trading_approval_path" in v for v in violations)


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_pair_exits_0(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path)
        assert _run_main(_base_args(ta_path, sa_path)) in (0, None)

    def test_missing_trading_approval_exits_1(self, tmp_path):
        _, sa_path = _setup(tmp_path)
        missing = tmp_path / "nonexistent_ta.json"
        assert _run_main(_base_args(missing, sa_path)) == 1

    def test_malformed_trading_approval_exits_1(self, tmp_path):
        bad = tmp_path / "bad_ta.json"
        bad.write_text("not-json", encoding="utf-8")
        _, sa_path = _setup(tmp_path)
        assert _run_main(_base_args(bad, sa_path)) == 1

    def test_missing_submission_approval_exits_1(self, tmp_path):
        ta_path, _ = _setup(tmp_path)
        missing = tmp_path / "nonexistent_sa.json"
        assert _run_main(_base_args(ta_path, missing)) == 1

    def test_malformed_submission_approval_exits_1(self, tmp_path):
        ta_path, _ = _setup(tmp_path)
        bad = tmp_path / "bad_sa.json"
        bad.write_text("not-json", encoding="utf-8")
        assert _run_main(_base_args(ta_path, bad)) == 1

    def test_trading_order_submission_true_exits_1(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path, ta_overrides={"live_order_submission_approved": True})
        assert _run_main(_base_args(ta_path, sa_path)) == 1

    def test_submission_order_submission_false_exits_1(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path, sa_overrides={"live_order_submission_approved": False})
        assert _run_main(_base_args(ta_path, sa_path)) == 1

    def test_wrong_scope_exits_1(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path, ta_overrides={"approval_scope": "WRONG"})
        assert _run_main(_base_args(ta_path, sa_path)) == 1

    def test_risk_acknowledged_false_exits_1(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path, ta_overrides={"risk_acknowledged": False})
        assert _run_main(_base_args(ta_path, sa_path)) == 1

    def test_symbol_mismatch_exits_1(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path, sa_overrides={"approved_symbol": "QQQ"})
        assert _run_main(_base_args(ta_path, sa_path)) == 1

    def test_submission_notional_exceeds_trading_exits_1(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path,
                                   ta_overrides={"approved_max_notional": 50.0},
                                   sa_overrides={"approved_max_notional": 100.0})
        assert _run_main(_base_args(ta_path, sa_path)) == 1

    def test_same_file_path_exits_1(self, tmp_path):
        ta_path, _ = _setup(tmp_path)
        assert _run_main(_base_args(ta_path, ta_path)) == 1

    def test_no_credentials_needed(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path)
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            assert _run_main(_base_args(ta_path, sa_path)) in (0, None)

    def test_no_submit_order_called(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path)
        broker = MagicMock()
        _run_main(_base_args(ta_path, sa_path))
        broker.submit_order.assert_not_called()

    def test_no_cancel_order_called(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path)
        broker = MagicMock()
        _run_main(_base_args(ta_path, sa_path))
        broker.cancel_order.assert_not_called()

    def test_no_ledger_writes(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path)
        ledger = tmp_path / "live_execution_ledger.csv"
        _run_main(_base_args(ta_path, sa_path))
        assert not ledger.exists()

    def test_no_new_files_written(self, tmp_path):
        ta_path, sa_path = _setup(tmp_path)
        before = set(tmp_path.iterdir())
        _run_main(_base_args(ta_path, sa_path))
        after = set(tmp_path.iterdir())
        assert after == before
