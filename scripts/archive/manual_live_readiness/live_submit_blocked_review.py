# ARCHIVED in PR R4. Historical manual/operator tool. Not part of active automated runtime.
# Moved from src/tools/live_submit_blocked_review.py. Not importable as src.tools.live_submit_blocked_review.
"""
tools/live_submit_blocked_review.py
-------------------------------------
Read-only review CLI for live_submit_executor blocked-path artifacts.

Usage::

    python -m src.tools.live_submit_blocked_review \\
        --report output/live_submit_executor/live_submit_blocked_report.json

Goal
----
Parse the ``live_submit_blocked_report.json`` written by
``maybe_execute_live_submit()`` and verify the executor failed closed safely.

Result
------
PASS — the executor failed closed:
  blocked=true, submit_order_called=false,
  block_guard non-empty, violations non-empty.

FAIL — any safety invariant is violated or the report is missing/malformed.

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

def parse_report(path: Path) -> dict[str, Any]:
    """Read and return the blocked report JSON.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When the file contains malformed JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"report not found: {path}")
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


def validate_report(report: dict[str, Any]) -> tuple[str, list[str]]:
    """Validate that the executor failed closed safely.

    Returns
    -------
    (result, violations)
        result     : "PASS" or "FAIL"
        violations : list of human-readable failure strings (empty on PASS)
    """
    violations: list[str] = []

    # blocked must be true
    if not _is_truthy(report.get("blocked", False)):
        violations.append(
            f"blocked={report.get('blocked')!r} — must be true"
        )

    # submit_order_called must be false
    if _is_truthy(report.get("submit_order_called", False)):
        violations.append(
            f"submit_order_called={report.get('submit_order_called')!r} — must be false"
        )

    # block_guard must be non-empty
    block_guard = str(report.get("block_guard") or "").strip()
    if not block_guard:
        violations.append(
            f"block_guard={report.get('block_guard')!r} — must be non-empty"
        )

    # violations must be a non-empty list
    report_violations = report.get("violations")
    if not isinstance(report_violations, list) or len(report_violations) == 0:
        violations.append(
            f"violations={report_violations!r} — must be a non-empty list"
        )

    result = "FAIL" if violations else "PASS"
    return result, violations


# ---------------------------------------------------------------------------
# Review builder
# ---------------------------------------------------------------------------

def build_review(report: dict[str, Any]) -> dict[str, Any]:
    """Return a structured review dict for printing and testing."""
    result, violations = validate_report(report)
    return {
        "symbol":               report.get("symbol", ""),
        "blocked":              report.get("blocked"),
        "block_guard":          report.get("block_guard", ""),
        "submit_order_called":  report.get("submit_order_called"),
        "violations":           report.get("violations", []),
        "review_result":        result,
        "review_violations":    violations,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_review(review: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live Submit Blocked Review ===")
    print(f"  symbol              : {review['symbol']}")
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
    print("=" * 34)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_submit_blocked_review",
        description=(
            "Read-only review of live_submit_executor blocked-path artifact. "
            "Verifies the executor failed closed safely. "
            "No credentials required. Never calls Alpaca. Never writes files."
        ),
    )
    parser.add_argument(
        "--report", required=True,
        help="Path to live_submit_blocked_report.json",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.report)

    try:
        report = parse_report(report_path)
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
