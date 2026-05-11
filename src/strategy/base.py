"""
strategy/base.py
----------------
Abstract base class for all trading strategies.

A strategy receives a *slice* of bar data that ends at the current bar
(no future data) and returns a :class:`Signal` or ``None``.  This design
makes look-ahead bias structurally impossible — the engine never passes
future bars to the strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd


class SignalDirection(Enum):
    LONG  = "LONG"
    SHORT = "SHORT"   # reserved for future use
    EXIT  = "EXIT"


@dataclass
class Signal:
    """A trading signal emitted by a strategy.

    Attributes
    ----------
    direction:
        Trade direction.
    symbol:
        Ticker this signal applies to.
    entry_price:
        Suggested limit/market price.  The engine may apply slippage on top.
    stop_loss:
        Hard stop-loss price.  ``None`` means no stop.
    timestamp:
        Bar close time that triggered the signal.
    meta:
        Arbitrary strategy-specific metadata for logging / research.
    """

    direction: SignalDirection
    symbol: str
    entry_price: float
    stop_loss: float | None
    timestamp: pd.Timestamp
    meta: dict[str, Any] | None = None


class BaseStrategy(ABC):
    """Every strategy must implement :meth:`generate_signal`."""

    def __init__(self, params: dict[str, Any]) -> None:
        """
        Parameters
        ----------
        params:
            Strategy parameters loaded from ``config/settings.yaml``.
            Never hard-code values inside a subclass.
        """
        self.params = params

    def reset(self) -> None:
        """Clear any internal state accumulated from a previous backtest run.

        Override in stateful subclasses.  Called by the engine before each
        run so that re-using the same strategy instance produces identical
        results to using a fresh one.
        """

    @abstractmethod
    def generate_signal(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_bar: pd.Timestamp,
    ) -> Signal | None:
        """Evaluate the current bar and optionally emit a signal.

        Parameters
        ----------
        symbol:
            Ticker being evaluated.
        bars:
            All bars **up to and including** *current_bar*.  The strategy
            must not peek at any index beyond *current_bar*.
        current_bar:
            Timestamp of the bar being processed — i.e. the *last* row
            in *bars*.

        Returns
        -------
        Signal or None
            ``None`` means "do nothing this bar".
        """
        ...
