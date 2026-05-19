"""
tools/live_readiness_gate.py
------------------------------
Unified GO / NO-GO live-readiness gate.

Usage::

    python -m src.tools.live_readiness_gate \\
        --config  config/settings.paper.local.yaml \\
        --output-dir output/live_readiness_gate

    # Optional: append a snapshot row to a history CSV
    python -m src.tools.live_readiness_gate \\
        --config  config/settings.paper.local.yaml \\
        --output-dir output/live_readiness_gate \\
        --append-history output/live_readiness_history.csv

Goal
----
Run all live-readiness checks in one command and produce a single GO / NO-GO
decision.  Stages run in order; all stages always run (no early exit) so the
operator gets a full picture.

Stages
------
1. account_check        — credentials + live account health (buying_power, blocks)
2. shadow_preflight     — single-symbol strategy preview + live account state
3. shadow_review        — review of preflight artifacts
4. symbol_screen        — multi-symbol live sizing screen
5. symbol_screen_review — review of symbol-screen artifacts

Decision
--------
GO   — all five stages PASS
NO-GO — any stage is WARN or FAIL

What it never does
------------------
* Never calls ``submit_order`` or ``cancel_order``.
* Never writes a ledger row.
* Never reads paper credentials (``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``).
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.live_account_check import _make_live_client, check_credentials
from src.tools.live_shadow_preflight import (
    _run_strategy_preview,
    check_candidates,
    check_live_account,
    check_live_orders,
    check_live_position,
    check_live_sizing,
    write_report as _write_preflight_report,
)
from src.tools.live_shadow_review import (
    build_summary as _build_preflight_summary,
    parse_candidates as _parse_preflight_candidates,
    parse_report as _parse_preflight_report,
)
from src.tools.live_shadow_screen_review import (
    build_summary as _build_screen_summary,
    parse_csv as _parse_screen_csv,
    parse_report as _parse_screen_report,
)
from src.tools.live_shadow_screen_symbols import (
    _normalize_symbols,
    compute_overall,
    read_account,
    screen_symbol,
    write_screen_report as _write_screen_report,
)
from src.tools.paper_status import check_config

_STAGE_NAMES = (
    "account_check",
    "shadow_preflight",
    "shadow_review",
    "symbol_screen",
    "symbol_screen_review",
)

_FAIL_ALL = "FAIL"


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def _stage_account_check(client: Any) -> dict[str, Any]:
    """Stage 1: live account health."""
    result = check_live_account(client)
    status = result["status"]
    blockers = [result.get("detail", "")] if status in ("WARN", "FAIL") else []
    return {"status": status, "blockers": blockers}


def _stage_preflight(
    cfg: Any,
    client: Any,
    symbol: str,
    output_dir: Path,
    config_path: str,
) -> dict[str, Any]:
    """Stage 2: single-symbol shadow preflight + artifact write."""
    checks: list[dict[str, Any]] = []
    all_statuses: list[str] = []
    intents: list = []

    try:
        intents = _run_strategy_preview(cfg, symbol)
    except Exception as exc:
        checks.append({"label": "strategy_preview", "status": "FAIL",
                       "detail": f"engine error — {exc}"})
        all_statuses.append("FAIL")

    if not any(c["label"] == "strategy_preview" for c in checks):
        cand = check_candidates(intents, symbol)
        checks.append(cand)
        all_statuses.append(cand["status"])
        if cand["status"] != "FAIL":
            sizing = check_live_sizing(intents, cfg)
            checks.append(sizing)
            all_statuses.append(sizing["status"])

    for fn, label in (
        (lambda: check_live_account(client),           "live_account"),
        (lambda: check_live_position(client, symbol),  "live_position"),
        (lambda: check_live_orders(client, symbol),    "live_orders"),
    ):
        r = fn()
        checks.append(r)
        all_statuses.append(r["status"])

    final_status = (
        "FAIL" if "FAIL" in all_statuses else
        "WARN" if "WARN" in all_statuses else
        "PASS"
    )

    json_path, csv_path = _write_preflight_report(
        output_dir=output_dir,
        symbol=symbol,
        config_path=config_path,
        checks=checks,
        intents=intents,
        cfg=cfg,
        final_status=final_status,
    )

    blockers = [c.get("detail", "") for c in checks if c["status"] == "FAIL"]
    return {
        "status":    final_status,
        "json_path": json_path,
        "csv_path":  csv_path,
        "blockers":  blockers,
    }


def _stage_preflight_review(json_path: Path, csv_path: Path) -> dict[str, Any]:
    """Stage 3: review of preflight artifacts."""
    try:
        report     = _parse_preflight_report(json_path)
        candidates = _parse_preflight_candidates(csv_path)
        summary    = _build_preflight_summary(report, candidates)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "FAIL", "blockers": [str(exc)]}

    blockers = [b["message"] for b in summary["blockers"]]
    return {"status": summary["result"], "blockers": blockers}


def _stage_symbol_screen(
    cfg: Any,
    client: Any,
    output_dir: Path,
) -> dict[str, Any]:
    """Stage 4: multi-symbol live sizing screen + artifact write."""
    symbols = _normalize_symbols(cfg.execution.live_shadow_screen_symbols)
    account = read_account(client)

    symbol_results: list[dict] = []
    for sym in symbols:
        try:
            intents = _run_strategy_preview(cfg, sym)
        except Exception:
            intents = []
        symbol_results.append(screen_symbol(sym, intents, account, cfg))

    overall = compute_overall(True, account, symbol_results)

    json_path, csv_path = _write_screen_report(
        output_dir=output_dir,
        symbols=symbols,
        account=account,
        symbol_results=symbol_results,
        overall_result=overall,
    )

    blockers = [
        f"{r['symbol']}: {r['blocker_summary']}"
        for r in symbol_results
        if r["best_status"] == "FAIL" and r.get("blocker_summary")
    ]
    return {
        "status":    overall,
        "json_path": json_path,
        "csv_path":  csv_path,
        "blockers":  blockers[:3],
    }


def _stage_symbol_screen_review(json_path: Path, csv_path: Path) -> dict[str, Any]:
    """Stage 5: review of symbol-screen artifacts."""
    try:
        report  = _parse_screen_report(json_path)
        rows    = _parse_screen_csv(csv_path)
        summary = _build_screen_summary(report, rows)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "FAIL", "blockers": [str(exc)]}

    return {"status": summary["result"], "blockers": summary["suggested_actions"][:3]}


# ---------------------------------------------------------------------------
# Decision + reporting
# ---------------------------------------------------------------------------

def compute_decision(stages: dict[str, str]) -> str:
    """GO only when every stage is PASS."""
    return "GO" if all(s == "PASS" for s in stages.values()) else "NO-GO"


def _trim_blocker(text: str, max_len: int = 120) -> str:
    """Strip verbose field-dump prefix from detail strings and truncate."""
    # check_live_* detail strings use " — " to separate field dump from summary
    if " — " in text:
        text = text.split(" — ", 1)[-1]
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def collect_top_blockers(
    stage_results: dict[str, dict[str, Any]],
) -> list[str]:
    """Return up to 5 blockers, preferring compacted review-stage summaries.

    Priority rules:
    - shadow_review blockers outrank raw shadow_preflight sizing detail.
    - symbol_screen_review suggested actions outrank raw symbol_screen detail.
    - account_check detail is trimmed to its meaningful suffix (after " — ").
    """
    out: list[str] = []

    # Stage 1: account_check — trim to meaningful part
    for b in stage_results.get("account_check", {}).get("blockers", [])[:1]:
        if b:
            out.append(f"[account_check] {_trim_blocker(b)}")

    # Stages 2+3: shadow_review preferred; fall back to trimmed shadow_preflight
    review_bs = stage_results.get("shadow_review", {}).get("blockers", [])
    if review_bs:
        for b in review_bs[:2]:
            if b:
                out.append(f"[shadow_review] {b}")
    else:
        for b in stage_results.get("shadow_preflight", {}).get("blockers", [])[:1]:
            if b:
                out.append(f"[shadow_preflight] {_trim_blocker(b)}")

    # Stages 4+5: symbol_screen_review preferred; fall back to trimmed symbol_screen
    screen_review_bs = stage_results.get("symbol_screen_review", {}).get("blockers", [])
    if screen_review_bs:
        for b in screen_review_bs[:2]:
            if b:
                out.append(f"[symbol_screen_review] {b}")
    else:
        for b in stage_results.get("symbol_screen", {}).get("blockers", [])[:1]:
            if b:
                out.append(f"[symbol_screen] {_trim_blocker(b)}")

    return out[:5]


def print_gate_report(
    stages: dict[str, str],
    decision: str,
    top_blockers: list[str],
) -> None:
    print("\n=== Live Readiness Gate ===")
    for name, status in stages.items():
        print(f"  {name:<24}: {status}")
    print()
    print(f"  decision: {decision}")
    if top_blockers:
        print("  top_blockers:")
        for b in top_blockers:
            print(f"    ! {b}")
    print("=" * 28)


def write_gate_report(
    output_dir: Path,
    stages: dict[str, str],
    decision: str,
    top_blockers: list[str],
    checked_at_utc: str | None = None,
) -> Path:
    """Write live_readiness_gate_report.json to output_dir."""
    report = {
        "checked_at_utc": checked_at_utc or datetime.now(timezone.utc).isoformat(),
        "decision":       decision,
        "stages":         stages,
        "top_blockers":   top_blockers,
    }
    path = output_dir / "live_readiness_gate_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


_HISTORY_COLUMNS = [
    "checked_at_utc",
    "decision",
    "account_check",
    "shadow_preflight",
    "shadow_review",
    "symbol_screen",
    "symbol_screen_review",
    "top_blockers",
]


def append_history_row(
    history_path: Path,
    checked_at_utc: str,
    decision: str,
    stages: dict[str, str],
    top_blockers: list[str],
) -> None:
    """Append one snapshot row to the history CSV.

    Creates the file (with header) if it does not exist.
    Creates parent directories as needed.
    Never raises — silently skips on any write error.
    """
    history_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not history_path.exists()
    try:
        with history_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_HISTORY_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow({
                "checked_at_utc":      checked_at_utc,
                "decision":            decision,
                "account_check":       stages.get("account_check", ""),
                "shadow_preflight":    stages.get("shadow_preflight", ""),
                "shadow_review":       stages.get("shadow_review", ""),
                "symbol_screen":       stages.get("symbol_screen", ""),
                "symbol_screen_review": stages.get("symbol_screen_review", ""),
                "top_blockers":        " | ".join(top_blockers),
            })
    except Exception:
        pass


def _fail_all(reason: str, output_dir: Path) -> None:
    """Mark all stages FAIL, write gate report, print, and exit 1."""
    stages = {name: "FAIL" for name in _STAGE_NAMES}
    top_blockers = [f"[all] {reason}"]
    print_gate_report(stages, "NO-GO", top_blockers)
    write_gate_report(output_dir, stages, "NO-GO", top_blockers)
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.live_readiness_gate",
        description=(
            "Unified GO/NO-GO live-readiness gate. "
            "Runs all live-readiness checks in one command. "
            "Never submits or cancels orders. "
            "Requires ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY."
        ),
    )
    parser.add_argument("--config",      required=True, help="Path to settings YAML")
    parser.add_argument("--output-dir",  required=True, help="Output directory for audit artifacts")
    parser.add_argument(
        "--append-history", default=None, metavar="CSV_PATH",
        help="Append a one-line snapshot to this CSV after every run (created if missing).",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Config ---
    config_result, cfg = check_config(Path(args.config))
    if config_result["status"] == "FAIL":
        print(f"\n[FAIL] config: {config_result['detail']}")
        _fail_all(f"config: {config_result['detail']}", output_dir)

    # --- Credentials ---
    cred_result, creds = check_credentials()
    if cred_result["status"] == "FAIL":
        _fail_all(f"credentials: {cred_result['detail']}", output_dir)

    # --- Live client ---
    try:
        client = _make_live_client(*creds)
    except Exception as exc:
        _fail_all(f"live client: {exc}", output_dir)

    # --- Run stages ---
    stage_results: dict[str, dict[str, Any]] = {}

    # 1. Account check
    stage_results["account_check"] = _stage_account_check(client)

    # 2. Shadow preflight
    preflight_symbol = cfg.symbols[0] if cfg.symbols else "SPY"
    stage_results["shadow_preflight"] = _stage_preflight(
        cfg, client, preflight_symbol, output_dir, args.config,
    )

    # 3. Shadow review
    stage_results["shadow_review"] = _stage_preflight_review(
        stage_results["shadow_preflight"]["json_path"],
        stage_results["shadow_preflight"]["csv_path"],
    )

    # 4. Symbol screen
    stage_results["symbol_screen"] = _stage_symbol_screen(cfg, client, output_dir)

    # 5. Symbol screen review
    stage_results["symbol_screen_review"] = _stage_symbol_screen_review(
        stage_results["symbol_screen"]["json_path"],
        stage_results["symbol_screen"]["csv_path"],
    )

    # --- Decision ---
    stages     = {name: res["status"] for name, res in stage_results.items()}
    decision   = compute_decision(stages)
    top_blockers = collect_top_blockers(stage_results)

    checked_at_utc = datetime.now(timezone.utc).isoformat()
    print_gate_report(stages, decision, top_blockers)
    gate_path = write_gate_report(output_dir, stages, decision, top_blockers, checked_at_utc)
    print(f"  gate report: {gate_path}")

    if args.append_history:
        history_path = Path(args.append_history)
        append_history_row(history_path, checked_at_utc, decision, stages, top_blockers)
        print(f"  history    : {history_path}")

    if decision != "GO":
        sys.exit(1)


if __name__ == "__main__":
    main()
