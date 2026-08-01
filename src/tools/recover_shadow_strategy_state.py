"""
tools/recover_shadow_strategy_state.py
=======================================
Deterministic recovery procedure for a S62 (``logs/shadow_strategy``)
state directory whose bookkeeping has drifted into an inconsistent
state — for example the equality-touch episode-bookkeeping bug where
``unique_bullish_episode_count < bullish_crossover_count``.

This tool is read-mostly with respect to the existing (possibly
broken) state directory and NEVER writes to it:

  1. Read the existing frozen S62 manifest from ``--state-dir``
     (read-only).
  2. Load the current SPY 60m cache from ``--cache-dir`` (read-only).
  3. Replay every cached bar — from the frozen forward cutoff onward —
     into a FRESH, empty work directory, using the exact same
     :func:`run_shadow_strategy_cycle.run_cycle` code path production
     uses. This is a from-scratch rebuild: the work directory must not
     already contain a manifest, state, or event log.
  4. Validate the rebuilt manifest, state, and event log using S62's
     own strict validators (:func:`load_manifest_readonly`,
     :func:`load_state_readonly`, :func:`_validate_and_load_event_ids`).
  5. Verify a battery of invariants — see
     :func:`_verify_rebuilt_experiment` — including that a second
     replay against the same bars appends zero new events.
  6. Print the rebuilt directory's path and explicit operator shell
     commands to back up the broken directory and atomically replace
     it with the validated rebuild.

This tool NEVER touches ``--state-dir`` itself (no write, no delete),
NEVER executes the backup/replace commands it prints, NEVER creates or
enables a Windows Scheduled Task, and NEVER imports anything from the
paper-trading path. It only reads the frozen S62 manifest/cache and
writes to a brand-new work directory the operator explicitly controls.
"""

from __future__ import annotations

import argparse
import json
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


def _require_empty_work_dir(work_dir: Path) -> None:
    """The rebuild must start from scratch — refuse to reuse a
    directory that already has any S62 artifact in it, since that
    would silently turn this into a continuation instead of a clean
    replay and would corrupt the "second replay appends zero events"
    check."""
    for filename in (_MANIFEST_FILENAME, _STATE_FILENAME, _EVENTS_FILENAME):
        if (work_dir / filename).exists():
            raise RecoveryError(
                f"work directory {work_dir} already contains {filename} — "
                "the rebuild must start from an empty directory. Pass a "
                "fresh --work-dir or remove its contents first."
            )


def _replay(work_dir: Path, bars, now_utc: datetime) -> dict[str, Any]:
    _require_empty_work_dir(work_dir)
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

    # Manifest hash is unchanged between the existing (broken) manifest
    # and the freshly rebuilt one — both are pure functions of the
    # frozen candidates + cutoff + execution/commission/slippage
    # constants, so they must match exactly.
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
# Operator guidance (printed only — never executed by this tool)
# ---------------------------------------------------------------------------


def _operator_commands(state_dir: Path, work_dir: Path, now_utc: datetime) -> str:
    ts = now_utc.strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{state_dir}.broken.{ts}"
    superseded_path = f"{state_dir}.superseded.{ts}"
    return (
        "# This tool has NOT modified {state_dir} and will NOT run the\n"
        "# commands below for you. Review the validation results above,\n"
        "# then run these manually, in order, from the repository root:\n"
        "\n"
        "# 1. Back up the broken directory (kept, never deleted):\n"
        "cp -a \"{state_dir}\" \"{backup_path}\"\n"
        "\n"
        "# 2. Atomically replace it with the validated rebuild — the\n"
        "#    broken directory is renamed aside (not deleted) so this\n"
        "#    step is reversible if anything looks wrong afterward:\n"
        "mv \"{state_dir}\" \"{superseded_path}\"\n"
        "mv \"{work_dir}\" \"{state_dir}\"\n"
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
    work_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    """Run the full read-mostly recovery procedure. Raises
    :class:`RecoveryError` on any failure. Never mutates ``state_dir``.
    """
    existing_manifest = _read_existing_manifest(state_dir)
    bars = _load_cache_bars(cache_dir)
    if not bars:
        raise RecoveryError(f"no {_SYMBOL}/{_INTERVAL} bars loaded from {cache_dir}")

    first_replay = _replay(work_dir, bars, now_utc)
    checks = _verify_rebuilt_experiment(
        existing_manifest=existing_manifest, work_dir=work_dir,
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
            "operator commands to back up and atomically replace the "
            "broken state directory."
        ),
    )
    p.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR),
                   help=f"Existing (possibly broken) S62 state directory "
                        f"to diagnose — read-only (default: {_DEFAULT_STATE_DIR}).")
    p.add_argument("--cache-dir", default=str(_DEFAULT_CACHE_DIR),
                   help=f"SPY 60m cache directory (default: {_DEFAULT_CACHE_DIR}).")
    p.add_argument("--work-dir", default=None,
                   help="Fresh, empty directory to rebuild into. Must not "
                        "already contain a manifest/state/event log. "
                        "Defaults to a new temporary directory, printed "
                        "in the output.")
    p.add_argument("--json", action="store_true",
                   help="Print only the JSON summary (operator commands "
                        "are still included as a field).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    now_utc = datetime.now(timezone.utc)
    state_dir = Path(args.state_dir)
    cache_dir = Path(args.cache_dir)
    if args.work_dir is not None:
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="s62_shadow_recovery_"))

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
            "work_dir": str(work_dir),
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
