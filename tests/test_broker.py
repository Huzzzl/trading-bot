"""
tests/test_broker.py
--------------------
Tests for BrokerAdapter, OrderResult, and FakeBrokerAdapter.

No network calls, no real broker, no API keys.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
import pandas as pd

from src.execution.broker import BrokerAdapter, OrderResult
from src.execution.fake_broker import FakeBrokerAdapter
from src.execution.order_intent import OrderIntent

_ET = ZoneInfo("America/New_York")
_TS = pd.Timestamp("2024-01-02 10:05", tz=_ET)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(
    symbol: str = "SPY",
    side: str = "buy",
    quantity: float = 100.0,
    order_type: str = "market",
    reason: str = "entry",
    timestamp: pd.Timestamp = _TS,
    **kwargs,
) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        reason=reason,
        timestamp=timestamp,
        **kwargs,
    )


# ===========================================================================
# BrokerAdapter — abstract interface
# ===========================================================================

class TestBrokerAdapterIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BrokerAdapter()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_all_methods(self):
        class Partial(BrokerAdapter):
            def submit_order(self, intent):
                return None

        with pytest.raises(TypeError):
            Partial()

    def test_full_concrete_subclass_can_be_instantiated(self):
        class Concrete(BrokerAdapter):
            def submit_order(self, intent):   return None
            def get_positions(self):          return {}
            def get_account(self):            return {}
            def cancel_order(self, order_id): return False

        c = Concrete()
        assert isinstance(c, BrokerAdapter)


# ===========================================================================
# OrderResult dataclass
# ===========================================================================

class TestOrderResult:
    def _result(self, **kwargs) -> OrderResult:
        defaults = dict(
            order_id="ORD-1",
            symbol="SPY",
            side="buy",
            quantity=100.0,
            status="accepted",
            submitted_at=_TS,
            reason="entry",
        )
        defaults.update(kwargs)
        return OrderResult(**defaults)

    def test_basic_construction(self):
        r = self._result()
        assert r.order_id == "ORD-1"
        assert r.symbol   == "SPY"
        assert r.status   == "accepted"

    def test_optional_fields_default_to_none(self):
        r = self._result()
        assert r.filled_at    is None
        assert r.filled_price is None
        assert r.metadata     == {}

    def test_filled_result(self):
        r = self._result(status="filled", filled_at=_TS, filled_price=450.0)
        assert r.filled_price == 450.0
        assert r.filled_at    == _TS

    def test_to_dict_contains_all_keys(self):
        d = self._result().to_dict()
        for key in ("order_id", "symbol", "side", "quantity", "status",
                    "submitted_at", "reason", "filled_at", "filled_price", "metadata"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_submitted_at_is_string(self):
        d = self._result().to_dict()
        assert isinstance(d["submitted_at"], str)

    def test_to_dict_filled_at_none_when_not_filled(self):
        d = self._result().to_dict()
        assert d["filled_at"] is None

    def test_to_dict_filled_at_string_when_filled(self):
        d = self._result(filled_at=_TS).to_dict()
        assert isinstance(d["filled_at"], str)

    def test_to_dict_is_json_serialisable(self):
        import json
        json.dumps(self._result().to_dict())


# ===========================================================================
# FakeBrokerAdapter — construction
# ===========================================================================

class TestFakeBrokerConstruction:
    def test_instantiates_with_defaults(self):
        fb = FakeBrokerAdapter()
        assert fb._fill_immediately is True
        assert fb._initial_cash     == 100_000.0

    def test_custom_params(self):
        fb = FakeBrokerAdapter(fill_immediately=False, initial_cash=50_000.0)
        assert fb._fill_immediately is False
        assert fb._initial_cash     == 50_000.0

    def test_is_broker_adapter_subclass(self):
        assert issubclass(FakeBrokerAdapter, BrokerAdapter)


# ===========================================================================
# FakeBrokerAdapter — submit_order
# ===========================================================================

class TestFakeBrokerSubmitOrder:
    def test_returns_order_result(self):
        fb = FakeBrokerAdapter()
        result = fb.submit_order(_intent())
        assert isinstance(result, OrderResult)

    def test_order_id_deterministic_first(self):
        fb = FakeBrokerAdapter()
        r = fb.submit_order(_intent())
        assert r.order_id == "FAKE-000001"

    def test_order_id_incremental(self):
        fb = FakeBrokerAdapter()
        r1 = fb.submit_order(_intent())
        r2 = fb.submit_order(_intent())
        r3 = fb.submit_order(_intent())
        assert r1.order_id == "FAKE-000001"
        assert r2.order_id == "FAKE-000002"
        assert r3.order_id == "FAKE-000003"

    def test_market_order_filled_immediately_with_limit_price(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(order_type="market", limit_price=450.0)
        result = fb.submit_order(intent)
        assert result.status       == "filled"
        assert result.filled_price == 450.0
        assert result.filled_at    == _TS

    def test_market_order_filled_immediately_with_metadata_entry_price(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(order_type="market", metadata={"entry_price": 451.0})
        result = fb.submit_order(intent)
        assert result.status       == "filled"
        assert result.filled_price == 451.0

    def test_market_order_filled_immediately_with_metadata_price(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(order_type="market", metadata={"price": 452.0})
        result = fb.submit_order(intent)
        assert result.status       == "filled"
        assert result.filled_price == 452.0

    def test_market_order_accepted_when_no_fill_price(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(order_type="market")
        result = fb.submit_order(intent)
        assert result.status       == "accepted"
        assert result.filled_price is None

    def test_market_order_accepted_when_fill_immediately_false(self):
        fb     = FakeBrokerAdapter(fill_immediately=False)
        intent = _intent(order_type="market", limit_price=450.0)
        result = fb.submit_order(intent)
        assert result.status       == "accepted"
        assert result.filled_price is None

    def test_limit_order_always_accepted(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(order_type="limit", limit_price=449.0)
        result = fb.submit_order(intent)
        assert result.status == "accepted"

    def test_stop_order_always_accepted(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(order_type="stop", stop_price=445.0)
        result = fb.submit_order(intent)
        assert result.status == "accepted"

    def test_result_echoes_symbol_and_side(self):
        fb     = FakeBrokerAdapter()
        intent = _intent(symbol="QQQ", side="sell", quantity=50.0)
        result = fb.submit_order(intent)
        assert result.symbol   == "QQQ"
        assert result.side     == "sell"
        assert result.quantity == 50.0

    def test_order_count_increments(self):
        fb = FakeBrokerAdapter()
        assert fb.order_count == 0
        fb.submit_order(_intent())
        assert fb.order_count == 1
        fb.submit_order(_intent())
        assert fb.order_count == 2


# ===========================================================================
# FakeBrokerAdapter — get_positions
# ===========================================================================

class TestFakeBrokerGetPositions:
    def test_empty_when_no_orders(self):
        fb = FakeBrokerAdapter()
        assert fb.get_positions() == {}

    def test_returns_dict(self):
        fb = FakeBrokerAdapter()
        fb.submit_order(_intent(limit_price=450.0))
        assert isinstance(fb.get_positions(), dict)

    def test_filled_buy_creates_position(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        intent = _intent(symbol="SPY", side="buy", quantity=10.0, limit_price=450.0)
        fb.submit_order(intent)
        pos = fb.get_positions()
        assert "SPY" in pos
        assert pos["SPY"]["quantity"] == 10.0

    def test_avg_price_matches_fill_price(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=10.0, limit_price=400.0))
        assert fb.get_positions()["SPY"]["avg_price"] == 400.0

    def test_filled_sell_reduces_position(self):
        fb = FakeBrokerAdapter(fill_immediately=True)
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=10.0, limit_price=450.0))
        fb.submit_order(_intent(symbol="SPY", side="sell", quantity=10.0, limit_price=455.0))
        pos = fb.get_positions()
        assert "SPY" not in pos  # net zero — position removed

    def test_partial_sell_leaves_residual(self):
        fb = FakeBrokerAdapter(fill_immediately=True)
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=10.0, limit_price=450.0))
        fb.submit_order(_intent(symbol="SPY", side="sell", quantity=4.0,  limit_price=455.0))
        pos = fb.get_positions()
        assert "SPY" in pos
        assert round(pos["SPY"]["quantity"], 6) == 6.0

    def test_accepted_orders_not_counted(self):
        fb = FakeBrokerAdapter(fill_immediately=False)
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=10.0, limit_price=450.0))
        assert fb.get_positions() == {}

    def test_multiple_symbols_tracked_independently(self):
        fb = FakeBrokerAdapter(fill_immediately=True)
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=10.0, limit_price=450.0))
        fb.submit_order(_intent(symbol="QQQ", side="buy", quantity=5.0,  limit_price=380.0))
        pos = fb.get_positions()
        assert "SPY" in pos
        assert "QQQ" in pos


# ===========================================================================
# FakeBrokerAdapter — get_account
# ===========================================================================

class TestFakeBrokerGetAccount:
    def test_returns_dict(self):
        assert isinstance(FakeBrokerAdapter().get_account(), dict)

    def test_initial_cash_with_no_orders(self):
        fb = FakeBrokerAdapter(initial_cash=50_000.0)
        assert fb.get_account()["cash"] == 50_000.0

    def test_cash_decreases_after_buy(self):
        fb = FakeBrokerAdapter(fill_immediately=True, initial_cash=100_000.0)
        fb.submit_order(_intent(side="buy", quantity=10.0, limit_price=400.0))
        # 10 * 400 = 4_000 spent
        assert fb.get_account()["cash"] == 96_000.0

    def test_cash_increases_after_sell(self):
        fb = FakeBrokerAdapter(fill_immediately=True, initial_cash=100_000.0)
        fb.submit_order(_intent(side="buy",  quantity=10.0, limit_price=400.0))
        fb.submit_order(_intent(side="sell", quantity=10.0, limit_price=410.0))
        # -4000 + 4100 = +100
        assert fb.get_account()["cash"] == 100_100.0

    def test_account_currency_is_usd(self):
        assert FakeBrokerAdapter().get_account()["currency"] == "USD"

    def test_account_status_is_active(self):
        assert FakeBrokerAdapter().get_account()["status"] == "active"

    def test_account_broker_is_fake(self):
        assert FakeBrokerAdapter().get_account()["broker"] == "fake"

    def test_accepted_orders_not_counted(self):
        fb = FakeBrokerAdapter(fill_immediately=False, initial_cash=100_000.0)
        fb.submit_order(_intent(side="buy", quantity=10.0, limit_price=400.0))
        assert fb.get_account()["cash"] == 100_000.0


# ===========================================================================
# FakeBrokerAdapter — cancel_order
# ===========================================================================

class TestFakeBrokerCancelOrder:
    def test_cancel_unknown_order_returns_false(self):
        fb = FakeBrokerAdapter()
        assert fb.cancel_order("FAKE-999999") is False

    def test_cancel_accepted_order_returns_true(self):
        fb     = FakeBrokerAdapter(fill_immediately=False)
        result = fb.submit_order(_intent(order_type="limit", limit_price=449.0))
        assert fb.cancel_order(result.order_id) is True

    def test_cancelled_order_status_is_cancelled(self):
        fb     = FakeBrokerAdapter(fill_immediately=False)
        result = fb.submit_order(_intent(order_type="limit", limit_price=449.0))
        fb.cancel_order(result.order_id)
        _, updated = fb.get_order(result.order_id)
        assert updated.status == "cancelled"

    def test_cancel_filled_order_returns_false(self):
        fb     = FakeBrokerAdapter(fill_immediately=True)
        result = fb.submit_order(_intent(limit_price=450.0))
        assert fb.cancel_order(result.order_id) is False

    def test_cancel_already_cancelled_order_returns_false(self):
        fb     = FakeBrokerAdapter(fill_immediately=False)
        result = fb.submit_order(_intent(order_type="limit", limit_price=449.0))
        fb.cancel_order(result.order_id)
        assert fb.cancel_order(result.order_id) is False


# ===========================================================================
# FakeBrokerAdapter — inspection helpers
# ===========================================================================

class TestFakeBrokerInspectionHelpers:
    def test_get_order_returns_none_for_unknown(self):
        fb = FakeBrokerAdapter()
        assert fb.get_order("FAKE-999999") is None

    def test_get_order_returns_tuple_for_known(self):
        fb     = FakeBrokerAdapter()
        result = fb.submit_order(_intent())
        pair   = fb.get_order(result.order_id)
        assert pair is not None
        intent_back, result_back = pair
        assert result_back.order_id == result.order_id

    def test_all_orders_returns_list(self):
        fb = FakeBrokerAdapter()
        assert isinstance(fb.all_orders(), list)

    def test_all_orders_length_matches_order_count(self):
        fb = FakeBrokerAdapter()
        fb.submit_order(_intent())
        fb.submit_order(_intent())
        assert len(fb.all_orders()) == fb.order_count == 2

    def test_all_orders_returns_copy(self):
        fb = FakeBrokerAdapter()
        fb.submit_order(_intent())
        lst = fb.all_orders()
        lst.clear()
        assert fb.order_count == 1


# ===========================================================================
# FakeBrokerAdapter — position accounting correctness
# ===========================================================================

class TestFakeBrokerPositionAccounting:
    """Verify that avg_price is tracked correctly across buys and sells."""

    def _fb(self) -> FakeBrokerAdapter:
        return FakeBrokerAdapter(fill_immediately=True, initial_cash=100_000.0)

    def test_single_buy_creates_correct_position(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=100.0, limit_price=10.0))
        pos = fb.get_positions()
        assert pos["SPY"]["quantity"]  == 100.0
        assert pos["SPY"]["avg_price"] == 10.0

    def test_two_buys_weighted_average(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=100.0, limit_price=10.0))
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=100.0, limit_price=20.0))
        pos = fb.get_positions()
        assert pos["SPY"]["quantity"]  == 200.0
        assert pos["SPY"]["avg_price"] == 15.0

    def test_partial_sell_preserves_avg_price(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=100.0, limit_price=10.0))
        fb.submit_order(_intent(symbol="SPY", side="sell", quantity=50.0,  limit_price=12.0))
        pos = fb.get_positions()
        assert pos["SPY"]["quantity"]  == 50.0
        assert pos["SPY"]["avg_price"] == 10.0  # unchanged by sell

    def test_full_sell_removes_position(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=100.0, limit_price=10.0))
        fb.submit_order(_intent(symbol="SPY", side="sell", quantity=100.0, limit_price=12.0))
        assert fb.get_positions() == {}

    def test_oversized_sell_is_rejected(self):
        fb     = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=100.0, limit_price=10.0))
        result = fb.submit_order(_intent(symbol="SPY", side="sell", quantity=150.0, limit_price=12.0))
        assert result.status       == "rejected"
        assert result.filled_price is None
        assert result.filled_at    is None

    def test_rejected_sell_does_not_change_position(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=100.0, limit_price=10.0))
        fb.submit_order(_intent(symbol="SPY", side="sell", quantity=150.0, limit_price=12.0))
        pos = fb.get_positions()
        assert pos["SPY"]["quantity"]  == 100.0
        assert pos["SPY"]["avg_price"] == 10.0

    def test_rejected_sell_does_not_affect_cash(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy",  quantity=100.0, limit_price=10.0))
        cash_after_buy = fb.get_account()["cash"]
        fb.submit_order(_intent(symbol="SPY", side="sell", quantity=150.0, limit_price=12.0))
        assert fb.get_account()["cash"] == cash_after_buy

    def test_sell_with_no_position_is_rejected(self):
        fb     = self._fb()
        result = fb.submit_order(_intent(symbol="SPY", side="sell", quantity=10.0, limit_price=12.0))
        assert result.status == "rejected"

    def test_get_positions_returns_independent_copy(self):
        fb = self._fb()
        fb.submit_order(_intent(symbol="SPY", side="buy", quantity=100.0, limit_price=10.0))
        pos = fb.get_positions()
        pos["SPY"]["quantity"] = 999.0
        assert fb.get_positions()["SPY"]["quantity"] == 100.0
