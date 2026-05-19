"""
tools/live_readiness_history_review.py
----------------------------------------
Read-only review CLI for the live readiness history CSV.

Usage::

    python -m src.tools.live_readiness_history_review \\
        --history output/live_readiness_history.csv

Goal
----
Read the CSV written by ``live_readiness_gate --append-history`` and
summarise the readiness trend over time: run counts, GO/NO-GO ratio,
recurring blockers, and a plain-English trend statement.

What it never does
------------------
* Never calls any Alpaca endpoint.
* Never reads credentials.
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes any file.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_STAGE_COLUMNS = (
    "account_check",
    "shadow_preflight",
    "shadow_review",
    "symbol_screen",
    "symbol_screen_review",
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_history(path: Path) -> list[dict[str, str]]:
    """Read and return history CSV rows.

    Raises FileNotFoundError on missing file, ValueError on empty or
    unreadable CSV.
    """
    if not path.exists():
        raise FileNotFoundError(f"history file not found: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        raise ValueError(f"could not read history CSV {path}: {exc}") from exc
    if not rows:
        raise ValueError(f"history CSV is empty (no data rows): {path}")
    return rows


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def count_decisions(rows: list[dict[str, str]]) -> tuple[int, int]:
    """Return (go_count, no_go_count)."""
    go     = sum(1 for r in rows if r.get("decision") == "GO")
    no_go  = sum(1 for r in rows if r.get("decision") != "GO")
    return go, no_go


def top_recurring_blockers(
    rows: list[dict[str, str]],
    top_n: int = 5,
) -> list[tuple[str, int]]:
    """Return up to top_n (phrase, count) pairs sorted by descending count.

    Blocker phrases are extracted by splitting the top_blockers field on
    " | " and stripping whitespace.  Empty phrases are ignored.
    """
    counter: Counter[str] = Counter()
    for row in rows:
        raw = row.get("top_blockers", "")
        for phrase in raw.split(" | "):
            phrase = phrase.strip()
            if phrase:
                counter[phrase] += 1
    return counter.most_common(top_n)


def latest_stage_statuses(row: dict[str, str]) -> dict[str, str]:
    """Extract per-stage statuses from the latest row."""
    return {col: row.get(col, "") for col in _STAGE_COLUMNS}


def trend_summary(rows: list[dict[str, str]]) -> str:
    """Return a plain-English readiness trend statement."""
    latest = rows[-1].get("decision", "")
    prior_go = any(r.get("decision") == "GO" for r in rows[:-1])

    if latest == "GO":
        return "Latest run is GO."
    if prior_go:
        return "Readiness regressed from GO to NO-GO."
    return "No GO observed yet."


def build_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Return a structured summary dict."""
    latest   = rows[-1]
    go, nogo = count_decisions(rows)
    blockers = top_recurring_blockers(rows)

    return {
        "total_runs":           len(rows),
        "latest_checked_at_utc": latest.get("checked_at_utc", ""),
        "latest_decision":      latest.get("decision", ""),
        "go_count":             go,
        "no_go_count":          nogo,
        "latest_stage_statuses": latest_stage_statuses(latest),
        "recurring_blockers":   blockers,
        "trend_summary":        trend_summary(rows),
    }


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------

def print_summary(summary: dict[str, Any]) -> None:
    """Print the operator review summary."""
    print("\n=== Live Readiness History Review ===")
    print(f"  total_runs             : {summary['total_runs']}")
    print(f"  latest_checked_at_utc  : {summary['latest_checked_at_utc']}")
    print(f"  latest_decision        : {summary['latest_decision']}")
    print(f"  go_count               : {summary['go_count']}")
    print(f"  no_go_count            : {summary['no_go_count']}")

    print()
    print("  latest_stage_statuses:")
    for stage, status in summary["latest_stage_statuses"].items():
        print(f"    {stage:<24}: {status or '(unknown)'}")

    print()
    print("  recurring_blockers (top 5):")
    if summary["recurring_blockers"]:
        for phrase, count in summary["recurring_blockers"]:
            print(f"    ({count}x) {phrase}")
    else:
        print("    (none)")

    print()
    print(f"  trend_summary          : {summary['trend_summary']}")
    print("=" * 42)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_readiness_history_review",
        description=(
            "Read-only review of the live readiness history CSV. "
            "Summarises trend, GO/NO-GO counts, and recurring blockers. "
            "No credentials required. Never calls Alpaca."
        ),
    )
    parser.add_argument(
        "--history",
        required=True,
        help="Path to live_readiness_history.csv",
    )
    args = parser.parse_args(argv)

    history_path = Path(args.history)

    try:
        rows = parse_history(history_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)

    summary = build_summary(rows)
    print_summary(summary)

    if summary["latest_decision"] != "GO":
        sys.exit(1)


if __name__ == "__main__":
    main()
