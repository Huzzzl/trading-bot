"""
tools/live_account_check.py
-----------------------------
Read-only live Alpaca account verification CLI.

Usage::

    python -m src.tools.live_account_check

This tool is **not** a live trading submission tool.  It only reads account
state so you can verify live credentials and account health before any future
live trading work.

What it does
------------
1. Resolves live credentials from ``ALPACA_LIVE_API_KEY`` and
   ``ALPACA_LIVE_SECRET_KEY`` environment variables.
2. Creates an Alpaca ``TradingClient`` with ``paper=False``.
3. Reads account status, buying power, portfolio value, open positions,
   and open orders — all read-only.
4. Prints a structured report and exits 0 (PASS) or 1 (WARN / FAIL).

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never modifies positions or orders.
* Never reads paper credentials (``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``).
* Never writes any file.
"""

from __future__ import annotations

import os
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _resolve_live_credentials() -> tuple[str, str]:
    """Return ``(api_key, secret_key)`` from live env vars, or raise.

    Uses ``ALPACA_LIVE_API_KEY`` and ``ALPACA_LIVE_SECRET_KEY`` exclusively.
    Paper credentials (``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``) are
    deliberately ignored.

    Raises
    ------
    RuntimeError
        If either variable is missing or empty.
    """
    key    = os.environ.get("ALPACA_LIVE_API_KEY",    "").strip()
    secret = os.environ.get("ALPACA_LIVE_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError(
            "Missing live Alpaca credentials. "
            "Set ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY before running. "
            "Do NOT use paper credentials (ALPACA_API_KEY / ALPACA_SECRET_KEY)."
        )
    return key, secret


# ---------------------------------------------------------------------------
# Client creation
# ---------------------------------------------------------------------------

def _make_live_client(api_key: str, secret_key: str) -> Any:
    """Create a live (non-paper) ``TradingClient`` for read-only queries.

    Parameters
    ----------
    api_key, secret_key:
        Live Alpaca credentials.

    Returns
    -------
    alpaca.trading.client.TradingClient
        Configured with ``paper=False``.  The optional
        ``ALPACA_LIVE_BASE_URL`` env var overrides the default endpoint.
    """
    from alpaca.trading.client import TradingClient
    url_override = os.environ.get("ALPACA_LIVE_BASE_URL") or None
    return TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=False,
        url_override=url_override,
    )


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get *key* from a dict or SDK object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _str(obj: Any, key: str, default: str = "") -> str:
    v = _get(obj, key, default)
    return str(v).strip() if v is not None else default


def _bool(obj: Any, key: str, default: bool = False) -> bool:
    v = _get(obj, key, default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _result(label: str, status: str, detail: str = "") -> dict[str, Any]:
    return {"label": label, "status": status, "detail": detail}


def check_credentials() -> tuple[dict[str, Any], tuple[str, str] | None]:
    """Resolve live credentials.  Returns (result, (key, secret) | None)."""
    try:
        creds = _resolve_live_credentials()
        return _result("credentials", "PASS", "ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY present"), creds
    except RuntimeError as exc:
        return _result("credentials", "FAIL", str(exc)), None


def check_account(client: Any) -> dict[str, Any]:
    """Read account state and verify it is active and unblocked.

    Returns PASS, WARN, or FAIL.  Never mutates the account.
    """
    try:
        raw = client.get_account()
    except Exception as exc:
        return _result("account", "FAIL", f"get_account() failed — {exc}")

    status          = _str(raw, "status", "unknown").lower()
    trading_blocked = _bool(raw, "trading_blocked")
    account_blocked = _bool(raw, "account_blocked")
    buying_power    = _str(raw, "buying_power", "unknown")
    portfolio_value = _str(raw, "portfolio_value", "unknown")

    issues: list[str] = []
    if status != "active":
        issues.append(f"account status={status!r} (expected 'active')")
    if trading_blocked:
        issues.append("trading_blocked=true")
    if account_blocked:
        issues.append("account_blocked=true")

    detail = (
        f"status={status} trading_blocked={trading_blocked} "
        f"account_blocked={account_blocked} "
        f"buying_power={buying_power} portfolio_value={portfolio_value}"
    )

    if issues:
        return _result("account", "FAIL", detail) | {"issues": issues,
            "account_status": status, "trading_blocked": trading_blocked,
            "account_blocked": account_blocked,
            "buying_power": buying_power, "portfolio_value": portfolio_value}

    return _result("account", "PASS", detail) | {
        "account_status": status, "trading_blocked": trading_blocked,
        "account_blocked": account_blocked,
        "buying_power": buying_power, "portfolio_value": portfolio_value,
    }


def check_positions(client: Any) -> dict[str, Any]:
    """Read open positions.  Returns PASS (none) or WARN (any found)."""
    try:
        if hasattr(client, "get_all_positions"):
            raw_list = client.get_all_positions()
        elif hasattr(client, "get_positions"):
            raw_list = client.get_positions()
        else:
            return _result("positions", "FAIL",
                           "client exposes neither get_all_positions nor get_positions")
    except Exception as exc:
        return _result("positions", "FAIL", f"position lookup failed — {exc}")

    if raw_list is None:
        raw_list = []

    count = len(raw_list)
    symbols = [_str(p, "symbol") for p in raw_list if _str(p, "symbol")]

    if count == 0:
        return _result("positions", "PASS", "no open positions") | {"position_count": 0, "symbols": []}

    detail = f"{count} open position(s): {symbols}"
    return _result("positions", "WARN", detail) | {"position_count": count, "symbols": symbols}


def check_orders(client: Any) -> dict[str, Any]:
    """Read open orders.  Returns PASS (none) or WARN (any found)."""
    try:
        if hasattr(client, "get_orders"):
            raw_list = client.get_orders()
        elif hasattr(client, "list_orders"):
            raw_list = client.list_orders()
        else:
            return _result("orders", "FAIL",
                           "client exposes neither get_orders nor list_orders")
    except Exception as exc:
        return _result("orders", "FAIL", f"order lookup failed — {exc}")

    if raw_list is None:
        raw_list = []

    count = len(raw_list)
    if count == 0:
        return _result("orders", "PASS", "no open orders") | {"open_order_count": 0}

    detail = f"{count} open order(s) found — review before live trading"
    return _result("orders", "WARN", detail) | {"open_order_count": count}


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(
    checks: list[dict[str, Any]],
    final_status: str,
) -> None:
    """Print a human-readable live account status report."""
    _ICON = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    print("\n=== Live Account Check ===")

    # Print account fields prominently
    for c in checks:
        if c["label"] == "account" and c["status"] != "FAIL":
            print(f"  account_status    : {c.get('account_status', 'unknown')}")
            print(f"  trading_blocked   : {c.get('trading_blocked', '?')}")
            print(f"  account_blocked   : {c.get('account_blocked', '?')}")
            print(f"  buying_power      : {c.get('buying_power', '?')}")
            print(f"  portfolio_value   : {c.get('portfolio_value', '?')}")
        if c["label"] == "positions":
            print(f"  position_count    : {c.get('position_count', '?')}")
        if c["label"] == "orders":
            print(f"  open_order_count  : {c.get('open_order_count', '?')}")

    print()
    for c in checks:
        icon   = _ICON.get(c["status"], f"[{c['status']}]")
        detail = f"  ({c['detail']})" if c.get("detail") else ""
        print(f"  {icon} {c['label']}{detail}")
        if c.get("issues"):
            for issue in c["issues"]:
                print(f"         ! {issue}")

    print()
    print(f"  RESULT: {final_status}")
    print("=" * 26)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    # This tool has no flags beyond --help; argv is accepted for testability.
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_account_check",
        description=(
            "Read-only live Alpaca account check. "
            "Never submits or cancels orders. "
            "Requires ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY."
        ),
    )
    parser.parse_args(argv)  # handles --help; no other flags

    checks: list[dict[str, Any]] = []
    all_statuses: list[str] = []

    # --- 1. Credentials ---
    cred_result, creds = check_credentials()
    checks.append(cred_result)
    all_statuses.append(cred_result["status"])

    client = None
    if creds is not None:
        # --- 2. Client creation ---
        try:
            client = _make_live_client(*creds)
        except Exception as exc:
            checks.append(_result("client", "FAIL", f"TradingClient creation failed — {exc}"))
            all_statuses.append("FAIL")

    if client is not None:
        # --- 3. Account ---
        acct_result = check_account(client)
        checks.append(acct_result)
        all_statuses.append(acct_result["status"])

        # --- 4. Positions ---
        pos_result = check_positions(client)
        checks.append(pos_result)
        all_statuses.append(pos_result["status"])

        # --- 5. Orders ---
        ord_result = check_orders(client)
        checks.append(ord_result)
        all_statuses.append(ord_result["status"])

    # --- Final status ---
    if "FAIL" in all_statuses:
        final_status = "FAIL"
    elif "WARN" in all_statuses:
        final_status = "WARN"
    else:
        final_status = "PASS"

    print_report(checks, final_status=final_status)

    if final_status != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
