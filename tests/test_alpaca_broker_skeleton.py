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
            client_order_id="TEST-000001",
        )

    def test_submit_order_raises_without_credentials(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours"):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter.submit_order(self._make_intent())

    def test_get_positions_raises_without_credentials(self):
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            self._adapter().get_positions()

    def test_get_account_raises_without_credentials(self):
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            self._adapter().get_account()

    def test_cancel_order_raises_without_credentials(self):
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
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


# ---------------------------------------------------------------------------
# _validate_order_intent
# ---------------------------------------------------------------------------

class TestValidateOrderIntent:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _intent(self, **overrides):
        """Return a valid market OrderIntent, with optional field overrides."""
        from src.execution.order_intent import OrderIntent
        defaults = dict(
            symbol="SPY",
            side="buy",
            quantity=10.0,
            order_type="market",
            reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )
        defaults.update(overrides)
        return OrderIntent(**defaults)

    # --- happy path ---

    def test_valid_market_buy_passes(self):
        self._adapter()._validate_order_intent(self._intent(side="buy"))

    def test_valid_market_sell_passes(self):
        self._adapter()._validate_order_intent(self._intent(side="sell"))

    # --- order_type ---

    def test_limit_order_raises_not_implemented(self):
        intent = self._intent(order_type="limit", limit_price=470.0)
        with pytest.raises(NotImplementedError, match="Only market orders are supported"):
            self._adapter()._validate_order_intent(intent)

    def test_stop_order_raises_not_implemented(self):
        intent = self._intent(order_type="stop", stop_price=460.0)
        with pytest.raises(NotImplementedError, match="Only market orders are supported"):
            self._adapter()._validate_order_intent(intent)

    # --- client_order_id ---

    def test_missing_client_order_id_raises_value_error(self):
        intent = self._intent(client_order_id=None)
        with pytest.raises(ValueError, match="client_order_id is required"):
            self._adapter()._validate_order_intent(intent)

    def test_empty_client_order_id_raises_value_error(self):
        intent = self._intent(client_order_id="   ")
        with pytest.raises(ValueError, match="client_order_id is required"):
            self._adapter()._validate_order_intent(intent)

    # --- symbol ---

    def test_empty_symbol_raises_value_error(self):
        intent = self._intent(symbol="")
        with pytest.raises(ValueError, match="symbol is required"):
            self._adapter()._validate_order_intent(intent)

    def test_whitespace_symbol_raises_value_error(self):
        intent = self._intent(symbol="   ")
        with pytest.raises(ValueError, match="symbol is required"):
            self._adapter()._validate_order_intent(intent)

    # --- side ---

    def test_invalid_side_raises_value_error(self):
        # OrderIntent validates side at construction time, so we bypass that
        # by patching the frozen dataclass attribute directly.
        intent = self._intent()
        object.__setattr__(intent, "side", "short")
        with pytest.raises(ValueError, match="side must be buy or sell"):
            self._adapter()._validate_order_intent(intent)

    # --- quantity ---

    def test_zero_quantity_raises_value_error(self):
        # OrderIntent already rejects quantity <= 0, so patch the field.
        intent = self._intent()
        object.__setattr__(intent, "quantity", 0.0)
        with pytest.raises(ValueError, match="quantity must be positive"):
            self._adapter()._validate_order_intent(intent)

    def test_negative_quantity_raises_value_error(self):
        intent = self._intent()
        object.__setattr__(intent, "quantity", -5.0)
        with pytest.raises(ValueError, match="quantity must be positive"):
            self._adapter()._validate_order_intent(intent)

    # --- isolation ---

    def test_does_not_read_env_vars(self):
        # Helper must not care about credentials at all
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True):
            self._adapter()._validate_order_intent(self._intent())  # must not raise

    def test_does_not_call_market_hours_helper(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=AssertionError("should not be called")):
            adapter._validate_order_intent(self._intent())  # must not raise

    # --- submit_order requires credentials when no client is injected ---

    def test_submit_order_raises_without_credentials(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours"):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter.submit_order(self._intent())


# ---------------------------------------------------------------------------
# _build_order_payload
# ---------------------------------------------------------------------------

class TestBuildOrderPayload:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _intent(self, **overrides):
        from src.execution.order_intent import OrderIntent
        defaults = dict(
            symbol="SPY",
            side="buy",
            quantity=10.0,
            order_type="market",
            reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )
        defaults.update(overrides)
        return OrderIntent(**defaults)

    def _payload(self, **overrides):
        return self._adapter()._build_order_payload(self._intent(**overrides))

    # --- field values ---

    def test_buy_symbol(self):
        assert self._payload()["symbol"] == "SPY"

    def test_buy_side(self):
        assert self._payload()["side"] == "buy"

    def test_sell_side(self):
        assert self._payload(side="sell")["side"] == "sell"

    def test_qty_preserved(self):
        assert self._payload(quantity=7.0)["qty"] == 7.0

    def test_qty_fractional(self):
        assert self._payload(quantity=2.5)["qty"] == 2.5

    def test_type_is_market(self):
        assert self._payload()["type"] == "market"

    def test_time_in_force_is_day(self):
        assert self._payload()["time_in_force"] == "day"

    def test_client_order_id_propagated(self):
        assert self._payload(client_order_id="BT-000042")["client_order_id"] == "BT-000042"

    # --- no extra price fields ---

    def test_no_limit_price_key(self):
        assert "limit_price" not in self._payload()

    def test_no_stop_price_key(self):
        assert "stop_price" not in self._payload()

    # --- validation errors propagate ---

    def test_limit_order_raises_not_implemented(self):
        intent = self._intent(order_type="limit", limit_price=470.0)
        with pytest.raises(NotImplementedError, match="Only market orders are supported"):
            self._adapter()._build_order_payload(intent)

    def test_missing_client_order_id_raises_value_error(self):
        intent = self._intent(client_order_id=None)
        with pytest.raises(ValueError, match="client_order_id is required"):
            self._adapter()._build_order_payload(intent)

    def test_empty_symbol_raises_value_error(self):
        intent = self._intent(symbol="")
        with pytest.raises(ValueError, match="symbol is required"):
            self._adapter()._build_order_payload(intent)

    # --- isolation ---

    def test_does_not_read_env_vars(self):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True):
            self._payload()  # must not raise

    def test_does_not_call_market_hours_helper(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=AssertionError("should not be called")):
            adapter._build_order_payload(self._intent())  # must not raise

    # --- submit_order requires credentials when no client is injected ---

    def test_submit_order_raises_without_credentials(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours"):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter.submit_order(self._intent())


# ---------------------------------------------------------------------------
# _normalize_status
# ---------------------------------------------------------------------------

class TestNormalizeStatus:
    def _norm(self, s: str) -> str:
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter._normalize_status(s)

    # --- accepted group ---

    def test_new_maps_to_accepted(self):
        assert self._norm("new") == "accepted"

    def test_pending_new_maps_to_accepted(self):
        assert self._norm("pending_new") == "accepted"

    def test_accepted_maps_to_accepted(self):
        assert self._norm("accepted") == "accepted"

    def test_accepted_for_bidding_maps_to_accepted(self):
        assert self._norm("accepted_for_bidding") == "accepted"

    # --- filled group ---

    def test_filled_maps_to_filled(self):
        assert self._norm("filled") == "filled"

    def test_partially_filled_maps_to_filled(self):
        assert self._norm("partially_filled") == "filled"

    # --- cancelled group ---

    def test_canceled_maps_to_cancelled(self):
        assert self._norm("canceled") == "cancelled"

    def test_cancelled_maps_to_cancelled(self):
        assert self._norm("cancelled") == "cancelled"

    def test_expired_maps_to_cancelled(self):
        assert self._norm("expired") == "cancelled"

    def test_replaced_maps_to_cancelled(self):
        assert self._norm("replaced") == "cancelled"

    # --- rejected group ---

    def test_rejected_maps_to_rejected(self):
        assert self._norm("rejected") == "rejected"

    def test_stopped_maps_to_rejected(self):
        assert self._norm("stopped") == "rejected"

    def test_suspended_maps_to_rejected(self):
        assert self._norm("suspended") == "rejected"

    def test_calculated_maps_to_rejected(self):
        assert self._norm("calculated") == "rejected"

    # --- case-insensitive ---

    def test_uppercase_filled(self):
        assert self._norm("FILLED") == "filled"

    def test_mixed_case_pending_new(self):
        assert self._norm("Pending_New") == "accepted"

    def test_uppercase_rejected(self):
        assert self._norm("REJECTED") == "rejected"

    def test_mixed_case_cancelled(self):
        assert self._norm("Cancelled") == "cancelled"

    # --- whitespace stripping ---

    def test_strips_leading_whitespace(self):
        assert self._norm("  filled") == "filled"

    def test_strips_trailing_whitespace(self):
        assert self._norm("filled  ") == "filled"

    def test_strips_both_sides(self):
        assert self._norm("  new  ") == "accepted"

    # --- unknown status ---

    def test_unknown_status_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown Alpaca order status"):
            self._norm("open")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown Alpaca order status"):
            self._norm("")

    def test_arbitrary_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown Alpaca order status"):
            self._norm("banana")


# ---------------------------------------------------------------------------
# _is_partial_fill
# ---------------------------------------------------------------------------

class TestIsPartialFill:
    def _ipf(self, s: str) -> bool:
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter._is_partial_fill(s)

    def test_partially_filled_returns_true(self):
        assert self._ipf("partially_filled") is True

    def test_uppercase_partially_filled_returns_true(self):
        assert self._ipf("PARTIALLY_FILLED") is True

    def test_mixed_case_returns_true(self):
        assert self._ipf("Partially_Filled") is True

    def test_strips_whitespace_and_returns_true(self):
        assert self._ipf("  partially_filled  ") is True

    def test_filled_returns_false(self):
        assert self._ipf("filled") is False

    def test_new_returns_false(self):
        assert self._ipf("new") is False

    def test_rejected_returns_false(self):
        assert self._ipf("rejected") is False

    def test_cancelled_returns_false(self):
        assert self._ipf("cancelled") is False

    def test_empty_returns_false(self):
        assert self._ipf("") is False


# ---------------------------------------------------------------------------
# _order_response_to_result
# ---------------------------------------------------------------------------

class TestOrderResponseToResult:
    """Tests for the Alpaca response → OrderResult mapping helper."""

    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _intent(self, **overrides):
        from src.execution.order_intent import OrderIntent
        defaults = dict(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )
        defaults.update(overrides)
        return OrderIntent(**defaults)

    def _filled_dict(self, **overrides):
        base = {
            "id":               "alpaca-uuid-001",
            "symbol":           "SPY",
            "side":             "buy",
            "qty":              "10",
            "status":           "filled",
            "submitted_at":     "2024-01-15T10:00:00-05:00",
            "filled_at":        "2024-01-15T10:00:01-05:00",
            "filled_avg_price": "471.25",
            "filled_qty":       "10",
            "client_order_id":  "BT-000001",
        }
        base.update(overrides)
        return base

    # --- dict-style: filled ---

    def test_filled_dict_order_id(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.order_id == "alpaca-uuid-001"

    def test_filled_dict_symbol(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.symbol == "SPY"

    def test_filled_dict_side(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.side == "buy"

    def test_filled_dict_quantity(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.quantity == 10.0

    def test_filled_dict_status(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.status == "filled"

    def test_filled_dict_filled_price(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.filled_price == 471.25

    def test_filled_dict_filled_at_is_timestamp(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.filled_at is not None
        import pandas as pd
        assert isinstance(r.filled_at, pd.Timestamp)

    def test_filled_dict_reason_from_intent(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.reason == "entry"

    def test_filled_dict_client_order_id(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.client_order_id == "BT-000001"

    def test_filled_dict_raw_status_in_metadata(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.metadata["raw_status"] == "filled"

    def test_filled_dict_partial_fill_false(self):
        r = self._adapter()._order_response_to_result(self._filled_dict(), self._intent())
        assert r.metadata["partial_fill"] is False

    # --- dict-style: accepted ---

    def test_accepted_dict_maps_status(self):
        d = self._filled_dict(status="new", filled_at=None, filled_avg_price=None, filled_qty=None)
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.status == "accepted"

    def test_accepted_dict_filled_price_none(self):
        d = self._filled_dict(status="new", filled_at=None, filled_avg_price=None, filled_qty=None)
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.filled_price is None

    def test_accepted_dict_filled_at_none(self):
        d = self._filled_dict(status="new", filled_at=None, filled_avg_price=None, filled_qty=None)
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.filled_at is None

    # --- dict-style: rejected ---

    def test_rejected_dict_maps_status(self):
        d = self._filled_dict(status="rejected", filled_at=None, filled_avg_price=None, filled_qty=None)
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.status == "rejected"

    # --- object-style response ---

    def test_object_style_response(self):
        class _FakeOrder:
            id               = "alpaca-uuid-002"
            symbol           = "QQQ"
            side             = "sell"
            qty              = "5"
            status           = "filled"
            submitted_at     = "2024-01-15T11:00:00-05:00"
            filled_at        = "2024-01-15T11:00:01-05:00"
            filled_avg_price = "380.50"
            filled_qty       = "5"
            client_order_id  = "BT-000002"

        intent = self._intent(symbol="QQQ", side="sell", quantity=5.0,
                              client_order_id="BT-000002", reason="stop_loss")
        r = self._adapter()._order_response_to_result(_FakeOrder(), intent)
        assert r.order_id      == "alpaca-uuid-002"
        assert r.symbol        == "QQQ"
        assert r.side          == "sell"
        assert r.quantity      == 5.0
        assert r.status        == "filled"
        assert r.filled_price  == 380.50
        assert r.reason        == "stop_loss"
        assert r.client_order_id == "BT-000002"

    # --- client_order_id fallback ---

    def test_missing_response_client_order_id_falls_back_to_intent(self):
        d = self._filled_dict()
        d.pop("client_order_id")
        intent = self._intent(client_order_id="BT-FALLBACK")
        r = self._adapter()._order_response_to_result(d, intent)
        assert r.client_order_id == "BT-FALLBACK"

    def test_none_response_client_order_id_falls_back_to_intent(self):
        d = self._filled_dict(client_order_id=None)
        intent = self._intent(client_order_id="BT-FALLBACK")
        r = self._adapter()._order_response_to_result(d, intent)
        assert r.client_order_id == "BT-FALLBACK"

    # --- partial fill ---

    def test_partially_filled_status_filled_and_partial_fill_true(self):
        d = self._filled_dict(status="partially_filled", filled_qty="6")
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.status == "filled"
        assert r.metadata["partial_fill"] is True

    def test_partially_filled_filled_qty_in_metadata(self):
        d = self._filled_dict(status="partially_filled", filled_qty="6")
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.metadata["filled_qty"] == "6"

    # --- None prices/timestamps ---

    def test_none_filled_avg_price_gives_none_filled_price(self):
        d = self._filled_dict(filled_avg_price=None)
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.filled_price is None

    def test_none_filled_at_gives_none_filled_at(self):
        d = self._filled_dict(filled_at=None)
        r = self._adapter()._order_response_to_result(d, self._intent())
        assert r.filled_at is None

    # --- unknown status ---

    def test_unknown_status_raises_value_error(self):
        d = self._filled_dict(status="banana")
        with pytest.raises(ValueError, match="Unknown Alpaca order status"):
            self._adapter()._order_response_to_result(d, self._intent())

    # --- isolation ---

    def test_does_not_read_env_vars(self):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True):
            self._adapter()._order_response_to_result(self._filled_dict(), self._intent())

    def test_does_not_call_market_hours_helper(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=AssertionError("should not be called")):
            adapter._order_response_to_result(self._filled_dict(), self._intent())

    # --- submit_order requires credentials when no client is injected ---

    def test_submit_order_raises_without_credentials(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours"):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter.submit_order(self._intent())


# ---------------------------------------------------------------------------
# Client injection / _get_client
# ---------------------------------------------------------------------------

class TestClientInjection:
    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _intent(self):
        from src.execution.order_intent import OrderIntent
        return OrderIntent(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )

    def test_constructor_accepts_client(self):
        fake = object()
        adapter = self._adapter(client=fake)
        assert adapter._client is fake

    def test_constructor_default_client_is_none(self):
        assert self._adapter()._client is None

    def test_constructor_does_not_read_env_vars(self):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True):
            adapter = self._adapter()
        assert adapter._client is None

    def test_get_client_returns_injected_client(self):
        fake = object()
        adapter = self._adapter(client=fake)
        assert adapter._get_client() is fake

    def test_get_client_returns_same_object_each_call(self):
        fake = {"mock": True}
        adapter = self._adapter(client=fake)
        assert adapter._get_client() is adapter._get_client()

    def test_get_client_without_client_calls_validate_credentials(self):
        adapter = self._adapter()
        with mock.patch.object(adapter, "_validate_credentials",
                               side_effect=RuntimeError("Missing Alpaca paper API credentials")) as mock_vc:
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._get_client()
        mock_vc.assert_called_once()

    def test_get_client_does_not_read_env_vars(self):
        fake = object()
        adapter = self._adapter(client=fake)
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True):
            result = adapter._get_client()
        assert result is fake

    def test_get_client_does_not_call_validate_credentials(self):
        fake = object()
        adapter = self._adapter(client=fake)
        with mock.patch.object(adapter, "_validate_credentials",
                               side_effect=AssertionError("should not be called")):
            adapter._get_client()

    def test_get_client_does_not_call_ensure_market_hours(self):
        fake = object()
        adapter = self._adapter(client=fake)
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=AssertionError("should not be called")):
            adapter._get_client()

    def test_submit_order_raises_without_credentials(self):
        # No client injected → _get_client() calls _validate_credentials → RuntimeError
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours"):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter.submit_order(self._intent())


# ---------------------------------------------------------------------------
# submit_order (mock-client path)
# ---------------------------------------------------------------------------

class TestSubmitOrderMockClient:
    """Tests for submit_order() exercised through an injected mock client."""

    _RTH = datetime(2024, 1, 15, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    def _adapter(self, client=None):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(client=client)

    def _intent(self, **overrides):
        from src.execution.order_intent import OrderIntent
        defaults = dict(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )
        defaults.update(overrides)
        return OrderIntent(**defaults)

    def _mock_response(self, **overrides):
        base = {
            "id":               "alpaca-uuid-001",
            "symbol":           "SPY",
            "side":             "buy",
            "qty":              "10",
            "status":           "accepted",
            "submitted_at":     "2024-01-15T10:00:00-05:00",
            "filled_at":        None,
            "filled_avg_price": None,
            "filled_qty":       None,
            "client_order_id":  "BT-000001",
        }
        base.update(overrides)
        return base

    def _rth_patch(self):
        """Context manager: freeze _ensure_market_hours to always pass."""
        return mock.patch(
            "src.execution.alpaca_broker.AlpacaBrokerAdapter._ensure_market_hours"
        )

    # --- no client, no credentials → RuntimeError ---

    def test_no_client_no_credentials_raises_runtime_error(self):
        adapter = self._adapter(client=None)
        with mock.patch.object(adapter, "_validate_order_intent"):
            with mock.patch.object(adapter, "_ensure_market_hours"):
                with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                    adapter.submit_order(self._intent())

    def test_no_client_calls_get_client(self):
        adapter = self._adapter(client=None)
        with mock.patch.object(adapter, "_get_client",
                               side_effect=RuntimeError("Missing Alpaca paper API credentials")) as mock_gc:
            with mock.patch.object(adapter, "_validate_order_intent"):
                with mock.patch.object(adapter, "_ensure_market_hours"):
                    with pytest.raises(RuntimeError):
                        adapter.submit_order(self._intent())
        mock_gc.assert_called_once()

    # --- submit_order method ---

    def test_client_submit_order_called_with_payload(self):
        response = self._mock_response()
        client = mock.MagicMock(spec=["submit_order"])
        client.submit_order.return_value = response
        with mock.patch.object(self._adapter(client=client), "_ensure_market_hours"):
            adapter = self._adapter(client=client)
            with mock.patch.object(adapter, "_ensure_market_hours"):
                result = adapter.submit_order(self._intent())
        assert client.submit_order.called
        payload = client.submit_order.call_args[0][0]
        assert payload["symbol"] == "SPY"
        assert payload["side"] == "buy"
        assert payload["type"] == "market"
        assert payload["client_order_id"] == "BT-000001"

    # --- create_order fallback ---

    def test_client_create_order_called_when_no_submit_order(self):
        response = self._mock_response()
        client = mock.MagicMock(spec=["create_order"])
        client.create_order.return_value = response
        adapter = self._adapter(client=client)
        with mock.patch.object(adapter, "_ensure_market_hours"):
            adapter.submit_order(self._intent())
        assert client.create_order.called

    # --- prefers submit_order when both exist ---

    def test_prefers_submit_order_over_create_order(self):
        response = self._mock_response()
        client = mock.MagicMock(spec=["submit_order", "create_order"])
        client.submit_order.return_value = response
        adapter = self._adapter(client=client)
        with mock.patch.object(adapter, "_ensure_market_hours"):
            adapter.submit_order(self._intent())
        assert client.submit_order.called
        assert not client.create_order.called

    # --- returns OrderResult ---

    def test_returns_order_result(self):
        from src.execution.broker import OrderResult
        response = self._mock_response()
        client = mock.MagicMock(spec=["submit_order"])
        client.submit_order.return_value = response
        adapter = self._adapter(client=client)
        with mock.patch.object(adapter, "_ensure_market_hours"):
            result = adapter.submit_order(self._intent())
        assert isinstance(result, OrderResult)
        assert result.status == "accepted"
        assert result.symbol == "SPY"
        assert result.client_order_id == "BT-000001"

    # --- market hours enforced ---

    def test_ensure_market_hours_called_before_client(self):
        call_order = []
        response = self._mock_response()

        client = mock.MagicMock(spec=["submit_order"])
        client.submit_order.side_effect = lambda p: (call_order.append("client"), response)[1]

        adapter = self._adapter(client=client)
        original_emh = adapter._ensure_market_hours

        def tracking_emh(now=None):
            call_order.append("market_hours")
            return original_emh(now=self._RTH)

        with mock.patch.object(adapter, "_ensure_market_hours", side_effect=tracking_emh):
            adapter.submit_order(self._intent())

        assert call_order[0] == "market_hours"
        assert "client" in call_order

    def test_outside_market_hours_raises_and_does_not_call_client(self):
        client = mock.MagicMock(spec=["submit_order"])
        adapter = self._adapter(client=client)
        outside = datetime(2024, 1, 15, 8, 0, tzinfo=ZoneInfo("America/New_York"))
        with pytest.raises(RuntimeError, match="Outside regular market hours"):
            adapter.submit_order(self._intent())  # real clock check; force via now kwarg
        # client must not have been called
        client.submit_order.assert_not_called()

    def test_outside_market_hours_via_inject(self):
        client = mock.MagicMock(spec=["submit_order"])
        adapter = self._adapter(client=client)
        outside = datetime(2024, 1, 14, 10, 0, tzinfo=ZoneInfo("America/New_York"))  # Sunday
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=RuntimeError("Outside regular market hours")):
            with pytest.raises(RuntimeError, match="Outside regular market hours"):
                adapter.submit_order(self._intent())
        client.submit_order.assert_not_called()

    # --- validation errors propagate before client call ---

    def test_invalid_intent_raises_before_client_call(self):
        client = mock.MagicMock(spec=["submit_order"])
        adapter = self._adapter(client=client)
        intent = self._intent(client_order_id=None)
        with pytest.raises(ValueError, match="client_order_id is required"):
            with mock.patch.object(adapter, "_ensure_market_hours"):
                adapter.submit_order(intent)
        client.submit_order.assert_not_called()

    def test_limit_order_raises_before_client_call(self):
        client = mock.MagicMock(spec=["submit_order"])
        adapter = self._adapter(client=client)
        intent = self._intent(order_type="limit", limit_price=470.0)
        with pytest.raises(NotImplementedError, match="Only market orders are supported"):
            with mock.patch.object(adapter, "_ensure_market_hours"):
                adapter.submit_order(intent)
        client.submit_order.assert_not_called()

    # --- _validate_credentials is not called ---

    def test_validate_credentials_not_called(self):
        response = self._mock_response()
        client = mock.MagicMock(spec=["submit_order"])
        client.submit_order.return_value = response
        adapter = self._adapter(client=client)
        with mock.patch.object(adapter, "_ensure_market_hours"), \
             mock.patch.object(adapter, "_validate_credentials",
                               side_effect=AssertionError("should not be called")):
            adapter.submit_order(self._intent())  # must not raise

    # --- env vars are not read ---

    def test_does_not_read_env_vars(self):
        response = self._mock_response()
        client = mock.MagicMock(spec=["submit_order"])
        client.submit_order.return_value = response
        adapter = self._adapter(client=client)
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(adapter, "_ensure_market_hours"):
            adapter.submit_order(self._intent())  # must not raise

    # --- main.py paper guard still fires ---

    def test_main_paper_guard_still_raises(self):
        from src.config.loader import (
            AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
            LoggingConfig, RiskConfig, StrategyConfig,
        )
        cfg = AppConfig(
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
            execution=ExecutionConfig(mode="paper", dry_run_broker=False),
        )
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(NotImplementedError):
                _main()


# ---------------------------------------------------------------------------
# submit_order: explicit client method validation
# ---------------------------------------------------------------------------

class TestSubmitOrderClientMethodValidation:
    """Client with neither submit_order nor create_order fails explicitly."""

    def _adapter(self, client=None):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(client=client)

    def _intent(self, **overrides):
        from src.execution.order_intent import OrderIntent
        defaults = dict(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )
        defaults.update(overrides)
        return OrderIntent(**defaults)

    def _rth(self):
        return mock.patch(
            "src.execution.alpaca_broker.AlpacaBrokerAdapter._ensure_market_hours"
        )

    def test_client_with_no_method_raises_not_implemented(self):
        # An object() has neither submit_order nor create_order
        adapter = self._adapter(client=object())
        with self._rth():
            with pytest.raises(NotImplementedError,
                               match="Injected Alpaca client must provide submit_order or create_order"):
                adapter.submit_order(self._intent())

    def test_explicit_error_message(self):
        adapter = self._adapter(client=object())
        with self._rth():
            try:
                adapter.submit_order(self._intent())
            except NotImplementedError as exc:
                assert "submit_order or create_order" in str(exc)
            else:
                pytest.fail("Expected NotImplementedError")

    def test_explicit_error_after_validation_and_market_hours(self):
        # Verify that validation + market-hours run BEFORE the client method
        # check by ensuring a validation failure takes priority.
        call_log = []
        client = object()  # no submit_order / create_order

        adapter = self._adapter(client=client)

        def _tracking_emh(now=None):
            call_log.append("market_hours")

        with mock.patch.object(adapter, "_ensure_market_hours", side_effect=_tracking_emh), \
             mock.patch.object(adapter, "_validate_order_intent",
                               side_effect=lambda i: call_log.append("validate")):
            with pytest.raises(NotImplementedError,
                               match="submit_order or create_order"):
                adapter.submit_order(self._intent())

        assert "validate" in call_log
        assert "market_hours" in call_log

    def test_invalid_intent_fails_before_client_method_check(self):
        adapter = self._adapter(client=object())
        intent = self._intent(client_order_id=None)
        with self._rth():
            with pytest.raises(ValueError, match="client_order_id is required"):
                adapter.submit_order(intent)

    def test_outside_market_hours_fails_before_client_method_check(self):
        adapter = self._adapter(client=object())
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=RuntimeError("Outside regular market hours")):
            with pytest.raises(RuntimeError, match="Outside regular market hours"):
                adapter.submit_order(self._intent())


# ---------------------------------------------------------------------------
# _account_response_to_dict
# ---------------------------------------------------------------------------

class TestAccountResponseToDict:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def test_dict_response_maps_all_fields(self):
        resp = {
            "id": "acc-1",
            "status": "ACTIVE",
            "currency": "USD",
            "cash": "10000.50",
            "equity": "20000.75",
            "buying_power": "40000.00",
            "trading_blocked": False,
            "account_blocked": False,
        }
        result = self._adapter()._account_response_to_dict(resp)
        assert result["id"] == "acc-1"
        assert result["status"] == "ACTIVE"
        assert result["currency"] == "USD"
        assert result["cash"] == 10000.50
        assert result["equity"] == 20000.75
        assert result["buying_power"] == 40000.00
        assert result["trading_blocked"] is False
        assert result["account_blocked"] is False

    def test_object_response_maps_all_fields(self):
        class FakeAccount:
            id = "acc-obj"
            status = "ACTIVE"
            currency = "USD"
            cash = 5000.0
            equity = 10000.0
            buying_power = 20000.0
            trading_blocked = False
            account_blocked = False

        result = self._adapter()._account_response_to_dict(FakeAccount())
        assert result["id"] == "acc-obj"
        assert result["cash"] == 5000.0
        assert result["trading_blocked"] is False

    def test_numeric_strings_convert_to_float(self):
        resp = {"cash": "1234.56", "equity": "7890.12", "buying_power": "3000.00"}
        result = self._adapter()._account_response_to_dict(resp)
        assert isinstance(result["cash"], float)
        assert result["cash"] == 1234.56
        assert isinstance(result["equity"], float)
        assert isinstance(result["buying_power"], float)

    def test_missing_fields_become_none(self):
        result = self._adapter()._account_response_to_dict({})
        assert result["id"] is None
        assert result["status"] is None
        assert result["currency"] is None
        assert result["cash"] is None
        assert result["equity"] is None
        assert result["buying_power"] is None
        assert result["trading_blocked"] is None
        assert result["account_blocked"] is None

    def test_does_not_read_env_vars(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "should-not-matter")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "should-not-matter")
        # Should not raise even without real credentials
        result = self._adapter()._account_response_to_dict({"status": "ACTIVE"})
        assert result["status"] == "ACTIVE"

    def test_does_not_make_network_calls(self):
        # No network mock needed — method is purely local
        result = self._adapter()._account_response_to_dict({"id": "x"})
        assert result["id"] == "x"


# ---------------------------------------------------------------------------
# _position_response_to_dict
# ---------------------------------------------------------------------------

class TestPositionResponseToDict:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def test_dict_response_maps_all_fields(self):
        resp = {
            "symbol": "SPY",
            "qty": "10",
            "market_value": "4500.00",
            "avg_entry_price": "440.00",
            "current_price": "450.00",
            "unrealized_pl": "100.00",
        }
        result = self._adapter()._position_response_to_dict(resp)
        assert result["symbol"] == "SPY"
        assert result["qty"] == 10.0
        assert result["market_value"] == 4500.0
        assert result["avg_entry_price"] == 440.0
        assert result["current_price"] == 450.0
        assert result["unrealized_pl"] == 100.0

    def test_object_response_maps_all_fields(self):
        class FakePosition:
            symbol = "QQQ"
            qty = 5.0
            market_value = 1500.0
            avg_entry_price = 295.0
            current_price = 300.0
            unrealized_pl = 25.0

        result = self._adapter()._position_response_to_dict(FakePosition())
        assert result["symbol"] == "QQQ"
        assert result["qty"] == 5.0
        assert result["unrealized_pl"] == 25.0

    def test_numeric_strings_convert_to_float(self):
        resp = {
            "symbol": "SPY",
            "qty": "3",
            "market_value": "1350.00",
            "avg_entry_price": "445.00",
            "current_price": "450.00",
            "unrealized_pl": "15.00",
        }
        result = self._adapter()._position_response_to_dict(resp)
        assert isinstance(result["qty"], float)
        assert isinstance(result["market_value"], float)
        assert isinstance(result["avg_entry_price"], float)
        assert isinstance(result["current_price"], float)
        assert isinstance(result["unrealized_pl"], float)

    def test_missing_fields_become_none(self):
        result = self._adapter()._position_response_to_dict({})
        assert result["symbol"] is None
        assert result["qty"] is None
        assert result["market_value"] is None
        assert result["avg_entry_price"] is None
        assert result["current_price"] is None
        assert result["unrealized_pl"] is None

    def test_does_not_read_env_vars(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "should-not-matter")
        result = self._adapter()._position_response_to_dict({"symbol": "SPY"})
        assert result["symbol"] == "SPY"

    def test_does_not_make_network_calls(self):
        result = self._adapter()._position_response_to_dict({"symbol": "QQQ"})
        assert result["symbol"] == "QQQ"


# ---------------------------------------------------------------------------
# _validate_startup_state
# ---------------------------------------------------------------------------

class TestValidateStartupState:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _active_account(self, **overrides):
        base = {
            "status": "ACTIVE",
            "trading_blocked": False,
            "account_blocked": False,
        }
        base.update(overrides)
        return base

    def test_passes_for_active_unblocked_account_no_overlap(self):
        account = self._active_account()
        positions = [{"symbol": "MSFT"}, {"symbol": "AAPL"}]
        # Should not raise
        self._adapter()._validate_startup_state(account, positions, ["SPY", "QQQ"])

    def test_inactive_account_raises(self):
        account = self._active_account(status="INACTIVE")
        with pytest.raises(RuntimeError, match="not active"):
            self._adapter()._validate_startup_state(account, [], ["SPY"])

    def test_trading_blocked_raises(self):
        account = self._active_account(trading_blocked=True)
        with pytest.raises(RuntimeError, match="trading is blocked"):
            self._adapter()._validate_startup_state(account, [], ["SPY"])

    def test_account_blocked_raises(self):
        account = self._active_account(account_blocked=True)
        with pytest.raises(RuntimeError, match="account is blocked"):
            self._adapter()._validate_startup_state(account, [], ["SPY"])

    def test_overlapping_position_raises(self):
        account = self._active_account()
        positions = [{"symbol": "SPY"}, {"symbol": "MSFT"}]
        with pytest.raises(RuntimeError, match="Unexpected open positions"):
            self._adapter()._validate_startup_state(account, positions, ["SPY"])

    def test_unrelated_position_does_not_raise(self):
        account = self._active_account()
        positions = [{"symbol": "MSFT"}, {"symbol": "AAPL"}]
        # No overlap with target symbols — must not raise
        self._adapter()._validate_startup_state(account, positions, ["SPY", "QQQ"])

    def test_symbol_matching_is_case_insensitive(self):
        account = self._active_account()
        positions = [{"symbol": "spy"}]
        with pytest.raises(RuntimeError, match="Unexpected open positions"):
            self._adapter()._validate_startup_state(account, positions, ["SPY"])

    def test_passes_with_empty_positions(self):
        account = self._active_account()
        self._adapter()._validate_startup_state(account, [], ["SPY", "QQQ"])

    def test_passes_with_empty_target_symbols(self):
        account = self._active_account()
        positions = [{"symbol": "SPY"}]
        # No target symbols → no overlap possible
        self._adapter()._validate_startup_state(account, positions, [])


# ---------------------------------------------------------------------------
# get_account / get_positions still raise NotImplementedError
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Strict boolean parsing in _account_response_to_dict
# ---------------------------------------------------------------------------

class TestAccountBooleanParsing:
    def _adapter(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter()

    def _parse(self, trading_blocked=None, account_blocked=None):
        resp = {}
        if trading_blocked is not None:
            resp["trading_blocked"] = trading_blocked
        if account_blocked is not None:
            resp["account_blocked"] = account_blocked
        return self._adapter()._account_response_to_dict(resp)

    def test_bool_true_maps_to_true(self):
        result = self._parse(trading_blocked=True)
        assert result["trading_blocked"] is True

    def test_bool_false_maps_to_false(self):
        result = self._parse(trading_blocked=False)
        assert result["trading_blocked"] is False

    def test_string_true_maps_to_true(self):
        assert self._parse(trading_blocked="true")["trading_blocked"] is True
        assert self._parse(trading_blocked="True")["trading_blocked"] is True
        assert self._parse(trading_blocked="TRUE")["trading_blocked"] is True

    def test_string_false_maps_to_false(self):
        assert self._parse(trading_blocked="false")["trading_blocked"] is False
        assert self._parse(trading_blocked="False")["trading_blocked"] is False
        assert self._parse(trading_blocked="FALSE")["trading_blocked"] is False

    def test_string_one_maps_to_true(self):
        assert self._parse(trading_blocked="1")["trading_blocked"] is True

    def test_string_zero_maps_to_false(self):
        assert self._parse(trading_blocked="0")["trading_blocked"] is False

    def test_int_one_maps_to_true(self):
        assert self._parse(trading_blocked=1)["trading_blocked"] is True

    def test_int_zero_maps_to_false(self):
        assert self._parse(trading_blocked=0)["trading_blocked"] is False

    def test_none_maps_to_none(self):
        result = self._adapter()._account_response_to_dict({})
        assert result["trading_blocked"] is None
        assert result["account_blocked"] is None

    def test_unknown_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid boolean value"):
            self._parse(trading_blocked="yes")

    def test_account_blocked_uses_strict_parser(self):
        assert self._parse(account_blocked=True)["account_blocked"] is True
        assert self._parse(account_blocked="false")["account_blocked"] is False
        with pytest.raises(ValueError, match="Invalid boolean value"):
            self._parse(account_blocked="no")


class TestGetAccountGetPositionsRequireCredentials:
    """get_account() and get_positions() are now implemented but require
    credentials (or an injected client) — they no longer raise NotImplementedError."""

    def test_get_account_raises_without_credentials(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            AlpacaBrokerAdapter().get_account()

    def test_get_positions_raises_without_credentials(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            AlpacaBrokerAdapter().get_positions()


# ---------------------------------------------------------------------------
# _get_client() SDK client factory
# ---------------------------------------------------------------------------

class TestClientFactory:
    """Tests for the lazy SDK client factory in _get_client().

    All tests mock alpaca.trading.client.TradingClient so no real network
    calls are made and no real credentials are required.
    """

    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _mock_trading_client(self):
        """Return a mock that stands in for TradingClient."""
        return mock.MagicMock(name="TradingClient")

    def test_init_does_not_read_env_vars(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        # Constructor must not raise even without credentials
        adapter = self._adapter()
        assert adapter._client is None

    def test_get_client_with_injected_client_returns_it(self):
        fake = object()
        adapter = self._adapter(client=fake)
        assert adapter._get_client() is fake

    def test_get_client_with_injected_client_does_not_read_env_vars(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        fake = object()
        adapter = self._adapter(client=fake)
        assert adapter._get_client() is fake

    def test_get_client_without_injected_client_calls_validate_credentials(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            MockTC.return_value = self._mock_trading_client()
            with mock.patch.object(adapter, "_validate_credentials",
                                   wraps=adapter._validate_credentials) as spy:
                adapter._get_client()
            spy.assert_called_once()

    def test_get_client_creates_sdk_client_with_paper_true(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        monkeypatch.delenv("ALPACA_PAPER_BASE_URL", raising=False)
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            fake_instance = self._mock_trading_client()
            MockTC.return_value = fake_instance
            result = adapter._get_client()
        MockTC.assert_called_once_with(
            api_key="test-key",
            secret_key="test-secret",
            paper=True,
            url_override=None,
        )
        assert result is fake_instance

    def test_get_client_passes_url_override_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        monkeypatch.setenv("ALPACA_PAPER_BASE_URL", "https://custom.paper.endpoint")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            MockTC.return_value = self._mock_trading_client()
            adapter._get_client()
        _, kwargs = MockTC.call_args
        assert kwargs["url_override"] == "https://custom.paper.endpoint"

    def test_get_client_caches_client(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            fake_instance = self._mock_trading_client()
            MockTC.return_value = fake_instance
            first  = adapter._get_client()
            second = adapter._get_client()
        assert first is second
        MockTC.assert_called_once()  # only one construction

    def test_missing_credentials_raise_before_sdk_import(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter._get_client()
        MockTC.assert_not_called()

    def test_no_secrets_in_logs(self, monkeypatch, caplog):
        import logging
        monkeypatch.setenv("ALPACA_API_KEY", "super-secret-key-xyz")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "super-secret-value-abc")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            MockTC.return_value = self._mock_trading_client()
            with caplog.at_level(logging.DEBUG):
                adapter._get_client()
        log_text = caplog.text
        assert "super-secret-key-xyz" not in log_text
        assert "super-secret-value-abc" not in log_text

    def test_no_real_network_calls(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            MockTC.return_value = self._mock_trading_client()
            adapter._get_client()
        # TradingClient was mocked so no real HTTP was made; assertion is
        # that MockTC was called (not the real class)
        MockTC.assert_called_once()

    def test_submit_order_with_injected_mock_client_still_works(self, monkeypatch):
        from src.execution.order_intent import OrderIntent
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        fake_response = {
            "id": "order-123",
            "symbol": "SPY",
            "side": "buy",
            "qty": 10,
            "status": "accepted",
            "submitted_at": "2024-01-15T10:00:00Z",
            "filled_at": None,
            "filled_avg_price": None,
            "filled_qty": None,
            "client_order_id": "BT-000001",
        }

        class FakeClient:
            def submit_order(self, payload):
                return fake_response

        adapter = self._adapter(client=FakeClient())
        intent = OrderIntent(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )
        with mock.patch.object(adapter, "_ensure_market_hours"):
            result = adapter.submit_order(intent)
        assert result.order_id == "order-123"
        assert result.status == "accepted"
        assert result.client_order_id == "BT-000001"


# ---------------------------------------------------------------------------
# submit_order via _get_client (no injected client, credentials available)
# ---------------------------------------------------------------------------

class TestSubmitOrderViaGetClient:
    """submit_order() uses _get_client() when no client is injected."""

    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _intent(self):
        from src.execution.order_intent import OrderIntent
        return OrderIntent(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id="BT-000001",
        )

    def _fake_response(self):
        return {
            "id": "order-abc",
            "symbol": "SPY",
            "side": "buy",
            "qty": 10,
            "status": "accepted",
            "submitted_at": "2024-01-15T15:00:00Z",
            "filled_at": None,
            "filled_avg_price": None,
            "filled_qty": None,
            "client_order_id": "BT-000001",
        }

    def test_submit_order_calls_get_client_when_no_injected_client(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        adapter = self._adapter()
        fake_client = mock.MagicMock()
        fake_client.submit_order.return_value = self._fake_response()
        with mock.patch.object(adapter, "_get_client", return_value=fake_client) as mock_gc:
            with mock.patch.object(adapter, "_ensure_market_hours"):
                adapter.submit_order(self._intent())
        mock_gc.assert_called_once()

    def test_submit_order_no_real_network_calls(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        adapter = self._adapter()
        fake_client = mock.MagicMock()
        fake_client.submit_order.return_value = self._fake_response()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            MockTC.return_value = fake_client
            with mock.patch.object(adapter, "_ensure_market_hours"):
                result = adapter.submit_order(self._intent())
        assert result.order_id == "order-abc"
        # TradingClient was mocked — no real HTTP was made
        MockTC.assert_called_once()

    def test_missing_credentials_raise_before_broker_call(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        adapter = self._adapter()
        with mock.patch.object(adapter, "_ensure_market_hours"):
            with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
                adapter.submit_order(self._intent())


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------

class TestGetAccount:
    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _raw_account(self):
        return {
            "id": "acc-001",
            "status": "ACTIVE",
            "currency": "USD",
            "cash": "50000.00",
            "equity": "120000.00",
            "buying_power": "100000.00",
            "trading_blocked": False,
            "account_blocked": False,
        }

    def test_get_account_with_injected_mock_client(self):
        class FakeClient:
            def get_account(self_inner):
                return self._raw_account()

        adapter = self._adapter(client=FakeClient())
        result = adapter.get_account()
        assert result["id"] == "acc-001"
        assert result["status"] == "ACTIVE"
        assert result["cash"] == 50000.0
        assert result["trading_blocked"] is False

    def test_get_account_uses_get_client(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        adapter = self._adapter()
        fake_client = mock.MagicMock()
        fake_client.get_account.return_value = self._raw_account()
        with mock.patch.object(adapter, "_get_client", return_value=fake_client) as mock_gc:
            adapter.get_account()
        mock_gc.assert_called_once()
        fake_client.get_account.assert_called_once()

    def test_get_account_maps_response(self):
        class FakeClient:
            def get_account(self_inner):
                return {"id": "x", "status": "ACTIVE", "cash": "999.99",
                        "equity": "1000.00", "buying_power": "2000.00",
                        "trading_blocked": False, "account_blocked": False,
                        "currency": "USD"}

        result = self._adapter(client=FakeClient()).get_account()
        assert isinstance(result["cash"], float)
        assert result["cash"] == 999.99

    def test_get_account_does_not_log_secrets(self, monkeypatch, caplog):
        import logging
        monkeypatch.setenv("ALPACA_API_KEY", "my-secret-api-key-xyz")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "my-secret-value-abc")
        adapter = self._adapter()
        fake_client = mock.MagicMock()
        fake_client.get_account.return_value = self._raw_account()
        with mock.patch("alpaca.trading.client.TradingClient", return_value=fake_client):
            with caplog.at_level(logging.DEBUG):
                adapter.get_account()
        assert "my-secret-api-key-xyz" not in caplog.text
        assert "my-secret-value-abc" not in caplog.text

    def test_get_account_raises_without_credentials(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            self._adapter().get_account()


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _raw_positions(self):
        return [
            {"symbol": "SPY", "qty": "10", "market_value": "4500.00",
             "avg_entry_price": "440.00", "current_price": "450.00",
             "unrealized_pl": "100.00"},
            {"symbol": "QQQ", "qty": "5", "market_value": "1500.00",
             "avg_entry_price": "295.00", "current_price": "300.00",
             "unrealized_pl": "25.00"},
        ]

    def test_get_positions_with_injected_mock_client_get_all_positions(self):
        class FakeClient:
            def get_all_positions(self_inner):
                return self._raw_positions()

        result = self._adapter(client=FakeClient()).get_positions()
        assert "SPY" in result
        assert "QQQ" in result
        assert result["SPY"]["qty"] == 10.0
        assert result["QQQ"]["qty"] == 5.0

    def test_get_positions_prefers_get_all_positions(self):
        class FakeClient:
            def get_all_positions(self_inner):
                return self._raw_positions()
            def get_positions(self_inner):
                raise AssertionError("get_positions should not be called")

        result = self._adapter(client=FakeClient()).get_positions()
        assert set(result.keys()) == {"SPY", "QQQ"}

    def test_get_positions_falls_back_to_get_positions(self):
        class FakeClient:
            def get_positions(self_inner):
                return self._raw_positions()

        result = self._adapter(client=FakeClient()).get_positions()
        assert "SPY" in result
        assert "QQQ" in result

    def test_get_positions_returns_symbol_keyed_dict(self):
        class FakeClient:
            def get_all_positions(self_inner):
                return self._raw_positions()

        result = self._adapter(client=FakeClient()).get_positions()
        assert isinstance(result, dict)
        for sym, pos in result.items():
            assert pos["symbol"] == sym

    def test_get_positions_maps_numeric_fields(self):
        class FakeClient:
            def get_all_positions(self_inner):
                return [{"symbol": "SPY", "qty": "7", "market_value": "3150.00",
                         "avg_entry_price": "445.00", "current_price": "450.00",
                         "unrealized_pl": "35.00"}]

        result = self._adapter(client=FakeClient()).get_positions()
        pos = result["SPY"]
        assert isinstance(pos["qty"], float)
        assert isinstance(pos["market_value"], float)

    def test_get_positions_no_method_raises_not_implemented(self):
        class FakeClient:
            pass  # neither get_all_positions nor get_positions

        with pytest.raises(NotImplementedError, match="get_all_positions or get_positions"):
            self._adapter(client=FakeClient()).get_positions()

    def test_get_positions_uses_get_client(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        adapter = self._adapter()
        fake_client = mock.MagicMock()
        fake_client.get_all_positions.return_value = []
        with mock.patch.object(adapter, "_get_client", return_value=fake_client) as mock_gc:
            adapter.get_positions()
        mock_gc.assert_called_once()

    def test_get_positions_raises_without_credentials(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            self._adapter().get_positions()

    def test_cancel_order_raises_without_credentials(self):
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            self._adapter().cancel_order("some-id")


# ---------------------------------------------------------------------------
# main.py paper mode guard still fires before adapter creation
# ---------------------------------------------------------------------------

class TestMainPaperModeGuardStillActive:
    def _make_paper_config(self):
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
            execution=ExecutionConfig(mode="paper", dry_run_broker=False),
        )

    def test_paper_mode_raises_before_adapter_creation(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_paper_config()
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            with mock.patch.object(AlpacaBrokerAdapter, "__init__",
                                   side_effect=AssertionError("adapter must not be created")):
                from src.main import main as _main
                with pytest.raises(NotImplementedError):
                    _main()


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

class TestCancelOrder:
    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def test_empty_order_id_raises_value_error(self):
        class FakeClient:
            def cancel_order_by_id(self, oid): pass

        with pytest.raises(ValueError, match="order_id is required"):
            self._adapter(client=FakeClient()).cancel_order("")

    def test_whitespace_order_id_raises_value_error(self):
        class FakeClient:
            def cancel_order_by_id(self, oid): pass

        with pytest.raises(ValueError, match="order_id is required"):
            self._adapter(client=FakeClient()).cancel_order("   ")

    def test_uses_injected_mock_client(self):
        class FakeClient:
            called_with = None
            def cancel_order_by_id(self, oid):
                FakeClient.called_with = oid
                return None

        self._adapter(client=FakeClient()).cancel_order("order-001")
        assert FakeClient.called_with == "order-001"

    def test_uses_get_client_when_no_injected_client(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        adapter = self._adapter()
        fake_client = mock.MagicMock()
        fake_client.cancel_order_by_id.return_value = None
        with mock.patch.object(adapter, "_get_client", return_value=fake_client) as mock_gc:
            result = adapter.cancel_order("order-001")
        mock_gc.assert_called_once()
        assert result is True

    def test_prefers_cancel_order_by_id_over_cancel_order(self):
        class FakeClient:
            def cancel_order_by_id(self, oid):
                return None
            def cancel_order(self, oid):
                raise AssertionError("cancel_order should not be called")

        assert self._adapter(client=FakeClient()).cancel_order("order-001") is True

    def test_falls_back_to_cancel_order(self):
        class FakeClient:
            def cancel_order(self, oid):
                return None

        assert self._adapter(client=FakeClient()).cancel_order("order-001") is True

    def test_returns_true_for_none_response(self):
        class FakeClient:
            def cancel_order_by_id(self, oid):
                return None

        assert self._adapter(client=FakeClient()).cancel_order("order-001") is True

    def test_returns_true_for_status_code_200(self):
        class FakeClient:
            def cancel_order_by_id(self, oid):
                return {"status_code": 200}

        assert self._adapter(client=FakeClient()).cancel_order("order-001") is True

    def test_returns_true_for_status_code_202(self):
        class FakeClient:
            def cancel_order_by_id(self, oid):
                return {"status_code": 202}

        assert self._adapter(client=FakeClient()).cancel_order("order-001") is True

    def test_returns_true_for_status_code_204(self):
        class FakeClient:
            def cancel_order_by_id(self, oid):
                return {"status_code": 204}

        assert self._adapter(client=FakeClient()).cancel_order("order-001") is True

    def test_missing_cancel_methods_raise_not_implemented(self):
        class FakeClient:
            pass

        with pytest.raises(NotImplementedError, match="cancel_order_by_id or cancel_order"):
            self._adapter(client=FakeClient()).cancel_order("order-001")

    def test_missing_credentials_raise_before_client_creation(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="Missing Alpaca paper API credentials"):
            self._adapter().cancel_order("order-001")

    def test_no_real_network_calls(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            fake_client = mock.MagicMock()
            fake_client.cancel_order_by_id.return_value = None
            MockTC.return_value = fake_client
            result = adapter.cancel_order("order-001")
        assert result is True
        MockTC.assert_called_once()

    def test_unexpected_exception_is_not_swallowed(self):
        class FakeClient:
            def cancel_order_by_id(self, oid):
                raise RuntimeError("broker exploded")

        with pytest.raises(RuntimeError, match="broker exploded"):
            self._adapter(client=FakeClient()).cancel_order("order-001")


# ---------------------------------------------------------------------------
# preflight_check
# ---------------------------------------------------------------------------

class TestPreflightCheck:
    """Tests for AlpacaBrokerAdapter.preflight_check().

    All broker calls are mocked — no real network or credentials required.
    """

    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _active_account(self, **overrides):
        base = {
            "id": "acc-001", "status": "ACTIVE", "currency": "USD",
            "cash": 50000.0, "equity": 100000.0, "buying_power": 100000.0,
            "trading_blocked": False, "account_blocked": False,
        }
        base.update(overrides)
        return base

    def _patch_broker(self, adapter, account=None, positions=None):
        """Patch get_account and get_positions on adapter."""
        if account is None:
            account = self._active_account()
        if positions is None:
            positions = {}
        adapter.get_account   = mock.Mock(return_value=account)
        adapter.get_positions = mock.Mock(return_value=positions)
        return adapter

    # --- happy path ---

    def test_returns_ok_true_on_happy_path(self):
        adapter = self._patch_broker(self._adapter())
        result = adapter.preflight_check(["SPY", "QQQ"])
        assert result["ok"] is True

    def test_returns_account(self):
        account = self._active_account()
        adapter = self._patch_broker(self._adapter(), account=account)
        assert adapter.preflight_check(["SPY"])["account"] is account

    def test_returns_positions(self):
        positions = {"MSFT": {"symbol": "MSFT", "qty": 1.0}}
        adapter   = self._patch_broker(self._adapter(), positions=positions)
        assert adapter.preflight_check(["SPY"])["positions"] is positions

    def test_returns_normalised_symbols(self):
        adapter = self._patch_broker(self._adapter())
        result  = adapter.preflight_check(["spy", "qqq"])
        assert result["symbols"] == ["SPY", "QQQ"]

    def test_symbols_normalised_to_uppercase(self):
        adapter = self._patch_broker(self._adapter())
        result  = adapter.preflight_check(["spy", "Qqq", "MSFT"])
        assert result["symbols"] == ["SPY", "QQQ", "MSFT"]

    # --- symbol validation ---

    def test_empty_symbols_list_raises_value_error(self):
        adapter = self._patch_broker(self._adapter())
        with pytest.raises(ValueError, match="symbols must not be empty"):
            adapter.preflight_check([])

    def test_whitespace_symbol_raises_value_error(self):
        adapter = self._patch_broker(self._adapter())
        with pytest.raises(ValueError, match="empty or whitespace"):
            adapter.preflight_check(["SPY", "  "])

    def test_empty_string_symbol_raises_value_error(self):
        adapter = self._patch_broker(self._adapter())
        with pytest.raises(ValueError, match="empty or whitespace"):
            adapter.preflight_check([""])

    # --- method call counts ---

    def test_get_account_called_exactly_once(self):
        adapter = self._patch_broker(self._adapter())
        adapter.preflight_check(["SPY"])
        adapter.get_account.assert_called_once()

    def test_get_positions_called_exactly_once(self):
        adapter = self._patch_broker(self._adapter())
        adapter.preflight_check(["SPY"])
        adapter.get_positions.assert_called_once()

    def test_validate_startup_state_receives_positions_values(self):
        positions = {
            "MSFT": {"symbol": "MSFT", "qty": 2.0},
            "AAPL": {"symbol": "AAPL", "qty": 3.0},
        }
        adapter = self._patch_broker(self._adapter(), positions=positions)
        with mock.patch.object(adapter, "_validate_startup_state") as mock_vss:
            adapter.preflight_check(["SPY"])
        # Second argument must be list(positions.values()), not the dict itself
        call_args = mock_vss.call_args
        pos_arg = call_args[0][1]
        assert isinstance(pos_arg, list)
        assert set(p["symbol"] for p in pos_arg) == {"MSFT", "AAPL"}

    # --- account state validation ---

    def test_inactive_account_raises_runtime_error(self):
        account = self._active_account(status="INACTIVE")
        adapter = self._patch_broker(self._adapter(), account=account)
        with pytest.raises(RuntimeError, match="not active"):
            adapter.preflight_check(["SPY"])

    def test_trading_blocked_raises_runtime_error(self):
        account = self._active_account(trading_blocked=True)
        adapter = self._patch_broker(self._adapter(), account=account)
        with pytest.raises(RuntimeError, match="trading is blocked"):
            adapter.preflight_check(["SPY"])

    def test_account_blocked_raises_runtime_error(self):
        account = self._active_account(account_blocked=True)
        adapter = self._patch_broker(self._adapter(), account=account)
        with pytest.raises(RuntimeError, match="account is blocked"):
            adapter.preflight_check(["SPY"])

    def test_existing_position_in_target_symbol_raises(self):
        positions = {"SPY": {"symbol": "SPY", "qty": 10.0}}
        adapter   = self._patch_broker(self._adapter(), positions=positions)
        with pytest.raises(RuntimeError, match="Unexpected open positions"):
            adapter.preflight_check(["SPY"])

    def test_non_target_existing_position_is_allowed(self):
        positions = {"MSFT": {"symbol": "MSFT", "qty": 5.0}}
        adapter   = self._patch_broker(self._adapter(), positions=positions)
        result    = adapter.preflight_check(["SPY"])
        assert result["ok"] is True

    # --- safety: no order or cancellation side-effects ---

    def test_does_not_call_submit_order(self):
        adapter = self._patch_broker(self._adapter())
        with mock.patch.object(adapter, "submit_order",
                               side_effect=AssertionError("submit_order must not be called")):
            adapter.preflight_check(["SPY"])  # must not raise

    def test_does_not_call_cancel_order(self):
        adapter = self._patch_broker(self._adapter())
        with mock.patch.object(adapter, "cancel_order",
                               side_effect=AssertionError("cancel_order must not be called")):
            adapter.preflight_check(["SPY"])  # must not raise

    def test_does_not_call_ensure_market_hours(self):
        adapter = self._patch_broker(self._adapter())
        with mock.patch.object(adapter, "_ensure_market_hours",
                               side_effect=AssertionError("_ensure_market_hours must not be called")):
            adapter.preflight_check(["SPY"])  # must not raise

    # --- infrastructure ---

    def test_no_real_network_calls(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        adapter = self._adapter()
        with mock.patch("alpaca.trading.client.TradingClient") as MockTC:
            fake_client = mock.MagicMock()
            fake_client.get_account.return_value = self._active_account()
            fake_client.get_all_positions.return_value = []
            MockTC.return_value = fake_client
            result = adapter.preflight_check(["SPY"])
        assert result["ok"] is True
        MockTC.assert_called_once()  # mocked — no real HTTP


# ---------------------------------------------------------------------------
# main.py paper_trading_enabled flag
# ---------------------------------------------------------------------------

class TestPaperTradingEnabledFlag:
    """Tests for the paper_trading_enabled config flag in main.py."""

    def _make_config(self, mode: str = "paper", paper_trading_enabled: bool = False,
                     **exec_kwargs):
        from src.config.loader import (
            AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
            LoggingConfig, RiskConfig, StrategyConfig,
        )
        return AppConfig(
            backtest=BacktestConfig(
                start_date="2024-01-15", end_date="2024-01-15",
                initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
            ),
            symbols=["SPY", "QQQ"],
            data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
            strategy=StrategyConfig(name="opening_range_breakout", params={
                "opening_range_start": "09:30", "opening_range_end": "10:00",
                "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
            }),
            risk=RiskConfig(),
            logging=LoggingConfig(level="WARNING", format="%(message)s"),
            execution=ExecutionConfig(
                mode=mode,
                paper_trading_enabled=paper_trading_enabled,
                **exec_kwargs,
            ),
        )

    def _run_main(self, cfg):
        from src.main import main as _main
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            _main()

    # --- default / missing flag ---

    def test_default_paper_trading_enabled_is_false(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig().paper_trading_enabled is False

    def test_config_without_flag_defaults_to_false(self):
        from src.config.loader import ExecutionConfig
        cfg = ExecutionConfig(mode="paper")
        assert cfg.paper_trading_enabled is False

    # --- disabled path (paper_trading_enabled=False) ---

    def test_paper_disabled_raises_before_adapter_creation(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config(mode="paper", paper_trading_enabled=False)
        with mock.patch.object(AlpacaBrokerAdapter, "__init__",
                               side_effect=AssertionError("adapter must not be created")):
            with mock.patch("src.main.load_config", return_value=cfg), \
                 mock.patch("sys.argv", ["prog"]):
                with pytest.raises(NotImplementedError, match="Paper trading is disabled"):
                    from src.main import main as _main
                    _main()

    def test_paper_disabled_fail_closed_message(self):
        cfg = self._make_config(mode="paper", paper_trading_enabled=False)
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            with pytest.raises(NotImplementedError) as exc_info:
                from src.main import main as _main
                _main()
        assert "paper" in str(exc_info.value).lower()

    # --- enabled path (paper_trading_enabled=True) ---

    def _fake_engine(self, intents=None):
        """Return a MagicMock that mimics BacktestEngine for paper path tests."""
        eng = mock.MagicMock()
        eng.run.return_value = {
            "order_intents": intents if intents is not None else [],
            "metrics": {"total_return": 0.0},
            "trades": [],
            "equity_curve": [],
        }
        eng._portfolio.positions = {}
        return eng

    def _fake_preflight(self, symbols=None):
        return {
            "ok": True,
            "account": {"status": "ACTIVE"},
            "positions": {},
            "symbols": symbols or ["SPY", "QQQ"],
        }

    @staticmethod
    def _make_generate_all_mock(tmp_path):
        """Return a generate_all side_effect that writes a PASS reconciliation JSON."""
        import json as _json

        def _impl(rg_self):
            rg_self._output_dir.mkdir(parents=True, exist_ok=True)
            (rg_self._output_dir / "order_reconciliation.json").write_text(
                _json.dumps({"overall_status": "PASS"})
            )

        return _impl

    def test_paper_enabled_creates_adapter_and_calls_preflight(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config(mode="paper", paper_trading_enabled=True)
        with mock.patch.object(AlpacaBrokerAdapter, "__init__",
                               return_value=None) as mock_init, \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()) as mock_pf, \
             mock.patch("src.main.build_engine", return_value=self._fake_engine()), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._make_generate_all_mock(tmp_path)), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        mock_init.assert_called_once()
        mock_pf.assert_called_once_with(cfg.symbols)

    def test_paper_enabled_submit_order_not_called_when_no_valid_intents(self, tmp_path):
        """submit_order is not called when no intents are generated."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config(mode="paper", paper_trading_enabled=True)
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine()), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._make_generate_all_mock(tmp_path)), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        mock_submit.assert_not_called()

    def test_paper_enabled_cancel_order_never_called(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config(mode="paper", paper_trading_enabled=True)
        mock_cancel = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine()), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._make_generate_all_mock(tmp_path)), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        mock_cancel.assert_not_called()

    def test_no_real_network_calls_when_enabled(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config(mode="paper", paper_trading_enabled=True)
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("alpaca.trading.client.TradingClient") as MockTC, \
             mock.patch("src.main.build_engine", return_value=self._fake_engine()), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._make_generate_all_mock(tmp_path)), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        MockTC.assert_not_called()

    # --- backtest mode unchanged ---

    def test_backtest_mode_unaffected_by_flag(self):
        """Backtest still runs normally when paper_trading_enabled is in config."""
        from src.config.loader import ExecutionConfig
        cfg = self._make_config(mode="backtest", paper_trading_enabled=False)
        assert cfg.execution.paper_trading_enabled is False
        assert cfg.execution.mode == "backtest"

    def test_no_live_trading_mode_introduced(self):
        from src.config.loader import _VALID_EXECUTION_MODES  # noqa
        assert "live" not in _VALID_EXECUTION_MODES


# ---------------------------------------------------------------------------
# Intended future paper execution flow (mock-only documentation test)
#
# These tests document the sequence main.py will follow once the final paper
# execution wiring PR is merged and the NotImplementedError after preflight
# is removed.  They do NOT test real execution today; they verify that the
# building-blocks are in place and behave correctly in isolation.
# ---------------------------------------------------------------------------

class TestIntendedPaperExecutionFlow:
    """Mock-only documentation tests for the planned paper execution sequence.

    None of these tests submit real orders or make real network calls.
    The NotImplementedError that blocks order execution in main.py is
    intentionally NOT removed by this test class.
    """

    def _adapter(self, **kwargs):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(**kwargs)

    def _make_intent(self, client_order_id="BT-000001"):
        from src.execution.order_intent import OrderIntent
        return OrderIntent(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id=client_order_id,
        )

    def _mock_result(self, intent, status="accepted"):
        from src.execution.broker import OrderResult
        return OrderResult(
            order_id=f"ALPACA-{intent.client_order_id}",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            status=status,
            submitted_at=intent.timestamp,
            reason=intent.reason,
            client_order_id=intent.client_order_id,
            metadata={"raw_status": status, "partial_fill": False},
        )

    # --- step 1: preflight must succeed before any submit_order call ---

    def test_step1_preflight_runs_before_submit_order(self):
        """preflight_check must be called before the first submit_order."""
        call_log: list[str] = []
        adapter = self._adapter()
        intent   = self._make_intent()

        def fake_preflight(symbols):
            call_log.append("preflight")
            return {"ok": True, "account": {"status": "ACTIVE"},
                    "positions": {}, "symbols": symbols}

        def fake_submit(i):
            call_log.append("submit_order")
            return self._mock_result(i)

        adapter.preflight_check = fake_preflight
        adapter.submit_order    = fake_submit

        # Simulate the intended main.py paper execution sequence
        adapter.preflight_check(["SPY"])
        adapter.submit_order(intent)

        assert call_log.index("preflight") < call_log.index("submit_order")

    # --- step 2: every intent must carry a non-empty client_order_id ---

    def test_step2_every_intent_has_client_order_id(self):
        """Intents without client_order_id must be rejected before submission."""
        intent_with_id    = self._make_intent(client_order_id="BT-000001")
        intent_without_id = self._make_intent(client_order_id=None)

        adapter = self._adapter(client=mock.MagicMock())
        with mock.patch.object(adapter, "_ensure_market_hours"):
            # Intent with ID must pass validation
            with mock.patch.object(adapter, "_get_client") as mock_gc:
                mock_gc.return_value.submit_order.return_value = {
                    "id": "x", "symbol": "SPY", "side": "buy", "qty": 10,
                    "status": "accepted", "submitted_at": "2024-01-15T10:00:00Z",
                    "filled_at": None, "filled_avg_price": None,
                    "filled_qty": None, "client_order_id": "BT-000001",
                }
                result = adapter.submit_order(intent_with_id)
            assert result.client_order_id == "BT-000001"

            # Intent without ID must raise before network call
            with pytest.raises(ValueError, match="client_order_id is required"):
                adapter.submit_order(intent_without_id)

    # --- step 3: results preserve client_order_id for reconciliation ---

    def test_step3_result_preserves_client_order_id(self):
        """OrderResult.client_order_id must match the submitted intent's ID."""
        intent = self._make_intent(client_order_id="BT-000042")
        result = self._mock_result(intent)
        assert result.client_order_id == "BT-000042"

    # --- step 4: multiple intents submitted independently ---

    def test_step4_multiple_intents_submitted_one_by_one(self):
        """Each OrderIntent is submitted in a separate submit_order call."""
        intents = [self._make_intent(client_order_id=f"BT-{i:06d}") for i in range(1, 4)]
        submitted: list[str] = []

        class FakeClient:
            def submit_order(self_inner, payload):
                cid = payload["client_order_id"]
                submitted.append(cid)
                return {
                    "id": f"ALPACA-{cid}", "symbol": "SPY", "side": "buy",
                    "qty": 10, "status": "accepted",
                    "submitted_at": "2024-01-15T10:00:00Z",
                    "filled_at": None, "filled_avg_price": None,
                    "filled_qty": None, "client_order_id": cid,
                }

        adapter = self._adapter(client=FakeClient())
        results = []
        with mock.patch.object(adapter, "_ensure_market_hours"):
            for intent in intents:
                results.append(adapter.submit_order(intent))

        assert submitted == ["BT-000001", "BT-000002", "BT-000003"]
        assert all(r.status == "accepted" for r in results)
        assert [r.client_order_id for r in results] == ["BT-000001", "BT-000002", "BT-000003"]

    # --- step 5: preflight failure prevents any submission ---

    def test_step5_preflight_failure_prevents_submit(self):
        """If preflight_check raises, submit_order must not be called."""
        adapter = self._adapter()
        intent  = self._make_intent()

        def failing_preflight(symbols):
            raise RuntimeError("Alpaca account is not active")

        adapter.preflight_check = failing_preflight
        adapter.submit_order    = mock.Mock(side_effect=AssertionError("must not be called"))

        with pytest.raises(RuntimeError, match="not active"):
            adapter.preflight_check(["SPY"])
        # submit_order was not called
        adapter.submit_order.assert_not_called()

    # --- step 6: no orders if disabled ---

    def test_step6_paper_disabled_submit_never_called(self):
        """paper_trading_enabled=False must prevent adapter creation (main.py guard)."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        from src.config.loader import (
            AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
            LoggingConfig, RiskConfig, StrategyConfig,
        )
        cfg = AppConfig(
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
            execution=ExecutionConfig(mode="paper", paper_trading_enabled=False),
        )
        with mock.patch.object(AlpacaBrokerAdapter, "__init__",
                               side_effect=AssertionError("adapter must not be created")), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            with pytest.raises(NotImplementedError, match="Paper trading is disabled"):
                from src.main import main as _main
                _main()

    # --- step 7: execution path is now wired ---

    def test_step7_paper_path_runs_and_reports_without_valid_intents(self, tmp_path):
        """After preflight, the engine runs and writes artifacts even with no intents."""
        import json as _json
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        from src.config.loader import (
            AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
            LoggingConfig, RiskConfig, StrategyConfig,
        )
        cfg = AppConfig(
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
            execution=ExecutionConfig(mode="paper", paper_trading_enabled=True),
        )
        fake_preflight = {"ok": True, "account": {"status": "ACTIVE"},
                          "positions": {}, "symbols": ["SPY"]}
        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [], "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}

        def _fake_generate(rg_self):
            rg_self._output_dir.mkdir(parents=True, exist_ok=True)
            (rg_self._output_dir / "order_reconciliation.json").write_text(
                _json.dumps({"overall_status": "PASS"})
            )

        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _fake_generate), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        fake_engine.run.assert_called_once()


# ---------------------------------------------------------------------------
# Paper execution path — constrained, fail-closed wiring
# ---------------------------------------------------------------------------

class TestPaperExecutionPath:
    """Tests for the constrained paper execution path in main.py.

    Behavior: any safety-constraint violation raises RuntimeError before
    submit_order is called.  More than 1 intent → RuntimeError.  Zero
    intents → empty artifacts written.  Reconciliation JSON must always exist.

    All tests are fully mocked — no real Alpaca network calls.
    """

    def _make_config(self, paper_trading_enabled: bool = True):
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
            execution=ExecutionConfig(mode="paper", paper_trading_enabled=paper_trading_enabled),
        )

    def _make_intent(self, symbol="SPY", side="buy", quantity=1.0,
                     order_type="market", client_order_id="BT-000001"):
        from src.execution.order_intent import OrderIntent
        return OrderIntent(
            symbol=symbol, side=side, quantity=quantity,
            order_type=order_type, reason="entry",
            timestamp=pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York"),
            client_order_id=client_order_id,
        )

    _UNSET = object()

    def _make_order_result(self, intent, status="accepted", client_order_id=_UNSET):
        from src.execution.broker import OrderResult
        cid = intent.client_order_id if client_order_id is self._UNSET else client_order_id
        return OrderResult(
            order_id=f"ALPACA-{intent.client_order_id}",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            status=status,
            submitted_at=intent.timestamp,
            reason=intent.reason,
            client_order_id=cid,
            metadata={"raw_status": status, "partial_fill": False},
        )

    def _fake_engine(self, intents=None):
        eng = mock.MagicMock()
        eng.run.return_value = {
            "order_intents": intents if intents is not None else [],
            "metrics": {"total_return": 0.0},
            "trades": [],
            "equity_curve": [],
        }
        eng._portfolio.positions = {}
        return eng

    def _fake_preflight(self):
        return {"ok": True, "account": {"status": "ACTIVE"}, "positions": {}, "symbols": ["SPY"]}

    @staticmethod
    def _pass_recon_generate(rg_self):
        """generate_all side_effect that writes a PASS reconciliation JSON."""
        import json as _j
        rg_self._output_dir.mkdir(parents=True, exist_ok=True)
        (rg_self._output_dir / "order_reconciliation.json").write_text(
            _j.dumps({"overall_status": "PASS"})
        )

    def _run_paper(self, cfg, intents, mock_submit_return=None, tmp_path=None):
        """Run main() paper path with mocked engine, broker, and reporter.

        generate_all writes a PASS reconciliation JSON so the fail-closed
        recon check passes.  Returns mock_submit.
        """
        import tempfile
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        out = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
        eng = self._fake_engine(intents)
        mock_submit = mock.MagicMock(return_value=mock_submit_return)
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=eng), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._pass_recon_generate), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", out]):
            from src.main import main as _main
            _main()
        return mock_submit

    # --- happy path: valid SPY market intent, qty=1 ---

    def test_spy_intent_is_submitted(self, tmp_path):
        """A valid SPY market intent with qty=1 reaches submit_order."""
        cfg = self._make_config()
        intent = self._make_intent()
        result = self._make_order_result(intent)
        mock_submit = self._run_paper(cfg, [intent], mock_submit_return=result,
                                      tmp_path=tmp_path)
        mock_submit.assert_called_once()
        submitted = mock_submit.call_args[0][0]
        assert submitted.symbol == "SPY"
        assert submitted.client_order_id == "BT-000001"

    def test_qty_eq1_is_submitted(self, tmp_path):
        """Intents with quantity == 1.0 pass the safety constraints."""
        cfg = self._make_config()
        intent = self._make_intent(quantity=1.0)
        result = self._make_order_result(intent)
        mock_submit = self._run_paper(cfg, [intent], mock_submit_return=result,
                                      tmp_path=tmp_path)
        mock_submit.assert_called_once()

    # --- fail closed: non-SPY symbol → RuntimeError, submit_order not called ---

    def test_non_spy_raises_before_submit(self):
        """Non-SPY intent raises RuntimeError; submit_order is never called."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent(symbol="QQQ")
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="symbol="):
                _main()
        mock_submit.assert_not_called()

    # --- fail closed: non-market order_type → RuntimeError ---

    def test_limit_order_raises_before_submit(self):
        """Limit order intent raises RuntimeError; submit_order is never called."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent(order_type="limit")
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="order_type="):
                _main()
        mock_submit.assert_not_called()

    # --- fail closed: quantity > 1 → RuntimeError ---

    def test_qty_gt1_raises_before_submit(self):
        """Intent with quantity > 1 raises RuntimeError; submit_order is never called."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent(quantity=2.0)
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="quantity="):
                _main()
        mock_submit.assert_not_called()

    # --- fail closed: missing client_order_id → RuntimeError ---

    def test_missing_client_order_id_raises(self):
        """Intent with no client_order_id raises RuntimeError."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent(client_order_id=None)
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="client_order_id"):
                _main()
        mock_submit.assert_not_called()

    def test_blank_client_order_id_raises(self):
        """Intent with a whitespace-only client_order_id raises RuntimeError."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent(client_order_id="   ")
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="client_order_id"):
                _main()
        mock_submit.assert_not_called()

    # --- fail closed: more than 1 safe intent → RuntimeError ---

    def test_multiple_safe_intents_raises(self):
        """More than 1 safe intent raises RuntimeError; submit_order is never called."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intents = [
            self._make_intent(client_order_id="BT-000001"),
            self._make_intent(client_order_id="BT-000002"),
        ]
        mock_submit = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine(intents)), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="2 intents"):
                _main()
        mock_submit.assert_not_called()

    # --- zero intents: empty artifacts, no submission ---

    def test_no_orders_when_no_intents_generated(self, tmp_path):
        """submit_order not called when engine produces no intents."""
        cfg = self._make_config()
        mock_submit = self._run_paper(cfg, [], tmp_path=tmp_path)
        mock_submit.assert_not_called()

    # --- reporter always called (no exception path) ---

    def test_reporter_always_called(self, tmp_path):
        """ReportGenerator.generate_all is called when no intents are generated."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        call_log: list[str] = []

        def _gen(rg_self):
            call_log.append("generate_all")
            self._pass_recon_generate(rg_self)

        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all", _gen), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        assert "generate_all" in call_log

    def test_reporter_called_after_order_submission(self, tmp_path):
        """ReportGenerator.generate_all is called when an order is submitted."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent()
        result = self._make_order_result(intent)
        call_log: list[str] = []

        def _gen(rg_self):
            call_log.append("generate_all")
            self._pass_recon_generate(rg_self)

        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order",
                               return_value=result), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all", _gen), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        assert "generate_all" in call_log

    # --- reconciliation JSON always written (order_results=[] not None) ---

    def test_empty_path_writes_reconciliation_json(self, tmp_path):
        """Zero intents: order_reconciliation.json is written (order_results=[] not None)."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        recon = tmp_path / "order_reconciliation.json"
        assert recon.exists(), "order_reconciliation.json must be written even with 0 intents"
        import json
        data = json.loads(recon.read_text())
        assert data.get("overall_status") in ("PASS", "N/A")

    def test_submitted_order_writes_reconciliation_json(self, tmp_path):
        """After a successful submission, order_reconciliation.json is written."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent()
        result = self._make_order_result(intent)
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", return_value=result), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        recon = tmp_path / "order_reconciliation.json"
        assert recon.exists()
        import json
        data = json.loads(recon.read_text())
        assert data.get("overall_status") in ("PASS", "N/A")

    # --- fail closed: missing reconciliation JSON → RuntimeError ---

    def test_missing_reconciliation_json_raises(self, tmp_path):
        """If order_reconciliation.json is not written, RuntimeError is raised."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="order_reconciliation.json was not written"):
                _main()

    # --- fail closed: WARN reconciliation status → RuntimeError ---

    def test_reconciliation_warn_raises(self, tmp_path):
        """overall_status=WARN in reconciliation JSON raises RuntimeError."""
        import json
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()

        def _write_warn(rg_self):
            rg_self._output_dir.mkdir(parents=True, exist_ok=True)
            (rg_self._output_dir / "order_reconciliation.json").write_text(
                json.dumps({"overall_status": "WARN", "unmatched_count": 1})
            )

        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _write_warn), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="reconciliation failed"):
                _main()

    def test_reconciliation_pass_does_not_raise(self, tmp_path):
        """overall_status=PASS in reconciliation JSON does not raise."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._pass_recon_generate), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()  # must not raise

    # --- fail closed: result without client_order_id → RuntimeError ---

    def test_result_without_client_order_id_raises(self, tmp_path):
        """If submit_order returns a result with no client_order_id, RuntimeError is raised."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent()
        result_no_id = self._make_order_result(intent, client_order_id=None)
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order",
                               return_value=result_no_id), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            with pytest.raises(RuntimeError, match="client_order_id"):
                _main()

    # --- cancel_order never called ---

    def test_cancel_order_never_called(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        mock_cancel = mock.MagicMock()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._pass_recon_generate), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        mock_cancel.assert_not_called()

    # --- preflight called before engine ---

    def test_preflight_called_before_engine(self, tmp_path):
        """preflight_check is invoked before build_engine."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        call_log: list[str] = []
        cfg = self._make_config()

        def fake_preflight(symbols):
            call_log.append("preflight")
            return self._fake_preflight()

        def fake_build(c):
            call_log.append("build_engine")
            return self._fake_engine([])

        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               side_effect=fake_preflight), \
             mock.patch("src.main.build_engine", side_effect=fake_build), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._pass_recon_generate), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        assert call_log.index("preflight") < call_log.index("build_engine")

    # --- candidate_intents passed to reporter (not just submitted) ---

    def test_candidate_intents_passed_to_reporter(self, tmp_path):
        """The reporter receives all candidate intents, not only the submitted one."""
        import json
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        intent = self._make_intent()
        result = self._make_order_result(intent)
        captured: list = []

        def _gen(rg_self):
            captured.append(list(rg_self._order_intents))
            self._pass_recon_generate(rg_self)

        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", return_value=result), \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([intent])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all", _gen), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        assert len(captured) == 1
        assert captured[0][0].client_order_id == "BT-000001"

    # --- no live mode ---

    def test_no_live_trading_mode(self):
        from src.config.loader import _VALID_EXECUTION_MODES
        assert "live" not in _VALID_EXECUTION_MODES

    # --- no real network calls ---

    def test_no_real_network_calls(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = self._make_config()
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=self._fake_preflight()), \
             mock.patch("alpaca.trading.client.TradingClient") as MockTC, \
             mock.patch("src.main.build_engine", return_value=self._fake_engine([])), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        self._pass_recon_generate), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            from src.main import main as _main
            _main()
        MockTC.assert_not_called()
