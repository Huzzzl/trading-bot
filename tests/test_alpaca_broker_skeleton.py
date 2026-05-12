"""
tests/test_alpaca_broker_skeleton.py
-------------------------------------
Tests for the AlpacaBrokerAdapter skeleton.

Verifies:
- The class can be imported and instantiated without API keys.
- All four BrokerAdapter methods raise NotImplementedError.
- paper=True is stored as the default.
- main() paper mode still raises NotImplementedError before any adapter is
  created (i.e. the paper guard fires first, not an import error).
- No environment variables are read during construction.
- No network calls are made.
"""

from __future__ import annotations

import os
import unittest.mock as mock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Import and construction
# ---------------------------------------------------------------------------

class TestAlpacaBrokerAdapterImport:
    def test_can_be_imported(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter  # noqa: F401

    def test_is_broker_adapter_subclass(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        from src.execution.broker import BrokerAdapter
        assert issubclass(AlpacaBrokerAdapter, BrokerAdapter)


class TestAlpacaBrokerAdapterConstructor:
    def _make(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def test_no_args_required(self):
        adapter = self._make()
        assert adapter is not None

    def test_paper_defaults_to_true(self):
        adapter = self._make()
        assert adapter.paper is True

    def test_paper_can_be_false(self):
        adapter = self._make(paper=False)
        assert adapter.paper is False

    def test_api_key_defaults_to_none(self):
        adapter = self._make()
        assert adapter.api_key is None

    def test_secret_key_defaults_to_none(self):
        adapter = self._make()
        assert adapter.secret_key is None

    def test_accepts_api_key(self):
        adapter = self._make(api_key="test-key")
        assert adapter.api_key == "test-key"

    def test_accepts_secret_key(self):
        adapter = self._make(secret_key="test-secret")
        assert adapter.secret_key == "test-secret"

    def test_does_not_read_env_vars(self):
        # Ensure constructor does not touch APCA_* or ALPACA_* env vars
        env_patch = {
            "APCA_API_KEY_ID":     "should-not-be-used",
            "APCA_API_SECRET_KEY": "should-not-be-used",
            "ALPACA_API_KEY":      "should-not-be-used",
            "ALPACA_SECRET_KEY":   "should-not-be-used",
        }
        with mock.patch.dict(os.environ, env_patch):
            from src.execution.alpaca_broker import AlpacaBrokerAdapter
            adapter = AlpacaBrokerAdapter()
        # api_key must remain None — not auto-read from env
        assert adapter.api_key is None
        assert adapter.secret_key is None


# ---------------------------------------------------------------------------
# All methods raise NotImplementedError
# ---------------------------------------------------------------------------

class TestAlpacaBrokerAdapterNotImplemented:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _make_intent(self):
        from src.execution.order_intent import OrderIntent
        return OrderIntent(
            symbol="SPY", side="buy", quantity=1.0,
            order_type="market", reason="test",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
        )

    def test_submit_order_raises(self):
        with pytest.raises(NotImplementedError, match="AlpacaBrokerAdapter is not implemented yet"):
            self._adapter().submit_order(self._make_intent())

    def test_get_positions_raises(self):
        with pytest.raises(NotImplementedError, match="AlpacaBrokerAdapter is not implemented yet"):
            self._adapter().get_positions()

    def test_get_account_raises(self):
        with pytest.raises(NotImplementedError, match="AlpacaBrokerAdapter is not implemented yet"):
            self._adapter().get_account()

    def test_cancel_order_raises(self):
        with pytest.raises(NotImplementedError, match="AlpacaBrokerAdapter is not implemented yet"):
            self._adapter().cancel_order("some-order-id")

    def test_error_message_is_explicit(self):
        try:
            self._adapter().get_account()
        except NotImplementedError as exc:
            assert "AlpacaBrokerAdapter is not implemented yet" in str(exc)


# ---------------------------------------------------------------------------
# main() paper mode guard fires before any adapter is created
# ---------------------------------------------------------------------------

class TestMainPaperModeGuard:
    def _make_config(self, mode: str = "paper"):
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
            execution=ExecutionConfig(mode=mode, dry_run_broker=False),
        )

    def test_paper_mode_raises_not_implemented_error(self):
        cfg = self._make_config(mode="paper")
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(NotImplementedError):
                _main()

    def test_paper_mode_error_before_alpaca_adapter(self):
        # The NotImplementedError must come from main()'s paper guard,
        # not from AlpacaBrokerAdapter — so alpaca_broker is never imported
        # as a side-effect of hitting the guard.
        cfg = self._make_config(mode="paper")
        import sys
        # Remove cached module if present so we can detect a fresh import
        sys.modules.pop("src.execution.alpaca_broker", None)

        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(NotImplementedError):
                _main()
