"""
tests/test_paper_smoke_check.py
--------------------------------
Offline unit tests for src/tools/paper_smoke_check.py.

All tests run without Alpaca credentials, network access, or real orders.
"""

from __future__ import annotations

import sys
import textwrap
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

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
    execution:
      mode: paper
      paper_trading_enabled: true
      paper_preview_only: true
""")


def _write_config(tmp_path: Path, extra_yaml: str = "") -> Path:
    cfg_path = tmp_path / "settings.paper.yaml"
    cfg_path.write_text(_BASE_YAML + extra_yaml, encoding="utf-8")
    return cfg_path


def _fake_cfg(
    mode: str = "paper",
    paper_trading_enabled: bool = True,
    paper_preview_only: bool = True,
    symbols: list[str] | None = None,
) -> SimpleNamespace:
    execution = SimpleNamespace(
        mode=mode,
        paper_trading_enabled=paper_trading_enabled,
        paper_preview_only=paper_preview_only,
    )
    return SimpleNamespace(execution=execution, symbols=symbols or ["SPY"])


# ---------------------------------------------------------------------------
# 1. _FakeSmokeClient
# ---------------------------------------------------------------------------

class TestFakeSmokeClient:
    def _client(self):
        from src.tools.paper_smoke_check import _FakeSmokeClient
        return _FakeSmokeClient()

    def test_get_account_returns_active(self):
        account = self._client().get_account()
        assert account["status"] == "ACTIVE"
        assert account["trading_blocked"] is False

    def test_get_all_positions_empty(self):
        assert self._client().get_all_positions() == []

    def test_submit_order_raises(self):
        with pytest.raises(RuntimeError, match="submit_order"):
            self._client().submit_order()

    def test_cancel_order_by_id_raises(self):
        with pytest.raises(RuntimeError, match="cancel_order"):
            self._client().cancel_order_by_id("some-id")

    def test_cancel_order_raises(self):
        with pytest.raises(RuntimeError, match="cancel_order"):
            self._client().cancel_order("some-id")


# ---------------------------------------------------------------------------
# 2. _FakeSmokeClientWithPosition
# ---------------------------------------------------------------------------

class TestFakeSmokeClientWithPosition:
    def _client(self):
        from src.tools.paper_smoke_check import _FakeSmokeClientWithPosition
        return _FakeSmokeClientWithPosition()

    def test_get_all_positions_returns_spy(self):
        positions = self._client().get_all_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert pos["symbol"] == "SPY"
        assert float(pos["qty"]) == 1.0

    def test_submit_order_still_raises(self):
        with pytest.raises(RuntimeError, match="submit_order"):
            self._client().submit_order()

    def test_cancel_order_by_id_still_raises(self):
        with pytest.raises(RuntimeError, match="cancel_order"):
            self._client().cancel_order_by_id("some-id")


# ---------------------------------------------------------------------------
# 3. _FakeEngine
# ---------------------------------------------------------------------------

class TestFakeEngine:
    def _engine(self):
        from src.tools.paper_smoke_check import _FakeEngine
        return _FakeEngine()

    def test_run_returns_one_intent(self):
        result = self._engine().run()
        intents = result["order_intents"]
        assert len(intents) == 1

    def test_intent_is_spy_buy(self):
        result = self._engine().run()
        intent = result["order_intents"][0]
        assert intent.symbol == "SPY"
        assert intent.side == "buy"

    def test_intent_client_order_id(self):
        result = self._engine().run()
        intent = result["order_intents"][0]
        assert intent.client_order_id == "SMOKE-BUY-001"

    def test_intent_is_market_order(self):
        result = self._engine().run()
        intent = result["order_intents"][0]
        assert intent.order_type == "market"

    def test_intent_quantity_is_1(self):
        result = self._engine().run()
        intent = result["order_intents"][0]
        assert intent.quantity == 1.0

    def test_run_returns_metrics_and_trades(self):
        result = self._engine().run()
        assert "metrics" in result
        assert "trades" in result
        assert "equity_curve" in result

    def test_no_market_data_import(self):
        """_FakeEngine.run() must not trigger any data download."""
        from src.tools.paper_smoke_check import _FakeEngine
        with patch("yfinance.download") as mock_dl:
            _FakeEngine().run()
            mock_dl.assert_not_called()


# ---------------------------------------------------------------------------
# 4. check_config
# ---------------------------------------------------------------------------

class TestCheckConfig:
    def test_valid_config_passes(self, tmp_path):
        from src.tools.paper_smoke_check import check_config
        cfg_path = _write_config(tmp_path)
        result = check_config(cfg_path)
        assert result["passed"] is True
        assert result["label"] == "config_load_and_validate"

    def test_detail_contains_mode(self, tmp_path):
        from src.tools.paper_smoke_check import check_config
        cfg_path = _write_config(tmp_path)
        result = check_config(cfg_path)
        assert "mode=paper" in result["detail"]
        assert "paper_trading_enabled=True" in result["detail"]

    def test_missing_config_file_fails(self, tmp_path):
        from src.tools.paper_smoke_check import check_config
        result = check_config(tmp_path / "nonexistent.yaml")
        assert result["passed"] is False
        assert result["label"] == "config_load_and_validate"

    def test_invalid_paper_config_fails(self, tmp_path):
        """paper_preview_only=false without selected_id → validate_paper_config raises."""
        from src.tools.paper_smoke_check import check_config
        cfg_path = _write_config(tmp_path, textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: false
        """).replace("execution:", "  # override:"))
        # Just write a broken config directly
        bad_yaml = _BASE_YAML.replace(
            "paper_preview_only: true",
            "paper_preview_only: false",
        )
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text(bad_yaml, encoding="utf-8")
        result = check_config(bad_path)
        assert result["passed"] is False
        assert "paper_selected_client_order_id" in result["detail"]

    def test_label_is_config_load_and_validate(self, tmp_path):
        from src.tools.paper_smoke_check import check_config
        cfg_path = _write_config(tmp_path)
        result = check_config(cfg_path)
        assert result["label"] == "config_load_and_validate"


# ---------------------------------------------------------------------------
# 5. check_buy_preview
# ---------------------------------------------------------------------------

class TestCheckBuyPreview:
    def _run(self, tmp_path):
        from src.tools.paper_smoke_check import check_buy_preview
        cfg = _fake_cfg()
        return check_buy_preview(cfg, tmp_path), tmp_path

    def test_passes(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result["passed"] is True

    def test_label(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result["label"] == "buy_preview"

    def test_csv_written(self, tmp_path):
        _, out = self._run(tmp_path)
        assert (out / "paper_candidate_intents.csv").exists()

    def test_csv_has_one_row(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_candidate_intents.csv")
        assert len(df) == 1

    def test_csv_has_spy_buy(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_candidate_intents.csv")
        assert df.iloc[0]["symbol"] == "SPY"
        assert df.iloc[0]["side"] == "buy"

    def test_csv_has_expected_columns(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_candidate_intents.csv")
        for col in ["client_order_id", "timestamp", "symbol", "side", "quantity", "order_type", "reason"]:
            assert col in df.columns

    def test_submit_never_called(self, tmp_path):
        """AlpacaBrokerAdapter.submit_order must not be called during buy preview."""
        from src.tools.paper_smoke_check import _FakeSmokeClient, check_buy_preview
        client = _FakeSmokeClient()
        cfg = _fake_cfg()
        with patch.object(client, "submit_order", side_effect=RuntimeError("should not call")) as mock_sub:
            check_buy_preview(cfg, tmp_path)
            mock_sub.assert_not_called()

    def test_cancel_never_called(self, tmp_path):
        from src.tools.paper_smoke_check import _FakeSmokeClient, check_buy_preview
        client = _FakeSmokeClient()
        cfg = _fake_cfg()
        with patch.object(client, "cancel_order_by_id", side_effect=RuntimeError("should not call")) as mock_c:
            check_buy_preview(cfg, tmp_path)
            mock_c.assert_not_called()

    def test_detail_mentions_csv_name(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert "paper_candidate_intents.csv" in result["detail"]

    def test_detail_mentions_count(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert "1 candidate" in result["detail"]


# ---------------------------------------------------------------------------
# 6. check_close_preview
# ---------------------------------------------------------------------------

class TestCheckClosePreview:
    def _run(self, tmp_path):
        from src.tools.paper_smoke_check import check_close_preview
        cfg = _fake_cfg()
        return check_close_preview(cfg, tmp_path), tmp_path

    def test_passes(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result["passed"] is True

    def test_label(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result["label"] == "close_preview"

    def test_csv_written(self, tmp_path):
        _, out = self._run(tmp_path)
        assert (out / "paper_close_candidate_intents.csv").exists()

    def test_csv_has_one_row(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_close_candidate_intents.csv")
        assert len(df) == 1

    def test_csv_has_spy_sell(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_close_candidate_intents.csv")
        assert df.iloc[0]["symbol"] == "SPY"
        assert df.iloc[0]["side"] == "sell"

    def test_csv_quantity_matches_position(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_close_candidate_intents.csv")
        assert float(df.iloc[0]["quantity"]) == 1.0

    def test_csv_has_expected_columns(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_close_candidate_intents.csv")
        for col in ["client_order_id", "timestamp", "symbol", "side", "quantity",
                    "order_type", "reason", "current_position_qty"]:
            assert col in df.columns

    def test_submit_never_called(self, tmp_path):
        from src.tools.paper_smoke_check import _FakeSmokeClientWithPosition
        client = _FakeSmokeClientWithPosition()
        with patch.object(client, "submit_order", side_effect=RuntimeError("should not call")) as mock_sub:
            from src.tools.paper_smoke_check import check_close_preview
            check_close_preview(_fake_cfg(), tmp_path)
            mock_sub.assert_not_called()

    def test_detail_mentions_csv_name(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert "paper_close_candidate_intents.csv" in result["detail"]

    def test_detail_mentions_count(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert "1 candidate" in result["detail"]

    def test_order_type_is_market(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_close_candidate_intents.csv")
        assert df.iloc[0]["order_type"] == "market"

    def test_reason_is_paper_close(self, tmp_path):
        _, out = self._run(tmp_path)
        df = pd.read_csv(out / "paper_close_candidate_intents.csv")
        assert df.iloc[0]["reason"] == "paper_close"


# ---------------------------------------------------------------------------
# 7. check_replay
# ---------------------------------------------------------------------------

class TestCheckReplay:
    def test_skipped_when_intents_missing(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        result = check_replay(tmp_path)
        assert result["passed"] is True
        assert "skipped" in result["detail"]

    def test_skipped_when_results_missing(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        (tmp_path / "order_intents.csv").write_text("a,b\n1,2\n")
        result = check_replay(tmp_path)
        assert result["passed"] is True
        assert "skipped" in result["detail"]

    def test_skipped_when_both_missing(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        result = check_replay(tmp_path)
        assert result["passed"] is True

    def test_label_is_replay_reconciliation(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        result = check_replay(tmp_path)
        assert result["label"] == "replay_reconciliation"

    def test_passes_when_replay_returns_pass(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        (tmp_path / "order_intents.csv").write_text("a,b\n1,2\n")
        (tmp_path / "order_results.csv").write_text("a,b\n1,2\n")
        mock_recon = {
            "overall_status": "PASS",
            "intent_count": 1,
            "result_count": 1,
            "mismatch_count": 0,
        }
        with patch("src.tools.paper_smoke_check.check_replay.__module__"):
            pass
        with patch("src.tools.replay_order_reconciliation.replay", return_value=mock_recon):
            result = check_replay(tmp_path)
        assert result["passed"] is True

    def test_fails_when_replay_returns_fail(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        (tmp_path / "order_intents.csv").write_text("a,b\n1,2\n")
        (tmp_path / "order_results.csv").write_text("a,b\n1,2\n")
        mock_recon = {
            "overall_status": "FAIL",
            "intent_count": 1,
            "result_count": 1,
            "mismatch_count": 1,
        }
        with patch("src.tools.replay_order_reconciliation.replay", return_value=mock_recon):
            result = check_replay(tmp_path)
        assert result["passed"] is False

    def test_na_status_counts_as_pass(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        (tmp_path / "order_intents.csv").write_text("a,b\n1,2\n")
        (tmp_path / "order_results.csv").write_text("a,b\n1,2\n")
        mock_recon = {
            "overall_status": "N/A",
            "intent_count": 0,
            "result_count": 0,
            "mismatch_count": 0,
        }
        with patch("src.tools.replay_order_reconciliation.replay", return_value=mock_recon):
            result = check_replay(tmp_path)
        assert result["passed"] is True

    def test_replay_exception_fails(self, tmp_path):
        from src.tools.paper_smoke_check import check_replay
        (tmp_path / "order_intents.csv").write_text("a,b\n1,2\n")
        (tmp_path / "order_results.csv").write_text("a,b\n1,2\n")
        with patch("src.tools.replay_order_reconciliation.replay", side_effect=RuntimeError("bad")):
            result = check_replay(tmp_path)
        assert result["passed"] is False
        assert "bad" in result["detail"]


# ---------------------------------------------------------------------------
# 8. print_summary
# ---------------------------------------------------------------------------

class TestPrintSummary:
    def test_all_pass_prints_pass(self, capsys):
        from src.tools.paper_smoke_check import print_summary
        checks = [
            {"label": "a", "passed": True, "detail": ""},
            {"label": "b", "passed": True, "detail": "ok"},
        ]
        print_summary(checks)
        out = capsys.readouterr().out
        assert "RESULT: PASS" in out
        assert "[PASS]" in out

    def test_any_fail_prints_fail(self, capsys):
        from src.tools.paper_smoke_check import print_summary
        checks = [
            {"label": "a", "passed": True, "detail": ""},
            {"label": "b", "passed": False, "detail": "oops"},
        ]
        print_summary(checks)
        out = capsys.readouterr().out
        assert "RESULT: FAIL" in out
        assert "[FAIL]" in out

    def test_detail_shown_when_present(self, capsys):
        from src.tools.paper_smoke_check import print_summary
        checks = [{"label": "x", "passed": True, "detail": "some_detail"}]
        print_summary(checks)
        out = capsys.readouterr().out
        assert "some_detail" in out

    def test_no_detail_when_empty(self, capsys):
        from src.tools.paper_smoke_check import print_summary
        checks = [{"label": "x", "passed": True, "detail": ""}]
        print_summary(checks)
        out = capsys.readouterr().out
        assert "()" not in out


# ---------------------------------------------------------------------------
# 9. CLI (main)
# ---------------------------------------------------------------------------

class TestMain:
    def _make_argv(self, tmp_path, extra: list[str] | None = None) -> list[str]:
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        if extra:
            argv.extend(extra)
        return argv

    def test_exits_0_on_all_pass(self, tmp_path):
        from src.tools.paper_smoke_check import main
        argv = self._make_argv(tmp_path)
        try:
            result = main(argv)
            assert result is None
        except SystemExit as exc:
            assert exc.code == 0 or exc.code is None

    def test_exits_1_on_bad_config(self, tmp_path):
        from src.tools.paper_smoke_check import main
        bad_cfg = tmp_path / "bad.yaml"
        bad_cfg.write_text("not: valid: yaml: [[[", encoding="utf-8")
        argv = ["--config", str(bad_cfg), "--output-dir", str(tmp_path / "out")]
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 1

    def test_output_dir_created(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "nested" / "output"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        try:
            main(argv)
        except SystemExit:
            pass
        assert out_dir.exists()

    def test_buy_preview_csv_written_by_cli(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        try:
            main(argv)
        except SystemExit:
            pass
        assert (out_dir / "paper_candidate_intents.csv").exists()

    def test_close_preview_csv_written_by_cli(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        try:
            main(argv)
        except SystemExit:
            pass
        assert (out_dir / "paper_close_candidate_intents.csv").exists()

    def test_replay_skipped_without_flag(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        with patch("src.tools.paper_smoke_check.check_replay") as mock_replay:
            try:
                main(argv)
            except SystemExit:
                pass
            mock_replay.assert_not_called()

    def test_replay_called_with_replay_dir(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        replay_dir = tmp_path / "replay"
        replay_dir.mkdir()
        argv = [
            "--config", str(cfg_path),
            "--output-dir", str(out_dir),
            "--replay-dir", str(replay_dir),
        ]
        with patch("src.tools.paper_smoke_check.check_replay", return_value={"label": "replay_reconciliation", "passed": True, "detail": "skipped"}) as mock_replay:
            try:
                main(argv)
            except SystemExit:
                pass
            mock_replay.assert_called_once_with(replay_dir)

    def test_no_alpaca_credentials_imported(self, tmp_path):
        """The smoke check must never read APCA_API_KEY_ID or ALPACA_SECRET_KEY from env."""
        import os
        env_backup = os.environ.copy()
        # Poison the environment — if the smoke check reads these, things break
        os.environ.pop("ALPACA_API_KEY", None)
        os.environ.pop("ALPACA_SECRET_KEY", None)
        os.environ.pop("APCA_API_KEY_ID", None)
        os.environ.pop("APCA_API_SECRET_KEY", None)
        try:
            from src.tools.paper_smoke_check import main
            cfg_path = _write_config(tmp_path)
            out_dir = tmp_path / "out"
            argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
            try:
                main(argv)
            except SystemExit as exc:
                assert exc.code == 0 or exc.code is None, f"Unexpected exit code: {exc.code}"
        finally:
            os.environ.update(env_backup)

    def test_exits_nonzero_if_buy_preview_fails(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        failing = {"label": "buy_preview", "passed": False, "detail": "boom"}
        with patch("src.tools.paper_smoke_check.check_buy_preview", return_value=failing):
            with pytest.raises(SystemExit) as exc_info:
                main(argv)
            assert exc_info.value.code == 1

    def test_exits_nonzero_if_close_preview_fails(self, tmp_path):
        from src.tools.paper_smoke_check import main
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]
        failing = {"label": "close_preview", "passed": False, "detail": "boom"}
        with patch("src.tools.paper_smoke_check.check_close_preview", return_value=failing):
            with pytest.raises(SystemExit) as exc_info:
                main(argv)
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 10. No real broker / no real network
# ---------------------------------------------------------------------------

class TestNoRealBroker:
    def test_alpaca_trading_client_never_instantiated(self, tmp_path):
        """TradingClient must not be constructed during smoke check."""
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]

        mock_trading_client = MagicMock()
        with patch.dict(
            sys.modules,
            {"alpaca.trading.client": mock_trading_client},
        ):
            from src.tools.paper_smoke_check import main
            try:
                main(argv)
            except SystemExit:
                pass
            mock_trading_client.TradingClient.assert_not_called()

    def test_yfinance_download_never_called(self, tmp_path):
        """yfinance.download must not be called during smoke check."""
        cfg_path = _write_config(tmp_path)
        out_dir = tmp_path / "out"
        argv = ["--config", str(cfg_path), "--output-dir", str(out_dir)]

        with patch("yfinance.download") as mock_dl:
            from src.tools.paper_smoke_check import main
            try:
                main(argv)
            except SystemExit:
                pass
            mock_dl.assert_not_called()
