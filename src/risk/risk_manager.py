"""
risk/risk_manager.py
--------------------
Stateless risk checks applied before every trade and on every bar.

The risk manager answers two questions:
  1. ``approve_entry``  — is it safe to open a new position right now?
  2. ``check_exits``    — should any open positions be closed right now?

Keeping risk logic here (rather than inside the engine or strategy) means
that new rules (e.g. daily-loss limits, sector exposure caps) can be added
without touching either.

TODO (Alpaca integration): add a real-time buying-power check against the
Alpaca ``GET /v2/account`` endpoint before approving any entry.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

from src.backtest.trade import Trade
from src.portfolio.portfolio import OpenPosition, Portfolio
from src.strategy.base import Signal
from src.utils.logger import get_logger

logger = get_logger(__name__)

_EASTERN = ZoneInfo("America/New_York")


class RiskManager:
    """Apply pre-trade and per-bar risk rules.

    Parameters
    ----------
    force_exit_time:
        ``"HH:MM"`` string (Eastern) — all positions are closed at or after
        this time regardless of P&L.
    max_trades_per_symbol_per_day:
        Hard cap on how many times a symbol can be traded in one session.
        Defaults to 1 (ORB strategy requirement).
    """

    def __init__(
        self,
        force_exit_time: str = "15:55",
        max_trades_per_symbol_per_day: int = 1,
    ) -> None:
        self._force_exit_time = force_exit_time
        self._max_trades_per_day = max_trades_per_symbol_per_day
        # Tracks {date_str: {symbol: trade_count}}
        self._daily_trade_count: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------
    # Pre-entry gate
    # ------------------------------------------------------------------

    def approve_entry(
        self,
        signal: Signal,
        portfolio: Portfolio,
        current_bar: pd.Timestamp,
    ) -> bool:
        """Return ``True`` iff the signal is safe to act on.

        Checks:
        * No existing open position for the symbol.
        * Daily trade count not exceeded.
        * Current time is before ``force_exit_time``.
        * Sufficient cash available (delegated to Portfolio.open_long).
        """
        bar_eastern = current_bar.astimezone(_EASTERN)
        bar_hhmm    = bar_eastern.strftime("%H:%M")
        date_str    = bar_eastern.date().isoformat()

        # Rule: hard stop — no new entries after force-exit time
        if bar_hhmm >= self._force_exit_time:
            logger.debug("approve_entry(%s): rejected — past force_exit_time", signal.symbol)
            return False

        # Rule: no duplicate open positions
        if signal.symbol in portfolio.positions:
            logger.debug("approve_entry(%s): rejected — position already open", signal.symbol)
            return False

        # Rule: max trades per day
        daily = self._daily_trade_count.setdefault(date_str, {})
        count = daily.get(signal.symbol, 0)
        if count >= self._max_trades_per_day:
            logger.debug(
                "approve_entry(%s): rejected — %d trade(s) already taken today",
                signal.symbol, count,
            )
            return False

        return True

    def record_trade_taken(self, symbol: str, date_str: str) -> None:
        """Increment the daily trade counter for *symbol* on *date_str*.

        Called by the engine immediately after a position is opened.
        """
        daily = self._daily_trade_count.setdefault(date_str, {})
        daily[symbol] = daily.get(symbol, 0) + 1

    # ------------------------------------------------------------------
    # Per-bar exit rules
    # ------------------------------------------------------------------

    def check_exits(
        self,
        portfolio: Portfolio,
        current_bar: pd.Timestamp,
        bar_data: dict[str, dict[str, float]],
    ) -> list[tuple[str, float, str]]:
        """Return a list of ``(symbol, exit_price, reason)`` to close now.

        Evaluated in priority order:
          1. Stop-loss hit (bar low ≤ stop_loss).
          2. Force-exit time reached.

        Parameters
        ----------
        portfolio:
            Current portfolio state.
        current_bar:
            Timestamp of the current bar.
        bar_data:
            ``{symbol: {"open": …, "high": …, "low": …, "close": …}}``
            for all symbols.
        """
        bar_eastern = current_bar.astimezone(_EASTERN)
        bar_hhmm    = bar_eastern.strftime("%H:%M")
        is_force_exit = bar_hhmm >= self._force_exit_time

        exits: list[tuple[str, float, str]] = []

        for symbol, pos in list(portfolio.positions.items()):
            if symbol not in bar_data:
                continue

            bar = bar_data[symbol]
            close_px: float = bar["close"]
            low_px:   float = bar["low"]

            # Priority 1: stop-loss
            if pos.stop_loss is not None and low_px <= pos.stop_loss:
                # Assume we fill at the stop price (conservative)
                exit_px = min(pos.stop_loss, close_px)
                exits.append((symbol, exit_px, "stop_loss"))
                logger.debug(
                    "STOP   %s — low=%.4f <= stop=%.4f", symbol, low_px, pos.stop_loss
                )
                continue  # don't also force-exit the same position

            # Priority 2: force EOD exit
            if is_force_exit:
                exits.append((symbol, close_px, "force_exit"))
                logger.debug("FORCE_EXIT %s at %.4f", symbol, close_px)

        return exits
