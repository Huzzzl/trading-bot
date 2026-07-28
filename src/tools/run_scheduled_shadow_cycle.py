"""
tools/run_scheduled_shadow_cycle.py
====================================
S63 — isolated Windows scheduling gate for the S62 shadow experiment.

This tool is a standalone *gate*: it decides, from already-written
artifacts, whether it is safe to advance the S62 forward-only shadow
experiment by one cycle, and if so invokes the existing S62 runner.
It never runs the paper trading cycle itself and never touches the
paper execution path.

Safety boundaries
------------------
* No broker imports, no Alpaca SDK, no credentials.
* Never reads broker positions, orders, account data.
* Never submits, cancels, replaces, or queries orders.
* Never calls the automated paper-trading cycle entry point or its
  underlying trading-cycle function — this module imports nothing
  from that part of the codebase at all.
* Never refreshes the market-data cache — it only reads what the
  paper task already wrote.
* Never modifies the S62 frozen manifest, the paper strategy, SMA
  parameters, ``max_position_fraction``, or the Windows paper task.
* Never automatically promotes a shadow candidate — every S62
  guardrail (``research_only``, ``automatic_strategy_promotion_allowed:
  false``) is preserved untouched, and this tool adds its own
  ``research_only`` / ``automatic_promotion_allowed`` constants to its
  own audit trail.

Intended architecture
----------------------
::

    Existing paper task
            |
            | writes paper audit + refreshed SPY 60m cache
            v
    S62 shadow task (this tool), scheduled several minutes later

Two independent Windows Scheduled Tasks. This tool only *reads* the
paper task's output; it never replaces, edits, enables, disables, or
re-registers that task.

Gate contract
-------------
Invoke the S62 shadow runner only when ALL of the following hold:

1. a qualifying (SPY, 60m) paper audit record exists;
2. its ``timestamp_utc`` is not older than
   ``--max-paper-audit-age-minutes``;
3. its ``fetch_result`` is exactly ``"PASS"``;
4. its ``latest_bar_ts`` is a parseable UTC timestamp;
5. the latest bar in the loaded SPY 60m cache has the exact same
   timestamp as the paper audit's ``latest_bar_ts``;
6. the S62 manifest and state (if already initialized) pass their
   existing validation;
7. the S62 event log (if present) passes its existing strict
   validation.

The paper audit's ``final_result`` is deliberately NOT part of the
gate — a broker, clock, credential, or paper-order failure that
happens *after* a successful cache refresh must not block
independent shadow research. ``final_result``, ``exit_code``, and
``blocker`` are recorded for diagnostics only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.tools.backtest_strategy_eval import BacktestError, load_cached_bars
from src.tools.run_shadow_strategy_cycle import (
    EXPERIMENT_ID as _SHADOW_EXPERIMENT_ID,
    ShadowError,
    _bar_ts_utc as _shadow_bar_ts_utc,
    _EVENTS_FILENAME as _SHADOW_EVENTS_FILENAME,
    _MANIFEST_FILENAME as _SHADOW_MANIFEST_FILENAME,
    _STATE_FILENAME as _SHADOW_STATE_FILENAME,
    _validate_event_log_readonly as _shadow_validate_event_log_readonly,
    load_manifest_readonly as _shadow_load_manifest_readonly,
    load_state_readonly as _shadow_load_state_readonly,
    run_cycle as _shadow_run_cycle,
)

TOOL_NAME = "run_scheduled_shadow_cycle"

_SYMBOL = "SPY"
_INTERVAL = "60m"

_DEFAULT_PAPER_AUDIT_DIR = Path("logs/paper_cycles")
_DEFAULT_CACHE_DIR = Path("data/cache")
_DEFAULT_SHADOW_STATE_DIR = Path("logs/shadow_strategy")
_DEFAULT_SCHEDULER_AUDIT_DIR = Path("logs/shadow_scheduler")
_DEFAULT_MAX_PAPER_AUDIT_AGE_MINUTES = 20

# Tolerance for "future" audit timestamps — allows for minor clock
# skew between the process that wrote the audit and this process,
# without accepting genuinely bogus far-future timestamps.
_FUTURE_TS_TOLERANCE = timedelta(seconds=5)

# ---------------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------------

REASON_NO_PAPER_AUDIT = "NO_PAPER_AUDIT"
REASON_NO_MATCHING_SPY_60M_AUDIT = "NO_MATCHING_SPY_60M_AUDIT"
REASON_PAPER_AUDIT_STALE = "PAPER_AUDIT_STALE"
REASON_CACHE_REFRESH_NOT_PASS = "CACHE_REFRESH_NOT_PASS"

REASON_PAPER_AUDIT_CORRUPT = "PAPER_AUDIT_CORRUPT"
REASON_PAPER_AUDIT_TIMESTAMP_INVALID = "PAPER_AUDIT_TIMESTAMP_INVALID"
REASON_CACHE_LOAD_FAILED = "CACHE_LOAD_FAILED"
REASON_AUDIT_CACHE_TIMESTAMP_MISMATCH = "AUDIT_CACHE_TIMESTAMP_MISMATCH"
REASON_SHADOW_INTEGRITY_ERROR = "SHADOW_INTEGRITY_ERROR"
REASON_SHADOW_RUN_FAILED = "SHADOW_RUN_FAILED"
REASON_SCHEDULER_AUDIT_WRITE_FAILED = "SCHEDULER_AUDIT_WRITE_FAILED"

_SKIPPED_REASON_CODES = frozenset({
    REASON_NO_PAPER_AUDIT,
    REASON_NO_MATCHING_SPY_60M_AUDIT,
    REASON_PAPER_AUDIT_STALE,
    REASON_CACHE_REFRESH_NOT_PASS,
})


class GateError(Exception):
    """A classified gate failure. ``reason_code`` determines whether
    the overall outcome is SKIPPED (exit 0) or ERROR (exit 2)."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _parse_ts(s: str) -> datetime:
    ts = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Paper audit selection (read-only — never repairs or alters audit files)
# ---------------------------------------------------------------------------


def _any_audit_files(paper_audit_dir: Path) -> bool:
    if not paper_audit_dir.is_dir():
        return False
    return any(paper_audit_dir.glob("*.jsonl"))


def _iter_audit_lines(paper_audit_dir: Path):
    """Yield ``(path, line_no, stripped_line)`` for every non-blank
    line across every ``*.jsonl`` file in ``paper_audit_dir``, in a
    deterministic order (files sorted by name, lines top-to-bottom).
    """
    for path in sorted(paper_audit_dir.glob("*.jsonl")):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GateError(
                REASON_PAPER_AUDIT_CORRUPT, f"could not read {path}: {exc}",
            ) from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateError(
                REASON_PAPER_AUDIT_CORRUPT,
                f"{path} is not valid UTF-8: {exc}",
            ) from exc
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped:
                yield path, line_no, stripped


def select_latest_paper_audit(
    paper_audit_dir: Path, now_utc: datetime,
) -> dict[str, Any] | None:
    """Return the most recent qualifying (SPY, 60m) paper audit
    record, selected by parsed ``timestamp_utc`` — not by physical
    file/line order.

    Returns ``None`` when no ``*.jsonl`` files exist, or none contain
    a qualifying record. Raises :class:`GateError` (reason
    ``PAPER_AUDIT_CORRUPT`` or ``PAPER_AUDIT_TIMESTAMP_INVALID``) on
    any structural corruption encountered along the way — malformed
    JSON, a non-object record, or (for an otherwise-matching SPY/60m
    record) an unparseable or future ``timestamp_utc``. Records with
    the wrong ``symbol``/``interval`` are silently skipped, never an
    error.

    Never mutates or repairs the audit files.
    """
    best: dict[str, Any] | None = None
    best_ts: datetime | None = None
    for path, line_no, raw_line in _iter_audit_lines(paper_audit_dir):
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise GateError(
                REASON_PAPER_AUDIT_CORRUPT,
                f"malformed audit line {path}:{line_no}: {exc.msg}",
            ) from exc
        if not isinstance(obj, dict):
            raise GateError(
                REASON_PAPER_AUDIT_CORRUPT,
                f"audit line {path}:{line_no} is not a JSON object",
            )
        if obj.get("symbol") != _SYMBOL or obj.get("interval") != _INTERVAL:
            continue
        ts_raw = obj.get("timestamp_utc")
        if not isinstance(ts_raw, str):
            raise GateError(
                REASON_PAPER_AUDIT_TIMESTAMP_INVALID,
                f"audit line {path}:{line_no} timestamp_utc missing or "
                f"not a string",
            )
        try:
            ts = _parse_ts(ts_raw)
        except Exception as exc:
            raise GateError(
                REASON_PAPER_AUDIT_TIMESTAMP_INVALID,
                f"audit line {path}:{line_no} timestamp_utc unparseable: {exc}",
            ) from exc
        if ts > now_utc + _FUTURE_TS_TOLERANCE:
            raise GateError(
                REASON_PAPER_AUDIT_TIMESTAMP_INVALID,
                f"audit line {path}:{line_no} timestamp_utc "
                f"{ts.isoformat()} is in the future",
            )
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best = {"record": obj, "path": path, "line_no": line_no, "timestamp": ts}
    return best


def _hash_record(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# S62 preflight integrity check (read-only)
# ---------------------------------------------------------------------------


def _preflight_validate_shadow(shadow_state_dir: Path) -> None:
    """Read-only S62 integrity check performed BEFORE invoking the
    shadow runner.

    If no experiment has been initialized yet (no manifest on disk),
    this is a no-op — the S62 runner bootstraps it on first
    ``--once``. If a manifest exists, it (and the state / event log,
    when present) must pass S62's own validation; any corruption
    raises :class:`GateError` with reason ``SHADOW_INTEGRITY_ERROR``.

    Never mutates anything.
    """
    manifest_path = shadow_state_dir / _SHADOW_MANIFEST_FILENAME
    if not manifest_path.exists():
        return
    try:
        manifest = _shadow_load_manifest_readonly(shadow_state_dir)
        state_path = shadow_state_dir / _SHADOW_STATE_FILENAME
        if state_path.exists():
            state = _shadow_load_state_readonly(shadow_state_dir, manifest)
            events_path = shadow_state_dir / _SHADOW_EVENTS_FILENAME
            _shadow_validate_event_log_readonly(events_path, state, manifest)
    except ShadowError as exc:
        raise GateError(REASON_SHADOW_INTEGRITY_ERROR, str(exc)) from exc


# ---------------------------------------------------------------------------
# Scheduler audit
# ---------------------------------------------------------------------------

_SCHEDULER_RECORD_FIELDS = (
    "timestamp_utc", "tool", "result", "exit_code", "reason_codes",
    "paper_audit_path", "paper_audit_timestamp_utc",
    "paper_audit_record_sha256", "paper_mode", "paper_fetch_result",
    "paper_fetch_status", "paper_latest_bar_ts", "paper_final_result",
    "paper_exit_code", "paper_blocker", "cache_latest_bar_ts",
    "cache_matches_paper_audit", "shadow_invoked", "shadow_experiment_id",
    "shadow_manifest_hash", "shadow_forward_cutoff_utc",
    "shadow_candidate_events_appended", "shadow_experiment_events_appended",
    "shadow_events_appended", "shadow_error", "dry_run", "research_only",
    "automatic_promotion_allowed",
)


def _new_scheduler_record(now_utc: datetime) -> dict[str, Any]:
    return {
        "timestamp_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "tool": TOOL_NAME,
        "result": None,
        "exit_code": None,
        "reason_codes": [],
        "paper_audit_path": None,
        "paper_audit_timestamp_utc": None,
        "paper_audit_record_sha256": None,
        "paper_mode": None,
        "paper_fetch_result": None,
        "paper_fetch_status": None,
        "paper_latest_bar_ts": None,
        "paper_final_result": None,
        "paper_exit_code": None,
        "paper_blocker": None,
        "cache_latest_bar_ts": None,
        "cache_matches_paper_audit": None,
        "shadow_invoked": False,
        "shadow_experiment_id": None,
        "shadow_manifest_hash": None,
        "shadow_forward_cutoff_utc": None,
        "shadow_candidate_events_appended": None,
        "shadow_experiment_events_appended": None,
        "shadow_events_appended": None,
        "shadow_error": None,
        "dry_run": False,
        "research_only": True,
        "automatic_promotion_allowed": False,
    }


def _scheduler_audit_path(scheduler_audit_dir: Path, now_utc: datetime) -> Path:
    date_iso = now_utc.astimezone(timezone.utc).date().isoformat()
    return scheduler_audit_dir / f"{date_iso}.jsonl"


def _append_scheduler_audit(
    scheduler_audit_dir: Path, record: dict[str, Any], now_utc: datetime,
) -> None:
    """Append one canonical JSON line to today's scheduler audit file.

    Raises OSError on any filesystem failure — the caller converts
    this into a ``SCHEDULER_AUDIT_WRITE_FAILED`` outcome. Never
    touches S62 state; this file lives entirely under
    ``scheduler_audit_dir``.
    """
    scheduler_audit_dir.mkdir(parents=True, exist_ok=True)
    path = _scheduler_audit_path(scheduler_audit_dir, now_utc)
    line = json.dumps(record, sort_keys=True, default=str, ensure_ascii=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def run_gate(
    *,
    paper_audit_dir: Path,
    cache_dir: Path,
    shadow_state_dir: Path,
    scheduler_audit_dir: Path,
    max_paper_audit_age_minutes: int,
    now_utc: datetime,
    dry_run: bool = False,
    write_dry_run_audit: bool = False,
) -> dict[str, Any]:
    """Execute the full S63 gate exactly once.

    Transaction ordering:
      1. (caller) parse CLI
      2. read and validate the paper audit
      3. evaluate age and fetch gate
      4. load and validate cache
      5. compare audit/cache timestamps
      6. validate S62 manifest/state/events
      7. if dry_run, report what would happen without changing S62 state
      8. otherwise invoke S62
      9. persist the scheduler audit
      10. return the final record (exit_code carries the exit status)

    Never raises — every failure is classified into the returned
    record's ``result``/``exit_code``/``reason_codes``.
    """
    record = _new_scheduler_record(now_utc)
    record["dry_run"] = dry_run

    try:
        selected = select_latest_paper_audit(paper_audit_dir, now_utc)
        if selected is None:
            if not _any_audit_files(paper_audit_dir):
                raise GateError(
                    REASON_NO_PAPER_AUDIT,
                    f"no paper audit files found under {paper_audit_dir}",
                )
            raise GateError(
                REASON_NO_MATCHING_SPY_60M_AUDIT,
                f"no {_SYMBOL}/{_INTERVAL} paper audit record found under "
                f"{paper_audit_dir}",
            )

        audit_record = selected["record"]
        audit_path = selected["path"]
        audit_ts = selected["timestamp"]

        record["paper_audit_path"] = str(audit_path)
        record["paper_audit_timestamp_utc"] = audit_ts.isoformat()
        record["paper_audit_record_sha256"] = _hash_record(audit_record)
        record["paper_mode"] = audit_record.get("mode")
        record["paper_fetch_result"] = audit_record.get("fetch_result")
        record["paper_fetch_status"] = audit_record.get("fetch_status")
        record["paper_latest_bar_ts"] = audit_record.get("latest_bar_ts")
        record["paper_final_result"] = audit_record.get("final_result")
        record["paper_exit_code"] = audit_record.get("exit_code")
        record["paper_blocker"] = audit_record.get("blocker")

        # --- Age gate ---
        age = now_utc - audit_ts
        if age > timedelta(minutes=max_paper_audit_age_minutes):
            raise GateError(
                REASON_PAPER_AUDIT_STALE,
                f"paper audit is {age.total_seconds() / 60:.1f} minutes old "
                f"(max {max_paper_audit_age_minutes})",
            )

        # --- Fetch gate ---
        if audit_record.get("fetch_result") != "PASS":
            raise GateError(
                REASON_CACHE_REFRESH_NOT_PASS,
                f"paper audit fetch_result={audit_record.get('fetch_result')!r}",
            )

        # --- latest_bar_ts must be a parseable UTC timestamp ---
        latest_bar_ts_raw = audit_record.get("latest_bar_ts")
        if not isinstance(latest_bar_ts_raw, str):
            raise GateError(
                REASON_PAPER_AUDIT_TIMESTAMP_INVALID,
                "paper audit latest_bar_ts missing or not a string",
            )
        try:
            latest_bar_ts = _parse_ts(latest_bar_ts_raw)
        except Exception as exc:
            raise GateError(
                REASON_PAPER_AUDIT_TIMESTAMP_INVALID,
                f"paper audit latest_bar_ts unparseable: {exc}",
            ) from exc

        # --- Load + validate cache (read-only; never refreshed here) ---
        try:
            bars = load_cached_bars(cache_dir, _SYMBOL, _INTERVAL)
        except BacktestError as exc:
            raise GateError(REASON_CACHE_LOAD_FAILED, str(exc)) from exc
        if not bars:
            raise GateError(REASON_CACHE_LOAD_FAILED, "cache contains no bars")

        cache_latest_ts = _shadow_bar_ts_utc(bars[-1])
        record["cache_latest_bar_ts"] = cache_latest_ts.isoformat()
        matches = cache_latest_ts == latest_bar_ts
        record["cache_matches_paper_audit"] = matches
        if not matches:
            raise GateError(
                REASON_AUDIT_CACHE_TIMESTAMP_MISMATCH,
                f"cache latest bar {cache_latest_ts.isoformat()} != paper "
                f"audit latest_bar_ts {latest_bar_ts.isoformat()}",
            )

        # --- S62 preflight integrity (read-only) ---
        _preflight_validate_shadow(shadow_state_dir)

        # --- Gate passed: invoke (or dry-run) the S62 shadow runner ---
        record["shadow_invoked"] = True
        try:
            summary = _shadow_run_cycle(
                bars, state_dir=shadow_state_dir, now_utc=now_utc,
                dry_run=dry_run,
            )
        except ShadowError as exc:
            raise GateError(REASON_SHADOW_RUN_FAILED, str(exc)) from exc

        record["shadow_experiment_id"] = summary.get(
            "experiment_id", _SHADOW_EXPERIMENT_ID,
        )
        record["shadow_manifest_hash"] = summary.get("manifest_hash")
        record["shadow_forward_cutoff_utc"] = summary.get("forward_cutoff_utc")
        record["shadow_candidate_events_appended"] = summary.get(
            "candidate_events_appended",
            summary.get("candidate_events_would_append"),
        )
        record["shadow_experiment_events_appended"] = summary.get(
            "experiment_events_appended",
            summary.get("experiment_events_would_append"),
        )
        record["shadow_events_appended"] = summary.get(
            "events_appended", summary.get("events_would_append"),
        )
        record["result"] = "RUN"
        record["exit_code"] = 0
        record["reason_codes"] = []

    except GateError as exc:
        record["reason_codes"] = [exc.reason_code]
        record["shadow_error"] = str(exc) if exc.reason_code in (
            REASON_SHADOW_RUN_FAILED, REASON_SHADOW_INTEGRITY_ERROR,
        ) else record["shadow_error"]
        if exc.reason_code in _SKIPPED_REASON_CODES:
            record["result"] = "SKIPPED"
            record["exit_code"] = 0
        else:
            record["result"] = "ERROR"
            record["exit_code"] = 2

    # --- Persist the scheduler audit (unless dry-run without opt-in) ---
    should_persist = (not dry_run) or write_dry_run_audit
    if should_persist:
        try:
            _append_scheduler_audit(scheduler_audit_dir, record, now_utc)
        except OSError as exc:
            record["result"] = "ERROR"
            record["exit_code"] = 2
            record["reason_codes"] = [REASON_SCHEDULER_AUDIT_WRITE_FAILED]
            record["shadow_error"] = (
                record["shadow_error"] or f"scheduler audit write failed: {exc}"
            )

    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.run_scheduled_shadow_cycle",
        description=(
            "S63 isolated Windows scheduling gate for the S62 shadow "
            "experiment. Never invokes the automated paper-trading "
            "runner, never refreshes the cache, never touches broker "
            "state."
        ),
    )
    p.add_argument("--paper-audit-dir", default=str(_DEFAULT_PAPER_AUDIT_DIR),
                   help=f"Paper audit JSONL directory (default: {_DEFAULT_PAPER_AUDIT_DIR}).")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR),
                   help=f"SPY 60m cache directory (default: {_DEFAULT_CACHE_DIR}).")
    p.add_argument("--shadow-state-dir", default=str(_DEFAULT_SHADOW_STATE_DIR),
                   help=f"S62 shadow state directory (default: {_DEFAULT_SHADOW_STATE_DIR}).")
    p.add_argument("--scheduler-audit-dir", default=str(_DEFAULT_SCHEDULER_AUDIT_DIR),
                   help=f"S63 scheduler audit directory (default: {_DEFAULT_SCHEDULER_AUDIT_DIR}).")
    p.add_argument("--max-paper-audit-age-minutes", type=int,
                   default=_DEFAULT_MAX_PAPER_AUDIT_AGE_MINUTES,
                   help=f"Maximum paper-audit age to accept, in minutes "
                        f"(default: {_DEFAULT_MAX_PAPER_AUDIT_AGE_MINUTES}).")
    p.add_argument("--now-utc", default=None,
                   help="Diagnostic/test-only override for the current UTC "
                        "time, as an ISO-8601 timestamp. Defaults to the "
                        "real current time.")
    p.add_argument("--dry-run", action="store_true",
                   help="Perform all gate checks and call S62's dry-run "
                        "path when the gate passes; never mutate S62 "
                        "state, the paper audit, or the cache.")
    p.add_argument("--write-dry-run-audit", action="store_true",
                   help="Persist a scheduler audit record even when "
                        "--dry-run is set (default: dry-run writes no "
                        "scheduler audit).")
    p.add_argument("--json", action="store_true",
                   help="Present for interface compatibility with the "
                        "other S62 tools; output is always JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    now_utc = _parse_ts(args.now_utc) if args.now_utc else datetime.now(timezone.utc)

    record = run_gate(
        paper_audit_dir=Path(args.paper_audit_dir),
        cache_dir=Path(args.cache_dir),
        shadow_state_dir=Path(args.shadow_state_dir),
        scheduler_audit_dir=Path(args.scheduler_audit_dir),
        max_paper_audit_age_minutes=args.max_paper_audit_age_minutes,
        now_utc=now_utc,
        dry_run=args.dry_run,
        write_dry_run_audit=args.write_dry_run_audit,
    )
    print(json.dumps(record, indent=2, default=str, sort_keys=True))
    return int(record["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
