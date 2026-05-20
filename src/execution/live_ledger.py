"""
execution/live_ledger.py
-------------------------
Schema definition and append helper for the live execution ledger.

This module defines the column schema for the live ledger CSV and provides
``append_live_ledger_row()`` as a future write helper.  No live order
submission path exists in this codebase — the helper is write-guarded and
will raise unless ``allow_write=True`` is explicitly passed.

Public API
----------
LIVE_LEDGER_COLUMNS                               list[str]
append_live_ledger_row(path, row, *, allow_write) → None

What this module never does
---------------------------
* Never calls submit_order or any broker method.
* Never reads Alpaca credentials.
* Never submits or cancels orders.
* Never writes unless allow_write=True is explicitly passed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


LIVE_LEDGER_COLUMNS: list[str] = [
    "run_id",
    "flow",
    "client_order_id",
    "alpaca_order_id",
    "symbol",
    "side",
    "order_type",
    "live_sizing_mode",
    "quantity",
    "notional",
    "status",
    "submitted_at",
    "checked_at_utc",
    "dry_run_only",
    "submit_allowed",
    "notes",
]

VALID_SIDES: frozenset[str] = frozenset({"buy", "sell"})
VALID_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})


def append_live_ledger_row(
    path: Path,
    row: dict[str, Any],
    *,
    allow_write: bool = False,
) -> None:
    """Append one row to the live execution ledger CSV.

    Parameters
    ----------
    path:
        Destination ledger path.
    row:
        Mapping of column name → value.  Columns not present in *row* default
        to an empty string.  Extra keys are silently ignored.
    allow_write:
        Must be explicitly ``True`` to permit writing.  Any other value raises
        ``RuntimeError``.  This guard exists because no live submit path is
        implemented — callers must consciously opt in.

    Raises
    ------
    RuntimeError
        If ``allow_write`` is not exactly ``True``.
    """
    if allow_write is not True:
        raise RuntimeError(
            "append_live_ledger_row: allow_write must be explicitly True. "
            "No live order submission path is implemented. "
            "Do not call this helper without explicit operator sign-off."
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    if "checked_at_utc" not in row or not row["checked_at_utc"]:
        row = {**row, "checked_at_utc": datetime.now(timezone.utc).isoformat()}

    normalised: dict[str, Any] = {col: row.get(col, "") for col in LIVE_LEDGER_COLUMNS}
    new_row_df = pd.DataFrame([normalised], columns=LIVE_LEDGER_COLUMNS)

    write_header = not path.exists()
    new_row_df.to_csv(
        path,
        mode="a",
        index=False,
        header=write_header,
        encoding="utf-8",
    )
