"""One-shot automated paper trading runner — S53.

Performs the complete safe workflow:

  1. Force-refresh the Yahoo SPY 60m cache.
  2. Abort before any trading if the refresh fails.
  3. Build the real Alpaca paper adapter and read the broker clock once.
  4. Load cached bars and validate freshness session-aware (S52d).
  5. Run the paper trading cycle in dry-run mode to obtain the planned
     action (and reason codes).
  6. If --submit-paper was passed and the dry-run produced a buy_planned
     or sell_planned, derive a deterministic idempotency key and submit
     exactly once. Re-running with the same key blocks duplicate orders.
  7. Always write one JSONL audit record per invocation.

Defaults: SPY, 60m, max_position_fraction=0.01, DRY_RUN.

Suitable for a Windows Task Scheduler entry. No internal scheduler,
no infinite loop, no retries after any broker submission attempt.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.broker.alpaca_paper_adapter import (
    AlpacaPaperAdapter,
    AlpacaPaperAdapterError,
)
from src.runtime.paper_trading_cycle import run_paper_trading_cycle
from src.tools.run_paper_cycle import (
    _CliError,
    _build_signal_config,
    _load_cached_bars,
    validate_bar_freshness,
)
from src.tools.yahoo_cache_fetch import run_fetch

_SYMBOL = "SPY"
_INTERVAL = "60m"
_DEFAULT_CACHE_DIR = Path("data/cache")
_DEFAULT_AUDIT_DIR = Path("logs/paper_cycles")
_DEFAULT_MAX_POSITION_FRACTION = 0.01
_IDEMPOTENCY_FILENAME = "_submitted_keys.txt"


def _default_now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _session_date(latest_ts: datetime) -> str:
    """Derive the trading-session date from the latest bar timestamp."""
    return latest_ts.astimezone(timezone.utc).date().isoformat()


def _idempotency_key(
    *, symbol: str, interval: str, latest_ts: datetime,
    signal_side: str, session_date: str,
) -> str:
    """Deterministic short alphanumeric key suitable for client_order_id."""
    payload = "|".join([
        symbol, interval, session_date, signal_side,
        latest_ts.astimezone(timezone.utc).isoformat(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _idempotency_path(audit_dir: Path) -> Path:
    return audit_dir / _IDEMPOTENCY_FILENAME


def _load_submitted_keys(audit_dir: Path) -> set[str]:
    path = _idempotency_path(audit_dir)
    if not path.is_file():
        return set()
    keys: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                keys.add(line)
    except OSError:
        return set()
    return keys


def _persist_submitted_key(audit_dir: Path, key: str) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = _idempotency_path(audit_dir)
    with path.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _audit_log_path(audit_dir: Path, now: datetime) -> Path:
    date_iso = now.astimezone(timezone.utc).date().isoformat()
    return audit_dir / f"{date_iso}.jsonl"


def _new_audit_record(*, mode: str, symbol: str, interval: str, now: datetime) -> dict[str, Any]:
    """Return a fresh audit record skeleton with safe defaults."""
    return {
        "timestamp_utc": now.astimezone(timezone.utc).isoformat(),
        "mode": mode,
        "symbol": symbol,
        "interval": interval,
        "fetch_result": None,
        "fetch_status": None,
        "network_calls_made": None,
        "latest_bar_ts": None,
        "clock_is_open": None,
        "signal": None,
        "reason_codes": [],
        "order_plan": None,
        "action": None,
        "broker_order_id": None,
        "broker_order_status": None,
        "idempotency_key": None,
        "duplicate_prevented": False,
        "blocker": None,
        "final_result": None,
        "exit_code": None,
    }


def _write_audit_record(audit_dir: Path, record: dict[str, Any], now: datetime) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = _audit_log_path(audit_dir, now)
    line = json.dumps(record, default=str, sort_keys=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_automated_paper_cycle",
        description=(
            "One-shot automated paper trading runner. Force-refreshes the "
            "SPY 60m cache, then runs one paper trading cycle. Dry-run by "
            "default; --submit-paper is the only path that submits an "
            "order. Writes one JSONL audit record per invocation."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run", action="store_true", default=False,
        help="(default) Plan only; never submit. Mutually exclusive with --submit-paper.",
    )
    mode_group.add_argument(
        "--submit-paper", action="store_true", default=False,
        help="Submit at most one paper order this run. Mutually exclusive with --dry-run.",
    )
    parser.add_argument(
        "--max-position-fraction", type=float,
        default=_DEFAULT_MAX_POSITION_FRACTION,
        help=f"Fraction of equity per buy (default {_DEFAULT_MAX_POSITION_FRACTION}, max 0.25).",
    )
    parser.add_argument(
        "--cache-dir", default=str(_DEFAULT_CACHE_DIR),
        help=f"Cache directory (default: {_DEFAULT_CACHE_DIR}).",
    )
    parser.add_argument(
        "--audit-dir", default=str(_DEFAULT_AUDIT_DIR),
        help=f"Audit log + idempotency directory (default: {_DEFAULT_AUDIT_DIR}).",
    )
    return parser


def _print_mode(submit_enabled: bool) -> None:
    mode = "PAPER_SUBMIT" if submit_enabled else "DRY_RUN"
    sys.stdout.write(f"mode: {mode}\n")


def _finalize_and_exit(
    audit_dir: Path, record: dict[str, Any], now: datetime,
    final_result: str, exit_code: int,
) -> int:
    record["final_result"] = final_result
    record["exit_code"] = exit_code
    _write_audit_record(audit_dir, record, now)
    return exit_code


def main(argv: list[str] | None = None, *, now_utc_fn=_default_now_utc) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    submit_enabled = args.submit_paper  # dry-run default; explicit --submit-paper opts in
    mode = "PAPER_SUBMIT" if submit_enabled else "DRY_RUN"
    _print_mode(submit_enabled)

    now = now_utc_fn()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    audit_dir = Path(args.audit_dir)
    cache_dir = Path(args.cache_dir)

    record = _new_audit_record(
        mode=mode, symbol=_SYMBOL, interval=_INTERVAL, now=now,
    )

    # -----------------------------------------------------------------
    # Step 1. Force-refresh Yahoo cache. Abort before any trading if it
    # fails or produces a blocked result.
    # -----------------------------------------------------------------
    try:
        fetch = run_fetch(
            cache_dir=cache_dir,
            symbols=[_SYMBOL],
            intervals=[_INTERVAL],
            allow_network=True,
            force_refresh=True,
        )
    except Exception as exc:
        record["blocker"] = f"fetch raised {exc.__class__.__name__}"
        return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)

    record["fetch_result"] = fetch.get("result")
    record["network_calls_made"] = bool(fetch.get("network_calls_made"))
    fetch_entries = fetch.get("entries", [])
    if fetch_entries:
        record["fetch_status"] = fetch_entries[0].get("status")

    if fetch.get("result") != "PASS":
        record["blocker"] = (
            f"cache refresh did not PASS: {fetch.get('blocker') or 'unknown'}"
        )
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    # -----------------------------------------------------------------
    # Step 2. Build adapter and read the broker clock exactly once.
    # -----------------------------------------------------------------
    try:
        adapter = AlpacaPaperAdapter.from_environment()
    except AlpacaPaperAdapterError as exc:
        record["blocker"] = f"adapter init failed: {exc}"
        return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)

    try:
        clock = adapter.get_clock()
    except AlpacaPaperAdapterError as exc:
        record["blocker"] = f"clock read failed: {exc}"
        return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)
    if isinstance(clock, dict):
        record["clock_is_open"] = bool(clock.get("is_open"))

    # -----------------------------------------------------------------
    # Step 3. Load cached bars and validate freshness session-aware.
    # -----------------------------------------------------------------
    try:
        bars, latest_ts = _load_cached_bars(cache_dir, _SYMBOL, _INTERVAL)
    except _CliError as exc:
        record["blocker"] = str(exc)
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    record["latest_bar_ts"] = latest_ts.astimezone(timezone.utc).isoformat()

    freshness_blocker = validate_bar_freshness(
        latest_ts=latest_ts, now=now, clock=clock, interval=_INTERVAL,
    )
    if freshness_blocker is not None:
        record["blocker"] = freshness_blocker
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    # -----------------------------------------------------------------
    # Step 4. Dry-run the cycle first so we always have the planned
    # action, signal, and order_plan to log — and so the idempotency
    # key can be derived without consulting submission.
    # -----------------------------------------------------------------
    signal_config = _build_signal_config(_INTERVAL)
    dry = run_paper_trading_cycle(
        adapter=adapter,
        bars=bars,
        signal_config=signal_config,
        max_position_fraction=args.max_position_fraction,
        submit_enabled=False,
        clock_snapshot=clock,
    )

    record["signal"] = dry.get("signal")
    record["reason_codes"] = list(dry.get("reason_codes") or [])
    record["order_plan"] = dry.get("order_plan")
    record["action"] = dry.get("action")
    record["blocker"] = dry.get("blocker")

    if dry.get("result") == "ERROR":
        return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)
    if dry.get("result") == "BLOCKED":
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    # dry.get("result") == "PASS" here.
    action = dry.get("action")
    if action not in ("buy_planned", "sell_planned"):
        # HOLD / BLOCK signal — no action required. Dry-run and submit
        # mode behave identically in this case.
        return _finalize_and_exit(audit_dir, record, now, "PASS", 0)

    if not submit_enabled:
        # DRY_RUN with a plan — do NOT consume an idempotency key.
        return _finalize_and_exit(audit_dir, record, now, "PASS", 0)

    # -----------------------------------------------------------------
    # Step 5. PAPER_SUBMIT mode: idempotency + single submission.
    # -----------------------------------------------------------------
    side = "buy" if action == "buy_planned" else "sell"
    key = _idempotency_key(
        symbol=_SYMBOL, interval=_INTERVAL,
        latest_ts=latest_ts, signal_side=side,
        session_date=_session_date(latest_ts),
    )
    record["idempotency_key"] = key

    already = _load_submitted_keys(audit_dir)
    if key in already:
        record["duplicate_prevented"] = True
        record["blocker"] = "duplicate idempotency key — already submitted"
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    submit = run_paper_trading_cycle(
        adapter=adapter,
        bars=bars,
        signal_config=signal_config,
        max_position_fraction=args.max_position_fraction,
        submit_enabled=True,
        clock_snapshot=clock,
        client_order_id=key,
    )

    record["action"] = submit.get("action")
    record["order_plan"] = submit.get("order_plan")
    record["blocker"] = submit.get("blocker")
    order = submit.get("order")
    if isinstance(order, dict):
        record["broker_order_id"] = order.get("id")
        record["broker_order_status"] = order.get("status")

    if submit.get("result") == "PASS" and submit.get("action") in (
        "buy_submitted", "sell_submitted",
    ):
        _persist_submitted_key(audit_dir, key)
        return _finalize_and_exit(audit_dir, record, now, "PASS", 0)
    if submit.get("result") == "ERROR":
        return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)
    return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)


if __name__ == "__main__":
    raise SystemExit(main())
