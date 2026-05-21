"""
tools/live_ledger_verify.py
-----------------------------
Read-only live execution ledger schema validator.

Usage (dry-run submit ledger — primary mode)::

    python -m src.tools.live_ledger_verify \\
        --ledger output/live_submit_ledger.csv \\
        --output output/live_ledger_verify.json

    python -m src.tools.live_ledger_verify \\
        --ledger output/live_submit_ledger.csv \\
        --output output/live_ledger_verify.json \\
        --allow-attempting

Goal
----
Validate the structure and safety invariants of the live submit ledger CSV
after pre- and post-submit dry-run operations.  This is an offline schema and
constraint check only — it never calls Alpaca, never reads credentials,
and never submits or cancels orders.

Result
------
PASS — all rows satisfy every validation rule.
FAIL — one or more validation rules are violated.

Without ``--allow-attempting``, any row with ``status="attempting"`` is a
violation.  Future real submit must leave ``live_ledger_verify`` PASS after
every submit attempt.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never implements real submit.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.execution.live_ledger import LIVE_LEDGER_COLUMNS, VALID_SIDES, VALID_ORDER_TYPES
from src.tools.live_pre_submit_ledger_dry_run import LEDGER_COLUMNS


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
# Submit-ledger validation (dry-run schema, LEDGER_COLUMNS)
# ---------------------------------------------------------------------------

_VALID_STATUSES: frozenset[str] = frozenset({
    "attempting", "submitted", "rejected", "exception",
})


def _read_submit_ledger(path: Path) -> list[dict[str, str]]:
    """Read and schema-validate the submit ledger CSV.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file is malformed or header != LEDGER_COLUMNS.
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


def validate_submit_ledger(
    rows: list[dict[str, str]],
    *,
    allow_attempting: bool = False,
) -> tuple[str, list[str], dict[str, int], list[str]]:
    """Validate rows from the dry-run submit ledger.

    Returns
    -------
    (result, violations, counts, duplicate_ids)
        result      : "PASS" or "FAIL"
        violations  : list of human-readable failure strings
        counts      : dict with attempting/submitted/rejected/exception counts
        duplicate_ids : list of client_order_id values that appear more than once
    """
    violations: list[str] = []
    counts: dict[str, int] = {
        "attempting": 0, "submitted": 0, "rejected": 0, "exception": 0,
    }
    seen_ids: dict[str, int] = {}  # id -> first row index (1-based)
    duplicate_ids: list[str] = []

    for i, row in enumerate(rows, start=1):
        label = f"row {i}"

        client_order_id = str(row.get("client_order_id", "")).strip()
        if not client_order_id:
            violations.append(f"{label}: client_order_id is empty")
        else:
            if client_order_id in seen_ids:
                if client_order_id not in duplicate_ids:
                    duplicate_ids.append(client_order_id)
                violations.append(
                    f"{label}: duplicate client_order_id={client_order_id!r} "
                    f"(first seen at row {seen_ids[client_order_id]})"
                )
            else:
                seen_ids[client_order_id] = i

        status = str(row.get("status", "")).strip()
        if status not in _VALID_STATUSES:
            violations.append(
                f"{label}: invalid status={status!r} "
                f"(must be one of {sorted(_VALID_STATUSES)})"
            )
        else:
            if status in counts:
                counts[status] += 1

            if status == "attempting" and not allow_attempting:
                violations.append(
                    f"{label}: status='attempting' is not allowed without "
                    "--allow-attempting (row has not been updated after submit)"
                )

            broker_order_id = str(row.get("broker_order_id", "")).strip()
            error = str(row.get("error", "")).strip()

            if status == "submitted":
                if not broker_order_id:
                    violations.append(
                        f"{label}: submitted row must have non-empty broker_order_id"
                    )
                if error:
                    violations.append(
                        f"{label}: submitted row must have empty error "
                        f"(got {error!r})"
                    )
            elif status in ("rejected", "exception"):
                if not error:
                    violations.append(
                        f"{label}: {status} row must have non-empty error"
                    )

        source = str(row.get("source_enablement_gate", "")).strip()
        if not source:
            violations.append(f"{label}: source_enablement_gate is empty")

        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            violations.append(f"{label}: symbol is empty")

        side = str(row.get("side", "")).strip()
        if side != "buy":
            violations.append(
                f"{label}: side={side!r} -- only 'buy' is permitted"
            )

        notional_str = str(row.get("notional", "")).strip()
        try:
            notional = float(notional_str)
            if notional <= 0:
                violations.append(
                    f"{label}: notional={notional_str!r} must be > 0"
                )
        except (ValueError, TypeError):
            violations.append(
                f"{label}: notional={notional_str!r} is not a valid number"
            )

    result = "PASS" if not violations else "FAIL"
    return result, violations, counts, duplicate_ids


def run_verify(
    ledger_path: Path,
    *,
    allow_attempting: bool = False,
) -> dict[str, Any]:
    """Run all validation checks and return the result dict.  Never raises."""
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    violations: list[str] = []
    rows: list[dict[str, str]] = []
    counts: dict[str, int] = {
        "attempting": 0, "submitted": 0, "rejected": 0, "exception": 0,
    }
    duplicate_ids: list[str] = []

    try:
        rows = _read_submit_ledger(ledger_path)
    except (FileNotFoundError, ValueError) as exc:
        violations.append(str(exc))

    if not violations:
        result_str, row_violations, counts, duplicate_ids = validate_submit_ledger(
            rows, allow_attempting=allow_attempting
        )
        violations.extend(row_violations)
    else:
        result_str = "FAIL"

    return {
        "checked_at_utc":            checked_at,
        "result":                    result_str,
        "ledger_path":               str(ledger_path),
        "row_count":                 len(rows),
        "attempting_count":          counts["attempting"],
        "submitted_count":           counts["submitted"],
        "rejected_count":            counts["rejected"],
        "exception_count":           counts["exception"],
        "duplicate_client_order_ids": duplicate_ids,
        "violations":                violations,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def print_verify_result(result: dict[str, Any]) -> None:
    print("\n=== Live Ledger Verify ===")
    print(f"  checked_at_utc  : {result['checked_at_utc']}")
    print(f"  ledger_path     : {result['ledger_path']}")
    print(f"  result          : {result['result']}")
    print(f"  row_count       : {result['row_count']}")
    print(f"  attempting_count: {result['attempting_count']}")
    print(f"  submitted_count : {result['submitted_count']}")
    print(f"  rejected_count  : {result['rejected_count']}")
    print(f"  exception_count : {result['exception_count']}")
    if result["duplicate_client_order_ids"]:
        print(f"  duplicates      : {result['duplicate_client_order_ids']}")
    if result["violations"]:
        print("  violations:")
        for v in result["violations"]:
            print(f"    ! {v}")
    print("=" * 26)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_ledger_verify",
        description=(
            "Offline live submit ledger verifier. "
            "Validates the dry-run submit ledger CSV after pre- and post-submit "
            "dry-run operations. "
            "No credentials required. Never calls Alpaca. Never submits orders."
        ),
    )
    parser.add_argument("--ledger", required=True, help="Path to live_submit_ledger.csv")
    parser.add_argument(
        "--output", default=None,
        help=(
            "Path to write live_ledger_verify.json. "
            "When provided, validates the dry-run submit ledger schema "
            "(LEDGER_COLUMNS from live_pre_submit_ledger_dry_run) and always writes "
            "output JSON. When omitted, runs the legacy LIVE_LEDGER_COLUMNS validation."
        ),
    )
    parser.add_argument(
        "--allow-attempting", action="store_true", dest="allow_attempting",
        help=(
            "Allow rows with status='attempting'. "
            "Without this flag, any attempting row is a violation. "
            "Only used when --output is provided."
        ),
    )
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)

    if args.output is not None:
        # New path: dry-run submit ledger validation with JSON output.
        result = run_verify(ledger_path, allow_attempting=args.allow_attempting)
        output_path = Path(args.output)
        _write_json(output_path, result)
        print_verify_result(result)
        print(f"\n  Output written to: {output_path}")
        if result["result"] != "PASS":
            sys.exit(1)
        return

    # Legacy path: LIVE_LEDGER_COLUMNS schema, no output file.
    rows, missing_message = parse_ledger(ledger_path)

    if rows is None:
        if "could not read" in missing_message:
            print_result("FAIL", [missing_message], [], ledger_path, "", None)
            sys.exit(1)
        else:
            print_result("PASS", [], [], ledger_path, missing_message, None)
            return

    result_str, errors, warnings = validate_ledger(rows)
    print_result(result_str, errors, warnings, ledger_path, "", len(rows))

    if result_str == "FAIL":
        sys.exit(1)
    if result_str == "WARN":
        sys.exit(1)


if __name__ == "__main__":
    main()
