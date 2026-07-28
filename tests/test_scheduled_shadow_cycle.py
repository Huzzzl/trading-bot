"""Tests for src.tools.run_scheduled_shadow_cycle (S63).

All fixtures are synthetic — no network, no Alpaca, no broker
imports. Cache files are written as real CSVs on disk (the gate
reads the SPY 60m cache the same way S62/S56 do), and paper audit
files are written as real JSONL files (the gate reads them the same
way an operator's paper task output would look).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.tools import run_scheduled_shadow_cycle as rssc
from src.tools.run_shadow_strategy_cycle import ShadowError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CUTOFF = pd.Timestamp("2026-07-17 19:30", tz="UTC")  # S62 frozen cutoff


def _make_closes(pre: int = 800, post: int = 100) -> list[float]:
    total = pre + post
    closes: list[float] = []
    period = 60
    for i in range(total):
        phase = i % period
        if phase < period // 2:
            closes.append(float(1 + phase))
        else:
            closes.append(float(1 + (period - phase)))
    return closes


def _write_spy_cache(
    cache_dir: Path, *, pre: int = 800, post: int = 100,
) -> tuple[Path, pd.Timestamp]:
    """Write a SPY 60m cache CSV straddling the S62 cutoff. Returns
    ``(path, latest_bar_ts)`` — the last bar's UTC pandas Timestamp."""
    closes = _make_closes(pre, post)
    start = _CUTOFF + pd.Timedelta(hours=1) - pd.Timedelta(hours=pre)
    idx = pd.date_range(start=start, periods=len(closes), freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1_000.0] * len(closes),
    }, index=idx)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "SPY_2026-08-01_60m.csv"
    df.to_csv(path)
    return path, idx[-1]


def _make_paper_audit(
    *,
    now_utc: datetime,
    latest_bar_ts: datetime,
    fetch_result: str | None = "PASS",
    final_result: str | None = "PASS",
    exit_code: int | None = 0,
    blocker: str | None = None,
    symbol: str = "SPY",
    interval: str = "60m",
    mode: str = "DRY_RUN",
    fetch_status: str | None = "ok",
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "timestamp_utc": timestamp_utc or now_utc.astimezone(timezone.utc).isoformat(),
        "mode": mode,
        "symbol": symbol,
        "interval": interval,
        "fetch_result": fetch_result,
        "fetch_status": fetch_status,
        "network_calls_made": 1,
        "latest_bar_ts": (
            latest_bar_ts.astimezone(timezone.utc).isoformat()
            if latest_bar_ts is not None else None
        ),
        "clock_is_open": True,
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
        "blocker": blocker,
        "final_result": final_result,
        "exit_code": exit_code,
    }


def _write_paper_audit(
    paper_audit_dir: Path, filename: str, records: list[dict[str, Any]],
) -> Path:
    paper_audit_dir.mkdir(parents=True, exist_ok=True)
    path = paper_audit_dir / filename
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def _setup_happy_path(
    tmp_path: Path, *, pre: int = 800, post: int = 100,
) -> dict[str, Any]:
    paper_audit_dir = tmp_path / "paper_cycles"
    cache_dir = tmp_path / "cache"
    shadow_state_dir = tmp_path / "shadow_strategy"
    scheduler_audit_dir = tmp_path / "shadow_scheduler"

    cache_path, latest_ts = _write_spy_cache(cache_dir, pre=pre, post=post)
    now_utc = latest_ts.to_pydatetime() + timedelta(minutes=2)
    audit = _make_paper_audit(now_utc=now_utc, latest_bar_ts=latest_ts.to_pydatetime())
    _write_paper_audit(paper_audit_dir, "2026-08-01.jsonl", [audit])

    return {
        "paper_audit_dir": paper_audit_dir,
        "cache_dir": cache_dir,
        "shadow_state_dir": shadow_state_dir,
        "scheduler_audit_dir": scheduler_audit_dir,
        "now_utc": now_utc,
        "latest_bar_ts": latest_ts.to_pydatetime(),
        "cache_path": cache_path,
    }


def _gate(env: dict[str, Any], **overrides) -> dict[str, Any]:
    kwargs = dict(
        paper_audit_dir=env["paper_audit_dir"],
        cache_dir=env["cache_dir"],
        shadow_state_dir=env["shadow_state_dir"],
        scheduler_audit_dir=env["scheduler_audit_dir"],
        max_paper_audit_age_minutes=20,
        now_utc=env["now_utc"],
    )
    kwargs.update(overrides)
    return rssc.run_gate(**kwargs)


def _snapshot_dir(path: Path) -> dict[str, tuple[bytes, float]]:
    if not path.exists():
        return {}
    out: dict[str, tuple[bytes, float]] = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(path))] = (p.read_bytes(), p.stat().st_mtime_ns)
    return out


# ---------------------------------------------------------------------------
# 1-6: paper-audit selection correctness
# ---------------------------------------------------------------------------


def test_latest_audit_chosen_by_parsed_timestamp_not_physical_order(
    tmp_path: Path,
) -> None:
    paper_audit_dir = tmp_path / "paper_cycles"
    now = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
    old = _make_paper_audit(
        now_utc=now - timedelta(minutes=10), latest_bar_ts=now - timedelta(minutes=10),
        timestamp_utc=(now - timedelta(minutes=10)).isoformat(),
    )
    newest = _make_paper_audit(
        now_utc=now - timedelta(minutes=2), latest_bar_ts=now - timedelta(minutes=2),
        timestamp_utc=(now - timedelta(minutes=2)).isoformat(),
    )
    middle = _make_paper_audit(
        now_utc=now - timedelta(minutes=5), latest_bar_ts=now - timedelta(minutes=5),
        timestamp_utc=(now - timedelta(minutes=5)).isoformat(),
    )
    # Physical order: newest FIRST, then old, then middle — selection
    # must still find `newest` by parsed timestamp.
    _write_paper_audit(paper_audit_dir, "2026-08-01.jsonl", [newest, old, middle])
    selected = rssc.select_latest_paper_audit(paper_audit_dir, now)
    assert selected is not None
    assert selected["record"]["timestamp_utc"] == newest["timestamp_utc"]


def test_malformed_audit_line_fails_closed(tmp_path: Path) -> None:
    paper_audit_dir = tmp_path / "paper_cycles"
    now = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
    good = _make_paper_audit(now_utc=now, latest_bar_ts=now)
    path = _write_paper_audit(paper_audit_dir, "2026-08-01.jsonl", [good])
    with path.open("a", encoding="utf-8") as f:
        f.write("not-a-json-line\n")
    with pytest.raises(rssc.GateError) as exc_info:
        rssc.select_latest_paper_audit(paper_audit_dir, now)
    assert exc_info.value.reason_code == rssc.REASON_PAPER_AUDIT_CORRUPT


def test_invalid_utf8_fails_closed(tmp_path: Path) -> None:
    paper_audit_dir = tmp_path / "paper_cycles"
    paper_audit_dir.mkdir(parents=True)
    path = paper_audit_dir / "2026-08-01.jsonl"
    path.write_bytes(b'{"symbol": "SPY"}\n\xff\xfe not utf-8\n')
    now = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(rssc.GateError) as exc_info:
        rssc.select_latest_paper_audit(paper_audit_dir, now)
    assert exc_info.value.reason_code == rssc.REASON_PAPER_AUDIT_CORRUPT


def test_future_audit_timestamp_fails_closed(tmp_path: Path) -> None:
    paper_audit_dir = tmp_path / "paper_cycles"
    now = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
    future = _make_paper_audit(
        now_utc=now, latest_bar_ts=now,
        timestamp_utc=(now + timedelta(hours=5)).isoformat(),
    )
    _write_paper_audit(paper_audit_dir, "2026-08-01.jsonl", [future])
    with pytest.raises(rssc.GateError) as exc_info:
        rssc.select_latest_paper_audit(paper_audit_dir, now)
    assert exc_info.value.reason_code == rssc.REASON_PAPER_AUDIT_TIMESTAMP_INVALID


def test_wrong_symbol_ignored(tmp_path: Path) -> None:
    paper_audit_dir = tmp_path / "paper_cycles"
    now = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
    wrong_symbol = _make_paper_audit(
        now_utc=now, latest_bar_ts=now, symbol="AAPL",
        timestamp_utc=(now - timedelta(minutes=1)).isoformat(),
    )
    right = _make_paper_audit(
        now_utc=now, latest_bar_ts=now,
        timestamp_utc=(now - timedelta(minutes=2)).isoformat(),
    )
    _write_paper_audit(paper_audit_dir, "2026-08-01.jsonl", [wrong_symbol, right])
    selected = rssc.select_latest_paper_audit(paper_audit_dir, now)
    assert selected is not None
    assert selected["record"]["symbol"] == "SPY"


def test_wrong_interval_ignored(tmp_path: Path) -> None:
    paper_audit_dir = tmp_path / "paper_cycles"
    now = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
    wrong_interval = _make_paper_audit(
        now_utc=now, latest_bar_ts=now, interval="1d",
        timestamp_utc=(now - timedelta(minutes=1)).isoformat(),
    )
    right = _make_paper_audit(
        now_utc=now, latest_bar_ts=now,
        timestamp_utc=(now - timedelta(minutes=2)).isoformat(),
    )
    _write_paper_audit(paper_audit_dir, "2026-08-01.jsonl", [wrong_interval, right])
    selected = rssc.select_latest_paper_audit(paper_audit_dir, now)
    assert selected is not None
    assert selected["record"]["interval"] == "60m"


# ---------------------------------------------------------------------------
# 7-9: SKIPPED outcomes (exit 0)
# ---------------------------------------------------------------------------


def test_missing_audit_produces_skipped_exit_0(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    # Point at a paper_audit_dir that has no files.
    empty_dir = tmp_path / "no_such_audit_dir"
    result = _gate(env, paper_audit_dir=empty_dir)
    assert result["result"] == "SKIPPED"
    assert result["exit_code"] == 0
    assert result["reason_codes"] == [rssc.REASON_NO_PAPER_AUDIT]
    assert result["shadow_invoked"] is False


def test_stale_audit_produces_skipped_exit_0(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    stale_now = env["now_utc"] + timedelta(minutes=60)
    result = _gate(env, now_utc=stale_now, max_paper_audit_age_minutes=20)
    assert result["result"] == "SKIPPED"
    assert result["exit_code"] == 0
    assert result["reason_codes"] == [rssc.REASON_PAPER_AUDIT_STALE]
    assert result["shadow_invoked"] is False


def test_failed_cache_refresh_produces_skipped_exit_0(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    audit = _make_paper_audit(
        now_utc=env["now_utc"], latest_bar_ts=env["latest_bar_ts"],
        fetch_result="FAIL",
    )
    _write_paper_audit(env["paper_audit_dir"], "2026-08-01.jsonl", [audit])
    result = _gate(env)
    assert result["result"] == "SKIPPED"
    assert result["exit_code"] == 0
    assert result["reason_codes"] == [rssc.REASON_CACHE_REFRESH_NOT_PASS]
    assert result["shadow_invoked"] is False


# ---------------------------------------------------------------------------
# 10-12: RUN outcomes
# ---------------------------------------------------------------------------


def test_matching_cache_timestamp_passes(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    result = _gate(env)
    assert result["result"] == "RUN"
    assert result["exit_code"] == 0
    assert result["shadow_invoked"] is True
    assert result["cache_matches_paper_audit"] is True


def test_audit_cache_timestamp_mismatch_produces_error_exit_2(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    wrong_ts = env["latest_bar_ts"] - timedelta(hours=1)
    audit = _make_paper_audit(now_utc=env["now_utc"], latest_bar_ts=wrong_ts)
    _write_paper_audit(env["paper_audit_dir"], "2026-08-01.jsonl", [audit])
    result = _gate(env)
    assert result["result"] == "ERROR"
    assert result["exit_code"] == 2
    assert result["reason_codes"] == [rssc.REASON_AUDIT_CACHE_TIMESTAMP_MISMATCH]
    assert result["shadow_invoked"] is False


def test_paper_final_result_error_with_fetch_pass_may_still_invoke_shadow(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    audit = _make_paper_audit(
        now_utc=env["now_utc"], latest_bar_ts=env["latest_bar_ts"],
        fetch_result="PASS", final_result="ERROR", exit_code=2,
        blocker="broker clock read failed",
    )
    _write_paper_audit(env["paper_audit_dir"], "2026-08-01.jsonl", [audit])
    result = _gate(env)
    assert result["result"] == "RUN"
    assert result["exit_code"] == 0
    assert result["shadow_invoked"] is True
    # Diagnostics recorded but did not block the gate.
    assert result["paper_final_result"] == "ERROR"
    assert result["paper_blocker"] == "broker clock read failed"


# ---------------------------------------------------------------------------
# 13-15: shadow invocation semantics
# ---------------------------------------------------------------------------


def test_gate_invokes_shadow_runner_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _setup_happy_path(tmp_path)
    calls: list[dict[str, Any]] = []
    real_run_cycle = rssc._shadow_run_cycle

    def _spy(bars, **kwargs):
        calls.append(kwargs)
        return real_run_cycle(bars, **kwargs)

    monkeypatch.setattr(rssc, "_shadow_run_cycle", _spy)
    result = _gate(env)
    assert result["result"] == "RUN"
    assert len(calls) == 1
    assert calls[0]["state_dir"] == env["shadow_state_dir"]
    assert calls[0]["now_utc"] == env["now_utc"]
    assert calls[0]["dry_run"] is False


def test_shadow_exception_produces_error_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _setup_happy_path(tmp_path)

    def _boom(bars, **kwargs):
        raise ShadowError("simulated shadow failure")

    monkeypatch.setattr(rssc, "_shadow_run_cycle", _boom)
    result = _gate(env)
    assert result["result"] == "ERROR"
    assert result["exit_code"] == 2
    assert result["reason_codes"] == [rssc.REASON_SHADOW_RUN_FAILED]
    assert "simulated shadow failure" in result["shadow_error"]


def test_shadow_failure_does_not_change_paper_audit_or_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _setup_happy_path(tmp_path)
    before_audit = _snapshot_dir(env["paper_audit_dir"])
    before_cache = _snapshot_dir(env["cache_dir"])

    def _boom(bars, **kwargs):
        raise ShadowError("simulated shadow failure")

    monkeypatch.setattr(rssc, "_shadow_run_cycle", _boom)
    _gate(env)

    assert _snapshot_dir(env["paper_audit_dir"]) == before_audit
    assert _snapshot_dir(env["cache_dir"]) == before_cache


# ---------------------------------------------------------------------------
# 16: dry-run leaves everything unchanged
# ---------------------------------------------------------------------------


def test_dry_run_leaves_all_filesystem_bytes_and_mtimes_unchanged(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    # Bootstrap S62 for real first so dry-run has something to
    # meaningfully validate/replay.
    bootstrap = _gate(env)
    assert bootstrap["result"] == "RUN"

    before = _snapshot_dir(tmp_path)
    result = _gate(env, dry_run=True)
    assert result["result"] == "RUN"
    assert result["dry_run"] is True
    after = _snapshot_dir(tmp_path)
    assert before == after


def test_dry_run_with_write_dry_run_audit_persists_scheduler_record(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    _gate(env)  # bootstrap
    result = _gate(env, dry_run=True, write_dry_run_audit=True)
    assert result["dry_run"] is True
    scheduler_files = list(env["scheduler_audit_dir"].glob("*.jsonl"))
    assert scheduler_files


# ---------------------------------------------------------------------------
# 17: idempotency
# ---------------------------------------------------------------------------


def test_repeated_normal_invocation_creates_no_duplicate_events(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    first = _gate(env)
    assert first["result"] == "RUN"
    assert first["shadow_events_appended"] > 0

    second = _gate(env)
    assert second["result"] == "RUN"
    assert second["shadow_candidate_events_appended"] == 0
    assert second["shadow_experiment_events_appended"] == 0
    assert second["shadow_events_appended"] == 0


# ---------------------------------------------------------------------------
# 18-20: scheduler audit contract
# ---------------------------------------------------------------------------


def test_scheduler_audit_fields_are_complete(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    _gate(env)
    files = list(env["scheduler_audit_dir"].glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[0]
    record = json.loads(line)
    for field in rssc._SCHEDULER_RECORD_FIELDS:
        assert field in record, f"missing field {field!r}"
    assert record["tool"] == "run_scheduled_shadow_cycle"
    assert record["research_only"] is True
    assert record["automatic_promotion_allowed"] is False


def test_scheduler_audit_jsonl_records_are_independently_parseable(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    _gate(env)
    _gate(env, now_utc=env["now_utc"] + timedelta(seconds=1))
    files = list(env["scheduler_audit_dir"].glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert obj["tool"] == "run_scheduled_shadow_cycle"


def test_scheduler_audit_contains_no_credentials(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    _gate(env)
    files = list(env["scheduler_audit_dir"].glob("*.jsonl"))
    text = files[0].read_text(encoding="utf-8").lower()
    for banned in ("api_key", "secret", "password", "token", "credential"):
        assert banned not in text, f"scheduler audit contains {banned!r}"


# ---------------------------------------------------------------------------
# 21: scheduler-audit-write failure isolation
# ---------------------------------------------------------------------------


def test_scheduler_audit_write_failure_does_not_corrupt_shadow_state(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    # Block directory creation: put a plain FILE where the scheduler
    # audit directory should be.
    env["scheduler_audit_dir"].parent.mkdir(parents=True, exist_ok=True)
    env["scheduler_audit_dir"].write_text("not a directory", encoding="utf-8")

    result = _gate(env)
    assert result["result"] == "ERROR"
    assert result["exit_code"] == 2
    assert result["reason_codes"] == [rssc.REASON_SCHEDULER_AUDIT_WRITE_FAILED]

    # But the shadow run itself (which happens BEFORE the audit
    # write) completed successfully and left valid, loadable state.
    from src.tools.run_shadow_strategy_cycle import (
        load_manifest_readonly, load_state_readonly,
    )
    manifest = load_manifest_readonly(env["shadow_state_dir"])
    state = load_state_readonly(env["shadow_state_dir"], manifest)
    assert state["experiment_id"] == manifest["experiment_id"]


# ---------------------------------------------------------------------------
# 22, 25: safety scans
# ---------------------------------------------------------------------------


def test_no_broker_network_or_paper_runner_imports() -> None:
    source = Path("src/tools/run_scheduled_shadow_cycle.py").read_text(
        encoding="utf-8",
    )
    banned = (
        "alpaca", "requests", "httpx", "urllib.request", "socket",
        "submit_order", "cancel_order", "TradingClient",
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "run_automated_paper_cycle", "run_paper_trading_cycle",
    )
    for tok in banned:
        assert tok not in source, (
            f"run_scheduled_shadow_cycle.py must not reference {tok!r}"
        )


def test_paper_runner_source_unmodified_markers_present() -> None:
    """Best-effort guard: this PR must not have touched the paper
    strategy's known invariants."""
    source = Path("src/tools/run_automated_paper_cycle.py").read_text(
        encoding="utf-8",
    )
    assert '_SYMBOL = "SPY"' in source
    assert '_INTERVAL = "60m"' in source


# ---------------------------------------------------------------------------
# 23-24: PowerShell launcher
# ---------------------------------------------------------------------------


def test_powershell_launcher_has_no_submit_paper() -> None:
    text = Path("scripts/run_s62_shadow_task.ps1").read_text(encoding="utf-8")
    assert "--submit-paper" not in text


def test_powershell_launcher_propagates_exit_code() -> None:
    text = Path("scripts/run_s62_shadow_task.ps1").read_text(encoding="utf-8")
    assert "$LASTEXITCODE" in text
    assert "exit $exitCode" in text


def test_powershell_launcher_requires_repo_root_and_python_exe() -> None:
    text = Path("scripts/run_s62_shadow_task.ps1").read_text(encoding="utf-8")
    assert "$RepoRoot" in text
    assert "$PythonExe" in text
    assert "Mandatory = $true" in text


def test_powershell_launcher_has_no_network_or_loop_constructs() -> None:
    text = Path("scripts/run_s62_shadow_task.ps1").read_text(encoding="utf-8")
    for tok in ("Invoke-WebRequest", "Invoke-RestMethod", "while (", "for (", "do {"):
        assert tok not in text


# ---------------------------------------------------------------------------
# 26-27: S62 invariants preserved
# ---------------------------------------------------------------------------


def test_shadow_manifest_byte_for_byte_unchanged_across_gate_invocations(
    tmp_path: Path,
) -> None:
    env = _setup_happy_path(tmp_path)
    _gate(env)
    manifest_path = env["shadow_state_dir"] / "manifest.json"
    before = manifest_path.read_bytes()
    _gate(env)  # second invocation
    after = manifest_path.read_bytes()
    assert before == after


def test_validation_status_remains_promotion_ineligible(tmp_path: Path) -> None:
    env = _setup_happy_path(tmp_path)
    result = _gate(env)
    assert result["research_only"] is True
    assert result["automatic_promotion_allowed"] is False

    from src.tools.run_shadow_strategy_cycle import (
        load_manifest_readonly, load_state_readonly,
    )
    from src.tools.shadow_strategy_report import build_report

    manifest = load_manifest_readonly(env["shadow_state_dir"])
    state = load_state_readonly(env["shadow_state_dir"], manifest)
    report = build_report(manifest, state)
    assert report["validation_status"]["promotion_eligible"] is False
    assert report["automatic_strategy_promotion_allowed"] is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_run_returns_exit_0_and_prints_json(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    env = _setup_happy_path(tmp_path)
    rc = rssc.main([
        "--paper-audit-dir", str(env["paper_audit_dir"]),
        "--cache-dir", str(env["cache_dir"]),
        "--shadow-state-dir", str(env["shadow_state_dir"]),
        "--scheduler-audit-dir", str(env["scheduler_audit_dir"]),
        "--now-utc", env["now_utc"].astimezone(timezone.utc).isoformat(),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "RUN"


def test_cli_skipped_returns_exit_0(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    empty_dir = tmp_path / "no_audit"
    cache_dir = tmp_path / "cache"
    shadow_state_dir = tmp_path / "shadow_strategy"
    scheduler_audit_dir = tmp_path / "shadow_scheduler"
    rc = rssc.main([
        "--paper-audit-dir", str(empty_dir),
        "--cache-dir", str(cache_dir),
        "--shadow-state-dir", str(shadow_state_dir),
        "--scheduler-audit-dir", str(scheduler_audit_dir),
        "--now-utc", "2026-08-01T15:00:00+00:00",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "SKIPPED"
    assert payload["reason_codes"] == ["NO_PAPER_AUDIT"]


def test_cli_error_returns_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    env = _setup_happy_path(tmp_path)
    wrong_ts = env["latest_bar_ts"] - timedelta(hours=1)
    audit = _make_paper_audit(now_utc=env["now_utc"], latest_bar_ts=wrong_ts)
    _write_paper_audit(env["paper_audit_dir"], "2026-08-01.jsonl", [audit])
    rc = rssc.main([
        "--paper-audit-dir", str(env["paper_audit_dir"]),
        "--cache-dir", str(env["cache_dir"]),
        "--shadow-state-dir", str(env["shadow_state_dir"]),
        "--scheduler-audit-dir", str(env["scheduler_audit_dir"]),
        "--now-utc", env["now_utc"].astimezone(timezone.utc).isoformat(),
    ])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "ERROR"
    assert payload["reason_codes"] == ["AUDIT_CACHE_TIMESTAMP_MISMATCH"]
