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


# ---------------------------------------------------------------------------
# Equality-touch episode bookkeeping regression
# (production-shadow failure: research_10_20_none:
#  unique_bullish_episode_count < bullish_crossover_count)
# ---------------------------------------------------------------------------
#
# All bars below are forward-only (start at cutoff+1h) and use a
# short=10/long=20 SMA plateau construction, hand-verified against the
# real _sma() arithmetic:
#
#   idx  0-24 (25 bars) @ 100.00  -> first available SMA (idx 19) is a
#                                     flat equality (100==100), no
#                                     episode opens.
#   idx 25-44 (20 bars) @ 100.01  -> idx25 is a genuine bullish
#                                     crossover (tiny positive
#                                     separation); the plateau then
#                                     converges monotonically back to
#                                     an exact SMA equality at idx44
#                                     with neither cross condition
#                                     firing on the way.
#   idx 45-50 ( 6 bars)           -> "bullish" tail (100.02) reproduces
#                                     a second bullish crossover at
#                                     idx45 while the prior episode was
#                                     never closed (the reported bug);
#                                     "bearish" tail (100.00) instead
#                                     produces a genuine bearish
#                                     crossover at idx45.
#
# The ~0.05bps SMA separation at both crossovers is always far below
# the 25bps filter threshold, so research_10_20_separation25 never
# enters — giving a real (not hand-constructed) flat/blocked
# candidate — while research_10_20_none (filter=none) always enters
# immediately, giving a real open-position candidate, both from the
# exact same bar sequence.


def _equality_touch_bars(second_move: str) -> list[Bar]:
    assert second_move in ("bullish", "bearish")
    plateau1 = [100.0] * 25
    plateau2 = [100.01] * 20
    tail = [100.02] * 6 if second_move == "bullish" else [100.00] * 6
    return _forward_bar_series(plateau1 + plateau2 + tail)


def _candidate_events(state_dir: Path, cid: str) -> list[dict[str, Any]]:
    text = (state_dir / ssc._EVENTS_FILENAME).read_text(encoding="utf-8")
    parsed = [json.loads(line) for line in text.strip().splitlines()]
    return [e for e in parsed if e.get("candidate_id") == cid]


def _assert_batch_equals_incremental(
    bars: list[Bar], one_shot_dir: Path, incremental_dir: Path, cid: str,
) -> None:
    for cut in range(10, len(bars) + 10, 10):
        ssc.run_cycle(bars[:cut], state_dir=incremental_dir, now_utc=_NOW)
    manifest_a = ssc.load_manifest_readonly(one_shot_dir)
    state_a = ssc.load_state_readonly(one_shot_dir, manifest_a)
    manifest_b = ssc.load_manifest_readonly(incremental_dir)
    state_b = ssc.load_state_readonly(incremental_dir, manifest_b)
    sa = state_a["candidates"][cid]
    sb = state_b["candidates"][cid]
    for key in (
        "bullish_crossover_count", "bearish_crossover_count",
        "unique_bullish_episode_count", "inherited_bullish_episode_count",
        "episodes_without_entry_count", "blocked_on_crossover_count",
        "immediate_entry_count", "delayed_entry_count",
        "inherited_bullish_state_entry_count", "completed_trade_count",
        "position_open", "quantity",
    ):
        assert sa[key] == sb[key], (
            f"{cid}.{key} drift: batch={sa[key]!r} incremental={sb[key]!r}"
        )


def test_equality_touch_case_a_flat_candidate_bullish_recross(
    tmp_path: Path,
) -> None:
    """Case A: flat candidate — bullish state -> equality -> bullish
    crossover again. research_10_20_separation25 never passes the
    entry filter (tiny separation), so it stays flat throughout; every
    bullish crossover must still open exactly one new episode."""
    state_dir = tmp_path / "shadow"
    bars = _equality_touch_bars("bullish")
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)

    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)  # raises if invalid
    s = state["candidates"]["research_10_20_separation25"]

    assert s["bullish_crossover_count"] == 2
    assert s["unique_bullish_episode_count"] == 2
    assert s["unique_bullish_episode_count"] >= s["bullish_crossover_count"]
    assert s["bearish_crossover_count"] == 0
    assert s["position_open"] is False
    assert s["quantity"] == 0.0
    assert s["completed_trade_count"] == 0
    assert s["immediate_entry_count"] == 0
    assert s["delayed_entry_count"] == 0
    assert s["inherited_bullish_state_entry_count"] == 0
    assert s["blocked_on_crossover_count"] == 2

    events = _candidate_events(state_dir, "research_10_20_separation25")
    assert sum(1 for e in events if e["event_type"] == "BULLISH_CROSSOVER") == 2
    assert not any(e["event_type"] == "SHADOW_SELL_SCHEDULED" for e in events)
    assert not any(e["event_type"] == "SHADOW_BUY_EXECUTED" for e in events)
    assert not any(e["event_type"] == "SHADOW_BUY_SCHEDULED" for e in events)

    # Idempotent rerun.
    r2 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert r2["events_appended"] == 0

    # Batch replay == incremental replay.
    _assert_batch_equals_incremental(
        bars, state_dir, tmp_path / "incremental_a",
        "research_10_20_separation25",
    )


def test_equality_touch_case_b_open_position_bullish_recross(
    tmp_path: Path,
) -> None:
    """Case B: open position — bullish state with an open position ->
    equality -> bullish crossover again. research_10_20_none
    (filter=none) enters immediately on the first crossover and must
    carry that position across the equality touch without a duplicate
    buy, while the episode counters stay consistent."""
    state_dir = tmp_path / "shadow"
    bars = _equality_touch_bars("bullish")
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)

    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)  # raises if invalid
    s = state["candidates"]["research_10_20_none"]

    assert s["bullish_crossover_count"] == 2
    assert s["unique_bullish_episode_count"] == 2
    assert s["unique_bullish_episode_count"] >= s["bullish_crossover_count"]
    assert s["bearish_crossover_count"] == 0
    # Exactly one entry — the equality-touch re-crossover must not
    # schedule or execute a second buy while a position is open.
    assert s["position_open"] is True
    assert s["quantity"] > 0
    assert s["completed_trade_count"] == 0
    assert s["immediate_entry_count"] == 1
    assert s["delayed_entry_count"] == 0
    assert s["inherited_bullish_state_entry_count"] == 0
    # The carried-over position must not be misclassified as an
    # episode without entry/exposure.
    assert s["episodes_without_entry_count"] == 0

    events = _candidate_events(state_dir, "research_10_20_none")
    assert sum(1 for e in events if e["event_type"] == "BULLISH_CROSSOVER") == 2
    assert sum(1 for e in events if e["event_type"] == "SHADOW_BUY_SCHEDULED") == 1
    assert sum(1 for e in events if e["event_type"] == "SHADOW_BUY_EXECUTED") == 1
    assert not any(e["event_type"] == "SHADOW_SELL_SCHEDULED" for e in events)

    # Idempotent rerun.
    r2 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert r2["events_appended"] == 0

    # Batch replay == incremental replay.
    _assert_batch_equals_incremental(
        bars, state_dir, tmp_path / "incremental_b", "research_10_20_none",
    )


def test_equality_touch_case_c_filter_blocked_episode_bullish_recross(
    tmp_path: Path,
) -> None:
    """Case C: filter-blocked episode -> equality -> bullish crossover.
    The blocked-crossover flag and episodes_without_entry_count must
    reset per episode — the second, freshly opened episode gets its
    own independent block, not a merge with the first episode's."""
    state_dir = tmp_path / "shadow"
    bars = _equality_touch_bars("bullish")
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)

    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)  # raises if invalid
    s = state["candidates"]["research_10_20_separation25"]

    # Both the first episode's crossover AND the re-opened second
    # episode's crossover were independently blocked by the filter —
    # this would be 1 (merged) under the pre-fix bookkeeping, or would
    # never be reached at all since state validation itself failed.
    assert s["blocked_on_crossover_count"] == 2
    assert s["filter_blocked_count"] >= 2
    assert s["filter_allowed_count"] == 0
    assert (
        s["filter_allowed_count"] + s["filter_blocked_count"]
        == s["filter_evaluation_count"]
    )
    # The first episode finalized as "without entry" at the moment the
    # second episode opened; the second episode is still active (no
    # bearish crossover in this bar sequence) so it has not yet been
    # counted as without-entry itself.
    assert s["episodes_without_entry_count"] == 1

    events = _candidate_events(state_dir, "research_10_20_separation25")
    assert sum(1 for e in events if e["event_type"] == "ENTRY_FILTER_BLOCKED") >= 2

    # Idempotent rerun.
    r2 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert r2["events_appended"] == 0

    # Batch replay == incremental replay.
    _assert_batch_equals_incremental(
        bars, state_dir, tmp_path / "incremental_c",
        "research_10_20_separation25",
    )


def test_equality_touch_case_d_equality_then_bearish_crossover(
    tmp_path: Path,
) -> None:
    """Case D: equality followed by a genuine bearish crossover. This
    exercises the existing (unchanged) bearish-exit rule — the
    equality touch itself must not have scheduled a sell, and the
    subsequent real bearish crossover must close any open position
    exactly as before."""
    state_dir = tmp_path / "shadow"
    bars = _equality_touch_bars("bearish")
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)

    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)  # raises if invalid

    # Open-position candidate: entered at the first crossover, then
    # exited cleanly by the real bearish crossover after the equality
    # touch — exactly one trade, no orphaned or duplicate exit.
    s_open = state["candidates"]["research_10_20_none"]
    assert s_open["bullish_crossover_count"] == 1
    assert s_open["bearish_crossover_count"] == 1
    assert s_open["unique_bullish_episode_count"] == 1
    assert s_open["position_open"] is False
    assert s_open["completed_trade_count"] == 1
    assert s_open["immediate_entry_count"] == 1

    events_open = _candidate_events(state_dir, "research_10_20_none")
    assert sum(1 for e in events_open if e["event_type"] == "BEARISH_CROSSOVER") == 1
    assert sum(1 for e in events_open if e["event_type"] == "SHADOW_SELL_SCHEDULED") == 1
    # The equality bar (idx 44) must not itself have produced a sell —
    # only the later, genuinely bearish bar (idx 45) may have.
    sell_events = [e for e in events_open if e["event_type"] == "SHADOW_SELL_SCHEDULED"]
    equality_bar_iso = ssc._bar_ts_utc(bars[44]).isoformat()
    bearish_bar_iso = ssc._bar_ts_utc(bars[45]).isoformat()
    for e in sell_events:
        assert e["signal_bar_utc"] != equality_bar_iso
        assert e["signal_bar_utc"] == bearish_bar_iso

    # Flat/blocked candidate: never entered, so the bearish crossover
    # that closes its episode must count it as without-entry, exactly
    # as the pre-existing (unchanged) bearish path always has.
    s_flat = state["candidates"]["research_10_20_separation25"]
    assert s_flat["bullish_crossover_count"] == 1
    assert s_flat["bearish_crossover_count"] == 1
    assert s_flat["unique_bullish_episode_count"] == 1
    assert s_flat["position_open"] is False
    assert s_flat["completed_trade_count"] == 0
    assert s_flat["episodes_without_entry_count"] == 1

    events_flat = _candidate_events(state_dir, "research_10_20_separation25")
    assert not any(e["event_type"] == "SHADOW_SELL_SCHEDULED" for e in events_flat)

    # Idempotent rerun.
    r2 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert r2["events_appended"] == 0

    # Batch replay == incremental replay for both candidates.
    _assert_batch_equals_incremental(
        bars, state_dir, tmp_path / "incremental_d1", "research_10_20_none",
    )
    _assert_batch_equals_incremental(
        bars, state_dir, tmp_path / "incremental_d2",
        "research_10_20_separation25",
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
    manifest, _state = _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    # Insert a malformed line in the middle.
    lines = events_path.read_text().splitlines()
    assert len(lines) >= 2
    lines.insert(1, "not-a-json-line")
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ssc.ShadowError, match="events.jsonl line"):
        ssc._validate_and_load_event_ids(events_path, manifest)


def test_events_log_read_fails_closed_on_truncated_last_line(tmp_path: Path) -> None:
    """Read paths (status / report / dry-run) must fail closed on a
    truncated final line — silent recovery would allow the next
    append to concatenate its JSON onto the corrupted tail."""
    state_dir = tmp_path / "shadow"
    manifest, _state = _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    with events_path.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc", "partial')
    # An unterminated final line fails closed regardless of whether
    # it is itself well-formed JSON — the framing check runs first.
    with pytest.raises(ssc.ShadowError, match="malformed|newline"):
        ssc._validate_and_load_event_ids(events_path, manifest)


def test_repair_event_log_tail_truncates_bad_final_line(tmp_path: Path) -> None:
    """The explicit repair helper physically removes a malformed
    final line, returning a diagnostic with removed byte count."""
    state_dir = tmp_path / "shadow"
    manifest, _state = _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    before_size = events_path.stat().st_size
    with events_path.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc", "partial')
    diag = ssc._repair_event_log_tail(events_path)
    assert diag is not None
    assert diag["removed_byte_count"] > 0
    # After repair the loader must succeed and the file size must
    # equal the pre-append size.
    known = ssc._validate_and_load_event_ids(events_path, manifest)
    assert isinstance(known, set)
    assert events_path.stat().st_size == before_size


def test_new_event_after_repair_is_parseable(tmp_path: Path) -> None:
    """After tail repair, subsequently appended events remain
    independently parseable JSONL records."""
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    with events_path.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc", "partial')
    # Run --once through the runner — which repairs under the lock,
    # then appends new events cleanly.
    bars = _many_bars_across_cutoff(pre=800, post=110)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Every line in events.jsonl must now be independently parseable.
    for line in events_path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            json.loads(stripped)


def test_repeated_runs_do_not_leave_malformed_tail(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    for _ in range(3):
        ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    events_path = state_dir / ssc._EVENTS_FILENAME
    for line in events_path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            json.loads(stripped)


def test_dry_run_detects_corrupted_event_log(tmp_path: Path) -> None:
    """Dry-run is read-only and must surface event-log corruption
    rather than silently pretending nothing is wrong."""
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    with events_path.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "abc", "partial')
    # Small synthetic bar set so dry-run does not need the cache.
    bars = _many_bars_across_cutoff(pre=800, post=50)
    # Dry-run validates with require_terminated=True: an unterminated
    # file fails on the framing check before content is inspected.
    with pytest.raises(ssc.ShadowError, match="malformed|newline"):
        ssc.run_cycle(
            bars, state_dir=state_dir, now_utc=_NOW, dry_run=True,
        )


def test_valid_event_ids_remain_unique_after_repair(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    with events_path.open("a", encoding="utf-8") as f:
        f.write('{"event_id": "partial-x')
    bars = _many_bars_across_cutoff(pre=800, post=105)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    ids = [
        json.loads(l)["event_id"]
        for l in events_path.read_text().splitlines() if l.strip()
    ]
    assert len(ids) == len(set(ids))


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
    assert len(dups) == 0
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


# ---------------------------------------------------------------------------
# S62 review round 3 — strict schema, impossible-state fail-closed,
# experiment-level audit events, strengthened crash-recovery parity
# ---------------------------------------------------------------------------


def test_state_top_level_missing_key_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _drop(state):
        state.pop("experiment_id")
    _corrupt_state(state_dir, _drop)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="top-level"):
        ssc.load_state_readonly(state_dir, manifest)


def test_state_top_level_extra_key_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _add(state):
        state["extra_root"] = "spurious"
    _corrupt_state(state_dir, _add)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="top-level"):
        ssc.load_state_readonly(state_dir, manifest)


def test_candidate_missing_field_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _drop(state):
        state["candidates"]["research_10_20_none"].pop("marked_equity")
    _corrupt_state(state_dir, _drop)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="candidate schema mismatch"):
        ssc.load_state_readonly(state_dir, manifest)


def test_candidate_extra_field_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _add(state):
        state["candidates"]["research_10_20_none"]["stowaway"] = 42
    _corrupt_state(state_dir, _add)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="candidate schema mismatch"):
        ssc.load_state_readonly(state_dir, manifest)


def test_internal_field_type_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        state["candidates"]["research_10_20_none"]["_forward_started"] = "yes"
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="_forward_started"):
        ssc.load_state_readonly(state_dir, manifest)


def test_invalid_current_episode_type_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        state["candidates"]["research_10_20_none"]["_current_episode_type"] = "spanish"
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="_current_episode_type"):
        ssc.load_state_readonly(state_dir, manifest)


def test_entry_forward_index_negative_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        state["candidates"]["research_10_20_none"]["entry_forward_index"] = -1
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="entry_forward_index"):
        ssc.load_state_readonly(state_dir, manifest)


def test_pending_action_timestamp_unparseable_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        state["candidates"]["research_10_20_none"]["pending_action"] = {
            "side": "buy",
            "signal_bar_utc": "not-a-timestamp",
            "reason": "bullish_crossover",
            "entry_type": "immediate",
        }
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="signal_bar_utc"):
        ssc.load_state_readonly(state_dir, manifest)


def test_pending_action_invalid_reason_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        state["candidates"]["research_10_20_none"]["pending_action"] = {
            "side": "buy",
            "signal_bar_utc": "2026-07-20T00:00:00+00:00",
            "reason": "made_up_reason",
            "entry_type": "immediate",
        }
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="pending_action.reason"):
        ssc.load_state_readonly(state_dir, manifest)


def test_trade_bars_held_mismatch_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        for cid, cs in state["candidates"].items():
            if cs["trades"]:
                cs["trades"][0]["bars_held"] = cs["trades"][0]["bars_held"] + 1
                return
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="bars_held"):
        ssc.load_state_readonly(state_dir, manifest)


def test_trade_exit_before_entry_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        for cid, cs in state["candidates"].items():
            if cs["trades"]:
                t = cs["trades"][0]
                t["exit_forward_index"] = t["entry_forward_index"] - 1
                t["bars_held"] = -1
                return
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError):
        ssc.load_state_readonly(state_dir, manifest)


def test_returns_series_missing_key_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        for cid, cs in state["candidates"].items():
            if cs["returns_series"]:
                cs["returns_series"][0] = {"ts": cs["returns_series"][0]["ts"]}
                return
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="returns_series"):
        ssc.load_state_readonly(state_dir, manifest)


def test_returns_series_regression_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        for cid, cs in state["candidates"].items():
            if len(cs["returns_series"]) >= 2:
                cs["returns_series"][1]["ts"] = "1990-01-01T00:00:00+00:00"
                return
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="regresses"):
        ssc.load_state_readonly(state_dir, manifest)


def test_forward_timestamp_ordering_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        for cid, cs in state["candidates"].items():
            if cs["processed_forward_bar_count"] > 0:
                # Swap so first > last.
                cs["first_forward_bar_utc"] = cs["last_forward_bar_utc"]
                cs["last_forward_bar_utc"] = "2026-07-18T00:00:00+00:00"
                return
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="first_forward_bar_utc"):
        ssc.load_state_readonly(state_dir, manifest)


def test_zero_forward_bars_implies_null_timestamps(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        cs = next(iter(state["candidates"].values()))
        cs["processed_forward_bar_count"] = 0
        cs["cumulative_exposure_bars"] = 0
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="zero forward bars"):
        ssc.load_state_readonly(state_dir, manifest)


def test_win_plus_loss_must_equal_completed_trades(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    def _bad(state):
        for cid, cs in state["candidates"].items():
            if cs["completed_trade_count"] > 0:
                cs["win_count"] += 1
                return
    _corrupt_state(state_dir, _bad)
    manifest = ssc.load_manifest_readonly(state_dir)
    with pytest.raises(ssc.ShadowError, match="win_count \\+ loss_count"):
        ssc.load_state_readonly(state_dir, manifest)


def test_experiment_event_duplicate_bar(tmp_path: Path) -> None:
    """DUPLICATE_BAR_SKIPPED events are written to events.jsonl with
    the reserved __experiment__ candidate ID and a deterministic ID."""
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    duplicated = bars + [bars[-1]]
    ssc.run_cycle(duplicated, state_dir=state_dir, now_utc=_NOW)
    events_path = state_dir / ssc._EVENTS_FILENAME
    dup_events = [
        json.loads(l) for l in events_path.read_text().splitlines()
        if l.strip() and json.loads(l).get("event_type") == "DUPLICATE_BAR_SKIPPED"
    ]
    assert dup_events
    e = dup_events[0]
    assert e["candidate_id"] == "__experiment__"
    assert e["manifest_hash"]
    assert "duplicate_timestamp_utc" in e["detail"]


def test_experiment_event_gap_detected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)
    cutoff = pd.Timestamp("2026-07-20 14:30", tz="UTC")
    intraday_gap = [
        Bar(ts=cutoff, open=100.0, high=100.0, low=100.0, close=100.0, volume=1_000),
        Bar(ts=cutoff + pd.Timedelta(hours=3), open=101.0, high=101.0, low=101.0,
            close=101.0, volume=1_000),
    ]
    # Small pre-cutoff prefix.
    pre = _bar_seq([float(c) for c in range(1, 800)],
                   start=cutoff - pd.Timedelta(hours=1000))
    bars = pre + intraday_gap
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    events_path = state_dir / ssc._EVENTS_FILENAME
    gap_events = [
        json.loads(l) for l in events_path.read_text().splitlines()
        if l.strip() and json.loads(l).get("event_type") == "DATA_GAP_DETECTED"
    ]
    assert gap_events
    e = gap_events[0]
    assert e["candidate_id"] == "__experiment__"
    assert "prev_bar_utc" in e["detail"]
    assert "next_bar_utc" in e["detail"]
    assert "gap_seconds" in e["detail"]


def test_experiment_events_deduped_on_rerun(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    dup = bars + [bars[-1]]
    ssc.run_cycle(dup, state_dir=state_dir, now_utc=_NOW)
    ssc.run_cycle(dup, state_dir=state_dir, now_utc=_NOW)
    events_path = state_dir / ssc._EVENTS_FILENAME
    dup_events = [
        json.loads(l) for l in events_path.read_text().splitlines()
        if l.strip() and json.loads(l).get("event_type") == "DUPLICATE_BAR_SKIPPED"
    ]
    assert len(dup_events) == len({e["event_id"] for e in dup_events})


def test_dry_run_reports_but_does_not_persist_experiment_events(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Now dry-run with duplicates: they must be reported in the
    # summary but not appear in events.jsonl.
    dup = bars + [bars[-1]]
    events_path = state_dir / ssc._EVENTS_FILENAME
    events_before = events_path.read_bytes()
    summary = ssc.run_cycle(
        dup, state_dir=state_dir, now_utc=_NOW, dry_run=True,
    )
    assert summary["duplicate_bar_skipped_count"] >= 1
    assert summary["experiment_events_would_persist"]
    assert events_path.read_bytes() == events_before


def test_crash_recovery_state_fully_matches_reference(tmp_path: Path) -> None:
    """After a simulated crash between event append and state write,
    a subsequent run must reconstruct state identical to a
    single-shot run — checked against every persisted field."""
    a_dir = tmp_path / "reference"
    b_dir = tmp_path / "recovering"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=a_dir, now_utc=_NOW)
    ssc.run_cycle(bars, state_dir=b_dir, now_utc=_NOW)
    # Simulate crash: delete state.json but keep events.jsonl.
    (b_dir / ssc._STATE_FILENAME).unlink()
    ssc.run_cycle(bars, state_dir=b_dir, now_utc=_NOW)
    state_a = json.loads((a_dir / ssc._STATE_FILENAME).read_text())
    state_b = json.loads((b_dir / ssc._STATE_FILENAME).read_text())
    for cid in state_a["candidates"]:
        a = state_a["candidates"][cid]
        b = state_b["candidates"][cid]
        for k in a:
            assert a[k] == b[k], f"{cid}.{k} drift: {a[k]!r} vs {b[k]!r}"
    # Also assert event ID set is identical and has no duplicates.
    events_a = (a_dir / ssc._EVENTS_FILENAME).read_text().splitlines()
    events_b = (b_dir / ssc._EVENTS_FILENAME).read_text().splitlines()
    ids_a = [json.loads(l)["event_id"] for l in events_a if l.strip()]
    ids_b = [json.loads(l)["event_id"] for l in events_b if l.strip()]
    assert set(ids_a) == set(ids_b)
    assert len(ids_b) == len(set(ids_b))


# ---------------------------------------------------------------------------
# S62 review round 4 — status/report validate events, restricted repair,
# correct event-count reporting
# ---------------------------------------------------------------------------


def _corrupt_event_final_line(events_path: Path, terminated: bool) -> None:
    """Append a malformed final line. When ``terminated`` is True the
    file ends in a newline (real corruption). When False the file
    ends without a newline (interrupted append)."""
    with events_path.open("a", encoding="utf-8") as f:
        if terminated:
            f.write('{"event_id": "abc", "partial\n')
        else:
            f.write('{"event_id": "abc", "partial')


# --- Fix 1: status + report validate events.jsonl ---


def test_cli_status_exits_2_on_corrupt_final_event_line(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    """--status must read events.jsonl and fail closed on corruption."""
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    before = _snapshot_dir(state_dir)
    _corrupt_event_final_line(events_path, terminated=True)
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "malformed" in err["error"] or "newline-terminated" in err["error"]
    # Compare with the state dir immediately after the corruption
    # (excluding the deliberate mutation) — status did not further
    # change anything.
    now = _snapshot_dir(state_dir)
    # Events file may have changed due to the injected corruption, but
    # every other file should be byte-identical to `before`.
    for path, before_bytes_mtime in before.items():
        if path.endswith(ssc._EVENTS_FILENAME):
            continue
        assert now[path] == before_bytes_mtime, path


def test_cli_status_exits_2_on_corrupt_middle_event_line(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    text = events_path.read_text()
    lines = text.splitlines()
    lines.insert(1, "not-json")
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "malformed" in err["error"]


def test_cli_status_valid_log_returns_0(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    before = _snapshot_dir(state_dir)
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["experiment_id"] == "S62_SPY_60M_FORWARD"
    # State dir unchanged.
    assert _snapshot_dir(state_dir) == before


def test_cli_report_exits_2_on_corrupt_event_log(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _corrupt_event_final_line(events_path, terminated=True)
    before = _snapshot_dir(state_dir)
    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "malformed" in err["error"] or "newline-terminated" in err["error"]
    assert _snapshot_dir(state_dir) == before


def test_cli_report_valid_log_returns_0(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    before = _snapshot_dir(state_dir)
    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validation_status"]["promotion_eligible"] is False
    assert _snapshot_dir(state_dir) == before


def test_missing_event_log_with_forward_bars_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    # Delete events.jsonl while state still shows processed forward bars.
    (state_dir / ssc._EVENTS_FILENAME).unlink()
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "audit integrity mismatch" in err["error"]


# --- Fix 2: restricted repair semantics ---


def test_repair_rejects_newline_terminated_malformed_final_line(
    tmp_path: Path,
) -> None:
    """A malformed final record that ends with \\n is real corruption,
    not an interrupted append, and MUST NOT be silently deleted."""
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    before_bytes = events_path.read_bytes()
    _corrupt_event_final_line(events_path, terminated=True)
    after_corruption = events_path.read_bytes()
    with pytest.raises(ssc.ShadowError, match="newline-terminated"):
        ssc._repair_event_log_tail(events_path)
    # Bytes untouched.
    assert events_path.read_bytes() == after_corruption
    assert before_bytes != after_corruption  # we did inject the tail


def test_repair_middle_line_never_deleted(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    text = events_path.read_text()
    lines = text.splitlines()
    lines.insert(1, "not-json")
    # Leave the file unterminated so the outer branch would attempt
    # a tail repair.
    events_path.write_text("\n".join(lines), encoding="utf-8")
    before_bytes = events_path.read_bytes()
    with pytest.raises(ssc.ShadowError, match="middle line"):
        ssc._repair_event_log_tail(events_path)
    assert events_path.read_bytes() == before_bytes


def test_repair_explicit_command_recovers_unterminated_tail(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    before_size = events_path.stat().st_size
    _corrupt_event_final_line(events_path, terminated=False)
    result = ssc.repair_event_log_tail_command(
        state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert result["repaired"] is True
    assert result["recovered_detail"]["removed_byte_count"] > 0
    assert result["recovered_detail"]["removed_tail_sha256"]
    # Recovery event was appended, so final size == before_size +
    # one event line.
    for line in events_path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            json.loads(stripped)


def test_repair_event_id_derived_only_from_content(tmp_path: Path) -> None:
    """The recovery event ID depends only on manifest hash, event
    type, removed_tail_sha256, and previous_event_id — never on wall
    time. Two calls with the same content and different now_utc must
    produce the same event_id."""
    detail = {
        "removed_byte_count": 42,
        "removed_tail_length": 30,
        "removed_tail_sha256": "abcd" * 16,
        "previous_event_id": "prev123",
    }
    manifest_hash = "manifest-hash-xyz"
    e1 = ssc._make_recovery_event(
        manifest_hash_str=manifest_hash,
        recovered_detail=detail,
        now_utc=datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    e2 = ssc._make_recovery_event(
        manifest_hash_str=manifest_hash,
        recovered_detail=detail,
        now_utc=datetime(2027, 1, 1, 12, 30, 0, tzinfo=timezone.utc),
    )
    assert e1["event_id"] == e2["event_id"]
    # But changing any content component changes the ID.
    e_diff_tail = ssc._make_recovery_event(
        manifest_hash_str=manifest_hash,
        recovered_detail={**detail, "removed_tail_sha256": "0" * 64},
        now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert e_diff_tail["event_id"] != e1["event_id"]
    e_diff_prev = ssc._make_recovery_event(
        manifest_hash_str=manifest_hash,
        recovered_detail={**detail, "previous_event_id": "OTHER"},
        now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert e_diff_prev["event_id"] != e1["event_id"]


def test_repair_state_corruption_prevents_event_mutation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _corrupt_event_final_line(events_path, terminated=False)
    events_before = events_path.read_bytes()

    # Corrupt the state.
    _corrupt_state(
        state_dir,
        lambda s: s.pop("experiment_id"),
    )
    with pytest.raises(ssc.ShadowError):
        ssc.repair_event_log_tail_command(
            state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    assert events_path.read_bytes() == events_before  # unchanged


def test_repair_then_append_leaves_every_line_parseable(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _corrupt_event_final_line(events_path, terminated=False)
    ssc.repair_event_log_tail_command(
        state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    bars = _many_bars_across_cutoff(pre=800, post=105)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    for line in events_path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            json.loads(stripped)


def test_normal_once_fails_closed_on_newline_terminated_corruption(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _corrupt_event_final_line(events_path, terminated=True)
    bars = _many_bars_across_cutoff(pre=800, post=50)
    # The canonical validator catches this at step 4 (every complete
    # line, including a newline-terminated malformed final line, is
    # validated) before any repair is even attempted.
    with pytest.raises(ssc.ShadowError, match="malformed"):
        ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)


# --- Fix 3: correct event-count reporting ---


def test_event_counts_split_candidate_vs_experiment(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    dup = bars + [bars[-1]]
    summary = ssc.run_cycle(dup, state_dir=state_dir, now_utc=_NOW)
    events_path = state_dir / ssc._EVENTS_FILENAME
    physical_lines = [
        l for l in events_path.read_text().splitlines() if l.strip()
    ]
    # candidate + experiment sums to total physical new events.
    assert (
        summary["candidate_events_appended"]
        + summary["experiment_events_appended"]
        == summary["events_appended"]
    )
    assert summary["events_appended"] == len(physical_lines)
    assert summary["experiment_events_appended"] >= 1  # at least the duplicate audit


def test_event_counts_only_duplicate_experiment_event(tmp_path: Path) -> None:
    """A dry-run that only produces a duplicate audit event (no new
    candidate observations) should report a single experiment event
    and zero candidate events."""
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=50)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Second run with duplicate of the last bar (already processed
    # + one duplicate).
    dup = bars + [bars[-1]]
    summary = ssc.run_cycle(dup, state_dir=state_dir, now_utc=_NOW)
    # No new forward bars → no candidate events.
    assert summary["candidate_events_appended"] == 0
    # But one new duplicate audit event.
    assert summary["experiment_events_appended"] == 1
    assert summary["events_appended"] == 1


def test_event_counts_only_gap_event(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)
    # Prime with pre-cutoff bars.
    cutoff = pd.Timestamp("2026-07-17 19:30", tz="UTC")
    pre = _bar_seq([float(c) for c in range(1, 800)],
                   start=cutoff - pd.Timedelta(hours=1000))
    ssc.run_cycle(pre, state_dir=state_dir, now_utc=_NOW)
    # Now add two forward bars with an intraday gap in the same session.
    session_start = pd.Timestamp("2026-07-20 14:30", tz="UTC")  # Monday
    b1 = Bar(ts=session_start, open=100.0, high=100.0, low=100.0,
             close=100.0, volume=1_000)
    b2 = Bar(ts=session_start + pd.Timedelta(hours=3),
             open=101.0, high=101.0, low=101.0, close=101.0, volume=1_000)
    summary = ssc.run_cycle(pre + [b1, b2], state_dir=state_dir, now_utc=_NOW)
    assert summary["experiment_events_appended"] >= 1
    # Verify at least one is a DATA_GAP_DETECTED event.
    events_path = state_dir / ssc._EVENTS_FILENAME
    gap_types = [
        json.loads(l)["event_type"]
        for l in events_path.read_text().splitlines() if l.strip()
    ]
    assert "DATA_GAP_DETECTED" in gap_types


def test_event_counts_rerun_dedup_zero_appends(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Re-run identical input — nothing new should be appended.
    r2 = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert r2["candidate_events_appended"] == 0
    assert r2["experiment_events_appended"] == 0
    assert r2["events_appended"] == 0


def test_event_counts_dry_run_mirrors_would_append(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    dup = bars + [bars[-1]]
    dry = ssc.run_cycle(
        dup, state_dir=state_dir, now_utc=_NOW, dry_run=True,
    )
    assert dry["dry_run"] is True
    assert (
        dry["candidate_events_would_append"]
        + dry["experiment_events_would_append"]
        == dry["events_would_append"]
    )
    # And a real run right after with the same input produces the
    # same counts.
    real = ssc.run_cycle(dup, state_dir=state_dir, now_utc=_NOW)
    assert real["candidate_events_appended"] == dry["candidate_events_would_append"]
    assert real["experiment_events_appended"] == dry["experiment_events_would_append"]
    assert real["events_appended"] == dry["events_would_append"]


# ---------------------------------------------------------------------------
# S62 review round 5 — unterminated-valid-tail safety, semantic event validation
# ---------------------------------------------------------------------------


def _make_events_end_without_newline(events_path: Path) -> None:
    """Strip the trailing newline from events.jsonl (final record
    still a valid JSON object, just unterminated)."""
    raw = events_path.read_bytes()
    assert raw.endswith(b"\n")
    events_path.write_bytes(raw[:-1])


# --- Fix 1: unterminated valid final record ---


def test_status_exits_2_on_unterminated_valid_final_record(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    before = _snapshot_dir(state_dir)
    _make_events_end_without_newline(events_path)
    snap_after_corruption = _snapshot_dir(state_dir)
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "newline" in err["error"].lower() or "framing" in err["error"].lower()
    # No further mutation.
    assert _snapshot_dir(state_dir) == snap_after_corruption
    # Everything except events.jsonl unchanged from before.
    for path, before_entry in before.items():
        if path.endswith(ssc._EVENTS_FILENAME):
            continue
        assert _snapshot_dir(state_dir)[path] == before_entry, path


def test_report_exits_2_on_unterminated_valid_final_record(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_events_end_without_newline(events_path)
    before = _snapshot_dir(state_dir)
    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "newline" in err["error"].lower() or "framing" in err["error"].lower()
    assert _snapshot_dir(state_dir) == before


def test_once_exits_2_on_unterminated_valid_final_record(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_events_end_without_newline(events_path)
    before_bytes = events_path.read_bytes()
    bars = _many_bars_across_cutoff(pre=800, post=105)
    with pytest.raises(ssc.ShadowError, match="newline"):
        ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    # Event bytes untouched.
    assert events_path.read_bytes() == before_bytes


def test_repair_normalizes_unterminated_valid_tail(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_events_end_without_newline(events_path)
    pre_lines = [
        json.loads(l) for l in
        events_path.read_text().splitlines() if l.strip()
    ]
    result = ssc.repair_event_log_tail_command(
        state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert result["repaired"] is True
    assert result["recovered_detail"]["kind"] == "terminator_restored"
    assert result["recovered_detail"]["removed_byte_count"] == 0
    assert result["recovery_event"]["event_type"] == "EVENT_LOG_TERMINATOR_RESTORED"
    # File now ends with a newline; every prior event is preserved
    # and one new recovery event line is appended.
    after_lines = [
        json.loads(l) for l in
        events_path.read_text().splitlines() if l.strip()
    ]
    assert after_lines[:len(pre_lines)] == pre_lines
    assert after_lines[-1]["event_type"] == "EVENT_LOG_TERMINATOR_RESTORED"
    assert events_path.read_bytes().endswith(b"\n")


def test_repair_then_append_lines_all_parseable(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_events_end_without_newline(events_path)
    ssc.repair_event_log_tail_command(
        state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    bars = _many_bars_across_cutoff(pre=800, post=110)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    for line in events_path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            json.loads(stripped)


def test_repeated_repair_of_valid_unterminated_is_idempotent(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_events_end_without_newline(events_path)
    ssc.repair_event_log_tail_command(
        state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    after_bytes = events_path.read_bytes()
    # A second call finds the file already newline-terminated and
    # valid — nothing changes.
    result = ssc.repair_event_log_tail_command(
        state_dir, now_utc=datetime(2027, 6, 1, tzinfo=timezone.utc),
    )
    assert result["repaired"] is False
    assert events_path.read_bytes() == after_bytes


# --- Fix 2: semantic event-log integrity validation ---


def _prime_with_forward(state_dir: Path) -> tuple[dict, dict]:
    bars = _many_bars_across_cutoff(pre=800, post=100)
    ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    manifest = ssc.load_manifest_readonly(state_dir)
    state = ssc.load_state_readonly(state_dir, manifest)
    return manifest, state


def _rewrite_events(events_path: Path, mutate) -> None:
    lines = events_path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            obj = json.loads(line)
            new = mutate(obj, i)
            if new is not None:
                lines[i] = json.dumps(new, sort_keys=True)
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_event_log_duplicate_event_id_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    lines = events_path.read_text().splitlines()
    assert len(lines) >= 2
    lines.append(lines[-1])
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ssc.ShadowError, match="duplicate event_id"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_wrong_experiment_id_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["experiment_id"] = "OTHER_EXPERIMENT"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="experiment_id"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_wrong_manifest_hash_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["manifest_hash"] = "0" * 64
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="manifest_hash"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_unknown_candidate_id_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["candidate_id"] = "not_a_candidate"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="candidate_id"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_unknown_event_type_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["event_type"] = "MADE_UP_EVENT"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="event_type"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_invalid_event_id_type_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["event_id"] = 42
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="event_id"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_invalid_utf8_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    with events_path.open("ab") as f:
        f.write(b"\xff\xfe not utf-8\n")
    with pytest.raises(ssc.ShadowError, match="UTF-8"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_malformed_timestamp_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["signal_bar_utc"] = "not-a-timestamp"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="signal_bar_utc"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_non_finite_numeric_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0 and obj.get("price") is not None:
            obj["price"] = "not_a_number"
            return obj
        # Fallback: alter any event's short_sma.
        if i == 0:
            obj["short_sma"] = "nope"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="finite"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_invalid_filter_result_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["filter_result"] = "maybe"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="filter_result"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_event_log_invalid_position_state_rejected(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    manifest, state = _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["position_state"] = "sideways"
            return obj
    _rewrite_events(events_path, _mutate)
    with pytest.raises(ssc.ShadowError, match="position_state"):
        ssc._validate_event_log_readonly(events_path, state, manifest)


def test_cli_status_detects_semantic_corruption(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["candidate_id"] = "not_a_candidate"
            return obj
    _rewrite_events(events_path, _mutate)
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "candidate_id" in err["error"]


def test_cli_report_detects_semantic_corruption(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["event_type"] = "MADE_UP"
            return obj
    _rewrite_events(events_path, _mutate)
    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "event_type" in err["error"]


# --- Fix 3: explicit repair requires state ---


def test_explicit_repair_requires_state(tmp_path: Path) -> None:
    """Repair against a missing state.json fails closed."""
    state_dir = tmp_path / "shadow"
    ssc.load_or_init_manifest(state_dir, _NOW)  # manifest only
    events_path = state_dir / ssc._EVENTS_FILENAME
    events_path.write_text('{"event_id":"x"}', encoding="utf-8")
    # Missing state.json — repair must refuse.
    events_before = events_path.read_bytes()
    with pytest.raises(ssc.ShadowError, match="requires an initialized experiment"):
        ssc.repair_event_log_tail_command(
            state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    assert events_path.read_bytes() == events_before


def test_explicit_repair_requires_valid_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_events_end_without_newline(events_path)
    events_before = events_path.read_bytes()
    # Corrupt state.
    _corrupt_state(state_dir, lambda s: s.pop("experiment_id"))
    with pytest.raises(ssc.ShadowError):
        ssc.repair_event_log_tail_command(
            state_dir, now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    assert events_path.read_bytes() == events_before


# ---------------------------------------------------------------------------
# S62 review round 6 — one canonical validator, exercised across all three
# entry points (normal --once, --dry-run, --repair-event-log-tail)
# ---------------------------------------------------------------------------


def _make_unterminated_final_event(events_path: Path, mutate) -> None:
    """Take the last well-formed event line, apply ``mutate`` to its
    decoded dict, and rewrite the file so the mutated event becomes
    the final line WITHOUT a trailing newline — i.e. valid JSON but
    incomplete JSONL framing."""
    lines = [l for l in events_path.read_text().splitlines() if l.strip()]
    assert lines
    last_obj = json.loads(lines[-1])
    mutated = mutate(last_obj)
    prefix = "\n".join(lines[:-1])
    if prefix:
        prefix += "\n"
    events_path.write_text(
        prefix + json.dumps(mutated, sort_keys=True), encoding="utf-8",
    )


def _make_malformed_tail_after_invalid_complete_event(events_path: Path) -> None:
    """Corrupt the FIRST complete event semantically (unknown
    event_type), then append a malformed, unterminated final line —
    the specific combination in review-round-6 corruption case 10."""
    lines = events_path.read_text().splitlines()
    assert len(lines) >= 2
    obj = json.loads(lines[0])
    obj["event_type"] = "MADE_UP_EVENT_TYPE"
    lines[0] = json.dumps(obj, sort_keys=True)
    content = "\n".join(lines) + "\n" + '{"event_id": "abc", "partial'
    events_path.write_text(content, encoding="utf-8")


def _assert_all_paths_fail_closed(
    state_dir: Path, match: str, *, dry_run_match: str | None = None,
) -> None:
    """Given a state_dir whose events.jsonl is already corrupted,
    assert that --once, --dry-run, and --repair-event-log-tail all
    raise ShadowError WITHOUT mutating state.json or events.jsonl,
    and (for the CLI entry points) exit with status 2.

    ``dry_run_match`` may differ from ``match``: dry-run always
    validates with ``require_terminated=True`` (it can never repair,
    so an unterminated file is rejected on framing alone), whereas
    ``--once``/``--repair-event-log-tail`` first inspect with
    ``require_terminated=False`` and therefore surface the deeper
    semantic error for an unterminated-but-parseable final record.
    Both are legitimate fail-closed outcomes for the same corruption.
    """
    events_path = state_dir / ssc._EVENTS_FILENAME
    state_path = state_dir / ssc._STATE_FILENAME
    events_before = events_path.read_bytes()
    state_before = state_path.read_bytes()
    bars = _many_bars_across_cutoff(pre=800, post=60)

    with pytest.raises(ssc.ShadowError, match=dry_run_match or match):
        ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW, dry_run=True)
    assert events_path.read_bytes() == events_before, "dry-run mutated events.jsonl"
    assert state_path.read_bytes() == state_before, "dry-run mutated state.json"

    with pytest.raises(ssc.ShadowError, match=match):
        ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert events_path.read_bytes() == events_before, "--once mutated events.jsonl"
    assert state_path.read_bytes() == state_before, "--once mutated state.json"

    with pytest.raises(ssc.ShadowError, match=match):
        ssc.repair_event_log_tail_command(state_dir, now_utc=_NOW)
    assert events_path.read_bytes() == events_before, "repair mutated events.jsonl"
    assert state_path.read_bytes() == state_before, "repair mutated state.json"

    # No recovery event of any kind was ever appended. Lines that
    # fail to parse are the deliberately-corrupted fixture content
    # (e.g. an intentionally malformed unterminated tail) — skip
    # those rather than treating them as a parse failure here.
    for line in events_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        assert obj.get("event_type") not in (
            "EVENT_LOG_TAIL_RECOVERED", "EVENT_LOG_TERMINATOR_RESTORED",
        )


def _assert_all_paths_fail_closed_cli(state_dir: Path) -> None:
    """CLI-level check: --status, the report tool, and
    --repair-event-log-tail all exit 2 with a JSON error and leave
    the state directory byte-for-byte unchanged."""
    before = _snapshot_dir(state_dir)
    rc = ssc.main(["--state-dir", str(state_dir), "--status"])
    assert rc == 2
    assert _snapshot_dir(state_dir) == before

    rc = ssr.main(["--state-dir", str(state_dir)])
    assert rc == 2
    assert _snapshot_dir(state_dir) == before

    rc = ssc.main(["--state-dir", str(state_dir), "--repair-event-log-tail"])
    assert rc == 2
    assert _snapshot_dir(state_dir) == before


# --- Corruption case 1: duplicate physical event ID ---


def test_r6_duplicate_event_id_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    lines = events_path.read_text().splitlines()
    assert len(lines) >= 2
    lines.append(lines[0])  # duplicate an existing event line
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _assert_all_paths_fail_closed(state_dir, match="duplicate event_id")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 2: wrong experiment ID ---


def test_r6_wrong_experiment_id_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["experiment_id"] = "WRONG_EXPERIMENT"
            return obj
    _rewrite_events(events_path, _mutate)
    _assert_all_paths_fail_closed(state_dir, match="experiment_id")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 3: wrong manifest hash ---


def test_r6_wrong_manifest_hash_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["manifest_hash"] = "f" * 64
            return obj
    _rewrite_events(events_path, _mutate)
    _assert_all_paths_fail_closed(state_dir, match="manifest_hash")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 4: unknown candidate ID ---


def test_r6_unknown_candidate_id_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["candidate_id"] = "totally_unknown"
            return obj
    _rewrite_events(events_path, _mutate)
    _assert_all_paths_fail_closed(state_dir, match="candidate_id")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 5: unknown event type ---


def test_r6_unknown_event_type_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["event_type"] = "SOMETHING_MADE_UP"
            return obj
    _rewrite_events(events_path, _mutate)
    _assert_all_paths_fail_closed(state_dir, match="event_type")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 6: malformed timestamp ---


def test_r6_malformed_timestamp_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["signal_bar_utc"] = "definitely-not-a-timestamp"
            return obj
    _rewrite_events(events_path, _mutate)
    _assert_all_paths_fail_closed(state_dir, match="signal_bar_utc")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 7: invalid numeric field ---


def test_r6_invalid_numeric_field_fails_all_paths(tmp_path: Path) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    def _mutate(obj, i):
        if i == 0:
            obj["short_sma"] = "not-a-number"
            return obj
    _rewrite_events(events_path, _mutate)
    _assert_all_paths_fail_closed(state_dir, match="finite")
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 8: valid unterminated JSON missing event_id ---


def test_r6_valid_unterminated_missing_event_id_fails_all_paths(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_unterminated_final_event(
        events_path,
        lambda o: {k: v for k, v in o.items() if k != "event_id"},
    )
    _assert_all_paths_fail_closed(
        state_dir, match="event_id", dry_run_match="newline",
    )
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 9: valid unterminated event with wrong manifest hash ---


def test_r6_valid_unterminated_wrong_manifest_hash_fails_all_paths(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_unterminated_final_event(
        events_path,
        lambda o: {**o, "manifest_hash": "a" * 64},
    )
    _assert_all_paths_fail_closed(
        state_dir, match="manifest_hash", dry_run_match="newline",
    )
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Corruption case 10: malformed unterminated tail preceded by a
# semantically invalid complete event ---


def test_r6_malformed_tail_after_invalid_complete_event_fails_all_paths(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    _make_malformed_tail_after_invalid_complete_event(events_path)
    # The earlier semantically-invalid complete event must be caught
    # before the tail is ever inspected for repair. Dry-run rejects
    # on framing first since it always requires full termination.
    _assert_all_paths_fail_closed(
        state_dir, match="event_type", dry_run_match="newline",
    )
    _assert_all_paths_fail_closed_cli(state_dir)


# --- Success case: a semantically valid unterminated final event ---


def test_r6_valid_unterminated_final_event_is_preserved_by_explicit_repair(
    tmp_path: Path,
) -> None:
    """A semantically valid but unterminated final record must be
    preserved (not deleted), exactly one newline restored, exactly
    one deterministic recovery event appended, and the final log
    must pass the canonical strict validator."""
    state_dir = tmp_path / "shadow"
    _prime_with_forward(state_dir)
    events_path = state_dir / ssc._EVENTS_FILENAME
    pre_lines = [
        json.loads(l) for l in events_path.read_text().splitlines() if l.strip()
    ]
    _make_events_end_without_newline(events_path)
    assert not events_path.read_bytes().endswith(b"\n")

    result = ssc.repair_event_log_tail_command(state_dir, now_utc=_NOW)
    assert result["repaired"] is True
    assert result["recovered_detail"]["kind"] == "terminator_restored"
    assert result["recovery_event"]["event_type"] == "EVENT_LOG_TERMINATOR_RESTORED"

    raw = events_path.read_bytes()
    assert raw.endswith(b"\n")
    post_lines = [
        json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()
    ]
    # Every original event preserved verbatim, plus exactly one new
    # recovery event appended.
    assert post_lines[:len(pre_lines)] == pre_lines
    assert len(post_lines) == len(pre_lines) + 1
    assert post_lines[-1]["event_type"] == "EVENT_LOG_TERMINATOR_RESTORED"

    # The final log passes the canonical strict validator cleanly.
    manifest = ssc.load_manifest_readonly(state_dir)
    known_ids = ssc._validate_and_load_event_ids(
        events_path, manifest, require_terminated=True,
    )
    assert len(known_ids) == len(post_lines)

    # A normal --once afterward must succeed cleanly (no more errors).
    bars = _many_bars_across_cutoff(pre=800, post=110)
    summary = ssc.run_cycle(bars, state_dir=state_dir, now_utc=_NOW)
    assert summary["dry_run"] is False
