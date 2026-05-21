"""
tools/live_pre_submit_ledger_dry_run.py
-----------------------------------------
Offline pre-submit ledger dry-run writer.

Validates intended live order metadata and appends one pre-submit ledger row
with ``status="attempting"``.  Never calls ``submit_order`` or any broker
endpoint.

Usage::

    python -m src.tools.live_pre_submit_ledger_dry_run \\
        --enablement-gate output/live_submit_enablement_gate.json \\
        --symbol SPY \\
        --side buy \\
        --notional 100.0 \\
        --client-order-id LIVE-TEST-000001 \\
        --ledger output/live_submit_ledger.csv \\
        --output output/live_pre_submit_ledger_dry_run.json

Purpose
-------
Proves that the required pre-submit ledger row can be written without
executing a real order.  Future real submit must reuse the same ledger
schema and update the same ``client_order_id`` row after the order attempt.

Result
------
``LEDGER_DRY_RUN_WRITTEN``
    All checks passed; one row appended to the ledger CSV with
    ``status="attempting"``.  Exit code 0.

``BLOCKED``
    A required precondition failed; no ledger row was written.  Exit code 1.

Ledger CSV columns
------------------
timestamp_utc, client_order_id, symbol, side, notional, status,
source_enablement_gate, broker_order_id, error

``broker_order_id`` and ``error`` are blank for the pre-submit row.

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


# ---------------------------------------------------------------------------
# Ledger schema
# ---------------------------------------------------------------------------

LEDGER_COLUMNS: list[str] = [
    "timestamp_utc",
    "client_order_id",
    "symbol",
    "side",
    "notional",
    "status",
    "source_enablement_gate",
    "broker_order_id",
    "error",
]


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _read_gate(path: Path) -> dict[str, Any]:
    """Read and return the enablement gate JSON.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file contains malformed JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"enablement gate artifact not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def _read_ledger_ids(path: Path) -> set[str]:
    """Return the set of client_order_ids already present in the ledger CSV.

    Returns an empty set when the ledger does not yet exist.

    Raises
    ------
    ValueError
        When the file exists but cannot be read as CSV.
    """
    if not path.exists():
        return set()
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            return {row.get("client_order_id", "") for row in reader}
    except Exception as exc:
        raise ValueError(f"could not read ledger {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_order(
    *,
    symbol: str,
    side: str,
    notional: float,
    client_order_id: str,
    existing_ids: set[str],
) -> list[str]:
    violations: list[str] = []

    if not symbol or not symbol.strip():
        violations.append("symbol is empty")

    if side != "buy":
        violations.append(
            f"side={side!r} -- only 'buy' is permitted for pre-submit dry-run"
        )

    if notional <= 0:
        violations.append(
            f"notional={notional} -- must be > 0"
        )

    if not client_order_id or not client_order_id.strip():
        violations.append("client_order_id is empty")
    elif client_order_id in existing_ids:
        violations.append(
            f"client_order_id={client_order_id!r} already present in ledger -- "
            "idempotency check failed"
        )

    return violations


# ---------------------------------------------------------------------------
# Ledger writer
# ---------------------------------------------------------------------------

def _append_ledger_row(
    path: Path,
    *,
    timestamp_utc: str,
    client_order_id: str,
    symbol: str,
    side: str,
    notional: float,
    source_enablement_gate: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()

    row = {
        "timestamp_utc":        timestamp_utc,
        "client_order_id":      client_order_id,
        "symbol":               symbol.strip().upper(),
        "side":                 side,
        "notional":             str(notional),
        "status":               "attempting",
        "source_enablement_gate": source_enablement_gate,
        "broker_order_id":      "",
        "error":                "",
    }

    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_dry_run(
    *,
    gate_path: Path,
    symbol: str,
    side: str,
    notional: float,
    client_order_id: str,
    ledger_path: Path,
) -> dict[str, Any]:
    """Validate inputs and write one pre-submit ledger row if all checks pass.

    Returns the result dict.  Never raises.
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    ledger_written = False

    # ------------------------------------------------------------------
    # Step 1: read enablement gate
    # ------------------------------------------------------------------
    gate: dict[str, Any] | None = None
    try:
        gate = _read_gate(gate_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(f"enablement gate: {exc}")

    if gate is not None:
        decision = str(gate.get("decision", "")).strip()
        if decision != "GO":
            violations.append(
                f"enablement gate decision={decision!r} -- must be 'GO'"
            )

    # ------------------------------------------------------------------
    # Step 2: read existing ledger IDs (idempotency check)
    # ------------------------------------------------------------------
    existing_ids: set[str] = set()
    try:
        existing_ids = _read_ledger_ids(ledger_path)
    except ValueError as exc:
        violations.append(f"ledger read error: {exc}")

    # ------------------------------------------------------------------
    # Step 3: validate order fields
    # ------------------------------------------------------------------
    order_violations = _validate_order(
        symbol=symbol,
        side=side,
        notional=notional,
        client_order_id=client_order_id,
        existing_ids=existing_ids,
    )
    violations.extend(order_violations)

    # ------------------------------------------------------------------
    # Step 4: write ledger row if all checks passed
    # ------------------------------------------------------------------
    if not violations:
        _append_ledger_row(
            ledger_path,
            timestamp_utc=checked_at,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            notional=notional,
            source_enablement_gate=str(gate_path),
        )
        ledger_written = True

    result_str = "LEDGER_DRY_RUN_WRITTEN" if ledger_written else "BLOCKED"

    return {
        "checked_at_utc":  checked_at,
        "result":          result_str,
        "ledger_written":  ledger_written,
        "ledger_path":     str(ledger_path),
        "client_order_id": client_order_id,
        "symbol":          symbol,
        "side":            side,
        "notional":        notional,
        "violations":      violations,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_result(result: dict[str, Any]) -> None:
    print("\n=== Live Pre-Submit Ledger Dry-Run ===")
    print(f"  checked_at_utc : {result['checked_at_utc']}")
    print(f"  result         : {result['result']}")
    print(f"  ledger_written : {result['ledger_written']}")
    print(f"  ledger_path    : {result['ledger_path']}")
    print(f"  client_order_id: {result['client_order_id']}")
    print(f"  symbol         : {result['symbol']}")
    print(f"  side           : {result['side']}")
    print(f"  notional       : {result['notional']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    print("=" * 38)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_pre_submit_ledger_dry_run",
        description=(
            "Offline pre-submit ledger dry-run writer. "
            "Validates live order metadata and writes one pre-submit ledger row "
            "with status='attempting'. "
            "Never calls submit_order. Never calls Alpaca. Never reads credentials."
        ),
    )
    parser.add_argument(
        "--enablement-gate", required=True, dest="enablement_gate",
        help="Path to live_submit_enablement_gate.json",
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True)
    parser.add_argument(
        "--notional", required=True, type=float,
        help="Intended notional value (must be > 0)",
    )
    parser.add_argument(
        "--client-order-id", required=True, dest="client_order_id",
        help="Unique client order identifier",
    )
    parser.add_argument(
        "--ledger", required=True,
        help="Path to live_submit_ledger.csv",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write live_pre_submit_ledger_dry_run.json",
    )
    args = parser.parse_args(argv)

    result = run_dry_run(
        gate_path=Path(args.enablement_gate),
        symbol=args.symbol,
        side=args.side,
        notional=args.notional,
        client_order_id=args.client_order_id,
        ledger_path=Path(args.ledger),
    )

    output_path = Path(args.output)
    _write_json(output_path, result)

    print_result(result)
    print(f"\n  Output written to: {output_path}")

    if result["result"] != "LEDGER_DRY_RUN_WRITTEN":
        sys.exit(1)


if __name__ == "__main__":
    main()
