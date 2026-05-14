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
