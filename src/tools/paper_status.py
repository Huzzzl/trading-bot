"""
tools/paper_status.py
----------------------
Read-only paper trading status / doctor CLI.

Usage::

    python -m src.tools.paper_status \\
        --config config/settings.paper.local.yaml \\
        --output-dir output/paper_smoke_check \\
        --ledger output/paper_execution_ledger.csv \\
        --replay-dir output/paper_submit_BT000035

What it reports
---------------
1. Config mode classification (buy_preview / buy_submit / close_preview /
   close_submit / disabled / backtest).
2. Config validation result (validate_paper_config).
3. Ledger path (present or missing) + last N rows (default 5).
4. Artifact presence in --output-dir.
5. Replay reconciliation summary (only when --replay-dir is given).
6. Final PASS / WARN / FAIL summary.

What it never does
------------------
* Never instantiates AlpacaBrokerAdapter.
* Never reads Alpaca credentials.
* Never calls submit_order or cancel_order.
* Never writes any file.
* Exit 0 for PASS, 1 for WARN or FAIL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Config mode classification
# ---------------------------------------------------------------------------

def classify_mode(execution) -> str:
    """Return a human-readable mode label for the execution config.

    Returns one of:
        ``"backtest"``, ``"disabled"``, ``"buy_preview"``, ``"buy_submit"``,
        ``"close_preview"``, ``"close_submit"``.
    """
    if execution.mode != "paper":
        return "backtest"
    if not execution.paper_trading_enabled:
        return "disabled"
    if execution.paper_close_positions_enabled:
        return "close_preview" if execution.paper_close_preview_only else "close_submit"
    return "buy_preview" if execution.paper_preview_only else "buy_submit"


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _result(label: str, status: str, detail: str = "") -> dict[str, Any]:
    """status is one of 'PASS', 'WARN', 'FAIL'."""
    return {"label": label, "status": status, "detail": detail}


def check_config(config_path: Path) -> tuple[dict[str, Any], Any]:
    """Load and validate config.  Returns (result_dict, cfg_or_None)."""
    try:
        from src.config.loader import load_config, validate_paper_config
        cfg = load_config(config_path)
        # validate_paper_config is already called by load_config; call explicitly
        # to surface any edge-case that might have been skipped.
        validate_paper_config(cfg.execution)
        mode_label = classify_mode(cfg.execution)
        return _result(
            "config",
            "PASS",
            f"mode={cfg.execution.mode} paper_trading_enabled={cfg.execution.paper_trading_enabled}"
            f" classification={mode_label}",
        ), cfg
    except Exception as exc:
        return _result("config", "FAIL", str(exc)), None


def check_ledger(ledger_path: Path, last_n: int = 5) -> dict[str, Any]:
    """Report ledger presence and last *last_n* rows."""
    from src.execution.paper_ledger import load_ledger
    if not ledger_path.exists():
        return _result("ledger", "WARN", f"missing: {ledger_path}")
    try:
        df = load_ledger(ledger_path)
        total = len(df)
        tail_rows = df.tail(last_n).to_dict(orient="records") if not df.empty else []
        detail = f"{total} row(s) total; showing last {min(last_n, total)}"
        return _result("ledger", "PASS", detail) | {"rows": tail_rows, "total_rows": total}
    except Exception as exc:
        return _result("ledger", "FAIL", str(exc))


def check_artifacts(output_dir: Path) -> dict[str, Any]:
    """Check which paper artifacts are present in *output_dir*."""
    _ARTIFACTS = [
        "paper_candidate_intents.csv",
        "paper_close_candidate_intents.csv",
        "order_intents.csv",
        "order_results.csv",
        "order_reconciliation.json",
    ]
    presence = {name: (output_dir / name).exists() for name in _ARTIFACTS}
    present  = [name for name, exists in presence.items() if exists]
    missing  = [name for name, exists in presence.items() if not exists]
    detail   = (
        f"{len(present)} present, {len(missing)} missing"
        + (f": {missing}" if missing else "")
    )
    return _result("artifacts", "PASS", detail) | {"presence": presence}


def check_replay(replay_dir: Path) -> dict[str, Any]:
    """Run offline reconciliation replay on *replay_dir* artifacts."""
    intents_path = replay_dir / "order_intents.csv"
    results_path = replay_dir / "order_results.csv"
    if not intents_path.exists() or not results_path.exists():
        return _result(
            "replay",
            "WARN",
            f"skipped — order_intents.csv or order_results.csv not found in {replay_dir}",
        )
    try:
        from src.tools.replay_order_reconciliation import replay
        recon  = replay(replay_dir)
        status = recon.get("overall_status", "UNKNOWN")
        mapped = "PASS" if status in ("PASS", "N/A") else "WARN"
        detail = (
            f"overall_status={status} "
            f"intent_count={recon.get('intent_count')} "
            f"result_count={recon.get('result_count')} "
            f"mismatch_count={recon.get('mismatch_count')}"
        )
        return _result("replay", mapped, detail) | {"reconciliation": recon}
    except Exception as exc:
        return _result("replay", "FAIL", str(exc))


# ---------------------------------------------------------------------------
# Output printer
# ---------------------------------------------------------------------------

def print_report(
    checks: list[dict[str, Any]],
    ledger_rows: list[dict] | None = None,
    final_status: str = "PASS",
) -> None:
    """Print a human-readable status report to stdout."""
    _ICON = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    print("\n=== Paper Status ===")
    for c in checks:
        icon   = _ICON.get(c["status"], f"[{c['status']}]")
        detail = f"  ({c['detail']})" if c.get("detail") else ""
        print(f"  {icon} {c['label']}{detail}")

        # Print artifact presence inline
        if c["label"] == "artifacts" and "presence" in c:
            for name, exists in c["presence"].items():
                marker = "  ✓" if exists else "  ✗"
                print(f"        {marker} {name}")

    # Print ledger tail rows
    if ledger_rows:
        print(f"\n  Last {len(ledger_rows)} ledger row(s):")
        cols = ["flow", "client_order_id", "symbol", "side", "status", "submitted_at"]
        for row in ledger_rows:
            parts = "  |  ".join(f"{c}={row.get(c, '')}" for c in cols)
            print(f"    {parts}")

    print()
    print(f"  RESULT: {final_status}")
    print("=" * 22)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.paper_status",
        description=(
            "Read-only paper trading status / doctor. "
            "No orders submitted. No Alpaca credentials required."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to paper settings YAML",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Run output directory to inspect for artifacts",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="Path to the paper execution ledger CSV (default: from config)",
    )
    parser.add_argument(
        "--replay-dir",
        default=None,
        help="Directory containing order_intents.csv + order_results.csv for replay",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=5,
        metavar="N",
        help="Number of ledger rows to show (default: 5)",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    output_dir  = Path(args.output_dir)

    checks: list[dict[str, Any]] = []
    all_statuses: list[str] = []

    # --- 1. Config ---
    config_result, cfg = check_config(config_path)
    checks.append(config_result)
    all_statuses.append(config_result["status"])

    # Resolve ledger path: CLI flag > config > default
    if args.ledger:
        ledger_path = Path(args.ledger)
    elif cfg is not None:
        ledger_path = Path(cfg.execution.paper_ledger_path)
    else:
        ledger_path = Path("output/paper_execution_ledger.csv")

    # --- 2. Ledger ---
    ledger_result = check_ledger(ledger_path, last_n=args.last)
    checks.append(ledger_result)
    all_statuses.append(ledger_result["status"])
    ledger_rows = ledger_result.get("rows") or []

    # --- 3. Artifacts ---
    artifact_result = check_artifacts(output_dir)
    checks.append(artifact_result)
    all_statuses.append(artifact_result["status"])

    # --- 4. Replay (optional) ---
    if args.replay_dir is not None:
        replay_result = check_replay(Path(args.replay_dir))
        checks.append(replay_result)
        all_statuses.append(replay_result["status"])

    # --- Final status ---
    if "FAIL" in all_statuses:
        final_status = "FAIL"
    elif "WARN" in all_statuses:
        final_status = "WARN"
    else:
        final_status = "PASS"

    print_report(checks, ledger_rows=ledger_rows, final_status=final_status)

    if final_status != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
