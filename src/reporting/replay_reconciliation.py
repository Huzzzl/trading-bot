"""
reporting/replay_reconciliation.py
------------------------------------
Offline reconciliation replay for paper execution artifacts.

Reads ``order_intents.csv`` and ``order_results.csv`` from a given directory,
normalises any Alpaca SDK enum strings in the result side/status columns
(e.g. ``"OrderSide.BUY"`` → ``"buy"``,
``"OrderStatus.PENDING_NEW"`` → ``"accepted"``), recomputes reconciliation,
and returns the result as a dict.

No Alpaca credentials, network calls, or order submissions are made.
This module is the runtime/research library form of the old
``src.tools.replay_order_reconciliation`` CLI tool (archived in PR R4d).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from src.reporting.reconciliation import build_reconciliation

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, str] = {
    "new":                  "accepted",
    "pending_new":          "accepted",
    "accepted":             "accepted",
    "accepted_for_bidding": "accepted",
    "filled":               "filled",
    "partially_filled":     "filled",
    "canceled":             "cancelled",
    "cancelled":            "cancelled",
    "expired":              "cancelled",
    "replaced":             "cancelled",
    "rejected":             "rejected",
    "stopped":              "rejected",
    "suspended":            "rejected",
    "calculated":           "rejected",
}


def _strip_enum_prefix(val: str) -> str:
    """``"OrderSide.BUY"`` → ``"buy"``, ``"buy"`` → ``"buy"``."""
    s = val.strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


def _normalize_side(raw: str) -> str:
    return _strip_enum_prefix(raw)


def _normalize_status(raw: str) -> str:
    key = _strip_enum_prefix(raw)
    return _STATUS_MAP.get(key, key)


# ---------------------------------------------------------------------------
# CSV loaders
# ---------------------------------------------------------------------------

def _load_intents(path: Path) -> list[SimpleNamespace]:
    if not path.exists():
        raise FileNotFoundError(f"order_intents.csv not found: {path}")
    df = pd.read_csv(path)
    out = []
    for _, row in df.iterrows():
        cid = row.get("client_order_id")
        out.append(SimpleNamespace(
            client_order_id=str(cid) if cid is not None and not pd.isna(cid) else None,
            symbol=str(row["symbol"]),
            side=str(row["side"]),
            quantity=float(row["quantity"]),
            reason=str(row.get("reason", "")),
        ))
    return out


def _load_results(path: Path) -> list[SimpleNamespace]:
    if not path.exists():
        raise FileNotFoundError(f"order_results.csv not found: {path}")
    df = pd.read_csv(path)
    out = []
    for _, row in df.iterrows():
        cid = row.get("client_order_id")
        out.append(SimpleNamespace(
            client_order_id=str(cid) if cid is not None and not pd.isna(cid) else None,
            symbol=str(row["symbol"]),
            side=_normalize_side(str(row["side"])),
            quantity=float(row["quantity"]),
            status=_normalize_status(str(row["status"])),
            reason=str(row.get("reason", "")),
        ))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def replay(output_dir: Path, write: bool = False) -> dict[str, Any]:
    """Load artifacts from *output_dir* and recompute reconciliation.

    Parameters
    ----------
    output_dir:
        Directory containing ``order_intents.csv`` and ``order_results.csv``.
    write:
        When ``True``, write the result to
        ``order_reconciliation_replay.json`` inside *output_dir*.

    Returns
    -------
    dict
        Reconciliation summary (same schema as ``order_reconciliation.json``).

    Raises
    ------
    FileNotFoundError
        If either CSV is absent.
    """
    output_dir = Path(output_dir)
    intents = _load_intents(output_dir / "order_intents.csv")
    results = _load_results(output_dir / "order_results.csv")
    recon   = build_reconciliation(intents, results)
    if write:
        out_path = output_dir / "order_reconciliation_replay.json"
        out_path.write_text(json.dumps(recon, indent=2), encoding="utf-8")
    return recon
