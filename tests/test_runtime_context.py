"""
tests/test_runtime_context.py
------------------------------
Tests for src/runtime/context.py (PR A2-4).

All dataclasses are frozen and deterministic.
No file I/O. No broker/API/env access.
"""

from __future__ import annotations

import inspect

import pytest

from src.runtime.context import RuntimeContext, RuntimeOrderContext


# ---------------------------------------------------------------------------
# TestRuntimeOrderContextDataclass
# ---------------------------------------------------------------------------

class TestRuntimeOrderContextDataclass:
    def test_fields_default_to_none(self):
        ctx = RuntimeOrderContext()
        assert ctx.client_order_id is None
        assert ctx.symbol is None
        assert ctx.side is None
        assert ctx.quantity is None
        assert ctx.action is None

    def test_metadata_default_is_empty(self):
        ctx = RuntimeOrderContext()
        assert ctx.metadata == {}

    def test_fields_settable_at_construction(self):
        ctx = RuntimeOrderContext(
            client_order_id="coid-001",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            action="SUBMIT_BUY",
        )
        assert ctx.client_order_id == "coid-001"
        assert ctx.symbol == "SPY"
        assert ctx.side == "buy"
        assert ctx.quantity == 10.0
        assert ctx.action == "SUBMIT_BUY"

    def test_frozen_raises_on_mutation(self):
        ctx = RuntimeOrderContext(client_order_id="coid-001")
        with pytest.raises(Exception):
            ctx.client_order_id = "mutated"  # type: ignore[misc]

    def test_metadata_accepts_arbitrary_mapping(self):
        ctx = RuntimeOrderContext(metadata={"key": "value", "count": 3})
        assert ctx.metadata["key"] == "value"
        assert ctx.metadata["count"] == 3

    def test_equality_on_same_fields(self):
        a = RuntimeOrderContext(client_order_id="x", symbol="SPY")
        b = RuntimeOrderContext(client_order_id="x", symbol="SPY")
        assert a == b

    def test_inequality_on_different_fields(self):
        a = RuntimeOrderContext(client_order_id="x")
        b = RuntimeOrderContext(client_order_id="y")
        assert a != b

    def test_quantity_accepts_float_and_int(self):
        ctx_float = RuntimeOrderContext(quantity=1.5)
        ctx_int = RuntimeOrderContext(quantity=10)
        assert ctx_float.quantity == 1.5
        assert ctx_int.quantity == 10


# ---------------------------------------------------------------------------
# TestRuntimeContextDataclass
# ---------------------------------------------------------------------------

class TestRuntimeContextDataclass:
    def test_fields_default_values(self):
        ctx = RuntimeContext()
        assert ctx.mode == "paper"
        assert ctx.allow_order_action is False
        assert ctx.allow_live_trading is False

    def test_order_defaults_to_empty_order_context(self):
        ctx = RuntimeContext()
        assert isinstance(ctx.order, RuntimeOrderContext)
        assert ctx.order.client_order_id is None

    def test_metadata_default_is_empty(self):
        ctx = RuntimeContext()
        assert ctx.metadata == {}

    def test_frozen_raises_on_mutation(self):
        ctx = RuntimeContext()
        with pytest.raises(Exception):
            ctx.mode = "live"  # type: ignore[misc]

    def test_allow_live_trading_always_false_by_default(self):
        ctx = RuntimeContext()
        assert ctx.allow_live_trading is False

    def test_allow_live_trading_cannot_be_set_live_implicitly(self):
        ctx = RuntimeContext(allow_live_trading=False)
        assert ctx.allow_live_trading is False

    def test_allow_live_trading_true_raises_value_error(self):
        with pytest.raises(ValueError, match="allow_live_trading must remain False"):
            RuntimeContext(allow_live_trading=True)

    def test_allow_live_trading_false_explicit_does_not_raise(self):
        ctx = RuntimeContext(allow_live_trading=False)
        assert ctx.allow_live_trading is False

    def test_order_field_accepts_runtime_order_context(self):
        order = RuntimeOrderContext(client_order_id="coid-X", symbol="SPY")
        ctx = RuntimeContext(order=order)
        assert ctx.order.client_order_id == "coid-X"
        assert ctx.order.symbol == "SPY"

    def test_equality_on_same_fields(self):
        order = RuntimeOrderContext(client_order_id="abc")
        a = RuntimeContext(order=order, mode="paper")
        b = RuntimeContext(order=order, mode="paper")
        assert a == b

    def test_inequality_different_mode(self):
        a = RuntimeContext(mode="paper")
        b = RuntimeContext(mode="simulation")
        assert a != b

    def test_metadata_carries_arbitrary_data(self):
        ctx = RuntimeContext(metadata={"cycle": 1, "label": "test"})
        assert ctx.metadata["cycle"] == 1

    def test_allow_order_action_can_be_true(self):
        ctx = RuntimeContext(allow_order_action=True)
        assert ctx.allow_order_action is True


# ---------------------------------------------------------------------------
# TestContextSourceScan
# ---------------------------------------------------------------------------

class TestContextSourceScan:
    def _src(self) -> str:
        import src.runtime.context as _ctx
        return inspect.getsource(_ctx)

    def test_no_alpaca_import(self):
        assert "AlpacaBrokerAdapter" not in self._src()
        assert "AlpacaLiveSubmitBroker" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_network_import(self):
        src = self._src()
        assert "requests" not in src
        assert "httpx" not in src
        assert "aiohttp" not in src

    def test_no_live_submit_import(self):
        assert "live_submit" not in self._src()

    def test_no_broker_calls_made_flag(self):
        assert "broker_calls_made" not in self._src()

    def test_no_order_submission(self):
        src = self._src()
        assert "submit_order" not in src
        assert "place_order" not in src
