"""Tests for src.tools.recover_shadow_strategy_state.

All fixtures are synthetic — no network, no broker imports. The SPY
60m cache is written as a real CSV on disk (the same way S62/S56 read
it), and the "existing" state directory is built with the real
run_shadow_strategy_cycle.run_cycle() so the tool can be exercised
against a realistic manifest/state/event-log layout.
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


# ---------------------------------------------------------------------------


def test_recover_succeeds_against_healthy_existing_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow_strategy"
    cache_dir = tmp_path / "cache"
    work_dir = tmp_path / "work"
    _write_spy_cache(cache_dir)
    bars = _load_bars_from_cache(cache_dir)

    # Build a normal, valid "existing" state directory the same way
    # production does.
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
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

    # The manifest hash must match the existing directory's manifest.
    existing_manifest = json.loads((state_dir / ssc._MANIFEST_FILENAME).read_text())
    assert checks["manifest_hash"] == existing_manifest["candidate_manifest_sha256"]

    # The existing directory was never touched.
    assert _snapshot_dir(state_dir) == before

    # The rebuilt work directory is independently valid.
    rebuilt_manifest = ssc.load_manifest_readonly(work_dir)
    rebuilt_state = ssc.load_state_readonly(work_dir, rebuilt_manifest)
    assert rebuilt_state["experiment_id"] == "S62_SPY_60M_FORWARD"

    # Operator commands are printed guidance only — never executed.
    assert "cp -a" in result["operator_commands"]
    assert "mv " in result["operator_commands"]
    assert str(state_dir) in result["operator_commands"]
    assert str(work_dir) in result["operator_commands"]


def test_recover_never_reads_or_mutates_broken_state_json(tmp_path: Path) -> None:
    """The tool only reads the existing manifest — never the existing
    state.json or events.jsonl — so a corrupted state.json (the exact
    failure mode this tool exists to recover from) must not prevent
    recovery, and must be left byte-for-byte untouched."""
    state_dir = tmp_path / "shadow_strategy"
    cache_dir = tmp_path / "cache"
    work_dir = tmp_path / "work"
    _write_spy_cache(cache_dir)
    bars = _load_bars_from_cache(cache_dir)

    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)

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


def test_recover_rejects_non_empty_work_dir(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow_strategy"
    cache_dir = tmp_path / "cache"
    work_dir = tmp_path / "work"
    _write_spy_cache(cache_dir)
    bars = _load_bars_from_cache(cache_dir)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)

    # Pre-populate the work dir with a leftover manifest.
    work_dir.mkdir(parents=True)
    (work_dir / ssc._MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    with pytest.raises(rsc.RecoveryError):
        rsc.recover(
            state_dir=state_dir, cache_dir=cache_dir, work_dir=work_dir,
            now_utc=_NOW,
        )


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
    state_dir = tmp_path / "shadow_strategy"
    cache_dir = tmp_path / "cache"
    work_dir = tmp_path / "work"
    _write_spy_cache(cache_dir)
    bars = _load_bars_from_cache(cache_dir)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
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
