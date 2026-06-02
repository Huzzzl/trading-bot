"""
tools/live_submit.py
----------------------
Live submit skeleton — dry-run mode only.

Usage::

    python -m src.tools.live_submit \\
        --config config/settings.paper.local.yaml \\
        --symbol SPY \\
        --confirm "DRY-RUN-LIVE-SUBMIT" \\
        --output-dir output/live_submit_dry_run

Goal
----
Validate every live submit precondition and write a dry-run audit plan
artifact.  While ``live_submit_dry_run=true`` (the only currently allowed
state), this tool **never** calls ``submit_order``.

Preconditions checked (all must pass before writing the plan)
------------------------------------------------------------
1. Confirmation token must equal ``DRY-RUN-LIVE-SUBMIT`` exactly.
2. Config must load without error.
3. ``live_trading_enabled`` must be ``false``.
4. ``live_submit_dry_run`` must be ``true``.
5. ``live_kill_switch_enabled`` must be ``true``.
6. ``live_require_human_confirm`` must be ``true``.
7. Automated risk gate must be implemented (currently BLOCKED — PR R4e
   removed the manual checklist chain; a real automated risk gate is
   required before this path is unblocked).
8. At least one matching dry-run intent must exist with:
   ``live_sizing_mode=notional``, ``sizing_status=PASS``,
   ``dry_run_only=true``, ``submit_allowed=false``.

Artifact written
----------------
``{output_dir}/live_submit_dry_run_plan.json``

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes the live ledger (``append_live_ledger_row`` is not called).
* Never reads paper credentials (``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``).
* Never submits or modifies any order or position.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REQUIRED_CONFIRM_TOKEN = "DRY-RUN-LIVE-SUBMIT"


# ---------------------------------------------------------------------------
# Pure validators
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _validate_confirmation_token(token: str) -> list[str]:
    """Return a non-empty error list if token is missing or wrong."""
    if not token or not token.strip():
        return [f"missing confirmation token — required: {_REQUIRED_CONFIRM_TOKEN}"]
    if token.strip() != _REQUIRED_CONFIRM_TOKEN:
        return [f"wrong confirmation token {token.strip()!r} — required: {_REQUIRED_CONFIRM_TOKEN}"]
    return []


def _validate_config_safety(ex: Any) -> list[str]:
    """Check live safety config fields.  Returns list of blocking errors."""
    errors: list[str] = []

    if _is_truthy(getattr(ex, "live_trading_enabled", False)):
        errors.append(
            "live_trading_enabled=true — must be false; "
            "real live submit is not implemented"
        )

    if not _is_truthy(getattr(ex, "live_submit_dry_run", True)):
        errors.append(
            "live_submit_dry_run=false — dry-run gate must remain true; "
            "real submit path is not implemented"
        )

    if not _is_truthy(getattr(ex, "live_kill_switch_enabled", True)):
        errors.append(
            "live_kill_switch_enabled=false — kill switch must be enabled"
        )

    if not _is_truthy(getattr(ex, "live_require_human_confirm", True)):
        errors.append(
            "live_require_human_confirm=false — human confirmation must be required"
        )

    return errors


def _validate_selected_intent(intent: dict[str, Any]) -> list[str]:
    """Validate safety fields on the selected intent row."""
    errors: list[str] = []

    if not _is_truthy(intent.get("dry_run_only", "")):
        errors.append(
            f"selected intent has dry_run_only={intent.get('dry_run_only')!r} — must be true"
        )

    if _is_truthy(intent.get("submit_allowed", "")):
        errors.append(
            f"selected intent has submit_allowed={intent.get('submit_allowed')!r} — must be false"
        )

    sizing_status = str(intent.get("sizing_status", "")).strip()
    if sizing_status != "PASS":
        errors.append(
            f"selected intent has sizing_status={sizing_status!r} — must be PASS"
        )

    return errors


# ---------------------------------------------------------------------------
# Automated risk gate placeholder
# ---------------------------------------------------------------------------

_AUTOMATED_RISK_GATE_IMPLEMENTED = False


def _check_automated_risk_gate() -> list[str]:
    """Return blocking errors until an automated risk gate is implemented.

    PR R4e removed the manual checklist chain (live_pre_submit_checklist /
    live_dry_run_review).  Live submit is fail-closed until an automated
    state-machine risk gate replaces it.
    """
    if not _AUTOMATED_RISK_GATE_IMPLEMENTED:
        return [
            "automated risk gate not implemented — "
            "manual checklist chain removed in PR R4e; "
            "live submit is blocked until an automated risk gate is in place"
        ]
    return []


# ---------------------------------------------------------------------------
# Intent loader
# ---------------------------------------------------------------------------

def _load_and_select_intent(
    intents_dir: Path,
    symbol: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read intent CSV and pick the best matching row for symbol.

    Selection criteria (all must match):
    - symbol matches (case-insensitive)
    - live_sizing_mode == "notional"
    - sizing_status == "PASS"

    Returns (intent_dict, []) on success, (None, [errors]) on failure.
    """
    intents_csv = intents_dir / "live_order_intents.csv"
    if not intents_csv.exists():
        return None, [
            f"no intent artifacts found at {intents_csv} — "
            "run live_dry_run_intents first"
        ]

    try:
        with intents_csv.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        return None, [f"could not read intent CSV {intents_csv}: {exc}"]

    candidates = [
        r for r in rows
        if str(r.get("symbol", "")).strip().upper() == symbol.upper()
        and str(r.get("live_sizing_mode", "")).strip().lower() == "notional"
        and str(r.get("sizing_status", "")).strip() == "PASS"
    ]

    if not candidates:
        return None, [
            f"no suitable intent found for symbol={symbol!r} with "
            "live_sizing_mode=notional and sizing_status=PASS"
        ]

    return candidates[0], []


# ---------------------------------------------------------------------------
# Plan builder and writer
# ---------------------------------------------------------------------------

def _build_plan(
    intent: dict[str, Any],
    ex: Any,
    checked_at_utc: str,
    symbol: str,
) -> dict[str, Any]:
    """Build the dry-run submit plan artifact dict."""
    return {
        "checked_at_utc":           checked_at_utc,
        "symbol":                   symbol,
        "intent_row_identity":      str(intent.get("checked_at_utc", "")),
        "side":                     str(intent.get("side", "")),
        "order_type":               str(intent.get("order_type", "")),
        "live_sizing_mode":         str(intent.get("live_sizing_mode", "")),
        "effective_quantity":       intent.get("effective_quantity"),
        "effective_notional":       intent.get("effective_notional"),
        "entry_price":              intent.get("entry_price"),
        "live_max_order_notional":  intent.get("live_max_order_notional"),
        "live_submit_dry_run":      True,
        "live_trading_enabled":     bool(getattr(ex, "live_trading_enabled",    False)),
        "live_kill_switch_enabled": bool(getattr(ex, "live_kill_switch_enabled", True)),
        "live_require_human_confirm": bool(getattr(ex, "live_require_human_confirm", True)),
        "submit_order_called":      False,
        "submit_allowed":           False,
        "final_action":             "DRY_RUN_ONLY_NO_ORDER_SUBMITTED",
    }


def write_plan(output_dir: Path, plan: dict[str, Any]) -> Path:
    """Write live_submit_dry_run_plan.json to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "live_submit_dry_run_plan.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    from src.tools.paper_status import check_config

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_submit",
        description=(
            "Live submit skeleton — dry-run mode only. "
            "Validates all preconditions and writes an audit plan. "
            "Never calls submit_order. Never writes the live ledger."
        ),
    )
    parser.add_argument("--config",      required=True, help="Path to YAML config")
    parser.add_argument("--symbol",      required=True, help="Symbol to evaluate")
    parser.add_argument("--confirm",     required=True,
                        help=f"Confirmation token (required: {_REQUIRED_CONFIRM_TOKEN})")
    parser.add_argument("--output-dir",  required=True,
                        help="Directory for plan artifact and checklist sub-artifacts")
    parser.add_argument("--intents-dir", default=None,
                        help="Path to live_dry_run_intents artifacts "
                             "(default: {output-dir}/live_dry_run_intents)")
    args = parser.parse_args(argv)

    output_dir     = Path(args.output_dir)
    symbol         = args.symbol.strip().upper()
    checked_at_utc = datetime.now(timezone.utc).isoformat()

    def _fail(msg: str) -> None:
        print(f"\n  [FAIL] {msg}")
        sys.exit(1)

    def _fail_many(errors: list[str]) -> None:
        for e in errors:
            print(f"  [FAIL] {e}")
        sys.exit(1)

    print(f"\n=== Live Submit (Dry-Run Mode) ===")
    print(f"  symbol  : {symbol}")
    print(f"  confirm : {args.confirm!r}")
    print()

    # 1. Confirmation token
    token_errors = _validate_confirmation_token(args.confirm)
    if token_errors:
        _fail_many(token_errors)

    # 2. Config
    cfg_result, cfg = check_config(args.config)
    if cfg_result["status"] == "FAIL" or cfg is None:
        _fail(f"config: {cfg_result.get('detail', 'load failed')}")

    # 3. Safety config fields
    safety_errors = _validate_config_safety(cfg.execution)
    if safety_errors:
        _fail_many(safety_errors)

    # 4. Automated risk gate
    gate_errors = _check_automated_risk_gate()
    if gate_errors:
        _fail_many(gate_errors)

    # 5. Load and select intent
    if args.intents_dir:
        intents_dir = Path(args.intents_dir)
    else:
        intents_dir = output_dir / "live_dry_run_intents"

    intent, intent_errors = _load_and_select_intent(intents_dir, symbol)
    if intent_errors:
        _fail_many(intent_errors)

    # 6. Validate selected intent
    validation_errors = _validate_selected_intent(intent)
    if validation_errors:
        _fail_many(validation_errors)

    # 7. Build and write plan
    plan      = _build_plan(intent, cfg.execution, checked_at_utc, symbol)
    plan_path = write_plan(output_dir, plan)

    # 8. Print summary
    print(f"  live_submit_dry_run   : {plan['live_submit_dry_run']}")
    print(f"  live_trading_enabled  : {plan['live_trading_enabled']}")
    print(f"  side                  : {plan['side']}")
    print(f"  order_type            : {plan['order_type']}")
    print(f"  live_sizing_mode      : {plan['live_sizing_mode']}")
    print(f"  effective_notional    : {plan['effective_notional']}")
    print(f"  submit_order_called   : {plan['submit_order_called']}")
    print(f"  submit_allowed        : {plan['submit_allowed']}")
    print(f"  final_action          : {plan['final_action']}")
    print(f"\n  plan written → {plan_path}")
    print("=" * 34)


if __name__ == "__main__":
    main()
