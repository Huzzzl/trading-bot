"""
execution/live_submit_executor.py
-----------------------------------
Guarded live submit executor skeleton.

``maybe_execute_live_submit()`` is the single entry point for real order
submission.  Every safety guard must pass before ``submit_order`` is called.
With the current default config and approval artifacts (which always carry
``live_trading_approved=false`` and ``live_order_submission_approved=false``),
``submit_order`` is permanently unreachable.

Confirmation token
------------------
Real submit requires the exact token ``REAL-LIVE-SUBMIT-AUTHORIZED``.  This is
distinct from the dry-run token ``DRY-RUN-LIVE-SUBMIT`` and must be supplied
explicitly by the operator.

Guards (all must pass in order)
--------------------------------
 1. Confirmation token == REAL-LIVE-SUBMIT-AUTHORIZED
 2. Approval artifact exists and is valid
 3. approval_for_real_submit_pr == true
 4. approval_scope == "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"
 5. live_trading_approved == false  → BLOCK  (always true with current tooling)
 6. live_order_submission_approved == false  → BLOCK  (always true with current tooling)
 7. live_trading_enabled == true    (default false → blocks)
 8. live_submit_dry_run == false    (default true  → blocks)
 9. live_kill_switch_enabled == false (default true → blocks; kill switch engaged)
10. live_require_human_confirm == true
11. Pre-submit checklist READY
12. Submit plan review PASS
13. Daily order count < live_max_orders_per_day
14. Daily notional + intended ≤ live_max_notional_per_day
15. No open position in target symbol
16. No open order for target symbol
17. client_order_id not already in live ledger
18. effective_notional ≤ live_max_order_notional

Blocked path
------------
When any guard fails, a ``live_submit_blocked_report.json`` is written to
``output_dir`` with ``submit_order_called=false`` and the blocking guard
identified.  ``submit_order`` is never called.

What this module never does
---------------------------
* Never calls ``submit_order`` unless all 18 guards pass (unreachable today).
* Never writes the live ledger on the blocked path.
* Never reads paper credentials.
* Never cancels orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REAL_SUBMIT_CONFIRM_TOKEN = "REAL-LIVE-SUBMIT-AUTHORIZED"
_EXPECTED_APPROVAL_SCOPE = "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON artifact.

    Raises
    ------
    FileNotFoundError / ValueError
    """
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Guard functions (pure — no I/O, easily testable)
# ---------------------------------------------------------------------------

def _check_confirm_token(token: str) -> list[str]:
    if token != REAL_SUBMIT_CONFIRM_TOKEN:
        return [
            f"confirmation token mismatch — "
            f"expected {REAL_SUBMIT_CONFIRM_TOKEN!r}, got {token!r}"
        ]
    return []


def _check_approval_artifact(artifact: dict[str, Any]) -> list[str]:
    """Validate the approval artifact fields.

    Guards 3–6: approval_for_real_submit_pr, approval_scope,
    live_trading_approved (must be false → block),
    live_order_submission_approved (must be false → block).
    """
    violations: list[str] = []

    if not _is_truthy(artifact.get("approval_for_real_submit_pr", False)):
        violations.append(
            f"approval_for_real_submit_pr="
            f"{artifact.get('approval_for_real_submit_pr')!r} — must be true"
        )

    scope = str(artifact.get("approval_scope", "")).strip()
    if scope != _EXPECTED_APPROVAL_SCOPE:
        violations.append(
            f"approval_scope={artifact.get('approval_scope')!r} — "
            f"must be {_EXPECTED_APPROVAL_SCOPE!r}"
        )

    # live_trading_approved must be true to proceed.
    # Current tooling always writes this as false → always blocks here.
    if not _is_truthy(artifact.get("live_trading_approved", False)):
        violations.append(
            f"live_trading_approved="
            f"{artifact.get('live_trading_approved')!r} — "
            "live trading is not approved; submit blocked. "
            "Current approval tooling always sets this to false."
        )

    # live_order_submission_approved must be true to proceed.
    # Current tooling always writes this as false → always blocks here.
    if not _is_truthy(artifact.get("live_order_submission_approved", False)):
        violations.append(
            f"live_order_submission_approved="
            f"{artifact.get('live_order_submission_approved')!r} — "
            "live order submission is not approved; submit blocked. "
            "Current approval tooling always sets this to false."
        )

    return violations


def _check_config_safety(
    live_trading_enabled: bool,
    live_submit_dry_run: bool,
    live_kill_switch_enabled: bool,
    live_require_human_confirm: bool,
) -> list[str]:
    """Validate config safety fields.

    Guards 7–10.
    """
    violations: list[str] = []

    if not live_trading_enabled:
        violations.append(
            "live_trading_enabled=false — live trading is disabled in config"
        )

    if live_submit_dry_run:
        violations.append(
            "live_submit_dry_run=true — dry-run gate is engaged; "
            "must be set to false by operator to enable real submit"
        )

    # kill_switch_enabled=true means kill switch is armed/engaged → blocks trading
    if live_kill_switch_enabled:
        violations.append(
            "live_kill_switch_enabled=true — kill switch is engaged; "
            "must be disengaged (false) to allow real submit"
        )

    if not live_require_human_confirm:
        violations.append(
            "live_require_human_confirm=false — human confirmation requirement "
            "must be enabled"
        )

    return violations


def _check_checklist_ready(final_result: str) -> list[str]:
    if str(final_result).strip().upper() != "READY":
        return [f"pre_submit_checklist.final_result={final_result!r} — must be READY"]
    return []


def _check_plan_review_pass(review_result: str) -> list[str]:
    if str(review_result).strip().upper() != "PASS":
        return [f"plan_review.review_result={review_result!r} — must be PASS"]
    return []


def _check_daily_limits(
    today_order_count: int,
    today_notional_total: float,
    intended_notional: float,
    max_orders_per_day: int,
    max_notional_per_day: float,
) -> list[str]:
    violations: list[str] = []
    if today_order_count >= max_orders_per_day:
        violations.append(
            f"daily order limit reached: today={today_order_count}, "
            f"max={max_orders_per_day}"
        )
    if today_notional_total + intended_notional > max_notional_per_day:
        violations.append(
            f"daily notional limit would be exceeded: "
            f"today={today_notional_total} + intended={intended_notional} "
            f"> max={max_notional_per_day}"
        )
    return violations


def _check_no_open_conflict(
    has_open_position: bool,
    has_open_order: bool,
    symbol: str,
) -> list[str]:
    violations: list[str] = []
    if has_open_position:
        violations.append(f"open position already exists for {symbol}")
    if has_open_order:
        violations.append(f"open order already exists for {symbol}")
    return violations


def _check_idempotency(client_order_id_used: bool, client_order_id: str) -> list[str]:
    if client_order_id_used:
        return [
            f"client_order_id={client_order_id!r} already in live ledger — "
            "duplicate order blocked"
        ]
    return []


def _check_notional_cap(
    effective_notional: float,
    live_max_order_notional: float,
) -> list[str]:
    if effective_notional > live_max_order_notional:
        return [
            f"effective_notional={effective_notional} exceeds "
            f"live_max_order_notional={live_max_order_notional}"
        ]
    return []


# ---------------------------------------------------------------------------
# I/O helpers (mockable in tests)
# ---------------------------------------------------------------------------

def _load_approval_artifact(approval_path: Path) -> dict[str, Any]:
    return _read_json(approval_path)


def _load_checklist(checklist_path: Path) -> dict[str, Any]:
    return _read_json(checklist_path)


def _load_plan_review(review_path: Path) -> dict[str, Any]:
    return _read_json(review_path)


def _count_today_orders(ledger_path: Path) -> tuple[int, float]:
    """Return (today_order_count, today_notional_total) from ledger.

    Returns (0, 0.0) when the ledger does not exist.
    """
    if not ledger_path.exists():
        return 0, 0.0
    import csv as _csv
    today = datetime.now(tz=timezone.utc).date().isoformat()
    count = 0
    notional = 0.0
    with ledger_path.open(encoding="utf-8", newline="") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            submitted_at = str(row.get("submitted_at", ""))
            if submitted_at.startswith(today):
                count += 1
                try:
                    notional += float(row.get("notional") or 0)
                except (TypeError, ValueError):
                    pass
    return count, notional


def _is_client_order_id_used(ledger_path: Path, client_order_id: str) -> bool:
    """Return True if client_order_id already in ledger."""
    if not ledger_path.exists():
        return False
    import csv as _csv
    with ledger_path.open(encoding="utf-8", newline="") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            if row.get("client_order_id") == client_order_id:
                return True
    return False


def _check_broker_position(broker_client: Any, symbol: str) -> bool:
    """Return True if an open live position exists for symbol."""
    try:
        broker_client.get_open_position(symbol)
        return True
    except Exception:
        return False


def _check_broker_open_order(broker_client: Any, symbol: str) -> bool:
    """Return True if an open live order exists for symbol."""
    try:
        orders = broker_client.get_orders(symbol=symbol, status="open")
        return bool(orders)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Blocked-report writer
# ---------------------------------------------------------------------------

def write_blocked_report(output_dir: Path, report: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "live_submit_blocked_report.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def maybe_execute_live_submit(
    *,
    confirm_token: str,
    approval_path: Path,
    checklist_path: Path,
    review_path: Path,
    symbol: str,
    intended_notional: float,
    client_order_id: str,
    live_trading_enabled: bool,
    live_submit_dry_run: bool,
    live_kill_switch_enabled: bool,
    live_require_human_confirm: bool,
    live_max_order_notional: float,
    live_max_orders_per_day: int,
    live_max_notional_per_day: float,
    ledger_path: Path,
    output_dir: Path,
    broker_client: Any = None,
) -> dict[str, Any]:
    """Run all safety guards; call submit_order only if every guard passes.

    With current default config and approval artifacts, submit_order is
    permanently unreachable.  A ``live_submit_blocked_report.json`` is written
    to ``output_dir`` whenever submission is blocked.

    Returns
    -------
    dict with at minimum:
        submit_order_called : bool  (always False with current tooling)
        blocked             : bool
        block_guard         : str | None
        violations          : list[str]
    """
    checked_at = datetime.now(tz=timezone.utc).isoformat()

    def _blocked(guard: str, violations: list[str]) -> dict[str, Any]:
        report = {
            "checked_at_utc":      checked_at,
            "symbol":              symbol,
            "submit_order_called": False,
            "blocked":             True,
            "block_guard":         guard,
            "violations":          violations,
        }
        write_blocked_report(output_dir, report)
        return report

    # Guard 1 — confirmation token
    v = _check_confirm_token(confirm_token)
    if v:
        return _blocked("confirm_token", v)

    # Guard 2 + 3–6 — approval artifact
    try:
        approval = _load_approval_artifact(approval_path)
    except (FileNotFoundError, ValueError) as exc:
        return _blocked("approval_artifact_load", [str(exc)])

    v = _check_approval_artifact(approval)
    if v:
        return _blocked("approval_artifact", v)

    # Guards 7–10 — config safety
    v = _check_config_safety(
        live_trading_enabled,
        live_submit_dry_run,
        live_kill_switch_enabled,
        live_require_human_confirm,
    )
    if v:
        return _blocked("config_safety", v)

    # Guard 11 — pre-submit checklist
    try:
        checklist = _load_checklist(checklist_path)
    except (FileNotFoundError, ValueError) as exc:
        return _blocked("checklist_load", [str(exc)])

    v = _check_checklist_ready(checklist.get("final_result", ""))
    if v:
        return _blocked("checklist_ready", v)

    # Guard 12 — plan review
    try:
        review = _load_plan_review(review_path)
    except (FileNotFoundError, ValueError) as exc:
        return _blocked("plan_review_load", [str(exc)])

    v = _check_plan_review_pass(review.get("review_result", ""))
    if v:
        return _blocked("plan_review_pass", v)

    # Guard 13–14 — daily limits
    today_count, today_notional = _count_today_orders(ledger_path)
    v = _check_daily_limits(
        today_count,
        today_notional,
        intended_notional,
        live_max_orders_per_day,
        live_max_notional_per_day,
    )
    if v:
        return _blocked("daily_limits", v)

    # Guards 15–16 — open position / order conflict
    if broker_client is not None:
        has_position = _check_broker_position(broker_client, symbol)
        has_order = _check_broker_open_order(broker_client, symbol)
    else:
        has_position = False
        has_order = False

    v = _check_no_open_conflict(has_position, has_order, symbol)
    if v:
        return _blocked("open_conflict", v)

    # Guard 17 — idempotency
    used = _is_client_order_id_used(ledger_path, client_order_id)
    v = _check_idempotency(used, client_order_id)
    if v:
        return _blocked("idempotency", v)

    # Guard 18 — notional cap
    v = _check_notional_cap(intended_notional, live_max_order_notional)
    if v:
        return _blocked("notional_cap", v)

    # -----------------------------------------------------------------------
    # All guards passed — submit_order would be called here.
    # Unreachable with current tooling (live_trading_approved=false,
    # live_order_submission_approved=false, live_submit_dry_run=true,
    # live_kill_switch_enabled=true, live_trading_enabled=false).
    # -----------------------------------------------------------------------
    # broker_client.submit_order(...)  # NOT CALLED — guards prevent reaching here
    return {
        "checked_at_utc":      checked_at,
        "symbol":              symbol,
        "submit_order_called": False,
        "blocked":             False,
        "block_guard":         None,
        "violations":          [],
        "note": (
            "All guards passed but submit_order was not called. "
            "Real submit requires a future implementation step."
        ),
    }
