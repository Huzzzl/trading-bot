"""
tools/live_shadow_screen_review.py
------------------------------------
Read-only artifact review CLI for live shadow symbol screen outputs.

Usage::

    python -m src.tools.live_shadow_screen_review \\
        --report output/live_shadow_symbol_screen/live_shadow_symbol_screen_report.json \\
        --csv    output/live_shadow_symbol_screen/live_shadow_symbol_screen.csv

Goal
----
Read the JSON report and CSV summary written by ``live_shadow_screen_symbols
--write-report`` and produce a concise operator summary: overall status,
suitable symbols, blocked symbols grouped by reason, and suggested next actions.

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

def parse_report(path: Path) -> dict[str, Any]:
    """Read and return the JSON report.  Raises on missing file or bad JSON."""
    if not path.exists():
        raise FileNotFoundError(f"report not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def parse_csv(path: Path) -> list[dict[str, str]]:
    """Read and return the symbol screen CSV as a list of row dicts.

    Raises on missing file or unreadable CSV.
    """
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        raise ValueError(f"could not read CSV {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _is_zero(value: Any) -> bool:
    if value is None:
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def classify_rows(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """Classify each symbol row into one of four buckets.

    Returns a dict with keys:
        suitable          - PASS symbols
        no_candidate      - FAIL with "no candidates" blocker
        sizing_blocked    - FAIL due to sizing/notional
        position_blocked  - FAIL due to existing position
        order_blocked     - FAIL due to existing open order
        other_blocked     - FAIL with unrecognised blocker
    """
    result: dict[str, list[str]] = {
        "suitable":         [],
        "no_candidate":     [],
        "sizing_blocked":   [],
        "position_blocked": [],
        "order_blocked":    [],
        "other_blocked":    [],
    }
    for row in rows:
        symbol  = row.get("symbol", "?")
        status  = row.get("best_status", "")
        blocker = row.get("blocker_summary", "").lower()

        if status == "PASS":
            result["suitable"].append(symbol)
        elif "no candidates" in blocker:
            result["no_candidate"].append(symbol)
        elif "fail sizing" in blocker or "notional" in blocker:
            result["sizing_blocked"].append(symbol)
        elif "position" in blocker:
            result["position_blocked"].append(symbol)
        elif "open order" in blocker or "order" in blocker:
            result["order_blocked"].append(symbol)
        else:
            result["other_blocked"].append(symbol)
    return result


def find_account_warnings(report: dict[str, Any]) -> list[str]:
    """Return human-readable warnings derived from the account snapshot."""
    warnings: list[str] = []
    account = report.get("account", {})
    if _is_zero(account.get("buying_power")):
        warnings.append("buying_power is 0")
    if _is_zero(account.get("portfolio_value")):
        warnings.append("portfolio_value is 0")
    if account.get("trading_blocked"):
        warnings.append("trading_blocked=True")
    if account.get("account_blocked"):
        warnings.append("account_blocked=True")
    if account.get("error"):
        warnings.append(f"account error: {account['error']}")
    return warnings


def suggest_actions(
    classification: dict[str, list[str]],
    account_warnings: list[str],
    overall_result: str,
) -> list[str]:
    """Return a prioritised list of suggested next actions."""
    actions: list[str] = []

    zero_funds = any("buying_power is 0" in w or "portfolio_value is 0" in w
                     for w in account_warnings)
    if zero_funds:
        actions.append(
            "Fund/activate the live account before any live submit work."
        )

    if not classification["suitable"]:
        actions.append(
            "No symbols currently suitable under current live sizing limits."
        )

    if classification["sizing_blocked"]:
        actions.append(
            "Do not raise live_max_notional without a full funding and risk review; "
            "consider lower-priced symbols instead."
        )

    if classification["no_candidate"]:
        actions.append(
            "Rerun on another signal day or expand the strategy universe to generate candidates."
        )

    if classification["position_blocked"]:
        syms = ", ".join(classification["position_blocked"])
        actions.append(
            f"Close existing live position(s) in {syms} manually before rerunning."
        )

    if classification["order_blocked"]:
        syms = ", ".join(classification["order_blocked"])
        actions.append(
            f"Cancel existing live open order(s) for {syms} manually before rerunning."
        )

    if not actions:
        actions.append(
            "All suitable symbols passed — review sizing before any live submit work."
        )

    return actions


def build_summary(
    report: dict[str, Any],
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Return a structured summary dict for printing and testing."""
    overall_result  = report.get("overall_result", "UNKNOWN")
    symbols_checked = report.get("symbols", [])
    classification  = classify_rows(rows)
    account_warnings = find_account_warnings(report)
    actions          = suggest_actions(classification, account_warnings, overall_result)

    result = (
        "PASS"
        if overall_result == "PASS" and classification["suitable"]
        else "FAIL"
    )

    return {
        "overall_result":      overall_result,
        "result":              result,
        "symbols_checked":     symbols_checked,
        "suitable_count":      len(classification["suitable"]),
        "suitable_symbols":    classification["suitable"],
        "no_candidate_symbols":  classification["no_candidate"],
        "sizing_blocked_symbols": classification["sizing_blocked"],
        "position_blocked_symbols": classification["position_blocked"],
        "order_blocked_symbols":   classification["order_blocked"],
        "other_blocked_symbols":   classification["other_blocked"],
        "account_warnings":    account_warnings,
        "suggested_actions":   actions,
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_summary(summary: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live Shadow Symbol Screen Review ===")
    print(f"  overall_status   : {summary['overall_result']}")
    print(f"  symbols_checked  : {', '.join(summary['symbols_checked']) or '(none)'}")
    print(f"  suitable_count   : {summary['suitable_count']}/{len(summary['symbols_checked'])}")

    print()
    print("  Suitable symbols:")
    if summary["suitable_symbols"]:
        print(f"    {', '.join(summary['suitable_symbols'])}")
    else:
        print("    (none)")

    if summary["no_candidate_symbols"]:
        print()
        print("  No-candidate symbols:")
        print(f"    {', '.join(summary['no_candidate_symbols'])}")

    if summary["sizing_blocked_symbols"]:
        print()
        print("  Sizing-blocked symbols (notional cap exceeded):")
        print(f"    {', '.join(summary['sizing_blocked_symbols'])}")

    if summary["position_blocked_symbols"]:
        print()
        print("  Position-blocked symbols (live position exists):")
        print(f"    {', '.join(summary['position_blocked_symbols'])}")

    if summary["order_blocked_symbols"]:
        print()
        print("  Order-blocked symbols (live open order exists):")
        print(f"    {', '.join(summary['order_blocked_symbols'])}")

    if summary["other_blocked_symbols"]:
        print()
        print("  Other blocked symbols:")
        print(f"    {', '.join(summary['other_blocked_symbols'])}")

    if summary["account_warnings"]:
        print()
        print("  Account warnings:")
        for w in summary["account_warnings"]:
            print(f"    ~ {w}")

    print()
    print("  Suggested actions:")
    for action in summary["suggested_actions"]:
        print(f"    > {action}")

    print()
    print(f"  RESULT: {summary['result']}")
    print("=" * 42)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_shadow_screen_review",
        description=(
            "Read-only review of live shadow symbol screen artifacts. "
            "Summarises overall status, suitable symbols, blocked symbols, "
            "and suggested actions. "
            "No credentials required. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to live_shadow_symbol_screen_report.json",
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="Path to live_shadow_symbol_screen.csv",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    csv_path    = Path(args.csv)

    try:
        report = parse_report(report_path)
        rows   = parse_csv(csv_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)

    summary = build_summary(report, rows)
    print_summary(summary)

    if summary["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
