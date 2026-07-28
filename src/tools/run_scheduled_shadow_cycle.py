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
6. the S62 state directory is in exactly one of two acceptable
   states — fully uninitialized (manifest, state, and event log all
   absent) or fully initialized (manifest present and valid, state
   present and valid, event log validated with S62's own strict
   validator). Any other, partial combination (e.g. a manifest with
   no state, or state with no manifest) fails closed with
   ``SHADOW_INTEGRITY_ERROR`` before S62 is invoked and before any
   terminal audit record is written.

The paper audit's ``final_result`` is deliberately NOT part of the
gate — a broker, clock, credential, or paper-order failure that
happens *after* a successful cache refresh must not block
independent shadow research. ``final_result``, ``exit_code``, and
``blocker`` are recorded for diagnostics only.

Scheduler-audit durability contract
------------------------------------
Every invocation of this tool is represented in the scheduler audit
trail as one *logical* invocation — not "exactly one physical JSONL
record". Once gate checks 1-6 above have all passed and S62 is about
to be invoked:

1. a dedicated S63 single-writer lock is acquired (mirrors S62's own
   ``_FileLock`` pattern — an ``O_CREAT | O_EXCL`` lock file that is
   deleted on release, so a concurrent invocation fails closed rather
   than racing);
2. today's existing scheduler-audit JSONL file (if any) is validated
   (UTF-8, per-line JSON parseability, required trailing newline)
   before this invocation appends to it;
3. a ``STARTED``-phase record carrying a deterministic
   ``invocation_id`` is appended and ``fsync``'d;
4. only if that write succeeds is S62 actually invoked — if the
   ``STARTED`` record cannot be durably persisted, S62 is NOT
   invoked and the invocation is reported as a
   ``SCHEDULER_AUDIT_WRITE_FAILED`` error;
5. a ``TERMINAL``-phase record (``result`` of ``RUN`` or ``ERROR``,
   never ``SKIPPED`` at this point since every skip condition was
   already ruled out in steps 1-6) is appended and ``fsync``'d with
   the SAME ``invocation_id``;
6. the lock is released.

A process that is killed between steps 3 and 5 leaves a ``STARTED``
record with no matching ``TERMINAL`` record for that
``invocation_id`` — this is a detectable, distinguishable state (see
:func:`find_incomplete_invocations`), not a silent "nothing happened"
or a false claim that the gate left S62 unchanged. Gate failures that
occur BEFORE step 1 above (no qualifying audit, stale audit, cache
mismatch, etc.) never reach the write-ahead protocol at all — S62 was
never invoked, so a single ``TERMINAL``-phase record fully and safely
represents that outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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

# Sanity ceiling for --max-paper-audit-age-minutes. Beyond this, the
# staleness gate is effectively disabled, which defeats its purpose.
_MAX_REASONABLE_PAPER_AUDIT_AGE_MINUTES = 24 * 60

# Tolerance for "future" audit timestamps — allows for minor clock
# skew between the process that wrote the audit and this process,
# without accepting genuinely bogus far-future timestamps.
_FUTURE_TS_TOLERANCE = timedelta(seconds=5)

_SCHEDULER_LOCK_FILENAME = ".s63_scheduler.lock"

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
REASON_SCHEDULER_LOCK_CONFLICT = "SCHEDULER_LOCK_CONFLICT"
REASON_INVALID_ARGUMENT = "INVALID_ARGUMENT"
REASON_UNEXPECTED_ERROR = "UNEXPECTED_ERROR"

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


def _parse_aware_ts(s: str) -> datetime:
    """Parse an ISO-8601 timestamp that MUST already carry a UTC
    offset. Used only for the ``--now-utc`` CLI override, where
    silently assuming UTC on a naive input would let a mistyped
    local-time string masquerade as a valid override for a tool that
    can advance shadow-experiment state.
    """
    ts = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise ValueError(
            f"--now-utc {s!r} is a naive timestamp — an explicit UTC "
            "offset (e.g. +00:00 or Z) is required"
        )
    return dt.astimezone(timezone.utc)


def _validate_max_age(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise GateError(
            REASON_INVALID_ARGUMENT,
            f"--max-paper-audit-age-minutes must be an integer (got {value!r})",
        )
    if value <= 0:
        raise GateError(
            REASON_INVALID_ARGUMENT,
            f"--max-paper-audit-age-minutes must be > 0 (got {value})",
        )
    if value > _MAX_REASONABLE_PAPER_AUDIT_AGE_MINUTES:
        raise GateError(
            REASON_INVALID_ARGUMENT,
            f"--max-paper-audit-age-minutes {value} exceeds the sanity "
            f"ceiling of {_MAX_REASONABLE_PAPER_AUDIT_AGE_MINUTES} minutes",
        )
    return value


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


def _compute_invocation_id(
    paper_audit_hash: str | None,
    cache_latest_ts_iso: str | None,
    now_utc: datetime,
) -> str:
    """Deterministic invocation identifier — derived from the selected
    paper-audit record's hash, the cache's latest bar timestamp, and
    the scheduled invocation timestamp. Reproducible given the same
    inputs; never based on randomness or process-local state.
    """
    material = "|".join([
        paper_audit_hash or "",
        cache_latest_ts_iso or "",
        now_utc.astimezone(timezone.utc).isoformat(),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _compute_shadow_commit_hash(shadow_state_dir: Path) -> str | None:
    """Read-only fingerprint of the S62 state file as observed
    immediately after an invocation attempt — independent of whatever
    the S62 runner's own return value claims. ``None`` when no state
    file exists (e.g. bootstrap dry-run, or a failure before S62 ever
    wrote anything).
    """
    state_path = shadow_state_dir / _SHADOW_STATE_FILENAME
    if not state_path.exists():
        return None
    try:
        raw = state_path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# S62 preflight integrity check (read-only)
# ---------------------------------------------------------------------------


def _preflight_validate_shadow(shadow_state_dir: Path) -> None:
    """Read-only S62 integrity check performed BEFORE invoking the
    shadow runner.

    Only two states are acceptable:

    * **Uninitialized** — manifest, state, and event log all absent.
      The S62 runner may bootstrap the experiment on first ``--once``.
    * **Initialized** — manifest present and valid; state present and
      valid; event log validated with S62's own strict validator
      (which itself tolerates a missing event log only when the
      state proves no forward observations exist yet).

    Every other, partial combination — state or events present
    without a manifest, a manifest present but state missing, etc. —
    raises :class:`GateError` with reason ``SHADOW_INTEGRITY_ERROR``
    before S62 is ever invoked. Never mutates anything.
    """
    manifest_path = shadow_state_dir / _SHADOW_MANIFEST_FILENAME
    state_path = shadow_state_dir / _SHADOW_STATE_FILENAME
    events_path = shadow_state_dir / _SHADOW_EVENTS_FILENAME

    manifest_exists = manifest_path.exists()
    state_exists = state_path.exists()
    events_exists = events_path.exists()

    if not manifest_exists and not state_exists and not events_exists:
        return

    if not manifest_exists:
        raise GateError(
            REASON_SHADOW_INTEGRITY_ERROR,
            f"S62 state/events present without a manifest at "
            f"{shadow_state_dir} — partial initialization is not allowed",
        )

    try:
        manifest = _shadow_load_manifest_readonly(shadow_state_dir)
    except ShadowError as exc:
        raise GateError(REASON_SHADOW_INTEGRITY_ERROR, str(exc)) from exc

    if not state_exists:
        raise GateError(
            REASON_SHADOW_INTEGRITY_ERROR,
            f"S62 manifest present but state missing at {shadow_state_dir} "
            "— partial initialization is not allowed",
        )

    try:
        state = _shadow_load_state_readonly(shadow_state_dir, manifest)
    except ShadowError as exc:
        raise GateError(REASON_SHADOW_INTEGRITY_ERROR, str(exc)) from exc

    try:
        _shadow_validate_event_log_readonly(events_path, state, manifest)
    except ShadowError as exc:
        raise GateError(REASON_SHADOW_INTEGRITY_ERROR, str(exc)) from exc


# ---------------------------------------------------------------------------
# Scheduler single-writer lock (mirrors S62's own _FileLock pattern)
# ---------------------------------------------------------------------------


class _SchedulerLock:
    """Exclusive single-writer lock for the S63 write-ahead journal.

    An ``O_CREAT | O_EXCL`` lock file that is removed on release. A
    concurrent invocation fails closed with ``SCHEDULER_LOCK_CONFLICT``
    rather than racing to append the journal or invoke S62 twice.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_SchedulerLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GateError(
                REASON_SCHEDULER_AUDIT_WRITE_FAILED,
                f"could not create scheduler audit directory "
                f"{self.path.parent}: {exc}",
            ) from exc
        try:
            self.fd = os.open(
                str(self.path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise GateError(
                REASON_SCHEDULER_LOCK_CONFLICT,
                f"another S63 scheduler invocation is holding {self.path} "
                "— concurrent invocations are not allowed",
            ) from exc
        except OSError as exc:
            raise GateError(
                REASON_SCHEDULER_AUDIT_WRITE_FAILED,
                f"could not acquire scheduler lock {self.path}: {exc}",
            ) from exc
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                try:
                    self.path.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Scheduler audit
# ---------------------------------------------------------------------------

_SCHEDULER_RECORD_FIELDS = (
    "timestamp_utc", "tool", "invocation_id", "phase", "started_timestamp_utc",
    "completed_timestamp_utc", "result", "exit_code", "reason_codes",
    "paper_audit_path", "paper_audit_timestamp_utc",
    "paper_audit_record_sha256", "paper_mode", "paper_fetch_result",
    "paper_fetch_status", "paper_latest_bar_ts", "paper_final_result",
    "paper_exit_code", "paper_blocker", "cache_latest_bar_ts",
    "cache_matches_paper_audit", "shadow_invoked", "shadow_experiment_id",
    "shadow_manifest_hash", "shadow_forward_cutoff_utc",
    "shadow_candidate_events_appended", "shadow_experiment_events_appended",
    "shadow_events_appended", "shadow_commit_observed", "shadow_error",
    "dry_run", "research_only", "automatic_promotion_allowed",
    "terminal_audit_persisted",
)


def _new_scheduler_record(now_utc: datetime) -> dict[str, Any]:
    return {
        "timestamp_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "tool": TOOL_NAME,
        "invocation_id": None,
        "phase": None,
        "started_timestamp_utc": None,
        "completed_timestamp_utc": None,
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
        "shadow_commit_observed": None,
        "shadow_error": None,
        "dry_run": False,
        "research_only": True,
        "automatic_promotion_allowed": False,
        "terminal_audit_persisted": False,
    }


def _scheduler_audit_path(scheduler_audit_dir: Path, now_utc: datetime) -> Path:
    date_iso = now_utc.astimezone(timezone.utc).date().isoformat()
    return scheduler_audit_dir / f"{date_iso}.jsonl"


def _validate_scheduler_audit_log(scheduler_audit_dir: Path, now_utc: datetime) -> None:
    """Fail-closed validation of today's scheduler-audit file BEFORE
    this invocation appends to it — mirrors S62's own event-log
    validator: the file (if present and non-empty) must decode as
    UTF-8, every line must be independently parseable JSON, and it
    must end with a trailing newline so an append cannot silently
    concatenate onto a partial final record.
    """
    path = _scheduler_audit_path(scheduler_audit_dir, now_utc)
    if not path.exists():
        return
    raw = path.read_bytes()
    if not raw:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError(
            REASON_SCHEDULER_AUDIT_WRITE_FAILED,
            f"{path} is not valid UTF-8, refusing to append: {exc}",
        ) from exc
    if not raw.endswith(b"\n"):
        raise GateError(
            REASON_SCHEDULER_AUDIT_WRITE_FAILED,
            f"{path} does not end with a newline — appending would "
            "concatenate onto a partial record and corrupt the log",
        )
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise GateError(
                REASON_SCHEDULER_AUDIT_WRITE_FAILED,
                f"{path} line {line_no} is malformed, refusing to append: "
                f"{exc.msg}",
            ) from exc


def _append_scheduler_audit(
    scheduler_audit_dir: Path, record: dict[str, Any], now_utc: datetime,
) -> None:
    """Append one canonical JSON line to today's scheduler audit file,
    flushed and ``fsync``'d before returning.

    Raises OSError on any filesystem failure — callers convert this
    into a ``SCHEDULER_AUDIT_WRITE_FAILED`` outcome. Never touches S62
    state; this file lives entirely under ``scheduler_audit_dir``.
    """
    scheduler_audit_dir.mkdir(parents=True, exist_ok=True)
    path = _scheduler_audit_path(scheduler_audit_dir, now_utc)
    line = json.dumps(record, sort_keys=True, default=str, ensure_ascii=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def find_incomplete_invocations(scheduler_audit_dir: Path) -> list[dict[str, Any]]:
    """Read-only diagnostic: scan every ``*.jsonl`` scheduler-audit
    file and return ``STARTED``-phase records that have no matching
    ``TERMINAL``-phase record (same ``invocation_id``) anywhere in the
    directory.

    A non-empty result means a process was interrupted between
    persisting the write-ahead record and the terminal record — used
    by the operator runbook and by tests to prove that this failure
    mode remains detectable rather than silently indistinguishable
    from "S62 was never invoked". Never mutates anything.
    """
    started: dict[str, dict[str, Any]] = {}
    terminal_ids: set[str] = set()
    if not scheduler_audit_dir.is_dir():
        return []
    for path in sorted(scheduler_audit_dir.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            inv_id = obj.get("invocation_id")
            if not inv_id:
                continue
            if obj.get("phase") == "STARTED":
                started[inv_id] = obj
            elif obj.get("phase") == "TERMINAL":
                terminal_ids.add(inv_id)
    return [rec for inv_id, rec in started.items() if inv_id not in terminal_ids]


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
      1. read and validate the paper audit
      2. evaluate age and fetch gate
      3. load and validate cache
      4. compare audit/cache timestamps
      5. validate S62 manifest/state/events (binary: fully absent or
         fully valid — see :func:`_preflight_validate_shadow`)
      6. if dry_run (without --write-dry-run-audit), invoke S62's
         dry-run path directly and report without any scheduler
         audit persistence
      7. otherwise: acquire the S63 write-ahead lock, validate the
         existing scheduler-audit log, persist+fsync a STARTED
         record, invoke S62, persist+fsync a TERMINAL record with the
         same invocation_id, release the lock
      8. return the final record (exit_code carries the exit status)

    Never raises — every failure, including any exception type not
    explicitly anticipated above, is classified into the returned
    record's ``result``/``exit_code``/``reason_codes``. Only
    ``KeyboardInterrupt`` and ``SystemExit`` propagate unchanged.
    """
    record = _new_scheduler_record(now_utc)
    record["dry_run"] = dry_run

    paper_audit_hash: str | None = None
    cache_latest_iso: str | None = None
    should_persist = (not dry_run) or write_dry_run_audit

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
        paper_audit_hash = _hash_record(audit_record)
        record["paper_audit_record_sha256"] = paper_audit_hash
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
        except (KeyboardInterrupt, SystemExit):
            raise
        except BacktestError as exc:
            raise GateError(REASON_CACHE_LOAD_FAILED, str(exc)) from exc
        except Exception as exc:
            # Broad on purpose: pandas parser errors and OSError-family
            # read failures from the underlying cache loader are not
            # BacktestError and must not escape uncaught.
            raise GateError(
                REASON_CACHE_LOAD_FAILED, f"cache load failed: {exc}",
            ) from exc
        if not bars:
            raise GateError(REASON_CACHE_LOAD_FAILED, "cache contains no bars")

        cache_latest_ts = _shadow_bar_ts_utc(bars[-1])
        cache_latest_iso = cache_latest_ts.isoformat()
        record["cache_latest_bar_ts"] = cache_latest_iso
        matches = cache_latest_ts == latest_bar_ts
        record["cache_matches_paper_audit"] = matches
        if not matches:
            raise GateError(
                REASON_AUDIT_CACHE_TIMESTAMP_MISMATCH,
                f"cache latest bar {cache_latest_ts.isoformat()} != paper "
                f"audit latest_bar_ts {latest_bar_ts.isoformat()}",
            )

        # --- S62 preflight integrity (read-only, binary state) ---
        _preflight_validate_shadow(shadow_state_dir)

        # --- Gate passed. From here on S62 may be invoked. ---
        invocation_id = _compute_invocation_id(paper_audit_hash, cache_latest_iso, now_utc)
        record["invocation_id"] = invocation_id
        record["started_timestamp_utc"] = now_utc.astimezone(timezone.utc).isoformat()

        def _invoke_shadow() -> None:
            record["shadow_invoked"] = True
            try:
                summary = _shadow_run_cycle(
                    bars, state_dir=shadow_state_dir, now_utc=now_utc,
                    dry_run=dry_run,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except ShadowError as exc:
                record["shadow_error"] = str(exc)
                record["reason_codes"] = [REASON_SHADOW_RUN_FAILED]
                record["result"] = "ERROR"
                record["exit_code"] = 2
            except Exception as exc:
                record["shadow_error"] = f"unexpected S62 failure: {exc}"
                record["reason_codes"] = [REASON_SHADOW_RUN_FAILED]
                record["result"] = "ERROR"
                record["exit_code"] = 2
            else:
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
            record["shadow_commit_observed"] = _compute_shadow_commit_hash(shadow_state_dir)
            record["completed_timestamp_utc"] = now_utc.astimezone(timezone.utc).isoformat()

        if not should_persist:
            # Pure dry-run, no audit opt-in: S62's own dry-run path
            # mutates nothing, so the write-ahead protocol (which
            # exists to protect a real state mutation) does not apply.
            record["phase"] = "TERMINAL"
            _invoke_shadow()
            record["terminal_audit_persisted"] = False
        else:
            lock_path = scheduler_audit_dir / _SCHEDULER_LOCK_FILENAME
            with _SchedulerLock(lock_path):
                _validate_scheduler_audit_log(scheduler_audit_dir, now_utc)

                started_record = dict(record)
                started_record["phase"] = "STARTED"
                started_record["terminal_audit_persisted"] = False
                try:
                    _append_scheduler_audit(scheduler_audit_dir, started_record, now_utc)
                except OSError as exc:
                    raise GateError(
                        REASON_SCHEDULER_AUDIT_WRITE_FAILED,
                        f"could not persist write-ahead scheduler record: {exc}",
                    ) from exc

                record["phase"] = "TERMINAL"
                _invoke_shadow()

                record["terminal_audit_persisted"] = True
                try:
                    _append_scheduler_audit(scheduler_audit_dir, record, now_utc)
                except OSError as exc:
                    # The STARTED record is already durable — a missing
                    # TERMINAL record is now a detectable, distinguishable
                    # incomplete invocation, not a silent state change.
                    record["terminal_audit_persisted"] = False
                    record["result"] = "ERROR"
                    record["exit_code"] = 2
                    record["reason_codes"] = [REASON_SCHEDULER_AUDIT_WRITE_FAILED]
                    record["shadow_error"] = (
                        record["shadow_error"]
                        or f"terminal scheduler audit write failed: {exc}"
                    )

    except GateError as exc:
        record["reason_codes"] = [exc.reason_code]
        if exc.reason_code in (REASON_SHADOW_RUN_FAILED, REASON_SHADOW_INTEGRITY_ERROR):
            record["shadow_error"] = str(exc)
        if exc.reason_code in _SKIPPED_REASON_CODES:
            record["result"] = "SKIPPED"
            record["exit_code"] = 0
        else:
            record["result"] = "ERROR"
            record["exit_code"] = 2
        record["phase"] = "TERMINAL"
        if record.get("invocation_id") is None:
            record["invocation_id"] = _compute_invocation_id(
                paper_audit_hash, cache_latest_iso, now_utc,
            )
        record["completed_timestamp_utc"] = now_utc.astimezone(timezone.utc).isoformat()
        record["terminal_audit_persisted"] = False

        if should_persist:
            record["terminal_audit_persisted"] = True
            try:
                _append_scheduler_audit(scheduler_audit_dir, record, now_utc)
            except OSError as exc2:
                record["terminal_audit_persisted"] = False
                record["result"] = "ERROR"
                record["exit_code"] = 2
                record["reason_codes"] = [REASON_SCHEDULER_AUDIT_WRITE_FAILED]
                record["shadow_error"] = (
                    record["shadow_error"] or f"scheduler audit write failed: {exc2}"
                )

    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # Final safety net — guarantees this function truly never lets
        # an unclassified exception escape. Anything landing here is a
        # bug in the gate itself, not a normal operational failure.
        record["result"] = "ERROR"
        record["exit_code"] = 2
        record["reason_codes"] = [REASON_UNEXPECTED_ERROR]
        record["shadow_error"] = f"unexpected gate failure: {exc}"
        record["phase"] = "TERMINAL"
        if record.get("invocation_id") is None:
            record["invocation_id"] = _compute_invocation_id(
                paper_audit_hash, cache_latest_iso, now_utc,
            )
        record["completed_timestamp_utc"] = now_utc.astimezone(timezone.utc).isoformat()
        record["terminal_audit_persisted"] = False
        if should_persist:
            record["terminal_audit_persisted"] = True
            try:
                _append_scheduler_audit(scheduler_audit_dir, record, now_utc)
            except OSError:
                record["terminal_audit_persisted"] = False

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
                        "time, as an aware ISO-8601 timestamp (must carry a "
                        "UTC offset). Defaults to the real current time.")
    p.add_argument("--dry-run", action="store_true",
                   help="Perform all gate checks and call S62's dry-run "
                        "path when the gate passes; never mutate S62 "
                        "state, the paper audit, or the cache.")
    p.add_argument("--write-dry-run-audit", action="store_true",
                   help="Persist a scheduler audit record even when "
                        "--dry-run is set. Requires --dry-run.")
    p.add_argument("--json", action="store_true",
                   help="Present for interface compatibility with the "
                        "other S62 tools; output is always JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    real_now = datetime.now(timezone.utc)
    scheduler_audit_dir = Path(args.scheduler_audit_dir)

    try:
        if args.write_dry_run_audit and not args.dry_run:
            raise GateError(
                REASON_INVALID_ARGUMENT,
                "--write-dry-run-audit requires --dry-run",
            )
        max_age = _validate_max_age(args.max_paper_audit_age_minutes)
        if args.now_utc is not None:
            try:
                now_utc = _parse_aware_ts(args.now_utc)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                raise GateError(
                    REASON_INVALID_ARGUMENT, f"invalid --now-utc: {exc}",
                ) from exc
        else:
            now_utc = real_now
    except GateError as exc:
        record = _new_scheduler_record(real_now)
        record["phase"] = "TERMINAL"
        record["completed_timestamp_utc"] = real_now.isoformat()
        record["result"] = "ERROR"
        record["exit_code"] = 2
        record["reason_codes"] = [exc.reason_code]
        record["shadow_error"] = str(exc)
        record["invocation_id"] = _compute_invocation_id(None, None, real_now)
        record["terminal_audit_persisted"] = True
        try:
            _append_scheduler_audit(scheduler_audit_dir, record, real_now)
        except OSError:
            record["terminal_audit_persisted"] = False
        print(json.dumps(record, indent=2, default=str, sort_keys=True))
        return 2

    record = run_gate(
        paper_audit_dir=Path(args.paper_audit_dir),
        cache_dir=Path(args.cache_dir),
        shadow_state_dir=Path(args.shadow_state_dir),
        scheduler_audit_dir=scheduler_audit_dir,
        max_paper_audit_age_minutes=max_age,
        now_utc=now_utc,
        dry_run=args.dry_run,
        write_dry_run_audit=args.write_dry_run_audit,
    )
    print(json.dumps(record, indent=2, default=str, sort_keys=True))
    return int(record["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
