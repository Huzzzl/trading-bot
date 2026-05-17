"""
tools/paper_ledger_import.py
-----------------------------
Offline paper ledger backfill / import utility.

Usage::

    # Import from a historical artifact directory
    python -m src.tools.paper_ledger_import \\
        --ledger output/paper_execution_ledger.csv \\
        --from-artifacts output/paper_submit_BT000035

    # Import from a hand-crafted CSV
    python -m src.tools.paper_ledger_import \\
        --ledger output/paper_execution_ledger.csv \\
        --manual-row manual_orders.csv

    # Preview without writing
    python -m src.tools.paper_ledger_import \\
        --ledger output/paper_execution_ledger.csv \\
        --from-artifacts output/paper_submit_BT000035 \\
        --dry-run

    # Skip already-imported entries instead of failing
    python -m src.tools.paper_ledger_import \\
        --ledger output/paper_execution_ledger.csv \\
        --from-artifacts output/paper_submit_BT000035 \\
        --skip-duplicates

What it does
------------
* Reads order_results.csv (and optionally order_intents.csv) from an
  artifact directory and appends one ledger row per result.
* Accepts a manual CSV whose columns are a subset of the ledger columns.
* Deduplicates by client_order_id before writing.
* Supports --dry-run (prints rows, writes nothing).

What it never does
------------------
* Never instantiates AlpacaBrokerAdapter.
* Never reads Alpaca credentials.
* Never calls submit_order or cancel_order.
* Never touches the network.
* Only writes the ledger CSV.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

_SIDE_TO_FLOW = {"buy": "buy_submit", "sell": "close_submit"}


def _normalize_side(value: str) -> str:
    """Strip SDK enum prefix and lowercase the side value.

    Converts strings like ``"OrderSide.BUY"`` or ``"orderside.buy"`` to
    ``"buy"``, and ``"OrderSide.SELL"`` to ``"sell"``.  Plain values such as
    ``"buy"`` or ``"sell"`` pass through unchanged.
    """
    raw = str(value).strip()
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.lower()


def _rows_from_artifacts(artifact_dir: Path) -> list[dict[str, Any]]:
    """Build ledger rows from an artifact directory.

    Reads ``order_results.csv`` (required).  If ``order_intents.csv`` is
    present it is joined on ``client_order_id`` to fill ``symbol`` when
    the results file lacks it.

    Parameters
    ----------
    artifact_dir:
        Directory that was written by a paper submit run.

    Returns
    -------
    list[dict]
        One dict per row in ``order_results.csv``, ready for
        ``append_ledger_row``.

    Raises
    ------
    FileNotFoundError
        If ``order_results.csv`` is absent from *artifact_dir*.
    """
    results_path = artifact_dir / "order_results.csv"
    if not results_path.exists():
        raise FileNotFoundError(
            f"order_results.csv not found in {artifact_dir}. "
            "Cannot import without order results."
        )

    results = pd.read_csv(results_path, dtype=str).fillna("")

    # Optional join with intents to fill symbol gaps
    intents_path = artifact_dir / "order_intents.csv"
    intents: pd.DataFrame | None = None
    if intents_path.exists():
        intents = pd.read_csv(intents_path, dtype=str).fillna("")

    run_id = artifact_dir.name
    output_dir = str(artifact_dir.resolve())

    rows: list[dict[str, Any]] = []
    for _, res in results.iterrows():
        client_order_id = str(res.get("client_order_id", "")).strip()
        alpaca_order_id = (
            str(res.get("alpaca_order_id", "")).strip()
            or str(res.get("order_id", "")).strip()
        )
        side = _normalize_side(res.get("side", ""))
        quantity = str(res.get("quantity", "")).strip()
        status = str(res.get("status", "")).strip()
        submitted_at = str(res.get("submitted_at", "")).strip()
        symbol = str(res.get("symbol", "")).strip()

        # Fill symbol from intents if missing
        if not symbol and intents is not None and client_order_id:
            match = intents[intents["client_order_id"] == client_order_id]
            if not match.empty:
                symbol = str(match.iloc[0].get("symbol", "")).strip()

        flow = _SIDE_TO_FLOW.get(side, "buy_submit")

        rows.append(
            {
                "run_id": run_id,
                "flow": flow,
                "client_order_id": client_order_id,
                "alpaca_order_id": alpaca_order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "status": status,
                "submitted_at": submitted_at,
                "output_dir": output_dir,
                "notes": "backfilled from artifacts",
            }
        )
    return rows


def _rows_from_manual_csv(manual_path: Path) -> list[dict[str, Any]]:
    """Build ledger rows from a hand-crafted CSV.

    The CSV must have at minimum a ``client_order_id`` column.  Any
    ``_LEDGER_COLUMNS`` column that is present will be used.  Missing columns
    are filled with empty strings.  A missing ``flow`` column is inferred from
    ``side`` when possible.

    Parameters
    ----------
    manual_path:
        Path to the manual CSV file.

    Returns
    -------
    list[dict]
        One dict per row in the CSV.

    Raises
    ------
    FileNotFoundError
        If *manual_path* does not exist.
    ValueError
        If the CSV has no ``client_order_id`` column.
    """
    if not manual_path.exists():
        raise FileNotFoundError(f"Manual CSV not found: {manual_path}")

    df = pd.read_csv(manual_path, dtype=str).fillna("")

    if "client_order_id" not in df.columns:
        raise ValueError(
            f"Manual CSV {manual_path} must have a 'client_order_id' column."
        )

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        row: dict[str, Any] = {}
        for col in _LEDGER_COLUMNS_REF():
            row[col] = str(r.get(col, "")).strip()

        # Normalise side (strips SDK enum prefix e.g. "OrderSide.BUY" → "buy")
        row["side"] = _normalize_side(row.get("side", ""))

        # Infer flow from side when flow is absent
        if not row.get("flow"):
            row["flow"] = _SIDE_TO_FLOW.get(row["side"], "buy_submit")

        # Tag as manual if notes is empty
        if not row.get("notes"):
            row["notes"] = "manual import"

        rows.append(row)
    return rows


def _LEDGER_COLUMNS_REF() -> list[str]:
    from src.execution.paper_ledger import _LEDGER_COLUMNS
    return _LEDGER_COLUMNS


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def import_rows(
    ledger_path: Path,
    rows: list[dict[str, Any]],
    *,
    skip_duplicates: bool = False,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Append *rows* to the ledger, with duplicate checking.

    Parameters
    ----------
    ledger_path:
        Destination ledger CSV.
    rows:
        Rows to import (as returned by ``_rows_from_artifacts`` or
        ``_rows_from_manual_csv``).
    skip_duplicates:
        When ``True``, silently skip rows whose ``client_order_id`` already
        exists in the ledger.  When ``False`` (default), raise
        ``RuntimeError`` on the first duplicate.
    dry_run:
        When ``True``, print what would be written but do not write anything.

    Returns
    -------
    (appended, skipped)
        Count of rows appended and rows skipped.

    Raises
    ------
    RuntimeError
        If ``skip_duplicates=False`` and a duplicate ``client_order_id`` is
        encountered.
    """
    from src.execution.paper_ledger import load_ledger, append_ledger_row

    existing = load_ledger(ledger_path)
    existing_ids: set[str] = (
        set(existing["client_order_id"].values) if not existing.empty else set()
    )

    appended = 0
    skipped = 0

    for row in rows:
        coid = str(row.get("client_order_id", "")).strip()
        if coid in existing_ids:
            if skip_duplicates:
                print(f"  [SKIP] {coid} — already in ledger")
                skipped += 1
                continue
            raise RuntimeError(
                f"Duplicate client_order_id: {coid!r} already exists in "
                f"{ledger_path}. Use --skip-duplicates to skip existing rows."
            )

        if dry_run:
            _print_row(row, prefix="  [DRY-RUN] would append:")
        else:
            append_ledger_row(ledger_path, row)
            existing_ids.add(coid)
            print(f"  [OK] appended {coid}")

        appended += 1

    return appended, skipped


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_row(row: dict[str, Any], prefix: str = "") -> None:
    cols = ["client_order_id", "flow", "symbol", "side", "quantity", "status", "submitted_at"]
    parts = "  |  ".join(f"{c}={row.get(c, '')}" for c in cols)
    print(f"{prefix} {parts}")


def print_summary(appended: int, skipped: int, *, dry_run: bool) -> None:
    print()
    if dry_run:
        print(f"  DRY-RUN: {appended} row(s) would be appended, {skipped} skipped.")
    else:
        print(f"  Done: {appended} row(s) appended, {skipped} skipped.")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.paper_ledger_import",
        description=(
            "Offline paper ledger backfill / import. "
            "No broker. No Alpaca. No network."
        ),
    )
    parser.add_argument(
        "--ledger",
        required=True,
        help="Path to the paper execution ledger CSV (created if absent)",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--from-artifacts",
        metavar="DIR",
        help="Artifact directory containing order_results.csv",
    )
    source.add_argument(
        "--manual-row",
        metavar="CSV",
        help="CSV file with manual ledger rows",
    )
    parser.add_argument(
        "--skip-duplicates",
        action="store_true",
        default=False,
        help="Skip rows whose client_order_id already exists (default: fail)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print rows that would be appended without writing",
    )
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)

    print("\n=== Paper Ledger Import ===")
    print(f"  ledger : {ledger_path}")

    try:
        if args.from_artifacts:
            artifact_dir = Path(args.from_artifacts)
            print(f"  source : artifacts @ {artifact_dir}")
            rows = _rows_from_artifacts(artifact_dir)
        else:
            manual_path = Path(args.manual_row)
            print(f"  source : manual CSV @ {manual_path}")
            rows = _rows_from_manual_csv(manual_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] {exc}")
        sys.exit(1)

    print(f"  rows   : {len(rows)} candidate(s)")
    if args.dry_run:
        print("  mode   : DRY-RUN (nothing will be written)")
    print()

    try:
        appended, skipped = import_rows(
            ledger_path,
            rows,
            skip_duplicates=args.skip_duplicates,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        print(f"\n  [FAIL] {exc}")
        sys.exit(1)

    print_summary(appended, skipped, dry_run=args.dry_run)
    print("=" * 28)


if __name__ == "__main__":
    main()
