"""Tests for src.tools.recover_shadow_strategy_state.

All fixtures are synthetic — no network, no broker imports. The SPY
60m cache is written as a real CSV on disk (the same way S62/S56 read
it), and the "existing" state directory is built with the real
run_shadow_strategy_cycle.run_cycle() so the tool can be exercised
against a realistic manifest/state/event-log layout.

This repository is operated on Windows PowerShell — the tool must
emit PowerShell (Copy-Item/Move-Item) operator guidance, never Unix
commands, default its rebuild work directory beside the state
directory (not under the OS temp dir), and fail closed rather than
recommend a cross-volume "atomic" move.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.tools import recover_shadow_strategy_state as rsc
from src.tools import run_shadow_strategy_cycle as ssc

_CUTOFF = pd.Timestamp("2026-07-17 19:30", tz="UTC")
_NOW = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)


def _make_closes(pre: int = 400, post: int = 100) -> list[float]:
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


def _write_spy_cache(cache_dir: Path, *, pre: int = 400, post: int = 100) -> Path:
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
    return path


def _snapshot_dir(path: Path) -> dict[str, tuple[bytes, float]]:
    if not path.exists():
        return {}
    out: dict[str, tuple[bytes, float]] = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(path))] = (p.read_bytes(), p.stat().st_mtime_ns)
    return out


def _load_bars_from_cache(cache_dir: Path):
    from src.tools.backtest_strategy_eval import load_cached_bars
    return load_cached_bars(cache_dir, "SPY", "60m")


def _build_existing_state(tmp_path: Path) -> tuple[Path, Path, list]:
    state_dir = tmp_path / "shadow_strategy"
    cache_dir = tmp_path / "cache"
    _write_spy_cache(cache_dir)
    bars = _load_bars_from_cache(cache_dir)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    return state_dir, cache_dir, bars


# ---------------------------------------------------------------------------
# Core recovery behavior
# ---------------------------------------------------------------------------


def test_recover_succeeds_against_healthy_existing_state(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    before = _snapshot_dir(state_dir)

    result = rsc.recover(
        state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
        now_utc=_NOW,
    )

    assert result["existing_state_dir_modified"] is False
    assert result["first_replay_events_appended"] > 0
    checks = result["checks"]
    assert checks["experiment_id"] == "S62_SPY_60M_FORWARD"
    assert checks["candidate_count"] == 5
    assert checks["research_only"] is True
    assert checks["automatic_strategy_promotion_allowed"] is False
    assert checks["promotion_eligible"] is False
    assert checks["event_log_line_count"] == checks["unique_event_id_count"]
    assert checks["manifest_bytes_identical_to_existing"] is True

    # The manifest hash must match the existing directory's manifest.
    existing_manifest = json.loads((state_dir / ssc._MANIFEST_FILENAME).read_text())
    assert checks["manifest_hash"] == existing_manifest["candidate_manifest_sha256"]

    # The existing directory was never touched.
    assert _snapshot_dir(state_dir) == before

    # The rebuilt work directory is independently valid.
    rebuilt_manifest = ssc.load_manifest_readonly(work_dir)
    rebuilt_state = ssc.load_state_readonly(work_dir, rebuilt_manifest)
    assert rebuilt_state["experiment_id"] == "S62_SPY_60M_FORWARD"

    assert str(state_dir) in result["operator_commands"]
    assert str(work_dir) in result["operator_commands"]


def test_recover_never_reads_or_mutates_broken_state_json(tmp_path: Path) -> None:
    """The tool only reads the existing manifest — never the existing
    state.json or events.jsonl — so a corrupted state.json (the exact
    failure mode this tool exists to recover from) must not prevent
    recovery, and must be left byte-for-byte untouched."""
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"

    # Corrupt state.json so it would fail _validate_state (mirrors the
    # reported production-shadow bug: an internally inconsistent
    # counter relationship).
    state_path = state_dir / ssc._STATE_FILENAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    cid = "research_10_20_none"
    state["candidates"][cid]["bullish_crossover_count"] = 999
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    # Confirm this really is broken, exactly like the reported bug.
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError):
        ssc.load_state_readonly(state_dir, manifest)

    before_state_bytes = state_path.read_bytes()
    before_state_mtime = state_path.stat().st_mtime_ns

    result = rsc.recover(
        state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
        now_utc=_NOW,
    )
    assert result["existing_state_dir_modified"] is False
    assert result["checks"]["experiment_id"] == "S62_SPY_60M_FORWARD"

    assert state_path.read_bytes() == before_state_bytes
    assert state_path.stat().st_mtime_ns == before_state_mtime


def test_recover_fails_closed_on_missing_existing_manifest(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow_strategy"  # never initialized
    cache_dir = tmp_path / "cache"
    work_dir = tmp_path / "work"
    _write_spy_cache(cache_dir)

    with pytest.raises(rsc.RecoveryError):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )
    # Never created the "existing" directory.
    assert not state_dir.exists()


def test_cli_json_output_and_no_mutation(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    before = _snapshot_dir(state_dir)

    rc = rsc.main([
        "--state-dir", str(state_dir),
        "--cache-dir", str(cache_dir),
        "--work-dir", str(work_dir),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "OK"
    assert payload["existing_state_dir_modified"] is False
    assert "operator_commands" in payload

    assert _snapshot_dir(state_dir) == before


def test_cli_error_exit_code_on_missing_state_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    _write_spy_cache(cache_dir)
    rc = rsc.main([
        "--state-dir", str(tmp_path / "no_such_dir"),
        "--cache-dir", str(cache_dir),
        "--work-dir", str(tmp_path / "work"),
        "--json",
    ])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "ERROR"
    assert payload["existing_state_dir_modified"] is False


def test_no_broker_network_or_paper_runner_imports() -> None:
    source = Path("src/tools/recover_shadow_strategy_state.py").read_text(
        encoding="utf-8",
    )
    banned = (
        "alpaca", "requests", "httpx", "urllib.request", "socket",
        "submit_order", "cancel_order", "TradingClient",
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "run_automated_paper_cycle", "run_paper_trading_cycle",
        "ScheduledTask", "Register-ScheduledTask",
    )
    for tok in banned:
        assert tok not in source, (
            f"recover_shadow_strategy_state.py must not reference {tok!r}"
        )


# ---------------------------------------------------------------------------
# Windows / PowerShell operator guidance
# ---------------------------------------------------------------------------


def test_operator_commands_use_powershell_syntax(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    result = rsc.recover(
        state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
        now_utc=_NOW,
    )
    commands = result["operator_commands"]
    assert "Copy-Item" in commands
    assert "-LiteralPath" in commands
    assert "-Recurse" in commands
    assert "Move-Item" in commands


def test_operator_commands_never_emit_unix_only_commands(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    result = rsc.recover(
        state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
        now_utc=_NOW,
    )
    commands = result["operator_commands"]
    for banned in ("cp -a", "\nmv ", "rm -rf", "sudo "):
        assert banned not in commands, f"unix-only command {banned!r} emitted"


def test_default_work_dir_is_created_beside_state_dir(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)

    result = rsc.recover(state_dir=state_dir, cache_dir=cache_dir, now_utc=_NOW)

    work_dir = Path(result["work_dir"])
    assert work_dir.parent == state_dir.parent
    assert work_dir != state_dir


def test_default_work_dir_helper_is_under_state_dir_parent(tmp_path: Path) -> None:
    state_dir = tmp_path / "nested" / "shadow_strategy"
    state_dir.parent.mkdir(parents=True)
    work_dir = rsc._default_work_dir(state_dir)
    assert work_dir.parent == state_dir.parent
    assert work_dir.exists()
    assert not any(work_dir.iterdir())


# ---------------------------------------------------------------------------
# Fail-closed replacement-safety checks
# ---------------------------------------------------------------------------


def test_cross_volume_work_dir_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the rebuild itself validates cleanly, a work_dir on a
    different filesystem/volume must fail closed rather than be
    recommended as an atomic same-volume move."""
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"

    monkeypatch.setattr(rsc, "_same_filesystem", lambda a, b: False)

    with pytest.raises(rsc.RecoveryError, match="filesystem|volume"):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


def test_work_dir_inside_state_dir_fails_closed(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = state_dir / "nested_work"

    with pytest.raises(rsc.RecoveryError, match="inside"):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


def test_state_dir_inside_work_dir_fails_closed(tmp_path: Path) -> None:
    """A work_dir that is an ANCESTOR of state_dir is unsafe, but any
    such ancestor is necessarily non-empty (it contains state_dir),
    so the full recover() pipeline already fails closed earlier, at
    the empty-work-dir precondition. Exercise the dedicated
    replacement-safety check directly to prove this specific
    direction is independently caught by name, not merely as a side
    effect of the emptiness check."""
    state_dir, _cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = state_dir.parent

    with pytest.raises(rsc.RecoveryError, match="inside"):
        rsc._verify_replacement_is_safe(state_dir, work_dir)


def test_state_dir_inside_work_dir_also_fails_closed_end_to_end(
    tmp_path: Path,
) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = state_dir.parent  # non-empty ancestor of state_dir

    with pytest.raises(rsc.RecoveryError):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


def test_work_dir_same_as_state_dir_fails_closed(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)

    with pytest.raises(rsc.RecoveryError):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=state_dir,
            now_utc=_NOW,
        )


def test_non_empty_work_dir_with_arbitrary_file_fails(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    (work_dir / "leftover.txt").write_text("not empty", encoding="utf-8")

    with pytest.raises(rsc.RecoveryError, match="not empty"):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


def test_non_empty_work_dir_with_stale_lock_file_fails(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    (work_dir / ssc._LOCK_FILENAME).write_text("", encoding="utf-8")

    with pytest.raises(rsc.RecoveryError, match="not empty"):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


def test_recover_rejects_non_empty_work_dir(tmp_path: Path) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"

    # Pre-populate the work dir with a leftover manifest.
    work_dir.mkdir(parents=True)
    (work_dir / ssc._MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    with pytest.raises(rsc.RecoveryError):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


# ---------------------------------------------------------------------------
# Manifest provenance preservation
# ---------------------------------------------------------------------------


def test_rebuilt_manifest_bytes_and_created_at_utc_are_preserved(
    tmp_path: Path,
) -> None:
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"

    existing_bytes = (state_dir / ssc._MANIFEST_FILENAME).read_bytes()
    existing_manifest = json.loads(existing_bytes.decode("utf-8"))

    result = rsc.recover(
        state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
        now_utc=_NOW,
    )

    rebuilt_bytes = (work_dir / ssc._MANIFEST_FILENAME).read_bytes()
    assert rebuilt_bytes == existing_bytes
    assert result["checks"]["manifest_bytes_identical_to_existing"] is True
    assert (
        result["checks"]["manifest_created_at_utc"]
        == existing_manifest["created_at_utc"]
    )

    rebuilt_manifest = json.loads(rebuilt_bytes.decode("utf-8"))
    assert rebuilt_manifest["created_at_utc"] == existing_manifest["created_at_utc"]


def test_manifest_provenance_survives_a_later_now_utc(tmp_path: Path) -> None:
    """The rebuild's now_utc is deliberately much later than the
    existing manifest's created_at_utc — the manifest must still be
    copied verbatim, not regenerated with the later timestamp."""
    state_dir, cache_dir, _bars = _build_existing_state(tmp_path)
    work_dir = tmp_path / "work"
    much_later = datetime(2030, 1, 1, tzinfo=timezone.utc)

    existing_manifest = json.loads(
        (state_dir / ssc._MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert existing_manifest["created_at_utc"] != much_later.isoformat()

    rsc.recover(
        state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
        now_utc=much_later,
    )

    rebuilt_manifest = json.loads(
        (work_dir / ssc._MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert rebuilt_manifest["created_at_utc"] == existing_manifest["created_at_utc"]
