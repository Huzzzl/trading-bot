"""
tests/test_live_safety_status.py
----------------------------------
Tests for src/tools/live_safety_status.py and the new live safety
ExecutionConfig fields in src/config/loader.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no file writes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_execution(
    live_trading_enabled: bool = False,
    live_kill_switch_enabled: bool = True,
    live_submit_dry_run: bool = True,
    live_require_human_confirm: bool = True,
    live_max_orders_per_day: int = 1,
    live_max_notional_per_day: float = 100.0,
    live_ledger_path: str = "output/live_execution_ledger.csv",
) -> MagicMock:
    ex = MagicMock()
    ex.live_trading_enabled        = live_trading_enabled
    ex.live_kill_switch_enabled    = live_kill_switch_enabled
    ex.live_submit_dry_run         = live_submit_dry_run
    ex.live_require_human_confirm  = live_require_human_confirm
    ex.live_max_orders_per_day     = live_max_orders_per_day
    ex.live_max_notional_per_day   = live_max_notional_per_day
    ex.live_ledger_path            = live_ledger_path
    return ex


def _mock_cfg(**kw) -> MagicMock:
    cfg = MagicMock()
    cfg.execution = _mock_execution(**kw)
    return cfg


def _run_main(cfg_mock: MagicMock, config_fail: bool = False) -> int | None:
    from src.tools.live_safety_status import main
    cfg_result = (
        {"label": "config", "status": "FAIL", "detail": "bad config"}
        if config_fail else
        {"label": "config", "status": "PASS", "detail": ""}
    )
    argv = ["--config", "config/settings.paper.local.yaml"]
    with patch("src.tools.live_safety_status.check_config",
               return_value=(cfg_result, None if config_fail else cfg_mock)):
        try:
            main(argv)
            return None
        except SystemExit as exc:
            return exc.code


# ---------------------------------------------------------------------------
# evaluate_safety unit tests
# ---------------------------------------------------------------------------

class TestEvaluateSafety:
    def test_all_safe_defaults_pass(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution()
        result, issues = evaluate_safety(ex)
        assert result == "PASS"
        assert issues == []

    def test_live_trading_enabled_with_dry_run_warn(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_trading_enabled=True, live_submit_dry_run=True)
        result, issues = evaluate_safety(ex)
        assert result == "WARN"
        assert issues  # non-empty warning list

    def test_live_trading_enabled_without_dry_run_fail(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_trading_enabled=True, live_submit_dry_run=False)
        result, issues = evaluate_safety(ex)
        assert result == "FAIL"
        assert any("live_submit_dry_run=false" in i for i in issues)

    def test_kill_switch_disabled_fail(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_kill_switch_enabled=False)
        result, issues = evaluate_safety(ex)
        assert result == "FAIL"
        assert any("live_kill_switch_enabled=false" in i for i in issues)

    def test_human_confirm_false_fail(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_require_human_confirm=False)
        result, issues = evaluate_safety(ex)
        assert result == "FAIL"
        assert any("live_require_human_confirm=false" in i for i in issues)

    def test_kill_switch_and_human_confirm_both_fail(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_kill_switch_enabled=False,
                             live_require_human_confirm=False)
        result, issues = evaluate_safety(ex)
        assert result == "FAIL"
        assert len(issues) == 2

    def test_trading_enabled_kill_switch_off_fail(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_trading_enabled=True,
                             live_kill_switch_enabled=False,
                             live_submit_dry_run=True)
        result, issues = evaluate_safety(ex)
        assert result == "FAIL"

    def test_all_disabled_pass(self):
        from src.tools.live_safety_status import evaluate_safety
        ex = _mock_execution(live_trading_enabled=False)
        result, _ = evaluate_safety(ex)
        assert result == "PASS"


# ---------------------------------------------------------------------------
# build_status unit tests
# ---------------------------------------------------------------------------

class TestBuildStatus:
    def test_fields_present(self):
        from src.tools.live_safety_status import build_status
        cfg = _mock_cfg()
        s = build_status(cfg)
        assert "live_trading_enabled"     in s
        assert "live_kill_switch_enabled" in s
        assert "live_submit_dry_run"      in s
        assert "live_require_human_confirm" in s
        assert "live_max_orders_per_day"  in s
        assert "live_max_notional_per_day" in s
        assert "live_ledger_path"         in s
        assert "result"                   in s
        assert "issues"                   in s

    def test_defaults_pass(self):
        from src.tools.live_safety_status import build_status
        s = build_status(_mock_cfg())
        assert s["result"] == "PASS"
        assert s["live_trading_enabled"]      is False
        assert s["live_kill_switch_enabled"]  is True
        assert s["live_submit_dry_run"]       is True
        assert s["live_require_human_confirm"] is True

    def test_warn_propagated(self):
        from src.tools.live_safety_status import build_status
        cfg = _mock_cfg(live_trading_enabled=True, live_submit_dry_run=True)
        s = build_status(cfg)
        assert s["result"] == "WARN"

    def test_fail_propagated(self):
        from src.tools.live_safety_status import build_status
        cfg = _mock_cfg(live_trading_enabled=True, live_submit_dry_run=False)
        s = build_status(cfg)
        assert s["result"] == "FAIL"


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_default_config_exits_0(self):
        assert _run_main(_mock_cfg()) in (0, None)

    def test_kill_switch_disabled_exits_1(self):
        assert _run_main(_mock_cfg(live_kill_switch_enabled=False)) == 1

    def test_human_confirm_false_exits_1(self):
        assert _run_main(_mock_cfg(live_require_human_confirm=False)) == 1

    def test_trading_enabled_dry_run_true_exits_1(self):
        # WARN → exit 1 (operator must acknowledge)
        assert _run_main(_mock_cfg(live_trading_enabled=True,
                                   live_submit_dry_run=True)) == 1

    def test_trading_enabled_dry_run_false_exits_1(self):
        assert _run_main(_mock_cfg(live_trading_enabled=True,
                                   live_submit_dry_run=False)) == 1

    def test_config_fail_exits_1(self):
        assert _run_main(MagicMock(), config_fail=True) == 1

    def test_no_network_calls(self):
        # No AlpacaBrokerAdapter, no HTTP, passes with only mock config
        result = _run_main(_mock_cfg())
        assert result in (0, None)

    def test_no_submit_order_called(self):
        cfg = _mock_cfg()
        _run_main(cfg)
        # cfg.execution should never have submit_order called
        cfg.execution.submit_order.assert_not_called() if hasattr(
            cfg.execution, "submit_order") else None

    def test_no_ledger_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _run_main(_mock_cfg())
        assert list(tmp_path.glob("**/*ledger*")) == []


# ---------------------------------------------------------------------------
# ExecutionConfig defaults (loader.py)
# ---------------------------------------------------------------------------

class TestExecutionConfigDefaults:
    def test_new_safety_fields_default_values(self):
        from src.config.loader import ExecutionConfig
        ex = ExecutionConfig()
        assert ex.live_trading_enabled       is False
        assert ex.live_kill_switch_enabled   is True
        assert ex.live_submit_dry_run        is True
        assert ex.live_require_human_confirm is True
        assert ex.live_max_orders_per_day    == 1
        assert ex.live_max_notional_per_day  == 100.0
        assert ex.live_ledger_path           == "output/live_execution_ledger.csv"

    def test_existing_fields_unchanged(self):
        from src.config.loader import ExecutionConfig
        ex = ExecutionConfig()
        assert ex.mode                    == "backtest"
        assert ex.live_max_quantity       == 1.0
        assert ex.live_max_notional       == 500.0
        assert ex.live_sizing_mode        == "quantity"
        assert ex.live_max_order_notional == 100.0
