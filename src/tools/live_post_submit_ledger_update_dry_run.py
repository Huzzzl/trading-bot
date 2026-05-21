"""
tools/live_post_submit_ledger_update_dry_run.py
-------------------------------------------------
Offline post-submit ledger update dry-run tool.

Proves the live ledger can update the same pre-submit row after a
hypothetical submit outcome.  Never calls ``submit_order`` or any broker
endpoint.

Usage::

    python -m src.tools.live_post_submit_ledger_update_dry_run \\
        --ledger output/live_submit_ledger.csv \\
        --client-order-id LIVE-TEST-000001 \\
        --outcome submitted \\
        --broker-order-id ALPACA-ORDER-123 \\
        --output output/live_post_submit_ledger_update_dry_run.json

    python -m src.tools.live_post_submit_ledger_update_dry_run \\
        --ledger output/live_submit_ledger.csv \\
        --client-order-id LIVE-TEST-000001 \\
        --outcome rejected \\
        --error "order rejected by broker" \\
        --output output/live_post_submit_ledger_update_dry_run.json

Purpose
-------
Proves that the same pre-submit ledger row (written by
``live_pre_submit_ledger_dry_run``) can be updated with a final outcome
without submitting any real order.

Future real submit must update the same ``client_order_id`` row with the
actual ``broker_order_id``, ``error``, and ``outcome`` fields after the order
attempt completes.

Valid outcomes
--------------
``submitted``
    Order was accepted by the broker.  ``broker_order_id`` required.

``rejected``
    Order was rejected by the broker.  ``error`` required.

``exception``
    An exception was raised during or after submission.  ``error`` required.

Result
------
``LEDGER_DRY_RUN_UPDATED``
    All checks passed; the matching row was updated in place.  Exit code 0.

``BLOCKED``
    A required precondition failed; the ledger is not modified.  Exit code 1.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never implements real submit.
* Never removes config_safety.
* Never modifies config defaults.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.live_pre_submit_ledger_dry_run import LEDGER_COLUMNS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_OUTCOMES: frozenset[str] = frozenset({"submitted", "rejected", "exception"})
_TERMINAL_STATUSES: frozenset[str] = frozenset({"submitted", "rejected", "exception"})


# ---------------------------------------------------------------------------
# Ledger reader
# ---------------------------------------------------------------------------

def _read_ledger(path: Path) -> list[dict[str, str]]:
    """Read all rows from the ledger CSV and validate the schema.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file is malformed or the header does not match LEDGER_COLUMNS.
    """
    if not path.exists():
        raise FileNotFoundError(f"ledger not found: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if list(reader.fieldnames or []) != LEDGER_COLUMNS:
                raise ValueError(
                    f"ledger schema mismatch: expected {LEDGER_COLUMNS}, "
                    f"got {list(reader.fieldnames or [])}"
                )
            return [dict(row) for row in reader]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"could not read ledger {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Ledger rewriter
# ---------------------------------------------------------------------------

def _rewrite_ledger(path: Path, rows: list[dict[str, str]]) -> None:
    """Overwrite the ledger CSV with exactly the given rows."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in LEDGER_COLUMNS})


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_update(
    *,
    ledger_path: Path,
    client_order_id: str,
    outcome: str,
    broker_order_id: str,
    error: str,
) -> dict[str, Any]:
    """Locate the pre-submit row and update it with the given outcome.

    Returns the result dict.  Never raises.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    ledger_updated = False

    # ------------------------------------------------------------------
    # Step 1: validate inputs
    # ------------------------------------------------------------------
    if not client_order_id or not client_order_id.strip():
        violations.append("client_order_id is empty")

    if outcome not in _VALID_OUTCOMES:
        violations.append(
            f"outcome={outcome!r} -- must be one of "
            f"{sorted(_VALID_OUTCOMES)}"
        )
    else:
        if outcome == "submitted" and not broker_order_id.strip():
            violations.append(
                "broker_order_id is required when outcome='submitted'"
            )
        if outcome in ("rejected", "exception") and not error.strip():
            violations.append(
                f"error is required when outcome={outcome!r}"
            )

    # ------------------------------------------------------------------
    # Step 2: read ledger
    # ------------------------------------------------------------------
    rows: list[dict[str, str]] = []
    if not violations:
        try:
            rows = _read_ledger(ledger_path)
        except (FileNotFoundError, ValueError) as exc:
            violations.append(f"ledger: {exc}")

    # ------------------------------------------------------------------
    # Step 3: locate the target row
    # ------------------------------------------------------------------
    matching_indices: list[int] = []
    if not violations:
        matching_indices = [
            i for i, r in enumerate(rows)
            if r.get("client_order_id", "") == client_order_id
        ]
        if len(matching_indices) == 0:
            violations.append(
                f"client_order_id={client_order_id!r} not found in ledger"
            )
        elif len(matching_indices) > 1:
            violations.append(
                f"client_order_id={client_order_id!r} found in "
                f"{len(matching_indices)} rows -- expected exactly one"
            )

    # ------------------------------------------------------------------
    # Step 4: require status == "attempting"
    # ------------------------------------------------------------------
    if not violations and matching_indices:
        idx = matching_indices[0]
        current_status = rows[idx].get("status", "")
        if current_status != "attempting":
            violations.append(
                f"row status={current_status!r} -- must be 'attempting' "
                "to accept a post-submit update"
            )

    # ------------------------------------------------------------------
    # Step 5: apply update
    # ------------------------------------------------------------------
    if not violations and matching_indices:
        idx = matching_indices[0]
        rows[idx]["status"] = outcome
        rows[idx]["broker_order_id"] = broker_order_id.strip()
        rows[idx]["error"] = error.strip()
        _rewrite_ledger(ledger_path, rows)
        ledger_updated = True

    result_str = "LEDGER_DRY_RUN_UPDATED" if ledger_updated else "BLOCKED"

    return {
        "checked_at_utc":  checked_at,
        "result":          result_str,
        "ledger_updated":  ledger_updated,
        "ledger_path":     str(ledger_path),
        "client_order_id": client_order_id,
        "outcome":         outcome,
        "broker_order_id": broker_order_id,
        "error":           error,
        "violations":      violations,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_result(result: dict[str, Any]) -> None:
    print("\n=== Live Post-Submit Ledger Update Dry-Run ===")
    print(f"  checked_at_utc : {result['checked_at_utc']}")
    print(f"  result         : {result['result']}")
    print(f"  ledger_updated : {result['ledger_updated']}")
    print(f"  ledger_path    : {result['ledger_path']}")
    print(f"  client_order_id: {result['client_order_id']}")
    print(f"  outcome        : {result['outcome']}")
    print(f"  broker_order_id: {result['broker_order_id']}")
    print(f"  error          : {result['error']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    print("=" * 46)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_post_submit_ledger_update_dry_run",
        description=(
            "Offline post-submit ledger update dry-run tool. "
            "Locates an existing 'attempting' ledger row and updates it with "
            "a hypothetical submit outcome. "
            "Never calls submit_order. Never calls Alpaca. Never reads credentials."
        ),
    )
    parser.add_argument(
        "--ledger", required=True,
        help="Path to live_submit_ledger.csv",
    )
    parser.add_argument(
        "--client-order-id", required=True, dest="client_order_id",
        help="client_order_id of the row to update",
    )
    parser.add_argument(
        "--outcome", required=True,
        help=(
            "Hypothetical submit outcome. "
            f"Valid values: {', '.join(sorted(_VALID_OUTCOMES))}. "
            "Invalid values are rejected by run_update() and produce result='BLOCKED'."
        ),
    )
    parser.add_argument(
        "--broker-order-id", default="", dest="broker_order_id",
        help="Broker-assigned order ID (required for outcome=submitted)",
    )
    parser.add_argument(
        "--error", default="",
        help="Error message (required for outcome=rejected or exception)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write live_post_submit_ledger_update_dry_run.json",
    )
    args = parser.parse_args(argv)

    result = run_update(
        ledger_path=Path(args.ledger),
        client_order_id=args.client_order_id,
        outcome=args.outcome,
        broker_order_id=args.broker_order_id,
        error=args.error,
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_result(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "LEDGER_DRY_RUN_UPDATED":
        sys.exit(1)


if __name__ == "__main__":
    main()
