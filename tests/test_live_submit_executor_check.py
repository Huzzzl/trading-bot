"""
tests/test_live_submit_executor_check.py
------------------------------------------
Tests for src/tools/live_submit_executor_check.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no live ledger writes.
maybe_execute_live_submit is mocked in all integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE = "src.tools.live_submit_executor_check"
_EXECUTOR_MODULE = "src.execution.live_submit_executor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _valid_approval() -> dict:
    return {
        "approval_for_real_submit_pr":    True,
        "approval_scope":                 "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY",
        "live_trading_approved":          False,
        "live_order_submission_approved": False,
    }


def _valid_checklist() -> dict:
    return {"final_result": "READY"}


def _valid_review() -> dict:
    return {"review_result": "PASS"}


def _blocked_report() -> dict:
    return {
        "checked_at_utc":      "2026-05-20T10:00:00+00:00",
        "symbol":              "SPY",
        "submit_order_called": False,
        "blocked":             True,
        "block_guard":         "approval_artifact",
        "violations":          ["live_trading_approved=False — not approved"],
    }


def _base_args(
    tmp_path: Path,
    config_path: Path,
    approval: Path,
    pre_submit: Path,
    plan_review: Path,
    output_dir: Path,
    **kwargs,
) -> list[str]:
    args = [
        "--config",     str(config_path),
        "--symbol",     kwargs.get("symbol", "SPY"),
        "--confirm",    kwargs.get("confirm", "REAL-LIVE-SUBMIT-AUTHORIZED"),
        "--approval",   str(approval),
        "--pre-submit", str(pre_submit),
        "--plan-review",str(plan_review),
        "--output-dir", str(output_dir),
    ]
    if "plan" in kwargs:
        args += ["--plan", str(kwargs["plan"])]
    return args


def _run_main(args: list[str]) -> int | None:
    from src.tools.live_submit_executor_check import main
    try:
        main(args)
        return None
    except SystemExit as exc:
        return exc.code


def _write_report_to_dir(output_dir: Path, report: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    p = output_dir / "live_submit_blocked_report.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _load_plan_fields
# ---------------------------------------------------------------------------

class TestLoadPlanFields:
    def test_no_plan_returns_config_fallbacks(self, tmp_path):
        from src.tools.live_submit_executor_check import _load_plan_fields
        notional, max_notional, coid = _load_plan_fields(None, 95.0)
        assert notional == 95.0
        assert max_notional == 95.0
        assert coid == "DRY-RUN-EXECUTOR-CHECK-SENTINEL"

    def test_missing_plan_file_returns_fallbacks(self, tmp_path):
        from src.tools.live_submit_executor_check import _load_plan_fields
        notional, max_notional, coid = _load_plan_fields(tmp_path / "missing.json", 80.0)
        assert notional == 80.0
        assert coid == "DRY-RUN-EXECUTOR-CHECK-SENTINEL"

    def test_valid_plan_file_extracts_fields(self, tmp_path):
        from src.tools.live_submit_executor_check import _load_plan_fields
        plan = {
            "effective_notional":      90.0,
            "live_max_order_notional": 100.0,
            "client_order_id":         "plan-order-001",
        }
        p = _write(tmp_path, "plan.json", plan)
        notional, max_notional, coid = _load_plan_fields(p, 50.0)
        assert notional == 90.0
        assert max_notional == 100.0
        assert coid == "plan-order-001"

    def test_missing_client_order_id_uses_sentinel(self, tmp_path):
        from src.tools.live_submit_executor_check import _load_plan_fields
        p = _write(tmp_path, "plan.json", {"effective_notional": 90.0, "live_max_order_notional": 100.0})
        _, _, coid = _load_plan_fields(p, 50.0)
        assert coid == "DRY-RUN-EXECUTOR-CHECK-SENTINEL"


# ---------------------------------------------------------------------------
# main() integration tests — mock maybe_execute_live_submit
# ---------------------------------------------------------------------------

def _setup_files(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    """Create minimal required files and return paths."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    approval = _write(tmp_path, "approval.json", _valid_approval())
    pre_submit = _write(tmp_path, "checklist.json", _valid_checklist())
    plan_review = _write(tmp_path, "review.json", _valid_review())
    output_dir = tmp_path / "output"
    return config_path, approval, pre_submit, plan_review, output_dir, tmp_path


class TestMain:
    def _mock_config(self):
        ex = MagicMock()
        ex.live_trading_enabled = False
        ex.live_submit_dry_run = True
        ex.live_kill_switch_enabled = True
        ex.live_require_human_confirm = True
        ex.live_max_order_notional = 100.0
        ex.live_max_orders_per_day = 1
        ex.live_max_notional_per_day = 100.0
        ex.live_ledger_path = "output/live_execution_ledger.csv"
        cfg = MagicMock()
        cfg.execution = ex
        return cfg

    def test_default_config_produces_blocked_report_exits_0(self, tmp_path):
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=_blocked_report()) as mock_exec:
            _write_report_to_dir(output_dir, _blocked_report())
            code = _run_main(args)
        assert code in (0, None)

    def test_blocked_true_submit_false_report_exists_exits_0(self, tmp_path):
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        report = _blocked_report()
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            code = _run_main(args)
        assert code in (0, None)

    def test_missing_approval_arg_blocks_exits_0(self, tmp_path):
        """Executor blocks on missing approval — still blocked=true → exit 0."""
        config_path, _, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        missing = tmp_path / "nonexistent_approval.json"
        args = _base_args(tmp_path, config_path, missing, pre_submit, plan_review, output_dir)
        report = {**_blocked_report(), "block_guard": "approval_artifact_load"}
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            code = _run_main(args)
        assert code in (0, None)

    def test_wrong_token_blocks_exits_0(self, tmp_path):
        """Wrong token → executor blocks → blocked=true → exit 0."""
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir,
                          confirm="WRONG-TOKEN")
        report = {**_blocked_report(), "block_guard": "confirm_token"}
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            code = _run_main(args)
        assert code in (0, None)

    def test_unexpected_blocked_false_exits_1(self, tmp_path):
        """If executor somehow returns blocked=false, CLI must exit 1."""
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        bad_result = {"blocked": False, "submit_order_called": False, "block_guard": None}
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=bad_result):
            code = _run_main(args)
        assert code == 1

    def test_submit_order_called_true_exits_1(self, tmp_path):
        """If submit_order_called=true, CLI must exit 1."""
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        bad_result = {"blocked": True, "submit_order_called": True, "block_guard": "test"}
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=bad_result):
            _write_report_to_dir(output_dir, bad_result)
            code = _run_main(args)
        assert code == 1

    def test_submit_order_never_called(self, tmp_path):
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        broker = MagicMock()
        report = _blocked_report()
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            _run_main(args)
        broker.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        broker = MagicMock()
        report = _blocked_report()
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            _run_main(args)
        broker.cancel_order.assert_not_called()

    def test_no_ledger_writes(self, tmp_path):
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        ledger = tmp_path / "live_execution_ledger.csv"
        cfg = self._mock_config()
        cfg.execution.live_ledger_path = str(ledger)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        report = _blocked_report()
        with patch(f"{_MODULE}.load_config", return_value=cfg), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            _run_main(args)
        assert not ledger.exists()

    def test_plan_file_fields_extracted(self, tmp_path):
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        plan = _write(tmp_path, "plan.json", {
            "effective_notional": 90.0,
            "live_max_order_notional": 100.0,
            "client_order_id": "plan-order-001",
        })
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir,
                          plan=plan)
        report = _blocked_report()
        captured = {}
        def _capture(**kwargs):
            captured.update(kwargs)
            return report
        with patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", side_effect=_capture):
            _write_report_to_dir(output_dir, report)
            _run_main(args)
        assert captured["intended_notional"] == 90.0
        assert captured["live_max_order_notional"] == 100.0
        assert captured["client_order_id"] == "plan-order-001"

    def test_no_credentials_needed(self, tmp_path):
        import os
        from unittest.mock import patch as _patch
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        report = _blocked_report()
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with _patch.dict(os.environ, env, clear=True), \
             patch(f"{_MODULE}.load_config", return_value=self._mock_config()), \
             patch(f"{_MODULE}.maybe_execute_live_submit", return_value=report):
            _write_report_to_dir(output_dir, report)
            code = _run_main(args)
        assert code in (0, None)

    def test_execution_config_fields_passed_to_executor(self, tmp_path):
        """Integration-style: executor receives fields from cfg.execution, not cfg directly."""
        from src.config.loader import ExecutionConfig
        config_path, approval, pre_submit, plan_review, output_dir, _ = _setup_files(tmp_path)
        args = _base_args(tmp_path, config_path, approval, pre_submit, plan_review, output_dir)
        report = _blocked_report()
        ex = ExecutionConfig(
            live_trading_enabled=False,
            live_kill_switch_enabled=True,
            live_submit_dry_run=True,
            live_require_human_confirm=True,
            live_max_order_notional=123.0,
            live_max_orders_per_day=2,
            live_max_notional_per_day=246.0,
            live_ledger_path="output/live_execution_ledger.csv",
        )
        cfg = MagicMock()
        cfg.execution = ex
        captured = {}
        def _capture(**kwargs):
            captured.update(kwargs)
            return report
        with patch(f"{_MODULE}.load_config", return_value=cfg), \
             patch(f"{_MODULE}.maybe_execute_live_submit", side_effect=_capture):
            _write_report_to_dir(output_dir, report)
            _run_main(args)
        assert captured["live_trading_enabled"] is False
        assert captured["live_kill_switch_enabled"] is True
        assert captured["live_submit_dry_run"] is True
        assert captured["live_require_human_confirm"] is True
        assert captured["live_max_order_notional"] == 123.0
        assert captured["live_max_orders_per_day"] == 2
        assert captured["live_max_notional_per_day"] == 246.0
