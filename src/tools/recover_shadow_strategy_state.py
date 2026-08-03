"""
tools/recover_shadow_strategy_state.py
=======================================
Deterministic recovery procedure for a S62 (``logs/shadow_strategy``)
state directory whose bookkeeping has drifted into an inconsistent
state — for example the equality-touch episode-bookkeeping bug where
``unique_bullish_episode_count < bullish_crossover_count``.

This repository is operated on Windows PowerShell, with the repo and
S62 state under a path such as ``F:\\gitrepo\\trading-bot``. This tool
is read-mostly with respect to the existing (possibly broken) state
directory and NEVER writes to it:

  1. Read the existing frozen S62 manifest from ``--state-dir``
     (read-only).
  2. Load the current SPY 60m cache from ``--cache-dir`` (read-only).
  3. Resolve ``work_dir`` (defaulting to a fresh sibling of
     ``--state-dir``, created under its parent — never under the OS
     temp directory) and validate, BEFORE creating or touching it in
     any way, that it is a distinct path from ``state_dir`` and that
     neither is nested inside the other (:func:`_resolve_work_dir` /
     :func:`_validate_distinct_and_unnested`). This check runs before
     any ``mkdir``, so an unsafe ``work_dir`` — e.g. one nested inside
     the broken ``state_dir`` — is rejected before this tool writes
     anything at all.
  4. Only once that is confirmed safe: create ``work_dir`` if needed,
     require it to be completely empty
     (:func:`_require_empty_work_dir`), and verify it lives on the
     same filesystem/volume as ``state_dir``
     (:func:`_verify_same_filesystem`) — a prerequisite for the later
     replacement rename to be atomic.
  5. Copy the existing, already-validated ``manifest.json`` byte-for-
     byte into ``work_dir``, then replay every cached bar into it
     using the exact same :func:`run_shadow_strategy_cycle.run_cycle`
     code path production uses. Copying the manifest instead of
     letting ``run_cycle`` mint a new one preserves its exact bytes —
     including ``created_at_utc`` — rather than silently resetting
     them.
  6. Validate the rebuilt manifest, state, and event log using S62's
     own strict validators (:func:`load_manifest_readonly`,
     :func:`load_state_readonly`, :func:`_validate_and_load_event_ids`),
     and verify a battery of invariants — see
     :func:`_verify_rebuilt_experiment` — including that the rebuilt
     manifest is byte-for-byte identical to the existing one and that
     a second replay against the same bars appends zero new events.
  7. Print the rebuilt directory's path and explicit **PowerShell**
     operator commands (``Copy-Item -Recurse`` / ``Move-Item``) to
     back up the broken directory and atomically replace it with the
     validated rebuild.

This tool NEVER touches ``--state-dir`` itself (no write, no delete),
NEVER executes the backup/replace commands it prints, NEVER creates or
enables a Windows Scheduled Task, and NEVER imports anything from the
paper-trading path. It only reads the frozen S62 manifest/cache and
writes to a brand-new work directory the operator explicitly controls
— and only after confirming that directory is not, in fact, some part
of the existing/broken state directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools import shadow_strategy_report as ssr
from src.tools.backtest_strategy_eval import BacktestError, load_cached_bars
from src.tools.run_shadow_strategy_cycle import (
    EXPERIMENT_ID,
    FORWARD_CUTOFF_UTC,
    ShadowError,
    _CANDIDATE_DEFINITIONS,
    _EVENTS_FILENAME,
    _MANIFEST_FILENAME,
    _STATE_FILENAME,
    _validate_and_load_event_ids,
    load_manifest_readonly,
    load_state_readonly,
    run_cycle,
)

TOOL_NAME = "recover_shadow_strategy_state"

_SYMBOL = "SPY"
_INTERVAL = "60m"

_DEFAULT_STATE_DIR = Path("logs/shadow_strategy")
_DEFAULT_CACHE_DIR = Path("data/cache")


class RecoveryError(Exception):
    """Raised on any recovery-procedure failure. The message explains
    exactly which step failed; the broken directory is never
    touched."""


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _read_existing_manifest(state_dir: Path) -> dict[str, Any]:
    try:
        return load_manifest_readonly(state_dir)
    except ShadowError as exc:
        raise RecoveryError(
            f"could not read the existing manifest at {state_dir}: {exc}"
        ) from exc


def _load_cache_bars(cache_dir: Path):
    try:
        return load_cached_bars(cache_dir, _SYMBOL, _INTERVAL)
    except BacktestError as exc:
        raise RecoveryError(
            f"could not load {_SYMBOL}/{_INTERVAL} cache from {cache_dir}: {exc}"
        ) from exc


def _default_work_dir(state_dir: Path) -> Path:
    """A fresh, empty work directory created BESIDE ``state_dir`` —
    under its parent, never under the OS temp directory — so the
    rebuild is guaranteed to land on the same filesystem/volume and a
    same-volume atomic rename is always possible later."""
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(
        prefix=f"{state_dir.name}_rebuild_", dir=str(state_dir.parent),
    ))


def _require_empty_work_dir(work_dir: Path) -> None:
    """The rebuild must start from a completely empty directory —
    refuse to reuse one that already has ANY entry in it (not just a
    manifest/state/events file — an arbitrary leftover file or a stale
    ``.lock`` is just as unsafe), since that would silently turn this
    into a continuation instead of a clean replay and would corrupt
    the "second replay appends zero events" check."""
    if work_dir.exists() and any(work_dir.iterdir()):
        entries = sorted(p.name for p in work_dir.iterdir())
        raise RecoveryError(
            f"work directory {work_dir} is not empty (contains "
            f"{entries}) — the rebuild must start from a completely "
            "empty directory. Pass a fresh --work-dir or remove its "
            "contents first."
        )


def _copy_existing_manifest(state_dir: Path, work_dir: Path) -> None:
    """Copy the already-validated existing manifest.json byte-for-byte
    into the fresh work directory, so ``run_cycle`` loads and validates
    it rather than minting a brand-new one — preserving its exact
    bytes, including ``created_at_utc``, instead of silently resetting
    them.

    Callers MUST have already validated path safety
    (:func:`_validate_distinct_and_unnested`) and directory emptiness
    (:func:`_require_empty_work_dir`) before calling this — it is the
    first point at which anything is written to ``work_dir``.
    """
    shutil.copy2(state_dir / _MANIFEST_FILENAME, work_dir / _MANIFEST_FILENAME)


def _replay(work_dir: Path, bars, now_utc: datetime) -> dict[str, Any]:
    try:
        first = run_cycle(bars, state_dir=work_dir, now_utc=now_utc)
    except ShadowError as exc:
        raise RecoveryError(f"replay into {work_dir} failed: {exc}") from exc
    try:
        second = run_cycle(bars, state_dir=work_dir, now_utc=now_utc)
    except ShadowError as exc:
        raise RecoveryError(
            f"second replay into {work_dir} (idempotency check) failed: {exc}"
        ) from exc
    if second["events_appended"] != 0:
        raise RecoveryError(
            f"second replay into {work_dir} appended "
            f"{second['events_appended']} new events — the rebuild is not "
            "idempotent, refusing to recommend it"
        )
    return first


def _verify_rebuilt_experiment(
    *,
    state_dir: Path,
    existing_manifest: dict[str, Any],
    work_dir: Path,
) -> dict[str, Any]:
    """Validate the rebuilt manifest/state/event log and check every
    invariant required before this rebuild may be recommended for
    promotion into the real state directory. Raises
    :class:`RecoveryError` on the first failure; never mutates
    anything."""
    checks: dict[str, Any] = {}

    try:
        rebuilt_manifest = load_manifest_readonly(work_dir)
    except ShadowError as exc:
        raise RecoveryError(f"rebuilt manifest failed validation: {exc}") from exc

    try:
        rebuilt_state = load_state_readonly(work_dir, rebuilt_manifest)
    except ShadowError as exc:
        raise RecoveryError(f"rebuilt state failed validation: {exc}") from exc

    events_path = work_dir / _EVENTS_FILENAME
    try:
        event_ids = _validate_and_load_event_ids(
            events_path, rebuilt_manifest, require_terminated=True,
        )
    except ShadowError as exc:
        raise RecoveryError(f"rebuilt event log failed validation: {exc}") from exc

    # All event IDs unique — the validator above already enforces this
    # (it raises on a duplicate), but confirm explicitly for the
    # operator-facing report by cross-checking the physical line count.
    raw_lines = [
        line for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    checks["event_log_line_count"] = len(raw_lines)
    checks["unique_event_id_count"] = len(event_ids)
    if len(raw_lines) != len(event_ids):
        raise RecoveryError(
            f"event log has {len(raw_lines)} physical lines but only "
            f"{len(event_ids)} unique event IDs — duplicate or "
            "unaccounted-for records"
        )

    # experiment_id remains S62_SPY_60M_FORWARD.
    if rebuilt_manifest["experiment_id"] != EXPERIMENT_ID:
        raise RecoveryError(
            f"rebuilt experiment_id {rebuilt_manifest['experiment_id']!r} "
            f"!= frozen {EXPERIMENT_ID!r}"
        )
    checks["experiment_id"] = rebuilt_manifest["experiment_id"]

    # The five frozen candidates are unchanged.
    expected_candidates = [dict(c) for c in _CANDIDATE_DEFINITIONS]
    if rebuilt_manifest["candidates"] != expected_candidates:
        raise RecoveryError(
            "rebuilt manifest candidates do not match the frozen "
            "_CANDIDATE_DEFINITIONS"
        )
    if existing_manifest["candidates"] != expected_candidates:
        raise RecoveryError(
            "existing (broken) manifest candidates do not match the "
            "frozen _CANDIDATE_DEFINITIONS — this is not the drift this "
            "tool recovers from; investigate manually before proceeding"
        )
    checks["candidate_count"] = len(rebuilt_manifest["candidates"])
    checks["candidate_ids"] = [c["id"] for c in rebuilt_manifest["candidates"]]

    # Manifest provenance: the rebuilt manifest.json must be
    # byte-for-byte identical to the existing one (it was copied, not
    # regenerated) — including created_at_utc, which must never be
    # silently reset by a rebuild.
    existing_manifest_bytes = (state_dir / _MANIFEST_FILENAME).read_bytes()
    rebuilt_manifest_bytes = (work_dir / _MANIFEST_FILENAME).read_bytes()
    if existing_manifest_bytes != rebuilt_manifest_bytes:
        raise RecoveryError(
            "rebuilt manifest.json is not byte-for-byte identical to the "
            "existing manifest.json — refusing to recommend a rebuild "
            "that would alter manifest provenance (including "
            "created_at_utc)"
        )
    checks["manifest_bytes_identical_to_existing"] = True
    checks["manifest_created_at_utc"] = rebuilt_manifest["created_at_utc"]

    # Manifest hash unchanged — implied by the byte-identity check
    # above, kept as an explicit, independent assertion.
    existing_hash = existing_manifest["candidate_manifest_sha256"]
    rebuilt_hash = rebuilt_manifest["candidate_manifest_sha256"]
    if existing_hash != rebuilt_hash:
        raise RecoveryError(
            f"existing manifest hash {existing_hash!r} != rebuilt "
            f"manifest hash {rebuilt_hash!r} — refusing to recommend "
            "a rebuild that would change the frozen manifest identity"
        )
    checks["manifest_hash"] = rebuilt_hash

    if rebuilt_manifest["forward_cutoff_utc"] != FORWARD_CUTOFF_UTC:
        raise RecoveryError(
            f"rebuilt forward_cutoff_utc "
            f"{rebuilt_manifest['forward_cutoff_utc']!r} != frozen "
            f"{FORWARD_CUTOFF_UTC!r}"
        )

    report = ssr.build_report(rebuilt_manifest, rebuilt_state)
    if report["research_only"] is not True:
        raise RecoveryError("rebuilt report research_only is not true")
    if report["automatic_strategy_promotion_allowed"] is not False:
        raise RecoveryError(
            "rebuilt report automatic_strategy_promotion_allowed is not false"
        )
    if report["validation_status"]["promotion_eligible"] is not False:
        raise RecoveryError(
            "rebuilt report validation_status.promotion_eligible is not false"
        )
    checks["research_only"] = report["research_only"]
    checks["automatic_strategy_promotion_allowed"] = (
        report["automatic_strategy_promotion_allowed"]
    )
    checks["promotion_eligible"] = report["validation_status"]["promotion_eligible"]

    return checks


# ---------------------------------------------------------------------------
# Replacement safety (path/volume layout) — fail closed
#
# Split into two functions because they must run at different points:
# the path-relationship check is pure path arithmetic and must run
# BEFORE work_dir is ever created or written to (an unsafe work_dir —
# e.g. nested inside the broken state_dir — must be rejected before
# this tool touches the filesystem at all); the same-filesystem check
# needs both directories to already exist, so it can only run after
# work_dir has been created (but still before anything is copied or
# replayed into it).
# ---------------------------------------------------------------------------


def _is_within(inner: Path, outer: Path) -> bool:
    try:
        return inner.resolve().is_relative_to(outer.resolve())
    except OSError:
        return False


def _validate_distinct_and_unnested(state_dir: Path, work_dir: Path) -> None:
    """Pure path-relationship validation — safe to call before
    ``work_dir`` exists. Requires ``state_dir`` and ``work_dir`` to be
    distinct paths, with neither nested inside the other. MUST run
    before any ``mkdir``, ``shutil.copy2``, or ``run_cycle`` call
    against ``work_dir`` — otherwise an unsafe ``work_dir`` (e.g. one
    nested inside the broken ``state_dir``) would already have been
    written to by the time this raises.
    """
    state_resolved = state_dir.resolve()
    work_resolved = work_dir.resolve()
    if state_resolved == work_resolved:
        raise RecoveryError(
            "work_dir and state_dir resolve to the same path — refusing "
            "to recommend a self-replacement"
        )
    if _is_within(work_resolved, state_resolved):
        raise RecoveryError(
            f"work_dir {work_dir} is inside state_dir {state_dir} — "
            "refusing to write into the existing/broken state directory"
        )
    if _is_within(state_resolved, work_resolved):
        raise RecoveryError(
            f"state_dir {state_dir} is inside work_dir {work_dir} — "
            "refusing to recommend this replacement layout"
        )


def _same_filesystem(a: Path, b: Path) -> bool:
    """True iff ``a`` and ``b`` live on the same filesystem/volume — a
    prerequisite for a same-volume rename (``Move-Item``/``os.rename``)
    to be atomic. Separated into its own function so tests can
    simulate a cross-volume layout without needing two real drives."""
    return os.stat(a).st_dev == os.stat(b).st_dev


def _verify_same_filesystem(state_dir: Path, work_dir: Path) -> None:
    """Requires ``state_dir`` and ``work_dir`` to live on the same
    filesystem/volume. Both must already exist. A same-volume
    directory rename is atomic; a cross-volume move is a
    copy-then-delete and must never be recommended as if it were
    atomic."""
    if not _same_filesystem(state_dir, work_dir):
        raise RecoveryError(
            f"work_dir {work_dir} is not on the same filesystem/volume "
            f"as state_dir {state_dir} — a cross-volume move is a "
            "copy-then-delete, not an atomic rename, and this tool "
            "refuses to recommend it as if it were one"
        )


def _resolve_work_dir(state_dir: Path, work_dir: Path | None) -> Path:
    """Resolve the work directory, validating path safety BEFORE any
    filesystem mutation.

    * Explicit ``work_dir``: validated with
      :func:`_validate_distinct_and_unnested` and then created — in
      that order — so a self-referencing or nested path is rejected
      before it is ever created, let alone written to.
    * Default (``work_dir is None``): :func:`_default_work_dir` always
      creates a fresh sibling of ``state_dir`` with a unique name, so
      it can never equal or nest inside ``state_dir`` by construction;
      it is still validated for uniformity and defense in depth.
    """
    if work_dir is None:
        work_dir = _default_work_dir(state_dir)
        _validate_distinct_and_unnested(state_dir, work_dir)
        return work_dir
    _validate_distinct_and_unnested(state_dir, work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


# ---------------------------------------------------------------------------
# Operator guidance (PowerShell — printed only, never executed)
# ---------------------------------------------------------------------------


def _operator_commands(state_dir: Path, work_dir: Path, now_utc: datetime) -> str:
    ts = now_utc.strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{state_dir}.broken.{ts}"
    superseded_path = f"{state_dir}.superseded.{ts}"
    return (
        "# This tool has NOT modified {state_dir} and will NOT run the\n"
        "# commands below for you. Review the validation results above,\n"
        "# then run these manually, in order, from a PowerShell prompt\n"
        "# (this repository is operated on Windows):\n"
        "\n"
        "# 1. Back up the broken directory (kept, never deleted):\n"
        "Copy-Item -LiteralPath \"{state_dir}\" -Destination "
        "\"{backup_path}\" -Recurse\n"
        "\n"
        "# 2. Atomically replace it with the validated rebuild. Both\n"
        "#    Move-Item calls are same-volume renames (verified above),\n"
        "#    so each one is atomic; the broken directory is renamed\n"
        "#    aside — not deleted — so this is reversible if anything\n"
        "#    looks wrong afterward:\n"
        "Move-Item -LiteralPath \"{state_dir}\" -Destination "
        "\"{superseded_path}\"\n"
        "Move-Item -LiteralPath \"{work_dir}\" -Destination \"{state_dir}\"\n"
        "\n"
        "# 3. Sanity-check the replacement:\n"
        "python -m src.tools.run_shadow_strategy_cycle --state-dir "
        "\"{state_dir}\" --status\n"
        "python -m src.tools.shadow_strategy_report --state-dir \"{state_dir}\"\n"
    ).format(
        state_dir=state_dir, work_dir=work_dir,
        backup_path=backup_path, superseded_path=superseded_path,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def recover(
    *,
    state_dir: Path,
    cache_dir: Path,
    work_dir: Path | None = None,
    now_utc: datetime,
) -> dict[str, Any]:
    """Run the full read-mostly recovery procedure. Raises
    :class:`RecoveryError` on any failure. Never mutates ``state_dir``.

    ``work_dir`` defaults to a fresh directory created beside
    ``state_dir`` (under its parent) — never under the OS temp
    directory — so it is guaranteed to be on the same volume.

    Ordering is safety-critical: every check that can be performed
    without creating or writing to ``work_dir`` runs BEFORE it is
    created or touched, so an unsafe ``work_dir`` (self-referencing,
    or nested inside the broken ``state_dir``) is rejected before this
    tool writes anything at all —

      1. resolve work_dir
      2. validate distinct / non-nested paths
      3. create work_dir if needed
      4. require a completely empty directory
      5. verify same filesystem/volume
      6. copy the existing manifest
      7. replay
      8. validate the rebuilt artifacts
      9. generate PowerShell guidance
    """
    existing_manifest = _read_existing_manifest(state_dir)
    bars = _load_cache_bars(cache_dir)
    if not bars:
        raise RecoveryError(f"no {_SYMBOL}/{_INTERVAL} bars loaded from {cache_dir}")

    work_dir = _resolve_work_dir(state_dir, work_dir)
    _require_empty_work_dir(work_dir)
    _verify_same_filesystem(state_dir, work_dir)

    _copy_existing_manifest(state_dir, work_dir)
    first_replay = _replay(work_dir, bars, now_utc)
    checks = _verify_rebuilt_experiment(
        state_dir=state_dir, existing_manifest=existing_manifest,
        work_dir=work_dir,
    )

    return {
        "tool": TOOL_NAME,
        "state_dir": str(state_dir),
        "cache_dir": str(cache_dir),
        "work_dir": str(work_dir),
        "bars_replayed": len(bars),
        "first_replay_events_appended": first_replay["events_appended"],
        "checks": checks,
        "existing_state_dir_modified": False,
        "operator_commands": _operator_commands(state_dir, work_dir, now_utc),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.recover_shadow_strategy_state",
        description=(
            "Deterministic S62 shadow-state recovery: replay the frozen "
            "manifest against the current cache into a fresh work "
            "directory, validate it, and print (never execute) the "
            "PowerShell operator commands to back up and atomically "
            "replace the broken state directory."
        ),
    )
    p.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR),
                   help=f"Existing (possibly broken) S62 state directory "
                        f"to diagnose — read-only (default: {_DEFAULT_STATE_DIR}).")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR),
                   help=f"SPY 60m cache directory (default: {_DEFAULT_CACHE_DIR}).")
    p.add_argument("--work-dir", default=None,
                   help="Fresh, completely empty directory to rebuild "
                        "into. Defaults to a new directory created "
                        "beside --state-dir (under its parent, on the "
                        "same volume), printed in the output.")
    p.add_argument("--json", action="store_true",
                   help="Print only the JSON summary (operator commands "
                        "are still included as a field).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    now_utc = datetime.now(timezone.utc)
    state_dir = Path(args.state_dir)
    cache_dir = Path(args.cache_dir)
    # Do NOT create --work-dir here: it must not be touched until
    # recover() has validated it is not the same as, or nested inside,
    # state_dir. Creating it eagerly would write into an unsafe path
    # (e.g. a work_dir nested inside the broken state directory)
    # before that check ever runs.
    work_dir = Path(args.work_dir) if args.work_dir is not None else None

    try:
        summary = recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=now_utc,
        )
    except RecoveryError as exc:
        payload = {
            "tool": TOOL_NAME,
            "result": "ERROR",
            "error": str(exc),
            "state_dir": str(state_dir),
            "work_dir": str(work_dir) if work_dir is not None else None,
            "existing_state_dir_modified": False,
        }
        print(json.dumps(payload, indent=2, default=str, sort_keys=True))
        return 2

    summary["result"] = "OK"
    if args.json:
        print(json.dumps(summary, indent=2, default=str, sort_keys=True))
    else:
        printable = dict(summary)
        commands = printable.pop("operator_commands")
        print(json.dumps(printable, indent=2, default=str, sort_keys=True))
        print()
        print(commands)
    return 0


if __name__ == "__main__":
    sys.exit(main())
