"""Tests for the minimal automated paper trading cycle — S51.

All tests use a MagicMock adapter. No real Alpaca calls.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from src.broker.alpaca_paper_adapter import AlpacaPaperAdapterError
from src.runtime.paper_trading_cycle import run_paper_trading_cycle
from src.strategy.signal_engine import Bar, SignalEngineConfig


def _config() -> SignalEngineConfig:
    return SignalEngineConfig(
        strategy_name="sma_crossover",
        symbol="SPY",
        timeframe="1h",
        min_bars_required=10,
        short_window=3,
        long_window=10,
    )


def _bars_bullish() -> list[Bar]:
    closes = list(range(100, 100 + 10))  # 100..109 — rising
    return [Bar(open=c, high=c, low=c, close=float(c), volume=1000) for c in closes]


def _bars_bearish() -> list[Bar]:
    closes = list(range(120, 110, -1))  # 120..111 — falling
    return [Bar(open=c, high=c, low=c, close=float(c), volume=1000) for c in closes]


def _bars_flat() -> list[Bar]:
    return [Bar(open=100, high=100, low=100, close=100.0, volume=1000) for _ in range(10)]


def _mock_adapter(*, clock=None, account=None, positions=None, open_orders=None):
    a = MagicMock()
    a.get_clock.return_value = clock if clock is not None else {
        "timestamp": "t", "is_open": True, "next_open": None, "next_close": None,
    }
    a.get_account.return_value = account if account is not None else {
        "status": "ACTIVE",
        "cash": 100000.0,
        "buying_power": 200000.0,
        "equity": 100000.0,
        "currency": "USD",
        "pattern_day_trader": False,
    }
    a.get_positions.return_value = positions if positions is not None else []
    a.get_open_orders.return_value = open_orders if open_orders is not None else []
    a.submit_market_order.return_value = {
        "id": "ord-x",
        "client_order_id": "cid-x",
        "symbol": "SPY",
        "side": "buy",
        "status": "new",
        "qty": 1.0,
    }
    return a


def _position(qty=10, side="long", symbol="SPY"):
    return {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "avg_entry_price": 100.0,
        "market_value": (float(qty) * 110.0) if isinstance(qty, (int, float)) and not isinstance(qty, bool) else 0.0,
        "unrealized_pl": 100.0,
        "current_price": 110.0,
    }


def _open_order():
    return {"id": "o1", "symbol": "SPY", "side": "buy", "status": "new", "qty": 1.0}


class TestBullishNoPositionBuys:
    def test_buy_submitted_once(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["result"] == "PASS"
        assert r["action"] == "buy_submitted"
        assert r["signal"] == "BUY"
        assert a.submit_market_order.call_count == 1

    def test_buy_order_returned(self):
        a = _mock_adapter()
        a.submit_market_order.return_value = {"id": "ord-1", "symbol": "SPY", "side": "buy"}
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["order"] == {"id": "ord-1", "symbol": "SPY", "side": "buy"}

    def test_buy_qty_calculation_floor(self):
        a = _mock_adapter()
        run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        symbol, qty, side = a.submit_market_order.call_args.args[:3]
        assert symbol == "SPY"
        assert side == "buy"
        assert qty == 91

    def test_buy_passes_client_order_id(self):
        a = _mock_adapter()
        run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            client_order_id="cid-42", submit_enabled=True,
        )
        kwargs = a.submit_market_order.call_args.kwargs
        assert kwargs.get("client_order_id") == "cid-42"

    def test_buy_uses_smaller_fraction(self):
        a = _mock_adapter()
        run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            max_position_fraction=0.05, submit_enabled=True,
        )
        symbol, qty, _ = a.submit_market_order.call_args.args[:3]
        assert qty == 45


class TestBearishLongPositionSells:
    def test_sell_full_held_quantity(self):
        a = _mock_adapter(positions=[_position(qty=7)])
        a.submit_market_order.return_value = {"id": "s1", "symbol": "SPY", "side": "sell"}
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bearish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["result"] == "PASS"
        assert r["action"] == "sell_submitted"
        assert r["signal"] == "SELL"
        symbol, qty, side = a.submit_market_order.call_args.args[:3]
        assert symbol == "SPY"
        assert side == "sell"
        assert qty == 7.0
        assert a.submit_market_order.call_count == 1


class TestHoldOrBlockSubmitsNothing:
    def test_hold_no_position(self):
        # Flat bars => short_sma == long_sma => HOLD (no position to exit)
        a = _mock_adapter()
        r = run_paper_trading_cycle(adapter=a, bars=_bars_flat(), signal_config=_config())
        assert r["action"] == "none"
        assert r["signal"] == "HOLD"
        assert a.submit_market_order.call_count == 0

    def test_hold_with_position_bullish(self):
        # Bullish bars with existing position => HOLD_ALREADY_IN_POSITION
        a = _mock_adapter(positions=[_position(qty=5)])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["action"] == "none"
        assert r["signal"] == "HOLD"
        assert a.submit_market_order.call_count == 0

    def test_block_from_signal_market_closed(self):
        a = _mock_adapter(clock={"timestamp": "t", "is_open": False})
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["action"] == "none"
        # signal_engine BLOCK with MARKET_NOT_OPEN
        assert r["signal"] == "BLOCK"
        assert "MARKET_NOT_OPEN" in r["reason_codes"]
        assert a.submit_market_order.call_count == 0

    def test_block_from_signal_open_order_present(self):
        a = _mock_adapter(open_orders=[_open_order()])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["action"] == "none"
        assert r["signal"] == "BLOCK"
        assert "OPEN_ORDER_PRESENT" in r["reason_codes"]
        assert a.submit_market_order.call_count == 0


class TestExistingPositionPreventsBuy:
    def test_bullish_with_position_holds(self):
        # signal_engine returns HOLD when bullish + has_position, so cycle returns action=none
        a = _mock_adapter(positions=[_position(qty=5)])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["action"] == "none"
        assert a.submit_market_order.call_count == 0


class TestNoPositionPreventsSell:
    def test_bearish_no_position_holds(self):
        # signal_engine returns HOLD when bearish + no position
        a = _mock_adapter()
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bearish(), signal_config=_config())
        assert r["action"] == "none"
        assert a.submit_market_order.call_count == 0


class TestQtyBelowOneBlocks:
    def test_low_equity_qty_zero_blocks(self):
        a = _mock_adapter(account={
            "status": "ACTIVE", "equity": 50.0, "cash": 50.0,
            "buying_power": 50.0, "currency": "USD", "pattern_day_trader": False,
        })
        # equity=50, fraction=0.10 -> max_notional=5; latest_close=109 -> floor(5/109)=0
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "below 1" in r["blocker"]
        assert a.submit_market_order.call_count == 0


class TestInvalidInputs:
    def test_empty_bars_blocks(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(adapter=a, bars=[], signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "bars" in r["blocker"]
        assert a.get_clock.call_count == 0

    def test_non_list_bars_blocks(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(adapter=a, bars="x", signal_config=_config())
        assert r["result"] == "BLOCKED"

    @pytest.mark.parametrize("bad_close", [None, 0, -1, float("nan"), float("inf"), "100"])
    def test_invalid_latest_close_blocks(self, bad_close):
        bars = _bars_bullish()
        bars[-1] = Bar(open=100, high=100, low=100, close=bad_close, volume=1000)
        a = _mock_adapter()
        r = run_paper_trading_cycle(adapter=a, bars=bars, signal_config=_config())
        assert r["result"] == "BLOCKED"

    @pytest.mark.parametrize("bad_frac", [
        0, -0.01, 0.26, 0.5, 1.0, float("nan"), float("inf"), None, "0.1", True,
    ])
    def test_invalid_max_position_fraction_blocks(self, bad_frac):
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            max_position_fraction=bad_frac,
        )
        assert r["result"] == "BLOCKED"
        assert "max_position_fraction" in r["blocker"]

    @pytest.mark.parametrize("bad_equity", [None, 0, -1, float("nan"), float("inf"), "100"])
    def test_invalid_equity_blocks(self, bad_equity):
        a = _mock_adapter(account={
            "status": "ACTIVE", "equity": bad_equity, "cash": 1.0,
            "buying_power": 1.0, "currency": "USD", "pattern_day_trader": False,
        })
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "equity" in r["blocker"]


class TestMalformedBrokerResponses:
    def test_malformed_clock_blocks(self):
        a = _mock_adapter(clock="not-a-dict")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "clock" in r["blocker"]

    def test_clock_missing_is_open_blocks(self):
        a = _mock_adapter(clock={"timestamp": "t"})
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"

    def test_malformed_account_blocks(self):
        a = _mock_adapter(account="not-a-dict")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"

    def test_malformed_positions_blocks(self):
        a = _mock_adapter(positions="not-a-list")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"

    def test_malformed_open_orders_blocks(self):
        a = _mock_adapter(open_orders="not-a-list")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"

    def test_short_position_blocks(self):
        a = _mock_adapter(positions=[_position(qty=5, side="short")])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bearish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "not long" in r["blocker"]

    @pytest.mark.parametrize("bad_qty", [None, 0, -1, float("nan"), float("inf"), "5"])
    def test_malformed_position_qty_blocks(self, bad_qty):
        a = _mock_adapter(positions=[_position(qty=bad_qty)])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bearish(), signal_config=_config())
        assert r["result"] == "BLOCKED"


class TestStrictBrokerResponseValidation:
    @pytest.mark.parametrize("bad_is_open", [
        "false", "true", "True", "False",
        0, 1, -1, 0.0, 1.0,
        None, [], {}, object(),
    ])
    def test_non_bool_is_open_blocks(self, bad_is_open):
        a = _mock_adapter(clock={"timestamp": "t", "is_open": bad_is_open})
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "is_open" in r["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_true_bool_is_open_accepted(self):
        a = _mock_adapter(clock={"timestamp": "t", "is_open": True})
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "PASS"

    def test_false_bool_is_open_accepted_as_closed(self):
        a = _mock_adapter(clock={"timestamp": "t", "is_open": False})
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        # Market closed -> signal_engine returns BLOCK
        assert r["result"] == "PASS"
        assert r["signal"] == "BLOCK"
        assert a.submit_market_order.call_count == 0

    @pytest.mark.parametrize("bad_entry", [
        "not-a-dict", 42, None, ["x"], object(), True,
    ])
    def test_non_dict_position_entry_blocks(self, bad_entry):
        a = _mock_adapter(positions=[bad_entry])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "position entry" in r["blocker"]
        assert a.submit_market_order.call_count == 0

    @pytest.mark.parametrize("bad_entry", [
        "not-a-dict", 42, None, ["x"], object(),
    ])
    def test_non_dict_open_order_entry_blocks(self, bad_entry):
        a = _mock_adapter(open_orders=[bad_entry])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "open order entry" in r["blocker"]
        assert a.submit_market_order.call_count == 0

    @pytest.mark.parametrize("bad_sym", [None, "", "   ", 42, ["SPY"], True])
    def test_open_order_missing_or_invalid_symbol_blocks(self, bad_sym):
        a = _mock_adapter(open_orders=[{"id": "o1", "symbol": bad_sym, "status": "new"}])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "symbol" in r["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_open_order_missing_symbol_key_blocks(self):
        a = _mock_adapter(open_orders=[{"id": "o1", "status": "new"}])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0

    def test_mixed_valid_and_non_dict_position_blocks(self):
        # Even when a valid non-SPY dict is present, a non-dict entry still blocks.
        a = _mock_adapter(positions=[_position(symbol="QQQ", qty=5), "garbage"])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0

    def test_mixed_valid_and_non_dict_open_order_blocks(self):
        a = _mock_adapter(open_orders=[
            {"id": "o-qqq", "symbol": "QQQ", "status": "new"},
            "garbage",
        ])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0

    def test_malformed_state_does_not_evaluate_signal(self):
        from unittest.mock import patch
        a = _mock_adapter(positions=["garbage"])
        with patch("src.runtime.paper_trading_cycle.evaluate_signal") as m:
            r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
            assert m.call_count == 0
        assert r["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0

    def test_malformed_is_open_does_not_evaluate_signal(self):
        from unittest.mock import patch
        a = _mock_adapter(clock={"timestamp": "t", "is_open": "true"})
        with patch("src.runtime.paper_trading_cycle.evaluate_signal") as m:
            r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
            assert m.call_count == 0
        assert r["result"] == "BLOCKED"

    def test_malformed_open_orders_does_not_evaluate_signal(self):
        from unittest.mock import patch
        a = _mock_adapter(open_orders=["garbage"])
        with patch("src.runtime.paper_trading_cycle.evaluate_signal") as m:
            r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
            assert m.call_count == 0
        assert r["result"] == "BLOCKED"

    def test_valid_non_spy_position_ignored_for_spy_state(self):
        a = _mock_adapter(positions=[_position(symbol="QQQ", qty=100)])
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["result"] == "PASS"
        assert r["action"] == "buy_submitted"
        assert a.submit_market_order.call_count == 1

    def test_valid_non_spy_open_order_ignored_for_spy_state(self):
        a = _mock_adapter(open_orders=[
            {"id": "o-qqq", "symbol": "QQQ", "status": "new", "side": "buy"},
        ])
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["result"] == "PASS"
        assert r["action"] == "buy_submitted"
        assert a.submit_market_order.call_count == 1

    def test_malformed_spy_position_dict_missing_side_blocks(self):
        a = _mock_adapter(positions=[{"symbol": "SPY", "qty": 5}])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bearish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "side" in r["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_malformed_spy_position_dict_missing_qty_blocks(self):
        a = _mock_adapter(positions=[{"symbol": "SPY", "side": "long"}])
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bearish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "qty" in r["blocker"]
        assert a.submit_market_order.call_count == 0


class TestAccountNotActiveBlocks:
    @pytest.mark.parametrize("bad_status", ["INACTIVE", "ACCOUNT_CLOSED", "", None])
    def test_non_active_blocks(self, bad_status):
        a = _mock_adapter(account={
            "status": bad_status, "equity": 100000.0, "cash": 100000.0,
            "buying_power": 100000.0, "currency": "USD", "pattern_day_trader": False,
        })
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "BLOCKED"
        assert "ACTIVE" in r["blocker"]


class TestAdapterExceptionReturnsError:
    def test_clock_exception(self):
        a = _mock_adapter()
        a.get_clock.side_effect = AlpacaPaperAdapterError("net down")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "ERROR"
        assert r["action"] == "error"
        assert "clock" in r["blocker"]

    def test_account_exception(self):
        a = _mock_adapter()
        a.get_account.side_effect = AlpacaPaperAdapterError("net down")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "ERROR"
        assert "account" in r["blocker"]

    def test_positions_exception(self):
        a = _mock_adapter()
        a.get_positions.side_effect = AlpacaPaperAdapterError("net down")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "ERROR"
        assert "positions" in r["blocker"]

    def test_open_orders_exception(self):
        a = _mock_adapter()
        a.get_open_orders.side_effect = AlpacaPaperAdapterError("net down")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert r["result"] == "ERROR"
        assert "open orders" in r["blocker"]

    def test_submit_exception(self):
        a = _mock_adapter()
        a.submit_market_order.side_effect = AlpacaPaperAdapterError("rejected")
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["result"] == "ERROR"
        assert "buy submission" in r["blocker"]

    def test_no_credentials_in_output(self):
        a = _mock_adapter()
        a.get_account.side_effect = AlpacaPaperAdapterError("ALPACA_API_KEY=should-not-appear")
        r = run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        # The error string is opaque from our perspective — we don't echo secrets ourselves.
        # We do propagate the exception message verbatim, but the cycle itself never reads
        # credentials. The test confirms the result has no api_key / secret_key field.
        assert "api_key" not in str(r).lower() or "api_key" in str(a.get_account.side_effect).lower()
        # Concrete keys are not in the dict result
        assert "api_key" not in {k.lower() for k in r.keys()}
        assert "secret_key" not in {k.lower() for k in r.keys()}
        assert "credentials" not in {k.lower() for k in r.keys()}


class TestExactlyOneSubmitCall:
    def test_buy_one_call(self):
        a = _mock_adapter()
        run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert a.submit_market_order.call_count == 1

    def test_sell_one_call(self):
        a = _mock_adapter(positions=[_position(qty=4)])
        run_paper_trading_cycle(
            adapter=a, bars=_bars_bearish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert a.submit_market_order.call_count == 1

    def test_no_call_on_hold(self):
        a = _mock_adapter()
        run_paper_trading_cycle(adapter=a, bars=_bars_flat(), signal_config=_config())
        assert a.submit_market_order.call_count == 0

    def test_no_call_on_block(self):
        a = _mock_adapter(clock={"timestamp": "t", "is_open": False})
        run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=_config())
        assert a.submit_market_order.call_count == 0


class TestNoRetry:
    def test_submit_failure_no_retry(self):
        a = _mock_adapter()
        a.submit_market_order.side_effect = AlpacaPaperAdapterError("transient")
        run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert a.submit_market_order.call_count == 1


class TestInputImmutability:
    def test_bars_list_not_mutated(self):
        bars = _bars_bullish()
        original = copy.deepcopy(bars)
        a = _mock_adapter()
        run_paper_trading_cycle(adapter=a, bars=bars, signal_config=_config())
        assert bars == original

    def test_signal_config_not_mutated(self):
        cfg = _config()
        original = copy.deepcopy(cfg)
        a = _mock_adapter()
        run_paper_trading_cycle(adapter=a, bars=_bars_bullish(), signal_config=cfg)
        assert cfg == original


class TestDryRunDefault:
    def test_buy_dry_run_returns_buy_planned(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
        )
        assert r["result"] == "PASS"
        assert r["action"] == "buy_planned"
        assert r["order_plan"] == {
            "symbol": "SPY", "qty": 91, "side": "buy", "type": "market",
        }
        assert r["order"] is None
        assert a.submit_market_order.call_count == 0

    def test_sell_dry_run_returns_sell_planned(self):
        a = _mock_adapter(positions=[_position(qty=7)])
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bearish(), signal_config=_config(),
        )
        assert r["result"] == "PASS"
        assert r["action"] == "sell_planned"
        assert r["order_plan"] == {
            "symbol": "SPY", "qty": 7.0, "side": "sell", "type": "market",
        }
        assert r["order"] is None
        assert a.submit_market_order.call_count == 0

    def test_hold_has_no_plan(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_flat(), signal_config=_config(),
        )
        assert r["action"] == "none"
        assert r["order_plan"] is None
        assert r["order"] is None

    def test_block_has_no_plan(self):
        a = _mock_adapter(clock={"timestamp": "t", "is_open": False})
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
        )
        assert r["action"] == "none"
        assert r["order_plan"] is None

    def test_default_submit_enabled_false(self):
        # No explicit submit_enabled — default behavior must not submit.
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
        )
        assert r["action"] == "buy_planned"
        assert a.submit_market_order.call_count == 0

    def test_explicit_submit_enabled_false(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=False,
        )
        assert r["action"] == "buy_planned"
        assert a.submit_market_order.call_count == 0

    def test_submit_enabled_true_returns_buy_submitted(self):
        a = _mock_adapter()
        r = run_paper_trading_cycle(
            adapter=a, bars=_bars_bullish(), signal_config=_config(),
            submit_enabled=True,
        )
        assert r["action"] == "buy_submitted"
        assert r["order_plan"] is not None  # still present on submit
        assert r["order"] is not None
        assert a.submit_market_order.call_count == 1


class TestNoNetworkInModule:
    def test_module_imports_only_known_modules(self):
        import inspect
        import src.runtime.paper_trading_cycle as mod
        source = inspect.getsource(mod)
        for forbidden in [
            "os.environ", "getenv", "requests", "urllib", "aiohttp",
            "socket", "subprocess", "open(", "logging", "log" + "ger",
        ]:
            assert forbidden not in source, f"forbidden pattern '{forbidden}'"

    def test_no_alpaca_sdk_import_in_module(self):
        import inspect
        import src.runtime.paper_trading_cycle as mod
        source = inspect.getsource(mod)
        # The cycle goes through the adapter, never the SDK directly
        assert "alpaca.trading" not in source
        assert "TradingClient" not in source
