# ARCHIVED in PR R4. Historical manual/operator tool. Not part of active automated runtime.
# Moved from src/tools/live_submit_plan_review.py. Not importable as src.tools.live_submit_plan_review.
"""
tools/live_submit_plan_review.py
----------------------------------
Read-only review CLI for live_submit dry-run plan artifacts.

Usage::

    python -m src.tools.live_submit_plan_review \\
        --plan output/live_submit_dry_run/live_submit_dry_run_plan.json

Goal
----
Parse the plan artifact written by ``live_submit`` and verify that every
safety field is in the correct state before any future real submit is
considered.

Result
------
PASS — all safety fields are correctly set:
  live_submit_dry_run=true, live_trading_enabled=false,
  live_kill_switch_enabled=true, submit_order_called=false,
  submit_allowed=false, final_action=DRY_RUN_ONLY_NO_ORDER_SUBMITTED,
  live_sizing_mode=notional, effective_notional > 0 and ≤ live_max_order_notional

FAIL — any required field is missing or in an unsafe state.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes any file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_plan(path: Path) -> dict[str, Any]:
    """Read and return live_submit_dry_run_plan.json.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file contains malformed JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"plan not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "live_submit_dry_run",
    "live_trading_enabled",
    "live_kill_switch_enabled",
    "submit_order_called",
    "submit_allowed",
    "final_action",
    "live_sizing_mode",
    "effective_notional",
    "live_max_order_notional",
]

_EXPECTED_FINAL_ACTION = "DRY_RUN_ONLY_NO_ORDER_SUBMITTED"


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_plan(plan: dict[str, Any]) -> tuple[str, list[str]]:
    """Validate all safety fields in the plan.

    Returns
    -------
    (result, violations)
        result     : "PASS" or "FAIL"
        violations : list of human-readable failure strings (empty on PASS)
    """
    violations: list[str] = []

    # Required fields present
    missing = [f for f in _REQUIRED_FIELDS if f not in plan]
    if missing:
        violations.append(f"missing required field(s): {missing}")
        return "FAIL", violations

    # live_submit_dry_run must be true
    if not _is_truthy(plan["live_submit_dry_run"]):
        violations.append(
            f"live_submit_dry_run={plan['live_submit_dry_run']!r} — must be true"
        )

    # live_trading_enabled must be false
    if _is_truthy(plan["live_trading_enabled"]):
        violations.append(
            f"live_trading_enabled={plan['live_trading_enabled']!r} — must be false"
        )

    # live_kill_switch_enabled must be true
    if not _is_truthy(plan["live_kill_switch_enabled"]):
        violations.append(
            f"live_kill_switch_enabled={plan['live_kill_switch_enabled']!r} — must be true"
        )

    # submit_order_called must be false
    if _is_truthy(plan["submit_order_called"]):
        violations.append(
            f"submit_order_called={plan['submit_order_called']!r} — must be false"
        )

    # submit_allowed must be false
    if _is_truthy(plan["submit_allowed"]):
        violations.append(
            f"submit_allowed={plan['submit_allowed']!r} — must be false"
        )

    # final_action must match exactly
    if str(plan["final_action"]).strip() != _EXPECTED_FINAL_ACTION:
        violations.append(
            f"final_action={plan['final_action']!r} — "
            f"must be {_EXPECTED_FINAL_ACTION!r}"
        )

    # live_sizing_mode must be "notional"
    sizing_mode = str(plan.get("live_sizing_mode", "")).strip().lower()
    if sizing_mode != "notional":
        violations.append(
            f"live_sizing_mode={plan['live_sizing_mode']!r} — must be 'notional'"
        )

    # effective_notional must be > 0
    notional = _to_float(plan["effective_notional"])
    if notional is None or notional <= 0:
        violations.append(
            f"effective_notional={plan['effective_notional']!r} — must be > 0"
        )

    # effective_notional must be <= live_max_order_notional
    max_notional = _to_float(plan["live_max_order_notional"])
    if notional is not None and max_notional is not None and notional > max_notional:
        violations.append(
            f"effective_notional={notional} exceeds "
            f"live_max_order_notional={max_notional}"
        )

    result = "FAIL" if violations else "PASS"
    return result, violations


# ---------------------------------------------------------------------------
# Review builder
# ---------------------------------------------------------------------------

def build_review(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a structured review dict for printing and testing."""
    result, violations = validate_plan(plan)
    return {
        "symbol":                 plan.get("symbol", ""),
        "side":                   plan.get("side", ""),
        "order_type":             plan.get("order_type", ""),
        "live_sizing_mode":       plan.get("live_sizing_mode", ""),
        "effective_quantity":     plan.get("effective_quantity"),
        "effective_notional":     plan.get("effective_notional"),
        "live_max_order_notional": plan.get("live_max_order_notional"),
        "live_submit_dry_run":    plan.get("live_submit_dry_run"),
        "live_trading_enabled":   plan.get("live_trading_enabled"),
        "live_kill_switch_enabled": plan.get("live_kill_switch_enabled"),
        "submit_order_called":    plan.get("submit_order_called"),
        "submit_allowed":         plan.get("submit_allowed"),
        "final_action":           plan.get("final_action", ""),
        "review_result":          result,
        "violations":             violations,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_review(review: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live Submit Plan Review ===")
    print(f"  symbol                 : {review['symbol']}")
    print(f"  side                   : {review['side']}")
    print(f"  order_type             : {review['order_type']}")
    print(f"  live_sizing_mode       : {review['live_sizing_mode']}")
    print(f"  effective_quantity     : {review['effective_quantity']}")
    print(f"  effective_notional     : {review['effective_notional']}")
    print(f"  live_max_order_notional: {review['live_max_order_notional']}")
    print(f"  live_submit_dry_run    : {review['live_submit_dry_run']}")
    print(f"  live_trading_enabled   : {review['live_trading_enabled']}")
    print(f"  live_kill_switch_enabled: {review['live_kill_switch_enabled']}")
    print(f"  submit_order_called    : {review['submit_order_called']}")
    print(f"  submit_allowed         : {review['submit_allowed']}")
    print(f"  final_action           : {review['final_action']}")
    print()
    if review["violations"]:
        for v in review["violations"]:
            print(f"  ! {v}")
        print()
    print(f"  review_result: {review['review_result']}")
    print("=" * 32)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_submit_plan_review",
        description=(
            "Read-only review of live submit dry-run plan artifact. "
            "Checks all safety fields. "
            "No credentials required. Never calls Alpaca. "
            "Optionally writes review JSON with --output."
        ),
    )
    parser.add_argument(
        "--plan", required=True,
        help="Path to live_submit_dry_run_plan.json",
    )
    parser.add_argument(
        "--output", required=False, default=None,
        help="Optional path to write the review result as JSON",
    )
    args = parser.parse_args(argv)

    plan_path = Path(args.plan)

    try:
        plan = parse_plan(plan_path)
    except FileNotFoundError as exc:
        print(f"\n  [FAIL] {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"\n  [FAIL] {exc}")
        sys.exit(1)

    review = build_review(plan)
    print_review(review)

    if args.output is not None:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(review, indent=2, default=str),
            encoding="utf-8",
        )

    if review["review_result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
