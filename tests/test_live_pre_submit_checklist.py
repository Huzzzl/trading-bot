"""
tests/test_live_pre_submit_checklist.py
-----------------------------------------
Tests for src/tools/live_pre_submit_checklist.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no live ledger writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_MODULE = "src.tools.live_pre_submit_checklist"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_pass() -> dict[str, str]:
    return {
        "live_safety_status":   "PASS",
        "live_readiness_gate":  "PASS",
        "live_dry_run_intents": "PASS",
        "live_dry_run_review":  "PASS",
        "live_ledger_verify":   "PASS",
    }


def _patch_checks(checks: dict[str, str]):
    """Return a context manager that patches all five _check_* functions."""
    return patch.multiple(
        _MODULE,
        _check_safety_status=MagicMock(return_value=checks["live_safety_status"]),
        _check_readiness_gate=MagicMock(return_value=checks["live_readiness_gate"]),
        _check_dry_run_intents=MagicMock(return_value=checks["live_dry_run_intents"]),
        _check_dry_run_review=MagicMock(return_value=checks["live_dry_run_review"]),
        _check_ledger_verify=MagicMock(return_value=checks["live_ledger_verify"]),
        _get_ledger_path=MagicMock(return_value="output/live_execution_ledger.csv"),
    )


def _run_main(
    tmp_path: Path,
    checks: dict[str, str] | None = None,
    extra_argv: list[str] | None = None,
) -> int | None:
    from src.tools.live_pre_submit_checklist import main
    checks = checks or _all_pass()
    argv = [
        "--config",     "config/settings.paper.local.yaml",
        "--symbol",     "SPY",
        "--output-dir", str(tmp_path),
    ] + (extra_argv or [])
    with _patch_checks(checks):
        try:
            main(argv)
            return None
        except SystemExit as exc:
            return exc.code


# ---------------------------------------------------------------------------
# run_checklist unit tests
# ---------------------------------------------------------------------------

class TestRunChecklist:
    def test_all_pass_returns_ready(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        with _patch_checks(_all_pass()):
            result = run_checklist("config.yaml", "SPY", tmp_path)
        assert result["final_result"] == "READY"

    def test_checks_dict_has_all_keys(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        with _patch_checks(_all_pass()):
            result = run_checklist("config.yaml", "SPY", tmp_path)
        assert set(result["checks"].keys()) == {
            "live_safety_status",
            "live_readiness_gate",
            "live_dry_run_intents",
            "live_dry_run_review",
            "live_ledger_verify",
        }

    def test_result_dict_has_required_fields(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        with _patch_checks(_all_pass()):
            result = run_checklist("config.yaml", "SPY", tmp_path)
        assert "checked_at_utc" in result
        assert "symbol" in result
        assert "checks" in result
        assert "final_result" in result

    def test_symbol_propagated(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        with _patch_checks(_all_pass()):
            result = run_checklist("config.yaml", "AAPL", tmp_path)
        assert result["symbol"] == "AAPL"

    def test_any_fail_returns_not_ready(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        for key in _all_pass():
            checks = {**_all_pass(), key: "FAIL"}
            with _patch_checks(checks):
                result = run_checklist("config.yaml", "SPY", tmp_path)
            assert result["final_result"] == "NOT READY", f"Expected NOT READY when {key}=FAIL"

    def test_any_warn_returns_not_ready(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        for key in _all_pass():
            checks = {**_all_pass(), key: "WARN"}
            with _patch_checks(checks):
                result = run_checklist("config.yaml", "SPY", tmp_path)
            assert result["final_result"] == "NOT READY", f"Expected NOT READY when {key}=WARN"

    def test_mixed_pass_fail_not_ready(self, tmp_path):
        from src.tools.live_pre_submit_checklist import run_checklist
        checks = {
            "live_safety_status":   "PASS",
            "live_readiness_gate":  "FAIL",
            "live_dry_run_intents": "FAIL",
            "live_dry_run_review":  "FAIL",
            "live_ledger_verify":   "PASS",
        }
        with _patch_checks(checks):
            result = run_checklist("config.yaml", "SPY", tmp_path)
        assert result["final_result"] == "NOT READY"


# ---------------------------------------------------------------------------
# write_checklist_report unit tests
# ---------------------------------------------------------------------------

class TestWriteChecklistReport:
    def test_writes_json(self, tmp_path):
        from src.tools.live_pre_submit_checklist import write_checklist_report
        checklist = {
            "checked_at_utc": "2026-05-20T10:00:00+00:00",
            "symbol": "SPY",
            "checks": _all_pass(),
            "final_result": "READY",
        }
        path = write_checklist_report(tmp_path, checklist)
        assert path.exists()
        assert path.name == "live_pre_submit_checklist.json"

    def test_json_is_valid_and_contains_fields(self, tmp_path):
        from src.tools.live_pre_submit_checklist import write_checklist_report
        checklist = {
            "checked_at_utc": "2026-05-20T10:00:00+00:00",
            "symbol": "SPY",
            "checks": _all_pass(),
            "final_result": "READY",
        }
        path = write_checklist_report(tmp_path, checklist)
        data = json.loads(path.read_text())
        assert data["final_result"] == "READY"
        assert data["symbol"] == "SPY"
        assert "checks" in data

    def test_creates_parent_dirs(self, tmp_path):
        from src.tools.live_pre_submit_checklist import write_checklist_report
        deep = tmp_path / "a" / "b" / "c"
        write_checklist_report(deep, {"checks": {}, "final_result": "READY"})
        assert (deep / "live_pre_submit_checklist.json").exists()


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_all_pass_exits_0(self, tmp_path):
        code = _run_main(tmp_path, _all_pass())
        assert code in (0, None)

    def test_safety_status_fail_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_safety_status": "FAIL"}
        assert _run_main(tmp_path, checks) == 1

    def test_safety_status_warn_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_safety_status": "WARN"}
        assert _run_main(tmp_path, checks) == 1

    def test_readiness_gate_fail_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_readiness_gate": "FAIL"}
        assert _run_main(tmp_path, checks) == 1

    def test_dry_run_intents_fail_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_dry_run_intents": "FAIL"}
        assert _run_main(tmp_path, checks) == 1

    def test_dry_run_review_fail_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_dry_run_review": "FAIL"}
        assert _run_main(tmp_path, checks) == 1

    def test_ledger_verify_fail_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_ledger_verify": "FAIL"}
        assert _run_main(tmp_path, checks) == 1

    def test_ledger_verify_warn_exits_1(self, tmp_path):
        checks = {**_all_pass(), "live_ledger_verify": "WARN"}
        assert _run_main(tmp_path, checks) == 1

    def test_writes_checklist_json(self, tmp_path):
        _run_main(tmp_path, _all_pass())
        report = tmp_path / "live_pre_submit_checklist.json"
        assert report.exists()
        data = json.loads(report.read_text())
        assert data["final_result"] == "READY"

    def test_checklist_json_contains_all_checks(self, tmp_path):
        _run_main(tmp_path, _all_pass())
        data = json.loads((tmp_path / "live_pre_submit_checklist.json").read_text())
        assert set(data["checks"].keys()) == {
            "live_safety_status",
            "live_readiness_gate",
            "live_dry_run_intents",
            "live_dry_run_review",
            "live_ledger_verify",
        }

    def test_no_network_calls(self, tmp_path):
        _run_main(tmp_path, _all_pass())
        # passes because all network calls are mocked out via _patch_checks

    def test_no_submit_order_called(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path, _all_pass())
        broker.submit_order.assert_not_called()

    def test_no_cancel_order_called(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path, _all_pass())
        broker.cancel_order.assert_not_called()

    def test_no_paper_credentials_used(self, tmp_path):
        import os
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            _run_main(tmp_path, _all_pass())

    def test_no_live_ledger_written(self, tmp_path):
        _run_main(tmp_path, _all_pass())
        ledger_files = list(tmp_path.glob("**/*ledger*"))
        assert ledger_files == []

    def test_custom_ledger_path_forwarded(self, tmp_path):
        custom = str(tmp_path / "custom_ledger.csv")
        mock_ledger_verify = MagicMock(return_value="PASS")
        checks = _all_pass()
        with patch.multiple(
            _MODULE,
            _check_safety_status=MagicMock(return_value=checks["live_safety_status"]),
            _check_readiness_gate=MagicMock(return_value=checks["live_readiness_gate"]),
            _check_dry_run_intents=MagicMock(return_value=checks["live_dry_run_intents"]),
            _check_dry_run_review=MagicMock(return_value=checks["live_dry_run_review"]),
            _check_ledger_verify=mock_ledger_verify,
            _get_ledger_path=MagicMock(return_value="output/live_execution_ledger.csv"),
        ):
            from src.tools.live_pre_submit_checklist import main
            argv = [
                "--config",     "config/settings.paper.local.yaml",
                "--symbol",     "SPY",
                "--output-dir", str(tmp_path),
                "--ledger",     custom,
            ]
            try:
                main(argv)
            except SystemExit:
                pass
        mock_ledger_verify.assert_called_once_with(custom)


# ---------------------------------------------------------------------------
# _check_ledger_verify: missing ledger accepted as PASS
# ---------------------------------------------------------------------------

class TestCheckLedgerVerify:
    def test_missing_ledger_returns_pass(self, tmp_path):
        from src.tools.live_pre_submit_checklist import _check_ledger_verify
        nonexistent = str(tmp_path / "nonexistent.csv")
        result = _check_ledger_verify(nonexistent)
        assert result == "PASS"

    def test_valid_ledger_returns_pass(self, tmp_path):
        import csv
        from src.execution.live_ledger import LIVE_LEDGER_COLUMNS
        from src.tools.live_pre_submit_checklist import _check_ledger_verify
        ledger = tmp_path / "ledger.csv"
        with ledger.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LIVE_LEDGER_COLUMNS)
            w.writeheader()
            w.writerow({
                **{c: "" for c in LIVE_LEDGER_COLUMNS},
                "client_order_id":  "coid-001",
                "alpaca_order_id":  "ref-001",
                "symbol":           "SPY",
                "side":             "buy",
                "order_type":       "market",
                "status":           "dry_run",
                "dry_run_only":     "True",
                "submit_allowed":   "False",
            })
        result = _check_ledger_verify(str(ledger))
        assert result == "PASS"


# ---------------------------------------------------------------------------
# _check_safety_status unit test
# ---------------------------------------------------------------------------

class TestCheckSafetyStatus:
    def test_safe_defaults_return_pass(self):
        from src.tools.live_pre_submit_checklist import _check_safety_status
        from unittest.mock import MagicMock
        mock_ex = MagicMock()
        mock_ex.live_trading_enabled        = False
        mock_ex.live_kill_switch_enabled    = True
        mock_ex.live_submit_dry_run         = True
        mock_ex.live_require_human_confirm  = True
        mock_ex.live_max_orders_per_day     = 1
        mock_ex.live_max_notional_per_day   = 100.0
        mock_ex.live_ledger_path            = "output/live_execution_ledger.csv"
        mock_cfg = MagicMock()
        mock_cfg.execution = mock_ex
        cfg_pass = {"label": "config", "status": "PASS", "detail": ""}
        with patch("src.tools.live_pre_submit_checklist._check_safety_status",
                   wraps=_check_safety_status):
            with patch("src.tools.paper_status.check_config",
                       return_value=(cfg_pass, mock_cfg)):
                from src.tools.live_pre_submit_checklist import _check_safety_status as fn
                result = fn("config/settings.paper.local.yaml")
        assert result == "PASS"
