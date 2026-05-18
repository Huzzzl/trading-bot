"""
tests/test_paper_open_order_guard.py
--------------------------------------
Tests for src/execution/paper_open_order_guard.py and its integration with the
paper buy-submit and close-submit flows in main.py.

All tests are fully offline: no Alpaca credentials, no real network calls,
no orders submitted or cancelled.
"""

from __future__ import annotations

import json
from unittest import mock

import pandas as pd
import pytest

from src.execution.paper_open_order_guard import (
    assert_no_open_orders_for_symbol,
    find_open_orders_for_symbol,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_order(symbol: str = "SPY") -> dict:
    return {"id": "order-001", "symbol": symbol, "status": "new"}


def _client_with_orders(orders: list) -> mock.MagicMock:
    c = mock.MagicMock()
    c.get_orders.return_value = orders
    c.submit_order.side_effect  = AssertionError("must not call submit_order")
    c.cancel_order.side_effect  = AssertionError("must not call cancel_order")
    return c


def _client_raising(exc: Exception) -> mock.MagicMock:
    c = mock.MagicMock()
    c.get_orders.side_effect = exc
    c.submit_order.side_effect = AssertionError("must not call submit_order")
    c.cancel_order.side_effect  = AssertionError("must not call cancel_order")
    return c


def _client_no_get_orders() -> mock.MagicMock:
    c = mock.MagicMock(spec=[])  # no attributes
    return c


def _get_client_fn(client) -> mock.MagicMock:
    """Return a zero-arg callable that returns *client*."""
    fn = mock.MagicMock(return_value=client)
    fn.submit_order = getattr(client, "submit_order", None)
    fn.cancel_order = getattr(client, "cancel_order", None)
    return fn


# ---------------------------------------------------------------------------
# Unit tests: find_open_orders_for_symbol
# ---------------------------------------------------------------------------

class TestFindOpenOrdersForSymbol:
    def test_returns_empty_when_no_orders(self):
        client = _client_with_orders([])
        assert find_open_orders_for_symbol(client, "SPY") == []

    def test_returns_matching_symbol(self):
        client = _client_with_orders([_open_order("SPY")])
        result = find_open_orders_for_symbol(client, "SPY")
        assert len(result) == 1

    def test_case_insensitive_symbol_match(self):
        client = _client_with_orders([_open_order("SPY")])
        assert len(find_open_orders_for_symbol(client, "spy")) == 1

    def test_does_not_return_other_symbols(self):
        client = _client_with_orders([_open_order("QQQ"), _open_order("AAPL")])
        assert find_open_orders_for_symbol(client, "SPY") == []

    def test_filters_mixed_symbols(self):
        client = _client_with_orders([_open_order("SPY"), _open_order("QQQ")])
        result = find_open_orders_for_symbol(client, "SPY")
        assert len(result) == 1

    def test_raises_when_no_get_orders_method(self):
        client = _client_no_get_orders()
        with pytest.raises(RuntimeError, match="get_orders"):
            find_open_orders_for_symbol(client, "SPY")

    def test_raises_on_api_exception(self):
        client = _client_raising(Exception("network timeout"))
        with pytest.raises(RuntimeError, match="Open-order lookup failed"):
            find_open_orders_for_symbol(client, "SPY")

    def test_uses_list_orders_as_fallback(self):
        c = mock.MagicMock(spec=["list_orders"])
        c.list_orders.return_value = [_open_order("SPY")]
        result = find_open_orders_for_symbol(c, "SPY")
        assert len(result) == 1
        c.list_orders.assert_called_once()

    def test_sdk_object_orders_supported(self):
        order = mock.MagicMock()
        order.symbol = "SPY"
        client = _client_with_orders([order])
        result = find_open_orders_for_symbol(client, "SPY")
        assert len(result) == 1

    def test_none_response_returns_empty(self):
        c = mock.MagicMock()
        c.get_orders.return_value = None
        assert find_open_orders_for_symbol(c, "SPY") == []


# ---------------------------------------------------------------------------
# Unit tests: assert_no_open_orders_for_symbol
# ---------------------------------------------------------------------------

class TestAssertNoOpenOrdersForSymbol:
    def test_passes_when_no_open_orders(self):
        fn = _get_client_fn(_client_with_orders([]))
        assert_no_open_orders_for_symbol(fn, "SPY")  # must not raise

    def test_raises_when_open_order_exists(self):
        fn = _get_client_fn(_client_with_orders([_open_order("SPY")]))
        with pytest.raises(RuntimeError, match="open paper order detected"):
            assert_no_open_orders_for_symbol(fn, "SPY")

    def test_error_says_no_order_submitted(self):
        fn = _get_client_fn(_client_with_orders([_open_order("SPY")]))
        with pytest.raises(RuntimeError, match="no order was submitted"):
            assert_no_open_orders_for_symbol(fn, "SPY")

    def test_error_includes_symbol(self):
        fn = _get_client_fn(_client_with_orders([_open_order("SPY")]))
        with pytest.raises(RuntimeError, match="SPY"):
            assert_no_open_orders_for_symbol(fn, "SPY")

    def test_lookup_failure_raises(self):
        fn = _get_client_fn(_client_raising(Exception("API error")))
        with pytest.raises(RuntimeError, match="Open-order lookup failed"):
            assert_no_open_orders_for_symbol(fn, "SPY")

    def test_client_creation_failure_raises(self):
        def _fail():
            raise RuntimeError("no credentials")
        with pytest.raises(RuntimeError, match="Open-order lookup failed"):
            assert_no_open_orders_for_symbol(_fail, "SPY")

    def test_does_not_raise_for_other_symbol(self):
        fn = _get_client_fn(_client_with_orders([_open_order("QQQ")]))
        assert_no_open_orders_for_symbol(fn, "SPY")  # must not raise

    def test_never_calls_cancel_order(self):
        client = _client_with_orders([_open_order("SPY")])
        fn = _get_client_fn(client)
        with pytest.raises(RuntimeError):
            assert_no_open_orders_for_symbol(fn, "SPY")
        client.cancel_order.assert_not_called()

    def test_never_calls_submit_order(self):
        client = _client_with_orders([])
        fn = _get_client_fn(client)
        assert_no_open_orders_for_symbol(fn, "SPY")
        client.submit_order.assert_not_called()

    def test_get_client_called_lazily(self):
        """get_client is called by assert_no_open_orders_for_symbol, not before."""
        client = _client_with_orders([])
        fn = mock.MagicMock(return_value=client)
        assert_no_open_orders_for_symbol(fn, "SPY")
        fn.assert_called_once()


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

_FIXED_TS         = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")
_FIXED_TS_LABEL   = "20240115100000"
_BUY_CID          = f"BT-{_FIXED_TS_LABEL}-SPY"
_CLOSE_CID        = "BC-20240115-SPY-CLOSE"


def _pass_recon_generate(rg_self):
    rg_self._output_dir.mkdir(parents=True, exist_ok=True)
    (rg_self._output_dir / "order_reconciliation.json").write_text(
        json.dumps({"overall_status": "PASS"})
    )


def _make_buy_config(
    *,
    preview_only: bool = False,
    selected_id: str = _BUY_CID,
    block_if_open_orders: bool = True,
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
            paper_block_if_open_orders=block_if_open_orders,
        ),
    )


def _make_close_config(*, block_if_open_orders: bool = True):
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
            paper_block_if_open_orders=block_if_open_orders,
        ),
    )


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


def _spy_position():
    return {"symbol": "SPY", "qty": 1.0, "market_value": 475.0,
            "avg_entry_price": 470.0, "current_price": 475.0, "unrealized_pl": 5.0}


def _run_buy(cfg, tmp_path, *, open_orders=None, submit_return=None):
    """Run the buy-submit flow with a controllable get_orders return value."""
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    fake_engine = mock.MagicMock()
    fake_engine.run.return_value = {
        "order_intents": [_make_buy_intent()],
        "metrics": {}, "trades": [], "equity_curve": [],
    }
    fake_engine._portfolio.positions = {}

    mock_client = mock.MagicMock()
    mock_client.get_orders.return_value = open_orders if open_orders is not None else []
    mock_submit = mock.MagicMock(return_value=submit_return or _make_buy_result())
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value={"ok": True, "account": {"status": "ACTIVE"},
                                         "positions": {}, "symbols": ["SPY"]}), \
         mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                           return_value=mock_client), \
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


def _run_close(cfg, tmp_path, *, open_orders=None, submit_return=None):
    """Run the close-submit flow with a controllable get_orders return value."""
    from src.execution.alpaca_broker import AlpacaBrokerAdapter
    import src.main

    mock_client = mock.MagicMock()
    mock_client.get_orders.return_value = open_orders if open_orders is not None else []
    mock_submit = mock.MagicMock(return_value=submit_return or _make_close_result())
    mock_cancel = mock.MagicMock()

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value={"ok": True, "account": {"status": "ACTIVE"},
                                         "positions": {"SPY": _spy_position()},
                                         "symbols": ["SPY"]}), \
         mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                           return_value=mock_client), \
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

class TestBuySubmitOpenOrderGuard:
    def test_no_open_orders_allows_submit(self, tmp_path):
        cfg = _make_buy_config()
        mock_submit, _ = _run_buy(cfg, tmp_path, open_orders=[])
        assert mock_submit.call_count == 1

    def test_open_spy_order_blocks_buy_before_submit(self, tmp_path):
        cfg = _make_buy_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}
        mock_client = mock.MagicMock()
        mock_client.get_orders.return_value = [_open_order("SPY")]
        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock_client), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="open paper order detected"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_open_qqq_order_does_not_block_spy_buy(self, tmp_path):
        cfg = _make_buy_config()
        mock_submit, _ = _run_buy(cfg, tmp_path, open_orders=[_open_order("QQQ")])
        assert mock_submit.call_count == 1

    def test_lookup_failure_blocks_before_submit(self, tmp_path):
        cfg = _make_buy_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}
        mock_client = mock.MagicMock()
        mock_client.get_orders.side_effect = Exception("connection error")
        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock_client), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="Open-order lookup failed"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_disabled_config_skips_guard(self, tmp_path):
        """With paper_block_if_open_orders=False, guard is skipped even with open orders."""
        cfg = _make_buy_config(block_if_open_orders=False)
        mock_submit, _ = _run_buy(cfg, tmp_path, open_orders=[_open_order("SPY")])
        assert mock_submit.call_count == 1

    def test_submit_exactly_once_when_allowed(self, tmp_path):
        cfg = _make_buy_config()
        mock_submit, _ = _run_buy(cfg, tmp_path, open_orders=[])
        assert mock_submit.call_count == 1

    def test_cancel_order_never_called(self, tmp_path):
        cfg = _make_buy_config()
        _, mock_cancel = _run_buy(cfg, tmp_path, open_orders=[])
        mock_cancel.assert_not_called()

    def test_preview_does_not_call_open_order_guard(self, tmp_path):
        """In preview mode, _get_client is never called for the open-order guard."""
        cfg = _make_buy_config(preview_only=True)
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [_make_buy_intent()],
            "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}
        mock_get_client = mock.MagicMock(side_effect=AssertionError(
            "guard must not run in preview"
        ))

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {}, "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client", mock_get_client), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _pass_recon_generate), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            src.main.main()  # must not raise

        mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests: close-submit
# ---------------------------------------------------------------------------

class TestCloseSubmitOpenOrderGuard:
    def test_open_spy_order_blocks_close_before_submit(self, tmp_path):
        cfg = _make_close_config()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        mock_client = mock.MagicMock()
        mock_client.get_orders.return_value = [_open_order("SPY")]
        mock_submit = mock.MagicMock()

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value={"ok": True, "account": {"status": "ACTIVE"},
                                             "positions": {"SPY": _spy_position()},
                                             "symbols": ["SPY"]}), \
             mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                               return_value=mock_client), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="open paper order detected"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_open_qqq_order_does_not_block_spy_close(self, tmp_path):
        cfg = _make_close_config()
        mock_submit, _ = _run_close(cfg, tmp_path, open_orders=[_open_order("QQQ")])
        assert mock_submit.call_count == 1

    def test_no_open_orders_allows_close_submit(self, tmp_path):
        cfg = _make_close_config()
        mock_submit, _ = _run_close(cfg, tmp_path, open_orders=[])
        assert mock_submit.call_count == 1

    def test_disabled_config_skips_guard_for_close(self, tmp_path):
        cfg = _make_close_config(block_if_open_orders=False)
        mock_submit, _ = _run_close(cfg, tmp_path, open_orders=[_open_order("SPY")])
        assert mock_submit.call_count == 1

    def test_cancel_order_never_called_in_close(self, tmp_path):
        cfg = _make_close_config()
        _, mock_cancel = _run_close(cfg, tmp_path, open_orders=[])
        mock_cancel.assert_not_called()
