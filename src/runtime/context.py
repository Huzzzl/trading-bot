"""
runtime/context.py
-------------------
Runtime context dataclasses for A2-4: context design and wiring.

Passes order-level context from the state machine to the risk gate and
lifecycle manager without requiring any broker, credential, or network access.

All dataclasses are frozen and deterministic.
No file I/O. No broker/API/env access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeOrderContext:
    """Order-level context for a single execution step."""
    client_order_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    quantity: float | None = None
    action: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeContext:
    """Top-level runtime context passed into AutomatedRuntimeStateMachine.step().

    allow_live_trading must remain False; no live automation approval exists.
    """
    order: RuntimeOrderContext = field(default_factory=RuntimeOrderContext)
    mode: str = "paper"
    allow_order_action: bool = False
    allow_live_trading: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
