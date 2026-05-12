"""
tests/test_alpaca_broker_skeleton.py
-------------------------------------
Tests for the AlpacaBrokerAdapter skeleton and safety-only layer.

Verifies:
- The class can be imported and instantiated without API keys.
- paper=False raises ValueError immediately.
- paper=True (default) is accepted.
- __init__ does not read environment variables.
- _validate_credentials uses constructor credentials when provided.
- _validate_credentials falls back to env vars when constructor credentials absent.
- Missing/empty credentials raise RuntimeError.
- _ensure_market_hours passes during weekday RTH.
- _ensure_market_hours rejects pre-market, after-hours, and weekends.
- All four BrokerAdapter methods still raise NotImplementedError.
- main() paper mode still raises NotImplementedError before any adapter is created.
- No network calls are made.
"""

from __future__ import annotations

import os
import unittest.mock as mock
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

_EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Import and subclass check
# ---------------------------------------------------------------------------

class TestAlpacaBrokerAdapterImport:
    def test_can_be_imported(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter  # noqa: F401

    def test_is_broker_adapter_subclass(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        from src.execution.broker import BrokerAdapter
        assert issubclass(AlpacaBrokerAdapter, BrokerAdapter)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestAlpacaBrokerAdapterConstructor:
    def _make(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def test_no_args_required(self):
        assert self._make() is not None

    def test_paper_defaults_to_true(self):
        assert self._make().paper is True

    def test_paper_false_raises_value_error(self):
        with pytest.raises(ValueError, match="Live trading is not supported"):
            self._make(paper=False)

    def test_api_key_defaults_to_none(self):
        assert self._make().api_key is None

    def test_secret_key_defaults_to_none(self):
        assert self._make().secret_key is None

    def test_accepts_api_key(self):
        assert self._make(api_key="test-key").api_key == "test-key"

    def test_accepts_secret_key(self):
        assert self._make(secret_key="test-secret").secret_key == "test-secret"

    def test_does_not_read_env_vars(self):
        env_patch = {
            "ALPACA_API_KEY":      "should-not-be-used",
            "ALPACA_SECRET_KEY":   "should-not-be-used",
        }
        with mock.patch.dict(os.environ, env_patch):
            from src.execution.alpaca_broker import AlpacaBrokerAdapter
            adapter = AlpacaBrokerAdapter()
        assert adapter.api_key    is None
        assert adapter.secret_key is None


# ---------------------------------------------------------------------------
# _validate_credentials
# ---------------------------------------------------------------------------

class TestValidateCredentials:
    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def test_uses_constructor_credentials_when_provided(self):
        adapter = self._adapter(api_key="my-key", secret_key="my-secret")
        key, secret = adapter._validate_credentials()
        assert key    == "my-key"
        assert secret == "my-secret"

    def test_reads_env_vars_when_no_constructor_credentials(self):
        adapter = self._adapter()
        with mock.patch.dict(os.environ, {
            "ALPACA_API_KEY":    "env-key",
            "ALPACA_SECRET_KEY": "env-secret",
        }):
            key, secret = adapter._validate_credentials()
        assert key    == "env-key"
        assert secret == "env-secret"

    def test_missing_api_key_raises_runtime_error(self):
        adapter = self._adapter()
        env = {"ALPACA_SECRET_KEY": "env-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._validate_credentials()

    def test_missing_secret_key_raises_runtime_error(self):
        adapter = self._adapter()
        env = {"ALPACA_API_KEY": "env-key"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._validate_credentials()

    def test_empty_api_key_raises_runtime_error(self):
        adapter = self._adapter()
        with mock.patch.dict(os.environ, {
            "ALPACA_API_KEY":    "",
            "ALPACA_SECRET_KEY": "env-secret",
        }):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._validate_credentials()

    def test_empty_secret_key_raises_runtime_error(self):
        adapter = self._adapter()
        with mock.patch.dict(os.environ, {
            "ALPACA_API_KEY":    "env-key",
            "ALPACA_SECRET_KEY": "",
        }):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._validate_credentials()

    def test_no_credentials_anywhere_raises_runtime_error(self):
        adapter = self._adapter()
        # Clear both env vars
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._validate_credentials()

    def test_constructor_credentials_take_precedence_over_env(self):
        adapter = self._adapter(api_key="ctor-key", secret_key="ctor-secret")
        with mock.patch.dict(os.environ, {
            "ALPACA_API_KEY":    "env-key",
            "ALPACA_SECRET_KEY": "env-secret",
        }):
            key, secret = adapter._validate_credentials()
        assert key    == "ctor-key"
        assert secret == "ctor-secret"

    def test_validate_not_called_by_init(self):
        # Constructing without credentials and without env vars must not raise
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        with mock.patch.dict(os.environ, clean, clear=True):
            adapter = AlpacaBrokerAdapter()   # must not raise
        assert adapter is not None


# ---------------------------------------------------------------------------
# _ensure_market_hours
# ---------------------------------------------------------------------------

class TestEnsureMarketHours:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _dt(self, weekday_offset: int, hour: int, minute: int) -> datetime:
        """Build a Monday+offset datetime in ET for the given time."""
        # 2024-01-15 is a Monday
        from datetime import date, timedelta
        d = date(2024, 1, 15) + timedelta(days=weekday_offset)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=_EASTERN)

    # --- RTH passes ---

    def test_market_open_passes(self):
        self._adapter()._ensure_market_hours(self._dt(0, 9, 30))   # 09:30 Mon

    def test_midday_passes(self):
        self._adapter()._ensure_market_hours(self._dt(0, 12, 0))   # 12:00 Mon

    def test_last_valid_minute_passes(self):
        self._adapter()._ensure_market_hours(self._dt(0, 15, 59))  # 15:59 Mon

    def test_friday_rth_passes(self):
        self._adapter()._ensure_market_hours(self._dt(4, 10, 0))   # 10:00 Fri

    # --- Pre-market rejected ---

    def test_premarket_rejected(self):
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(self._dt(0, 9, 29))  # 09:29 Mon

    def test_midnight_rejected(self):
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(self._dt(0, 0, 0))

    # --- After-hours rejected ---

    def test_market_close_exact_rejected(self):
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(self._dt(0, 16, 0))  # 16:00 Mon

    def test_after_hours_rejected(self):
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(self._dt(0, 17, 0))

    # --- Weekend rejected ---

    def test_saturday_rejected(self):
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(self._dt(5, 10, 0))  # Sat

    def test_sunday_rejected(self):
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(self._dt(6, 10, 0))  # Sun

    def test_uses_eastern_timezone(self):
        # A UTC time that is within ET RTH on Monday
        from datetime import timezone
        # 14:00 UTC = 09:00 ET (pre-market) on 2024-01-15
        dt_utc = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            self._adapter()._ensure_market_hours(dt_utc)


# ---------------------------------------------------------------------------
# Public broker methods still raise NotImplementedError
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
        cfg = self._make_config(mode="paper")
        import sys
        sys.modules.pop("src.execution.alpaca_broker", None)
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(NotImplementedError):
                _main()
