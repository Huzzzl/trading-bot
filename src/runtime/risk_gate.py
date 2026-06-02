"""
runtime/risk_gate.py
---------------------
Automated runtime risk gate skeleton (PR A2-2).

Evaluates a set of deterministic, offline, local rules before allowing any
order action. No broker, credential, env, or network access in this module.

Default behavior: fail-closed.
  enabled=False (default) → evaluate() returns BLOCKED("automated risk gate not enabled").
  __call__() returns False by default.

When enabled=True, rules are evaluated against a caller-supplied context dict.
All safety flags (broker_calls_made, credentials_read, network_calls_made) are
always False — this gate is offline-only.

live_trading_allowed is always False in A2-2. Live automation requires a
separate approval gate that does not exist yet.

This module does not import from src.tools live manual gates, broker adapters,
or environment variables. No network I/O. No broker calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_NOT_ENABLED_BLOCKER = "automated risk gate not enabled"


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"
    BLOCKED  = "BLOCKED"


@dataclass(frozen=True)
class RiskCheckResult:
    """Result of a single named rule check."""
    name: str
    passed: bool
    blocker: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskGateResult:
    """Deterministic result of AutomatedRiskGate.evaluate()."""
    decision: RiskDecision
    approved: bool
    blocker: str | None
    checks: tuple[RiskCheckResult, ...]
    order_action_allowed: bool
    live_trading_allowed: bool  # always False in A2-2
    broker_calls_made: bool     # always False — offline gate
    credentials_read: bool      # always False — no env access
    network_calls_made: bool    # always False — no network I/O


# ---------------------------------------------------------------------------
# Supported rule keys
# ---------------------------------------------------------------------------

_RULE_REQUIRE_ENABLED   = "require_enabled"
_RULE_MAX_ORDER_QTY     = "max_order_quantity"
_RULE_ALLOWED_SYMBOLS   = "allowed_symbols"
_RULE_ALLOWED_SIDES     = "allowed_sides"


class AutomatedRiskGate:
    """Automated runtime risk gate skeleton.

    Evaluates deterministic local rules against a context dict.
    No broker, credential, network, or env access.
    Fail-closed: enabled=False (default) → always BLOCKED.

    Parameters
    ----------
    enabled : bool
        Must be True for any APPROVED decision. Default False (fail-closed).
    rules : Mapping[str, Any], optional
        Local rule configuration.  Supported keys:
          - "max_order_quantity": numeric upper bound on context["quantity"]
          - "allowed_symbols": iterable of allowed symbol strings
          - "allowed_sides": iterable of allowed side strings
        Unknown keys are ignored.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        rules: Mapping[str, Any] | None = None,
    ) -> None:
        self._enabled = enabled
        self._rules: dict[str, Any] = dict(rules) if rules is not None else {}

    def evaluate(self, context: Mapping[str, Any] | None = None) -> RiskGateResult:
        """Evaluate all configured rules against *context*.

        Returns a fully populated RiskGateResult.  The result is deterministic
        for the same (enabled, rules, context) triple.  Context is never mutated.
        """
        ctx: Mapping[str, Any] = context if context is not None else {}
        checks: list[RiskCheckResult] = []

        # Gate 1 — enabled check (always first)
        if not self._enabled:
            checks.append(RiskCheckResult(
                name=_RULE_REQUIRE_ENABLED,
                passed=False,
                blocker=_NOT_ENABLED_BLOCKER,
            ))
            return self._blocked_result(_NOT_ENABLED_BLOCKER, tuple(checks))

        checks.append(RiskCheckResult(name=_RULE_REQUIRE_ENABLED, passed=True))

        # Gate 2 — max_order_quantity
        if _RULE_MAX_ORDER_QTY in self._rules:
            max_qty = self._rules[_RULE_MAX_ORDER_QTY]
            qty = ctx.get("quantity")
            if qty is not None and qty > max_qty:
                blocker = f"quantity {qty} exceeds max_order_quantity {max_qty}"
                checks.append(RiskCheckResult(
                    name=_RULE_MAX_ORDER_QTY,
                    passed=False,
                    blocker=blocker,
                    details={"quantity": qty, "max_order_quantity": max_qty},
                ))
            else:
                checks.append(RiskCheckResult(
                    name=_RULE_MAX_ORDER_QTY,
                    passed=True,
                    details={"quantity": qty, "max_order_quantity": max_qty},
                ))

        # Gate 3 — allowed_symbols
        if _RULE_ALLOWED_SYMBOLS in self._rules:
            allowed = set(self._rules[_RULE_ALLOWED_SYMBOLS])
            symbol = ctx.get("symbol")
            if symbol is not None and symbol not in allowed:
                blocker = f"symbol {symbol!r} not in allowed_symbols {sorted(allowed)}"
                checks.append(RiskCheckResult(
                    name=_RULE_ALLOWED_SYMBOLS,
                    passed=False,
                    blocker=blocker,
                    details={"symbol": symbol, "allowed_symbols": sorted(allowed)},
                ))
            else:
                checks.append(RiskCheckResult(
                    name=_RULE_ALLOWED_SYMBOLS,
                    passed=True,
                    details={"symbol": symbol, "allowed_symbols": sorted(allowed)},
                ))

        # Gate 4 — allowed_sides
        if _RULE_ALLOWED_SIDES in self._rules:
            allowed_sides = set(self._rules[_RULE_ALLOWED_SIDES])
            side = ctx.get("side")
            if side is not None and side not in allowed_sides:
                blocker = f"side {side!r} not in allowed_sides {sorted(allowed_sides)}"
                checks.append(RiskCheckResult(
                    name=_RULE_ALLOWED_SIDES,
                    passed=False,
                    blocker=blocker,
                    details={"side": side, "allowed_sides": sorted(allowed_sides)},
                ))
            else:
                checks.append(RiskCheckResult(
                    name=_RULE_ALLOWED_SIDES,
                    passed=True,
                    details={"side": side, "allowed_sides": sorted(allowed_sides)},
                ))

        # Aggregate
        failures = [c for c in checks if not c.passed]
        if failures:
            first_blocker = failures[0].blocker
            return self._blocked_result(first_blocker, tuple(checks))

        logger.info("Risk gate approved: %d checks passed", len(checks))
        return RiskGateResult(
            decision=RiskDecision.APPROVED,
            approved=True,
            blocker=None,
            checks=tuple(checks),
            order_action_allowed=True,
            live_trading_allowed=False,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
        )

    def __call__(self) -> bool:
        """Return True if evaluate() approves, False otherwise.

        Callable interface for injection into AutomatedRuntimeStateMachine.
        """
        return self.evaluate().approved

    @staticmethod
    def _blocked_result(blocker: str | None, checks: tuple[RiskCheckResult, ...]) -> RiskGateResult:
        return RiskGateResult(
            decision=RiskDecision.BLOCKED,
            approved=False,
            blocker=blocker,
            checks=checks,
            order_action_allowed=False,
            live_trading_allowed=False,
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
        )
