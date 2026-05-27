"""
strategy/factory.py
--------------------
Strategy factory — returns a constructed BaseStrategy by name.

Additive only. No existing strategy behaviour is changed.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer is touched.**
**No config files are read.**
**No data providers are called.**
"""

from __future__ import annotations

from typing import Any

from src.strategy.base import BaseStrategy
from src.strategy.opening_range_breakout import OpeningRangeBreakout

# Canonical name → canonical name; aliases map to the same canonical key.
_NAME_MAP: dict[str, str] = {
    "opening_range_breakout": "opening_range_breakout",
    "orb": "opening_range_breakout",
}

_SUPPORTED: tuple[str, ...] = tuple(sorted(_NAME_MAP.keys()))


def supported_strategy_names() -> tuple[str, ...]:
    """Return all recognised strategy name strings, including aliases."""
    return _SUPPORTED


def build_strategy(
    name: str,
    params: dict[str, Any] | None = None,
) -> BaseStrategy:
    """Construct and return a strategy instance by name.

    Parameters
    ----------
    name:
        Strategy identifier. Case-sensitive. See ``supported_strategy_names()``.
    params:
        Strategy parameters dict. ``None`` is treated as an empty dict.
        The caller's dict is never mutated.

    Returns
    -------
    BaseStrategy
        Instantiated strategy, ready for use with the backtest engine.

    Raises
    ------
    ValueError
        If ``name`` is not a recognised strategy name: ``"unknown strategy name"``.
        If ``params`` are structurally invalid for the strategy:
        ``"invalid strategy parameters"``.
        Raw ``name`` and ``params`` values are never echoed in the message.
    """
    if params is None:
        params = {}

    safe_params: dict[str, Any] = dict(params)

    canonical = _NAME_MAP.get(name)
    if canonical is None:
        raise ValueError("unknown strategy name")

    if canonical == "opening_range_breakout":
        try:
            return OpeningRangeBreakout(params=safe_params)
        except (ValueError, TypeError):
            raise ValueError("invalid strategy parameters")

    raise ValueError("unknown strategy name")  # unreachable; guards future additions
