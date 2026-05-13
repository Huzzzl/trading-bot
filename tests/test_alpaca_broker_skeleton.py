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
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
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

    # --- submit_order still blocked ---

    def test_submit_order_still_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
            self._adapter().submit_order(self._intent())


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

    # --- submit_order still blocked ---

    def test_submit_order_still_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
            self._adapter().submit_order(self._intent())


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

    # --- submit_order still blocked ---

    def test_submit_order_still_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
            self._adapter().submit_order(self._intent())


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

    def test_submit_order_still_raises_not_implemented(self):
        # No client injected → NotImplementedError before market-hours check
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
            self._adapter().submit_order(self._intent())


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

    # --- no client → NotImplementedError ---

    def test_no_client_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
            with self._rth_patch():
                self._adapter()._ensure_market_hours = mock.Mock()
            self._adapter().submit_order(self._intent())

    def test_no_client_raises_not_implemented_direct(self):
        adapter = self._adapter(client=None)
        with pytest.raises(NotImplementedError, match="Alpaca client is not implemented yet"):
            adapter.submit_order(self._intent())

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


class TestGetAccountGetPositionsStillNotImplemented:
    def test_get_account_raises(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        with pytest.raises(NotImplementedError):
            AlpacaBrokerAdapter().get_account()

    def test_get_positions_raises(self):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        with pytest.raises(NotImplementedError):
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
