"""
tools/live_v2_executor_readiness_review.py
--------------------------------------------
Offline CLI that reviews a ``live_submit_blocked_report.json`` produced by
``maybe_execute_live_submit()`` after v2 approval artifacts are provided.

Usage::

    python -m src.tools.live_v2_executor_readiness_review \\
        --blocked-report output/live_submit_executor/live_submit_blocked_report.json

Goal
----
Verify that the v2 approval artifacts (``live_trading_approval.json`` and
``live_order_submission_approval.json``) were accepted by the executor and
that the executor is now blocked by ``config_safety`` -- the three default
config flags that remain safe until explicitly changed by the operator.

PASS conditions
---------------
All of the following must be true:

* ``blocked == true``
* ``submit_order_called == false``
* ``block_guard == "config_safety"``
* ``violations`` includes at least one of:
    - ``live_trading_enabled=false``
    - ``live_submit_dry_run=true``
    - ``live_kill_switch_enabled=true``

FAIL conditions
---------------
Any of the following:

* ``blocked == false``
* ``submit_order_called == true``
* ``block_guard`` is ``approval_artifact``   -- v2 approvals were not accepted
* ``block_guard`` is ``v2_trading_approval`` -- v2 trading artifact failed
* ``block_guard`` is ``v2_submission_approval`` -- v2 submission artifact failed
* ``block_guard`` is ``v2_cross_check``      -- cross-artifact check failed
* ``block_guard`` is anything other than ``config_safety``
* report is missing or malformed

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls submit_order or cancel_order.
* Never writes the live ledger.
* Never writes any file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REQUIRED_BLOCK_GUARD = "config_safety"
_CONFIG_SAFETY_VIOLATION_SUBSTRINGS = (
    "live_trading_enabled=false",
    "live_submit_dry_run=true",
    "live_kill_switch_enabled=true",
)

# Guards that indicate v2 approval artifacts were not accepted.
_V2_APPROVAL_GUARDS = frozenset({
    "approval_artifact",
    "v2_trading_approval",
    "v2_submission_approval",
    "v2_cross_check",
})


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def parse_blocked_report(path: Path) -> dict[str, Any]:
    """Read and return the blocked report JSON.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file contains malformed JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"blocked report not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def validate_readiness(report: dict[str, Any]) -> tuple[str, list[str]]:
    """Verify the executor accepted v2 approvals and is blocked at config_safety.

    Returns
    -------
    (result, violations)
        result     : "PASS" or "FAIL"
        violations : human-readable failure strings (empty on PASS)
    """
    violations: list[str] = []

    if not _is_truthy(report.get("blocked", False)):
        violations.append(
            f"blocked={report.get('blocked')!r} -- must be true"
        )

    if _is_truthy(report.get("submit_order_called", False)):
        violations.append(
            f"submit_order_called={report.get('submit_order_called')!r} -- must be false"
        )

    block_guard = str(report.get("block_guard") or "").strip()

    if block_guard in _V2_APPROVAL_GUARDS:
        violations.append(
            f"block_guard={block_guard!r} -- v2 approval artifacts were not accepted; "
            "executor blocked before reaching config_safety"
        )
    elif block_guard != _REQUIRED_BLOCK_GUARD:
        violations.append(
            f"block_guard={block_guard!r} -- must be {_REQUIRED_BLOCK_GUARD!r}; "
            "executor did not reach the config_safety guard"
        )

    report_violations = report.get("violations", [])
    if not isinstance(report_violations, list):
        report_violations = []

    violation_text = " ".join(str(v) for v in report_violations)
    config_safety_found = any(
        sub in violation_text for sub in _CONFIG_SAFETY_VIOLATION_SUBSTRINGS
    )
    if not config_safety_found:
        violations.append(
            "violations do not contain any expected config_safety flag "
            "(live_trading_enabled=false, live_submit_dry_run=true, "
            "or live_kill_switch_enabled=true)"
        )

    result = "FAIL" if violations else "PASS"
    return result, violations


# ---------------------------------------------------------------------------
# Review builder
# ---------------------------------------------------------------------------

def build_review(report: dict[str, Any]) -> dict[str, Any]:
    """Return a structured review dict for printing and testing."""
    result, review_violations = validate_readiness(report)
    return {
        "blocked":              report.get("blocked"),
        "block_guard":          report.get("block_guard", ""),
        "submit_order_called":  report.get("submit_order_called"),
        "violations":           report.get("violations", []),
        "review_result":        result,
        "review_violations":    review_violations,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_review(review: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live V2 Executor Readiness Review ===")
    print(f"  blocked             : {review['blocked']}")
    print(f"  block_guard         : {review['block_guard']}")
    print(f"  submit_order_called : {review['submit_order_called']}")
    print(f"  violations          : {review['violations']}")
    print()
    if review["review_violations"]:
        for v in review["review_violations"]:
            print(f"  ! {v}")
        print()
    print(f"  review_result: {review['review_result']}")
    print("=" * 42)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_v2_executor_readiness_review",
        description=(
            "Offline review of a live_submit_blocked_report.json produced after "
            "v2 approval artifacts are supplied to the executor. "
            "PASS only when blocked=true, submit_order_called=false, and "
            "block_guard=config_safety with at least one expected config flag. "
            "No credentials required. Never calls Alpaca. Never writes files."
        ),
    )
    parser.add_argument(
        "--blocked-report", required=True,
        help="Path to live_submit_blocked_report.json",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.blocked_report)

    try:
        report = parse_blocked_report(report_path)
    except FileNotFoundError as exc:
        print(f"\n  [FAIL] {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"\n  [FAIL] {exc}")
        sys.exit(1)

    review = build_review(report)
    print_review(review)

    if review["review_result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
