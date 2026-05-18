"""
tests/test_paper_market_hours_guard.py
----------------------------------------
Tests for src/execution/paper_market_hours_guard.py and its integration with the
paper buy-submit and close-submit flows in main.py.

All tests are fully offline: no Alpaca credentials, no real network calls,
no orders submitted or cancelled.
"""

from __future__ import annotations

import json
from unittest import mock

import pandas as pd
import pytest

from src.execution.paper_market_hours_guard import (
    assert_regular_market_hours,
    is_regular_market_hours,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ET = "America/New_York"


def _ts(dt_str: str) -> pd.Timestamp:
    return pd.Timestamp(dt_str, tz=_ET)


def _provider(ts: pd.Timestamp):
    return lambda: ts


# ---------------------------------------------------------------------------
# Unit tests: is_regular_market_hours
# ---------------------------------------------------------------------------

class TestIsRegularMarketHours:
    def test_weekday_at_open_is_true(self):
        assert is_regular_market_hours(_ts("2024-01-15 09:30:00")) is True  # Monday

    def test_weekday_just_before_close_is_true(self):
        assert is_regular_market_hours(_ts("2024-01-15 15:59:59")) is True

    def test_weekday_at_close_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-15 16:00:00")) is False

    def test_weekday_just_before_open_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-15 09:29:59")) is False

    def test_weekday_midnight_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-15 00:00:00")) is False

    def test_weekday_after_close_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-15 20:00:00")) is False

    def test_saturday_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-13 10:00:00")) is False

    def test_sunday_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-14 10:00:00")) is False

    def test_friday_during_hours_is_true(self):
        assert is_regular_market_hours(_ts("2024-01-19 12:00:00")) is True

    def test_friday_after_close_is_false(self):
        assert is_regular_market_hours(_ts("2024-01-19 16:00:00")) is False

    def test_midday_wednesday_is_true(self):
        assert is_regular_market_hours(_ts("2024-01-17 13:30:00")) is True


# ---------------------------------------------------------------------------
# Unit tests: assert_regular_market_hours
# ---------------------------------------------------------------------------

class TestAssertRegularMarketHours:
    def test_weekday_at_open_allows(self):
        assert_regular_market_hours(_provider(_ts("2024-01-15 09:30:00")))  # no raise

    def test_weekday_before_close_allows(self):
        assert_regular_market_hours(_provider(_ts("2024-01-15 15:59:00")))  # no raise

    def test_weekday_at_close_raises(self):
        with pytest.raises(RuntimeError, match="market is closed"):
            assert_regular_market_hours(_provider(_ts("2024-01-15 16:00:00")))

    def test_error_says_no_order_submitted(self):
        with pytest.raises(RuntimeError, match="no order was submitted"):
            assert_regular_market_hours(_provider(_ts("2024-01-15 16:00:00")))

    def test_weekday_before_open_raises(self):
        with pytest.raises(RuntimeError, match="market is closed"):
            assert_regular_market_hours(_provider(_ts("2024-01-15 09:29:59")))

    def test_saturday_raises(self):
        with pytest.raises(RuntimeError, match="market is closed"):
            assert_regular_market_hours(_provider(_ts("2024-01-13 11:00:00")))

    def test_sunday_raises(self):
        with pytest.raises(RuntimeError, match="market is closed"):
            assert_regular_market_hours(_provider(_ts("2024-01-14 11:00:00")))

    def test_provider_called_once(self):
        provider = mock.MagicMock(return_value=_ts("2024-01-15 10:00:00"))
        assert_regular_market_hours(provider)
        provider.assert_called_once()

    def test_default_provider_uses_real_clock(self):
        # Just verifies no TypeError is raised when called without a provider.
        # The result depends on when the test runs; we only check it doesn't crash.
        try:
            assert_regular_market_hours()
        except RuntimeError as exc:
            assert "market is closed" in str(exc)


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

_FIXED_TS   = pd.Timestamp("2024-01-15 10:00:00", tz=_ET)  # Monday 10:00 — in hours
_BUY_CID    = f"BT-20240115100000-SPY"
_CLOSE_CID  = "BC-20240115-SPY-CLOSE"

_OUTSIDE_TS = pd.Timestamp("2024-01-15 16:00:00", tz=_ET)  # Monday 16:00 — out of hours
_WEEKEND_TS = pd.Timestamp("2024-01-13 10:00:00", tz=_ET)  # Saturday


def _pass_recon_generate(rg_self):
    rg_self._output_dir.mkdir(parents=True, exist_ok=True)
    (rg_self._output_dir / "order_reconciliation.json").write_text(
        json.dumps({"overall_status": "PASS"})
    )


def _make_buy_config(*, preview_only: bool = False, require_market_hours: bool = True):
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
            paper_require_market_hours=require_market_hours,
        ),
    )


def _make_close_config(*, require_market_hours: bool = True):
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
            paper_require_market_hours=require_market_hours,
        ),
    )


def _spy_position():
    return {"symbol": "SPY", "qty": 1.0, "market_value": 475.0,
            "avg_entry_price": 470.0, "current_price": 475.0, "unrealized_pl": 5.0}


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


def _make_buy_intent():
    from src.execution.order_intent import OrderIntent
    return OrderIntent(
        client_order_id=_BUY_CID, symbol="SPY", side="buy", quantity=1.0,
        order_type="market", reason="breakout", timestamp=_FIXED_TS, metadata={},
    )


def _run_buy(cfg, tmp_path, *, clock_ts: pd.Timestamp | None = None):
    """Run buy-submit flow; returns (mock_submit, mock_cancel)."""
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    ts = clock_ts if clock_ts is not None else _FIXED_TS

    fake_engine = mock.MagicMock()
    fake_engine.run.return_value = {
        "order_intents": [_make_buy_intent()],
        "metrics": {}, "trades": [], "equity_curve": [],
    }
    fake_engine._portfolio.positions = {}

    mock_submit = mock.MagicMock(return_value=_make_buy_result())
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=ts), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value={"ok": True, "account": {"status": "ACTIVE"},
                                         "positions": {}, "symbols": ["SPY"]}), \
         mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                           return_value=mock.MagicMock()), \
         mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
         mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
         mock.patch("src.main.build_engine", return_value=fake_engine), \
         mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                    _pass_recon_generate), \
         mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
         mock.patch("src.execution.paper_ledger.append_ledger_row"), \
         mock.patch("src.main.load_config", return_value=cfg), \
         mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
        src.main.main()

    return mock_submit, mock_cancel


def _run_close(cfg, tmp_path, *, clock_ts: pd.Timestamp | None = None):
    """Run close-submit flow; returns (mock_submit, mock_cancel)."""
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    ts = clock_ts if clock_ts is not None else _FIXED_TS

    mock_submit = mock.MagicMock(return_value=_make_close_result())
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=ts), \
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

class TestBuySubmitMarketHoursGuard:
    def test_weekday_0930_allows_submit(self, tmp_path):
        cfg = _make_buy_config()
        ts = pd.Timestamp("2024-01-15 09:30:00", tz=_ET)
        mock_submit, _ = _run_buy(cfg, tmp_path, clock_ts=ts)
        assert mock_submit.call_count == 1

    def test_weekday_1559_allows_submit(self, tmp_path):
        cfg = _make_buy_config()
        ts = pd.Timestamp("2024-01-15 15:59:00", tz=_ET)
        mock_submit, _ = _run_buy(cfg, tmp_path, clock_ts=ts)
        assert mock_submit.call_count == 1

    def test_weekday_1600_blocks_before_submit(self, tmp_path):
        cfg = _make_buy_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}
        mock_submit = mock.MagicMock()
        ts = pd.Timestamp("2024-01-15 16:00:00", tz=_ET)

        with mock.patch.object(pd.Timestamp, "now", return_value=ts), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="market is closed"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_weekday_0929_blocks_before_submit(self, tmp_path):
        cfg = _make_buy_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}
        mock_submit = mock.MagicMock()
        ts = pd.Timestamp("2024-01-15 09:29:00", tz=_ET)

        with mock.patch.object(pd.Timestamp, "now", return_value=ts), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="market is closed"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_saturday_blocks_buy(self, tmp_path):
        cfg = _make_buy_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}
        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_WEEKEND_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="market is closed"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_disabled_config_skips_guard(self, tmp_path):
        """With paper_require_market_hours=False, guard skipped even outside hours."""
        cfg = _make_buy_config(require_market_hours=False)
        mock_submit, _ = _run_buy(cfg, tmp_path, clock_ts=_OUTSIDE_TS)
        assert mock_submit.call_count == 1

    def test_submit_exactly_once_when_allowed(self, tmp_path):
        cfg = _make_buy_config()
        mock_submit, _ = _run_buy(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_cancel_never_called(self, tmp_path):
        cfg = _make_buy_config()
        _, mock_cancel = _run_buy(cfg, tmp_path)
        mock_cancel.assert_not_called()

    def test_preview_does_not_call_market_hours_guard(self, tmp_path):
        """In preview mode, assert_regular_market_hours is never invoked."""
        cfg = _make_buy_config(preview_only=True)
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}

        guard_spy = mock.MagicMock(
            side_effect=AssertionError("guard must not run in preview")
        )

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock.MagicMock()), \
             mock.patch("src.execution.paper_market_hours_guard.assert_regular_market_hours",
                        guard_spy), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _pass_recon_generate), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            src.main.main()  # must not raise

        guard_spy.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests: close-submit
# ---------------------------------------------------------------------------

class TestCloseSubmitMarketHoursGuard:
    def test_weekday_1559_allows_close_submit(self, tmp_path):
        cfg = _make_close_config()
        ts = pd.Timestamp("2024-01-15 15:59:00", tz=_ET)
        mock_submit, _ = _run_close(cfg, tmp_path, clock_ts=ts)
        assert mock_submit.call_count == 1

    def test_weekday_1600_blocks_close_before_submit(self, tmp_path):
        cfg = _make_close_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        mock_submit = mock.MagicMock()
        ts = pd.Timestamp("2024-01-15 16:00:00", tz=_ET)

        with mock.patch.object(pd.Timestamp, "now", return_value=ts), \
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
            with pytest.raises(RuntimeError, match="market is closed"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_sunday_blocks_close(self, tmp_path):
        cfg = _make_close_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        mock_submit = mock.MagicMock()
        ts = pd.Timestamp("2024-01-14 11:00:00", tz=_ET)  # Sunday

        with mock.patch.object(pd.Timestamp, "now", return_value=ts), \
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
            with pytest.raises(RuntimeError, match="market is closed"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_disabled_config_skips_guard_for_close(self, tmp_path):
        """With paper_require_market_hours=False, guard skipped even outside hours."""
        cfg = _make_close_config(require_market_hours=False)
        mock_submit, _ = _run_close(cfg, tmp_path, clock_ts=_OUTSIDE_TS)
        assert mock_submit.call_count == 1

    def test_submit_exactly_once_when_allowed(self, tmp_path):
        cfg = _make_close_config()
        mock_submit, _ = _run_close(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_cancel_never_called(self, tmp_path):
        cfg = _make_close_config()
        _, mock_cancel = _run_close(cfg, tmp_path)
        mock_cancel.assert_not_called()
