"""
execution/paper_open_order_guard.py
-------------------------------------
Read-only open-order guard for paper order submissions.

Before any real paper buy-submit or close-submit, main.py calls
assert_no_open_orders_for_symbol() to verify that no open Alpaca paper order
already exists for the selected symbol.  All checks are read-only: the client
is only queried, never mutated.

main.py passes ``broker._get_client`` (a zero-argument callable) rather than
the client object itself, so the client is only created when the guard
actually runs — not when the guard is patched to a no-op in tests.

What this module never does
---------------------------
* Never calls submit_order or cancel_order.
* Never writes to the ledger.
* Never reads Alpaca credentials directly.
* Never enforces during preview-only flows (caller responsibility).
"""

from __future__ import annotations

from typing import Any, Callable

from src.utils.logger import get_logger

_logger = get_logger(__name__)


def find_open_orders_for_symbol(client: Any, symbol: str) -> list[Any]:
    """Return all open orders for *symbol* from the Alpaca paper client.

    Uses ``get_orders`` if available, falling back to ``list_orders``.
    Filters results to the normalised uppercase symbol.

    Parameters
    ----------
    client:
        A ``TradingClient`` (real SDK) or test double that exposes
        ``get_orders`` or ``list_orders``.
    symbol:
        The ticker symbol to check (case-insensitive).

    Returns
    -------
    list
        Open order objects (dict or SDK object) whose symbol matches.
        Empty list when none found.

    Raises
    ------
    RuntimeError
        If the client exposes neither ``get_orders`` nor ``list_orders``,
        or if the API call raises an exception.
    """
    sym_upper = symbol.upper()

    try:
        if hasattr(client, "get_orders"):
            raw = client.get_orders()
        elif hasattr(client, "list_orders"):
            raw = client.list_orders()
        else:
            raise RuntimeError(
                f"Alpaca client exposes neither get_orders nor list_orders — "
                f"cannot check open orders for {sym_upper!r}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Open-order lookup failed for {sym_upper!r}: {exc}"
        ) from exc

    if raw is None:
        return []

    matches: list[Any] = []
    for order in raw:
        if isinstance(order, dict):
            order_sym = str(order.get("symbol", "")).upper()
        else:
            order_sym = str(getattr(order, "symbol", "")).upper()
        if order_sym == sym_upper:
            matches.append(order)

    return matches


def assert_no_open_orders_for_symbol(get_client: Callable[[], Any], symbol: str) -> None:
    """Raise RuntimeError if any open Alpaca paper order exists for *symbol*.

    Parameters
    ----------
    get_client:
        Zero-argument callable that returns a ``TradingClient`` (real SDK) or
        test double.  Called lazily so that patching this function as a no-op
        in tests never triggers client creation.
    symbol:
        The ticker symbol to check.

    Raises
    ------
    RuntimeError
        If one or more open orders are found for *symbol*, or if client
        creation or the lookup itself fails.  The message always states
        "open paper order detected" and "no order was submitted".
    """
    try:
        client = get_client()
    except Exception as exc:
        raise RuntimeError(
            f"Open-order lookup failed for {symbol.upper()!r}: "
            f"could not create client — {exc}"
        ) from exc

    open_orders = find_open_orders_for_symbol(client, symbol)

    if open_orders:
        raise RuntimeError(
            f"open paper order detected for {symbol.upper()!r} — no order was submitted. "
            f"Cancel or wait for the existing open order(s) to complete before submitting "
            f"another order. Open order count: {len(open_orders)}."
        )

    _logger.info("Open-order guard passed for %r: no open orders found.", symbol.upper())
