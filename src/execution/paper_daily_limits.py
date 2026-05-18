"""
execution/paper_daily_limits.py
--------------------------------
Read-only daily safety-limit enforcement for paper order submissions.

Before any real paper buy-submit or close-submit, main.py calls
assert_within_daily_limits() to verify that adding the new order would not
exceed the configured daily caps.  All checks are read-only: no broker calls,
no ledger writes.

What this module never does
---------------------------
* Never calls submit_order, cancel_order, or any broker method.
* Never writes to the ledger.
* Never reads Alpaca credentials.
* Never enforces during preview-only flows (caller responsibility).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.logger import get_logger

_logger = get_logger(__name__)

# Statuses (normalised lower-case) that do NOT count against daily limits.
_EXCLUDED_STATUSES: frozenset[str] = frozenset({"rejected", "cancelled", "canceled"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_trading_date(value: Any, tz: str) -> date | None:
    """Try to parse *value* as a tz-aware timestamp and return the local date.

    Returns None when the value is missing or unparseable.
    """
    if not value or (isinstance(value, float)):
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none", ""):
        return None
    try:
        ts = pd.Timestamp(s)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(tz).date()
    except Exception:
        return None


def _row_date(row: pd.Series, tz: str) -> date | None:
    """Return the local trading date for a ledger row.

    Prefers submitted_at; falls back to created_at_utc.
    """
    d = _parse_trading_date(row.get("submitted_at"), tz)
    if d is None:
        d = _parse_trading_date(row.get("created_at_utc"), tz)
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_daily_usage(
    ledger_path: Path,
    trading_date: date,
    tz: str = "America/New_York",
) -> dict[str, Any]:
    """Count ledger rows that fall on *trading_date* and are not excluded.

    Parameters
    ----------
    ledger_path:
        Path to the paper execution ledger CSV.  May be absent.
    trading_date:
        The calendar date (in *tz*) to count.
    tz:
        IANA timezone string used to convert timestamp columns.

    Returns
    -------
    dict with keys:
        ``total_orders`` – all qualifying rows for the day.
        ``buy_orders``   – rows where ``flow == "buy_submit"``.
        ``close_orders`` – rows where ``flow == "close_submit"``.
    """
    if not ledger_path.exists():
        return {"total_orders": 0, "buy_orders": 0, "close_orders": 0}

    df = pd.read_csv(ledger_path, dtype=str).fillna("")
    if df.empty:
        return {"total_orders": 0, "buy_orders": 0, "close_orders": 0}

    total = buy = close = 0
    for _, row in df.iterrows():
        status = str(row.get("status", "")).strip().lower()
        if status in _EXCLUDED_STATUSES:
            continue
        row_date = _row_date(row, tz)
        if row_date != trading_date:
            continue
        total += 1
        flow = str(row.get("flow", "")).strip()
        if flow == "buy_submit":
            buy += 1
        elif flow == "close_submit":
            close += 1

    return {"total_orders": total, "buy_orders": buy, "close_orders": close}


def assert_within_daily_limits(
    ledger_path: Path,
    trading_date: date,
    flow: str,
    intent_quantity: float,
    *,
    max_orders: int | None = None,
    max_buy_orders: int | None = None,
    max_close_orders: int | None = None,
    max_notional: float | None = None,
    intent_price: float | None = None,
    tz: str = "America/New_York",
) -> None:
    """Raise RuntimeError if submitting this order would exceed any daily limit.

    Parameters
    ----------
    ledger_path:
        Path to the paper execution ledger CSV.
    trading_date:
        Current trading date (in *tz*).
    flow:
        ``"buy_submit"`` or ``"close_submit"``.
    intent_quantity:
        Quantity of the order being checked.
    max_orders:
        Maximum total paper orders (buy + close) for the day.  ``None`` = no limit.
    max_buy_orders:
        Maximum buy-submit orders for the day.  ``None`` = no limit.
    max_close_orders:
        Maximum close-submit orders for the day.  ``None`` = no limit.
    max_notional:
        Maximum notional (``quantity * price``) for this order.  ``None`` = no limit.
    intent_price:
        Price used to compute notional.  Required when *max_notional* is not None.
        If *max_notional* is set and *intent_price* is None or <= 0, raises before
        submit (fail closed).
    tz:
        IANA timezone string.

    Raises
    ------
    RuntimeError
        If any limit would be exceeded.  The message always states
        "daily paper limit exceeded" and "no order was submitted".
    """
    usage = compute_daily_usage(ledger_path, trading_date, tz)

    violations: list[str] = []

    if max_orders is not None and usage["total_orders"] >= max_orders:
        violations.append(
            f"paper_daily_max_orders={max_orders} already reached "
            f"(today's submitted orders: {usage['total_orders']})"
        )

    if flow == "buy_submit" and max_buy_orders is not None:
        if usage["buy_orders"] >= max_buy_orders:
            violations.append(
                f"paper_daily_max_buy_orders={max_buy_orders} already reached "
                f"(today's buy orders: {usage['buy_orders']})"
            )

    if flow == "close_submit" and max_close_orders is not None:
        if usage["close_orders"] >= max_close_orders:
            violations.append(
                f"paper_daily_max_close_orders={max_close_orders} already reached "
                f"(today's close orders: {usage['close_orders']})"
            )

    if max_notional is not None:
        if intent_price is None or intent_price <= 0:
            violations.append(
                f"paper_daily_max_notional={max_notional} is enabled but "
                f"price is unavailable (intent_price={intent_price!r}); "
                "cannot verify notional — failing closed"
            )
        else:
            order_notional = intent_quantity * intent_price
            if order_notional > max_notional:
                violations.append(
                    f"paper_daily_max_notional={max_notional} exceeded: "
                    f"this order notional={order_notional:.2f} "
                    f"(qty={intent_quantity} * price={intent_price})"
                )

    if violations:
        raise RuntimeError(
            "daily paper limit exceeded — no order was submitted. "
            "Violations: " + "; ".join(violations)
        )

    _logger.info(
        "Daily limits OK for flow=%s date=%s total=%d buy=%d close=%d",
        flow, trading_date,
        usage["total_orders"], usage["buy_orders"], usage["close_orders"],
    )
