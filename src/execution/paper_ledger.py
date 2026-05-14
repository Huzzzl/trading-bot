"""
execution/paper_ledger.py
--------------------------
Local persistence for paper order submissions.

Every real paper buy-submit or close-submit appends one row to a CSV ledger
so that the same client_order_id can never be submitted twice accidentally.

Public API
----------
load_ledger(path)                          → pd.DataFrame (empty if missing)
assert_client_order_id_unused(path, coid) → None | raises RuntimeError
append_ledger_row(path, row)               → None

What this module never does
---------------------------
* Never calls submit_order or any broker method.
* Never reads Alpaca credentials.
* Never appends during preview-only flows.
* Never appends during smoke checks or replay.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


_LEDGER_COLUMNS: list[str] = [
    "run_id",
    "flow",
    "client_order_id",
    "alpaca_order_id",
    "symbol",
    "side",
    "quantity",
    "status",
    "submitted_at",
    "output_dir",
    "created_at_utc",
    "notes",
]


def load_ledger(path: Path) -> pd.DataFrame:
    """Return the ledger as a DataFrame, or an empty one if the file is absent.

    Parameters
    ----------
    path:
        Filesystem path to the ledger CSV.

    Returns
    -------
    pd.DataFrame
        All existing ledger rows, or an empty DataFrame with the canonical
        columns when the file does not exist.
    """
    if not path.exists():
        return pd.DataFrame(columns=_LEDGER_COLUMNS)
    return pd.read_csv(path, dtype=str)


def assert_client_order_id_unused(path: Path, client_order_id: str) -> None:
    """Raise RuntimeError if *client_order_id* already appears in the ledger.

    Parameters
    ----------
    path:
        Path to the ledger CSV (need not exist).
    client_order_id:
        The ID to check.  Comparison is exact (case-sensitive).

    Raises
    ------
    RuntimeError
        If the ledger file exists and contains *client_order_id* in any row.
        The error message explicitly states that no order was submitted.
    """
    df = load_ledger(path)
    if df.empty:
        return
    if client_order_id in df["client_order_id"].values:
        raise RuntimeError(
            f"Duplicate client_order_id detected: {client_order_id!r} already exists "
            f"in the paper execution ledger ({path}). "
            f"No order was submitted. "
            f"To reuse this ID you must first remove or archive the ledger entry."
        )


def append_ledger_row(path: Path, row: dict[str, Any]) -> None:
    """Append one row to the ledger CSV, creating the file and parent dirs if needed.

    Parameters
    ----------
    path:
        Destination ledger path.
    row:
        Mapping of column name → value.  Any column from ``_LEDGER_COLUMNS``
        not present in *row* defaults to an empty string.  Extra keys are
        silently ignored.

    Notes
    -----
    The row is written using UTF-8 encoding.  If the file does not exist it
    is created with the header.  If it already exists the row is appended
    without re-writing the header.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if "created_at_utc" not in row or not row["created_at_utc"]:
        row = {**row, "created_at_utc": datetime.now(timezone.utc).isoformat()}

    normalised: dict[str, Any] = {col: row.get(col, "") for col in _LEDGER_COLUMNS}
    new_row_df = pd.DataFrame([normalised], columns=_LEDGER_COLUMNS)

    write_header = not path.exists()
    new_row_df.to_csv(
        path,
        mode="a",
        index=False,
        header=write_header,
        encoding="utf-8",
    )
