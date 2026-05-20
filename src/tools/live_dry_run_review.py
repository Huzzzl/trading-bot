"""
tools/live_dry_run_review.py
-----------------------------
Read-only artifact review CLI for live dry-run intent outputs.

Usage::

    python -m src.tools.live_dry_run_review \\
        --summary output/live_dry_run_intents/live_dry_run_summary.json \\
        --intents output/live_dry_run_intents/live_order_intents.csv

Goal
----
Parse the artifacts written by ``live_dry_run_intents`` and produce a
concise operator summary: decision, sizing stats, safety flag check,
and a final PASS / FAIL review result.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes any file.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_summary(path: Path) -> dict[str, Any]:
    """Read and return live_dry_run_summary.json.  Raises on missing or bad JSON."""
    if not path.exists():
        raise FileNotFoundError(f"summary not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def parse_intents(path: Path) -> list[dict[str, str]]:
    """Read and return live_order_intents.csv as a list of row dicts.

    Raises on missing file or unreadable CSV.
    """
    if not path.exists():
        raise FileNotFoundError(f"intents CSV not found: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        raise ValueError(f"could not read CSV {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def _is_truthy(value: Any) -> bool:
    """Return True for True, 'true', 'True', 1, '1'; False otherwise."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False


def check_safety_flags(
    summary: dict[str, Any],
    intents: list[dict[str, str]],
) -> list[str]:
    """Return a list of safety violations found in the artifacts.

    Any non-empty return means the artifacts are inconsistent or dangerous.
    """
    violations: list[str] = []

    # Summary-level flags
    if _is_truthy(summary.get("submit_allowed")):
        violations.append("summary.submit_allowed is truthy — artifact may be corrupted")
    if not _is_truthy(summary.get("dry_run_only")):
        violations.append("summary.dry_run_only is not truthy — artifact may be corrupted")

    # Per-row flags
    for i, row in enumerate(intents):
        if _is_truthy(row.get("submit_allowed")):
            violations.append(
                f"row {i}: submit_allowed is truthy for {row.get('symbol', '?')} "
                f"({row.get('side', '?')} {row.get('order_type', '?')})"
            )
        if not _is_truthy(row.get("dry_run_only")):
            violations.append(
                f"row {i}: dry_run_only is not truthy for {row.get('symbol', '?')}"
            )

    return violations


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_sizing_stats(intents: list[dict[str, str]]) -> dict[str, Any]:
    """Return sizing stats derived from the intent rows."""
    if not intents:
        return {
            "sizing_mode":           None,
            "notional_per_order":    None,
            "min_effective_quantity": None,
            "max_effective_quantity": None,
        }

    sizing_mode = intents[0].get("live_sizing_mode", "")

    notionals: list[float] = []
    for row in intents:
        v = row.get("effective_notional")
        if v not in (None, "", "None"):
            try:
                notionals.append(float(v))
            except (TypeError, ValueError):
                pass
    notional_per_order = notionals[0] if notionals else None

    quantities: list[float] = []
    for row in intents:
        v = row.get("effective_quantity")
        if v not in (None, "", "None"):
            try:
                quantities.append(float(v))
            except (TypeError, ValueError):
                pass

    return {
        "sizing_mode":            sizing_mode,
        "notional_per_order":     notional_per_order,
        "min_effective_quantity": min(quantities) if quantities else None,
        "max_effective_quantity": max(quantities) if quantities else None,
    }


def build_review(
    summary: dict[str, Any],
    intents: list[dict[str, str]],
) -> dict[str, Any]:
    """Return a structured review dict for printing and testing."""
    violations = check_safety_flags(summary, intents)
    stats      = compute_sizing_stats(intents)

    fail_rows = [r for r in intents if r.get("sizing_status") == "FAIL"]

    # review_result: PASS only when everything is clean
    if (
        summary.get("decision") == "GO"
        and not violations
        and not fail_rows
    ):
        review_result = "PASS"
    else:
        review_result = "FAIL"

    blockers = list(summary.get("top_blockers", []))
    for v in violations:
        if v not in blockers:
            blockers.append(v)

    return {
        "decision":              summary.get("decision", "?"),
        "symbol":                summary.get("symbol", "?"),
        "intent_count":          summary.get("intent_count", 0),
        "pass_count":            summary.get("pass_count", 0),
        "fail_count":            summary.get("fail_count", 0),
        "dry_run_only_all":      not any(
            not _is_truthy(r.get("dry_run_only")) for r in intents
        ) if intents else _is_truthy(summary.get("dry_run_only")),
        "submit_allowed_any":    any(
            _is_truthy(r.get("submit_allowed")) for r in intents
        ),
        "sizing_mode":           stats["sizing_mode"],
        "notional_per_order":    stats["notional_per_order"],
        "min_effective_quantity": stats["min_effective_quantity"],
        "max_effective_quantity": stats["max_effective_quantity"],
        "top_blockers":          blockers[:5],
        "violations":            violations,
        "review_result":         review_result,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_review(review: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live Dry-Run Review ===")
    print(f"  decision              : {review['decision']}")
    print(f"  symbol                : {review['symbol']}")
    print(f"  intent_count          : {review['intent_count']}")
    print(f"  pass_count            : {review['pass_count']}")
    print(f"  fail_count            : {review['fail_count']}")
    print(f"  dry_run_only_all      : {review['dry_run_only_all']}")
    print(f"  submit_allowed_any    : {review['submit_allowed_any']}")
    print(f"  sizing_mode           : {review['sizing_mode'] or '(none)'}")
    print(f"  notional_per_order    : {review['notional_per_order'] or '(none)'}")
    print(f"  min_effective_quantity: {review['min_effective_quantity'] or '(none)'}")
    print(f"  max_effective_quantity: {review['max_effective_quantity'] or '(none)'}")

    print()
    if review["top_blockers"]:
        print("  Top blockers:")
        for b in review["top_blockers"]:
            print(f"    ! {b}")
    else:
        print("  Top blockers: (none)")

    if review["violations"]:
        print()
        print("  Safety violations:")
        for v in review["violations"]:
            print(f"    !! {v}")

    print()
    print(f"  RESULT: {review['review_result']}")
    print("=" * 28)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_dry_run_review",
        description=(
            "Read-only review of live dry-run intent artifacts. "
            "Checks safety flags, sizing stats, and decision. "
            "No credentials required. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--summary",
        required=True,
        help="Path to live_dry_run_summary.json",
    )
    parser.add_argument(
        "--intents",
        required=True,
        help="Path to live_order_intents.csv",
    )
    args = parser.parse_args(argv)

    summary_path = Path(args.summary)
    intents_path = Path(args.intents)

    try:
        summary = parse_summary(summary_path)
        intents = parse_intents(intents_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)

    review = build_review(summary, intents)
    print_review(review)

    if review["review_result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
