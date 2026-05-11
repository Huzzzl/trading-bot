"""
portfolio/portfolio.py
----------------------
Tracks cash, open positions, and completed trades.

The portfolio is deliberately kept simple: single-currency (USD),
no margin, no short selling in the MVP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.backtest.trade import Trade
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OpenPosition:
    """An open (not yet closed) position.

    Attributes
    ----------
    symbol : str
    entry_time : pd.Timestamp
    entry_price : float
        Fill price after slippage (before commission).
    shares : float
    stop_loss : float | None
    direction : str
    meta : dict
    """

    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    shares: float
    stop_loss: float | None
    direction: str
    meta: dict[str, Any] = field(default_factory=dict)


class Portfolio:
    """Portfolio state machine for the backtester.

    Parameters
    ----------
    initial_capital:
        Starting cash in USD.
    commission_per_share:
        One-way commission charged per share.
    slippage_per_share:
        One-way slippage applied per share (added to buy price,
        subtracted from sell price).
    """

    def __init__(
        self,
        initial_capital: float,
        commission_per_share: float = 0.005,
        slippage_per_share: float   = 0.01,
    ) -> None:
        self.initial_capital     = initial_capital
        self.cash                = initial_capital
        self.commission_per_share = commission_per_share
        self.slippage_per_share   = slippage_per_share

        self._positions: dict[str, OpenPosition] = {}   # symbol → OpenPosition
        self._trades:    list[Trade]              = []
        self._equity_curve: list[dict[str, Any]] = []   # snapshots

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def positions(self) -> dict[str, OpenPosition]:
        """Currently open positions keyed by symbol."""
        return dict(self._positions)

    @property
    def trades(self) -> list[Trade]:
        """All completed trades (immutable view)."""
        return list(self._trades)

    @property
    def equity_curve(self) -> pd.DataFrame:
        """Equity curve as a DataFrame with columns ``timestamp`` and ``equity``."""
        return pd.DataFrame(self._equity_curve).set_index("timestamp") if self._equity_curve else pd.DataFrame()

    # ------------------------------------------------------------------
    # Market-value helpers
    # ------------------------------------------------------------------

    def open_position_value(self, current_prices: dict[str, float]) -> float:
        """Mark open positions to market at *current_prices*.

        Parameters
        ----------
        current_prices:
            ``{symbol: price}`` mapping — typically the close of the current bar.
        """
        value = 0.0
        for sym, pos in self._positions.items():
            price = current_prices.get(sym, pos.entry_price)
            value += pos.shares * price
        return value

    def total_equity(self, current_prices: dict[str, float]) -> float:
        """Cash + mark-to-market value of open positions."""
        return self.cash + self.open_position_value(current_prices)

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def open_long(
        self,
        symbol: str,
        entry_price: float,
        timestamp: pd.Timestamp,
        position_size_pct: float,
        stop_loss: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> OpenPosition | None:
        """Buy *symbol* using *position_size_pct* of available cash.

        Returns ``None`` (and does nothing) if:
        * there is already an open position in *symbol*, or
        * available cash is insufficient to buy even one share.

        Slippage is added to the fill price; commission is deducted from cash
        at entry.
        """
        if symbol in self._positions:
            logger.debug("open_long(%s) skipped — position already open", symbol)
            return None

        # Apply buy-side slippage
        fill_price = entry_price + self.slippage_per_share

        # Size the position
        cash_to_use = self.cash * position_size_pct
        shares = cash_to_use / fill_price
        if shares < 1:
            logger.warning("open_long(%s): insufficient cash (%.2f) to buy even 1 share", symbol, self.cash)
            return None
        shares = float(int(shares))  # whole shares only

        # Entry commission
        commission_entry = shares * self.commission_per_share
        total_cost = shares * fill_price + commission_entry

        if total_cost > self.cash:
            logger.warning("open_long(%s): cost %.2f > cash %.2f — skipping", symbol, total_cost, self.cash)
            return None

        self.cash -= total_cost

        pos = OpenPosition(
            symbol=symbol,
            entry_time=timestamp,
            entry_price=fill_price,
            shares=shares,
            stop_loss=stop_loss,
            direction="LONG",
            meta=meta or {},
        )
        self._positions[symbol] = pos
        logger.info(
            "ENTER  %-5s | %s | price=%.4f | shares=%.0f | cash_remaining=%.2f",
            symbol, timestamp, fill_price, shares, self.cash,
        )
        return pos

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        timestamp: pd.Timestamp,
        reason: str = "signal",
    ) -> Trade | None:
        """Close an open position for *symbol*.

        Returns the completed :class:`Trade` or ``None`` if no open position.
        Slippage is subtracted from the fill price; exit commission is
        deducted from cash.
        """
        pos = self._positions.pop(symbol, None)
        if pos is None:
            logger.debug("close_position(%s) called but no open position", symbol)
            return None

        # Apply sell-side slippage
        fill_price = exit_price - self.slippage_per_share

        # Exit commission
        commission_exit  = pos.shares * self.commission_per_share
        total_commission = pos.shares * self.commission_per_share + commission_exit  # entry + exit
        proceeds = pos.shares * fill_price - commission_exit

        self.cash += proceeds

        trade = Trade(
            symbol=pos.symbol,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            shares=pos.shares,
            commission=total_commission,
            direction=pos.direction,
            exit_reason=reason,
            meta=pos.meta,
        )
        self._trades.append(trade)

        logger.info(
            "EXIT   %-5s | %s | price=%.4f | pnl=%.2f | reason=%s",
            symbol, timestamp, fill_price, trade.pnl, reason,
        )
        return trade

    # ------------------------------------------------------------------
    # Equity snapshot
    # ------------------------------------------------------------------

    def record_equity(self, timestamp: pd.Timestamp, current_prices: dict[str, float]) -> None:
        """Append a timestamped equity snapshot to the equity curve."""
        self._equity_curve.append({
            "timestamp": timestamp,
            "equity":    self.total_equity(current_prices),
        })
