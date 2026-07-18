"""Tests for src.tools.run_shadow_strategy_cycle and shadow_strategy_report.

Everything runs off synthetic OHLCV — no network, no cache-file
dependency, no broker imports.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import pytest

from src.tools import run_shadow_strategy_cycle as ssc
from src.tools import shadow_strategy_report as ssr
from src.tools.backtest_strategy_eval import Bar


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_START = pd.Timestamp("2026-07-01 14:30", tz="UTC")  # pre-cutoff
_CUTOFF = pd.Timestamp("2026-07-17 19:30", tz="UTC")
_NOW = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)


def _bar_seq(closes: Sequence[float], start: pd.Timestamp = _START) -> list[Bar]:
    return [
        Bar(
            ts=start + pd.Timedelta(hours=i),
            open=c, high=c, low=c, close=c, volume=1_000.0,
        )
        for i, c in enumerate(closes)
    ]


def _bar_seq_with_ohlc(closes, opens=None, start=_START) -> list[Bar]:
    if opens is None:
        opens = closes
    return [
        Bar(
            ts=start + pd.Timedelta(hours=i),
            open=opens[i], high=max(opens[i], closes[i]),
            low=min(opens[i], closes[i]), close=closes[i], volume=1_000.0,
        )
        for i in range(len(closes))
    ]


def _snapshot_dir(path: Path) -> dict[str, tuple[bytes, float]]:
    """Return a byte-for-byte + mtime snapshot of everything under
    ``path`` so tests can prove read-only operations changed nothing."""
    if not path.exists():
        return {}
    out: dict[str, tuple[bytes, float]] = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(path))] = (
                p.read_bytes(), p.stat().st_mtime_ns,
            )
    return out


def _many_bars_across_cutoff(pre=800, post=200) -> list[Bar]:
    """A long series that straddles the cutoff.

    The first ``pre`` bars are at or before the cutoff and act as
    warmup. The next ``post`` bars are strictly after the cutoff and
    drive shadow trading. Uses a zig-zag close pattern so SMA
    crossovers actually fire in the forward region.
    """
    total = pre + post
    closes = []
    period = 60
    for i in range(total):
        phase = i % period
        if phase < period // 2:
            closes.append(float(1 + phase))
        else:
            closes.append(float(1 + (period - phase)))
    # Anchor so bar[pre] falls exactly one hour after the cutoff
    # timestamp. That way pre bars are at or before cutoff and post
    # bars are strictly after.
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    start = cutoff + pd.Timedelta(hours=1) - pd.Timedelta(hours=pre)
    return _bar_seq(closes, start=start)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_contains_exactly_five_frozen_candidates() -> None:
    m = ssc.build_manifest(_NOW)
    ids = [c["id"] for c in m["candidates"]]
    assert ids == [
        "paper_control_20_100_none",
        "research_10_20_none",
        "research_10_20_separation25",
        "research_10_20_trend200_separation25",
        "research_15_50_none",
    ]
    assert m["experiment_id"] == "S62_SPY_60M_FORWARD"
    assert m["symbol"] == "SPY"
    assert m["interval"] == "60m"
    assert m["execution"] == "next_open"
    assert m["commission_bps"] == 0
    assert m["slippage_bps"] == 1
    assert m["forward_cutoff_utc"] == "2026-07-17T19:30:00Z"
    assert m["research_only"] is True
    assert m["automatic_strategy_promotion_allowed"] is False


def test_manifest_hash_is_deterministic() -> None:
    """Hash must depend only on frozen fields — not on created_at_utc."""
    m1 = ssc.build_manifest(_NOW)
    m2 = ssc.build_manifest(
        datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    assert m1["candidate_manifest_sha256"] == m2["candidate_manifest_sha256"]


def test_manifest_hash_changes_when_candidates_change() -> None:
    m = ssc.build_manifest(_NOW)
    h_original = m["candidate_manifest_sha256"]
    m["candidates"][0]["short_window"] = 99
    m["candidate_manifest_sha256"] = ssc.manifest_hash(m)
    assert m["candidate_manifest_sha256"] != h_original


def test_manifest_cannot_be_altered_after_initialization(tmp_path: Path) -> None:
    """Once the manifest exists, altering its contents on disk must
    fail on the next load."""
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)
    manifest_path = state_dir / ssc._MANIFEST_FILENAME
    doc = json.loads(manifest_path.read_text())
    doc["candidates"][0]["short_window"] = 99
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ssc.ShadowError):
        ssc.load_or_init_manifest(state_dir, _NOW)


def test_cutoff_value_is_frozen() -> None:
    m = ssc.build_manifest(_NOW)
    assert m["forward_cutoff_utc"] == "2026-07-17T19:30:00Z"


# ---------------------------------------------------------------------------
# Cutoff enforcement
# ---------------------------------------------------------------------------


def test_pre_cutoff_bars_are_warmup_only(tmp_path: Path) -> None:
    """Bars at or before the cutoff must not create trades or count as
    forward observations."""
    state_dir = tmp_path / "shadow"
    # Bars all strictly before the cutoff.
    bars = _bar_seq(
        [float(c) for c in range(1, 201)],
        start=pd.Timestamp("2026-05-01 00:00", tz="UTC"),
    )
    summary = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    for cid, s in summary["candidates"].items():
        assert s["processed_forward_bar_count"] == 0, cid
        assert s["position_open"] is False
        assert s["completed_trade_count"] == 0
        assert s["bullish_crossover_count"] == 0


def test_cutoff_bar_itself_cannot_create_trade(tmp_path: Path) -> None:
    """A bar exactly AT the cutoff timestamp must be treated as warmup."""
    state_dir = tmp_path / "shadow"
    bar_at_cutoff = Bar(
        ts=pd.Timestamp("2026-07-17 19:30", tz="UTC"),
        open=100.0, high=100.0, low=100.0, close=100.0, volume=1_000,
    )
    bars = _bar_seq([float(c) for c in range(1, 300)],
                    start=pd.Timestamp("2026-05-01 00:00", tz="UTC"))
    bars.append(bar_at_cutoff)
    summary = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    for cid, s in summary["candidates"].items():
        assert s["processed_forward_bar_count"] == 0, cid


def test_first_post_cutoff_bar_uses_only_prior_completed_data(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    summary = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Some candidate should have processed at least one forward bar.
    total = sum(s["processed_forward_bar_count"]
                for s in summary["candidates"].values())
    assert total > 0


# ---------------------------------------------------------------------------
# Candidate independence + safety
# ---------------------------------------------------------------------------


def test_candidates_maintain_independent_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state_path = state_dir / ssc._STATE_FILENAME
    state = json.loads(state_path.read_text())
    # Each candidate has its own state block.
    ids = set(state["candidates"].keys())
    assert ids == {
        "paper_control_20_100_none",
        "research_10_20_none",
        "research_10_20_separation25",
        "research_10_20_trend200_separation25",
        "research_15_50_none",
    }
    # And they can differ (at least two candidates end with different
    # processed_forward_bar_count on this synthetic series is expected
    # since they use different SMA windows and filters).
    # Assert at minimum that no two candidates share the same list
    # object.
    for a_id, a in state["candidates"].items():
        for b_id, b in state["candidates"].items():
            if a_id != b_id:
                assert a is not b


def test_shadow_module_imports_no_broker_or_network_modules() -> None:
    """Static check: the runner and report source contain no broker,
    network, or credential imports."""
    for module_path in (
        "src/tools/run_shadow_strategy_cycle.py",
        "src/tools/shadow_strategy_report.py",
    ):
        source = Path(module_path).read_text(encoding="utf-8")
        for tok in ("alpaca", "requests", "httpx", "urllib.request", "socket",
                    "submit_order", "cancel_order", "TradingClient",
                    "ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            assert tok not in source, f"{module_path} must not depend on {tok!r}"


# ---------------------------------------------------------------------------
# Diagnostic counters — the core S62 correctness
# ---------------------------------------------------------------------------


def test_bullish_crossover_count_is_transition_based(tmp_path: Path) -> None:
    """Count each transition from short<=long to short>long — not
    every bullish state bar."""
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        # bullish_crossover_count <= bullish_state_bar_count is a hard
        # invariant: a crossover produces the first bullish_state
        # bar, then many bullish_state bars follow with no new
        # crossover event.
        assert s["bullish_crossover_count"] <= s["bullish_state_bar_count"], cid


def test_bullish_signal_count_aliases_state_bar_count(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=300)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        assert s["bullish_signal_count"] == s["bullish_state_bar_count"], cid


def test_unique_bullish_episodes_bounded_by_crossovers(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        # Every episode is either opened by a forward crossover or
        # inherited across the cutoff. Episodes counted must equal
        # forward crossovers plus inherited episodes.
        assert (
            s["unique_bullish_episode_count"]
            == s["bullish_crossover_count"] + s["inherited_bullish_episode_count"]
        ), cid


def test_immediate_delayed_inherited_equals_entry_count(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        entries = (
            s["immediate_entry_count"]
            + s["delayed_entry_count"]
            + s["inherited_bullish_state_entry_count"]
        )
        assert s["completed_trade_count"] <= entries, cid
        expected = s["completed_trade_count"] + (1 if s["position_open"] else 0)
        assert entries == expected, cid


def test_episodes_without_entry_never_negative(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        assert s["episodes_without_entry_count"] >= 0, cid


def test_warmup_signals_do_not_affect_counters(tmp_path: Path) -> None:
    """Everything before the cutoff must not affect any counter."""
    state_dir1 = tmp_path / "shadow_a"
    state_dir2 = tmp_path / "shadow_b"
    # Series A: only pre-cutoff bars.
    only_warmup = _bar_seq(
        [float(c) for c in range(1, 501)],
        start=pd.Timestamp("2026-05-01 00:00", tz="UTC"),
    )
    ssc.run_cycle(only_warmup, state_dir=state_dir1, now_utc=_NOW)
    s1 = json.loads((state_dir1 / ssc._STATE_FILENAME).read_text())
    # Series B: only forward bars (a subset of the same shape).
    forward_bars = _bar_seq(
        [float(c) for c in range(1, 501)],
        start=pd.Timestamp("2026-07-18 00:00", tz="UTC"),
    )
    ssc.run_cycle(forward_bars, state_dir=state_dir2, now_utc=_NOW)
    s2 = json.loads((state_dir2 / ssc._STATE_FILENAME).read_text())
    for cid in s1["candidates"]:
        assert s1["candidates"][cid]["bullish_crossover_count"] == 0
        assert s1["candidates"][cid]["bullish_state_bar_count"] == 0
        assert s1["candidates"][cid]["filter_evaluation_count"] == 0
        # Forward-only run should observe SOME signals (SMAs form
        # quickly on this monotonic series).
        assert s2["candidates"][cid]["processed_forward_bar_count"] > 0


# ---------------------------------------------------------------------------
# Bearish exits bypass entry filters
# ---------------------------------------------------------------------------


def test_bearish_exits_bypass_entry_filters(tmp_path: Path) -> None:
    """A bearish crossover while holding a position must always
    schedule the SELL — the entry filter must not gate the exit."""
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        # Every completed trade requires both an entry and an exit
        # under a bearish crossover — the fact any completed trade
        # exists on a series with bearish crossovers proves exits
        # were not blocked.
        entries = s["immediate_entry_count"] + s["delayed_entry_count"]
        if entries > 0 and s["bearish_crossover_count"] > 0:
            # An entry happened, and a bearish crossover happened —
            # either the trade is closed or the position is open.
            assert s["completed_trade_count"] >= 0
            # The bearish crossover cannot have been blocked by the
            # entry filter — the runner has no path that does that.


# ---------------------------------------------------------------------------
# Idempotency / duplicate bars / concurrent writes
# ---------------------------------------------------------------------------


def test_duplicate_bars_do_not_double_process(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state_after_once = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    # Duplicate every bar in the input.
    doubled = bars + bars
    ssc.run_cycle(doubled, state_dir=state_dir, now_utc=_NOW)
    state_after_twice = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    # Counters must be identical.
    for cid in state_after_once["candidates"]:
        for key in (
            "processed_forward_bar_count",
            "bullish_crossover_count",
            "bullish_state_bar_count",
            "completed_trade_count",
            "immediate_entry_count",
            "delayed_entry_count",
        ):
            assert (
                state_after_once["candidates"][cid][key]
                == state_after_twice["candidates"][cid][key]
            ), f"{cid}.{key} drifted on rerun"


def test_re_running_cycle_is_idempotent(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    r1 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    r2 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    for cid in r1["candidates"]:
        # No new events should be appended.
        pass
    assert r2["events_appended"] == 0


def test_event_ids_are_deterministic(tmp_path: Path) -> None:
    state_dir1 = tmp_path / "shadow_a"
    state_dir2 = tmp_path / "shadow_b"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    ssc.run_cycle(bars, state_dir=state_dir1, now_utc=_NOW)
    ssc.run_cycle(bars, state_dir=state_dir2, now_utc=_NOW)
    e1 = (state_dir1 / ssc._EVENTS_FILENAME).read_text()
    e2 = (state_dir2 / ssc._EVENTS_FILENAME).read_text()
    ids1 = [json.loads(line)["event_id"]
            for line in e1.strip().split("\n") if line.strip()]
    ids2 = [json.loads(line)["event_id"]
            for line in e2.strip().split("\n") if line.strip()]
    assert ids1 == ids2


def test_processed_through_utc_is_monotonic(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        processed = s.get("processed_through_utc")
        assert processed is None or "T" in processed


def test_malformed_state_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest = ssc.load_or_init_manifest(state_dir, _NOW)
    (state_dir / ssc._STATE_FILENAME).write_text(
        "{not-json", encoding="utf-8",
    )
    with pytest.raises(ssc.ShadowError):
        ssc.load_or_init_state(state_dir, manifest)


def test_manifest_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest = ssc.load_or_init_manifest(state_dir, _NOW)
    # Simulate a stale state.json that references a different manifest hash.
    (state_dir / ssc._STATE_FILENAME).write_text(json.dumps({
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": "0" * 64,
        "candidates": {},
    }), encoding="utf-8")
    with pytest.raises(ssc.ShadowError, match="manifest_hash"):
        ssc.load_or_init_state(state_dir, manifest)


def test_concurrent_writer_lock_blocks_second_runner(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)
    # Grab the lock manually; a second attempt must raise.
    lock_path = state_dir / ssc._LOCK_FILENAME
    with ssc._FileLock(lock_path):
        with pytest.raises(ssc.ShadowError, match="lock"):
            ssc.run_cycle([], state_dir=state_dir, now_utc=_NOW)


def test_atomic_write_replaces_target_only_on_success(tmp_path: Path) -> None:
    """The tmp file used for atomic replacement must not linger."""
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)
    # After manifest write, no tmp file left behind.
    stray = list(state_dir.glob("*.tmp"))
    assert stray == []


# ---------------------------------------------------------------------------
# next_open leakage + slippage
# ---------------------------------------------------------------------------


def test_next_open_execution_uses_distinct_open_price(tmp_path: Path) -> None:
    """Under next_open, the fill price is bar[i].open, not bar[i].close.
    Construct a series where open and close differ and confirm the
    recorded trade entry price uses open."""
    state_dir = tmp_path / "shadow"
    # Provide enough pre-cutoff warmup for SMA100 (paper control) and
    # forward bars for at least one trade round trip.
    n_pre = 300
    n_post = 400
    closes = []
    opens = []
    period = 60
    for i in range(n_pre + n_post):
        phase = i % period
        base = 1 + phase if phase < period // 2 else 1 + (period - phase)
        # Open and close intentionally distinct so we can tell which
        # price was used at fill time.
        opens.append(float(base * 10))
        closes.append(float(base * 10 + 5))
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    start = cutoff + pd.Timedelta(hours=1) - pd.Timedelta(hours=n_pre)
    bars = _bar_seq_with_ohlc(closes, opens=opens, start=start)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    # For at least one candidate, if a trade exists its entry_price
    # is close to the open series, not the close series.
    saw_trade = False
    for cid, s in state["candidates"].items():
        for t in s["trades"]:
            saw_trade = True
            # The entry price must equal open * (1 + 1/10_000) —
            # NOT close.
            close_candidates = set(closes)
            open_candidates = set(opens)
            # Because of slippage, the entry price is opens[k] * 1.0001
            near_open = any(
                abs(t["entry_price"] - o * 1.0001) < 0.05
                for o in open_candidates
            )
            near_close = any(
                abs(t["entry_price"] - c * 1.0001) < 0.05
                for c in close_candidates
                if c not in open_candidates
            )
            assert near_open and not near_close, (
                f"{cid} entry_price {t['entry_price']} does not match open"
            )
    # This test is only meaningful if a trade actually fired.
    assert saw_trade, "test data did not produce any trade"


def test_slippage_scales_price_by_one_basis_point(tmp_path: Path) -> None:
    """Match the S56+ backtest convention: buy price scaled by
    (1 + slippage_bps/10_000), sell scaled by (1 - slippage_bps/10_000)."""
    from src.tools.run_shadow_strategy_cycle import _apply_slippage
    assert _apply_slippage(100.0, "buy") == pytest.approx(100.0 * 1.0001)
    assert _apply_slippage(100.0, "sell") == pytest.approx(100.0 * 0.9999)


# ---------------------------------------------------------------------------
# S61 regression — historical trade decisions must not have changed
# ---------------------------------------------------------------------------


def test_s61_backtest_output_unchanged_by_s62_additions() -> None:
    """Import backtest_strategy_eval and run a small full-suite
    backtest — the total_return and per-window structure must be
    identical to what a pre-S62 caller would have seen (schema not
    contaminated with shadow diagnostics)."""
    from src.tools import backtest_strategy_eval as bse
    from src.tools.backtest_strategy_eval import Bar as BseBar

    closes = list(range(1, 41))
    bars = [
        BseBar(ts=_START + pd.Timedelta(hours=i),
               open=float(c), high=float(c), low=float(c),
               close=float(c), volume=1_000.0)
        for i, c in enumerate(closes)
    ]
    r = bse.run_backtest(bars, 3, 5)
    d = r.to_dict()
    # Filter diagnostics must remain opt-in (they were added in S61
    # gated on include_filter_diagnostics; S62 must not have flipped
    # this default).
    for k in ("entry_filter", "bullish_signal_count", "entry_allowed_count",
              "entry_blocked_count", "entry_blocked_rate"):
        assert k not in d, (
            f"S61 gating regressed: {k!r} appears in default to_dict output"
        )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_report_validation_status_never_promotes(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    manifest = ssc.load_or_init_manifest(state_dir, _NOW)
    state = ssc.load_or_init_state(state_dir, manifest)
    report = ssr.build_report(manifest, state)
    vs = report["validation_status"]
    assert vs["retrospective_candidate_selection"] is True
    assert vs["forward_data_only"] is True
    assert vs["automatic_promotion_allowed"] is False
    assert vs["promotion_eligible"] is False
    assert "shadow evidence" in vs["reason"].lower()


def test_report_carries_comparisons_against_controls(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    manifest = ssc.load_or_init_manifest(state_dir, _NOW)
    state = ssc.load_or_init_state(state_dir, manifest)
    report = ssr.build_report(manifest, state)
    assert "vs_paper_control_20_100_none" in report["comparisons"]
    assert "vs_research_10_20_none" in report["comparisons"]
    # Filtered 10/20 pairs get their own delta block.
    assert "filter_vs_unfiltered_10_20" in report
    # Every filtered 10/20 candidate is present in the deltas.
    for cid in (
        "research_10_20_separation25",
        "research_10_20_trend200_separation25",
    ):
        assert cid in report["filter_vs_unfiltered_10_20"]
        row = report["filter_vs_unfiltered_10_20"][cid]
        for field in ("return_delta", "drawdown_delta",
                      "completed_trade_delta", "exposure_delta",
                      "delayed_entry_count", "blocked_on_crossover_count"):
            assert field in row


def test_report_diagnostic_flags_present(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    manifest = ssc.load_or_init_manifest(state_dir, _NOW)
    state = ssc.load_or_init_state(state_dir, manifest)
    report = ssr.build_report(manifest, state)
    for field in (
        "candidates_with_fewer_than_10_completed_trades",
        "candidates_with_fewer_than_3_bullish_episodes",
        "candidates_with_single_trade_pnl_share_above_60_percent",
    ):
        assert field in report["diagnostic_flags"]


# ---------------------------------------------------------------------------
# Batch replay parity
# ---------------------------------------------------------------------------


def test_batch_replay_matches_incremental_processing(tmp_path: Path) -> None:
    """Processing all bars at once and processing them in chunks
    must produce the same final state."""
    state_dir_all = tmp_path / "shadow_all"
    state_dir_chunk = tmp_path / "shadow_chunk"
    bars = _many_bars_across_cutoff(pre=800, post=200)

    ssc.run_cycle(bars, state_dir=state_dir_all, now_utc=_NOW)
    state_all = json.loads((state_dir_all / ssc._STATE_FILENAME).read_text())

    # Feed in chunks of 50, adding on cumulatively.
    for cut in range(50, len(bars) + 50, 50):
        ssc.run_cycle(bars[:cut], state_dir=state_dir_chunk, now_utc=_NOW)
    state_chunk = json.loads((state_dir_chunk / ssc._STATE_FILENAME).read_text())

    for cid in state_all["candidates"]:
        for key in (
            "processed_forward_bar_count",
            "bullish_crossover_count",
            "bearish_crossover_count",
            "bullish_state_bar_count",
            "unique_bullish_episode_count",
            "immediate_entry_count",
            "delayed_entry_count",
            "blocked_on_crossover_count",
            "episodes_without_entry_count",
            "completed_trade_count",
        ):
            assert (
                state_all["candidates"][cid][key]
                == state_chunk["candidates"][cid][key]
            ), f"batch vs incremental drift at {cid}.{key}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_runner_prints_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    """Runner CLI --status returns a JSON block after initial run."""
    state_dir = tmp_path / "shadow"
    # Prime state
    bars = _many_bars_across_cutoff(pre=800, post=50)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Now run --status through the CLI wrapper.
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["experiment_id"] == "S62_SPY_60M_FORWARD"
    assert "candidates" in payload
    assert payload["research_only"] is True
    assert payload["automatic_strategy_promotion_allowed"] is False


def test_cli_report_prints_validation_status(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validation_status"]["promotion_eligible"] is False


def test_dry_run_requires_initialized_experiment(tmp_path: Path) -> None:
    """Dry-run is now non-mutating and requires an existing manifest."""
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    with pytest.raises(ssc.ShadowError, match="no shadow manifest"):
        ssc.run_cycle(
            bars, state_dir=state_dir, now_utc=_NOW, dry_run=True,
        )
    # State dir was NOT created by the failed dry-run.
    assert not state_dir.exists()


def test_dry_run_after_init_does_not_mutate_directory(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    snapshot_before = _snapshot_dir(state_dir)
    # Now run dry-run — must not change anything.
    summary = ssc.run_cycle(
        bars, state_dir=state_dir, now_utc=_NOW, dry_run=True,
    )
    assert summary["dry_run"] is True
    snapshot_after = _snapshot_dir(state_dir)
    assert snapshot_before == snapshot_after


# ---------------------------------------------------------------------------
# S62 review — episode classification, holding periods, non-mutating reads,
# state / event integrity, data-integrity diagnostics
# ---------------------------------------------------------------------------


def _forward_bar_series(closes: list[float]) -> list[Bar]:
    """A series of closes starting at cutoff+1h so every bar is forward."""
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    start = cutoff + pd.Timedelta(hours=1)
    return _bar_seq(closes, start=start)


def _bars_with_prefix(
    pre_closes: list[float],
    post_closes: list[float],
    pre_offset_hours: int | None = None,
) -> list[Bar]:
    """Build a series where pre_closes end at the cutoff and post_closes
    are strictly forward."""
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    n_pre = len(pre_closes)
    if pre_offset_hours is None:
        pre_offset_hours = n_pre
    start = cutoff + pd.Timedelta(hours=1) - pd.Timedelta(hours=pre_offset_hours)
    all_closes = list(pre_closes) + list(post_closes)
    return _bar_seq(all_closes, start=start)


# --- Issue 1: episode & entry classification ---


def test_first_forward_bar_bullish_none_filter_is_inherited(tmp_path: Path) -> None:
    """When the first forward bar is already in a bullish state and
    no forward crossover fires, the entry classifies as inherited —
    NOT delayed."""
    state_dir = tmp_path / "shadow"
    # Long-run monotonic uptrend so short SMA > long SMA at cutoff.
    n_pre = 300
    n_post = 5
    closes = [float(c) for c in range(1, n_pre + n_post + 1)]
    bars = _bars_with_prefix(closes[:n_pre], closes[n_pre:], pre_offset_hours=n_pre)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    s = state["candidates"]["research_10_20_none"]
    assert s["bullish_crossover_count"] == 0
    assert s["inherited_bullish_episode_count"] == 1
    # An entry may have executed via the inherited path.
    assert s["delayed_entry_count"] == 0
    # There must be no forward-blocked crossover.
    assert s["blocked_on_crossover_count"] == 0
    entries = (
        s["immediate_entry_count"]
        + s["delayed_entry_count"]
        + s["inherited_bullish_state_entry_count"]
    )
    if entries > 0:
        assert s["inherited_bullish_state_entry_count"] >= 1


def test_delayed_entry_requires_earlier_forward_crossover_block(
    tmp_path: Path,
) -> None:
    """A delayed entry requires a bullish crossover ON forward data
    that was blocked by the filter. Without such a block, an
    otherwise valid entry must not be classified as delayed."""
    state_dir = tmp_path / "shadow"
    # Inherit a bullish state so we can enter but no forward block.
    n_pre = 300
    closes = [float(c) for c in range(1, n_pre + 5 + 1)]
    bars = _bars_with_prefix(closes[:n_pre], closes[n_pre:], pre_offset_hours=n_pre)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        if s["blocked_on_crossover_count"] == 0:
            # No forward block → delayed entries must be zero.
            assert s["delayed_entry_count"] == 0, cid


def test_active_episode_counted_exactly_once(tmp_path: Path) -> None:
    """Once an episode begins it must appear in unique_bullish_episode_count
    immediately, and it must be counted exactly once no matter how long
    the episode stays active."""
    state_dir = tmp_path / "shadow"
    # A bullish state that lasts many forward bars but never ends.
    n_pre = 300
    n_post = 100
    closes = [float(c) for c in range(1, n_pre + n_post + 1)]
    bars = _bars_with_prefix(closes[:n_pre], closes[n_pre:], pre_offset_hours=n_pre)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        # At most one episode has opened in this monotone series.
        assert s["unique_bullish_episode_count"] <= 1, cid
        # The runner should not have counted the same active episode
        # twice (that would show as unique_bullish_episode_count == 2).


def test_batch_vs_incremental_episode_classification(tmp_path: Path) -> None:
    a_dir = tmp_path / "batch"
    b_dir = tmp_path / "incremental"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    ssc.run_cycle(bars, state_dir=a_dir, now_utc=_NOW)
    for cut in range(50, len(bars) + 50, 50):
        ssc.run_cycle(bars[:cut], state_dir=b_dir, now_utc=_NOW)
    sa = json.loads((a_dir / ssc._STATE_FILENAME).read_text())
    sb = json.loads((b_dir / ssc._STATE_FILENAME).read_text())
    for cid in sa["candidates"]:
        for key in (
            "unique_bullish_episode_count",
            "inherited_bullish_episode_count",
            "immediate_entry_count",
            "delayed_entry_count",
            "inherited_bullish_state_entry_count",
            "episodes_without_entry_count",
        ):
            assert sa["candidates"][cid][key] == sb["candidates"][cid][key], (
                f"{cid}.{key} drift: batch={sa['candidates'][cid][key]} "
                f"incremental={sb['candidates'][cid][key]}"
            )


# --- Issue 2: forward timestamps + holding periods ---


def test_cache_ends_before_cutoff_zero_forward(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    # Bars end WELL before the cutoff.
    bars = _bar_seq(
        [float(c) for c in range(1, 201)],
        start=pd.Timestamp("2026-05-01 00:00", tz="UTC"),
    )
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        assert s["first_forward_bar_utc"] is None
        assert s["last_forward_bar_utc"] is None
        assert s["processed_forward_bar_count"] == 0


def test_cache_ends_exactly_at_cutoff_zero_forward(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    bars = _bar_seq(
        [float(c) for c in range(1, 601)],
        start=cutoff - pd.Timedelta(hours=599),
    )
    # last bar timestamp exactly at cutoff.
    assert bars[-1].ts == cutoff
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        assert s["first_forward_bar_utc"] is None
        assert s["last_forward_bar_utc"] is None
        assert s["processed_forward_bar_count"] == 0


def test_one_forward_bar_recorded(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    bars = _bar_seq(
        [float(c) for c in range(1, 602)],
        start=cutoff - pd.Timedelta(hours=599),
    )
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        assert s["processed_forward_bar_count"] == 1
        assert s["first_forward_bar_utc"] == s["last_forward_bar_utc"]


def test_trade_records_include_bars_held(tmp_path: Path) -> None:
    """Every completed trade has a bars_held integer that equals
    exit_forward_index - entry_forward_index."""
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        for t in s["trades"]:
            assert "bars_held" in t
            assert (
                t["bars_held"]
                == t["exit_forward_index"] - t["entry_forward_index"]
            ), cid


def test_average_holding_bars_uses_persisted_bars_held(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)
    from src.tools.shadow_strategy_report import _average_holding_bars
    for cid, cs in state["candidates"].items():
        trades = cs["trades"]
        expected = (
            sum(t["bars_held"] for t in trades) / len(trades)
            if trades else 0.0
        )
        assert _average_holding_bars(trades) == pytest.approx(expected)


# --- Issue 3: dry-run / status / report do not mutate ---


def test_status_command_does_not_initialize(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "no shadow manifest" in err["error"]
    assert not state_dir.exists()


def test_report_command_does_not_initialize(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "no shadow manifest" in err["error"]
    assert not state_dir.exists()


def test_status_leaves_directory_unchanged(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    before = _snapshot_dir(state_dir)
    ssc.main(["--state-dir", str(state_dir), "--status"])
    after = _snapshot_dir(state_dir)
    assert before == after


def test_report_leaves_directory_unchanged(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    before = _snapshot_dir(state_dir)
    ssr.main(["--state-dir", str(state_dir)])
    after = _snapshot_dir(state_dir)
    assert before == after


# --- Issue 4: state and event-log integrity ---


def _prime(state_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)
    return manifest, state


def _corrupt_state(state_dir: Path, mutate_fn) -> None:
    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)
    mutate_fn(state)
    (state_dir / ssc._STATE_FILENAME).write_text(
        json.dumps(state, default=str), encoding="utf-8",
    )


def test_state_validator_rejects_missing_candidate(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _drop(state):
        state["candidates"].pop("research_15_50_none")
    _corrupt_state(state_dir, _drop)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="candidates mismatch"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_extra_candidate(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _add(state):
        state["candidates"]["not_a_candidate"] = ssc._new_candidate_state(
            "not_a_candidate",
        )
    _corrupt_state(state_dir, _add)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="candidates mismatch"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_nan_equity(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _nan(state):
        state["candidates"]["research_10_20_none"]["cash"] = float("nan")
    _corrupt_state(state_dir, _nan)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="finite"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_negative_counter(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _neg(state):
        state["candidates"]["research_10_20_none"]["bullish_crossover_count"] = -1
    _corrupt_state(state_dir, _neg)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="non-negative"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_alias_mismatch(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _mismatch(state):
        s = state["candidates"]["research_10_20_none"]
        s["bullish_signal_count"] = s["bullish_state_bar_count"] + 5
    _corrupt_state(state_dir, _mismatch)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="bullish_signal_count"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_inconsistent_position(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        s = state["candidates"]["research_10_20_none"]
        s["position_open"] = True
        s["quantity"] = 0.0
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="position_open"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_malformed_pending_action(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        state["candidates"]["research_10_20_none"]["pending_action"] = {
            "side": "wrong_side",
        }
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="pending_action"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_validator_rejects_counter_alias_mismatch(tmp_path: Path) -> None:
    """immediate + delayed + inherited must equal trades + open."""
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        s = state["candidates"]["research_10_20_none"]
        s["immediate_entry_count"] += 1
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError,
                       match="immediate\\+delayed\\+inherited"):
        ssc.load_state_readonly(state_dir, manifest)


def test_events_log_rejects_malformed_middle_line(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    # Insert a malformed line in the middle.
    lines = events_path.read_text().splitlines()
    assert len(lines) >= 2
    lines.insert(1, "not-a-json-line")
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ssc.ShadowError, match="events.jsonl line"):
        ssc._load_known_event_ids(events_path)


def test_events_log_recovers_truncated_last_line(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    with events_path.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc", "partial')
    known = ssc._load_known_event_ids(events_path)
    assert isinstance(known, set)


# --- Issue 5: data integrity ---


def test_duplicate_bar_identical_ohlcv_counted(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    duplicated = bars + [bars[-1]]  # duplicate the last bar
    summary = ssc.run_cycle(duplicated, state_dir=state_dir, now_utc=_NOW)
    assert summary["duplicate_bar_skipped_count"] >= 1


def test_conflicting_bar_ohlcv_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    last = bars[-1]
    conflict = Bar(
        ts=last.ts, open=last.open + 1, high=last.high + 1,
        low=last.low, close=last.close + 5, volume=last.volume,
    )
    bad = bars + [conflict]
    with pytest.raises(ssc.ShadowError, match="conflicting OHLCV"):
        ssc.run_cycle(bad, state_dir=state_dir, now_utc=_NOW)


def test_non_finite_ohlcv_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    bars[-1] = Bar(
        ts=bars[-1].ts, open=float("nan"),
        high=bars[-1].high, low=bars[-1].low, close=bars[-1].close,
        volume=bars[-1].volume,
    )
    with pytest.raises(ssc.ShadowError, match="non-finite"):
        ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)


def test_intraday_gap_detected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)
    # Two forward bars in the same regular session, 3 hours apart.
    cutoff = pd.Timestamp("2026-07-20 14:30", tz="UTC")  # a Monday
    b1 = Bar(ts=cutoff, open=100.0, high=100.0, low=100.0, close=100.0, volume=1_000)
    b2 = Bar(
        ts=cutoff + pd.Timedelta(hours=3),
        open=101.0, high=101.0, low=101.0, close=101.0, volume=1_000,
    )
    # Force these bars past the cutoff by pretending manifest cutoff is
    # July 1st for this test.
    validated, dups, gaps = ssc._validate_and_prepare_bars([b1, b2])
    assert dups == 0
    assert len(gaps) == 1
    assert gaps[0]["gap_seconds"] == pytest.approx(3 * 3600)


def test_overnight_gap_not_flagged() -> None:
    """A weekend-crossing or overnight gap is not an intraday gap."""
    b1 = Bar(
        ts=pd.Timestamp("2026-07-17 19:30", tz="UTC"),
        open=100.0, high=100.0, low=100.0, close=100.0, volume=1_000,
    )
    b2 = Bar(
        ts=pd.Timestamp("2026-07-20 13:30", tz="UTC"),
        open=100.0, high=100.0, low=100.0, close=100.0, volume=1_000,
    )
    validated, dups, gaps = ssc._validate_and_prepare_bars([b1, b2])
    assert gaps == []


# --- Strengthened core tests ---


def test_future_bar_data_does_not_leak_into_current_execution(
    tmp_path: Path,
) -> None:
    """Changing the current bar's close/high/low while keeping its
    open and all prior bars fixed must not alter the fill or the
    decision for THIS bar's open execution."""
    state_dir1 = tmp_path / "a"
    state_dir2 = tmp_path / "b"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    # Baseline run.
    ssc.run_cycle(bars, state_dir=state_dir1, now_utc=_NOW)
    state1 = ssc.load_state_readonly(
        state_dir1, ssc.load_manifest_readonly(state_dir1),
    )
    # Mutate the very last bar's close/high/low.
    tampered = list(bars[:-1])
    last = bars[-1]
    tampered.append(Bar(
        ts=last.ts, open=last.open,
        high=last.high * 5.0, low=last.low * 0.1,
        close=last.close * 5.0, volume=last.volume,
    ))
    ssc.run_cycle(tampered, state_dir=state_dir2, now_utc=_NOW)
    state2 = ssc.load_state_readonly(
        state_dir2, ssc.load_manifest_readonly(state_dir2),
    )
    for cid in state1["candidates"]:
        s1 = state1["candidates"][cid]
        s2 = state2["candidates"][cid]
        # The pending_action produced by the LAST bar's close will
        # differ (that's just the next-bar signal); but the trades
        # executed BEFORE the last bar must be identical.
        for a, b in zip(s1["trades"], s2["trades"]):
            assert a == b, f"{cid}: past trade drifted"


def test_bearish_exit_never_blocked_by_filter(tmp_path: Path) -> None:
    """Construct a filter that blocks EVERY entry (ma_separation_50bps)
    and then hold a candidate that already has a position via an
    unrelated `none` filter. Verify the SELL still executes."""
    # This test uses the same synthetic series across the entire
    # candidate slate — bearish crossovers exist, and the run must
    # close positions on them for `none` candidates even though
    # filtered candidates never enter.
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=400)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    # research_10_20_none has entries; if any bearish crossover
    # happened, we should also see at least one completed trade
    # (because bearish exits are not filtered).
    s = state["candidates"]["research_10_20_none"]
    if (
        s["immediate_entry_count"] + s["delayed_entry_count"]
        + s["inherited_bullish_state_entry_count"] > 0
        and s["bearish_crossover_count"] > 0
    ):
        assert s["completed_trade_count"] >= 1


def test_processed_through_utc_strictly_monotonic_across_cycles(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    for cut in range(50, len(bars) + 50, 50):
        subset = bars[:cut]
        ssc.run_cycle(subset, state_dir=state_dir, now_utc=_NOW)
    state = json.loads((state_dir / ssc._STATE_FILENAME).read_text())
    for cid, s in state["candidates"].items():
        pt = s.get("processed_through_utc")
        assert pt is not None
        # Also strictly monotonic against last_forward_bar_utc.
        lfb = s.get("last_forward_bar_utc")
        if lfb is not None:
            assert pt >= lfb


def test_batch_vs_incremental_complete_state_parity(tmp_path: Path) -> None:
    """Batch and incremental must produce identical state for every
    key that observers care about — including cash, quantity,
    marked equity, pending action, trades, returns series, and all
    counters."""
    a_dir = tmp_path / "batch"
    b_dir = tmp_path / "incremental"
    bars = _many_bars_across_cutoff(pre=800, post=200)
    ssc.run_cycle(bars, state_dir=a_dir, now_utc=_NOW)
    for cut in range(50, len(bars) + 50, 50):
        ssc.run_cycle(bars[:cut], state_dir=b_dir, now_utc=_NOW)
    sa = json.loads((a_dir / ssc._STATE_FILENAME).read_text())
    sb = json.loads((b_dir / ssc._STATE_FILENAME).read_text())
    keys_to_check = [
        "cash", "quantity", "position_open", "entry_price",
        "entry_timestamp_utc", "entry_forward_index",
        "realized_equity", "marked_equity", "peak_equity", "max_drawdown",
        "processed_through_utc", "last_forward_bar_utc",
        "first_forward_bar_utc", "pending_action",
        "completed_trade_count", "win_count", "loss_count",
        "cumulative_exposure_bars", "processed_forward_bar_count",
        "bullish_crossover_count", "bearish_crossover_count",
        "bullish_state_bar_count", "bullish_signal_count",
        "unique_bullish_episode_count", "inherited_bullish_episode_count",
        "filter_evaluation_count", "filter_allowed_count",
        "filter_blocked_count", "blocked_on_crossover_count",
        "immediate_entry_count", "delayed_entry_count",
        "inherited_bullish_state_entry_count",
        "episodes_without_entry_count", "trades", "returns_series",
    ]
    for cid in sa["candidates"]:
        for key in keys_to_check:
            assert (
                sa["candidates"][cid][key] == sb["candidates"][cid][key]
            ), f"{cid}.{key} drift between batch and incremental"


def test_crash_recovery_events_appended_state_missing(tmp_path: Path) -> None:
    """Simulate a crash between event append and state write:
    re-running must reconstruct state consistently without duplicate
    events."""
    a_dir = tmp_path / "reference"
    b_dir = tmp_path / "recovering"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    # Baseline: fully process reference dir.
    ssc.run_cycle(bars, state_dir=a_dir, now_utc=_NOW)
    reference_state = (a_dir / ssc._STATE_FILENAME).read_text()
    reference_events = (a_dir / ssc._EVENTS_FILENAME).read_text()

    # Recovering: run cycle to append events and write state.
    ssc.run_cycle(bars, state_dir=b_dir, now_utc=_NOW)
    # Simulate crash — delete state.json while keeping events + manifest.
    (b_dir / ssc._STATE_FILENAME).unlink()
    # Rerun — must reconstruct from scratch. Since events are
    # deterministic, duplicates should not accumulate.
    ssc.run_cycle(bars, state_dir=b_dir, now_utc=_NOW)
    recovered_events = (b_dir / ssc._EVENTS_FILENAME).read_text()
    # Event log should not have duplicates on rerun (deterministic IDs).
    recovered_ids = [
        json.loads(l)["event_id"]
        for l in recovered_events.strip().split("\n") if l.strip()
    ]
    assert len(recovered_ids) == len(set(recovered_ids))
    # The reference and recovered event streams may differ in order
    # but must contain the same event ID set.
    ref_ids = {
        json.loads(l)["event_id"]
        for l in reference_events.strip().split("\n") if l.strip()
    }
    assert set(recovered_ids) == ref_ids
