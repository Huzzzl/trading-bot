"""
tests/test_paper_kill_switch.py
---------------------------------
Tests for src/execution/paper_kill_switch.py and its integration with the
paper buy-submit and close-submit flows in main.py.

All tests are fully offline: no Alpaca credentials, no real network calls,
no orders submitted or cancelled.
"""

from __future__ import annotations

import json
from unittest import mock

import pandas as pd
import pytest

from src.execution.paper_kill_switch import assert_kill_switch_disabled


# ---------------------------------------------------------------------------
# Unit tests: assert_kill_switch_disabled
# ---------------------------------------------------------------------------

class TestAssertKillSwitchDisabled:
    def test_disabled_is_noop(self):
        assert_kill_switch_disabled(False)  # must not raise

    def test_enabled_raises(self):
        with pytest.raises(RuntimeError):
            assert_kill_switch_disabled(True)

    def test_error_contains_kill_switch_enabled(self):
        with pytest.raises(RuntimeError, match="paper kill switch enabled"):
            assert_kill_switch_disabled(True)

    def test_error_says_no_order_submitted(self):
        with pytest.raises(RuntimeError, match="no order was submitted"):
            assert_kill_switch_disabled(True)


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

_ET         = "America/New_York"
_FIXED_TS   = pd.Timestamp("2024-01-15 10:00:00", tz=_ET)
_BUY_CID    = "BT-20240115100000-SPY"
_CLOSE_CID  = "BC-20240115-SPY-CLOSE"


def _pass_recon_generate(rg_self):
    rg_self._output_dir.mkdir(parents=True, exist_ok=True)
    (rg_self._output_dir / "order_reconciliation.json").write_text(
        json.dumps({"overall_status": "PASS"})
    )


def _make_buy_config(*, kill_switch_enabled: bool = False, preview_only: bool = False):
    from src.config.loader import (
        AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
        LoggingConfig, RiskConfig, StrategyConfig,
    )
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-15", end_date="2024-01-15",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone=_ET),
        strategy=StrategyConfig(name="opening_range_breakout", params={
            "opening_range_start": "09:30", "opening_range_end": "10:00",
            "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
        }),
        risk=RiskConfig(),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_preview_only=preview_only,
            paper_selected_client_order_id=_BUY_CID if not preview_only else None,
            paper_order_quantity_override=1.0,
            paper_kill_switch_enabled=kill_switch_enabled,
        ),
    )


def _make_close_config(*, kill_switch_enabled: bool = False):
    from src.config.loader import (
        AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
        LoggingConfig, RiskConfig, StrategyConfig,
    )
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-15", end_date="2024-01-15",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone=_ET),
        strategy=StrategyConfig(name="opening_range_breakout", params={
            "opening_range_start": "09:30", "opening_range_end": "10:00",
            "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
        }),
        risk=RiskConfig(),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_close_positions_enabled=True,
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_CLOSE_CID,
            paper_kill_switch_enabled=kill_switch_enabled,
        ),
    )


def _spy_position():
    return {"symbol": "SPY", "qty": 1.0, "market_value": 475.0,
            "avg_entry_price": 470.0, "current_price": 475.0, "unrealized_pl": 5.0}


def _make_buy_intent():
    from src.execution.order_intent import OrderIntent
    return OrderIntent(
        client_order_id=_BUY_CID, symbol="SPY", side="buy", quantity=1.0,
        order_type="market", reason="breakout", timestamp=_FIXED_TS, metadata={},
    )


def _make_buy_result():
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id=f"ALPACA-{_BUY_CID}", symbol="SPY", side="buy", quantity=1.0,
        status="accepted", submitted_at=_FIXED_TS, reason="breakout",
        client_order_id=_BUY_CID,
        metadata={"raw_status": "accepted", "partial_fill": False},
    )


def _make_close_result():
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id=f"ALPACA-{_CLOSE_CID}", symbol="SPY", side="sell", quantity=1.0,
        status="accepted", submitted_at=_FIXED_TS, reason="paper_close",
        client_order_id=_CLOSE_CID,
        metadata={"raw_status": "accepted", "partial_fill": False},
    )


def _run_buy(cfg, tmp_path):
    """Run buy-submit flow; returns (mock_submit, mock_cancel)."""
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    mock_submit = mock.MagicMock(return_value=_make_buy_result())
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value={"ok": True, "account": {"status": "ACTIVE"},
                                         "positions": {}, "symbols": ["SPY"]}), \
         mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                           return_value=mock.MagicMock()), \
         mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
         mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
         mock.patch("src.backtest.backtest_runner.run_backtest") as mock_run_backtest, \
         mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                    _pass_recon_generate), \
         mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
         mock.patch("src.execution.paper_ledger.append_ledger_row"), \
         mock.patch("src.main.load_config", return_value=cfg), \
         mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
        mock_run_backtest.return_value.order_intents = [_make_buy_intent()]
        mock_run_backtest.return_value.metrics = {}
        mock_run_backtest.return_value.trades = []
        mock_run_backtest.return_value.equity_curve = []
        src.main.main()

    return mock_submit, mock_cancel


def _run_close(cfg, tmp_path):
    """Run close-submit flow; returns (mock_submit, mock_cancel)."""
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    mock_submit = mock.MagicMock(return_value=_make_close_result())
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value={"ok": True, "account": {"status": "ACTIVE"},
                                         "positions": {"SPY": _spy_position()},
                                         "symbols": ["SPY"]}), \
         mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                           return_value=mock.MagicMock()), \
         mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
         mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
         mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                    _pass_recon_generate), \
         mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
         mock.patch("src.execution.paper_ledger.append_ledger_row"), \
         mock.patch("src.main.load_config", return_value=cfg), \
         mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
        src.main.main()

    return mock_submit, mock_cancel


# ---------------------------------------------------------------------------
# Integration tests: buy-submit
# ---------------------------------------------------------------------------

class TestBuySubmitKillSwitch:
    def test_enabled_blocks_buy_before_submit(self, tmp_path):
        cfg = _make_buy_config(kill_switch_enabled=True)
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.backtest.backtest_runner.run_backtest") as mock_run_backtest, \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            mock_run_backtest.return_value.order_intents = [_make_buy_intent()]
            mock_run_backtest.return_value.metrics = {}
            mock_run_backtest.return_value.trades = []
            mock_run_backtest.return_value.equity_curve = []
            with pytest.raises(RuntimeError, match="paper kill switch enabled"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_disabled_allows_buy_submit(self, tmp_path):
        cfg = _make_buy_config(kill_switch_enabled=False)
        mock_submit, _ = _run_buy(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_submit_exactly_once_when_disabled(self, tmp_path):
        cfg = _make_buy_config(kill_switch_enabled=False)
        mock_submit, _ = _run_buy(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_cancel_never_called_on_buy(self, tmp_path):
        cfg = _make_buy_config(kill_switch_enabled=False)
        _, mock_cancel = _run_buy(cfg, tmp_path)
        mock_cancel.assert_not_called()

    def test_preview_unaffected_by_kill_switch(self, tmp_path):
        """Kill switch is enabled but preview mode means no submit path is reached."""
        cfg = _make_buy_config(kill_switch_enabled=True, preview_only=True)
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.backtest.backtest_runner.run_backtest") as mock_run_backtest, \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _pass_recon_generate), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            mock_run_backtest.return_value.order_intents = [_make_buy_intent()]
            mock_run_backtest.return_value.metrics = {}
            mock_run_backtest.return_value.trades = []
            mock_run_backtest.return_value.equity_curve = []
            src.main.main()  # must not raise

        mock_submit.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests: close-submit
# ---------------------------------------------------------------------------

class TestCloseSubmitKillSwitch:
    def test_enabled_blocks_close_before_submit(self, tmp_path):
        cfg = _make_close_config(kill_switch_enabled=True)
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {"SPY": _spy_position()},
                                             "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="paper kill switch enabled"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_disabled_allows_close_submit(self, tmp_path):
        cfg = _make_close_config(kill_switch_enabled=False)
        mock_submit, _ = _run_close(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_submit_exactly_once_when_disabled(self, tmp_path):
        cfg = _make_close_config(kill_switch_enabled=False)
        mock_submit, _ = _run_close(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_cancel_never_called_on_close(self, tmp_path):
        cfg = _make_close_config(kill_switch_enabled=False)
        _, mock_cancel = _run_close(cfg, tmp_path)
        mock_cancel.assert_not_called()
