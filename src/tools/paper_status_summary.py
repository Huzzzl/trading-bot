"""
tools/paper_status_summary.py
------------------------------
Read-only paper trading status summary.

Reconciles the immediate post-submit audit trail written by
``run_automated_paper_cycle`` (``logs/paper_cycles/YYYY-MM-DD.jsonl``)
against the current state of the Alpaca paper account, so operators can
see final order outcomes and current positions in one place.

Usage::

    python -m src.tools.paper_status_summary
    python -m src.tools.paper_status_summary --audit-date 2026-07-09
    python -m src.tools.paper_status_summary --no-write

The tool never submits, cancels, or otherwise mutates broker state. It
calls only these adapter reads:

* ``get_account``
* ``get_position(SPY)``
* ``list_open_orders(symbol=SPY)``
* ``get_order`` / ``get_order_by_client_order_id``

The summary is emitted as JSON to stdout and (unless ``--no-write``) also
persisted under ``logs/paper_status/YYYY-MM-DD.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.broker.alpaca_paper_adapter import (
    AlpacaPaperAdapter,
    AlpacaPaperAdapterError,
)

_SYMBOL = "SPY"
_DEFAULT_AUDIT_DIR = Path("logs/paper_cycles")
_DEFAULT_OUTPUT_DIR = Path("logs/paper_status")
_CLAIMS_SUBDIR = "_claims"
_CLAIM_SUFFIX = ".claim"
_SUBMITTED_SUFFIX = ".submitted"

_FILLED_STATUSES = frozenset({"filled"})
_PENDING_STATUSES = frozenset(
    {"new", "accepted", "accepted_for_bidding", "pending_new", "partially_filled"}
)
_REJECTED_STATUSES = frozenset({"rejected", "canceled", "cancelled", "expired"})


# ---------------------------------------------------------------------------
# Audit reading
# ---------------------------------------------------------------------------


def audit_log_path(audit_dir: Path, date_utc: str) -> Path:
    return audit_dir / f"{date_utc}.jsonl"


def read_audit_records(audit_path: Path) -> list[dict[str, Any]]:
    """Return every valid JSON record in ``audit_path``.

    Blank lines and lines that fail to parse are skipped rather than
    raising — a partially-written line at process exit must not prevent
    the summary tool from running.
    """
    if not audit_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with audit_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def collect_audit_orders(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract per-order entries from audit records.

    Deduplicates by ``broker_order_id`` (preferred) or ``client_order_id``.
    Keeps the latest occurrence per key so the caller reconciles against
    the most recent recorded post-submit state.
    """
    by_key: dict[str, dict[str, Any]] = {}
    for rec in records:
        broker_id = rec.get("broker_order_id")
        client_id = rec.get("client_order_id")
        if not broker_id and not client_id:
            continue
        key = broker_id or client_id
        by_key[key] = {
            "broker_order_id": broker_id,
            "client_order_id": client_id,
            "audit_status": rec.get("broker_order_status") or rec.get("order_status"),
            "action": rec.get("action"),
            "submitted_qty": rec.get("submitted_qty"),
            "timestamp_utc": rec.get("timestamp_utc"),
            "reason_codes": list(rec.get("reason_codes") or []),
        }
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _classify(status: str | None) -> str:
    if not isinstance(status, str):
        return "unknown"
    s = status.strip().lower()
    if s in _FILLED_STATUSES:
        return "filled"
    if s in _PENDING_STATUSES:
        return "pending"
    if s in _REJECTED_STATUSES:
        return "rejected"
    return "unknown"


def reconcile_orders(
    adapter: Any,
    audit_orders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fetch final status per audit order from the broker.

    Uses ``broker_order_id`` when available; on failure or empty result
    falls back to ``client_order_id`` before declaring the order missing.
    An order is only considered missing when both lookups (where
    applicable) have been tried and neither returned a matching order.
    """
    reconciled: list[dict[str, Any]] = []
    for entry in audit_orders:
        broker_id = entry.get("broker_order_id")
        client_id = entry.get("client_order_id")
        fetched: dict[str, Any] | None = None
        fetch_error: str | None = None
        if broker_id:
            try:
                fetched = adapter.get_order(broker_id)
            except AlpacaPaperAdapterError as exc:
                fetch_error = str(exc)
        if fetched is None and client_id:
            try:
                fallback = adapter.get_order_by_client_order_id(client_id)
            except AlpacaPaperAdapterError as exc:
                if fetch_error is None:
                    fetch_error = str(exc)
            else:
                if fallback is not None:
                    fetched = fallback
                    fetch_error = None
        current_status = fetched.get("status") if isinstance(fetched, dict) else None
        reconciled.append({
            "broker_order_id": broker_id,
            "client_order_id": client_id,
            "audit_status": entry.get("audit_status"),
            "current_status": current_status,
            "classification": _classify(current_status),
            "filled_qty": fetched.get("filled_qty") if isinstance(fetched, dict) else None,
            "filled_avg_price": (
                fetched.get("filled_avg_price") if isinstance(fetched, dict) else None
            ),
            "side": fetched.get("side") if isinstance(fetched, dict) else None,
            "timestamp_utc": entry.get("timestamp_utc"),
            "found_at_broker": fetched is not None,
            "fetch_error": fetch_error,
        })
    return reconciled


# ---------------------------------------------------------------------------
# Local claim scan
# ---------------------------------------------------------------------------


def scan_orphan_claims(audit_dir: Path) -> list[str]:
    """Return idempotency keys with a ``.claim`` but no ``.submitted`` marker.

    An orphan claim usually means the runner crashed after reserving the
    key but before recording the broker response — a state that requires
    manual reconciliation before automation resumes for that key.
    """
    claims_dir = audit_dir / _CLAIMS_SUBDIR
    if not claims_dir.exists():
        return []
    orphans: list[str] = []
    for path in sorted(claims_dir.glob(f"*{_CLAIM_SUFFIX}")):
        key = path.name[: -len(_CLAIM_SUFFIX)]
        submitted = claims_dir / f"{key}{_SUBMITTED_SUFFIX}"
        if not submitted.exists():
            orphans.append(key)
    return orphans


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def position_predates_audit(
    position: dict[str, Any] | None,
    reconciled: list[dict[str, Any]],
) -> bool:
    """Return ``True`` when a broker position exists but the selected audit
    date shows no filled BUY that would explain it.

    Strategy allows overnight positions, so this is purely informational
    — it means the position was likely opened on a prior trading day and
    is expected. Callers surface this as a summary field, not a warning.
    """
    if position is None:
        return False
    qty = position.get("qty")
    if qty in (None, 0, 0.0):
        return False
    for r in reconciled:
        if r.get("classification") != "filled":
            continue
        side = r.get("side")
        if isinstance(side, str) and side.lower() == "buy":
            return False
    return True


def detect_warnings(
    *,
    reconciled: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    audit_records: list[dict[str, Any]],
    orphan_claims: list[str],
) -> list[str]:
    warnings: list[str] = []

    for r in reconciled:
        if not r["found_at_broker"] and r.get("broker_order_id"):
            warnings.append(
                "AUDIT_ORDER_MISSING_AT_BROKER: audit shows broker_order_id="
                f"{r['broker_order_id']} but Alpaca cannot find it"
            )

    audit_broker_ids = {
        r["broker_order_id"] for r in reconciled if r.get("broker_order_id")
    }
    audit_client_ids = {
        r["client_order_id"] for r in reconciled if r.get("client_order_id")
    }
    for o in open_orders:
        oid = o.get("id")
        cid = o.get("client_order_id")
        if oid in audit_broker_ids or cid in audit_client_ids:
            continue
        warnings.append(
            f"OPEN_ORDER_NOT_IN_AUDIT: Alpaca has open SPY order id={oid} "
            f"client_order_id={cid} that is not present in audit"
        )

    for rec in audit_records:
        codes = rec.get("reason_codes") or []
        if isinstance(codes, list) and "ORDER_STATUS_UNKNOWN" in codes:
            warnings.append(
                "ORDER_STATUS_UNKNOWN_IN_AUDIT: at least one audit record has "
                "reason_code=ORDER_STATUS_UNKNOWN — status was not confirmed"
            )
            break

    for key in orphan_claims:
        warnings.append(
            f"ORPHAN_CLAIM: idempotency key {key} has a .claim marker but no "
            ".submitted marker — manual reconciliation required"
        )

    return warnings


# ---------------------------------------------------------------------------
# Summary assembly
# ---------------------------------------------------------------------------


def _safe_account(adapter: Any) -> dict[str, Any]:
    try:
        return adapter.get_account()
    except AlpacaPaperAdapterError as exc:
        return {"error": str(exc)}


def _safe_position(adapter: Any, symbol: str) -> dict[str, Any] | None:
    try:
        return adapter.get_position(symbol)
    except AlpacaPaperAdapterError:
        return None


def _safe_open_orders(adapter: Any, symbol: str) -> list[dict[str, Any]]:
    try:
        return adapter.list_open_orders(symbol=symbol)
    except AlpacaPaperAdapterError:
        return []


def _portfolio_value(account: dict[str, Any]) -> float | None:
    for key in ("portfolio_value", "equity"):
        val = account.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def build_summary(
    *,
    adapter: Any,
    audit_dir: Path,
    audit_date_utc: str,
    now_utc: datetime,
    symbol: str = _SYMBOL,
) -> dict[str, Any]:
    audit_path = audit_log_path(audit_dir, audit_date_utc)
    records = read_audit_records(audit_path)
    audit_orders = collect_audit_orders(records)

    account = _safe_account(adapter)
    position = _safe_position(adapter, symbol)
    open_orders = _safe_open_orders(adapter, symbol)
    reconciled = reconcile_orders(adapter, audit_orders)
    orphan_claims = scan_orphan_claims(audit_dir)

    warnings = detect_warnings(
        reconciled=reconciled,
        open_orders=open_orders,
        audit_records=records,
        orphan_claims=orphan_claims,
    )

    filled = sum(1 for r in reconciled if r["classification"] == "filled")
    pending = sum(1 for r in reconciled if r["classification"] == "pending")
    rejected = sum(1 for r in reconciled if r["classification"] == "rejected")

    return {
        "timestamp_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "audit_date_utc": audit_date_utc,
        "audit_path": str(audit_path),
        "symbol": symbol,
        "account_cash": account.get("cash"),
        "buying_power": account.get("buying_power"),
        "portfolio_value": _portfolio_value(account),
        "account_status": account.get("status"),
        "spy_position_qty": position.get("qty") if position else None,
        "spy_avg_entry_price": position.get("avg_entry_price") if position else None,
        "spy_market_value": position.get("market_value") if position else None,
        "spy_unrealized_pl": position.get("unrealized_pl") if position else None,
        "position_may_predate_audit_date": position_predates_audit(position, reconciled),
        "open_orders": open_orders,
        "open_order_count": len(open_orders),
        "reconciled_orders": reconciled,
        "audit_order_count": len(audit_orders),
        "filled_order_count": filled,
        "pending_order_count": pending,
        "rejected_order_count": rejected,
        "orphan_claim_keys": orphan_claims,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_summary(summary: dict[str, Any], output_dir: Path, date_utc: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{date_utc}.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.paper_status_summary",
        description=(
            "Read-only paper trading status summary. Reconciles the daily "
            "audit JSONL against current Alpaca paper account state."
        ),
    )
    parser.add_argument(
        "--audit-dir",
        default=str(_DEFAULT_AUDIT_DIR),
        help=f"Audit JSONL directory (default: {_DEFAULT_AUDIT_DIR})",
    )
    parser.add_argument(
        "--audit-date",
        default=None,
        help="UTC date to summarise (YYYY-MM-DD). Defaults to today (UTC).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help=f"Where to write the JSON summary (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write the summary to disk; stdout only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    now_utc = datetime.now(timezone.utc)
    audit_date_utc = args.audit_date or now_utc.date().isoformat()

    try:
        adapter = AlpacaPaperAdapter.from_environment()
    except AlpacaPaperAdapterError as exc:
        error = {
            "error": f"could not construct AlpacaPaperAdapter: {exc}",
            "timestamp_utc": now_utc.astimezone(timezone.utc).isoformat(),
        }
        print(json.dumps(error, indent=2))
        return 2

    summary = build_summary(
        adapter=adapter,
        audit_dir=Path(args.audit_dir),
        audit_date_utc=audit_date_utc,
        now_utc=now_utc,
    )

    print(json.dumps(summary, indent=2, default=str))

    if not args.no_write:
        write_summary(summary, Path(args.output_dir), audit_date_utc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
