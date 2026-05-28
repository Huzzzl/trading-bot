"""
tests/test_paper_daily_limits.py
----------------------------------
Tests for src/execution/paper_daily_limits.py and its integration with the
paper buy-submit and close-submit flows in main.py.

All tests are fully offline: no Alpaca credentials, no real network calls,
no orders submitted or cancelled.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from src.execution.paper_daily_limits import (
    assert_within_daily_limits,
    compute_daily_usage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date(2024, 1, 15)
_TZ    = "America/New_York"

# Timestamp that falls on _TODAY in New York (10 AM ET = 15:00 UTC).
_TODAY_TS = "2024-01-15T15:00:00+00:00"
# Timestamp that falls on the previous day in New York.
_PREV_TS  = "2024-01-14T15:00:00+00:00"


def _write_ledger(path: Path, rows: list[dict]) -> None:
    cols = [
        "run_id", "flow", "client_order_id", "alpaca_order_id",
        "symbol", "side", "quantity", "status",
        "submitted_at", "output_dir", "created_at_utc", "notes",
    ]
    df = pd.DataFrame(rows)
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    df[cols].to_csv(path, index=False)


def _make_row(
    flow: str = "buy_submit",
    status: str = "filled",
    submitted_at: str = _TODAY_TS,
    created_at_utc: str = "",
) -> dict:
    return {
        "flow": flow,
        "status": status,
        "submitted_at": submitted_at,
        "created_at_utc": created_at_utc,
        "client_order_id": "test-coid",
    }


# ---------------------------------------------------------------------------
# Unit tests: compute_daily_usage
# ---------------------------------------------------------------------------

class TestComputeDailyUsage:
    def test_missing_ledger_returns_zero(self, tmp_path):
        usage = compute_daily_usage(tmp_path / "nonexistent.csv", _TODAY, _TZ)
        assert usage == {"total_orders": 0, "buy_orders": 0, "close_orders": 0}

    def test_empty_ledger_returns_zero(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage == {"total_orders": 0, "buy_orders": 0, "close_orders": 0}

    def test_counts_buy_and_close_for_today(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit",   "filled",   _TODAY_TS),
            _make_row("close_submit", "filled",   _TODAY_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 2
        assert usage["buy_orders"]   == 1
        assert usage["close_orders"] == 1

    def test_rejected_rows_do_not_count(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit",   "rejected",  _TODAY_TS),
            _make_row("buy_submit",   "filled",    _TODAY_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 1
        assert usage["buy_orders"]   == 1

    def test_cancelled_rows_do_not_count(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("close_submit", "cancelled", _TODAY_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 0

    def test_canceled_variant_does_not_count(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "canceled", _TODAY_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 0

    def test_previous_day_rows_do_not_count(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit",   "filled",  _PREV_TS),
            _make_row("close_submit", "accepted", _PREV_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 0

    def test_falls_back_to_created_at_utc(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "filled", submitted_at="", created_at_utc=_TODAY_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 1

    def test_accepted_status_counts(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "accepted", _TODAY_TS),
        ])
        usage = compute_daily_usage(p, _TODAY, _TZ)
        assert usage["total_orders"] == 1


# ---------------------------------------------------------------------------
# Unit tests: assert_within_daily_limits
# ---------------------------------------------------------------------------

class TestAssertWithinDailyLimits:
    def test_no_limits_passes(self, tmp_path):
        assert_within_daily_limits(
            tmp_path / "ledger.csv", _TODAY, "buy_submit", 1.0,
        )

    def test_max_orders_not_reached_passes(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [_make_row("buy_submit", "filled", _TODAY_TS)])
        assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_orders=3)

    def test_max_orders_reached_raises(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit",   "filled", _TODAY_TS),
            _make_row("close_submit", "filled", _TODAY_TS),
            _make_row("buy_submit",   "filled", _TODAY_TS),
        ])
        with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
            assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_orders=3)

    def test_error_says_no_order_submitted(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
        ])
        with pytest.raises(RuntimeError, match="no order was submitted"):
            assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_orders=3)

    def test_max_buy_orders_reached_blocks_buy(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
        ])
        with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
            assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_buy_orders=2)

    def test_max_buy_orders_does_not_block_close(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
        ])
        # Should not raise: close flow is not subject to max_buy_orders.
        assert_within_daily_limits(p, _TODAY, "close_submit", 1.0, max_buy_orders=2)

    def test_max_close_orders_reached_blocks_close(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("close_submit", "filled", _TODAY_TS),
            _make_row("close_submit", "filled", _TODAY_TS),
        ])
        with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
            assert_within_daily_limits(p, _TODAY, "close_submit", 1.0, max_close_orders=2)

    def test_max_close_orders_does_not_block_buy(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("close_submit", "filled", _TODAY_TS),
            _make_row("close_submit", "filled", _TODAY_TS),
        ])
        assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_close_orders=2)

    def test_rejected_rows_not_counted_for_limit(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "rejected", _TODAY_TS),
            _make_row("buy_submit", "rejected", _TODAY_TS),
            _make_row("buy_submit", "rejected", _TODAY_TS),
        ])
        # All rejected — should not raise even at max_orders=3.
        assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_orders=3)

    def test_previous_day_rows_not_counted_for_limit(self, tmp_path):
        p = tmp_path / "ledger.csv"
        _write_ledger(p, [
            _make_row("buy_submit", "filled", _PREV_TS),
            _make_row("buy_submit", "filled", _PREV_TS),
            _make_row("buy_submit", "filled", _PREV_TS),
        ])
        assert_within_daily_limits(p, _TODAY, "buy_submit", 1.0, max_orders=3)

    def test_notional_within_limit_passes(self, tmp_path):
        assert_within_daily_limits(
            tmp_path / "ledger.csv", _TODAY, "buy_submit", 1.0,
            max_notional=500.0, intent_price=450.0,
        )

    def test_notional_exceeds_limit_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
            assert_within_daily_limits(
                tmp_path / "ledger.csv", _TODAY, "buy_submit", 1.0,
                max_notional=400.0, intent_price=450.0,
            )

    def test_notional_enabled_price_missing_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
            assert_within_daily_limits(
                tmp_path / "ledger.csv", _TODAY, "buy_submit", 1.0,
                max_notional=500.0, intent_price=None,
            )

    def test_notional_enabled_price_zero_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
            assert_within_daily_limits(
                tmp_path / "ledger.csv", _TODAY, "buy_submit", 1.0,
                max_notional=500.0, intent_price=0.0,
            )

    def test_notional_none_price_missing_passes(self, tmp_path):
        # max_notional=None means notional check is skipped entirely.
        assert_within_daily_limits(
            tmp_path / "ledger.csv", _TODAY, "buy_submit", 1.0,
            max_notional=None, intent_price=None,
        )


# ---------------------------------------------------------------------------
# Integration tests: buy-submit flow
# ---------------------------------------------------------------------------

_FIXED_TS         = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")
_FIXED_TS_LABEL   = "20240115100000"
_BUY_CID          = f"BT-{_FIXED_TS_LABEL}-SPY"


def _pass_recon_generate(rg_self):
    rg_self._output_dir.mkdir(parents=True, exist_ok=True)
    (rg_self._output_dir / "order_reconciliation.json").write_text(
        json.dumps({"overall_status": "PASS"})
    )


def _make_buy_config(
    *,
    preview_only: bool = False,
    selected_id: str = _BUY_CID,
    ledger_path: str | None = None,
    max_orders: int = 3,
    max_buy_orders: int = 2,
    max_close_orders: int = 2,
    max_notional: float | None = None,
):
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
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
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
            paper_selected_client_order_id=selected_id if not preview_only else None,
            paper_order_quantity_override=1.0,
            paper_ledger_path=ledger_path or "output/paper_execution_ledger.csv",
            paper_daily_max_orders=max_orders,
            paper_daily_max_buy_orders=max_buy_orders,
            paper_daily_max_close_orders=max_close_orders,
            paper_daily_max_notional=max_notional,
        ),
    )


def _make_buy_result(coid: str = _BUY_CID, qty: float = 1.0, status: str = "accepted"):
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id=f"ALPACA-{coid}",
        symbol="SPY", side="buy", quantity=qty, status=status,
        submitted_at=_FIXED_TS, reason="breakout",
        client_order_id=coid,
        metadata={"raw_status": status, "partial_fill": False},
    )


def _make_buy_intent(coid: str = _BUY_CID, qty: float = 1.0, entry_price: float | None = None):
    from src.execution.order_intent import OrderIntent
    meta = {}
    if entry_price is not None:
        meta["entry_price"] = entry_price
    return OrderIntent(
        client_order_id=coid,
        symbol="SPY", side="buy", quantity=qty,
        order_type="market", reason="breakout",
        timestamp=_FIXED_TS, metadata=meta,
    )


def _run_buy_submit(cfg, tmp_path, *, intent=None, submit_return=None, extra_positions=None):
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    _intent = intent or _make_buy_intent()
    _result = submit_return or _make_buy_result()

    fake_preflight = {
        "ok": True, "account": {"status": "ACTIVE"},
        "positions": extra_positions or {}, "symbols": ["SPY"],
    }

    mock_submit = mock.MagicMock(return_value=_result)
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value=fake_preflight), \
         mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
         mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
         mock.patch("src.backtest.backtest_runner.run_backtest") as mock_run_backtest, \
         mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                    _pass_recon_generate), \
         mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
         mock.patch("src.execution.paper_ledger.append_ledger_row"), \
         mock.patch("src.main.load_config", return_value=cfg), \
         mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
        mock_run_backtest.return_value.order_intents = [_intent]
        mock_run_backtest.return_value.metrics = {}
        mock_run_backtest.return_value.trades = []
        mock_run_backtest.return_value.equity_curve = []
        src.main.main()

    return mock_submit, mock_cancel


class TestBuySubmitDailyLimits:
    def test_preview_does_not_enforce_limits(self, tmp_path):
        """Preview-only mode never reaches the limit check."""
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
        ])
        cfg = _make_buy_config(preview_only=True, ledger_path=str(ledger))
        # Should not raise even though max_orders=3 is reached.
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
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

    def test_missing_ledger_counts_as_zero(self, tmp_path):
        ledger = tmp_path / "nonexistent.csv"
        cfg = _make_buy_config(ledger_path=str(ledger), max_orders=3)
        mock_submit, _ = _run_buy_submit(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_max_total_orders_blocks_buy_before_submit(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [
            _make_row("buy_submit",   "filled", _TODAY_TS),
            _make_row("close_submit", "filled", _TODAY_TS),
            _make_row("buy_submit",   "filled", _TODAY_TS),
        ])
        cfg = _make_buy_config(ledger_path=str(ledger), max_orders=3)
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
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
            with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_max_buy_orders_blocks_buy_before_submit(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [
            _make_row("buy_submit", "filled", _TODAY_TS),
            _make_row("buy_submit", "filled", _TODAY_TS),
        ])
        cfg = _make_buy_config(ledger_path=str(ledger), max_buy_orders=2, max_orders=99)
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
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
            with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_submit_order_called_exactly_once_when_within_limits(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        cfg = _make_buy_config(ledger_path=str(ledger), max_orders=3)
        mock_submit, _ = _run_buy_submit(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_cancel_order_never_called(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        cfg = _make_buy_config(ledger_path=str(ledger), max_orders=3)
        _, mock_cancel = _run_buy_submit(cfg, tmp_path)
        mock_cancel.assert_not_called()

    def test_notional_limit_blocks_buy_when_exceeded(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        cfg = _make_buy_config(ledger_path=str(ledger), max_notional=400.0)
        intent = _make_buy_intent(entry_price=450.0)
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.backtest.backtest_runner.run_backtest") as mock_run_backtest, \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            mock_run_backtest.return_value.order_intents = [intent]
            mock_run_backtest.return_value.metrics = {}
            mock_run_backtest.return_value.trades = []
            mock_run_backtest.return_value.equity_curve = []
            with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_notional_enabled_price_missing_raises_before_submit(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        cfg = _make_buy_config(ledger_path=str(ledger), max_notional=500.0)
        intent = _make_buy_intent()  # no entry_price in metadata
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.backtest.backtest_runner.run_backtest") as mock_run_backtest, \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            mock_run_backtest.return_value.order_intents = [intent]
            mock_run_backtest.return_value.metrics = {}
            mock_run_backtest.return_value.trades = []
            mock_run_backtest.return_value.equity_curve = []
            with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
                src.main.main()
        mock_submit.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests: close-submit flow
# ---------------------------------------------------------------------------

_CLOSE_CID        = "BC-20240115-SPY-CLOSE"


def _make_close_config(
    *,
    ledger_path: str | None = None,
    max_orders: int = 3,
    max_buy_orders: int = 2,
    max_close_orders: int = 2,
    max_notional: float | None = None,
):
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
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
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
            paper_ledger_path=ledger_path or "output/paper_execution_ledger.csv",
            paper_daily_max_orders=max_orders,
            paper_daily_max_buy_orders=max_buy_orders,
            paper_daily_max_close_orders=max_close_orders,
            paper_daily_max_notional=max_notional,
        ),
    )


def _make_close_submit_return(qty: float = 1.0):
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id=f"ALPACA-{_CLOSE_CID}",
        symbol="SPY", side="sell", quantity=qty, status="accepted",
        submitted_at=_FIXED_TS, reason="paper_close",
        client_order_id=_CLOSE_CID,
        metadata={"raw_status": "accepted", "partial_fill": False},
    )


def _run_close_submit(cfg, tmp_path, *, submit_return=None):
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    _result = submit_return or _make_close_submit_return()
    fake_preflight = {
        "ok": True, "account": {"status": "ACTIVE"},
        "positions": {"SPY": {"symbol": "SPY", "qty": 1.0, "market_value": 475.0,
                               "avg_entry_price": 470.0, "current_price": 475.0,
                               "unrealized_pl": 5.0}},
        "symbols": ["SPY"],
    }
    mock_submit = mock.MagicMock(return_value=_result)
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value=fake_preflight), \
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


class TestCloseSubmitDailyLimits:
    def test_max_close_orders_blocks_close_before_submit(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [
            _make_row("close_submit", "filled", _TODAY_TS),
            _make_row("close_submit", "filled", _TODAY_TS),
        ])
        cfg = _make_close_config(ledger_path=str(ledger), max_close_orders=2, max_orders=99)
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": {"symbol": "SPY", "qty": 1.0, "market_value": 475.0,
                                   "avg_entry_price": 470.0, "current_price": 475.0,
                                   "unrealized_pl": 5.0}},
            "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="daily paper limit exceeded"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_submit_order_called_exactly_once_when_within_limits(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        cfg = _make_close_config(ledger_path=str(ledger), max_orders=3)
        mock_submit, _ = _run_close_submit(cfg, tmp_path)
        assert mock_submit.call_count == 1

    def test_cancel_order_never_called(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        cfg = _make_close_config(ledger_path=str(ledger), max_orders=3)
        _, mock_cancel = _run_close_submit(cfg, tmp_path)
        mock_cancel.assert_not_called()
