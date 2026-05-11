"""
strategy/opening_range_breakout.py
-----------------------------------
Opening Range Breakout (ORB) strategy — long only.

Rules (all times in US Eastern):
  1. Opening range   = [09:30, 10:00)
  2. opening_range_high / opening_range_low are the max/min of that range.
  3. After 10:00, if a bar's *close* exceeds opening_range_high → LONG signal.
  4. Stop-loss = opening_range_low.
  5. At most one trade per symbol per day (tracked by the engine, not here).
  6. Force-exit at 15:55 is handled by the engine / risk manager.

Design note
-----------
The strategy only computes signals; position sizing, commission, and exit
management are delegated to the engine and risk manager, keeping this class
narrowly focused and easy to unit-test.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

from src.strategy.base import BaseStrategy, Signal, SignalDirection
from src.utils.logger import get_logger

logger = get_logger(__name__)

_EASTERN = ZoneInfo("America/New_York")


class OpeningRangeBreakout(BaseStrategy):
    """Long-only Opening Range Breakout strategy.

    Parameters (via ``params`` dict from settings.yaml)
    ---------------------------------------------------
    opening_range_start : str  — ``"HH:MM"`` Eastern, default ``"09:30"``
    opening_range_end   : str  — ``"HH:MM"`` Eastern, default ``"10:00"``
    force_exit_time     : str  — ``"HH:MM"`` Eastern, default ``"15:55"``
    position_size_pct   : float — fraction of cash to deploy, default ``0.95``
    long_only           : bool  — ``True`` for MVP
    """

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)

        self._range_start_str: str = params.get("opening_range_start", "09:30")
        self._range_end_str:   str = params.get("opening_range_end",   "10:00")
        self._force_exit_str:  str = params.get("force_exit_time",     "15:55")
        self._long_only: bool      = bool(params.get("long_only", True))

        # Per-day cache:  date → {"high": float, "low": float, "formed": bool}
        self._daily_range: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate_signal(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_bar: pd.Timestamp,
    ) -> Signal | None:
        """Evaluate *current_bar* and return a :class:`Signal` or ``None``.

        This method is called once per bar by the backtest engine.  ``bars``
        contains *only* data up to and including ``current_bar`` — no
        look-ahead is possible.

        Parameters
        ----------
        symbol, bars, current_bar:
            See :class:`~src.strategy.base.BaseStrategy`.
        """
        bar_time  = current_bar.astimezone(_EASTERN)
        bar_date  = bar_time.date().isoformat()
        bar_hhmm  = bar_time.strftime("%H:%M")

        range_start = self._range_start_str   # "09:30"
        range_end   = self._range_end_str     # "10:00"
        force_exit  = self._force_exit_str    # "15:55"

        # ---- Step 1: accumulate the opening range ----------------------
        if range_start <= bar_hhmm < range_end:
            self._update_opening_range(symbol, bar_date, bars.loc[current_bar])
            return None   # no signal during range formation

        # ---- Step 2: mark range as fully formed once 10:00 arrives ----
        if bar_hhmm == range_end or (
            bar_date in self._daily_range
            and not self._daily_range[bar_date].get("formed", False)
            and bar_hhmm > range_end
        ):
            self._mark_range_formed(symbol, bar_date, bars, range_start, range_end)

        # ---- Step 3: no range available yet (e.g. first bar of day) ---
        if bar_date not in self._daily_range or not self._daily_range[bar_date].get("formed"):
            return None

        # ---- Step 4: force-exit handled by the engine — no signal here
        if bar_hhmm >= force_exit:
            return None

        # ---- Step 5: breakout detection --------------------------------
        or_high: float = self._daily_range[bar_date]["high"]
        or_low:  float = self._daily_range[bar_date]["low"]
        bar_close: float = float(bars.loc[current_bar, "close"])

        if bar_close > or_high:
            logger.debug(
                "%s %s — breakout detected close=%.4f > or_high=%.4f",
                symbol, bar_hhmm, bar_close, or_high,
            )
            return Signal(
                direction=SignalDirection.LONG,
                symbol=symbol,
                entry_price=bar_close,   # fill at close of breakout bar
                stop_loss=or_low,
                timestamp=current_bar,
                meta={"or_high": or_high, "or_low": or_low},
            )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_opening_range(
        self, symbol: str, date_str: str, bar: pd.Series
    ) -> None:
        """Extend the day's opening-range high/low with *bar*."""
        key = date_str
        if key not in self._daily_range:
            self._daily_range[key] = {
                "high": float(bar["high"]),
                "low":  float(bar["low"]),
                "formed": False,
            }
        else:
            self._daily_range[key]["high"] = max(
                self._daily_range[key]["high"], float(bar["high"])
            )
            self._daily_range[key]["low"] = min(
                self._daily_range[key]["low"], float(bar["low"])
            )

    def _mark_range_formed(
        self,
        symbol: str,
        date_str: str,
        bars: pd.DataFrame,
        range_start: str,
        range_end: str,
    ) -> None:
        """Recompute the opening range from *bars* and mark it formed.

        We re-derive from the full bar slice rather than relying on the
        incremental cache so that the result is always correct even if
        the engine calls this strategy on a partial day's data.
        """
        # Filter bars that fall within [range_start, range_end)
        idx = bars.index[
            (bars.index.strftime("%H:%M") >= range_start) &
            (bars.index.strftime("%H:%M") <  range_end)  &
            (bars.index.date.astype(str) == date_str)
        ]
        if idx.empty:
            logger.warning("%s %s: no bars in opening range — cannot form range", symbol, date_str)
            return

        or_high = float(bars.loc[idx, "high"].max())
        or_low  = float(bars.loc[idx, "low"].min())

        self._daily_range[date_str] = {"high": or_high, "low": or_low, "formed": True}
        logger.debug(
            "%s %s — range formed: high=%.4f  low=%.4f",
            symbol, date_str, or_high, or_low,
        )
