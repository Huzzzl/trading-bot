"""
tools/live_ledger_verify.py
-----------------------------
Read-only live execution ledger schema validator.

Usage::

    python -m src.tools.live_ledger_verify \\
        --ledger output/live_execution_ledger.csv

Goal
----
Validate the structure and safety invariants of the live execution ledger CSV
before any live order submission path is built.  This is a schema and
constraint check only — it never calls Alpaca, never reads credentials,
and never submits or cancels orders.

Result
------
PASS — ledger is absent (not yet created) or all rows pass all checks.
WARN — non-fatal issues found (e.g. alpaca_order_id missing for dry-run rows).
FAIL — a hard safety constraint is violated.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes any file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

from src.execution.live_ledger import LIVE_LEDGER_COLUMNS, VALID_SIDES, VALID_ORDER_TYPES


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_ledger(path: Path) -> tuple[list[dict[str, Any]] | None, str]:
    """Read the ledger CSV, returning (rows, message).

    Returns
    -------
    (None, message)
        When the file does not exist — caller treats as PASS with the message.
    (rows, "")
        When the file exists and can be parsed.
    (None, error_message)
        When the file exists but cannot be parsed — caller treats as FAIL.
    """
    if not path.exists():
        return None, f"no live ledger exists yet at {path}"

    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:
        return None, f"could not read ledger: {exc}"

    return df.fillna("").to_dict("records"), ""


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    """Return True for Python True, 'True', 'true', '1', 1, etc."""
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in ("true", "1", "yes")


def validate_ledger(rows: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """Validate ledger rows.

    Parameters
    ----------
    rows:
        List of row dicts (already filled-NA to empty strings).

    Returns
    -------
    (result, errors, warnings)
        result  : "PASS", "WARN", or "FAIL"
        errors  : hard constraint violations → FAIL
        warnings: non-fatal issues → WARN (only if no errors)
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not rows:
        return "PASS", [], []

    # --- Required columns present ---
    sample = rows[0]
    missing_cols = [c for c in LIVE_LEDGER_COLUMNS if c not in sample]
    if missing_cols:
        errors.append(f"missing required column(s): {missing_cols}")
        return "FAIL", errors, warnings

    for i, row in enumerate(rows, start=1):
        label = f"row {i}"

        dry_run_only  = _is_truthy(row.get("dry_run_only", ""))
        submit_allowed = _is_truthy(row.get("submit_allowed", ""))

        # submit_allowed truthy but dry_run_only also truthy — contradictory
        if submit_allowed and dry_run_only:
            errors.append(
                f"{label}: submit_allowed=true but dry_run_only=true — "
                "contradictory safety flags"
            )

        # Missing client_order_id on any live row
        coid = str(row.get("client_order_id", "")).strip()
        if not coid:
            errors.append(f"{label}: client_order_id is empty")

        # Empty status
        status = str(row.get("status", "")).strip()
        if not status:
            errors.append(f"{label}: status is empty")

        # Invalid side
        side = str(row.get("side", "")).strip().lower()
        if side and side not in VALID_SIDES:
            errors.append(
                f"{label}: invalid side={side!r} (expected one of {sorted(VALID_SIDES)})"
            )

        # Invalid order_type
        order_type = str(row.get("order_type", "")).strip().lower()
        if order_type and order_type not in VALID_ORDER_TYPES:
            errors.append(
                f"{label}: invalid order_type={order_type!r} "
                f"(expected one of {sorted(VALID_ORDER_TYPES)})"
            )

        # WARN: alpaca_order_id missing for dry-run rows
        alpaca_id = str(row.get("alpaca_order_id", "")).strip()
        if dry_run_only and not alpaca_id:
            warnings.append(
                f"{label}: alpaca_order_id is empty for a dry-run row "
                "(expected for dry-run intent records)"
            )

    if errors:
        return "FAIL", errors, warnings
    if warnings:
        return "WARN", [], warnings
    return "PASS", [], []


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_result(
    result: str,
    errors: list[str],
    warnings: list[str],
    path: Path,
    missing_message: str,
    row_count: int | None,
) -> None:
    print("\n=== Live Ledger Verify ===")
    print(f"  ledger : {path}")
    if row_count is not None:
        print(f"  rows   : {row_count}")
    print()
    if missing_message:
        print(f"  ~ {missing_message}")
        print()
    for err in errors:
        print(f"  ! {err}")
    for warn in warnings:
        print(f"  ~ {warn}")
    if errors or warnings:
        print()
    print(f"  RESULT: {result}")
    print("=" * 26)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_ledger_verify",
        description=(
            "Read-only live ledger schema validator. "
            "Checks required columns and safety invariants. "
            "No credentials required. Never calls Alpaca."
        ),
    )
    parser.add_argument("--ledger", required=True, help="Path to live ledger CSV")
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)
    rows, missing_message = parse_ledger(ledger_path)

    if rows is None:
        # Either file missing (PASS) or unreadable (FAIL)
        if "could not read" in missing_message:
            print_result("FAIL", [missing_message], [], ledger_path, "", None)
            sys.exit(1)
        else:
            print_result("PASS", [], [], ledger_path, missing_message, None)
            return

    result, errors, warnings = validate_ledger(rows)
    print_result(result, errors, warnings, ledger_path, "", len(rows))

    if result == "FAIL":
        sys.exit(1)
    if result == "WARN":
        sys.exit(1)


if __name__ == "__main__":
    main()
