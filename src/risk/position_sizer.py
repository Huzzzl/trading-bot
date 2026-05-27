"""
risk/position_sizer.py
-----------------------
Pure position-sizing helpers for long trades.

calculate_shares_by_risk() computes whole-share counts from equity risk and
stop distance.  calculate_notional() converts shares back to dollar exposure.

Both functions are pure, deterministic, and offline-only.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
**No orders are placed.**
**No trade records are written.**
**No portfolio state is mutated.**
**No settings are mutated.**
"""

from __future__ import annotations

import math


def calculate_shares_by_risk(
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    *,
    max_notional: float | None = None,
) -> int:
    """Return whole shares to buy for a long trade based on max risk.

    Formula
    -------
    risk_amount    = equity * risk_pct / 100
    per_share_risk = entry_price - stop_price
    shares         = floor(risk_amount / per_share_risk)

    If ``max_notional`` is provided, the result is additionally capped:
    shares = min(shares, floor(max_notional / entry_price))

    The return value is always >= 0.  If the computed share count is less
    than 1, 0 is returned — the trade is not sized.

    Parameters
    ----------
    equity:
        Total account equity in dollars.  Must be > 0.
    risk_pct:
        Fraction of equity to risk on this trade, expressed as a percent
        (e.g. ``1.0`` means 1 %).  Must be > 0.
    entry_price:
        Expected entry price per share.  Must be > 0.
    stop_price:
        Stop-loss price per share.  Must be > 0 and < ``entry_price``.
    max_notional:
        Optional hard cap on the total position value in dollars.  When
        provided must be > 0.  ``None`` means no cap.

    Returns
    -------
    int
        Number of whole shares to buy.  Returns 0 when the computed size
        is less than 1.

    Raises
    ------
    ValueError
        ``"invalid position sizing parameters"`` when any input is out of
        range.  Raw values are never echoed in the message.
    """
    try:
        equity = float(equity)
        risk_pct = float(risk_pct)
        entry_price = float(entry_price)
        stop_price = float(stop_price)
        if max_notional is not None:
            max_notional = float(max_notional)
    except (TypeError, ValueError):
        raise ValueError("invalid position sizing parameters")

    if equity <= 0:
        raise ValueError("invalid position sizing parameters")
    if risk_pct <= 0:
        raise ValueError("invalid position sizing parameters")
    if entry_price <= 0:
        raise ValueError("invalid position sizing parameters")
    if stop_price <= 0:
        raise ValueError("invalid position sizing parameters")
    if stop_price >= entry_price:
        raise ValueError("invalid position sizing parameters")
    if max_notional is not None and max_notional <= 0:
        raise ValueError("invalid position sizing parameters")

    risk_amount = equity * risk_pct / 100.0
    per_share_risk = entry_price - stop_price
    shares = math.floor(risk_amount / per_share_risk)

    if max_notional is not None:
        shares_by_notional = math.floor(max_notional / entry_price)
        shares = min(shares, shares_by_notional)

    return max(0, shares)


def calculate_notional(shares: int, entry_price: float) -> float:
    """Return the dollar notional value of a position.

    Parameters
    ----------
    shares:
        Number of whole shares.  Must be >= 0.
    entry_price:
        Price per share.  Must be > 0.

    Returns
    -------
    float
        ``shares * entry_price``.

    Raises
    ------
    ValueError
        ``"invalid position sizing parameters"`` when any input is invalid.
        Raw values are never echoed in the message.
    """
    try:
        shares = int(shares)
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        raise ValueError("invalid position sizing parameters")

    if shares < 0:
        raise ValueError("invalid position sizing parameters")
    if entry_price <= 0:
        raise ValueError("invalid position sizing parameters")

    return shares * entry_price
