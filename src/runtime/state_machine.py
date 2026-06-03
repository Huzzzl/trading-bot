"""
runtime/state_machine.py
-------------------------
Automated runtime state machine (A2-1, updated in A2-4).

Coordinates paper execution runners behind an injected risk gate with optional
lifecycle manager wiring. No broker, credential, or Alpaca access in this module.
All broker interaction remains inside the injected paper runners.

Default behavior: fail-closed.
  No risk_gate injected → every submit action returns BLOCKED.
  No order is submitted unless:
    (a) action is SUBMIT_BUY or SUBMIT_CLOSE,
    (b) an injected risk gate explicitly approves, and
    (c) the injected paper runner is called and executes normally.

Risk gate calling conventions (A2-4):
  1. If risk_gate has evaluate(context), call it; use .approved.
  2. Elif risk_gate requires ≥1 argument, call risk_gate(context).
  3. Else call risk_gate() — backward-compatible zero-arg form.

Lifecycle manager wiring (A2-4, optional):
  When lifecycle_manager is present and action is SUBMIT_BUY/SUBMIT_CLOSE:
  - Requires context.order.client_order_id; blocks if absent.
  - Creates lifecycle record (or reuses existing), applies RISK_APPROVE and
    REQUEST_SUBMIT before calling the runner. Invalid transitions block.
  - After runner returns with order_action_requested=True, applies MARK_SUBMITTED.
  - On runner exception, applies MARK_FAILED if record exists.
  Preview actions never require lifecycle_manager.

Config is deep-copied before preview/submit flags are set.
The original config passed to step() is never mutated.
Context passed to step() is never mutated.

This module does not import from src.tools live manual gates.
Live trading is not enabled. Live gates are not modified.
"""

from __future__ import annotations

import copy
import inspect as _inspect
import logging
from dataclasses import dataclass
from enum import Enum

from src.config.loader import AppConfig

logger = logging.getLogger(__name__)

_NO_RISK_GATE_BLOCKER = "automated risk gate not implemented"
_NO_COID_BLOCKER = "runtime order context missing client_order_id"


class RuntimeState(str, Enum):
    IDLE                = "IDLE"
    CHECKING_RISK       = "CHECKING_RISK"
    PAPER_PREVIEW       = "PAPER_PREVIEW"
    PAPER_SUBMIT_READY  = "PAPER_SUBMIT_READY"
    PAPER_SUBMITTED     = "PAPER_SUBMITTED"
    PAPER_CLOSE_PREVIEW = "PAPER_CLOSE_PREVIEW"
    PAPER_CLOSED        = "PAPER_CLOSED"
    BLOCKED             = "BLOCKED"
    ERROR               = "ERROR"


class RuntimeAction(str, Enum):
    NONE          = "NONE"
    PREVIEW_BUY   = "PREVIEW_BUY"
    SUBMIT_BUY    = "SUBMIT_BUY"
    PREVIEW_CLOSE = "PREVIEW_CLOSE"
    SUBMIT_CLOSE  = "SUBMIT_CLOSE"


@dataclass(frozen=True)
class RuntimeDecision:
    """Snapshot of a state machine decision (for logging / audit)."""
    state: RuntimeState
    action: RuntimeAction
    blocker: str | None
    reason: str


@dataclass(frozen=True)
class RuntimeStepResult:
    """Result of a single AutomatedRuntimeStateMachine.step() call."""
    previous_state: RuntimeState
    next_state: RuntimeState
    action: RuntimeAction
    blocker: str | None
    paper_result: object | None
    safety_flags: dict


class AutomatedRuntimeStateMachine:
    """Automated runtime state machine.

    Coordinates injected paper runners behind an injected risk gate.
    Fail-closed: no risk_gate injected → every submit action is BLOCKED.
    No direct broker, credential, or Alpaca access.

    Parameters
    ----------
    risk_gate : callable or object with evaluate(), optional
        Zero-arg callable, one-arg callable(context), or object with
        evaluate(context) method. If None, all submit actions are BLOCKED.
    paper_buy_runner : callable, optional
        Callable(config: AppConfig) → PaperRunResult-like.
    paper_close_runner : callable, optional
        Callable(config: AppConfig) → PaperCloseRunResult-like.
    lifecycle_manager : OrderLifecycleManager, optional
        If provided, submit actions wire lifecycle transitions.
        Requires context.order.client_order_id; blocks if absent.
    state_store : object, optional
        Optional external state persistence; not used in A2-x skeleton.
    """

    def __init__(
        self,
        *,
        risk_gate=None,
        paper_buy_runner=None,
        paper_close_runner=None,
        lifecycle_manager=None,
        state_store=None,
    ):
        self._risk_gate = risk_gate
        self._paper_buy_runner = paper_buy_runner
        self._paper_close_runner = paper_close_runner
        self._lifecycle_manager = lifecycle_manager
        self._state_store = state_store
        self._state: RuntimeState = RuntimeState.IDLE

    @property
    def state(self) -> RuntimeState:
        return self._state

    def step(
        self,
        action: RuntimeAction,
        config: AppConfig,
        context=None,
    ) -> RuntimeStepResult:
        """Execute one state machine step.

        Config is deep-copied before modification; never mutated.
        Context is passed to the risk gate but never mutated.
        Any unhandled exception from a runner transitions state to ERROR.
        """
        prev = self._state
        try:
            result = self._dispatch(action, config, context)
        except Exception as exc:
            self._state = RuntimeState.ERROR
            # Apply MARK_FAILED to lifecycle record if one exists
            if self._lifecycle_manager is not None:
                try:
                    self._lc_on_error(context)
                except Exception:
                    pass
            logger.error("State machine transition to ERROR: action=%s exc=%s", action, exc)
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.ERROR,
                action=action,
                blocker=str(exc),
                paper_result=None,
                safety_flags={},
            )
        self._state = result.next_state
        logger.info(
            "State machine step: %s → %s  action=%s  blocker=%s",
            prev, result.next_state, action, result.blocker,
        )
        return result

    def _dispatch(self, action: RuntimeAction, config: AppConfig, context) -> RuntimeStepResult:
        prev = self._state

        if action == RuntimeAction.NONE:
            return RuntimeStepResult(
                previous_state=prev,
                next_state=prev,
                action=action,
                blocker=None,
                paper_result=None,
                safety_flags={},
            )

        if action == RuntimeAction.PREVIEW_BUY:
            cfg = copy.deepcopy(config)
            cfg.execution.paper_preview_only = True
            paper_result = self._paper_buy_runner(cfg)
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.PAPER_PREVIEW,
                action=action,
                blocker=None,
                paper_result=paper_result,
                safety_flags=_extract_safety_flags(paper_result),
            )

        if action == RuntimeAction.SUBMIT_BUY:
            blocked, blocker_msg, gate_flags = self._evaluate_risk_gate(context)
            if blocked:
                return RuntimeStepResult(
                    previous_state=prev,
                    next_state=RuntimeState.BLOCKED,
                    action=action,
                    blocker=blocker_msg,
                    paper_result=None,
                    safety_flags=gate_flags,
                )
            if self._lifecycle_manager is not None:
                lc_blocked, lc_blocker = self._lc_pre_submit(context)
                if lc_blocked:
                    return RuntimeStepResult(
                        previous_state=prev,
                        next_state=RuntimeState.BLOCKED,
                        action=action,
                        blocker=lc_blocker,
                        paper_result=None,
                        safety_flags=gate_flags,
                    )
            cfg = copy.deepcopy(config)
            cfg.execution.paper_preview_only = False
            paper_result = self._paper_buy_runner(cfg)
            if self._lifecycle_manager is not None:
                self._lc_post_submit(context, paper_result)
            flags = {**gate_flags, **_extract_safety_flags(paper_result)}
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.PAPER_SUBMITTED,
                action=action,
                blocker=None,
                paper_result=paper_result,
                safety_flags=flags,
            )

        if action == RuntimeAction.PREVIEW_CLOSE:
            cfg = copy.deepcopy(config)
            cfg.execution.paper_close_preview_only = True
            paper_result = self._paper_close_runner(cfg)
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.PAPER_CLOSE_PREVIEW,
                action=action,
                blocker=None,
                paper_result=paper_result,
                safety_flags=_extract_safety_flags(paper_result),
            )

        if action == RuntimeAction.SUBMIT_CLOSE:
            blocked, blocker_msg, gate_flags = self._evaluate_risk_gate(context)
            if blocked:
                return RuntimeStepResult(
                    previous_state=prev,
                    next_state=RuntimeState.BLOCKED,
                    action=action,
                    blocker=blocker_msg,
                    paper_result=None,
                    safety_flags=gate_flags,
                )
            if self._lifecycle_manager is not None:
                lc_blocked, lc_blocker = self._lc_pre_submit(context)
                if lc_blocked:
                    return RuntimeStepResult(
                        previous_state=prev,
                        next_state=RuntimeState.BLOCKED,
                        action=action,
                        blocker=lc_blocker,
                        paper_result=None,
                        safety_flags=gate_flags,
                    )
            cfg = copy.deepcopy(config)
            cfg.execution.paper_close_preview_only = False
            paper_result = self._paper_close_runner(cfg)
            if self._lifecycle_manager is not None:
                self._lc_post_submit(context, paper_result)
            flags = {**gate_flags, **_extract_safety_flags(paper_result)}
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.PAPER_CLOSED,
                action=action,
                blocker=None,
                paper_result=paper_result,
                safety_flags=flags,
            )

        raise ValueError(f"Unknown action: {action!r}")

    # ------------------------------------------------------------------
    # Risk gate evaluation — three calling conventions
    # ------------------------------------------------------------------

    def _evaluate_risk_gate(self, context) -> tuple[bool, str | None, dict]:
        """Returns (blocked, blocker_message, gate_safety_flags).

        Calling conventions:
          1. risk_gate has evaluate(context) → use .approved and extract safety flags.
          2. risk_gate requires ≥1 positional arg → call risk_gate(context).
          3. risk_gate requires 0 args → call risk_gate().  (backward compat)
        """
        if self._risk_gate is None:
            return True, _NO_RISK_GATE_BLOCKER, {}

        gate_result = None

        if hasattr(self._risk_gate, "evaluate"):
            gate_result = self._risk_gate.evaluate(context)
            approved = getattr(gate_result, "approved", bool(gate_result))
        else:
            try:
                sig = _inspect.signature(self._risk_gate)
                n_req = sum(
                    1 for p in sig.parameters.values()
                    if p.default is _inspect.Parameter.empty
                    and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                )
            except (ValueError, TypeError):
                n_req = 0
            gate_result = self._risk_gate(context) if n_req >= 1 else self._risk_gate()
            approved = getattr(gate_result, "approved", bool(gate_result))

        gate_flags = _extract_safety_flags(gate_result)
        if not approved:
            return True, "risk gate rejected", gate_flags
        return False, None, gate_flags

    # ------------------------------------------------------------------
    # Lifecycle manager helpers (lazy import to preserve test-patch compat)
    # ------------------------------------------------------------------

    def _lc_coid(self, context) -> str | None:
        if context is None:
            return None
        order = getattr(context, "order", None)
        return getattr(order, "client_order_id", None) if order is not None else None

    def _lc_pre_submit(self, context) -> tuple[bool, str | None]:
        """Create record and apply RISK_APPROVE + REQUEST_SUBMIT. Returns (blocked, blocker)."""
        from src.runtime.order_lifecycle import OrderLifecycleEvent as _OLE

        coid = self._lc_coid(context)
        if not coid:
            return True, _NO_COID_BLOCKER

        order = getattr(context, "order", None)
        if self._lifecycle_manager.get(coid) is None:
            self._lifecycle_manager.create_order(
                coid,
                symbol=getattr(order, "symbol", None),
                side=getattr(order, "side", None),
                quantity=getattr(order, "quantity", None),
            )

        t1 = self._lifecycle_manager.apply_event(coid, _OLE.RISK_APPROVE)
        if not t1.allowed:
            return True, f"lifecycle RISK_APPROVE blocked: {t1.blocker}"

        t2 = self._lifecycle_manager.apply_event(coid, _OLE.REQUEST_SUBMIT)
        if not t2.allowed:
            return True, f"lifecycle REQUEST_SUBMIT blocked: {t2.blocker}"

        return False, None

    def _lc_post_submit(self, context, paper_result) -> None:
        """Apply MARK_SUBMITTED if order_action_requested is True."""
        from src.runtime.order_lifecycle import OrderLifecycleEvent as _OLE

        coid = self._lc_coid(context)
        if not coid:
            return
        if getattr(paper_result, "order_action_requested", False):
            self._lifecycle_manager.apply_event(coid, _OLE.MARK_SUBMITTED)

    def _lc_on_error(self, context) -> None:
        """Apply MARK_FAILED on runner exception if lifecycle record exists."""
        from src.runtime.order_lifecycle import OrderLifecycleEvent as _OLE

        coid = self._lc_coid(context)
        if not coid:
            return
        if self._lifecycle_manager.get(coid) is not None:
            self._lifecycle_manager.apply_event(coid, _OLE.MARK_FAILED)


def _extract_safety_flags(paper_result) -> dict:
    """Extract standard safety flags from a paper runner result object."""
    if paper_result is None:
        return {}
    flags = {}
    for attr in ("order_action_requested", "credentials_read", "network_calls_made", "broker_calls_made"):
        if hasattr(paper_result, attr):
            flags[attr] = getattr(paper_result, attr)
    return flags
