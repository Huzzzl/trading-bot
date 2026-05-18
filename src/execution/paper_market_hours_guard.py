"""
execution/paper_market_hours_guard.py
--------------------------------------
Read-only market-hours guard for paper order submissions.

Before any real paper buy-submit or close-submit, main.py calls
assert_regular_market_hours() to verify that the current New York time is
inside regular NYSE trading hours (Mon–Fri, 09:30–15:59:59 ET).

No holiday or early-close calendar is applied in this implementation.
That can be layered on top later without changing this module's interface.

What this module never does
---------------------------
* Never calls submit_order or cancel_order.
* Never writes to the ledger.
* Never reads Alpaca credentials.
* Never enforces during preview-only flows (caller responsibility).
"""

from __future__ import annotations

import datetime
from typing import Callable

import pandas as pd

from src.utils.logger import get_logger

_logger = get_logger(__name__)

_EASTERN = "America/New_York"
_MARKET_OPEN  = datetime.time(9, 30)
_MARKET_CLOSE = datetime.time(16, 0)   # exclusive upper bound


def is_regular_market_hours(now: pd.Timestamp) -> bool:
    """Return True if *now* falls inside regular NYSE market hours.

    Parameters
    ----------
    now:
        A timezone-aware :class:`pandas.Timestamp`.  The caller is responsible
        for passing a value in the correct timezone (America/New_York is
        strongly recommended).

    Returns
    -------
    bool
        ``True`` when *now* is Monday–Friday and
        ``09:30:00 <= now.time() < 16:00:00`` (ET).
    """
    if now.dayofweek >= 5:   # 5=Saturday, 6=Sunday
        return False
    t = now.time()
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def assert_regular_market_hours(
    now_provider: Callable[[], pd.Timestamp] | None = None,
) -> None:
    """Raise RuntimeError if the current time is outside regular market hours.

    Parameters
    ----------
    now_provider:
        Zero-argument callable that returns a timezone-aware
        :class:`pandas.Timestamp` representing "now".  When ``None`` the
        function uses :func:`pandas.Timestamp.now` with ``America/New_York``.
        Pass a custom callable in tests to pin the clock without monkey-
        patching the global ``pd.Timestamp.now``.

    Raises
    ------
    RuntimeError
        If the current time is outside Mon–Fri 09:30–15:59:59 ET.
        The message always states "market is closed" and
        "no order was submitted".
    """
    if now_provider is None:
        now = pd.Timestamp.now(tz=_EASTERN)
    else:
        now = now_provider()

    if not is_regular_market_hours(now):
        day_name = now.day_name()
        time_str = now.strftime("%H:%M:%S %Z")
        raise RuntimeError(
            f"market is closed — no order was submitted. "
            f"Current time: {day_name} {time_str}. "
            f"Regular market hours are Monday–Friday 09:30–16:00 ET."
        )

    _logger.info(
        "Market-hours guard passed: %s %s is within regular trading hours.",
        now.day_name(),
        now.strftime("%H:%M:%S %Z"),
    )
