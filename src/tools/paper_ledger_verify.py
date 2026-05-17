"""
tools/paper_ledger_verify.py
-----------------------------
Read-only paper ledger verification CLI.

Usage::

    python -m src.tools.paper_ledger_verify \\
        --ledger output/paper_execution_ledger.csv \\
        --output output/paper_ledger_verification.csv

Optional: update ledger status column in-place after verification::

    python -m src.tools.paper_ledger_verify \\
        --ledger output/paper_execution_ledger.csv \\
        --output output/paper_ledger_verification.csv \\
        --update-ledger-status

What it does
------------
1. Reads ``paper_execution_ledger.csv``.
2. For every row that has an ``alpaca_order_id``, calls Alpaca's read-only
   ``get_order_by_id`` endpoint.
3. Writes a verification CSV with one row per ledger entry showing whether
   the local ledger status matches what Alpaca currently reports.
4. With ``--update-ledger-status`` the ledger ``status`` column is rewritten
   in-place for rows where a definitive Alpaca status was retrieved.

What it never does
------------------
* Never calls submit_order.
* Never calls cancel_order.
* Never modifies positions.
* Never touches the ledger (unless ``--update-ledger-status`` is given).
* Fails clearly before any network call if credentials are missing.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Verification CSV columns
# ---------------------------------------------------------------------------

_VERIFY_COLUMNS = [
    "client_order_id",
    "alpaca_order_id",
    "ledger_status",
    "alpaca_status",
    "symbol",
    "side",
    "quantity",
    "status_match",
    "checked_at_utc",
    "error",
]


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _resolve_credentials() -> tuple[str, str]:
    """Return ``(api_key, secret_key)`` from env vars, or raise.

    Raises
    ------
    RuntimeError
        If either ``ALPACA_API_KEY`` or ``ALPACA_SECRET_KEY`` is missing or
        empty.  The error is raised *before* any network call.
    """
    key    = os.environ.get("ALPACA_API_KEY",    "").strip()
    secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError(
            "Missing Alpaca paper API credentials. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY before running."
        )
    return key, secret


# ---------------------------------------------------------------------------
# Minimal read-only Alpaca client
# ---------------------------------------------------------------------------

def _make_client(api_key: str, secret_key: str) -> Any:
    """Create a paper ``TradingClient`` for read-only order lookups.

    Parameters
    ----------
    api_key, secret_key:
        Alpaca paper credentials.

    Returns
    -------
    alpaca.trading.client.TradingClient
        Configured with ``paper=True``.  The optional
        ``ALPACA_PAPER_BASE_URL`` env var overrides the default endpoint.
    """
    from alpaca.trading.client import TradingClient
    url_override = os.environ.get("ALPACA_PAPER_BASE_URL") or None
    return TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=True,
        url_override=url_override,
    )


def _lookup_order(client: Any, alpaca_order_id: str) -> dict[str, Any]:
    """Return a dict with ``status``, ``symbol``, ``side``, ``qty`` from Alpaca.

    Parameters
    ----------
    client:
        A ``TradingClient`` (real SDK) or a test double that exposes
        ``get_order_by_id``.
    alpaca_order_id:
        The Alpaca-assigned UUID for the order.

    Returns
    -------
    dict
        Keys: ``"status"``, ``"symbol"``, ``"side"``, ``"qty"``, ``"error"``.
        On success ``"error"`` is ``""``.  On failure ``"status"`` is
        ``""`` and ``"error"`` contains the exception message.
    """
    try:
        response = client.get_order_by_id(alpaca_order_id)
        raw = _response_get(response, "status", "")
        status = _normalize_alpaca_status(raw)
        return {
            "status": status,
            "symbol": str(_response_get(response, "symbol", "") or ""),
            "side":   _normalize_side(_response_get(response, "side", "")),
            "qty":    str(_response_get(response, "qty", "") or ""),
            "error":  "",
        }
    except Exception as exc:
        return {"status": "", "symbol": "", "side": "", "qty": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# Response helpers (same logic as AlpacaBrokerAdapter)
# ---------------------------------------------------------------------------

def _response_get(response: Any, key: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(key, default)
    return getattr(response, key, default)


def _normalize_alpaca_status(raw: Any) -> str:
    """Map an Alpaca status (str or SDK enum) to a canonical string.

    Returns one of ``"accepted"``, ``"filled"``, ``"cancelled"``,
    ``"rejected"``, or the raw lowercased value for unrecognised statuses.
    """
    s = str(raw).strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    s = s.lower()
    if s in {"new", "pending_new", "accepted", "accepted_for_bidding"}:
        return "accepted"
    if s in {"filled", "partially_filled"}:
        return "filled"
    if s in {"canceled", "cancelled", "expired", "replaced"}:
        return "cancelled"
    if s in {"rejected", "stopped", "suspended", "calculated"}:
        return "rejected"
    return s  # pass through unknown — do not raise in a verify-only tool


def _normalize_side(raw: Any) -> str:
    s = str(raw).strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


# ---------------------------------------------------------------------------
# Core verification logic
# ---------------------------------------------------------------------------

def verify_ledger(
    ledger_path: Path,
    client: Any,
) -> list[dict[str, Any]]:
    """Verify each ledger row against Alpaca.

    Parameters
    ----------
    ledger_path:
        Path to the paper execution ledger CSV.
    client:
        Read-only Alpaca client exposing ``get_order_by_id``.

    Returns
    -------
    list[dict]
        One verification row per ledger entry (including rows without an
        ``alpaca_order_id``, which are recorded with ``alpaca_status=""``
        and ``error="no alpaca_order_id"``).
    """
    from src.execution.paper_ledger import load_ledger

    df = load_ledger(ledger_path)
    checked_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    for _, entry in df.fillna("").iterrows():
        coid          = str(entry.get("client_order_id", "")).strip()
        alpaca_id     = str(entry.get("alpaca_order_id", "")).strip()
        ledger_status = str(entry.get("status", "")).strip()
        symbol        = str(entry.get("symbol", "")).strip()
        side          = str(entry.get("side", "")).strip()
        quantity      = str(entry.get("quantity", "")).strip()

        if not alpaca_id:
            rows.append({
                "client_order_id": coid,
                "alpaca_order_id": alpaca_id,
                "ledger_status":   ledger_status,
                "alpaca_status":   "",
                "symbol":          symbol,
                "side":            side,
                "quantity":        quantity,
                "status_match":    False,
                "checked_at_utc":  checked_at,
                "error":           "no alpaca_order_id",
            })
            continue

        result = _lookup_order(client, alpaca_id)
        alpaca_status = result["status"]
        error         = result["error"]
        status_match  = (alpaca_status == ledger_status) if not error else False

        rows.append({
            "client_order_id": coid,
            "alpaca_order_id": alpaca_id,
            "ledger_status":   ledger_status,
            "alpaca_status":   alpaca_status,
            "symbol":          symbol or result["symbol"],
            "side":            side   or result["side"],
            "quantity":        quantity or result["qty"],
            "status_match":    status_match,
            "checked_at_utc":  checked_at,
            "error":           error,
        })

    return rows


def write_verification_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    """Write verification rows to *output_path* as UTF-8 CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=_VERIFY_COLUMNS)
    df.to_csv(output_path, index=False, encoding="utf-8")


def update_ledger_status(
    ledger_path: Path,
    verify_rows: list[dict[str, Any]],
) -> int:
    """Rewrite ledger ``status`` column using verified Alpaca statuses.

    Only updates rows where ``alpaca_status`` is non-empty and no error
    occurred.  Returns the number of rows updated.
    """
    from src.execution.paper_ledger import load_ledger, _LEDGER_COLUMNS

    df = load_ledger(ledger_path)
    if df.empty:
        return 0

    by_coid = {
        r["client_order_id"]: r
        for r in verify_rows
        if r.get("alpaca_status") and not r.get("error")
    }

    updated = 0
    for idx, row in df.iterrows():
        coid = str(row.get("client_order_id", "")).strip()
        if coid in by_coid:
            new_status = by_coid[coid]["alpaca_status"]
            if df.at[idx, "status"] != new_status:
                df.at[idx, "status"] = new_status
                updated += 1

    if updated:
        df.to_csv(ledger_path, index=False, encoding="utf-8")

    return updated


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.paper_ledger_verify",
        description=(
            "Read-only paper ledger verifier. "
            "Checks each alpaca_order_id against Alpaca paper account. "
            "Never submits or cancels orders."
        ),
    )
    parser.add_argument(
        "--ledger",
        required=True,
        help="Path to the paper execution ledger CSV",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the verification CSV",
    )
    parser.add_argument(
        "--update-ledger-status",
        action="store_true",
        default=False,
        help="Update ledger status column in-place with verified Alpaca statuses",
    )
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)
    output_path = Path(args.output)

    print("\n=== Paper Ledger Verify ===")
    print(f"  ledger : {ledger_path}")
    print(f"  output : {output_path}")
    if args.update_ledger_status:
        print("  mode   : verify + update ledger status")
    else:
        print("  mode   : read-only verify")
    print()

    # Fail before any network call if credentials are missing
    try:
        api_key, secret_key = _resolve_credentials()
    except RuntimeError as exc:
        print(f"  [FAIL] {exc}")
        sys.exit(1)

    if not ledger_path.exists():
        print(f"  [FAIL] ledger not found: {ledger_path}")
        sys.exit(1)

    client = _make_client(api_key, secret_key)

    verify_rows = verify_ledger(ledger_path, client)
    write_verification_csv(verify_rows, output_path)

    total      = len(verify_rows)
    matched    = sum(1 for r in verify_rows if r["status_match"])
    mismatched = sum(1 for r in verify_rows if not r["status_match"] and not r["error"])
    errors     = sum(1 for r in verify_rows if r["error"])
    skipped    = sum(1 for r in verify_rows if r["error"] == "no alpaca_order_id")

    print(f"  {total} row(s) checked:")
    print(f"    matched    : {matched}")
    print(f"    mismatched : {mismatched}")
    print(f"    errors     : {errors - skipped}")
    print(f"    skipped (no alpaca_order_id): {skipped}")
    print(f"\n  verification written → {output_path}")

    if args.update_ledger_status:
        n = update_ledger_status(ledger_path, verify_rows)
        print(f"  ledger status updated for {n} row(s)")

    print("=" * 28)

    if mismatched or (errors - skipped):
        sys.exit(1)


if __name__ == "__main__":
    main()
