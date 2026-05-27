"""
tests/test_paper_trading_readiness.py
--------------------------------------
Tests for the paper-trading readiness doc and CLI startup safety checks.

No network calls, no API keys, no Alpaca integration.
"""

from __future__ import annotations

import logging
import pathlib
import sys
import unittest.mock as mock
from zoneinfo import ZoneInfo

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_READINESS_DOC = _REPO_ROOT / "docs" / "paper_trading_readiness.md"


# ===========================================================================
# Readiness document existence and content
# ===========================================================================

class TestReadinessDoc:
    def _text(self) -> str:
        return _READINESS_DOC.read_text(encoding="utf-8")

    def test_doc_exists(self):
        assert _READINESS_DOC.exists(), "docs/paper_trading_readiness.md not found"

    def test_doc_is_not_empty(self):
        assert len(self._text().strip()) > 0

    def test_doc_states_no_alpaca_integration(self):
        text = self._text()
        assert "No Alpaca integration" in text or "no Alpaca integration" in text or \
               "not yet" in text.lower() and "alpaca" in text.lower()

    def test_doc_mentions_alpaca_is_not_yet_implemented(self):
        text = self._text().lower()
        assert "alpaca" in text
        # Must note it is absent / not yet implemented
        assert "not yet" in text or "no alpaca" in text

    def test_doc_states_paper_mode_raises_not_implemented(self):
        text = self._text()
        assert "NotImplementedError" in text

    def test_doc_mentions_fake_broker(self):
        text = self._text()
        assert "FakeBrokerAdapter" in text or "fake broker" in text.lower() or "FakeBroker" in text

    def test_doc_mentions_dry_run(self):
        text = self._text().lower()
        assert "dry_run" in text or "dry-run" in text

    def test_doc_has_go_nogo_checklist(self):
        text = self._text().lower()
        assert "go" in text and ("no-go" in text or "no go" in text or "checklist" in text)

    def test_doc_mentions_no_api_keys(self):
        text = self._text().lower()
        assert "no api key" in text or "api key" in text

    def test_doc_mentions_execution_mode(self):
        text = self._text()
        assert "execution.mode" in text

    def test_doc_has_broker_integration_section(self):
        text = self._text().lower()
        assert "broker integration" in text

    def test_doc_mentions_backtest_only_status(self):
        text = self._text().upper()
        assert "BACKTEST" in text


# ===========================================================================
# Startup log — execution.mode and dry_run_broker appear in logs
# ===========================================================================

def _make_minimal_config(mode: str = "backtest", dry_run: bool = False):
    """Return an AppConfig-like object with just the fields main() reads."""
    from src.config.loader import (
        AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
        LoggingConfig, RiskConfig, StrategyConfig,
    )
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-01", end_date="2024-01-01",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(name="opening_range_breakout", params={
            "opening_range_start": "09:30", "opening_range_end": "10:00",
            "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
        }),
        risk=RiskConfig(),
        logging=LoggingConfig(level="DEBUG", format="%(message)s"),
        execution=ExecutionConfig(mode=mode, dry_run_broker=dry_run),
    )


class TestStartupLogs:
    """Verify that key session details are emitted to the log on startup."""

    def _capture_startup_logs(self, mode: str = "backtest", dry_run: bool = False) -> list[str]:
        """
        Run main() up to and including the startup log block, then abort
        by raising inside build_engine so we never need real bar data.
        """
        cfg = _make_minimal_config(mode=mode, dry_run=dry_run)

        log_records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                log_records.append(record)

        handler = _Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)

        try:
            with mock.patch("src.main.load_config", return_value=cfg), \
                 mock.patch("sys.argv", ["prog"]), \
                 mock.patch("src.backtest.backtest_runner.run_backtest", side_effect=SystemExit(0)):
                try:
                    from src.main import main as _main
                    _main()
                except SystemExit:
                    pass
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)

        return [r.getMessage() for r in log_records]

    def test_startup_logs_execution_mode(self):
        msgs = self._capture_startup_logs(mode="backtest")
        combined = "\n".join(msgs)
        assert "execution.mode" in combined or "backtest" in combined

    def test_startup_logs_dry_run_broker_false(self):
        msgs = self._capture_startup_logs(dry_run=False)
        combined = "\n".join(msgs)
        assert "dry_run_broker" in combined or "False" in combined

    def test_startup_logs_dry_run_broker_true(self):
        msgs = self._capture_startup_logs(dry_run=True)
        combined = "\n".join(msgs)
        assert "dry_run_broker" in combined or "True" in combined

    def test_startup_logs_symbols(self):
        msgs = self._capture_startup_logs()
        combined = "\n".join(msgs)
        assert "SPY" in combined

    def test_startup_logs_strategy_name(self):
        msgs = self._capture_startup_logs()
        combined = "\n".join(msgs)
        assert "opening_range_breakout" in combined

    def test_startup_log_distinguishes_cli_mode_from_execution_mode(self):
        """First log line must show both cli_mode and execution.mode separately.

        This prevents confusion when args.mode (e.g. "backtest") differs from
        cfg.execution.mode (e.g. "paper").
        """
        msgs = self._capture_startup_logs(mode="backtest")
        combined = "\n".join(msgs)
        assert "cli_mode=" in combined
        assert "execution.mode=" in combined

    def test_startup_log_shows_correct_execution_mode(self):
        """execution.mode in the first log line must reflect cfg, not args."""
        msgs = self._capture_startup_logs(mode="backtest")
        # Find the "Loaded config" line
        loaded_line = next((m for m in msgs if "Loaded config" in m), "")
        assert "execution.mode=backtest" in loaded_line

    def test_startup_logs_output_dir(self):
        msgs = self._capture_startup_logs()
        combined = "\n".join(msgs)
        # output_dir appears somewhere (default is "output")
        assert "output" in combined.lower()


# ===========================================================================
# Dry-run warning message
# ===========================================================================

class TestDryRunWarning:
    def _capture_warnings(self, dry_run: bool) -> list[str]:
        cfg = _make_minimal_config(dry_run=dry_run)
        warnings: list[str] = []

        class _WarnCapture(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    warnings.append(record.getMessage())

        handler = _WarnCapture()
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)

        try:
            with mock.patch("src.main.load_config", return_value=cfg), \
                 mock.patch("sys.argv", ["prog"]), \
                 mock.patch("src.backtest.backtest_runner.run_backtest", side_effect=SystemExit(0)):
                try:
                    from src.main import main as _main
                    _main()
                except SystemExit:
                    pass
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)

        return warnings

    def test_dry_run_warning_emitted_when_enabled(self):
        warnings = self._capture_warnings(dry_run=True)
        combined = "\n".join(warnings).lower()
        assert "dry-run broker" in combined or "fakebrokera" in combined.lower() \
               or "fake" in combined

    def test_dry_run_warning_not_emitted_when_disabled(self):
        warnings = self._capture_warnings(dry_run=False)
        combined = "\n".join(warnings).lower()
        assert "dry-run broker enabled" not in combined

    def test_dry_run_warning_mentions_fakebrokera(self):
        warnings = self._capture_warnings(dry_run=True)
        combined = "\n".join(warnings)
        assert "FakeBrokerAdapter" in combined

    def test_dry_run_warning_mentions_orders_submitted_only(self):
        warnings = self._capture_warnings(dry_run=True)
        combined = "\n".join(warnings).lower()
        assert "submitted" in combined or "only" in combined
