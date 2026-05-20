"""
tools/live_operator_release_checklist.py
-----------------------------------------
Offline CLI that reads pre-submit checklist, dry-run plan, and optional plan-review
artifacts and produces a RELEASE_READY / NOT_RELEASE_READY verdict.

Usage::

    python -m src.tools.live_operator_release_checklist \\
        --config   config/settings.paper.local.yaml \\
        --pre-submit  output/live_pre_submit_checklist/live_pre_submit_checklist.json \\
        --submit-plan output/live_submit_dry_run/live_submit_dry_run_plan.json \\
        [--plan-review output/live_submit_dry_run/live_submit_plan_review.json] \\
        --output   output/live_operator_release_checklist.json

Result
------
RELEASE_READY   — all 8 conditions pass.
NOT_RELEASE_READY — any condition fails.

The output JSON includes manual approval fields (all null / false / empty)
that an operator must fill in before opening a real-submit implementation PR.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file contains malformed JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_pre_submit_checklist(checklist: dict[str, Any]) -> list[str]:
    """Return a list of violations from the pre-submit checklist artifact."""
    violations: list[str] = []
    final = checklist.get("final_result", "")
    if str(final).strip().upper() != "READY":
        violations.append(
            f"pre_submit_checklist.final_result={final!r} — must be 'READY'"
        )
    return violations


_PLAN_SAFE_TRUE = [
    "live_submit_dry_run",
    "live_kill_switch_enabled",
]
_PLAN_SAFE_FALSE = [
    "live_trading_enabled",
    "submit_order_called",
    "submit_allowed",
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


def _validate_submit_plan(plan: dict[str, Any]) -> list[str]:
    """Return a list of violations from the submit plan artifact (8 checks)."""
    violations: list[str] = []

    # 1. live_submit_dry_run must be truthy
    if not _is_truthy(plan.get("live_submit_dry_run", False)):
        violations.append(
            f"live_submit_dry_run={plan.get('live_submit_dry_run')!r} — must be true"
        )

    # 2. live_trading_enabled must be falsy
    if _is_truthy(plan.get("live_trading_enabled", False)):
        violations.append(
            f"live_trading_enabled={plan.get('live_trading_enabled')!r} — must be false"
        )

    # 3. live_kill_switch_enabled must be truthy
    if not _is_truthy(plan.get("live_kill_switch_enabled", False)):
        violations.append(
            f"live_kill_switch_enabled={plan.get('live_kill_switch_enabled')!r} — must be true"
        )

    # 4. submit_order_called must be falsy
    if _is_truthy(plan.get("submit_order_called", False)):
        violations.append(
            f"submit_order_called={plan.get('submit_order_called')!r} — must be false"
        )

    # 5. submit_allowed must be falsy
    if _is_truthy(plan.get("submit_allowed", False)):
        violations.append(
            f"submit_allowed={plan.get('submit_allowed')!r} — must be false"
        )

    # 6. final_action must match exactly
    final_action = str(plan.get("final_action", "")).strip()
    if final_action != _EXPECTED_FINAL_ACTION:
        violations.append(
            f"final_action={plan.get('final_action')!r} — "
            f"must be {_EXPECTED_FINAL_ACTION!r}"
        )

    # 7. live_sizing_mode must be "notional"
    sizing_mode = str(plan.get("live_sizing_mode", "")).strip().lower()
    if sizing_mode != "notional":
        violations.append(
            f"live_sizing_mode={plan.get('live_sizing_mode')!r} — must be 'notional'"
        )

    # 8. effective_notional must be > 0
    notional = _to_float(plan.get("effective_notional"))
    if notional is None or notional <= 0:
        violations.append(
            f"effective_notional={plan.get('effective_notional')!r} — must be > 0"
        )

    return violations


def _validate_plan_review(review: dict[str, Any]) -> list[str]:
    """Return violations from the optional plan-review artifact."""
    violations: list[str] = []
    result = str(review.get("review_result", "")).strip().upper()
    if result != "PASS":
        violations.append(
            f"plan_review.review_result={review.get('review_result')!r} — must be 'PASS'"
        )
    return violations


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_release_checklist(
    checklist: dict[str, Any],
    plan: dict[str, Any],
    plan_review: dict[str, Any] | None,
) -> dict[str, Any]:
    """Produce the release checklist result dict."""
    violations: list[str] = []
    violations.extend(_validate_pre_submit_checklist(checklist))
    violations.extend(_validate_submit_plan(plan))
    if plan_review is not None:
        violations.extend(_validate_plan_review(plan_review))

    result = "RELEASE_READY" if not violations else "NOT_RELEASE_READY"
    return {
        "checked_at_utc":                 datetime.now(tz=timezone.utc).isoformat(),
        "pre_submit_checklist_result":     checklist.get("final_result"),
        "submit_plan_symbol":             plan.get("symbol"),
        "submit_plan_side":               plan.get("side"),
        "submit_plan_live_sizing_mode":   plan.get("live_sizing_mode"),
        "submit_plan_effective_notional": plan.get("effective_notional"),
        "submit_plan_final_action":       plan.get("final_action"),
        "plan_review_result":             plan_review.get("review_result") if plan_review else None,
        "violations":                     violations,
        "release_result":                 result,
        # Manual approval fields — must be filled by a human operator
        "operator_name":                  None,
        "approval_timestamp_utc":         None,
        "approval_for_real_submit_pr":    False,
        "notes":                          "",
    }


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_release_checklist(output_path: Path, result: dict[str, Any]) -> Path:
    """Write the release checklist JSON to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_release_checklist(result: dict[str, Any]) -> None:
    """Print the operator release checklist summary."""
    print("\n=== Live Operator Release Checklist ===")
    print(f"  pre_submit_checklist_result    : {result['pre_submit_checklist_result']}")
    print(f"  submit_plan_symbol             : {result['submit_plan_symbol']}")
    print(f"  submit_plan_side               : {result['submit_plan_side']}")
    print(f"  submit_plan_live_sizing_mode   : {result['submit_plan_live_sizing_mode']}")
    print(f"  submit_plan_effective_notional : {result['submit_plan_effective_notional']}")
    print(f"  submit_plan_final_action       : {result['submit_plan_final_action']}")
    print(f"  plan_review_result             : {result['plan_review_result']}")
    print()
    if result["violations"]:
        for v in result["violations"]:
            print(f"  ! {v}")
        print()
    print(f"  release_result: {result['release_result']}")
    print()
    print("  --- Manual approval (operator must complete) ---")
    print(f"  operator_name               : {result['operator_name']}")
    print(f"  approval_timestamp_utc      : {result['approval_timestamp_utc']}")
    print(f"  approval_for_real_submit_pr : {result['approval_for_real_submit_pr']}")
    print(f"  notes                       : {result['notes']!r}")
    print("=" * 40)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_operator_release_checklist",
        description=(
            "Offline release checklist: reads pre-submit checklist, dry-run plan, "
            "and optional plan-review artifacts; produces RELEASE_READY / NOT_RELEASE_READY. "
            "No credentials required. Never calls Alpaca. Never submits orders."
        ),
    )
    parser.add_argument("--config", required=True, help="Path to settings YAML (unused offline; kept for consistency)")
    parser.add_argument("--pre-submit", required=True, dest="pre_submit", help="Path to live_pre_submit_checklist.json")
    parser.add_argument("--submit-plan", required=True, dest="submit_plan", help="Path to live_submit_dry_run_plan.json")
    parser.add_argument("--plan-review", required=False, dest="plan_review", default=None, help="Path to plan review JSON (optional)")
    parser.add_argument("--output", required=True, help="Path to write live_operator_release_checklist.json")
    args = parser.parse_args(argv)

    try:
        checklist = _read_json(Path(args.pre_submit))
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] pre-submit checklist: {exc}")
        sys.exit(1)

    try:
        plan = _read_json(Path(args.submit_plan))
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  [FAIL] submit plan: {exc}")
        sys.exit(1)

    plan_review: dict[str, Any] | None = None
    if args.plan_review is not None:
        try:
            plan_review = _read_json(Path(args.plan_review))
        except (FileNotFoundError, ValueError) as exc:
            print(f"\n  [FAIL] plan review: {exc}")
            sys.exit(1)

    result = build_release_checklist(checklist, plan, plan_review)
    print_release_checklist(result)

    output_path = write_release_checklist(Path(args.output), result)
    print(f"\n  artifact: {output_path}")

    if result["release_result"] != "RELEASE_READY":
        sys.exit(1)


if __name__ == "__main__":
    main()
