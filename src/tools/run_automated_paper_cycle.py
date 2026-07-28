"""One-shot automated paper trading runner — S53.

Performs the complete safe workflow:

  1. Force-refresh the Yahoo SPY 60m cache.
  2. Abort before any trading if the refresh fails.
  3. Load the just-refreshed cached bars and populate the audit
     record's latest_bar_ts immediately (S63 audit-ordering change) —
     before any broker/adapter call, so a later adapter-init or
     clock-read failure still leaves a record identifying exactly
     which cache snapshot was refreshed.
  4. Build the real Alpaca paper adapter and read the broker clock once.
  5. Validate freshness session-aware (S52d), reusing the bars loaded
     in step 3 — the cache is not re-read.
  6. Run the paper trading cycle in dry-run mode to obtain the planned
     action (and reason codes).
  7. If --submit-paper was passed and the dry-run produced a buy_planned
     or sell_planned, derive a deterministic idempotency key and submit
     exactly once. Re-running with the same key blocks duplicate orders.
  8. Always write one JSONL audit record per invocation.

Defaults: SPY, 60m, max_position_fraction=0.01, DRY_RUN.

Suitable for a Windows Task Scheduler entry. No internal scheduler,
no infinite loop, no retries after any broker submission attempt.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
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

# Per-key idempotency state is stored under <audit_dir>/<_CLAIMS_SUBDIR>/.
# Two file forms are used:
#   <key>.claim     — process has reserved the key and is mid-submission
#                     (or the previous run terminated with an ambiguous
#                     broker response).
#   <key>.submitted — broker accepted the order and the runner persisted
#                     the final state.
# Either file's presence blocks future automatic submissions for this
# key. The legacy filename is retained for backward-compatible tests.
_CLAIMS_SUBDIR = "_claims"
_CLAIM_SUFFIX = ".claim"
_SUBMITTED_SUFFIX = ".submitted"
_IDEMPOTENCY_FILENAME = "_submitted_keys.txt"  # legacy: no longer written


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


class _IdempotencyError(Exception):
    """Idempotency storage / state failure. Always fails closed."""


def _claims_dir(audit_dir: Path) -> Path:
    return audit_dir / _CLAIMS_SUBDIR


def _claim_path(audit_dir: Path, key: str) -> Path:
    return _claims_dir(audit_dir) / f"{key}{_CLAIM_SUFFIX}"


def _submitted_path(audit_dir: Path, key: str) -> Path:
    return _claims_dir(audit_dir) / f"{key}{_SUBMITTED_SUFFIX}"


# Legacy alias retained so external tests can reference it without crashing.
def _idempotency_path(audit_dir: Path) -> Path:
    return audit_dir / _IDEMPOTENCY_FILENAME


def _read_idempotency_state(audit_dir: Path, key: str) -> str | None:
    """Return ``"CLAIMED"``, ``"SUBMITTED"``, or ``None``.

    Raises :class:`_IdempotencyError` on any OS-level read failure or
    if a claim file is present but unreadable / decode-broken. The
    runner converts the exception into a BLOCKED result so a corrupt
    state never silently allows a duplicate submission.
    """
    cdir = _claims_dir(audit_dir)
    if not cdir.exists():
        return None
    try:
        if _submitted_path(audit_dir, key).exists():
            return "SUBMITTED"
        cpath = _claim_path(audit_dir, key)
        if cpath.exists():
            try:
                _ = cpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise _IdempotencyError(
                    f"claim file unreadable: {exc.__class__.__name__}"
                ) from exc
            return "CLAIMED"
    except _IdempotencyError:
        raise
    except OSError as exc:
        raise _IdempotencyError(
            f"idempotency state read failed: {exc.__class__.__name__}"
        ) from exc
    return None


def _try_claim(audit_dir: Path, key: str, now: datetime) -> None:
    """Atomically reserve the key with ``O_CREAT | O_EXCL``.

    Atomic across separate processes on both POSIX and Windows. Raises
    :class:`_IdempotencyError` on FileExistsError (duplicate claim) or
    any other storage failure.
    """
    cdir = _claims_dir(audit_dir)
    try:
        cdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _IdempotencyError(
            f"could not create claims dir: {exc.__class__.__name__}"
        ) from exc
    cpath = _claim_path(audit_dir, key)
    try:
        fd = os.open(
            str(cpath),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        raise _IdempotencyError("claim already exists")
    except OSError as exc:
        raise _IdempotencyError(
            f"claim creation failed: {exc.__class__.__name__}"
        ) from exc
    try:
        os.write(
            fd,
            f"CLAIMED\n{now.astimezone(timezone.utc).isoformat()}\n".encode("utf-8"),
        )
    finally:
        os.close(fd)


def _release_claim(audit_dir: Path, key: str) -> None:
    """Best-effort claim release for confirmed broker-not-accepted failures.

    Leaves the file in place on OSError — keeping the file errs on the
    side of blocking future automatic submissions.
    """
    try:
        _claim_path(audit_dir, key).unlink(missing_ok=True)
    except OSError:
        pass


def _finalize_submitted(audit_dir: Path, key: str, now: datetime) -> bool:
    """Atomically transition ``<key>.claim`` -> ``<key>.submitted``.

    Returns ``True`` on success, ``False`` on any storage failure. On
    failure, the original claim file is left in place so future
    automatic submissions stay blocked (manual reconciliation required).
    """
    cpath = _claim_path(audit_dir, key)
    spath = _submitted_path(audit_dir, key)
    tmp_path = cpath.with_suffix(_CLAIM_SUFFIX + ".tmp")
    try:
        tmp_path.write_text(
            f"SUBMITTED\n{now.astimezone(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(spath))
        try:
            cpath.unlink()
        except OSError:
            pass
        return True
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _classify_broker_failure(blocker: str | None) -> str:
    """Return ``"AMBIGUOUS"`` or ``"CONFIRMED_NOT_SUBMITTED"``.

    Heuristic: the adapter wraps real SDK-call exceptions with the
    prefix ``"submit_market_order failed:"``. Pre-broker validation
    errors (symbol/qty/side rejection, "sell rejected: no SPY position")
    raise without that prefix. Anything carrying the prefix means the
    broker SDK *was* invoked — outcome is therefore ambiguous.
    """
    if not isinstance(blocker, str):
        return "AMBIGUOUS"
    if "submit_market_order failed:" in blocker:
        return "AMBIGUOUS"
    return "CONFIRMED_NOT_SUBMITTED"


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
        "client_order_id": None,
        "broker_position_qty": None,
        "broker_open_buy_order_count": None,
        "order_status": None,
        "submitted_qty": None,
        "filled_qty": None,
        "filled_avg_price": None,
        "reconciliation_attempted": False,
        "reconciliation_result": None,
        "duplicate_prevented": False,
        "blocker": None,
        "final_result": None,
        "exit_code": None,
    }


# ---------------------------------------------------------------------------
# Broker-state safety helpers
# ---------------------------------------------------------------------------


_STATUS_REASON_MAP = {
    "new": "ORDER_SUBMITTED",
    "accepted": "ORDER_SUBMITTED",
    "accepted_for_bidding": "ORDER_SUBMITTED",
    "pending_new": "ORDER_SUBMITTED",
    "partially_filled": "ORDER_PARTIALLY_FILLED",
    "filled": "ORDER_FILLED",
    "rejected": "ORDER_REJECTED",
    "canceled": "ORDER_CANCELED",
    "cancelled": "ORDER_CANCELED",
    "expired": "ORDER_CANCELED",
}


def _status_to_reason_code(status: Any) -> str:
    """Map an Alpaca order status string to a runner reason code."""
    if not isinstance(status, str) or not status.strip():
        return "ORDER_STATUS_UNKNOWN"
    return _STATUS_REASON_MAP.get(status.strip().lower(), "ORDER_STATUS_UNKNOWN")


def _copy_status_fields(record: dict[str, Any], order: dict[str, Any]) -> None:
    """Copy status/quantity/price fields from an order dict onto ``record``.

    Kept in one place so the post-submit and reconciliation paths stay
    schema-consistent.
    """
    record["order_status"] = order.get("status")
    if record.get("broker_order_id") is None:
        record["broker_order_id"] = order.get("id")
    if record.get("broker_order_status") is None:
        record["broker_order_status"] = order.get("status")
    if record.get("submitted_qty") is None:
        record["submitted_qty"] = order.get("qty")
    record["filled_qty"] = order.get("filled_qty")
    record["filled_avg_price"] = order.get("filled_avg_price")


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
    # Step 2. Load the just-refreshed cache and populate latest_bar_ts
    # immediately after a successful cache refresh — before touching
    # the broker adapter at all. This is an audit-ordering change
    # only: it guarantees that any *later* adapter-init/clock/broker
    # failure still leaves a record with a populated latest_bar_ts,
    # since S63's scheduling gate keys off latest_bar_ts to confirm
    # the audit corresponds to the refreshed cache. The bars and
    # latest_ts loaded here are reused below — the cache is not
    # re-read after this point.
    # -----------------------------------------------------------------
    try:
        bars, latest_ts = _load_cached_bars(cache_dir, _SYMBOL, _INTERVAL)
    except _CliError as exc:
        record["blocker"] = str(exc)
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    record["latest_bar_ts"] = latest_ts.astimezone(timezone.utc).isoformat()

    # -----------------------------------------------------------------
    # Step 3. Build adapter and read the broker clock exactly once.
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
    # Step 4. Validate freshness session-aware, reusing the bars and
    # latest_ts already loaded in Step 2 (no re-read of the cache).
    # -----------------------------------------------------------------
    freshness_blocker = validate_bar_freshness(
        latest_ts=latest_ts, now=now, clock=clock, interval=_INTERVAL,
    )
    if freshness_blocker is not None:
        record["blocker"] = freshness_blocker
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    # -----------------------------------------------------------------
    # Step 5. Dry-run the cycle first so we always have the planned
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
    # Step 6. Pre-submit broker-state safety checks (BUY only).
    #
    # Broker state is the source of truth. A race between dry-run and
    # this second read would produce a duplicate order; catching it
    # here means neither a claim is created nor a broker order sent.
    # -----------------------------------------------------------------
    if action == "buy_planned":
        try:
            broker_position = adapter.get_position(_SYMBOL)
        except AlpacaPaperAdapterError as exc:
            record["blocker"] = f"pre-submit position check failed: {exc}"
            return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)

        if broker_position is not None:
            record["broker_position_qty"] = broker_position.get("qty")
            record["reason_codes"].append("POSITION_ALREADY_EXISTS")
            record["action"] = "none"
            record["blocker"] = (
                "broker reports an existing SPY position — no additional BUY"
            )
            return _finalize_and_exit(audit_dir, record, now, "PASS", 0)
        record["broker_position_qty"] = 0.0

        try:
            broker_open_orders = adapter.list_open_orders(symbol=_SYMBOL)
        except AlpacaPaperAdapterError as exc:
            record["blocker"] = f"pre-submit open-orders check failed: {exc}"
            return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)

        open_buy_orders = [
            o for o in broker_open_orders
            if isinstance(o, dict) and (o.get("side") or "").lower() == "buy"
        ]
        record["broker_open_buy_order_count"] = len(open_buy_orders)
        if open_buy_orders:
            record["reason_codes"].append("OPEN_BUY_ORDER_ALREADY_EXISTS")
            record["action"] = "none"
            record["blocker"] = (
                "broker reports an existing open SPY BUY order — "
                "no additional BUY"
            )
            return _finalize_and_exit(audit_dir, record, now, "PASS", 0)

    # -----------------------------------------------------------------
    # Step 7. PAPER_SUBMIT: idempotency + single submission.
    # -----------------------------------------------------------------
    side = "buy" if action == "buy_planned" else "sell"
    key = _idempotency_key(
        symbol=_SYMBOL, interval=_INTERVAL,
        latest_ts=latest_ts, signal_side=side,
        session_date=_session_date(latest_ts),
    )
    record["idempotency_key"] = key
    record["client_order_id"] = key

    try:
        existing_state = _read_idempotency_state(audit_dir, key)
    except _IdempotencyError as exc:
        record["blocker"] = f"idempotency state unreadable: {exc}"
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    if existing_state in ("CLAIMED", "SUBMITTED"):
        record["duplicate_prevented"] = True
        if existing_state == "SUBMITTED":
            record["blocker"] = (
                "duplicate idempotency key — already submitted"
            )
        else:
            record["blocker"] = (
                "claim already exists — concurrent run or pending "
                "manual reconciliation"
            )
        return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)

    try:
        _try_claim(audit_dir, key, now)
    except _IdempotencyError as exc:
        record["duplicate_prevented"] = True
        record["blocker"] = f"could not claim idempotency: {exc}"
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
        record["submitted_qty"] = order.get("qty")

    if submit.get("result") == "PASS" and submit.get("action") in (
        "buy_submitted", "sell_submitted",
    ):
        # Post-submit status audit. If the follow-up lookup fails, keep
        # the claim as CLAIMED and record ORDER_STATUS_UNKNOWN — the
        # order WAS submitted but its status is not confirmed.
        broker_order_id = (order or {}).get("id") if isinstance(order, dict) else None
        if broker_order_id:
            try:
                latest = adapter.get_order(broker_order_id)
            except AlpacaPaperAdapterError as exc:
                record["reason_codes"].append("ORDER_STATUS_UNKNOWN")
                record["blocker"] = (
                    f"order submitted but status lookup failed: {exc} "
                    "(claim retained for manual reconciliation)"
                )
                return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)
            _copy_status_fields(record, latest)
            record["reason_codes"].append(
                _status_to_reason_code(latest.get("status"))
            )
        else:
            record["reason_codes"].append("ORDER_STATUS_UNKNOWN")

        finalized = _finalize_submitted(audit_dir, key, now)
        if not finalized:
            record["blocker"] = (
                "broker accepted but SUBMITTED finalization failed — "
                "claim retained to block future automatic submissions"
            )
        return _finalize_and_exit(audit_dir, record, now, "PASS", 0)

    if submit.get("result") == "ERROR":
        classification = _classify_broker_failure(submit.get("blocker"))
        if classification == "CONFIRMED_NOT_SUBMITTED":
            _release_claim(audit_dir, key)
            record["blocker"] = (
                (submit.get("blocker") or "broker rejected before acceptance")
                + " (claim released)"
            )
            return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)

        # Ambiguous — attempt reconciliation by client_order_id and keep
        # the claim regardless of the outcome so future runs block until
        # an operator reviews.
        record["reconciliation_attempted"] = True
        recon: dict[str, Any] | None
        try:
            recon = adapter.get_order_by_client_order_id(key)
        except AlpacaPaperAdapterError as exc:
            recon = None
            record["reconciliation_result"] = (
                f"lookup_failed:{exc.__class__.__name__}"
            )
        else:
            if recon is None:
                record["reconciliation_result"] = "not_found"
            else:
                record["reconciliation_result"] = "found"
                _copy_status_fields(record, recon)
                record["reason_codes"].append(
                    _status_to_reason_code(recon.get("status"))
                )

        if recon is None:
            record["reason_codes"].append("ORDER_STATUS_UNKNOWN")

        record["blocker"] = (
            (submit.get("blocker") or "broker submission outcome ambiguous")
            + " (claim retained for manual reconciliation)"
        )
        return _finalize_and_exit(audit_dir, record, now, "ERROR", 2)

    _release_claim(audit_dir, key)
    return _finalize_and_exit(audit_dir, record, now, "BLOCKED", 1)


if __name__ == "__main__":
    raise SystemExit(main())
