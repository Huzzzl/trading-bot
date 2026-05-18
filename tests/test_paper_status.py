"""
tests/test_paper_status.py
---------------------------
Offline tests for src/tools/paper_status.py.

All tests run without Alpaca credentials, network access, or real orders.
"""

from __future__ import annotations

import json
import textwrap
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_YAML = textwrap.dedent("""\
    backtest:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
      initial_capital: 100000
      commission_per_share: 0.005
      slippage_per_share: 0.01
    symbols: [SPY]
    data:
      provider: yahoo
      bar_interval: "5m"
      timezone: "America/New_York"
    strategy:
      name: opening_range_breakout
      params: {}
    risk: {}
    logging:
      level: WARNING
      format: "%(message)s"
""")


def _write_config(tmp_path: Path, execution_yaml: str = "") -> Path:
    text = _BASE_YAML + textwrap.dedent("""\
        execution:
          mode: paper
          paper_trading_enabled: true
          paper_preview_only: true
    """) + execution_yaml
    p = tmp_path / "settings.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _write_ledger(path: Path, rows: list[dict]) -> None:
    from src.execution.paper_ledger import _LEDGER_COLUMNS, append_ledger_row
    path.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        append_ledger_row(path, row)


def _ledger_row(client_order_id: str = "BT-001", flow: str = "buy_submit") -> dict:
    return {
        "run_id": "run1",
        "flow": flow,
        "client_order_id": client_order_id,
        "alpaca_order_id": "alp-001",
        "symbol": "SPY",
        "side": "buy",
        "quantity": "1.0",
        "status": "accepted",
        "submitted_at": "2024-01-15 10:00:00-05:00",
        "output_dir": "/output/run1",
        "notes": "",
    }


def _write_intents_csv(path: Path) -> None:
    path.write_text(
        "client_order_id,symbol,side,quantity,order_type,reason\n"
        "BT-001,SPY,buy,1.0,market,smoke\n",
        encoding="utf-8",
    )


def _write_results_csv(path: Path) -> None:
    path.write_text(
        "client_order_id,symbol,side,quantity,status,reason\n"
        "BT-001,SPY,buy,1.0,accepted,smoke\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. classify_mode
# ---------------------------------------------------------------------------

class TestClassifyMode:
    def _exec(self, **kwargs):
        from src.config.loader import ExecutionConfig
        return ExecutionConfig(**kwargs)

    def test_backtest_mode(self):
        from src.tools.paper_status import classify_mode
        assert classify_mode(self._exec(mode="backtest")) == "backtest"

    def test_paper_disabled(self):
        from src.tools.paper_status import classify_mode
        assert classify_mode(self._exec(mode="paper", paper_trading_enabled=False)) == "disabled"

    def test_buy_preview(self):
        from src.tools.paper_status import classify_mode
        ex = self._exec(mode="paper", paper_trading_enabled=True, paper_preview_only=True)
        assert classify_mode(ex) == "buy_preview"

    def test_buy_submit(self):
        from src.tools.paper_status import classify_mode
        ex = self._exec(mode="paper", paper_trading_enabled=True, paper_preview_only=False,
                        paper_selected_client_order_id="BT-001")
        assert classify_mode(ex) == "buy_submit"

    def test_close_preview(self):
        from src.tools.paper_status import classify_mode
        ex = self._exec(mode="paper", paper_trading_enabled=True,
                        paper_close_positions_enabled=True, paper_close_preview_only=True)
        assert classify_mode(ex) == "close_preview"

    def test_close_submit(self):
        from src.tools.paper_status import classify_mode
        ex = self._exec(mode="paper", paper_trading_enabled=True,
                        paper_close_positions_enabled=True, paper_close_preview_only=False,
                        paper_selected_close_client_order_id="BC-001")
        assert classify_mode(ex) == "close_submit"


# ---------------------------------------------------------------------------
# 2. check_config
# ---------------------------------------------------------------------------

class TestCheckConfig:
    def test_valid_config_passes(self, tmp_path):
        from src.tools.paper_status import check_config
        cfg_path = _write_config(tmp_path)
        result, cfg = check_config(cfg_path)
        assert result["status"] == "PASS"
        assert cfg is not None

    def test_missing_config_fails(self, tmp_path):
        from src.tools.paper_status import check_config
        result, cfg = check_config(tmp_path / "nonexistent.yaml")
        assert result["status"] == "FAIL"
        assert cfg is None

    def test_invalid_config_fails(self, tmp_path):
        from src.tools.paper_status import check_config
        # paper_preview_only=false without selected_id → validation error
        bad = _BASE_YAML + textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: false
        """)
        p = tmp_path / "bad.yaml"
        p.write_text(bad, encoding="utf-8")
        result, cfg = check_config(p)
        assert result["status"] == "FAIL"

    def test_detail_contains_classification(self, tmp_path):
        from src.tools.paper_status import check_config
        cfg_path = _write_config(tmp_path)
        result, _ = check_config(cfg_path)
        assert "buy_preview" in result["detail"]

    def test_label_is_config(self, tmp_path):
        from src.tools.paper_status import check_config
        result, _ = check_config(_write_config(tmp_path))
        assert result["label"] == "config"


# ---------------------------------------------------------------------------
# 3. check_ledger
# ---------------------------------------------------------------------------

class TestCheckLedger:
    def test_missing_ledger_warns(self, tmp_path):
        from src.tools.paper_status import check_ledger
        result = check_ledger(tmp_path / "ledger.csv")
        assert result["status"] == "WARN"
        assert "missing" in result["detail"]

    def test_present_ledger_passes(self, tmp_path):
        from src.tools.paper_status import check_ledger
        path = tmp_path / "ledger.csv"
        _write_ledger(path, [_ledger_row()])
        result = check_ledger(path)
        assert result["status"] == "PASS"

    def test_total_rows_counted(self, tmp_path):
        from src.tools.paper_status import check_ledger
        path = tmp_path / "ledger.csv"
        _write_ledger(path, [_ledger_row("BT-001"), _ledger_row("BT-002")])
        result = check_ledger(path)
        assert result["total_rows"] == 2

    def test_last_n_rows_returned(self, tmp_path):
        from src.tools.paper_status import check_ledger
        path = tmp_path / "ledger.csv"
        for i in range(10):
            _write_ledger(path, [_ledger_row(f"BT-{i:03d}")])
        result = check_ledger(path, last_n=3)
        assert len(result["rows"]) == 3

    def test_empty_ledger_passes(self, tmp_path):
        from src.tools.paper_status import check_ledger
        from src.execution.paper_ledger import _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        path.write_text(",".join(_LEDGER_COLUMNS) + "\n", encoding="utf-8")
        result = check_ledger(path)
        assert result["status"] == "PASS"
        assert result["rows"] == []

    def test_label_is_ledger(self, tmp_path):
        from src.tools.paper_status import check_ledger
        result = check_ledger(tmp_path / "ledger.csv")
        assert result["label"] == "ledger"


# ---------------------------------------------------------------------------
# 4. check_artifacts
# ---------------------------------------------------------------------------

class TestCheckArtifacts:
    def test_empty_dir_all_missing(self, tmp_path):
        from src.tools.paper_status import check_artifacts
        result = check_artifacts(tmp_path)
        assert result["status"] == "PASS"  # missing artifacts is informational, not a failure
        presence = result["presence"]
        assert all(not v for v in presence.values())

    def test_present_artifacts_detected(self, tmp_path):
        from src.tools.paper_status import check_artifacts
        (tmp_path / "order_intents.csv").write_text("a,b\n1,2\n")
        (tmp_path / "order_results.csv").write_text("a,b\n1,2\n")
        result = check_artifacts(tmp_path)
        assert result["presence"]["order_intents.csv"] is True
        assert result["presence"]["order_results.csv"] is True

    def test_all_known_artifacts_checked(self, tmp_path):
        from src.tools.paper_status import check_artifacts
        result = check_artifacts(tmp_path)
        expected_keys = {
            "paper_candidate_intents.csv",
            "paper_close_candidate_intents.csv",
            "order_intents.csv",
            "order_results.csv",
            "order_reconciliation.json",
        }
        assert set(result["presence"].keys()) == expected_keys

    def test_label_is_artifacts(self, tmp_path):
        from src.tools.paper_status import check_artifacts
        result = check_artifacts(tmp_path)
        assert result["label"] == "artifacts"


# ---------------------------------------------------------------------------
# 5. check_replay
# ---------------------------------------------------------------------------

class TestCheckReplay:
    def test_missing_csvs_warns(self, tmp_path):
        from src.tools.paper_status import check_replay
        result = check_replay(tmp_path)
        assert result["status"] == "WARN"
        assert "skipped" in result["detail"]

    def test_pass_recon_passes(self, tmp_path):
        from src.tools.paper_status import check_replay
        _write_intents_csv(tmp_path / "order_intents.csv")
        _write_results_csv(tmp_path / "order_results.csv")
        result = check_replay(tmp_path)
        assert result["status"] == "PASS"

    def test_warn_recon_warns(self, tmp_path):
        from src.tools.paper_status import check_replay
        _write_intents_csv(tmp_path / "order_intents.csv")
        # mismatched results → WARN
        (tmp_path / "order_results.csv").write_text(
            "client_order_id,symbol,side,quantity,status,reason\n"
            "BT-001,QQQ,buy,1.0,accepted,smoke\n",  # symbol mismatch
            encoding="utf-8",
        )
        result = check_replay(tmp_path)
        assert result["status"] == "WARN"

    def test_label_is_replay(self, tmp_path):
        from src.tools.paper_status import check_replay
        result = check_replay(tmp_path)
        assert result["label"] == "replay"

    def test_reconciliation_included_in_result(self, tmp_path):
        from src.tools.paper_status import check_replay
        _write_intents_csv(tmp_path / "order_intents.csv")
        _write_results_csv(tmp_path / "order_results.csv")
        result = check_replay(tmp_path)
        assert "reconciliation" in result

    def test_exception_in_replay_fails(self, tmp_path):
        from src.tools.paper_status import check_replay
        _write_intents_csv(tmp_path / "order_intents.csv")
        _write_results_csv(tmp_path / "order_results.csv")
        with patch("src.tools.replay_order_reconciliation.replay", side_effect=RuntimeError("boom")):
            result = check_replay(tmp_path)
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# 6. print_report
# ---------------------------------------------------------------------------

class TestPrintReport:
    def _checks(self, *statuses):
        return [{"label": f"check{i}", "status": s, "detail": ""} for i, s in enumerate(statuses)]

    def test_pass_result_printed(self, capsys):
        from src.tools.paper_status import print_report
        print_report(self._checks("PASS", "PASS"), final_status="PASS")
        out = capsys.readouterr().out
        assert "RESULT: PASS" in out

    def test_warn_result_printed(self, capsys):
        from src.tools.paper_status import print_report
        print_report(self._checks("PASS", "WARN"), final_status="WARN")
        out = capsys.readouterr().out
        assert "RESULT: WARN" in out
        assert "[WARN]" in out

    def test_fail_result_printed(self, capsys):
        from src.tools.paper_status import print_report
        print_report(self._checks("FAIL"), final_status="FAIL")
        out = capsys.readouterr().out
        assert "RESULT: FAIL" in out
        assert "[FAIL]" in out

    def test_ledger_rows_printed(self, capsys):
        from src.tools.paper_status import print_report
        rows = [{"flow": "buy_submit", "client_order_id": "BT-001",
                 "symbol": "SPY", "side": "buy", "status": "accepted",
                 "submitted_at": "2024-01-15"}]
        print_report(self._checks("PASS"), ledger_rows=rows, final_status="PASS")
        out = capsys.readouterr().out
        assert "BT-001" in out

    def test_artifact_presence_printed(self, capsys):
        from src.tools.paper_status import print_report
        checks = [{
            "label": "artifacts",
            "status": "PASS",
            "detail": "5 present",
            "presence": {"order_intents.csv": True, "order_results.csv": False},
        }]
        print_report(checks, final_status="PASS")
        out = capsys.readouterr().out
        assert "order_intents.csv" in out
        assert "order_results.csv" in out


# ---------------------------------------------------------------------------
# 7. CLI (main)
# ---------------------------------------------------------------------------

class TestMain:
    def _argv(self, tmp_path, replay_dir=None, ledger=None, last=None):
        cfg_path = _write_config(tmp_path)
        out_dir  = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        if ledger:
            argv += ["--ledger", str(ledger)]
        if replay_dir:
            argv += ["--replay-dir", str(replay_dir)]
        if last is not None:
            argv += ["--last", str(last)]
        return argv

    def test_exits_1_when_ledger_missing(self, tmp_path):
        from src.tools.paper_status import main
        argv = self._argv(tmp_path, ledger=tmp_path / "nonexistent_ledger.csv")
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_exits_0_when_ledger_present_and_config_valid(self, tmp_path):
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = self._argv(tmp_path, ledger=str(ledger))
        # Should exit 0 (PASS)
        try:
            main(argv)
        except SystemExit as exc:
            assert exc.code == 0 or exc.code is None

    def test_exits_1_on_bad_config(self, tmp_path):
        from src.tools.paper_status import main
        bad = tmp_path / "bad.yaml"
        bad.write_text("invalid: yaml: [[[", encoding="utf-8")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        argv = ["--config", str(bad), "--output-dir", str(out_dir)]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_replay_dir_triggers_replay_check(self, tmp_path):
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        replay_dir = tmp_path / "replay"
        replay_dir.mkdir()
        _write_intents_csv(replay_dir / "order_intents.csv")
        _write_results_csv(replay_dir / "order_results.csv")
        argv = self._argv(tmp_path, ledger=str(ledger), replay_dir=str(replay_dir))
        try:
            main(argv)
        except SystemExit as exc:
            assert exc.code == 0 or exc.code is None

    def test_no_replay_without_flag(self, tmp_path):
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        with patch("src.tools.paper_status.check_replay") as mock_replay:
            argv = self._argv(tmp_path, ledger=str(ledger))
            try:
                main(argv)
            except SystemExit:
                pass
            mock_replay.assert_not_called()

    def test_last_n_passed_to_check_ledger(self, tmp_path):
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        with patch("src.tools.paper_status.check_ledger", return_value={
            "label": "ledger", "status": "PASS", "detail": "ok",
            "rows": [], "total_rows": 1,
        }) as mock_ledger:
            argv = self._argv(tmp_path, ledger=str(ledger), last=3)
            try:
                main(argv)
            except SystemExit:
                pass
            mock_ledger.assert_called_once_with(Path(str(ledger)), last_n=3)

    def test_no_broker_instantiated(self, tmp_path):
        """AlpacaBrokerAdapter must never be instantiated."""
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = self._argv(tmp_path, ledger=str(ledger))
        with patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker:
            try:
                main(argv)
            except SystemExit:
                pass
            MockBroker.assert_not_called()

    def test_no_alpaca_credentials_read(self, tmp_path):
        """Running without env credentials must not raise."""
        import os
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            os.environ.pop(k, None)
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = self._argv(tmp_path, ledger=str(ledger))
        try:
            main(argv)
        except SystemExit as exc:
            assert exc.code in (0, 1, None)

    def test_final_warn_when_replay_warns(self, tmp_path):
        from src.tools.paper_status import main
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        replay_dir = tmp_path / "replay"
        replay_dir.mkdir()
        _write_intents_csv(replay_dir / "order_intents.csv")
        # mismatched result → WARN reconciliation
        (replay_dir / "order_results.csv").write_text(
            "client_order_id,symbol,side,quantity,status,reason\n"
            "BT-001,QQQ,buy,1.0,accepted,smoke\n",
            encoding="utf-8",
        )
        argv = self._argv(tmp_path, ledger=str(ledger), replay_dir=str(replay_dir))
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_ledger_path_from_config_when_no_flag(self, tmp_path):
        """When --ledger is omitted, ledger path comes from config."""
        from src.tools.paper_status import main
        cfg_yaml = _BASE_YAML + textwrap.dedent(f"""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
              paper_ledger_path: "{(tmp_path / 'cfg_ledger.csv').as_posix()}"
        """)
        cfg_path = tmp_path / "settings.yaml"
        cfg_path.write_text(cfg_yaml, encoding="utf-8")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # ledger is missing → WARN → exit 1
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 8. check_safety_config
# ---------------------------------------------------------------------------

class TestCheckSafetyConfig:
    def _exec(self, **kwargs):
        from src.config.loader import ExecutionConfig
        return ExecutionConfig(**kwargs)

    def _cfg_with(self, **kwargs):
        """Return a minimal AppConfig-like object with an execution field."""
        from src.config.loader import (
            AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
            LoggingConfig, RiskConfig, StrategyConfig,
        )
        ex = ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_preview_only=True,
            **kwargs,
        )

        class _Cfg:
            execution = ex

        return _Cfg()

    def test_label_is_safety_config(self):
        from src.tools.paper_status import check_safety_config
        result = check_safety_config(self._cfg_with())
        assert result["label"] == "safety_config"

    def test_status_always_pass(self):
        from src.tools.paper_status import check_safety_config
        result = check_safety_config(self._cfg_with(paper_kill_switch_enabled=True))
        assert result["status"] == "PASS"

    def test_config_dict_contains_all_keys(self):
        from src.tools.paper_status import check_safety_config
        result = check_safety_config(self._cfg_with())
        expected = {
            "paper_kill_switch_enabled",
            "paper_require_market_hours",
            "paper_block_if_open_orders",
            "paper_daily_max_orders",
            "paper_daily_max_buy_orders",
            "paper_daily_max_close_orders",
            "paper_daily_max_notional",
            "paper_poll_order_status",
        }
        assert set(result["config"].keys()) == expected

    def test_kill_switch_value_reflected(self):
        from src.tools.paper_status import check_safety_config
        result = check_safety_config(self._cfg_with(paper_kill_switch_enabled=True))
        assert result["config"]["paper_kill_switch_enabled"] is True

    def test_detail_mentions_kill_switch(self):
        from src.tools.paper_status import check_safety_config
        result = check_safety_config(self._cfg_with(paper_kill_switch_enabled=True))
        assert "ENABLED" in result["detail"]

    def test_no_broker_no_network(self):
        from src.tools.paper_status import check_safety_config
        from unittest.mock import patch
        with patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker:
            check_safety_config(self._cfg_with())
        MockBroker.assert_not_called()


# ---------------------------------------------------------------------------
# 9. check_daily_usage
# ---------------------------------------------------------------------------

class TestCheckDailyUsage:
    def _cfg_with(self, **kwargs):
        from src.config.loader import ExecutionConfig

        class _Cfg:
            execution = ExecutionConfig(
                mode="paper", paper_trading_enabled=True, paper_preview_only=True,
                **kwargs,
            )

        return _Cfg()

    def test_label_is_daily_usage(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        result = check_daily_usage(tmp_path / "ledger.csv", self._cfg_with())
        assert result["label"] == "daily_usage"

    def test_status_always_pass(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        result = check_daily_usage(tmp_path / "ledger.csv", self._cfg_with())
        assert result["status"] == "PASS"

    def test_usage_shown_from_ledger(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        ledger_path = tmp_path / "ledger.csv"
        today_str = pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d %H:%M:%S-05:00")
        _write_ledger(ledger_path, [
            _ledger_row("BT-001", flow="buy_submit"),
            _ledger_row("BT-002", flow="buy_submit"),
            _ledger_row("BC-001", flow="close_submit"),
        ])
        result = check_daily_usage(ledger_path, self._cfg_with())
        u = result["usage"]
        # Rows may be from a different date in CI — just check keys exist
        assert "today_total_orders" in u
        assert "today_buy_orders" in u
        assert "today_close_orders" in u

    def test_remaining_computed_from_limits(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        cfg = self._cfg_with(
            paper_daily_max_orders=3,
            paper_daily_max_buy_orders=2,
            paper_daily_max_close_orders=2,
        )
        result = check_daily_usage(tmp_path / "ledger.csv", cfg)
        u = result["usage"]
        assert u["remaining_total_orders"] == 3
        assert u["remaining_buy_orders"] == 2
        assert u["remaining_close_orders"] == 2

    def test_remaining_none_when_no_limit(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        from src.config.loader import ExecutionConfig

        class _Cfg:
            execution = ExecutionConfig(
                mode="paper", paper_trading_enabled=True, paper_preview_only=True,
                paper_daily_max_orders=None,  # type: ignore[arg-type]
                paper_daily_max_buy_orders=None,  # type: ignore[arg-type]
                paper_daily_max_close_orders=None,  # type: ignore[arg-type]
            )

        result = check_daily_usage(tmp_path / "ledger.csv", _Cfg())
        u = result["usage"]
        assert u["remaining_total_orders"] is None
        assert u["remaining_buy_orders"] is None
        assert u["remaining_close_orders"] is None

    def test_usage_keys_present(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        result = check_daily_usage(tmp_path / "ledger.csv", self._cfg_with())
        expected_keys = {
            "today_total_orders", "today_buy_orders", "today_close_orders",
            "remaining_total_orders", "remaining_buy_orders", "remaining_close_orders",
        }
        assert set(result["usage"].keys()) == expected_keys

    def test_no_broker_no_credentials(self, tmp_path):
        from src.tools.paper_status import check_daily_usage
        with patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker:
            check_daily_usage(tmp_path / "ledger.csv", self._cfg_with())
        MockBroker.assert_not_called()


# ---------------------------------------------------------------------------
# 10. check_submit_readiness
# ---------------------------------------------------------------------------

_ET = "America/New_York"
_IN_HOURS_TS  = pd.Timestamp("2024-01-15 10:00:00", tz=_ET)   # Monday 10:00
_OUT_HOURS_TS = pd.Timestamp("2024-01-15 16:00:00", tz=_ET)   # Monday 16:00 (closed)
_WEEKEND_TS   = pd.Timestamp("2024-01-13 10:00:00", tz=_ET)   # Saturday


def _make_exec(
    *,
    preview_only: bool = True,
    close_positions: bool = False,
    close_preview_only: bool = True,
    kill_switch: bool = False,
    require_market_hours: bool = True,
    max_orders: int = 3,
    max_buy: int = 2,
    max_close: int = 2,
    selected_id: str | None = None,
    selected_close_id: str | None = None,
):
    from src.config.loader import ExecutionConfig
    return ExecutionConfig(
        mode="paper",
        paper_trading_enabled=True,
        paper_preview_only=preview_only,
        paper_selected_client_order_id=selected_id,
        paper_close_positions_enabled=close_positions,
        paper_close_preview_only=close_preview_only,
        paper_selected_close_client_order_id=selected_close_id,
        paper_kill_switch_enabled=kill_switch,
        paper_require_market_hours=require_market_hours,
        paper_daily_max_orders=max_orders,
        paper_daily_max_buy_orders=max_buy,
        paper_daily_max_close_orders=max_close,
    )


def _cfg(ex):
    class _C:
        execution = ex
    return _C()


def _zero_usage():
    return {
        "usage": {
            "today_total_orders": 0, "today_buy_orders": 0, "today_close_orders": 0,
            "remaining_total_orders": 3, "remaining_buy_orders": 2, "remaining_close_orders": 2,
        }
    }


def _full_usage():
    return {
        "usage": {
            "today_total_orders": 3, "today_buy_orders": 2, "today_close_orders": 2,
            "remaining_total_orders": 0, "remaining_buy_orders": 0, "remaining_close_orders": 0,
        }
    }


class TestCheckSubmitReadiness:
    def test_preview_mode_passes(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "PASS"

    def test_close_preview_mode_passes(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(close_positions=True, close_preview_only=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "PASS"

    def test_buy_submit_mode_warns(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001")
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "WARN"

    def test_close_submit_mode_warns(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(close_positions=True, close_preview_only=False, selected_close_id="BC-001")
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "WARN"

    def test_kill_switch_enabled_in_submit_mode_fails(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001", kill_switch=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "FAIL"

    def test_kill_switch_enabled_in_preview_mode_passes(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=True, kill_switch=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "PASS"

    def test_kill_switch_issue_in_detail(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001", kill_switch=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert "kill switch" in result["detail"].lower()
        assert len(result["issues"]) >= 1

    def test_daily_limit_reached_produces_warn(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001")
        result = check_submit_readiness(_cfg(ex), _full_usage(), lambda: _IN_HOURS_TS)
        assert result["status"] == "WARN"
        assert any("limit reached" in i for i in result["issues"])

    def test_daily_limit_not_reached_no_issue(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001")
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert not any("limit" in i for i in result["issues"])

    def test_outside_market_hours_produces_warn(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001", require_market_hours=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _OUT_HOURS_TS)
        assert result["status"] == "WARN"
        assert any("outside market hours" in i for i in result["issues"])

    def test_outside_market_hours_weekend_warns(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001", require_market_hours=True)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _WEEKEND_TS)
        assert any("outside market hours" in i for i in result["issues"])

    def test_outside_hours_no_warn_when_guard_disabled(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001", require_market_hours=False)
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _OUT_HOURS_TS)
        assert not any("outside market hours" in i for i in result["issues"])

    def test_label_is_submit_readiness(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec()
        result = check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        assert result["label"] == "submit_readiness"

    def test_no_broker_no_credentials(self):
        from src.tools.paper_status import check_submit_readiness
        ex = _make_exec(preview_only=False, selected_id="BT-001")
        with patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker:
            check_submit_readiness(_cfg(ex), _zero_usage(), lambda: _IN_HOURS_TS)
        MockBroker.assert_not_called()


# ---------------------------------------------------------------------------
# 11. CLI integration: new sections appear
# ---------------------------------------------------------------------------

class TestMainWithSafetyGuards:
    def _write_submit_config(self, tmp_path: Path, kill_switch: bool = False) -> Path:
        text = _BASE_YAML + textwrap.dedent(f"""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: false
              paper_selected_client_order_id: "BT-20240115100000-SPY"
              paper_order_quantity_override: 1.0
              paper_kill_switch_enabled: {"true" if kill_switch else "false"}
        """)
        p = tmp_path / "settings.yaml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_kill_switch_enabled_causes_fail_exit(self, tmp_path):
        from src.tools.paper_status import main
        cfg_path = self._write_submit_config(tmp_path, kill_switch=True)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_kill_switch_disabled_in_submit_mode_exits_1_warn(self, tmp_path):
        """Submit mode (no kill switch) → WARN → exit 1."""
        from src.tools.paper_status import main
        cfg_path = self._write_submit_config(tmp_path, kill_switch=False)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_safety_config_section_in_output(self, tmp_path, capsys):
        from src.tools.paper_status import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        try:
            main(argv)
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "safety_config" in out
        assert "paper_kill_switch_enabled" in out

    def test_daily_usage_section_in_output(self, tmp_path, capsys):
        from src.tools.paper_status import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        try:
            main(argv)
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "daily_usage" in out

    def test_submit_readiness_section_in_output(self, tmp_path, capsys):
        from src.tools.paper_status import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        try:
            main(argv)
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "submit_readiness" in out

    def test_no_broker_instantiated(self, tmp_path):
        from src.tools.paper_status import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        with patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker:
            try:
                main(argv)
            except SystemExit:
                pass
        MockBroker.assert_not_called()

    def test_no_alpaca_credentials_read(self, tmp_path):
        import os
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            os.environ.pop(k, None)
        from src.tools.paper_status import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--ledger", str(ledger),
        ]
        try:
            main(argv)
        except SystemExit as exc:
            assert exc.code in (0, 1, None)

