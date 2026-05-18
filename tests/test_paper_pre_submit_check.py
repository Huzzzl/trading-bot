"""
tests/test_paper_pre_submit_check.py
--------------------------------------
Tests for src/tools/paper_pre_submit_check.py.

All tests run without Alpaca credentials, network access, or real orders.
AlpacaBrokerAdapter is never instantiated unless --verify-ledger is explicitly
set (and even then it is mocked).
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Config / ledger helpers
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


def _write_submit_config(
    tmp_path: Path,
    kill_switch: bool = False,
    require_market_hours: bool = True,
    max_orders: int = 3,
) -> Path:
    text = _BASE_YAML + textwrap.dedent(f"""\
        execution:
          mode: paper
          paper_trading_enabled: true
          paper_preview_only: false
          paper_selected_client_order_id: "BT-20240115100000-SPY"
          paper_order_quantity_override: 1.0
          paper_kill_switch_enabled: {"true" if kill_switch else "false"}
          paper_require_market_hours: {"true" if require_market_hours else "false"}
          paper_daily_max_orders: {max_orders}
    """)
    p = tmp_path / "settings.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _write_ledger(path: Path, rows: list[dict] | None = None) -> None:
    """Write a minimal ledger CSV directly (bypasses the no-op autouse patch)."""
    from src.execution.paper_ledger import _LEDGER_COLUMNS
    path.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LEDGER_COLUMNS)
        writer.writeheader()
        for row in (rows or []):
            full_row = {col: row.get(col, "") for col in _LEDGER_COLUMNS}
            writer.writerow(full_row)


def _ledger_row(
    client_order_id: str = "BT-001",
    flow: str = "buy_submit",
    submitted_at: str = "2024-01-15 10:00:00-05:00",
) -> dict:
    return {
        "run_id": "run1",
        "flow": flow,
        "client_order_id": client_order_id,
        "alpaca_order_id": "alp-001",
        "symbol": "SPY",
        "side": "buy",
        "quantity": "1.0",
        "status": "accepted",
        "submitted_at": submitted_at,
        "output_dir": "/output/run1",
        "notes": "",
    }


def _argv(tmp_path: Path, cfg_path: Path, ledger: Path | None = None,
          verify_ledger: bool = False, last: int | None = None) -> list[str]:
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    argv = [
        "--config", str(cfg_path),
        "--output-dir", str(out_dir),
    ]
    if ledger is not None:
        argv += ["--ledger", str(ledger)]
    if verify_ledger:
        argv.append("--verify-ledger")
    if last is not None:
        argv += ["--last", str(last)]
    return argv


# ---------------------------------------------------------------------------
# 1. check_mode_summary
# ---------------------------------------------------------------------------

class TestCheckModeSummary:
    def _exec(self, **kwargs):
        from src.config.loader import ExecutionConfig
        return ExecutionConfig(**kwargs)

    def _cfg(self, ex):
        class _C:
            execution = ex
        return _C()

    def test_preview_mode_passes(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True, paper_preview_only=True)
        result = check_mode_summary(self._cfg(ex))
        assert result["status"] == "PASS"
        assert "PREVIEW MODE" in result["detail"]

    def test_buy_submit_warns(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True,
                        paper_preview_only=False,
                        paper_selected_client_order_id="BT-001")
        result = check_mode_summary(self._cfg(ex))
        assert result["status"] == "WARN"
        assert "SUBMIT MODE DETECTED" in result["detail"]

    def test_buy_submit_detail_contains_coid(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True,
                        paper_preview_only=False,
                        paper_selected_client_order_id="BT-20240115100000-SPY")
        result = check_mode_summary(self._cfg(ex))
        assert "BT-20240115100000-SPY" in result["detail"]

    def test_close_submit_warns(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True,
                        paper_close_positions_enabled=True,
                        paper_close_preview_only=False,
                        paper_selected_close_client_order_id="BC-20240115-SPY-CLOSE")
        result = check_mode_summary(self._cfg(ex))
        assert result["status"] == "WARN"
        assert "SUBMIT MODE DETECTED" in result["detail"]

    def test_close_preview_passes(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True,
                        paper_close_positions_enabled=True,
                        paper_close_preview_only=True)
        result = check_mode_summary(self._cfg(ex))
        assert result["status"] == "PASS"

    def test_label_is_mode_summary(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True, paper_preview_only=True)
        result = check_mode_summary(self._cfg(ex))
        assert result["label"] == "mode_summary"

    def test_no_order_will_be_submitted_in_preview(self):
        from src.tools.paper_pre_submit_check import check_mode_summary
        ex = self._exec(mode="paper", paper_trading_enabled=True, paper_preview_only=True)
        result = check_mode_summary(self._cfg(ex))
        assert "no order will be submitted" in result["detail"].lower()


# ---------------------------------------------------------------------------
# 2. check_verify_ledger
# ---------------------------------------------------------------------------

class TestCheckVerifyLedger:
    def test_missing_credentials_fails(self, tmp_path):
        from src.tools.paper_pre_submit_check import check_verify_ledger
        ledger_path = tmp_path / "ledger.csv"
        _write_ledger(ledger_path, [_ledger_row()])
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ALPACA_API_KEY", None)
            os.environ.pop("ALPACA_SECRET_KEY", None)
            result = check_verify_ledger(ledger_path)
        assert result["status"] == "FAIL"
        assert "credentials" in result["detail"].lower()

    def test_missing_ledger_fails(self, tmp_path):
        from src.tools.paper_pre_submit_check import check_verify_ledger
        with patch("src.tools.paper_ledger_verify._resolve_credentials",
                   return_value=("key", "secret")):
            result = check_verify_ledger(tmp_path / "nonexistent.csv")
        assert result["status"] == "FAIL"
        assert "ledger not found" in result["detail"]

    def test_all_match_passes(self, tmp_path):
        from src.tools.paper_pre_submit_check import check_verify_ledger
        ledger_path = tmp_path / "ledger.csv"
        _write_ledger(ledger_path, [_ledger_row()])
        mock_rows = [{"status_match": True, "error": ""}]
        with patch("src.tools.paper_ledger_verify._resolve_credentials",
                   return_value=("key", "secret")), \
             patch("src.tools.paper_ledger_verify._make_client",
                   return_value=MagicMock()), \
             patch("src.tools.paper_ledger_verify.verify_ledger",
                   return_value=mock_rows):
            result = check_verify_ledger(ledger_path)
        assert result["status"] == "PASS"

    def test_mismatch_warns(self, tmp_path):
        from src.tools.paper_pre_submit_check import check_verify_ledger
        ledger_path = tmp_path / "ledger.csv"
        _write_ledger(ledger_path, [_ledger_row()])
        mock_rows = [{"status_match": False, "error": ""}]
        with patch("src.tools.paper_ledger_verify._resolve_credentials",
                   return_value=("key", "secret")), \
             patch("src.tools.paper_ledger_verify._make_client",
                   return_value=MagicMock()), \
             patch("src.tools.paper_ledger_verify.verify_ledger",
                   return_value=mock_rows):
            result = check_verify_ledger(ledger_path)
        assert result["status"] == "WARN"

    def test_label_is_verify_ledger(self, tmp_path):
        from src.tools.paper_pre_submit_check import check_verify_ledger
        with patch("src.tools.paper_ledger_verify._resolve_credentials",
                   return_value=("key", "secret")), \
             patch("src.tools.paper_ledger_verify._make_client",
                   return_value=MagicMock()), \
             patch("src.tools.paper_ledger_verify.verify_ledger",
                   return_value=[]):
            ledger_path = tmp_path / "ledger.csv"
            _write_ledger(ledger_path, [])
            result = check_verify_ledger(ledger_path)
        assert result["label"] == "verify_ledger"


# ---------------------------------------------------------------------------
# 3. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_preview_mode_exits_0(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        # Preview mode with all guards passed → exit 0
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit as exc:
            assert exc.code in (0, None), f"Expected 0, got {exc.code}"

    def test_submit_mode_exits_1_with_warn(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_submit_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        with pytest.raises(SystemExit) as exc_info:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        assert exc_info.value.code == 1

    def test_submit_mode_output_contains_submit_warning(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_submit_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "SUBMIT MODE DETECTED" in out

    def test_preview_mode_output_contains_preview_message(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "PREVIEW MODE" in out

    def test_kill_switch_submit_mode_exits_1_fail(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_submit_config(tmp_path, kill_switch=True)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        with pytest.raises(SystemExit) as exc_info:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_kill_switch_submit_output_contains_kill_switch(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_submit_config(tmp_path, kill_switch=True)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "kill switch" in out.lower()

    def test_missing_ledger_exits_1(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(_argv(tmp_path, cfg_path, ledger=tmp_path / "nonexistent.csv"))
        assert exc_info.value.code == 1

    def test_daily_limit_reached_exits_1(self, tmp_path):
        """When today's buy orders already hit the buy cap, submit_readiness → WARN → exit 1."""
        from src.tools.paper_pre_submit_check import main
        # max_orders=1, ledger has 1 buy already today
        cfg_path = _write_submit_config(tmp_path, max_orders=1)
        ledger = tmp_path / "ledger.csv"
        today_ts = pd.Timestamp.now(tz="America/New_York").strftime(
            "%Y-%m-%d %H:%M:%S%z"
        )
        # Use a patched compute_daily_usage to return usage at the limit
        from src.tools.paper_pre_submit_check import main as _main
        with patch("src.execution.paper_daily_limits.compute_daily_usage",
                   return_value={"total_orders": 1, "buy_orders": 1, "close_orders": 0}):
            _write_ledger(ledger, [_ledger_row()])
            with pytest.raises(SystemExit) as exc_info:
                main(_argv(tmp_path, cfg_path, ledger=ledger))
        assert exc_info.value.code == 1

    def test_no_broker_instantiated_by_default(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        with patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker:
            try:
                main(_argv(tmp_path, cfg_path, ledger=ledger))
            except SystemExit:
                pass
        MockBroker.assert_not_called()

    def test_no_credentials_read_by_default(self, tmp_path):
        for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            os.environ.pop(k, None)
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        # Must not raise due to missing credentials
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit as exc:
            assert exc.code in (0, 1, None)

    def test_verify_ledger_not_called_without_flag(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        with patch("src.tools.paper_pre_submit_check.check_verify_ledger") as mock_verify:
            try:
                main(_argv(tmp_path, cfg_path, ledger=ledger))
            except SystemExit:
                pass
        mock_verify.assert_not_called()

    def test_verify_ledger_called_with_flag(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        mock_result = {"label": "verify_ledger", "status": "PASS", "detail": "ok"}
        with patch("src.tools.paper_pre_submit_check.check_verify_ledger",
                   return_value=mock_result) as mock_verify:
            try:
                main(_argv(tmp_path, cfg_path, ledger=ledger, verify_ledger=True))
            except SystemExit:
                pass
        mock_verify.assert_called_once_with(ledger)

    def test_verify_ledger_fail_exits_1(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        fail_result = {"label": "verify_ledger", "status": "FAIL",
                       "detail": "credentials missing"}
        with patch("src.tools.paper_pre_submit_check.check_verify_ledger",
                   return_value=fail_result):
            with pytest.raises(SystemExit) as exc_info:
                main(_argv(tmp_path, cfg_path, ledger=ledger, verify_ledger=True))
        assert exc_info.value.code == 1

    def test_bad_config_exits_1(self, tmp_path):
        from src.tools.paper_pre_submit_check import main
        bad = tmp_path / "bad.yaml"
        bad.write_text("invalid: yaml: [[[", encoding="utf-8")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        argv = ["--config", str(bad), "--output-dir", str(out_dir)]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_output_contains_safety_config(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "safety_config" in out

    def test_output_contains_daily_usage(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "daily_usage" in out

    def test_output_contains_submit_readiness(self, tmp_path, capsys):
        from src.tools.paper_pre_submit_check import main
        cfg_path = _write_config(tmp_path)
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        try:
            main(_argv(tmp_path, cfg_path, ledger=ledger))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "submit_readiness" in out

    def test_ledger_path_from_config_when_no_flag(self, tmp_path):
        """When --ledger is omitted, ledger path comes from config."""
        from src.tools.paper_pre_submit_check import main
        ledger_path = tmp_path / "cfg_ledger.csv"
        cfg_yaml = _BASE_YAML + textwrap.dedent(f"""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
              paper_ledger_path: "{ledger_path.as_posix()}"
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
