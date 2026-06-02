"""
runtime/state_machine.py
-------------------------
Automated runtime state machine skeleton (PR A2-1).

Coordinates paper execution runners as injectable dependencies.
No broker, credential, or Alpaca access in this module.
All broker interaction remains inside the injected paper runners.

Default behavior: fail-closed.
  No risk_gate injected → every submit action returns BLOCKED.
  No order is submitted unless:
    (a) action is SUBMIT_BUY or SUBMIT_CLOSE,
    (b) an injected risk gate explicitly approves, and
    (c) the injected paper runner is called and executes normally.

Config is deep-copied before preview/submit flags are set.
The original config passed to step() is never mutated.

This module does not import from src.tools live manual gates.
Live trading is not enabled. Live gates are not modified.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from enum import Enum

from src.config.loader import AppConfig

logger = logging.getLogger(__name__)

_AUTOMATED_RISK_GATE_IMPLEMENTED = False
_NO_RISK_GATE_BLOCKER = "automated risk gate not implemented"


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
    """Automated runtime state machine skeleton.

    Coordinates injected paper runners behind an injected risk gate.
    Fail-closed: no risk_gate injected → every submit action is BLOCKED.
    No direct broker, credential, or Alpaca access.

    Parameters
    ----------
    risk_gate : callable, optional
        Callable that takes no arguments and returns truthy to approve,
        falsy to reject. If None, all submit actions are BLOCKED with
        blocker="automated risk gate not implemented".
    paper_buy_runner : callable, optional
        Callable(config: AppConfig) → PaperRunResult-like. Required for
        PREVIEW_BUY and SUBMIT_BUY actions.
    paper_close_runner : callable, optional
        Callable(config: AppConfig) → PaperCloseRunResult-like. Required for
        PREVIEW_CLOSE and SUBMIT_CLOSE actions.
    state_store : object, optional
        Optional external state persistence; not used in A2-1 skeleton.
    """

    def __init__(
        self,
        *,
        risk_gate=None,
        paper_buy_runner=None,
        paper_close_runner=None,
        state_store=None,
    ):
        self._risk_gate = risk_gate
        self._paper_buy_runner = paper_buy_runner
        self._paper_close_runner = paper_close_runner
        self._state_store = state_store
        self._state: RuntimeState = RuntimeState.IDLE

    @property
    def state(self) -> RuntimeState:
        return self._state

    def step(self, action: RuntimeAction, config: AppConfig) -> RuntimeStepResult:
        """Execute one state machine step.

        Config is deep-copied before modification; the caller's config is never mutated.
        Any unhandled exception from a runner transitions state to ERROR.
        """
        prev = self._state
        try:
            result = self._dispatch(action, config)
        except Exception as exc:
            self._state = RuntimeState.ERROR
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

    def _dispatch(self, action: RuntimeAction, config: AppConfig) -> RuntimeStepResult:
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
            blocked, blocker_msg = self._check_risk_gate()
            if blocked:
                return RuntimeStepResult(
                    previous_state=prev,
                    next_state=RuntimeState.BLOCKED,
                    action=action,
                    blocker=blocker_msg,
                    paper_result=None,
                    safety_flags={},
                )
            cfg = copy.deepcopy(config)
            cfg.execution.paper_preview_only = False
            paper_result = self._paper_buy_runner(cfg)
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.PAPER_SUBMITTED,
                action=action,
                blocker=None,
                paper_result=paper_result,
                safety_flags=_extract_safety_flags(paper_result),
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
            blocked, blocker_msg = self._check_risk_gate()
            if blocked:
                return RuntimeStepResult(
                    previous_state=prev,
                    next_state=RuntimeState.BLOCKED,
                    action=action,
                    blocker=blocker_msg,
                    paper_result=None,
                    safety_flags={},
                )
            cfg = copy.deepcopy(config)
            cfg.execution.paper_close_preview_only = False
            paper_result = self._paper_close_runner(cfg)
            return RuntimeStepResult(
                previous_state=prev,
                next_state=RuntimeState.PAPER_CLOSED,
                action=action,
                blocker=None,
                paper_result=paper_result,
                safety_flags=_extract_safety_flags(paper_result),
            )

        raise ValueError(f"Unknown action: {action!r}")

    def _check_risk_gate(self) -> tuple[bool, str | None]:
        """Returns (blocked, blocker_message). Fail-closed if no risk gate."""
        if self._risk_gate is None:
            return True, _NO_RISK_GATE_BLOCKER
        approved = self._risk_gate()
        if not approved:
            return True, "risk gate rejected"
        return False, None


def _extract_safety_flags(paper_result) -> dict:
    """Extract standard safety flags from a paper runner result object."""
    if paper_result is None:
        return {}
    flags = {}
    for attr in ("order_action_requested", "credentials_read", "network_calls_made", "broker_calls_made"):
        if hasattr(paper_result, attr):
            flags[attr] = getattr(paper_result, attr)
    return flags
