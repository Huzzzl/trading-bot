"""
research/report_persistence.py
-----------------------------------
S10: Controlled local report persistence.

Persists PipelineRunResult objects produced by S8
run_offline_research_pipeline() to a caller-supplied local directory.
Produces deterministic JSON (sort_keys=True, indent=2).
Schema version: "S9/1.0".

Fail-closed rules (write nothing and return BLOCKED):
  - output_dir is None or empty string.
  - Any safety flag on the pipeline result is True.
  - pipeline_result.live_trading_allowed is True (explicit redundant check).
  - output_dir resolves into data/ or output/ under the repo root.
  - output_dir is an absolute path and allow_unsafe_path=False.
  - Final run directory already exists and allow_overwrite=False.

Atomic write:
  All files are written to a temporary sibling directory first.
  The temp directory is renamed to the final run directory only after all
  files are successfully prepared.
  On any write failure the temp directory is cleaned up and no partial
  run directory is left.

No broker/API/credential/env/network/order/live/paper access.
No live/paper trading approval. No parameter optimisation.
No files are written unless output_dir is explicitly supplied.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
from dataclasses import dataclass
from typing import Any

from src.research.pipeline_orchestrator import CandidateFilter, PipelineRunResult
from src.research.report_schema import research_report_to_dict

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "S9/1.0"

_SAFETY_FLAGS: tuple[str, ...] = (
    "broker_calls_made",
    "credentials_read",
    "network_calls_made",
    "order_action_requested",
    "live_trading_allowed",
)

_ALL_FALSE: dict[str, bool] = {flag: False for flag in _SAFETY_FLAGS}

_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_FORBIDDEN_DIRS: tuple[pathlib.Path, ...] = (
    (_REPO_ROOT / "data").resolve(),
    (_REPO_ROOT / "output").resolve(),
)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportPersistenceResult:
    """Immutable result of persist_pipeline_run_result().

    result is "PASS" on successful write, "BLOCKED" when a validation
    check prevents writing (output_dir missing, safety flag set, path
    forbidden, overwrite refused), or "ERROR" on unexpected failure.
    All five safety flags are always False — they represent actions taken
    by the persistence function itself, which never contacts brokers,
    credentials, network, or orders.
    """
    result: str
    blocker: str | None
    output_dir: str | None
    run_id: str | None
    manifest_path: str | None
    pipeline_summary_path: str | None
    report_paths: tuple[str, ...]
    markdown_summary_path: str | None
    files_written: tuple[str, ...]
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blocked(blocker: str) -> ReportPersistenceResult:
    return ReportPersistenceResult(
        result="BLOCKED",
        blocker=blocker,
        output_dir=None,
        run_id=None,
        manifest_path=None,
        pipeline_summary_path=None,
        report_paths=(),
        markdown_summary_path=None,
        files_written=(),
        **_ALL_FALSE,
    )


def _make_run_id(generated_at_utc: str, result: str) -> str:
    """Build a deterministic run ID from timestamp and result."""
    dt = datetime.datetime.fromisoformat(generated_at_utc)
    compact = dt.strftime("%Y%m%dT%H%M%SZ")
    safe_result = "".join(c if c.isalnum() else "_" for c in result.upper())
    return f"{compact}_{safe_result}"


def _check_output_path(
    output_dir: pathlib.Path,
    allow_unsafe_path: bool,
) -> str | None:
    """Return a blocker string if the path is forbidden, else None.

    data/ and output/ are always forbidden.
    Absolute paths are forbidden unless allow_unsafe_path=True.
    """
    resolved = output_dir.resolve()
    for forbidden in _FORBIDDEN_DIRS:
        if resolved == forbidden or resolved.is_relative_to(forbidden):
            return (
                f"output_dir must not point into {forbidden.name}/: {output_dir}"
            )
    if output_dir.is_absolute() and not allow_unsafe_path:
        return (
            f"absolute output_dir requires allow_unsafe_path=True: {output_dir}"
        )
    return None


def _candidate_filter_to_dict(f: CandidateFilter) -> dict[str, Any]:
    return {
        "groups": list(f.groups),
        "symbols": list(f.symbols),
        "intervals": list(f.intervals),
        "strategy_families": list(f.strategy_families),
        "holding_horizons": list(f.holding_horizons),
        "max_candidates": f.max_candidates,
    }


def _pipeline_summary_to_dict(
    pipeline_result: PipelineRunResult,
    run_id: str,
    git_commit_sha: str | None,
) -> dict[str, Any]:
    s = pipeline_result.summary
    return {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "git_commit_sha": git_commit_sha,
        "result": s.result,
        "blocker": s.blocker,
        "candidates_selected": s.candidates_selected,
        "reports_created": s.reports_created,
        "reports_passed": s.reports_passed,
        "reports_blocked": s.reports_blocked,
        "reports_error": s.reports_error,
        "best_candidate_id": s.best_candidate_id,
        "best_average_monthly_return": s.best_average_monthly_return,
        "worst_max_drawdown": s.worst_max_drawdown,
        "total_trades_sum": s.total_trades_sum,
        "candidate_filter": _candidate_filter_to_dict(pipeline_result.filter),
        "safety": {flag: getattr(pipeline_result, flag) for flag in _SAFETY_FLAGS},
    }


def _make_manifest_dict(
    pipeline_result: PipelineRunResult,
    run_id: str,
    generated_at_utc: str,
    git_commit_sha: str | None,
    report_filenames: list[str],
) -> dict[str, Any]:
    s = pipeline_result.summary
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "run_id": run_id,
        "result": pipeline_result.result,
        "blocker": pipeline_result.blocker,
        "git_commit_sha": git_commit_sha,
        "candidate_filter": _candidate_filter_to_dict(pipeline_result.filter),
        "counts": {
            "candidates_selected": s.candidates_selected,
            "reports_created": s.reports_created,
            "reports_passed": s.reports_passed,
            "reports_blocked": s.reports_blocked,
            "reports_error": s.reports_error,
        },
        "best_candidate_id": s.best_candidate_id,
        "best_average_monthly_return": s.best_average_monthly_return,
        "worst_max_drawdown": s.worst_max_drawdown,
        "total_trades_sum": s.total_trades_sum,
        "safety": {flag: getattr(pipeline_result, flag) for flag in _SAFETY_FLAGS},
        "files": {
            "pipeline_summary": "pipeline_summary.json",
            "reports": report_filenames,
        },
    }


def _make_markdown_summary(
    pipeline_result: PipelineRunResult,
    run_id: str,
    generated_at_utc: str,
    git_commit_sha: str | None,
) -> str:
    s = pipeline_result.summary
    lines = [
        f"# Research Pipeline Run: {run_id}",
        "",
        f"- **Result**: {pipeline_result.result}",
        f"- **Generated**: {generated_at_utc}",
    ]
    if git_commit_sha:
        lines.append(f"- **Git commit**: {git_commit_sha}")
    if pipeline_result.blocker:
        lines.append(f"- **Blocker**: {pipeline_result.blocker}")
    lines += [
        "",
        "## Counts",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Candidates selected | {s.candidates_selected} |",
        f"| Reports created | {s.reports_created} |",
        f"| Reports passed | {s.reports_passed} |",
        f"| Reports blocked | {s.reports_blocked} |",
        f"| Reports error | {s.reports_error} |",
        "",
        "## Best Candidate",
        "",
        f"- **ID**: {s.best_candidate_id}",
        f"- **Average monthly return**: {s.best_average_monthly_return}",
        f"- **Worst max drawdown**: {s.worst_max_drawdown}",
        f"- **Total trades**: {s.total_trades_sum}",
        "",
        "---",
        "",
        "*Offline research report. Does not approve live or paper trading.*",
    ]
    return "\n".join(lines) + "\n"


def _write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, sort_keys=True, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def persist_pipeline_run_result(
    pipeline_result: PipelineRunResult,
    *,
    output_dir: str | pathlib.Path | None,
    generated_at_utc: str | None = None,
    git_commit_sha: str | None = None,
    include_markdown: bool = False,
    allow_overwrite: bool = False,
    allow_unsafe_path: bool = False,
    _before_rename_hook=None,
) -> ReportPersistenceResult:
    """Persist an offline pipeline run result to a controlled local directory.

    Parameters
    ----------
    pipeline_result:
        The PipelineRunResult from run_offline_research_pipeline().
    output_dir:
        Caller-supplied directory under which a run-specific subdirectory
        is created. Must be supplied explicitly; None or empty → BLOCKED.
        May not resolve into data/ or output/. Absolute paths require
        allow_unsafe_path=True.
    generated_at_utc:
        ISO-8601 UTC timestamp for the run ID and manifest. Injectable for
        deterministic tests; defaults to current UTC time when None.
    git_commit_sha:
        Optional 40-char git commit SHA for provenance; not read by this
        function (caller supplies if desired).
    include_markdown:
        If True, write a human-readable summary.md. No files sent to network.
    allow_overwrite:
        If True, atomically replace an existing run directory. Default False.
    allow_unsafe_path:
        If True, allow absolute output_dir paths outside the repo root.
        data/ and output/ are always refused regardless of this flag.
    _before_rename_hook:
        Test injection: callable() called just before the atomic rename.
        Raise from here to simulate a mid-write failure and verify cleanup.

    Returns
    -------
    ReportPersistenceResult
        result="PASS"    on successful write.
        result="BLOCKED" on any validation refusal (no files written).
        result="ERROR"   never returned; unexpected exceptions propagate
                         after temp-dir cleanup.
        All safety flags are always False — the persistence function
        itself never contacts brokers, credentials, network, or orders.

    Notes
    -----
    No broker/API/credential/env/network/order access.
    No live/paper trading approval. No parameter optimisation.
    Persisting a report does not approve or trigger any trade.
    """
    # 1. Validate output_dir.
    if not output_dir:
        return _blocked("output_dir is required and must not be empty")

    output_dir_path = pathlib.Path(output_dir)

    path_blocker = _check_output_path(output_dir_path, allow_unsafe_path)
    if path_blocker is not None:
        return _blocked(path_blocker)

    # 2. Validate safety flags (fail-closed if any flag is True).
    for flag in _SAFETY_FLAGS:
        if getattr(pipeline_result, flag, False):
            return _blocked(
                f"refusing to write: safety flag {flag!r} is True on pipeline result"
            )

    # 3. Explicit redundant check for live_trading_allowed.
    if pipeline_result.live_trading_allowed:
        return _blocked(
            "refusing to write: live_trading_allowed is True on pipeline result"
        )

    # 4. Resolve generated_at_utc and compute run_id.
    ts = (
        generated_at_utc
        if generated_at_utc is not None
        else datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    run_id = _make_run_id(ts, pipeline_result.result)
    final_run_dir = output_dir_path / run_id
    tmp_run_dir = output_dir_path / f"._tmp_{run_id}"

    # 5. Check overwrite.
    if final_run_dir.exists() and not allow_overwrite:
        return _blocked(
            f"run directory already exists; use allow_overwrite=True to replace: "
            f"{final_run_dir}"
        )

    # 6. Atomic write: prepare in temp sibling, then rename.
    reports_dir = tmp_run_dir / "reports"
    try:
        output_dir_path.mkdir(parents=True, exist_ok=True)
        tmp_run_dir.mkdir(exist_ok=False)
        reports_dir.mkdir()

        # Collect report filenames and write individual reports.
        report_filenames: list[str] = []
        report_paths: list[str] = []
        for report in pipeline_result.snapshot.reports:
            filename = f"{report.candidate.candidate_id}.json"
            report_filenames.append(filename)
            report_path = reports_dir / filename
            _write_json(report_path, research_report_to_dict(report))
            report_paths.append(str(final_run_dir / "reports" / filename))

        # Write pipeline_summary.json.
        summary_data = _pipeline_summary_to_dict(pipeline_result, run_id, git_commit_sha)
        _write_json(tmp_run_dir / "pipeline_summary.json", summary_data)

        # Write manifest.json.
        manifest_data = _make_manifest_dict(
            pipeline_result, run_id, ts, git_commit_sha, report_filenames
        )
        _write_json(tmp_run_dir / "manifest.json", manifest_data)

        # Write optional summary.md.
        markdown_summary_path: str | None = None
        if include_markdown:
            md_content = _make_markdown_summary(
                pipeline_result, run_id, ts, git_commit_sha
            )
            (tmp_run_dir / "summary.md").write_text(md_content, encoding="utf-8")
            markdown_summary_path = str(final_run_dir / "summary.md")

        # Inject test hook before rename (raises to simulate failure).
        if _before_rename_hook is not None:
            _before_rename_hook()

        # Atomic rename: replace existing if allow_overwrite.
        if final_run_dir.exists():
            shutil.rmtree(final_run_dir)
        tmp_run_dir.rename(final_run_dir)

    except Exception:
        if tmp_run_dir.exists():
            shutil.rmtree(tmp_run_dir, ignore_errors=True)
        raise

    # 7. Collect all written file paths.
    files_written: list[str] = [
        str(final_run_dir / "manifest.json"),
        str(final_run_dir / "pipeline_summary.json"),
    ]
    files_written.extend(report_paths)
    if markdown_summary_path is not None:
        files_written.append(markdown_summary_path)

    manifest_path = str(final_run_dir / "manifest.json")
    pipeline_summary_path = str(final_run_dir / "pipeline_summary.json")

    logger.info(
        "report_persistence: wrote run %s to %s (%d file(s))",
        run_id,
        final_run_dir,
        len(files_written),
    )

    return ReportPersistenceResult(
        result="PASS",
        blocker=None,
        output_dir=str(output_dir_path),
        run_id=run_id,
        manifest_path=manifest_path,
        pipeline_summary_path=pipeline_summary_path,
        report_paths=tuple(report_paths),
        markdown_summary_path=markdown_summary_path,
        files_written=tuple(files_written),
        **_ALL_FALSE,
    )
