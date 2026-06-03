"""
runtime/fake_cycle.py
----------------------
Fully offline fake end-to-end runtime cycle (PR A2-5).

Wires RuntimeContext, AutomatedRuntimeStateMachine, AutomatedRiskGate,
OrderLifecycleManager, and local fake paper runners into a deterministic
paper buy/close cycle without broker, API, credential, env, or network access.

This module validates the automated runtime skeleton integration only.
It does NOT approve paper or live automation. No real orders are submitted.
All broker/credential/network safety flags remain False. order_action_requested
is True only for fake submit steps, faithfully reporting that a paper action
was requested (even though it is against a fake runner, not a real broker).

Cycle steps:
  A. PREVIEW_BUY   → RuntimeState.PAPER_PREVIEW
  B. SUBMIT_BUY    → RuntimeState.PAPER_SUBMITTED
  C. PREVIEW_CLOSE → RuntimeState.PAPER_CLOSE_PREVIEW
  D. SUBMIT_CLOSE  → RuntimeState.PAPER_CLOSED

Fail-closed: any BLOCKED or ERROR step halts the cycle immediately and
returns the partial result. No live trading. No live gates unfrozen.

This module does not import from src.tools live manual gates, broker adapters,
or environment variables. No file I/O. No network I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.config.loader import AppConfig
from src.runtime.context import RuntimeContext, RuntimeOrderContext
from src.runtime.order_lifecycle import OrderLifecycleManager, OrderLifecycleRecord
from src.runtime.risk_gate import AutomatedRiskGate
from src.runtime.state_machine import (
    AutomatedRuntimeStateMachine,
    RuntimeAction,
    RuntimeState,
    RuntimeStepResult,
)

logger = logging.getLogger(__name__)

_FAKE_RULES: dict[str, Any] = {
    "max_order_quantity": 1,
    "allowed_symbols": ["SPY"],
    "allowed_sides": ["buy", "sell"],
}


# ---------------------------------------------------------------------------
# Fake paper runner result (no real execution)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakePaperResult:
    result: str = "PREVIEW_COMPLETE"
    order_action_requested: bool = False
    credentials_read: bool = False
    network_calls_made: bool = False
    broker_calls_made: bool = False


def _default_buy_runner(cfg: AppConfig) -> _FakePaperResult:
    if getattr(cfg.execution, "paper_preview_only", True):
        return _FakePaperResult(result="PREVIEW_COMPLETE", order_action_requested=False)
    return _FakePaperResult(result="SUBMIT_COMPLETE", order_action_requested=True)


def _default_close_runner(cfg: AppConfig) -> _FakePaperResult:
    if getattr(cfg.execution, "paper_close_preview_only", True):
        return _FakePaperResult(result="PREVIEW_COMPLETE", order_action_requested=False)
    return _FakePaperResult(result="SUBMIT_COMPLETE", order_action_requested=True)


# ---------------------------------------------------------------------------
# Context adapter: RuntimeContext → dict for AutomatedRiskGate
# ---------------------------------------------------------------------------

def _context_to_risk_dict(context: Any) -> dict[str, Any]:
    """Extract risk-relevant order fields from a RuntimeContext into a plain dict."""
    if context is None:
        return {}
    order = getattr(context, "order", None)
    if order is None:
        return {}
    d: dict[str, Any] = {}
    for attr in ("quantity", "symbol", "side"):
        val = getattr(order, attr, None)
        if val is not None:
            d[attr] = val
    return d


class _AdaptedGate:
    """Wraps AutomatedRiskGate to accept RuntimeContext via the evaluate(context) protocol."""

    def __init__(self, gate: AutomatedRiskGate) -> None:
        self._gate = gate

    def evaluate(self, context: Any):
        return self._gate.evaluate(_context_to_risk_dict(context))


# ---------------------------------------------------------------------------
# Cycle result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FakeRuntimeCycleResult:
    """Deterministic result of a run_fake_paper_cycle() call.

    result is "PASS", "BLOCKED", or "ERROR".
    All broker/credential/network flags are False.
    order_action_requested is True if any submit step requested a fake paper action.
    live_trading_allowed is always False.
    """
    result: str
    blocker: str | None
    steps: tuple[RuntimeStepResult, ...]
    lifecycle_records: tuple[OrderLifecycleRecord, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Cycle runner
# ---------------------------------------------------------------------------

def run_fake_paper_cycle(
    config: AppConfig,
    *,
    symbol: str = "SPY",
    quantity: float = 1.0,
    buy_coid: str | None = "FAKE-SPY-BUY-1",
    close_coid: str | None = "FAKE-SPY-CLOSE-1",
    _paper_buy_runner=None,
    _paper_close_runner=None,
) -> FakeRuntimeCycleResult:
    """Run a fully offline fake paper buy/close cycle.

    Wires AutomatedRiskGate, OrderLifecycleManager, and local fake paper
    runners behind AutomatedRuntimeStateMachine. Config is deep-copied by
    the state machine before preview/submit flags are set; the original is
    never mutated. No broker, credential, env, or network access. No real
    order submitted. Halts early on any BLOCKED or ERROR step.

    Parameters
    ----------
    config : AppConfig
        Application config (never mutated).
    symbol : str
        Symbol for risk gate evaluation (default "SPY").
    quantity : float
        Order quantity (must be ≤ max_order_quantity rule, default 1.0).
    buy_coid : str | None
        client_order_id for the buy lifecycle record. None → BLOCKED (missing coid).
    close_coid : str | None
        client_order_id for the close lifecycle record. None → BLOCKED (missing coid).
    _paper_buy_runner : callable, optional
        Override buy runner for testing (e.g. inject a raising callable).
    _paper_close_runner : callable, optional
        Override close runner for testing (e.g. inject a raising callable).
    """
    gate = AutomatedRiskGate(enabled=True, rules=_FAKE_RULES)
    lm = OrderLifecycleManager()
    buy_runner = _paper_buy_runner if _paper_buy_runner is not None else _default_buy_runner
    close_runner = _paper_close_runner if _paper_close_runner is not None else _default_close_runner

    sm = AutomatedRuntimeStateMachine(
        risk_gate=_AdaptedGate(gate),
        paper_buy_runner=buy_runner,
        paper_close_runner=close_runner,
        lifecycle_manager=lm,
    )

    buy_ctx = RuntimeContext(
        order=RuntimeOrderContext(
            client_order_id=buy_coid,
            symbol=symbol,
            side="buy",
            quantity=quantity,
            action="submit_buy",
        )
    )
    close_ctx = RuntimeContext(
        order=RuntimeOrderContext(
            client_order_id=close_coid,
            symbol=symbol,
            side="sell",
            quantity=quantity,
            action="submit_close",
        )
    )

    steps: list[RuntimeStepResult] = []

    # A. PREVIEW_BUY
    r1 = sm.step(RuntimeAction.PREVIEW_BUY, config, buy_ctx)
    steps.append(r1)
    if r1.next_state == RuntimeState.ERROR:
        return _build_result("ERROR", steps, lm)

    # B. SUBMIT_BUY
    r2 = sm.step(RuntimeAction.SUBMIT_BUY, config, buy_ctx)
    steps.append(r2)
    if r2.next_state == RuntimeState.ERROR:
        return _build_result("ERROR", steps, lm)
    if r2.next_state == RuntimeState.BLOCKED:
        return _build_result("BLOCKED", steps, lm)

    # C. PREVIEW_CLOSE
    r3 = sm.step(RuntimeAction.PREVIEW_CLOSE, config, close_ctx)
    steps.append(r3)
    if r3.next_state == RuntimeState.ERROR:
        return _build_result("ERROR", steps, lm)

    # D. SUBMIT_CLOSE
    r4 = sm.step(RuntimeAction.SUBMIT_CLOSE, config, close_ctx)
    steps.append(r4)
    if r4.next_state == RuntimeState.ERROR:
        return _build_result("ERROR", steps, lm)
    if r4.next_state == RuntimeState.BLOCKED:
        return _build_result("BLOCKED", steps, lm)

    return _build_result("PASS", steps, lm)


def _build_result(
    result_str: str,
    steps: list[RuntimeStepResult],
    lm: OrderLifecycleManager,
) -> FakeRuntimeCycleResult:
    blocker = next((s.blocker for s in steps if s.blocker is not None), None)
    return FakeRuntimeCycleResult(
        result=result_str,
        blocker=blocker,
        steps=tuple(steps),
        lifecycle_records=tuple(lm.all_orders()),
        broker_calls_made=any(s.safety_flags.get("broker_calls_made", False) for s in steps),
        credentials_read=any(s.safety_flags.get("credentials_read", False) for s in steps),
        network_calls_made=any(s.safety_flags.get("network_calls_made", False) for s in steps),
        order_action_requested=any(s.safety_flags.get("order_action_requested", False) for s in steps),
        live_trading_allowed=False,
    )
