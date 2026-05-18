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
3. Safety guard configuration summary.
4. Current daily usage vs limits (from ledger).
5. Submit readiness (PASS / WARN / FAIL).
6. Ledger path (present or missing) + last N rows (default 5).
7. Artifact presence in --output-dir.
8. Replay reconciliation summary (only when --replay-dir is given).
9. Final PASS / WARN / FAIL summary.

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
from typing import Any, Callable

import pandas as pd


_EASTERN = "America/New_York"


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


def check_safety_config(cfg) -> dict[str, Any]:
    """Return an informational summary of all paper safety guard settings.

    Status is always PASS — this check is purely informational.
    """
    ex = cfg.execution
    config_vals = {
        "paper_kill_switch_enabled":   ex.paper_kill_switch_enabled,
        "paper_require_market_hours":  ex.paper_require_market_hours,
        "paper_block_if_open_orders":  ex.paper_block_if_open_orders,
        "paper_daily_max_orders":      ex.paper_daily_max_orders,
        "paper_daily_max_buy_orders":  ex.paper_daily_max_buy_orders,
        "paper_daily_max_close_orders": ex.paper_daily_max_close_orders,
        "paper_daily_max_notional":    ex.paper_daily_max_notional,
        "paper_poll_order_status":     ex.paper_poll_order_status,
    }
    ks_flag = "ENABLED" if ex.paper_kill_switch_enabled else "disabled"
    detail = (
        f"kill_switch={ks_flag} "
        f"require_market_hours={ex.paper_require_market_hours} "
        f"block_if_open_orders={ex.paper_block_if_open_orders} "
        f"daily_max_orders={ex.paper_daily_max_orders}"
    )
    return _result("safety_config", "PASS", detail) | {"config": config_vals}


def check_daily_usage(
    ledger_path: Path,
    cfg,
    tz: str = _EASTERN,
) -> dict[str, Any]:
    """Compute today's paper order usage from the ledger vs configured limits.

    Status is always PASS — WARN for approaching/exceeded limits is surfaced
    in check_submit_readiness instead.
    """
    from src.execution.paper_daily_limits import compute_daily_usage

    today = pd.Timestamp.now(tz=tz).date()
    raw = compute_daily_usage(ledger_path, today, tz)

    ex = cfg.execution
    max_total  = ex.paper_daily_max_orders
    max_buy    = ex.paper_daily_max_buy_orders
    max_close  = ex.paper_daily_max_close_orders

    def _remaining(used: int, limit: int | None) -> int | None:
        return max(0, limit - used) if limit is not None else None

    usage = {
        "today_total_orders":    raw["total_orders"],
        "today_buy_orders":      raw["buy_orders"],
        "today_close_orders":    raw["close_orders"],
        "remaining_total_orders":  _remaining(raw["total_orders"], max_total),
        "remaining_buy_orders":    _remaining(raw["buy_orders"], max_buy),
        "remaining_close_orders":  _remaining(raw["close_orders"], max_close),
    }

    detail = (
        f"today total={raw['total_orders']} buy={raw['buy_orders']} close={raw['close_orders']}"
        + (f"  remaining total={usage['remaining_total_orders']}" if max_total is not None else "")
    )
    return _result("daily_usage", "PASS", detail) | {"usage": usage}


def check_submit_readiness(
    cfg,
    daily_usage_result: dict[str, Any],
    now_provider: Callable[[], pd.Timestamp] | None = None,
) -> dict[str, Any]:
    """Return PASS / WARN / FAIL based on current submit conditions.

    PASS  — preview-only or non-submit mode.
    WARN  — in a submit mode (buy_submit or close_submit) but no blocking issues.
    FAIL  — kill switch enabled in submit mode.

    Additional WARN issues are collected for:
    - daily limit already reached or exceeded.
    - outside market hours (when paper_require_market_hours=true).
    """
    ex = cfg.execution
    mode = classify_mode(ex)

    _SUBMIT_MODES = {"buy_submit", "close_submit"}

    if mode not in _SUBMIT_MODES:
        return _result("submit_readiness", "PASS", f"preview/non-submit mode ({mode})")

    issues: list[str] = []
    status = "WARN"

    # Kill switch check
    if ex.paper_kill_switch_enabled:
        issues.append("kill switch ENABLED — all submissions blocked")
        status = "FAIL"

    # Daily limit check
    usage = daily_usage_result.get("usage", {})
    _limit_checks = [
        ("total",  usage.get("remaining_total_orders"),  "paper_daily_max_orders"),
        ("buy",    usage.get("remaining_buy_orders"),    "paper_daily_max_buy_orders"),
        ("close",  usage.get("remaining_close_orders"),  "paper_daily_max_close_orders"),
    ]
    for label, remaining, cfg_key in _limit_checks:
        if remaining is not None and remaining == 0:
            issues.append(f"daily {label} limit reached ({cfg_key}={getattr(ex, cfg_key)})")

    # Market hours check
    if ex.paper_require_market_hours:
        from src.execution.paper_market_hours_guard import is_regular_market_hours
        now = now_provider() if now_provider is not None else pd.Timestamp.now(tz=_EASTERN)
        if not is_regular_market_hours(now):
            time_str = now.strftime("%A %H:%M:%S %Z")
            issues.append(f"outside market hours ({time_str}) — paper_require_market_hours=true")

    detail_parts = [f"mode={mode}"]
    if issues:
        detail_parts += issues
    detail = "; ".join(detail_parts)

    return _result("submit_readiness", status, detail) | {"issues": issues, "mode": mode}


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

        # Print safety config values inline
        if c["label"] == "safety_config" and "config" in c:
            for k, v in c["config"].items():
                print(f"         {k}={v}")

        # Print daily usage inline
        if c["label"] == "daily_usage" and "usage" in c:
            u = c["usage"]
            print(
                f"         today  total={u['today_total_orders']}"
                f"  buy={u['today_buy_orders']}"
                f"  close={u['today_close_orders']}"
            )
            rem_total = u["remaining_total_orders"]
            rem_buy   = u["remaining_buy_orders"]
            rem_close = u["remaining_close_orders"]
            if any(v is not None for v in (rem_total, rem_buy, rem_close)):
                print(
                    f"         remaining  total={rem_total}"
                    f"  buy={rem_buy}"
                    f"  close={rem_close}"
                )

        # Print submit readiness issues
        if c["label"] == "submit_readiness" and "issues" in c:
            for issue in c["issues"]:
                print(f"         ! {issue}")

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

    # --- 2. Safety config (only when config loaded successfully) ---
    if cfg is not None:
        safety_result = check_safety_config(cfg)
        checks.append(safety_result)
        all_statuses.append(safety_result["status"])

    # --- 3. Daily usage (only when config loaded successfully) ---
    daily_usage_result: dict[str, Any] = {}
    if cfg is not None:
        daily_result = check_daily_usage(ledger_path, cfg)
        checks.append(daily_result)
        all_statuses.append(daily_result["status"])
        daily_usage_result = daily_result

    # --- 4. Submit readiness (only when config loaded successfully) ---
    if cfg is not None:
        readiness_result = check_submit_readiness(cfg, daily_usage_result)
        checks.append(readiness_result)
        all_statuses.append(readiness_result["status"])

    # --- 5. Ledger ---
    ledger_result = check_ledger(ledger_path, last_n=args.last)
    checks.append(ledger_result)
    all_statuses.append(ledger_result["status"])
    ledger_rows = ledger_result.get("rows") or []

    # --- 6. Artifacts ---
    artifact_result = check_artifacts(output_dir)
    checks.append(artifact_result)
    all_statuses.append(artifact_result["status"])

    # --- 7. Replay (optional) ---
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
