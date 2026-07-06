"""Tests for the minimal Alpaca paper adapter — S50.

All tests use an injected mock client. No real Alpaca API calls.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.broker.alpaca_paper_adapter import (
    AlpacaPaperAdapter,
    AlpacaPaperAdapterError,
)


def _ns(**fields) -> SimpleNamespace:
    return SimpleNamespace(**fields)


def _account(**overrides):
    defaults = dict(
        status="ACTIVE",
        cash="100000.50",
        buying_power="200000.00",
        equity="150000.25",
        currency="USD",
        pattern_day_trader=False,
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _position(**overrides):
    defaults = dict(
        symbol="SPY",
        qty="10",
        side="long",
        avg_entry_price="450.00",
        market_value="4550.00",
        unrealized_pl="50.00",
        current_price="455.00",
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _order(**overrides):
    defaults = dict(
        id="ord-1",
        client_order_id="cid-1",
        symbol="SPY",
        side="buy",
        type="market",
        time_in_force="day",
        qty="1",
        filled_qty="0",
        filled_avg_price=None,
        status="new",
        submitted_at="2026-06-23T14:30:00Z",
        filled_at=None,
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _clock(**overrides):
    defaults = dict(
        timestamp="2026-06-23T14:30:00Z",
        is_open=True,
        next_open="2026-06-24T09:30:00-04:00",
        next_close="2026-06-23T16:00:00-04:00",
    )
    defaults.update(overrides)
    return _ns(**defaults)


def _adapter(client=None) -> AlpacaPaperAdapter:
    if client is None:
        client = MagicMock()
    return AlpacaPaperAdapter(client=client, paper=True)


class TestConstruction:
    def test_paper_true_with_client_constructs(self):
        a = _adapter()
        assert isinstance(a, AlpacaPaperAdapter)

    def test_paper_false_raises(self):
        with pytest.raises(AlpacaPaperAdapterError):
            AlpacaPaperAdapter(client=MagicMock(), paper=False)

    def test_no_client_raises(self):
        with pytest.raises(AlpacaPaperAdapterError):
            AlpacaPaperAdapter(client=None, paper=True)

    def test_live_base_url_raises(self):
        with pytest.raises(AlpacaPaperAdapterError):
            AlpacaPaperAdapter(
                client=MagicMock(),
                paper=True,
                base_url="https://api.alpaca.markets",
            )

    def test_paper_base_url_accepted(self):
        a = AlpacaPaperAdapter(
            client=MagicMock(),
            paper=True,
            base_url="https://paper-api.alpaca.markets",
        )
        assert isinstance(a, AlpacaPaperAdapter)

    def test_empty_base_url_accepted(self):
        a = AlpacaPaperAdapter(client=MagicMock(), paper=True, base_url=None)
        assert isinstance(a, AlpacaPaperAdapter)


class TestFromEnvironment:
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": "s"}, clear=False):
            with pytest.raises(AlpacaPaperAdapterError):
                AlpacaPaperAdapter.from_environment()

    def test_missing_secret_key_raises(self):
        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": ""}, clear=False):
            with pytest.raises(AlpacaPaperAdapterError):
                AlpacaPaperAdapter.from_environment()

    def test_live_base_url_in_env_raises(self):
        with patch.dict(
            os.environ,
            {
                "ALPACA_API_KEY": "k",
                "ALPACA_SECRET_KEY": "s",
                "ALPACA_PAPER_BASE_URL": "https://api.alpaca.markets/v2",
            },
            clear=False,
        ):
            with pytest.raises(AlpacaPaperAdapterError):
                AlpacaPaperAdapter.from_environment()

    def test_factory_constructs_with_real_sdk_mocked(self):
        fake_client = MagicMock()
        with patch.dict(
            os.environ,
            {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"},
            clear=False,
        ), patch(
            "alpaca.trading.client.TradingClient",
            return_value=fake_client,
            create=True,
        ):
            os.environ.pop("ALPACA_PAPER_BASE_URL", None)
            a = AlpacaPaperAdapter.from_environment()
        assert isinstance(a, AlpacaPaperAdapter)
        assert a._client is fake_client


class TestGetAccount:
    def test_returns_dict_with_expected_keys(self):
        client = MagicMock()
        client.get_account.return_value = _account()
        a = _adapter(client)
        res = a.get_account()
        assert res == {
            "status": "ACTIVE",
            "cash": 100000.50,
            "buying_power": 200000.00,
            "equity": 150000.25,
            "currency": "USD",
            "pattern_day_trader": False,
        }

    def test_broker_exception_raises_adapter_error(self):
        client = MagicMock()
        client.get_account.side_effect = RuntimeError("network down")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.get_account()
        assert "get_account" in str(ei.value)

    def test_handles_missing_fields(self):
        client = MagicMock()
        client.get_account.return_value = _ns(status="ACTIVE")
        a = _adapter(client)
        res = a.get_account()
        assert res["status"] == "ACTIVE"
        assert res["cash"] is None
        assert res["buying_power"] is None


class TestGetPositions:
    def test_returns_normalized_list(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position()]
        a = _adapter(client)
        res = a.get_positions()
        assert len(res) == 1
        assert res[0]["symbol"] == "SPY"
        assert res[0]["qty"] == 10.0
        assert res[0]["avg_entry_price"] == 450.0

    def test_empty_positions(self):
        client = MagicMock()
        client.get_all_positions.return_value = []
        a = _adapter(client)
        assert a.get_positions() == []

    def test_broker_exception_raises(self):
        client = MagicMock()
        client.get_all_positions.side_effect = RuntimeError("fail")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_positions()


class TestGetOpenOrders:
    def test_returns_normalized_list(self):
        client = MagicMock()
        client.get_orders.return_value = [_order()]
        a = _adapter(client)
        res = a.get_open_orders()
        assert len(res) == 1
        assert res[0]["id"] == "ord-1"
        assert res[0]["symbol"] == "SPY"
        assert res[0]["status"] == "new"

    def test_empty_orders(self):
        client = MagicMock()
        client.get_orders.return_value = []
        a = _adapter(client)
        assert a.get_open_orders() == []

    def test_broker_exception_raises(self):
        client = MagicMock()
        client.get_orders.side_effect = RuntimeError("fail")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_open_orders()


class TestGetClock:
    def test_returns_clock_dict(self):
        client = MagicMock()
        client.get_clock.return_value = _clock()
        a = _adapter(client)
        res = a.get_clock()
        assert res["is_open"] is True
        assert res["timestamp"] == "2026-06-23T14:30:00Z"
        assert "next_open" in res
        assert "next_close" in res

    def test_market_closed(self):
        client = MagicMock()
        client.get_clock.return_value = _clock(is_open=False)
        a = _adapter(client)
        res = a.get_clock()
        assert res["is_open"] is False

    def test_broker_exception_raises(self):
        client = MagicMock()
        client.get_clock.side_effect = RuntimeError("fail")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_clock()


class TestSubmitMarketOrder:
    def test_buy_returns_normalized_order(self):
        client = MagicMock()
        client.submit_order.return_value = _order(side="buy")
        a = _adapter(client)
        res = a.submit_market_order("SPY", 1, "buy")
        assert res["symbol"] == "SPY"
        assert res["side"] == "buy"
        assert res["status"] == "new"
        assert client.submit_order.call_count == 1

    def test_sell_returns_normalized_order(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(qty="10")]
        client.submit_order.return_value = _order(side="sell", id="ord-2")
        a = _adapter(client)
        res = a.submit_market_order("SPY", 1, "sell")
        assert res["side"] == "sell"
        assert res["id"] == "ord-2"

    def test_client_order_id_passed_through_when_provided(self):
        client = MagicMock()
        client.submit_order.return_value = _order(client_order_id="cid-xyz")
        a = _adapter(client)
        res = a.submit_market_order("SPY", 1, "buy", client_order_id="cid-xyz")
        assert res["client_order_id"] == "cid-xyz"

    @pytest.mark.parametrize("symbol", ["AAPL", "QQQ", "spy", "", "SPY ", None])
    def test_non_spy_symbol_rejected(self, symbol):
        a = _adapter()
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order(symbol, 1, "buy")

    @pytest.mark.parametrize("side", ["short", "BUY", "", "long", None, "sell_short"])
    def test_invalid_side_rejected(self, side):
        a = _adapter()
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", 1, side)

    @pytest.mark.parametrize("qty", [0, -1, -0.5, None, "1", True, float("nan"), float("inf"), float("-inf")])
    def test_invalid_quantity_rejected(self, qty):
        a = _adapter()
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", qty, "buy")

    def test_valid_quantity_float_accepted(self):
        client = MagicMock()
        client.submit_order.return_value = _order(qty="1.5")
        a = _adapter(client)
        res = a.submit_market_order("SPY", 1.5, "buy")
        assert res["qty"] == 1.5

    def test_broker_exception_raises_adapter_error(self):
        client = MagicMock()
        client.submit_order.side_effect = RuntimeError("rate limit")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 1, "buy")
        assert "submit_market_order" in str(ei.value)

    def test_no_retry_on_failure(self):
        client = MagicMock()
        client.submit_order.side_effect = RuntimeError("transient")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", 1, "buy")
        # Exactly one submission attempt — no automatic retry.
        assert client.submit_order.call_count == 1


class TestSubmitOrderSingleCall:
    def test_typeerror_performs_exactly_one_call(self):
        client = MagicMock()
        client.submit_order.side_effect = TypeError(
            "submit_order() got unexpected keyword argument 'order_data'"
        )
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", 1, "buy")
        assert client.submit_order.call_count == 1

    def test_uses_order_data_keyword(self):
        client = MagicMock()
        client.submit_order.return_value = _order()
        a = _adapter(client)
        a.submit_market_order("SPY", 1, "buy")
        _, kwargs = client.submit_order.call_args
        assert "order_data" in kwargs
        assert kwargs["order_data"] is not None

    def test_no_positional_fallback_after_typeerror(self):
        client = MagicMock()
        client.submit_order.side_effect = TypeError("rejected kwarg")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", 1, "buy")
        # All call_args must use the kwarg form; no positional retry.
        assert client.submit_order.call_count == 1
        args, kwargs = client.submit_order.call_args
        assert args == ()
        assert "order_data" in kwargs

    def test_generic_exception_performs_one_call(self):
        client = MagicMock()
        client.submit_order.side_effect = RuntimeError("net")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", 1, "buy")
        assert client.submit_order.call_count == 1


class TestLongOnlySellGuard:
    def test_sell_equal_to_held_passes(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(qty="5")]
        client.submit_order.return_value = _order(side="sell")
        a = _adapter(client)
        res = a.submit_market_order("SPY", 5, "sell")
        assert res["side"] == "sell"
        assert client.submit_order.call_count == 1

    def test_sell_below_held_passes(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(qty="10")]
        client.submit_order.return_value = _order(side="sell")
        a = _adapter(client)
        res = a.submit_market_order("SPY", 3, "sell")
        assert res["side"] == "sell"
        assert client.submit_order.call_count == 1

    def test_sell_with_no_position_blocks(self):
        client = MagicMock()
        client.get_all_positions.return_value = []
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 1, "sell")
        assert "no SPY position" in str(ei.value)
        assert client.submit_order.call_count == 0

    def test_sell_above_held_blocks(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(qty="3")]
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 5, "sell")
        assert "exceeds held" in str(ei.value)
        assert client.submit_order.call_count == 0

    def test_short_position_blocks(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(qty="5", side="short")]
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 1, "sell")
        assert "not long" in str(ei.value)
        assert client.submit_order.call_count == 0

    @pytest.mark.parametrize("bad_qty", [
        None, "abc", float("nan"), float("inf"), float("-inf"), -1, 0,
    ])
    def test_malformed_position_qty_blocks(self, bad_qty):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(qty=bad_qty)]
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 1, "sell")
        msg = str(ei.value)
        assert "malformed" in msg or "no SPY position" in msg
        assert client.submit_order.call_count == 0

    def test_missing_position_side_blocks(self):
        client = MagicMock()
        pos = _ns(symbol="SPY", qty="5")
        client.get_all_positions.return_value = [pos]
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 1, "sell")
        assert "not long" in str(ei.value)
        assert client.submit_order.call_count == 0

    def test_unrelated_symbol_position_ignored(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_position(symbol="QQQ", qty="100")]
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError) as ei:
            a.submit_market_order("SPY", 1, "sell")
        assert "no SPY position" in str(ei.value)
        assert client.submit_order.call_count == 0

    def test_buy_does_not_consult_positions(self):
        client = MagicMock()
        client.submit_order.return_value = _order()
        a = _adapter(client)
        a.submit_market_order("SPY", 1, "buy")
        assert client.get_all_positions.call_count == 0

    def test_position_read_exception_blocks_and_no_submit(self):
        client = MagicMock()
        client.get_all_positions.side_effect = RuntimeError("net")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.submit_market_order("SPY", 1, "sell")
        assert client.submit_order.call_count == 0


class TestCancelOrder:
    def test_cancel_success(self):
        client = MagicMock()
        client.cancel_order_by_id.return_value = None
        a = _adapter(client)
        res = a.cancel_order("ord-1")
        assert res == {"order_id": "ord-1", "status": "cancel_requested"}
        assert client.cancel_order_by_id.call_count == 1

    def test_cancel_falls_back_to_cancel_order(self):
        client = MagicMock(spec=["cancel_order"])
        a = _adapter(client)
        res = a.cancel_order("ord-2")
        assert res["order_id"] == "ord-2"
        assert client.cancel_order.call_count == 1

    @pytest.mark.parametrize("bad", [None, "", "  ", 42, ["x"]])
    def test_invalid_order_id_rejected(self, bad):
        a = _adapter()
        with pytest.raises(AlpacaPaperAdapterError):
            a.cancel_order(bad)

    def test_broker_exception_raises(self):
        client = MagicMock()
        client.cancel_order_by_id.side_effect = RuntimeError("not found")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.cancel_order("ord-x")


class TestNoCredentialLogging:
    def test_no_print_in_module(self):
        import inspect
        import src.broker.alpaca_paper_adapter as mod
        source = inspect.getsource(mod)
        assert "print(" not in source

    def test_no_logger_in_module(self):
        import inspect
        import src.broker.alpaca_paper_adapter as mod
        source = inspect.getsource(mod)
        assert "logging" not in source
        assert "log" + "ger" not in source

    def test_error_message_does_not_echo_credentials(self):
        with patch.dict(
            os.environ,
            {"ALPACA_API_KEY": "secret-key-value", "ALPACA_SECRET_KEY": ""},
            clear=False,
        ):
            try:
                AlpacaPaperAdapter.from_environment()
            except AlpacaPaperAdapterError as e:
                assert "secret-key-value" not in str(e)


class TestEnumNormalization:
    """Cover real Alpaca SDK enum values at the _to_str boundary."""

    def test_account_status_active_enum_normalized(self):
        from enum import Enum
        from src.broker.alpaca_paper_adapter import _to_str

        class AccountStatus(str, Enum):
            ACTIVE = "ACTIVE"
            ACCOUNT_CLOSED = "ACCOUNT_CLOSED"
        assert _to_str(AccountStatus.ACTIVE) == "ACTIVE"

    def test_account_status_closed_enum_normalized(self):
        from enum import Enum
        from src.broker.alpaca_paper_adapter import _to_str

        class AccountStatus(str, Enum):
            ACTIVE = "ACTIVE"
            ACCOUNT_CLOSED = "ACCOUNT_CLOSED"
        assert _to_str(AccountStatus.ACCOUNT_CLOSED) == "ACCOUNT_CLOSED"

    def test_plain_active_string_unchanged(self):
        from src.broker.alpaca_paper_adapter import _to_str
        assert _to_str("ACTIVE") == "ACTIVE"

    def test_order_side_buy_enum_normalized(self):
        from enum import Enum
        from src.broker.alpaca_paper_adapter import _to_str

        class OrderSide(str, Enum):
            BUY = "buy"
            SELL = "sell"
        # Alpaca-py exposes the SDK string via .value.
        assert _to_str(OrderSide.BUY) == "buy"
        assert _to_str(OrderSide.SELL) == "sell"

    def test_order_status_new_enum_normalized(self):
        from enum import Enum
        from src.broker.alpaca_paper_adapter import _to_str

        class OrderStatus(str, Enum):
            NEW = "new"
            FILLED = "filled"
        assert _to_str(OrderStatus.NEW) == "new"
        assert _to_str(OrderStatus.FILLED) == "filled"

    def test_time_in_force_day_enum_normalized(self):
        from enum import Enum
        from src.broker.alpaca_paper_adapter import _to_str

        class TimeInForce(str, Enum):
            DAY = "day"
            GTC = "gtc"
        assert _to_str(TimeInForce.DAY) == "day"

    def test_order_type_market_enum_normalized(self):
        from enum import Enum
        from src.broker.alpaca_paper_adapter import _to_str

        class OrderType(str, Enum):
            MARKET = "market"
            LIMIT = "limit"
        assert _to_str(OrderType.MARKET) == "market"

    def test_string_prefix_path_still_works(self):
        # Plain strings that look like enum reprs should still be stripped.
        from src.broker.alpaca_paper_adapter import _to_str
        assert _to_str("OrderSide.BUY") == "BUY"
        assert _to_str("OrderStatus.PENDING_NEW") == "PENDING_NEW"

    def test_datetime_value_preserved(self):
        from datetime import datetime, timezone
        from src.broker.alpaca_paper_adapter import _to_str
        dt = datetime(2026, 6, 23, 14, 30, tzinfo=timezone.utc)
        # Not an Enum; str() representation is preserved.
        assert _to_str(dt) == str(dt)

    def test_uuid_value_preserved(self):
        import uuid
        from src.broker.alpaca_paper_adapter import _to_str
        u = uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert _to_str(u) == str(u)

    def test_get_account_with_enum_returns_active(self):
        from enum import Enum
        from types import SimpleNamespace

        class AccountStatus(str, Enum):
            ACTIVE = "ACTIVE"
        client = MagicMock()
        client.get_account.return_value = SimpleNamespace(
            status=AccountStatus.ACTIVE,
            cash="100000.0",
            buying_power="200000.0",
            equity="150000.0",
            currency="USD",
            pattern_day_trader=False,
        )
        a = AlpacaPaperAdapter(client=client, paper=True)
        res = a.get_account()
        assert res["status"] == "ACTIVE"

    def test_no_substring_match_on_status(self):
        # "ACTIVE" as a substring of a longer string must not match.
        from src.broker.alpaca_paper_adapter import _to_str
        assert _to_str("INACTIVE") == "INACTIVE"
        assert _to_str("ACCOUNT_ACTIVE_PENDING") == "ACCOUNT_ACTIVE_PENDING"


class TestGetPosition:
    def test_returns_normalized_dict(self):
        client = MagicMock()
        client.get_open_position.return_value = _position(qty="5")
        a = _adapter(client)
        res = a.get_position("SPY")
        assert res is not None
        assert res["symbol"] == "SPY"
        assert res["qty"] == 5.0

    def test_returns_none_when_broker_says_not_found(self):
        client = MagicMock()
        client.get_open_position.side_effect = RuntimeError("position not found")
        a = _adapter(client)
        assert a.get_position("SPY") is None

    def test_returns_none_on_404_status(self):
        client = MagicMock()
        exc = RuntimeError("boom")
        exc.status_code = 404  # type: ignore[attr-defined]
        client.get_open_position.side_effect = exc
        a = _adapter(client)
        assert a.get_position("SPY") is None

    def test_wraps_other_errors(self):
        client = MagicMock()
        client.get_open_position.side_effect = RuntimeError("500 server error")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_position("SPY")

    @pytest.mark.parametrize("bad_symbol", ["AAPL", "spy", ""])
    def test_non_spy_symbol_rejected(self, bad_symbol):
        a = _adapter()
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_position(bad_symbol)


class TestListOpenOrders:
    def test_unfiltered_returns_all(self):
        client = MagicMock()
        client.get_orders.return_value = [
            _order(symbol="SPY"), _order(symbol="QQQ", id="ord-2"),
        ]
        a = _adapter(client)
        res = a.list_open_orders()
        assert len(res) == 2

    def test_symbol_filter_applied(self):
        client = MagicMock()
        client.get_orders.return_value = [
            _order(symbol="SPY"), _order(symbol="QQQ", id="ord-2"),
        ]
        a = _adapter(client)
        res = a.list_open_orders(symbol="SPY")
        assert len(res) == 1
        assert res[0]["symbol"] == "SPY"

    def test_empty(self):
        client = MagicMock()
        client.get_orders.return_value = []
        a = _adapter(client)
        assert a.list_open_orders(symbol="SPY") == []


class TestGetOrder:
    def test_returns_normalized_order(self):
        client = MagicMock()
        client.get_order_by_id.return_value = _order(id="ord-42", status="filled")
        a = _adapter(client)
        res = a.get_order("ord-42")
        assert res["id"] == "ord-42"
        assert res["status"] == "filled"

    def test_broker_exception_wraps(self):
        client = MagicMock()
        client.get_order_by_id.side_effect = RuntimeError("network down")
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_order("ord-1")

    @pytest.mark.parametrize("bad", [None, "", "   ", 42])
    def test_invalid_id_rejected(self, bad):
        a = _adapter()
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_order(bad)


class TestGetOrderByClientOrderId:
    def test_returns_normalized_order(self):
        client = MagicMock()
        client.get_order_by_client_order_id.return_value = _order(
            id="ord-x", client_order_id="cid-abc", status="filled",
        )
        a = _adapter(client)
        res = a.get_order_by_client_order_id("cid-abc")
        assert res is not None
        assert res["client_order_id"] == "cid-abc"

    def test_returns_none_when_not_found(self):
        client = MagicMock()
        client.get_order_by_client_order_id.side_effect = RuntimeError(
            "order not found",
        )
        a = _adapter(client)
        assert a.get_order_by_client_order_id("missing") is None

    def test_returns_none_on_404(self):
        client = MagicMock()
        exc = RuntimeError("boom")
        exc.status_code = 404  # type: ignore[attr-defined]
        client.get_order_by_client_order_id.side_effect = exc
        a = _adapter(client)
        assert a.get_order_by_client_order_id("missing") is None

    def test_wraps_other_errors(self):
        client = MagicMock()
        client.get_order_by_client_order_id.side_effect = RuntimeError(
            "500 something else",
        )
        a = _adapter(client)
        with pytest.raises(AlpacaPaperAdapterError):
            a.get_order_by_client_order_id("cid-abc")


class TestNoLiveTradingSupport:
    def test_module_has_no_live_methods(self):
        import src.broker.alpaca_paper_adapter as mod
        forbidden = [
            "live_submit",
            "submit_live_order",
            "enable_live",
            "switch_to_live",
        ]
        for name in forbidden:
            assert not hasattr(mod, name)
            assert not hasattr(AlpacaPaperAdapter, name)

    def test_no_paper_false_path(self):
        with pytest.raises(AlpacaPaperAdapterError):
            AlpacaPaperAdapter(client=MagicMock(), paper=False)
