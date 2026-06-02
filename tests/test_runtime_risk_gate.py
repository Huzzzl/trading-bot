"""
tests/test_runtime_risk_gate.py
---------------------------------
Tests for src/runtime/risk_gate.py (PR A2-2).

All tests are offline — no broker calls, no credentials, no network access.
No live gates unfrozen. No live trading.
"""

from __future__ import annotations

import inspect

import pytest

from src.runtime.risk_gate import (
    AutomatedRiskGate,
    RiskCheckResult,
    RiskDecision,
    RiskGateResult,
    _NOT_ENABLED_BLOCKER,
)


# ---------------------------------------------------------------------------
# TestRiskDecisionEnum
# ---------------------------------------------------------------------------

class TestRiskDecisionEnum:
    def test_approved_and_blocked_exist(self):
        assert RiskDecision.APPROVED == "APPROVED"
        assert RiskDecision.BLOCKED == "BLOCKED"

    def test_str_enum(self):
        assert RiskDecision.APPROVED == "APPROVED"
        assert RiskDecision.BLOCKED == "BLOCKED"

    def test_only_two_values(self):
        assert len(list(RiskDecision)) == 2


# ---------------------------------------------------------------------------
# TestRiskCheckResultDataclass
# ---------------------------------------------------------------------------

class TestRiskCheckResultDataclass:
    def test_required_fields(self):
        r = RiskCheckResult(name="test_check", passed=True)
        assert r.name == "test_check"
        assert r.passed is True
        assert r.blocker is None
        assert r.details == {}

    def test_with_blocker_and_details(self):
        r = RiskCheckResult(
            name="max_order_quantity",
            passed=False,
            blocker="quantity too high",
            details={"quantity": 5, "max": 1},
        )
        assert r.blocker == "quantity too high"
        assert r.details["quantity"] == 5

    def test_frozen(self):
        r = RiskCheckResult(name="x", passed=True)
        with pytest.raises(Exception):
            r.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestRiskGateResultDataclass
# ---------------------------------------------------------------------------

class TestRiskGateResultDataclass:
    def _blocked(self) -> RiskGateResult:
        return RiskGateResult(
            decision=RiskDecision.BLOCKED,
            approved=False,
            blocker="test blocker",
            checks=(),
            order_action_allowed=False,
            live_trading_allowed=False,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
        )

    def test_all_fields_present(self):
        r = self._blocked()
        assert r.decision == RiskDecision.BLOCKED
        assert r.approved is False
        assert r.blocker == "test blocker"
        assert r.checks == ()
        assert r.order_action_allowed is False
        assert r.live_trading_allowed is False
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False

    def test_frozen(self):
        r = self._blocked()
        with pytest.raises(Exception):
            r.approved = True  # type: ignore[misc]

    def test_checks_is_tuple(self):
        check = RiskCheckResult(name="c", passed=True)
        r = RiskGateResult(
            decision=RiskDecision.APPROVED,
            approved=True,
            blocker=None,
            checks=(check,),
            order_action_allowed=True,
            live_trading_allowed=False,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
        )
        assert isinstance(r.checks, tuple)
        assert r.checks[0].name == "c"


# ---------------------------------------------------------------------------
# TestDefaultFailClosed
# ---------------------------------------------------------------------------

class TestDefaultFailClosed:
    def test_default_evaluate_is_blocked(self):
        gate = AutomatedRiskGate()
        result = gate.evaluate()
        assert result.decision == RiskDecision.BLOCKED
        assert result.approved is False

    def test_default_blocker_message(self):
        gate = AutomatedRiskGate()
        result = gate.evaluate()
        assert result.blocker == _NOT_ENABLED_BLOCKER

    def test_default_call_returns_false(self):
        gate = AutomatedRiskGate()
        assert gate() is False

    def test_default_order_action_not_allowed(self):
        gate = AutomatedRiskGate()
        result = gate.evaluate()
        assert result.order_action_allowed is False

    def test_disabled_with_context_still_blocked(self):
        gate = AutomatedRiskGate(enabled=False)
        result = gate.evaluate(context={"quantity": 1, "symbol": "SPY", "side": "buy"})
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocker == _NOT_ENABLED_BLOCKER


# ---------------------------------------------------------------------------
# TestSafetyFlags — always offline
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_broker_calls_made_always_false_when_blocked(self):
        result = AutomatedRiskGate().evaluate()
        assert result.broker_calls_made is False

    def test_credentials_read_always_false_when_blocked(self):
        result = AutomatedRiskGate().evaluate()
        assert result.credentials_read is False

    def test_network_calls_made_always_false_when_blocked(self):
        result = AutomatedRiskGate().evaluate()
        assert result.network_calls_made is False

    def test_broker_calls_made_always_false_when_approved(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.broker_calls_made is False

    def test_credentials_read_always_false_when_approved(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.credentials_read is False

    def test_network_calls_made_always_false_when_approved(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.network_calls_made is False


# ---------------------------------------------------------------------------
# TestLiveTradingAlwaysFalse
# ---------------------------------------------------------------------------

class TestLiveTradingAlwaysFalse:
    def test_live_trading_false_when_blocked(self):
        result = AutomatedRiskGate().evaluate()
        assert result.live_trading_allowed is False

    def test_live_trading_false_when_approved(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.live_trading_allowed is False

    def test_live_trading_false_even_with_allow_live_rule(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allow_live_trading": True})
        result = gate.evaluate()
        assert result.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestEnabledApproval
# ---------------------------------------------------------------------------

class TestEnabledApproval:
    def test_enabled_no_rules_approves(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.decision == RiskDecision.APPROVED
        assert result.approved is True

    def test_enabled_call_returns_true(self):
        gate = AutomatedRiskGate(enabled=True)
        assert gate() is True

    def test_enabled_order_action_allowed(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.order_action_allowed is True

    def test_enabled_no_blocker(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        assert result.blocker is None

    def test_enabled_checks_include_require_enabled(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate()
        names = [c.name for c in result.checks]
        assert "require_enabled" in names


# ---------------------------------------------------------------------------
# TestMaxOrderQuantityRule
# ---------------------------------------------------------------------------

class TestMaxOrderQuantityRule:
    def test_quantity_within_limit_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 5})
        result = gate.evaluate(context={"quantity": 3})
        assert result.approved is True

    def test_quantity_at_limit_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 1})
        result = gate.evaluate(context={"quantity": 1})
        assert result.approved is True

    def test_quantity_exceeds_limit_blocks(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 1})
        result = gate.evaluate(context={"quantity": 2})
        assert result.approved is False
        assert result.decision == RiskDecision.BLOCKED

    def test_quantity_exceeded_blocker_message(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 1})
        result = gate.evaluate(context={"quantity": 5})
        assert result.blocker is not None
        assert "quantity" in result.blocker

    def test_no_quantity_in_context_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 1})
        result = gate.evaluate(context={})
        assert result.approved is True

    def test_quantity_check_in_checks_list(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 1})
        result = gate.evaluate(context={"quantity": 2})
        names = [c.name for c in result.checks]
        assert "max_order_quantity" in names


# ---------------------------------------------------------------------------
# TestAllowedSymbolsRule
# ---------------------------------------------------------------------------

class TestAllowedSymbolsRule:
    def test_allowed_symbol_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_symbols": ["SPY"]})
        result = gate.evaluate(context={"symbol": "SPY"})
        assert result.approved is True

    def test_disallowed_symbol_blocks(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_symbols": ["SPY"]})
        result = gate.evaluate(context={"symbol": "AAPL"})
        assert result.approved is False
        assert result.decision == RiskDecision.BLOCKED

    def test_disallowed_symbol_blocker_message(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_symbols": ["SPY"]})
        result = gate.evaluate(context={"symbol": "AAPL"})
        assert result.blocker is not None
        assert "AAPL" in result.blocker

    def test_no_symbol_in_context_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_symbols": ["SPY"]})
        result = gate.evaluate(context={})
        assert result.approved is True

    def test_symbol_check_in_checks_list(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_symbols": ["SPY"]})
        result = gate.evaluate(context={"symbol": "AAPL"})
        names = [c.name for c in result.checks]
        assert "allowed_symbols" in names


# ---------------------------------------------------------------------------
# TestAllowedSidesRule
# ---------------------------------------------------------------------------

class TestAllowedSidesRule:
    def test_allowed_side_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_sides": ["buy"]})
        result = gate.evaluate(context={"side": "buy"})
        assert result.approved is True

    def test_disallowed_side_blocks(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_sides": ["buy"]})
        result = gate.evaluate(context={"side": "sell"})
        assert result.approved is False

    def test_disallowed_side_blocker_message(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_sides": ["buy"]})
        result = gate.evaluate(context={"side": "sell"})
        assert result.blocker is not None
        assert "sell" in result.blocker

    def test_no_side_in_context_passes(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_sides": ["buy"]})
        result = gate.evaluate(context={})
        assert result.approved is True


# ---------------------------------------------------------------------------
# TestMultipleViolations
# ---------------------------------------------------------------------------

class TestMultipleViolations:
    def test_two_violations_both_in_checks(self):
        gate = AutomatedRiskGate(enabled=True, rules={
            "max_order_quantity": 1,
            "allowed_symbols": ["SPY"],
        })
        result = gate.evaluate(context={"quantity": 5, "symbol": "AAPL"})
        assert result.approved is False
        names = [c.name for c in result.checks]
        assert "max_order_quantity" in names
        assert "allowed_symbols" in names

    def test_first_violation_sets_blocker(self):
        gate = AutomatedRiskGate(enabled=True, rules={
            "max_order_quantity": 1,
            "allowed_symbols": ["SPY"],
        })
        result = gate.evaluate(context={"quantity": 5, "symbol": "AAPL"})
        assert result.blocker is not None

    def test_all_rules_pass_gives_approved(self):
        gate = AutomatedRiskGate(enabled=True, rules={
            "max_order_quantity": 5,
            "allowed_symbols": ["SPY"],
            "allowed_sides": ["buy"],
        })
        result = gate.evaluate(context={"quantity": 1, "symbol": "SPY", "side": "buy"})
        assert result.approved is True
        assert all(c.passed for c in result.checks)


# ---------------------------------------------------------------------------
# TestContextHandling
# ---------------------------------------------------------------------------

class TestContextHandling:
    def test_none_context_handled_safely(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate(context=None)
        assert result.decision == RiskDecision.APPROVED

    def test_empty_context_handled_safely(self):
        gate = AutomatedRiskGate(enabled=True)
        result = gate.evaluate(context={})
        assert result.decision == RiskDecision.APPROVED

    def test_context_not_mutated(self):
        gate = AutomatedRiskGate(enabled=True, rules={"allowed_symbols": ["SPY"]})
        ctx = {"symbol": "SPY", "extra": "value"}
        original_keys = set(ctx.keys())
        gate.evaluate(context=ctx)
        assert set(ctx.keys()) == original_keys
        assert ctx["symbol"] == "SPY"
        assert ctx["extra"] == "value"

    def test_rules_not_mutated(self):
        rules = {"max_order_quantity": 1, "allowed_symbols": ["SPY"]}
        gate = AutomatedRiskGate(enabled=True, rules=rules)
        gate.evaluate(context={"quantity": 2, "symbol": "AAPL"})
        assert rules["max_order_quantity"] == 1
        assert rules["allowed_symbols"] == ["SPY"]


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_result(self):
        gate = AutomatedRiskGate(enabled=True, rules={"max_order_quantity": 1})
        ctx = {"quantity": 2}
        r1 = gate.evaluate(context=ctx)
        r2 = gate.evaluate(context=ctx)
        assert r1.decision == r2.decision
        assert r1.blocker == r2.blocker
        assert r1.approved == r2.approved

    def test_two_gates_same_config_same_result(self):
        rules = {"allowed_symbols": ["SPY"]}
        g1 = AutomatedRiskGate(enabled=True, rules=rules)
        g2 = AutomatedRiskGate(enabled=True, rules=rules)
        ctx = {"symbol": "SPY"}
        assert g1.evaluate(context=ctx).approved == g2.evaluate(context=ctx).approved


# ---------------------------------------------------------------------------
# TestCallableInterface
# ---------------------------------------------------------------------------

class TestCallableInterface:
    def test_call_returns_bool(self):
        gate = AutomatedRiskGate()
        assert isinstance(gate(), bool)

    def test_call_false_when_disabled(self):
        assert AutomatedRiskGate(enabled=False)() is False

    def test_call_true_when_enabled_no_rules(self):
        assert AutomatedRiskGate(enabled=True)() is True

    def test_injectable_into_state_machine(self):
        from src.runtime.state_machine import AutomatedRuntimeStateMachine, RuntimeAction
        from src.config.loader import load_config

        gate = AutomatedRiskGate(enabled=False)
        sm = AutomatedRuntimeStateMachine(risk_gate=gate, paper_buy_runner=lambda cfg: None)
        result = sm.step(RuntimeAction.SUBMIT_BUY, load_config(None))
        from src.runtime.state_machine import RuntimeState
        assert result.next_state == RuntimeState.BLOCKED

    def test_enabled_gate_injectable_and_approves(self):
        from src.runtime.state_machine import AutomatedRuntimeStateMachine, RuntimeAction, RuntimeState
        from src.config.loader import load_config
        from tests.test_runtime_state_machine import _FakeBuyResult

        gate = AutomatedRiskGate(enabled=True)
        sm = AutomatedRuntimeStateMachine(
            risk_gate=gate,
            paper_buy_runner=lambda cfg: _FakeBuyResult(),
        )
        result = sm.step(RuntimeAction.SUBMIT_BUY, load_config(None))
        assert result.next_state == RuntimeState.PAPER_SUBMITTED


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.runtime.risk_gate as _rg
        return inspect.getsource(_rg)

    def test_no_alpaca_broker_import(self):
        assert "AlpacaBrokerAdapter" not in self._src()

    def test_no_live_submit_import(self):
        assert "live_submit" not in self._src()

    def test_no_live_readiness_gate_import(self):
        assert "live_readiness_gate" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_requests_or_httpx(self):
        src = self._src()
        assert "requests" not in src
        assert "httpx" not in src
