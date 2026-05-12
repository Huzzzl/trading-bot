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


_VALID_STOP_EXECUTION    = {"bar_close", "stop_price"}
_VALID_DAILY_LOSS_ACTION = {"block_new_entries", "close_all"}


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
    max_open_positions:
        Maximum number of concurrent open positions across all symbols.
        ``None`` means unlimited.  Set to ``1`` for strictly sequential
        trading (deterministic results when multiple symbols trigger
        simultaneously).
    stop_execution:
        How to price a stop-loss exit when ``bar.low <= stop_loss``.

        ``"bar_close"``  — exit at the bar's closing price (default).
        ``"stop_price"`` — exit at the exact stop-loss price; Portfolio will
        still deduct normal sell-side slippage and commission on top.
    daily_loss_limit_pct:
        Maximum allowed daily loss as a positive percentage of day-start
        equity (e.g. ``2.0`` means −2 %).  ``None`` (default) disables the
        kill-switch entirely.
    daily_loss_action:
        What to do when the daily loss limit is breached.

        ``"block_new_entries"`` — reject all new entry signals for the rest
        of the day; existing open positions are managed normally.
        ``"close_all"`` — immediately close every open position at bar close
        and block new entries for the rest of the day.
    """

    def __init__(
        self,
        force_exit_time: str = "15:55",
        max_trades_per_symbol_per_day: int = 1,
        max_open_positions: int | None = None,
        stop_execution: str = "bar_close",
        daily_loss_limit_pct: float | None = None,
        daily_loss_action: str = "block_new_entries",
    ) -> None:
        if stop_execution not in _VALID_STOP_EXECUTION:
            raise ValueError(
                f"Invalid stop_execution={stop_execution!r}. "
                f"Must be one of {sorted(_VALID_STOP_EXECUTION)}."
            )
        if daily_loss_action not in _VALID_DAILY_LOSS_ACTION:
            raise ValueError(
                f"Invalid daily_loss_action={daily_loss_action!r}. "
                f"Must be one of {sorted(_VALID_DAILY_LOSS_ACTION)}."
            )
        self._force_exit_time       = force_exit_time
        self._max_trades_per_day    = max_trades_per_symbol_per_day
        self._max_open_positions    = max_open_positions
        self._stop_execution        = stop_execution
        self._daily_loss_limit_pct  = daily_loss_limit_pct
        self._daily_loss_action     = daily_loss_action

        # Tracks {date_str: {symbol: trade_count}}
        self._daily_trade_count: dict[str, dict[str, int]] = {}

        # Daily loss kill-switch state — reset at the start of each new day
        self._current_day:       str | None   = None
        self._day_start_equity:  float | None = None
        self._daily_limit_breached: bool      = False

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

        # Rule: daily loss limit — block new entries for the rest of this day
        if self._daily_limit_breached:
            logger.debug(
                "approve_entry(%s): rejected — daily loss limit breached today", signal.symbol
            )
            return False

        # Rule: no duplicate open positions
        if signal.symbol in portfolio.positions:
            logger.debug("approve_entry(%s): rejected — position already open", signal.symbol)
            return False

        # Rule: portfolio-level open-position cap
        if (
            self._max_open_positions is not None
            and len(portfolio.positions) >= self._max_open_positions
        ):
            logger.debug(
                "approve_entry(%s): rejected — max_open_positions=%d reached",
                signal.symbol, self._max_open_positions,
            )
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
        bar_eastern   = current_bar.astimezone(_EASTERN)
        bar_hhmm      = bar_eastern.strftime("%H:%M")
        bar_date      = bar_eastern.date().isoformat()
        is_force_exit = bar_hhmm >= self._force_exit_time

        exits: list[tuple[str, float, str]] = []

        # ---- Daily loss kill-switch ----------------------------------------
        if self._daily_loss_limit_pct is not None:
            current_prices  = {sym: float(data["close"]) for sym, data in bar_data.items()}
            current_equity  = portfolio.total_equity(current_prices)

            # New trading day: snapshot day-start equity and reset breach flag.
            if bar_date != self._current_day:
                self._current_day          = bar_date
                self._day_start_equity     = current_equity
                self._daily_limit_breached = False

            # Check breach only once per day (after it is set we stop re-checking).
            if (
                not self._daily_limit_breached
                and self._day_start_equity is not None
                and self._day_start_equity > 0
            ):
                daily_pnl_pct = (
                    (current_equity - self._day_start_equity)
                    / self._day_start_equity * 100.0
                )
                if daily_pnl_pct <= -self._daily_loss_limit_pct:
                    self._daily_limit_breached = True
                    logger.warning(
                        "DAILY_LOSS_LIMIT %s — daily_pnl=%.2f%% <= -%.2f%%  action=%s",
                        bar_date, daily_pnl_pct,
                        self._daily_loss_limit_pct, self._daily_loss_action,
                    )
                    if self._daily_loss_action == "close_all":
                        for sym, pos in list(portfolio.positions.items()):
                            bar   = bar_data.get(sym)
                            price = float(bar["close"]) if bar is not None else pos.entry_price
                            exits.append((sym, price, "daily_loss_limit"))
                            logger.debug(
                                "DAILY_LOSS_LIMIT close_all: %s at %.4f", sym, price
                            )

        # Symbols already scheduled for close_all — skip in the normal loop
        # so that one position never generates two exit orders on the same bar.
        daily_loss_closed: set[str] = {sym for sym, _, r in exits if r == "daily_loss_limit"}

        # ---- Per-position stop-loss and force-exit -------------------------
        for symbol, pos in list(portfolio.positions.items()):
            if symbol in daily_loss_closed:
                continue
            if symbol not in bar_data:
                continue

            bar = bar_data[symbol]
            close_px: float = bar["close"]
            low_px:   float = bar["low"]

            # Priority 1: stop-loss
            if pos.stop_loss is not None and low_px <= pos.stop_loss:
                if self._stop_execution == "stop_price":
                    exit_px = pos.stop_loss
                else:  # "bar_close"
                    exit_px = close_px
                exits.append((symbol, exit_px, "stop_loss"))
                logger.debug(
                    "STOP   %s — low=%.4f <= stop=%.4f  exit_px=%.4f  mode=%s",
                    symbol, low_px, pos.stop_loss, exit_px, self._stop_execution,
                )
                continue  # don't also force-exit the same position

            # Priority 2: force EOD exit
            if is_force_exit:
                exits.append((symbol, close_px, "force_exit"))
                logger.debug("FORCE_EXIT %s at %.4f", symbol, close_px)

        return exits
