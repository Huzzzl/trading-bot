"""
tests/test_runtime_fake_cycle.py
----------------------------------
Tests for src/runtime/fake_cycle.py (PR A2-5).

Validates the fully offline fake end-to-end runtime cycle that wires:
  RuntimeContext + AutomatedRuntimeStateMachine + AutomatedRiskGate +
  OrderLifecycleManager + fake paper buy/close runners.

No real Alpaca calls, no broker/API/credential/env access.
No live gates unfrozen. No live trading. No real order submitted.
"""

from __future__ import annotations

import inspect

import pytest

from src.config.loader import load_config
from src.runtime.context import RuntimeContext
from src.runtime.fake_cycle import (
    FakeRuntimeCycleResult,
    _context_to_risk_dict,
    run_fake_paper_cycle,
)
from src.runtime.order_lifecycle import OrderLifecycleState
from src.runtime.state_machine import RuntimeAction, RuntimeState


@pytest.fixture
def base_cfg():
    return load_config(None)


# ---------------------------------------------------------------------------
# TestFakeRuntimeCycleResultDataclass
# ---------------------------------------------------------------------------

class TestFakeRuntimeCycleResultDataclass:
    def test_frozen(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_fields_exist(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert hasattr(r, "result")
        assert hasattr(r, "blocker")
        assert hasattr(r, "steps")
        assert hasattr(r, "lifecycle_records")
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")

    def test_result_is_string(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert isinstance(r.result, str)


# ---------------------------------------------------------------------------
# TestHappyPath
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_result_is_pass(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.result == "PASS"

    def test_no_blocker(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.blocker is None

    def test_exactly_4_steps(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert len(r.steps) == 4

    def test_step_actions(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        actions = [s.action for s in r.steps]
        assert actions == [
            RuntimeAction.PREVIEW_BUY,
            RuntimeAction.SUBMIT_BUY,
            RuntimeAction.PREVIEW_CLOSE,
            RuntimeAction.SUBMIT_CLOSE,
        ]

    def test_step_next_states(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        states = [s.next_state for s in r.steps]
        assert states == [
            RuntimeState.PAPER_PREVIEW,
            RuntimeState.PAPER_SUBMITTED,
            RuntimeState.PAPER_CLOSE_PREVIEW,
            RuntimeState.PAPER_CLOSED,
        ]

    def test_order_action_requested_true(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.order_action_requested is True

    def test_broker_calls_made_false(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.broker_calls_made is False

    def test_credentials_read_false(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.credentials_read is False

    def test_network_calls_made_false(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.network_calls_made is False

    def test_live_trading_allowed_false(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.live_trading_allowed is False

    def test_lifecycle_has_two_records(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert len(r.lifecycle_records) == 2

    def test_buy_lifecycle_record_reaches_submitted(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        buy_record = next(
            rec for rec in r.lifecycle_records
            if rec.client_order_id == "FAKE-SPY-BUY-1"
        )
        assert buy_record.state == OrderLifecycleState.SUBMITTED

    def test_close_lifecycle_record_reaches_submitted(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        close_record = next(
            rec for rec in r.lifecycle_records
            if rec.client_order_id == "FAKE-SPY-CLOSE-1"
        )
        assert close_record.state == OrderLifecycleState.SUBMITTED

    def test_submit_buy_order_action_in_step_flags(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        submit_buy_step = r.steps[1]
        assert submit_buy_step.safety_flags.get("order_action_requested") is True

    def test_submit_close_order_action_in_step_flags(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        submit_close_step = r.steps[3]
        assert submit_close_step.safety_flags.get("order_action_requested") is True

    def test_preview_steps_no_order_action(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        preview_buy = r.steps[0]
        preview_close = r.steps[2]
        assert preview_buy.safety_flags.get("order_action_requested") is False
        assert preview_close.safety_flags.get("order_action_requested") is False


# ---------------------------------------------------------------------------
# TestBlockedByRiskGate
# ---------------------------------------------------------------------------

class TestBlockedByRiskGate:
    def test_excessive_quantity_blocks(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg, quantity=999.0)
        assert r.result == "BLOCKED"
        assert r.blocker is not None

    def test_excessive_quantity_submit_runner_not_called(self, base_cfg):
        # PREVIEW_BUY runs without risk gate; SUBMIT_BUY should be blocked before runner.
        submit_called: list = []

        def spy_runner(cfg):
            if not getattr(cfg.execution, "paper_preview_only", True):
                submit_called.append(cfg)
            from src.runtime.fake_cycle import _FakePaperResult
            return _FakePaperResult()

        r = run_fake_paper_cycle(base_cfg, quantity=999.0, _paper_buy_runner=spy_runner)
        assert r.result == "BLOCKED"
        assert submit_called == []

    def test_disallowed_symbol_blocks(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg, symbol="AAPL")
        assert r.result == "BLOCKED"
        assert r.blocker is not None

    def test_disallowed_symbol_stops_after_submit_buy(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg, symbol="AAPL")
        assert len(r.steps) == 2
        assert r.steps[1].next_state == RuntimeState.BLOCKED

    def test_missing_buy_coid_blocks(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg, buy_coid=None)
        assert r.result == "BLOCKED"
        assert r.blocker is not None

    def test_missing_close_coid_blocks(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg, close_coid=None)
        assert r.result == "BLOCKED"
        assert r.blocker is not None


# ---------------------------------------------------------------------------
# TestErrorPath
# ---------------------------------------------------------------------------

class TestErrorPath:
    def test_buy_runner_exception_returns_error(self, base_cfg):
        def exploding_runner(cfg):
            raise RuntimeError("fake buy runner exploded")

        r = run_fake_paper_cycle(base_cfg, _paper_buy_runner=exploding_runner)
        assert r.result == "ERROR"
        assert r.blocker is not None
        assert "exploded" in r.blocker

    def test_buy_runner_exception_lifecycle_mark_failed(self, base_cfg):
        def exploding_runner(cfg):
            if not getattr(cfg.execution, "paper_preview_only", True):
                raise RuntimeError("submit exploded")
            from src.runtime.fake_cycle import _FakePaperResult
            return _FakePaperResult()

        r = run_fake_paper_cycle(base_cfg, _paper_buy_runner=exploding_runner)
        assert r.result == "ERROR"
        buy_record = next(
            (rec for rec in r.lifecycle_records if rec.client_order_id == "FAKE-SPY-BUY-1"),
            None,
        )
        assert buy_record is not None
        assert buy_record.state == OrderLifecycleState.FAILED

    def test_close_runner_exception_returns_error(self, base_cfg):
        def exploding_close_runner(cfg):
            raise RuntimeError("fake close runner exploded")

        r = run_fake_paper_cycle(base_cfg, _paper_close_runner=exploding_close_runner)
        assert r.result == "ERROR"

    def test_error_step_has_blocker(self, base_cfg):
        def exploding_runner(cfg):
            raise ValueError("bad value from runner")

        r = run_fake_paper_cycle(base_cfg, _paper_buy_runner=exploding_runner)
        assert r.blocker is not None


# ---------------------------------------------------------------------------
# TestContextGuard
# ---------------------------------------------------------------------------

class TestContextGuard:
    def test_allow_live_trading_true_raises(self):
        with pytest.raises(ValueError, match="allow_live_trading must remain False"):
            RuntimeContext(allow_live_trading=True)

    def test_cycle_does_not_set_live_trading(self, base_cfg):
        r = run_fake_paper_cycle(base_cfg)
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestContextToRiskDict
# ---------------------------------------------------------------------------

class TestContextToRiskDict:
    def test_extracts_quantity_symbol_side(self):
        from src.runtime.context import RuntimeOrderContext
        order = RuntimeOrderContext(quantity=1.0, symbol="SPY", side="buy")
        ctx = RuntimeContext(order=order)
        d = _context_to_risk_dict(ctx)
        assert d["quantity"] == 1.0
        assert d["symbol"] == "SPY"
        assert d["side"] == "buy"

    def test_none_context_returns_empty(self):
        assert _context_to_risk_dict(None) == {}

    def test_missing_fields_omitted(self):
        from src.runtime.context import RuntimeOrderContext
        order = RuntimeOrderContext(symbol="SPY")
        ctx = RuntimeContext(order=order)
        d = _context_to_risk_dict(ctx)
        assert "symbol" in d
        assert "quantity" not in d
        assert "side" not in d


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.runtime.fake_cycle as _fc
        return inspect.getsource(_fc)

    def test_no_alpaca_broker_adapter(self):
        assert "AlpacaBrokerAdapter" not in self._src()

    def test_no_alpaca_live_submit_broker(self):
        assert "AlpacaLiveSubmitBroker" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_live_submit_import(self):
        assert "live_submit" not in self._src()

    def test_no_requests_import(self):
        assert "requests" not in self._src()

    def test_no_broker_calls_flag_set_true(self):
        # broker_calls_made must always be False in this module
        src = self._src()
        assert "broker_calls_made=True" not in src

    def test_no_credentials_read_flag_set_true(self):
        assert "credentials_read=True" not in self._src()

    def test_no_network_calls_flag_set_true(self):
        assert "network_calls_made=True" not in self._src()


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_runs_same_result(self, base_cfg):
        r1 = run_fake_paper_cycle(base_cfg)
        r2 = run_fake_paper_cycle(base_cfg)
        assert r1.result == r2.result
        assert r1.blocker == r2.blocker
        assert len(r1.steps) == len(r2.steps)
        for s1, s2 in zip(r1.steps, r2.steps):
            assert s1.action == s2.action
            assert s1.next_state == s2.next_state

    def test_repeated_blocked_runs_same_result(self, base_cfg):
        r1 = run_fake_paper_cycle(base_cfg, quantity=999.0)
        r2 = run_fake_paper_cycle(base_cfg, quantity=999.0)
        assert r1.result == r2.result == "BLOCKED"
