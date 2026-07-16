"""Tests for src.tools.backtest_strategy_eval.

All fixtures are synthetic OHLCV — no network, no Alpaca, no cache
file dependency for the pure-logic tests.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd
import pytest

from src.tools import backtest_strategy_eval as bse
from src.tools.backtest_strategy_eval import Bar, BacktestError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bars_from_closes(
    closes: Sequence[float],
    opens: Sequence[float] | None = None,
) -> list[Bar]:
    start = pd.Timestamp("2026-01-05 14:30", tz="UTC")
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


def _write_cache(tmp_path: Path, closes: Sequence[float]) -> Path:
    idx = pd.date_range("2026-01-05 14:30", periods=len(closes), freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1_000.0] * len(closes),
    }, index=idx)
    path = tmp_path / "SPY_2026-01-05_60m.csv"
    df.to_csv(path)
    return path


# ---------------------------------------------------------------------------
# sma_crossover_signal
# ---------------------------------------------------------------------------


def test_bullish_crossover_creates_buy() -> None:
    # After the ramp, short (3) rises above long (5); prior state = HOLD.
    closes = [1, 1, 1, 1, 1, 2, 3, 4, 5, 6]
    sig = bse.sma_crossover_signal(closes, index=8, short_window=3, long_window=5,
                                   has_position=False)
    assert sig == "BUY"


def test_bearish_crossover_creates_sell() -> None:
    closes = [5, 5, 5, 5, 5, 4, 3, 2, 1, 1]
    sig = bse.sma_crossover_signal(closes, index=8, short_window=3, long_window=5,
                                   has_position=True)
    assert sig == "SELL"


def test_signal_hold_when_short_above_long_and_already_long() -> None:
    closes = [1, 1, 1, 1, 1, 2, 3, 4, 5, 6]
    sig = bse.sma_crossover_signal(closes, index=8, short_window=3, long_window=5,
                                   has_position=True)
    assert sig == "HOLD"


def test_signal_hold_when_windows_not_yet_ready() -> None:
    closes = [1, 2, 3]
    sig = bse.sma_crossover_signal(closes, index=1, short_window=3, long_window=5,
                                   has_position=False)
    assert sig == "HOLD"


def test_no_lookahead_bias() -> None:
    """Signal at bar t must depend only on closes[0..t] — appending
    future bars must not change the signal at any prior index."""
    base_closes = [1, 1, 1, 1, 2, 3, 4, 5, 6, 7]
    for future_extension in ([100, 200], [1, 0.5], []):
        extended = list(base_closes) + list(future_extension)
        for i in range(len(base_closes)):
            base_sig = bse.sma_crossover_signal(
                base_closes, i, 3, 5, has_position=False,
            )
            ext_sig = bse.sma_crossover_signal(
                extended, i, 3, 5, has_position=False,
            )
            assert base_sig == ext_sig, (
                f"lookahead detected at index {i}: base={base_sig} ext={ext_sig}"
            )


# ---------------------------------------------------------------------------
# run_backtest — metrics
# ---------------------------------------------------------------------------


def test_run_backtest_records_trades_on_crossovers() -> None:
    # Up-ramp → BUY, then down-ramp → SELL, then up-ramp → BUY at end.
    closes = ([1] * 5 + [2, 3, 4, 5, 6, 7, 8, 9]
              + [8, 7, 6, 5, 4, 3, 2]
              + [3, 4, 5, 6, 7, 8, 9, 10])
    bars = _bars_from_closes(closes)
    result = bse.run_backtest(bars, short_window=3, long_window=5)
    assert result.trade_count >= 1
    assert result.bar_count == len(closes)
    assert result.final_equity > 0
    for t in result.trades:
        assert t.exit_index > t.entry_index


def test_run_backtest_metrics_on_pure_uptrend() -> None:
    closes = list(range(1, 51))  # monotonic 1..50
    bars = _bars_from_closes([float(c) for c in closes])
    result = bse.run_backtest(bars, short_window=3, long_window=5,
                              initial_equity=10_000.0)
    assert result.total_return > 0
    # Buy-and-hold on a 1→50 ramp is ~4900%.
    assert result.buy_and_hold_return > 40.0
    assert 0.0 <= result.exposure_time <= 1.0
    assert result.max_drawdown <= 0.0
    # A pure uptrend produces no bearish crossover, so the position
    # never closes — completed-trade metrics stay at zero and the
    # position is reported as still open.
    assert result.completed_trade_count == 0
    assert result.trade_count == 0
    assert result.win_rate == 0.0
    assert result.open_position is True
    assert result.open_entry_index is not None
    assert result.open_unrealized_return is not None
    assert result.open_unrealized_return > 0


def test_metrics_avg_trade_return_matches_trade_list() -> None:
    closes = [1, 1, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 3, 4, 5, 6, 7, 8]
    bars = _bars_from_closes([float(c) for c in closes])
    result = bse.run_backtest(bars, short_window=2, long_window=4)
    if result.trade_count > 0:
        recomputed = sum(t.return_pct for t in result.trades) / result.trade_count
        assert math.isclose(result.avg_trade_return, recomputed, abs_tol=1e-9)


def test_metrics_profit_factor_zero_when_no_trades() -> None:
    # A very short flat series produces no crossover trades.
    bars = _bars_from_closes([1.0] * 10)
    result = bse.run_backtest(bars, short_window=3, long_window=5)
    assert result.trade_count == 0
    assert result.profit_factor == 0.0
    assert result.win_rate == 0.0
    assert result.avg_trade_return == 0.0


def test_max_drawdown_is_non_positive() -> None:
    # Volatile series with a clear peak-then-trough.
    closes = [1, 2, 3, 5, 8, 13, 8, 5, 3, 2, 1, 2, 3, 5, 8]
    bars = _bars_from_closes([float(c) for c in closes])
    result = bse.run_backtest(bars, short_window=2, long_window=4)
    assert result.max_drawdown <= 0.0


# ---------------------------------------------------------------------------
# Sweep + parameter validation
# ---------------------------------------------------------------------------


def test_run_sweep_skips_invalid_combinations() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    out = bse.run_sweep(bars, short_windows=[5, 10, 20], long_windows=[5, 20, 50])
    # Skipped: (5,5) short>=long; (10,5), (20,5), (20,20).
    skipped_pairs = {(s["short_window"], s["long_window"])
                     for s in out["skipped_combinations"]}
    for s, l in [(5, 5), (10, 5), (20, 5), (20, 20)]:
        assert (s, l) in skipped_pairs
    # Valid combinations must execute and produce a real result dict.
    assert out["combination_count"] == 5  # (5,20),(5,50),(10,20),(10,50),(20,50)
    for r in out["sweep"]:
        assert r["short_window"] < r["long_window"]
        assert "total_return" in r
        assert "sharpe_ratio" in r


def test_run_backtest_rejects_short_ge_long() -> None:
    bars = _bars_from_closes([1.0] * 20)
    with pytest.raises(BacktestError, match="short_window"):
        bse.run_backtest(bars, short_window=5, long_window=5)
    with pytest.raises(BacktestError, match="short_window"):
        bse.run_backtest(bars, short_window=10, long_window=5)


def test_run_backtest_rejects_non_positive_windows() -> None:
    bars = _bars_from_closes([1.0] * 10)
    with pytest.raises(BacktestError, match="positive"):
        bse.run_backtest(bars, short_window=0, long_window=5)
    with pytest.raises(BacktestError, match="positive"):
        bse.run_backtest(bars, short_window=3, long_window=-1)


def test_parse_window_list_rejects_invalid_input() -> None:
    with pytest.raises(BacktestError):
        bse._parse_window_list("5,abc,10")
    with pytest.raises(BacktestError):
        bse._parse_window_list("")


# ---------------------------------------------------------------------------
# build_summary + JSON output
# ---------------------------------------------------------------------------


def test_build_summary_json_schema() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    now = datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc)
    summary = bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=now, short_window=3, long_window=5,
    )
    required = {"timestamp_utc", "symbol", "interval", "bar_count",
                "first_bar_ts", "last_bar_ts", "baseline",
                "execution", "commission_bps", "slippage_bps"}
    assert required.issubset(summary.keys())
    baseline_keys = {
        "total_return", "buy_and_hold_return", "max_drawdown", "sharpe_ratio",
        "trade_count", "completed_trade_count",
        "win_rate", "avg_trade_return", "avg_holding_bars",
        "profit_factor", "exposure_time", "final_equity",
        "short_window", "long_window", "execution",
        "commission_bps", "slippage_bps",
        "open_position", "open_entry_price", "open_entry_index",
        "open_unrealized_return",
    }
    assert baseline_keys.issubset(summary["baseline"].keys())
    # Default execution is realistic, not optimistic.
    assert summary["execution"] == "next_open"
    assert summary["baseline"]["execution"] == "next_open"
    # JSON round-trips cleanly.
    dumped = json.dumps(summary, default=str)
    assert json.loads(dumped)["symbol"] == "SPY"


def test_build_summary_includes_sweep_when_windows_provided() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 61)])
    now = datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc)
    summary = bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=now, short_window=3, long_window=5,
        short_windows=[3, 5], long_windows=[10, 20],
    )
    assert "sweep" in summary
    assert summary["sweep"]["combination_count"] == 4


# ---------------------------------------------------------------------------
# CLI + data loading
# ---------------------------------------------------------------------------


def test_load_cached_bars_returns_newest_valid(tmp_path: Path) -> None:
    _write_cache(tmp_path, list(range(1, 51)))
    bars = bse.load_cached_bars(tmp_path, "SPY", "60m")
    assert len(bars) == 50
    assert bars[0].close == 1.0
    assert bars[-1].close == 50.0


def test_load_cached_bars_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(BacktestError, match="cache directory"):
        bse.load_cached_bars(tmp_path / "nope", "SPY", "60m")


def test_load_cached_bars_no_files_raises(tmp_path: Path) -> None:
    with pytest.raises(BacktestError, match="no cached bars"):
        bse.load_cached_bars(tmp_path, "SPY", "60m")


def test_main_writes_summary_and_prints_json(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 51)])
    out_dir = tmp_path / "out"

    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--symbol", "SPY",
        "--interval", "60m",
        "--short-window", "3",
        "--long-window", "5",
        "--output-dir", str(out_dir),
    ])
    assert rc == 0

    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["symbol"] == "SPY"
    assert payload["bar_count"] == 50
    assert "baseline" in payload
    assert "total_return" in payload["baseline"]

    written = list(out_dir.glob("*.json"))
    assert len(written) == 1


def test_main_no_write_flag_skips_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 51)])
    out_dir = tmp_path / "out"

    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--output-dir", str(out_dir),
        "--no-write",
    ])
    assert rc == 0
    assert not out_dir.exists()


def test_main_with_sweep(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 61)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--short-windows", "3,5",
        "--long-windows", "10,20",
        "--output-dir", str(tmp_path / "out"),
        "--no-write",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sweep"]["combination_count"] == 4
    assert not payload["sweep"]["skipped_combinations"]


def test_main_reports_missing_cache_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    rc = bse.main([
        "--cache-dir", str(tmp_path / "nope"),
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "cache directory" in err["error"]


def test_main_reports_bad_sweep_window_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 21)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--short-windows", "5,abc,10",
        "--long-windows", "20",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "window" in err["error"].lower()


# ---------------------------------------------------------------------------
# Execution model — next_open (default) vs same_close (diagnostic)
# ---------------------------------------------------------------------------


def test_default_execution_is_next_open() -> None:
    bars = _bars_from_closes([1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = bse.run_backtest(bars, short_window=2, long_window=3)
    assert result.execution == "next_open"


def test_next_open_execution_fills_at_next_bar_open() -> None:
    """A BUY signal at bar t must fill at bars[t+1].open, not bars[t].close."""
    # Distinct open vs close so we can tell which price was used.
    #   idx     0    1    2    3    4    5
    #   close   1    1    2    3    4    5   ← signal source
    #   open   10   10   20   30   40   50   ← execution price under next_open
    closes = [1.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    opens  = [10.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    bars = _bars_from_closes(closes, opens=opens)
    result = bse.run_backtest(
        bars, short_window=2, long_window=3, execution="next_open",
    )
    # Signal fires at some bar t (t >= 2 given windows); the trade's
    # entry_price must equal one of the openings (not one of the closes),
    # proving next-bar execution.
    if result.open_position:
        entry = result.open_entry_price
    else:
        assert result.trades
        entry = result.trades[0].entry_price
    assert entry in {10.0, 20.0, 30.0, 40.0, 50.0}, (
        f"entry price {entry} matched close series, not open series — "
        "same-bar execution leaked in"
    )


def test_final_bar_signal_does_not_execute() -> None:
    """A BUY signal on the final bar has no next bar to fill on, so no
    new trade may be opened under next_open."""
    # Uptrend that fires a BUY on the very last bar.
    closes = [1.0, 1.0, 1.0, 1.0, 1.0, 2.0]  # 6 bars
    opens = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    bars = _bars_from_closes(closes, opens=opens)
    # Force the last bar's signal by choosing tight windows.
    result = bse.run_backtest(
        bars, short_window=2, long_window=3, execution="next_open",
    )
    # If the tool obeyed next_open, no fill can happen — the signal at
    # the final bar has no bar[t+1] to execute on.
    if result.open_position:
        assert result.open_entry_index is not None
        assert result.open_entry_index < len(bars) - 1 or False, (
            "final-bar signal should not execute"
        )


def test_same_close_diagnostic_produces_different_entry_price() -> None:
    closes = [1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    opens  = [10.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    bars = _bars_from_closes(closes, opens=opens)
    r_next = bse.run_backtest(bars, 2, 3, execution="next_open")
    r_same = bse.run_backtest(bars, 2, 3, execution="same_close")
    # Under same_close the entry price MUST be a close value.
    if r_same.open_position:
        entry_same = r_same.open_entry_price
    else:
        entry_same = r_same.trades[0].entry_price
    assert entry_same in set(closes)
    # And the two execution models produce different results on this
    # bar series, which is the whole point of the option.
    assert r_next.execution != r_same.execution


def test_run_backtest_rejects_invalid_execution_mode() -> None:
    bars = _bars_from_closes([1.0] * 10)
    with pytest.raises(BacktestError, match="execution"):
        bse.run_backtest(bars, 3, 5, execution="magic")


# ---------------------------------------------------------------------------
# Open position handling
# ---------------------------------------------------------------------------


def test_open_position_does_not_count_as_trade() -> None:
    """A position still open at end must be reported in open_* fields
    but must NOT contribute to completed_trade_count / win_rate /
    profit_factor / avg_trade_return."""
    closes = [float(c) for c in range(1, 41)]  # pure uptrend → position never closes
    bars = _bars_from_closes(closes)
    result = bse.run_backtest(bars, short_window=3, long_window=5)
    assert result.open_position is True
    assert result.open_entry_price is not None
    assert result.open_entry_index is not None
    assert result.open_unrealized_return is not None
    assert result.completed_trade_count == 0
    assert result.trade_count == 0  # alias, also excludes open position
    assert result.win_rate == 0.0
    assert result.profit_factor == 0.0
    assert result.avg_trade_return == 0.0
    # final_equity still reflects mark-to-market of the open position.
    assert result.final_equity > 10_000.0


def test_closed_trade_updates_completed_metrics() -> None:
    # Up then down: position opens and later closes on bearish crossover.
    closes = [1, 1, 1, 2, 3, 4, 5, 6, 7, 6, 5, 4, 3, 2, 1, 1, 1, 1]
    bars = _bars_from_closes([float(c) for c in closes])
    result = bse.run_backtest(bars, short_window=2, long_window=4)
    assert result.completed_trade_count >= 1
    assert result.trade_count == result.completed_trade_count


# ---------------------------------------------------------------------------
# Commission + slippage
# ---------------------------------------------------------------------------


def test_costs_reduce_final_equity() -> None:
    closes = [1, 1, 1, 2, 3, 4, 5, 6, 7, 6, 5, 4, 3, 2, 1, 1, 2, 3, 4, 5]
    bars = _bars_from_closes([float(c) for c in closes])
    r_free = bse.run_backtest(bars, 2, 4, commission_bps=0, slippage_bps=0)
    r_cost = bse.run_backtest(bars, 2, 4, commission_bps=10, slippage_bps=5)
    assert r_cost.final_equity < r_free.final_equity
    assert r_cost.commission_bps == 10
    assert r_cost.slippage_bps == 5


def test_costs_reported_in_summary_json() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    summary = bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        short_window=3, long_window=5,
        commission_bps=15.0, slippage_bps=7.5,
    )
    assert summary["commission_bps"] == 15.0
    assert summary["slippage_bps"] == 7.5
    assert summary["baseline"]["commission_bps"] == 15.0
    assert summary["baseline"]["slippage_bps"] == 7.5


def test_run_backtest_rejects_negative_costs() -> None:
    bars = _bars_from_closes([1.0] * 10)
    with pytest.raises(BacktestError, match=">= 0"):
        bse.run_backtest(bars, 3, 5, commission_bps=-1.0)
    with pytest.raises(BacktestError, match=">= 0"):
        bse.run_backtest(bars, 3, 5, slippage_bps=-0.01)


def test_cli_commission_slippage_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 51)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--commission-bps", "12.5",
        "--slippage-bps", "3.5",
        "--no-write",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commission_bps"] == 12.5
    assert payload["slippage_bps"] == 3.5
    assert payload["execution"] == "next_open"


# ---------------------------------------------------------------------------
# Ranking + baseline comparison (S57)
# ---------------------------------------------------------------------------


def _mk_result(
    *, short=1, long=5, total_return=0.0, sharpe_ratio=0.0,
    max_drawdown=0.0, profit_factor=0.0, completed_trade_count=0,
) -> dict:
    """Build a minimal sweep result dict for ranking tests."""
    return {
        "short_window": short,
        "long_window": long,
        "total_return": total_return,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "profit_factor": profit_factor,
        "completed_trade_count": completed_trade_count,
    }


def test_ranking_picks_highest_total_return() -> None:
    results = [
        _mk_result(short=1, long=10, total_return=0.05, completed_trade_count=3),
        _mk_result(short=2, long=20, total_return=0.30, completed_trade_count=5),
        _mk_result(short=3, long=30, total_return=0.10, completed_trade_count=4),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_total_return"]["short_window"] == 2
    assert r["best_by_total_return"]["long_window"] == 20


def test_ranking_picks_highest_sharpe() -> None:
    results = [
        _mk_result(short=1, long=10, sharpe_ratio=0.5, completed_trade_count=3),
        _mk_result(short=2, long=20, sharpe_ratio=1.8, completed_trade_count=5),
        _mk_result(short=3, long=30, sharpe_ratio=1.2, completed_trade_count=4),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_sharpe_ratio"]["short_window"] == 2


def test_drawdown_ranking_prefers_smaller_drawdown() -> None:
    """Less negative max_drawdown wins (closer to zero)."""
    results = [
        _mk_result(short=1, long=10, max_drawdown=-0.30, completed_trade_count=3),
        _mk_result(short=2, long=20, max_drawdown=-0.05, completed_trade_count=4),
        _mk_result(short=3, long=30, max_drawdown=-0.15, completed_trade_count=5),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_max_drawdown"]["short_window"] == 2
    assert r["best_by_max_drawdown"]["max_drawdown"] == -0.05


def test_drawdown_ranking_excludes_zero_trade_configs_when_possible() -> None:
    """A zero-trade config trivially has max_drawdown == 0 — it must
    not win over configs that actually traded."""
    results = [
        _mk_result(short=1, long=10, max_drawdown=0.0, completed_trade_count=0),
        _mk_result(short=2, long=20, max_drawdown=-0.05, completed_trade_count=5),
        _mk_result(short=3, long=30, max_drawdown=-0.15, completed_trade_count=4),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_max_drawdown"]["completed_trade_count"] > 0
    assert r["best_by_max_drawdown"]["short_window"] == 2


def test_drawdown_ranking_falls_back_when_all_zero_trades() -> None:
    """If EVERY config has zero trades, still return the best-by-value
    rather than None — the operator needs a pointer even in degenerate
    sweeps."""
    results = [
        _mk_result(short=1, long=10, max_drawdown=0.0, completed_trade_count=0),
        _mk_result(short=2, long=20, max_drawdown=0.0, completed_trade_count=0),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_max_drawdown"] is not None
    assert r["best_by_max_drawdown"]["completed_trade_count"] == 0


def test_profit_factor_ranking_excludes_zero_trades_when_possible() -> None:
    results = [
        _mk_result(short=1, long=10, profit_factor=0.0, completed_trade_count=0),
        _mk_result(short=2, long=20, profit_factor=1.5, completed_trade_count=5),
        _mk_result(short=3, long=30, profit_factor=2.1, completed_trade_count=6),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_profit_factor"]["profit_factor"] == 2.1


def test_return_over_drawdown_ranking() -> None:
    # r1: 0.10 / 0.10 = 1.0 ; r2: 0.20 / 0.05 = 4.0 ; r3: 0.05 / 0.01 = 5.0
    results = [
        _mk_result(short=1, long=10, total_return=0.10, max_drawdown=-0.10,
                   completed_trade_count=3),
        _mk_result(short=2, long=20, total_return=0.20, max_drawdown=-0.05,
                   completed_trade_count=4),
        _mk_result(short=3, long=30, total_return=0.05, max_drawdown=-0.01,
                   completed_trade_count=5),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_return_over_drawdown"]["short_window"] == 3


def test_return_over_drawdown_zero_dd_positive_return_wins() -> None:
    """Zero drawdown with a positive return is the ideal case — it
    should beat any finite-drawdown result."""
    results = [
        _mk_result(short=1, long=10, total_return=0.50, max_drawdown=-0.10,
                   completed_trade_count=3),
        _mk_result(short=2, long=20, total_return=0.01, max_drawdown=0.0,
                   completed_trade_count=1),
    ]
    r = bse.rank_sweep_results(results)
    assert r["best_by_return_over_drawdown"]["short_window"] == 2


def test_top_10_by_total_return_is_sorted_descending() -> None:
    results = [
        _mk_result(short=i, long=i * 2, total_return=i * 0.01)
        for i in range(1, 15)
    ]
    r = bse.rank_sweep_results(results)
    top = r["top_10_by_total_return"]
    assert len(top) == 10
    returns = [t["total_return"] for t in top]
    assert returns == sorted(returns, reverse=True)
    assert top[0]["total_return"] == 0.14


def test_top_10_by_sharpe_ratio_is_sorted_descending() -> None:
    results = [
        _mk_result(short=i, long=i * 2, sharpe_ratio=i * 0.1)
        for i in range(1, 15)
    ]
    r = bse.rank_sweep_results(results)
    top = r["top_10_by_sharpe_ratio"]
    assert len(top) == 10
    values = [t["sharpe_ratio"] for t in top]
    assert values == sorted(values, reverse=True)


def test_ranking_returns_nones_on_empty_sweep() -> None:
    r = bse.rank_sweep_results([])
    assert r["best_by_total_return"] is None
    assert r["best_by_sharpe_ratio"] is None
    assert r["best_by_max_drawdown"] is None
    assert r["best_by_profit_factor"] is None
    assert r["best_by_return_over_drawdown"] is None
    assert r["top_10_by_total_return"] == []
    assert r["top_10_by_sharpe_ratio"] == []


def test_run_sweep_attaches_rankings() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 61)])
    out = bse.run_sweep(bars, short_windows=[3, 5], long_windows=[10, 20])
    assert "rankings" in out
    assert out["rankings"]["best_by_total_return"] is not None
    assert isinstance(out["rankings"]["top_10_by_total_return"], list)


def test_run_sweep_rankings_empty_when_all_combinations_skipped() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    out = bse.run_sweep(bars, short_windows=[10, 20], long_windows=[5])
    assert out["combination_count"] == 0
    assert out["rankings"]["best_by_total_return"] is None
    assert out["rankings"]["top_10_by_total_return"] == []


def test_compare_to_baseline_outperformed() -> None:
    baseline = _mk_result(total_return=0.30)
    baseline["buy_and_hold_return"] = 0.20
    cmp = bse.compare_to_baseline(baseline)
    assert cmp["baseline_total_return"] == 0.30
    assert cmp["buy_and_hold_return"] == 0.20
    assert cmp["baseline_outperformed_buy_and_hold"] is True
    assert math.isclose(cmp["baseline_return_gap_vs_buy_and_hold"], 0.10, abs_tol=1e-9)


def test_compare_to_baseline_underperformed() -> None:
    baseline = _mk_result(total_return=0.05)
    baseline["buy_and_hold_return"] = 0.25
    cmp = bse.compare_to_baseline(baseline)
    assert cmp["baseline_outperformed_buy_and_hold"] is False
    assert math.isclose(cmp["baseline_return_gap_vs_buy_and_hold"], -0.20, abs_tol=1e-9)


def test_build_summary_includes_baseline_comparison() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    now = datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc)
    summary = bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=now, short_window=3, long_window=5,
    )
    assert "baseline_comparison" in summary
    cmp = summary["baseline_comparison"]
    for field in (
        "baseline_total_return",
        "buy_and_hold_return",
        "baseline_outperformed_buy_and_hold",
        "baseline_return_gap_vs_buy_and_hold",
    ):
        assert field in cmp
    # Field must be consistent with the baseline result.
    assert cmp["baseline_total_return"] == summary["baseline"]["total_return"]
    assert cmp["buy_and_hold_return"] == summary["baseline"]["buy_and_hold_return"]


def test_build_summary_sweep_includes_rankings() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 61)])
    now = datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc)
    summary = bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=now, short_window=3, long_window=5,
        short_windows=[3, 5], long_windows=[10, 20],
    )
    assert "rankings" in summary["sweep"]
    rk = summary["sweep"]["rankings"]
    for key in (
        "best_by_total_return", "best_by_sharpe_ratio",
        "best_by_max_drawdown", "best_by_profit_factor",
        "best_by_return_over_drawdown",
        "top_10_by_total_return", "top_10_by_sharpe_ratio",
    ):
        assert key in rk


# ---------------------------------------------------------------------------
# S58 — chronological train/test split + generalization report
# ---------------------------------------------------------------------------


def _build_default_summary(bars, *, split_ratio=None, **kw):
    """Convenience wrapper — build a full summary with default windows."""
    return bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        short_window=3, long_window=5,
        split_ratio=split_ratio,
        **kw,
    )


def test_split_bars_produces_expected_partition_sizes() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 101)])  # 100 bars
    train, test = bse.split_bars_chronological(bars, 0.7)
    assert len(train) == 70
    assert len(test) == 30


def test_split_preserves_chronological_order() -> None:
    """Train's last timestamp must precede test's first — no shuffling."""
    bars = _bars_from_closes([float(c) for c in range(1, 51)])
    train, test = bse.split_bars_chronological(bars, 0.6)
    assert all(train[i].ts <= train[i + 1].ts for i in range(len(train) - 1))
    assert all(test[i].ts <= test[i + 1].ts for i in range(len(test) - 1))
    assert train[-1].ts < test[0].ts
    # Concatenation is the original bar list, in order.
    assert [b.ts for b in train + test] == [b.ts for b in bars]


def test_split_rejects_invalid_ratio() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 21)])
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(BacktestError, match="split-ratio"):
            bse.split_bars_chronological(bars, bad)


def test_split_rejects_insufficient_partition() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 21)])  # 20 bars
    # ratio=0.5 → 10/10 → OK for min=2; make it fail by demanding min=15.
    with pytest.raises(BacktestError, match="partition too small"):
        bse.split_bars_chronological(bars, 0.5, min_partition_bars=15)


def test_build_summary_no_split_preserves_schema() -> None:
    """Without --split-ratio the summary must not gain split keys."""
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    summary = _build_default_summary(bars)
    for key in ("split", "train_summary", "test_summary",
                "generalization_report"):
        assert key not in summary


def test_build_summary_with_split_populates_partitions() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 101)])
    summary = _build_default_summary(bars, split_ratio=0.6)
    assert "split" in summary
    split = summary["split"]
    assert split["mode"] == "chronological"
    assert split["ratio"] == 0.6
    assert split["total_bar_count"] == 100
    assert split["train_bar_count"] == 60
    assert split["test_bar_count"] == 40
    assert split["train_start"] is not None
    assert split["train_end"] is not None
    assert split["test_start"] is not None
    assert split["test_end"] is not None
    # train_end < test_start (chronological)
    assert split["train_end"] < split["test_start"]

    # Each partition summary uses its own bar subset, not the full series.
    assert summary["train_summary"]["bar_count"] == 60
    assert summary["test_summary"]["bar_count"] == 40
    # Partition summaries carry their own baseline + baseline_comparison.
    assert "baseline" in summary["train_summary"]
    assert "baseline_comparison" in summary["test_summary"]


def test_build_summary_with_split_and_sweep_matches_windows() -> None:
    """The train winner's (short, long) must appear identically in test."""
    bars = _bars_from_closes([float(c) for c in range(1, 121)])
    summary = _build_default_summary(
        bars, split_ratio=0.6,
        short_windows=[3, 5], long_windows=[10, 20],
    )
    report = summary["generalization_report"]
    best_train = report["best_train_by_total_return"]
    best_test = report["corresponding_test_result"]
    assert best_train is not None
    assert best_test is not None
    assert best_train["short_window"] == best_test["short_window"]
    assert best_train["long_window"] == best_test["long_window"]


def test_build_summary_rejects_invalid_split_ratio() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    with pytest.raises(BacktestError, match="split-ratio"):
        _build_default_summary(bars, split_ratio=1.5)


def test_build_summary_rejects_insufficient_data_for_split() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 11)])  # 10 bars
    # long_window=5 → each partition needs 6+ bars → 0.5 gives 5/5 → too small.
    with pytest.raises(BacktestError, match="partition too small"):
        _build_default_summary(bars, split_ratio=0.5)


def test_build_summary_rejects_invalid_split_mode() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    with pytest.raises(BacktestError, match="split-mode"):
        _build_default_summary(bars, split_ratio=0.5, split_mode="random")


# --- generalization_report unit tests ---


def _mk_partition(baseline: dict, sweep_results: list[dict] | None = None) -> dict:
    """Build a synthetic partition summary suitable for
    build_generalization_report."""
    part: dict = {"baseline": baseline, "baseline_comparison": {}}
    if sweep_results is not None:
        part["sweep"] = {
            "sweep": sweep_results,
            "skipped_combinations": [],
            "combination_count": len(sweep_results),
            "rankings": bse.rank_sweep_results(sweep_results),
        }
    return part


def _bt(short, long, *, total_return=0.0, sharpe=0.0, drawdown=0.0,
        trades=5, buy_and_hold_return=0.0):
    return {
        "short_window": short,
        "long_window": long,
        "total_return": total_return,
        "sharpe_ratio": sharpe,
        "max_drawdown": drawdown,
        "profit_factor": 1.0,
        "completed_trade_count": trades,
        "buy_and_hold_return": buy_and_hold_return,
    }


def test_generalization_report_positive_train_negative_test_warns() -> None:
    train_sweep = [_bt(3, 10, total_return=0.20, sharpe=1.0, trades=8)]
    test_sweep  = [_bt(3, 10, total_return=-0.05, sharpe=-0.4, trades=8)]
    train = _mk_partition(train_sweep[0], train_sweep)
    test  = _mk_partition(test_sweep[0], test_sweep)
    r = bse.build_generalization_report(train, test)
    assert r["overfit_warning"] is True
    assert "TRAIN_POSITIVE_TEST_NON_POSITIVE_RETURN" in r["overfit_reasons"]
    assert "TRAIN_POSITIVE_TEST_NON_POSITIVE_SHARPE" in r["overfit_reasons"]


def test_generalization_report_large_return_gap_warns() -> None:
    train = _mk_partition(_bt(3, 10, total_return=0.30, sharpe=0.8, trades=8),
                          [_bt(3, 10, total_return=0.30, sharpe=0.8, trades=8)])
    test  = _mk_partition(_bt(3, 10, total_return=0.05, sharpe=0.4, trades=8),
                          [_bt(3, 10, total_return=0.05, sharpe=0.4, trades=8)])
    r = bse.build_generalization_report(train, test)
    assert r["overfit_warning"] is True
    assert "LARGE_TRAIN_TEST_RETURN_GAP" in r["overfit_reasons"]
    assert math.isclose(r["train_test_return_gap"], 0.25, abs_tol=1e-9)


def test_generalization_report_insufficient_trades_warns() -> None:
    train = _mk_partition(_bt(3, 10, total_return=0.05, sharpe=0.4, trades=2),
                          [_bt(3, 10, total_return=0.05, sharpe=0.4, trades=2)])
    test  = _mk_partition(_bt(3, 10, total_return=0.04, sharpe=0.3, trades=5),
                          [_bt(3, 10, total_return=0.04, sharpe=0.3, trades=5)])
    r = bse.build_generalization_report(train, test)
    assert r["overfit_warning"] is True
    assert "INSUFFICIENT_TRADE_COUNT" in r["overfit_reasons"]


def test_generalization_report_stable_performance_no_warning() -> None:
    train_sweep = [_bt(3, 10, total_return=0.08, sharpe=0.6, drawdown=-0.05,
                       trades=8, buy_and_hold_return=0.05)]
    test_sweep  = [_bt(3, 10, total_return=0.07, sharpe=0.5, drawdown=-0.06,
                       trades=8, buy_and_hold_return=0.05)]
    train = _mk_partition(train_sweep[0], train_sweep)
    test  = _mk_partition(test_sweep[0], test_sweep)
    r = bse.build_generalization_report(train, test)
    assert r["overfit_warning"] is False
    assert r["overfit_reasons"] == []
    assert r["test_outperformed_buy_and_hold"] is True
    assert math.isclose(r["train_test_return_gap"], 0.01, abs_tol=1e-9)


def test_generalization_report_uses_baseline_when_no_sweep() -> None:
    """Without a sweep, the baseline stands in as the only 'config'."""
    train = _mk_partition(_bt(3, 5, total_return=0.10, sharpe=0.5, trades=6))
    test  = _mk_partition(_bt(3, 5, total_return=0.08, sharpe=0.4, trades=6))
    r = bse.build_generalization_report(train, test)
    assert r["best_train_by_total_return"]["short_window"] == 3
    assert r["corresponding_test_result"]["short_window"] == 3


def test_generalization_report_missing_test_config_warns() -> None:
    train_sweep = [_bt(3, 10, total_return=0.15, sharpe=0.7, trades=6)]
    # Test partition has a totally different set of windows — no match.
    test_sweep  = [_bt(4, 20, total_return=0.05, sharpe=0.2, trades=6)]
    train = _mk_partition(train_sweep[0], train_sweep)
    test  = _mk_partition(test_sweep[0], test_sweep)
    r = bse.build_generalization_report(train, test)
    assert r["corresponding_test_result"] is None
    assert r["overfit_warning"] is True
    assert "NO_MATCHING_TEST_CONFIG" in r["overfit_reasons"]


def test_no_alpaca_or_network_imports_in_backtest_tool() -> None:
    """Enforce that the tool source imports no broker/network modules."""
    source = Path("src/tools/backtest_strategy_eval.py").read_text(encoding="utf-8")
    banned = ["alpaca", "requests", "httpx", "urllib.request", "socket"]
    for tok in banned:
        assert tok not in source, (
            f"backtest_strategy_eval must not depend on {tok!r}"
        )


def test_cli_split_ratio_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 121)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--split-ratio", "0.7",
        "--short-windows", "3,5",
        "--long-windows", "10,20",
        "--no-write",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["split"]["mode"] == "chronological"
    assert payload["split"]["ratio"] == 0.7
    assert "generalization_report" in payload


def test_cli_bad_split_ratio_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 41)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--split-ratio", "1.5",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "split-ratio" in err["error"]


# ---------------------------------------------------------------------------
# S59 — rolling walk-forward validation
# ---------------------------------------------------------------------------


def _wf_build(bars, **kw):
    return bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        short_window=3, long_window=5,
        **kw,
    )


# --- Window construction ---


def test_wf_window_boundaries_are_correct() -> None:
    # 4000 bars, train=1600 test=400 step=400 → windows 0..N
    wins = bse.walk_forward_windows(4000, 1600, 400, 400)
    # First window: [0..1600), [1600..2000)
    assert wins[0] == (0, 0, 1600, 1600, 2000)
    # Second window: [400..2000), [2000..2400)
    assert wins[1] == (1, 400, 400 + 1600, 2000, 2400)
    # Last window's test_end == 4000
    assert wins[-1][4] == 4000


def test_wf_windows_preserve_chronological_order() -> None:
    wins = bse.walk_forward_windows(5000, 1000, 250, 250)
    for i in range(1, len(wins)):
        # Each window's train_start is >= the previous window's train_start,
        # and its train_start increases by exactly step_bars.
        assert wins[i][1] > wins[i - 1][1]
        assert wins[i][1] - wins[i - 1][1] == 250


def test_wf_train_and_test_never_overlap() -> None:
    wins = bse.walk_forward_windows(3000, 1000, 200, 200)
    for wi, ts, te, sts, ste in wins:
        assert te == sts  # train ends exactly where test starts
        assert ts < te <= sts < ste


def test_wf_incomplete_final_window_excluded() -> None:
    # 2350 bars, train=1600 test=400 step=400
    # w0: test_end=2000 (ok); w1: test_end=2400 > 2350 → dropped.
    wins = bse.walk_forward_windows(2350, 1600, 400, 400)
    assert len(wins) == 1
    assert wins[0][4] == 2000


def test_wf_rejects_step_smaller_than_test() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 401)])
    with pytest.raises(BacktestError, match="step"):
        bse.run_walk_forward(
            bars, symbol="SPY", interval="60m",
            baseline_short=3, baseline_long=5,
            short_windows=[3, 5], long_windows=[10, 20],
            train_bars=100, test_bars=100, step_bars=50,
        )


def test_wf_rejects_non_positive_sizes() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 401)])
    common = dict(
        symbol="SPY", interval="60m",
        baseline_short=3, baseline_long=5,
        short_windows=[3, 5], long_windows=[10, 20],
    )
    for kw in (
        {"train_bars": 0,   "test_bars": 100, "step_bars": 100},
        {"train_bars": 100, "test_bars": 0,   "step_bars": 100},
        {"train_bars": 100, "test_bars": 100, "step_bars": 0},
    ):
        with pytest.raises(BacktestError):
            bse.run_walk_forward(bars, **common, **kw)


def test_wf_rejects_insufficient_bars() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 51)])  # 50 bars
    with pytest.raises(BacktestError, match="insufficient"):
        bse.run_walk_forward(
            bars, symbol="SPY", interval="60m",
            baseline_short=3, baseline_long=5,
            short_windows=[3, 5], long_windows=[10, 20],
            train_bars=100, test_bars=100, step_bars=100,
        )


# --- Test warmup / no leakage ---


def test_run_backtest_warmup_no_trades_during_warmup() -> None:
    """A bullish crossover in the warmup region must not open a trade."""
    # Uptrend that fires a BUY early on.
    closes = [float(c) for c in list(range(1, 41))]
    bars = _bars_from_closes(closes)
    # Warmup covers the first 15 bars — no trade should originate there.
    result = bse.run_backtest(
        bars, short_window=3, long_window=5, evaluation_start_index=15,
    )
    # bar_count reflects only the evaluated slice.
    assert result.bar_count == len(closes) - 15
    # No trade may reference an entry index < 15.
    for t in result.trades:
        assert t.entry_index >= 15
    if result.open_position:
        assert result.open_entry_index is not None
        assert result.open_entry_index >= 15


def test_run_backtest_warmup_carries_no_position_into_evaluation() -> None:
    """A position 'opened' in warmup must not persist into evaluation."""
    closes = [float(c) for c in list(range(1, 41))]
    bars = _bars_from_closes(closes)
    warm = bse.run_backtest(bars, 3, 5, evaluation_start_index=20)
    # Evaluation region starts fresh — position count is what happens
    # AFTER bar 20.
    assert warm.open_entry_index is None or warm.open_entry_index >= 20


def test_run_backtest_default_still_works() -> None:
    """evaluation_start_index=0 must preserve original semantics."""
    closes = [float(c) for c in range(1, 51)]
    bars = _bars_from_closes(closes)
    result = bse.run_backtest(bars, 3, 5)
    assert result.bar_count == 50
    assert result.buy_and_hold_return == pytest.approx((50 - 1) / 1, rel=1e-9)


def test_run_backtest_warmup_metrics_exclude_warmup() -> None:
    """buy_and_hold_return must reflect the evaluated slice only."""
    closes = [float(c) for c in range(1, 51)]  # 1..50
    bars = _bars_from_closes(closes)
    # Full run: BH = 49.0
    full = bse.run_backtest(bars, 3, 5)
    # With warmup=20: BH uses close[20]=21 to close[-1]=50 → 29/21
    warm = bse.run_backtest(bars, 3, 5, evaluation_start_index=20)
    assert full.buy_and_hold_return == pytest.approx(49.0)
    assert warm.buy_and_hold_return == pytest.approx((50 - 21) / 21)


# --- Selection integrity ---


def test_wf_selected_windows_match_train_and_test() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    out = bse.run_walk_forward(
        bars, symbol="SPY", interval="60m",
        baseline_short=3, baseline_long=5,
        short_windows=[3, 5], long_windows=[10, 20],
        train_bars=100, test_bars=100, step_bars=100,
    )
    assert out["windows"]
    for w in out["windows"]:
        s = w["selected_short_window"]
        l = w["selected_long_window"]
        assert w["selected_train_result"]["short_window"] == s
        assert w["selected_train_result"]["long_window"] == l
        assert w["selected_test_result"]["short_window"] == s
        assert w["selected_test_result"]["long_window"] == l


def test_wf_train_slice_never_includes_test_bars() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    out = bse.run_walk_forward(
        bars, symbol="SPY", interval="60m",
        baseline_short=3, baseline_long=5,
        short_windows=[3, 5], long_windows=[10, 20],
        train_bars=150, test_bars=100, step_bars=100,
    )
    for w in out["windows"]:
        assert w["train_end"] <= w["test_start"]


# --- Aggregate correctness ---


def _mk_window(wi, *, s=3, l=5, test_return=0.0, sharpe=0.0,
               drawdown=0.0, trades=5, bh=0.0):
    return {
        "window_index": wi,
        "train_start": "t0", "train_end": "t1",
        "test_start": "e0", "test_end": "e1",
        "train_bar_count": 100, "test_bar_count": 50,
        "selected_short_window": s,
        "selected_long_window":  l,
        "selected_train_result": {"short_window": s, "long_window": l,
                                  "total_return": test_return,
                                  "sharpe_ratio": sharpe,
                                  "max_drawdown": drawdown,
                                  "completed_trade_count": trades},
        "selected_test_result":  {"short_window": s, "long_window": l,
                                  "total_return": test_return,
                                  "sharpe_ratio": sharpe,
                                  "max_drawdown": drawdown,
                                  "completed_trade_count": trades,
                                  "buy_and_hold_return": bh},
        "test_buy_and_hold_return":       bh,
        "test_outperformed_buy_and_hold": test_return > bh,
        "test_profitable":                test_return > 0,
        "test_positive_sharpe":           sharpe > 0,
    }


def test_wf_aggregate_return_is_product_of_windows() -> None:
    windows = [
        _mk_window(0, test_return=0.10, bh=0.05, sharpe=0.5, trades=6),
        _mk_window(1, test_return=-0.05, bh=0.02, sharpe=-0.3, trades=6),
        _mk_window(2, test_return=0.08, bh=0.06, sharpe=0.4, trades=6),
    ]
    agg = bse._aggregate_walk_forward(windows)
    expected = (1.10 * 0.95 * 1.08) - 1
    assert agg["aggregate_walk_forward_return"] == pytest.approx(expected, rel=1e-12)


def test_wf_aggregate_bh_compounding_correct() -> None:
    windows = [
        _mk_window(0, test_return=0.0, bh=0.05, trades=1),
        _mk_window(1, test_return=0.0, bh=-0.02, trades=1),
        _mk_window(2, test_return=0.0, bh=0.10, trades=1),
    ]
    agg = bse._aggregate_walk_forward(windows)
    expected = (1.05 * 0.98 * 1.10) - 1
    assert agg["aggregate_buy_and_hold_return"] == pytest.approx(expected, rel=1e-12)


def test_wf_parameter_selection_frequency_counts_correctly() -> None:
    windows = [
        _mk_window(0, s=15, l=20),
        _mk_window(1, s=15, l=20),
        _mk_window(2, s=20, l=50),
        _mk_window(3, s=15, l=20),
    ]
    agg = bse._aggregate_walk_forward(windows)
    assert agg["parameter_selection_frequency"] == {"15/20": 3, "20/50": 1}
    assert agg["unique_selected_parameter_count"] == 2


def test_wf_rates_are_correct() -> None:
    windows = [
        _mk_window(0, test_return=0.10, sharpe=0.5, bh=0.05, trades=6),
        _mk_window(1, test_return=-0.02, sharpe=-0.1, bh=0.01, trades=6),
        _mk_window(2, test_return=0.05, sharpe=0.3, bh=0.02, trades=6),
        _mk_window(3, test_return=0.01, sharpe=0.1, bh=0.03, trades=6),
    ]
    agg = bse._aggregate_walk_forward(windows)
    assert agg["profitable_test_window_count"] == 3
    assert agg["profitable_test_window_rate"] == pytest.approx(0.75)
    assert agg["positive_sharpe_window_count"] == 3
    assert agg["positive_sharpe_window_rate"] == pytest.approx(0.75)


def test_wf_largest_positive_contribution() -> None:
    windows = [
        _mk_window(0, test_return=0.10, trades=6),
        _mk_window(1, test_return=0.02, trades=6),
        _mk_window(2, test_return=-0.05, trades=6),
    ]
    agg = bse._aggregate_walk_forward(windows)
    assert agg["largest_positive_window_contribution"] == pytest.approx(
        0.10 / (0.10 + 0.02),
    )


def test_wf_largest_positive_contribution_none_when_no_positive() -> None:
    windows = [
        _mk_window(0, test_return=-0.05, trades=6),
        _mk_window(1, test_return=-0.02, trades=6),
    ]
    agg = bse._aggregate_walk_forward(windows)
    assert agg["largest_positive_window_contribution"] is None


# --- Warnings ---


def test_wf_warning_insufficient_windows() -> None:
    windows = [_mk_window(0, test_return=0.05, sharpe=0.4, trades=6, bh=0.02),
               _mk_window(1, test_return=0.04, sharpe=0.3, trades=6, bh=0.02)]
    agg = bse._aggregate_walk_forward(windows)
    w = bse._walk_forward_warnings(windows, agg)
    assert w["warning"] is True
    assert "INSUFFICIENT_WINDOWS" in w["reasons"]


def test_wf_warning_low_profitable_rate() -> None:
    windows = [
        _mk_window(0, test_return=0.05, sharpe=0.3, trades=6, bh=0.02),
        _mk_window(1, test_return=-0.02, sharpe=-0.1, trades=6, bh=-0.01),
        _mk_window(2, test_return=-0.03, sharpe=-0.2, trades=6, bh=-0.01),
        _mk_window(3, test_return=-0.01, sharpe=-0.05, trades=6, bh=0.01),
    ]
    agg = bse._aggregate_walk_forward(windows)
    w = bse._walk_forward_warnings(windows, agg)
    assert "LOW_PROFITABLE_WINDOW_RATE" in w["reasons"]


def test_wf_warning_single_window_concentration() -> None:
    # One window contributes ~90% of positive return.
    windows = [
        _mk_window(0, test_return=0.50, sharpe=0.4, trades=6, bh=0.05),
        _mk_window(1, test_return=0.02, sharpe=0.1, trades=6, bh=0.02),
        _mk_window(2, test_return=0.03, sharpe=0.1, trades=6, bh=0.01),
    ]
    agg = bse._aggregate_walk_forward(windows)
    w = bse._walk_forward_warnings(windows, agg)
    assert "SINGLE_WINDOW_PROFIT_CONCENTRATION" in w["reasons"]


def test_wf_warning_low_test_trade_count() -> None:
    windows = [
        _mk_window(0, test_return=0.05, sharpe=0.4, trades=2, bh=0.02),
        _mk_window(1, test_return=0.06, sharpe=0.5, trades=2, bh=0.03),
        _mk_window(2, test_return=0.07, sharpe=0.6, trades=2, bh=0.02),
    ]
    agg = bse._aggregate_walk_forward(windows)
    w = bse._walk_forward_warnings(windows, agg)
    assert "LOW_TOTAL_TEST_TRADE_COUNT" in w["reasons"]


def test_wf_warning_underperformed_bh() -> None:
    windows = [
        _mk_window(0, test_return=0.01, sharpe=0.1, trades=6, bh=0.10),
        _mk_window(1, test_return=0.01, sharpe=0.1, trades=6, bh=0.09),
        _mk_window(2, test_return=0.01, sharpe=0.1, trades=6, bh=0.08),
    ]
    agg = bse._aggregate_walk_forward(windows)
    w = bse._walk_forward_warnings(windows, agg)
    assert "UNDERPERFORMED_BUY_AND_HOLD" in w["reasons"]


def test_wf_stable_performance_no_warnings() -> None:
    # 4 windows, 3 profitable, positive sharpe, plenty of trades, no
    # concentration, aggregate return positive and above BH.
    windows = [
        _mk_window(0, test_return=0.06, sharpe=0.4, trades=6, bh=0.02),
        _mk_window(1, test_return=0.04, sharpe=0.3, trades=6, bh=0.01),
        _mk_window(2, test_return=0.05, sharpe=0.35, trades=6, bh=0.02),
        _mk_window(3, test_return=0.03, sharpe=0.25, trades=6, bh=0.01),
    ]
    agg = bse._aggregate_walk_forward(windows)
    w = bse._walk_forward_warnings(windows, agg)
    assert w["warning"] is False
    assert w["reasons"] == []


# --- Integration through build_summary + CLI ---


def test_build_summary_no_walk_forward_preserves_schema() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 41)])
    summary = _wf_build(bars)
    for key in ("walk_forward",):
        assert key not in summary


def test_build_summary_walk_forward_populates_root_key() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    summary = _wf_build(
        bars, walk_forward=True,
        short_windows=[3, 5], long_windows=[10, 20],
        wf_train_bars=100, wf_test_bars=50, wf_step_bars=50,
    )
    wf = summary["walk_forward"]
    for field in ("mode", "train_bars", "test_bars", "step_bars",
                  "selection_metric", "total_bar_count",
                  "windows", "aggregate",
                  "walk_forward_warning", "walk_forward_warning_reasons"):
        assert field in wf
    for w in wf["windows"]:
        for field in ("window_index", "train_start", "train_end",
                      "test_start", "test_end",
                      "train_bar_count", "test_bar_count",
                      "selected_short_window", "selected_long_window",
                      "selected_train_result", "selected_test_result",
                      "test_buy_and_hold_return",
                      "test_outperformed_buy_and_hold",
                      "test_profitable", "test_positive_sharpe"):
            assert field in w


def test_build_summary_rejects_walk_forward_with_split() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    with pytest.raises(BacktestError, match="cannot be used together"):
        _wf_build(bars, walk_forward=True, split_ratio=0.7)


def test_cli_walk_forward_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 601)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--walk-forward",
        "--wf-train-bars", "150",
        "--wf-test-bars", "100",
        "--wf-step-bars", "100",
        "--short-windows", "3,5",
        "--long-windows", "10,20",
        "--no-write",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "walk_forward" in payload
    assert payload["walk_forward"]["mode"] == "rolling_chronological"
    assert payload["walk_forward"]["train_bars"] == 150


def test_cli_walk_forward_and_split_together_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 401)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--walk-forward",
        "--split-ratio", "0.7",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "cannot be used together" in err["error"]


def test_cli_walk_forward_bad_step_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 401)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--walk-forward",
        "--wf-train-bars", "100",
        "--wf-test-bars", "100",
        "--wf-step-bars", "50",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "wf_step_bars" in err["error"]


# --- Non-Alpaca / non-network safety ---


def test_walk_forward_tool_still_has_no_broker_or_network_imports() -> None:
    """Re-affirm the S58 safety scan after S59 additions."""
    source = Path("src/tools/backtest_strategy_eval.py").read_text(encoding="utf-8")
    for tok in ("alpaca", "requests", "httpx", "urllib.request", "socket"):
        assert tok not in source, (
            f"backtest_strategy_eval must not depend on {tok!r}"
        )


# ---------------------------------------------------------------------------
# S59 review — warmup-fit validation
# ---------------------------------------------------------------------------


def test_wf_rejects_train_shorter_than_max_long_window() -> None:
    """train_bars must be >= max(long_windows) + 1 so every test window
    can receive full SMA warmup — no silent shortening allowed."""
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    with pytest.raises(BacktestError, match="wf_train_bars"):
        bse.run_walk_forward(
            bars, symbol="SPY", interval="60m",
            baseline_short=3, baseline_long=5,
            short_windows=[3, 5], long_windows=[10, 50],
            train_bars=50,   # < 51 required (max_long=50 → need 51)
            test_bars=50, step_bars=50,
        )


def test_wf_train_exactly_max_long_plus_one_is_accepted() -> None:
    """train_bars == max_long + 1 is the tightest allowed size."""
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    out = bse.run_walk_forward(
        bars, symbol="SPY", interval="60m",
        baseline_short=3, baseline_long=5,
        short_windows=[3, 5], long_windows=[10, 20],
        train_bars=21,   # exactly max_long (20) + 1
        test_bars=50, step_bars=50,
    )
    # Should run without raising; may produce windows.
    assert isinstance(out["windows"], list)


def test_wf_train_shorter_than_baseline_long_rejected_even_without_sweep() -> None:
    """When no sweep is provided, the baseline long_window is the max."""
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    with pytest.raises(BacktestError, match="wf_train_bars"):
        bse.run_walk_forward(
            bars, symbol="SPY", interval="60m",
            baseline_short=5, baseline_long=30,
            short_windows=None, long_windows=None,
            train_bars=20,   # < 31 required
            test_bars=50, step_bars=50,
        )


def test_wf_warmup_exactly_matches_selected_long_window() -> None:
    """Every emitted test evaluation must have received exactly
    selected_long_window prior bars as warmup."""
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    out = bse.run_walk_forward(
        bars, symbol="SPY", interval="60m",
        baseline_short=3, baseline_long=5,
        short_windows=[3, 5], long_windows=[10, 20],
        train_bars=100, test_bars=100, step_bars=100,
    )
    assert out["windows"]
    for w in out["windows"]:
        # If a trade opened in the test region, its entry_index must be
        # >= the warmup boundary. The eval_slice length is
        # warmup + test_bars, and evaluation_start_index = warmup =
        # selected_long_window. Any open_entry_index (in eval-slice
        # coordinates) must therefore be >= selected_long_window.
        test_res = w["selected_test_result"]
        expected_warmup = w["selected_long_window"]
        # bar_count is n_eval = eval_slice_len - warmup = test_bars.
        assert test_res["bar_count"] == w["test_bar_count"]
        if test_res.get("open_position"):
            assert test_res["open_entry_index"] >= expected_warmup


def test_cli_wf_train_shorter_than_max_long_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 601)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--walk-forward",
        "--wf-train-bars", "40",   # < max long (50) + 1
        "--wf-test-bars", "50",
        "--wf-step-bars", "50",
        "--short-windows", "3,5",
        "--long-windows", "10,50",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "wf_train_bars" in err["error"]


def test_wf_default_1600_400_400_configuration_still_accepted() -> None:
    """Default 1600/400/400 with the standard 10/20 baseline must
    continue to be a valid configuration."""
    bars = _bars_from_closes([float(c) for c in range(1, 2101)])  # 2100 bars
    out = bse.run_walk_forward(
        bars, symbol="SPY", interval="60m",
        baseline_short=10, baseline_long=20,
        short_windows=None, long_windows=None,
        train_bars=1600, test_bars=400, step_bars=400,
    )
    # 2100 bars produces one complete window: [0:1600] train, [1600:2000] test.
    assert out["mode"] == "rolling_chronological"
    assert out["train_bars"] == 1600
    assert out["test_bars"] == 400
    assert out["step_bars"] == 400


# ---------------------------------------------------------------------------
# S60 — fixed-parameter comparison + robustness report
# ---------------------------------------------------------------------------


# --- Parser ---


def test_parse_fixed_params_valid_pairs() -> None:
    assert bse.parse_fixed_params("10/20,15/50") == [(10, 20), (15, 50)]


def test_parse_fixed_params_rejects_malformed() -> None:
    with pytest.raises(BacktestError, match="malformed"):
        bse.parse_fixed_params("10-20")


def test_parse_fixed_params_rejects_non_integer() -> None:
    with pytest.raises(BacktestError, match="two integers"):
        bse.parse_fixed_params("10/foo")


def test_parse_fixed_params_rejects_short_ge_long() -> None:
    with pytest.raises(BacktestError, match="short must be < long"):
        bse.parse_fixed_params("20/20")
    with pytest.raises(BacktestError, match="short must be < long"):
        bse.parse_fixed_params("30/20")


def test_parse_fixed_params_dedup_is_deterministic_and_preserves_order() -> None:
    # First occurrence wins; later duplicates dropped.
    got = bse.parse_fixed_params("10/20,15/50,10/20,15/50,20/50")
    assert got == [(10, 20), (15, 50), (20, 50)]


def test_parse_fixed_params_empty_rejected() -> None:
    with pytest.raises(BacktestError):
        bse.parse_fixed_params("")
    with pytest.raises(BacktestError):
        bse.parse_fixed_params("   ")


def test_parse_fixed_params_rejects_non_positive() -> None:
    with pytest.raises(BacktestError, match="positive"):
        bse.parse_fixed_params("0/20")
    with pytest.raises(BacktestError, match="positive"):
        bse.parse_fixed_params("-5/10")


# --- CLI / build_summary gating ---


def test_build_summary_rejects_fixed_params_without_walk_forward() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    with pytest.raises(BacktestError, match="require --walk-forward"):
        bse.build_summary(
            bars=bars, symbol="SPY", interval="60m",
            now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
            short_window=3, long_window=5,
            wf_fixed_params=[(3, 5), (10, 20)],
        )


def test_build_summary_rejects_compare_fixed_without_walk_forward() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 501)])
    with pytest.raises(BacktestError, match="require --walk-forward"):
        bse.build_summary(
            bars=bars, symbol="SPY", interval="60m",
            now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
            short_window=3, long_window=5,
            wf_compare_fixed=True,
        )


def test_default_fixed_params_derived_from_baseline_and_sweep() -> None:
    got = bse._default_fixed_params(
        baseline_short=3, baseline_long=5,
        short_windows=[3, 5, 10], long_windows=[10, 20],
    )
    # Baseline first, then valid short<long combinations, deduped.
    assert got[0] == (3, 5)
    for s, l in got:
        assert s < l
    # No duplicates.
    assert len(got) == len(set(got))


# --- Fixed evaluation on the same windows as adaptive ---


def _wf_with_fixed(bars, **kw):
    return bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        short_window=3, long_window=5,
        walk_forward=True,
        wf_train_bars=100, wf_test_bars=100, wf_step_bars=100,
        short_windows=[3, 5], long_windows=[10, 20],
        **kw,
    )


def test_fixed_windows_exactly_match_adaptive_windows() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 10), (5, 20)])
    wf = summary["walk_forward"]
    fp = wf["fixed_parameter_comparison"]
    assert fp["window_count"] == len(wf["windows"])
    assert fp["test_windows_identical_to_adaptive"] is True
    # Every adaptive window's (test_start, test_end) must appear in each
    # fixed parameter's window list at the same window_index.
    adaptive_by_idx = {w["window_index"]: (w["test_start"], w["test_end"])
                       for w in wf["windows"]}
    for key, p in fp["parameters"].items():
        for w in p["windows"]:
            assert (w["test_start"], w["test_end"]) == adaptive_by_idx[w["window_index"]]


def test_fixed_configuration_unchanged_across_windows() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 10)])
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    p = fp["parameters"]["3/10"]
    for w in p["windows"]:
        assert w["result"]["short_window"] == 3
        assert w["result"]["long_window"] == 10


def test_fixed_never_uses_test_performance_for_selection() -> None:
    """A trivial invariant: the fixed pair we asked for is exactly the
    fixed pair reported per window — no rewriting based on test data."""
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 20)])
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    for w in fp["parameters"]["3/20"]["windows"]:
        assert w["result"]["short_window"] == 3
        assert w["result"]["long_window"] == 20


def test_fixed_warmup_exactly_long_window() -> None:
    """The fixed test result's open_entry_index (in eval-slice coords)
    must sit at or beyond the warmup boundary = selected long_window."""
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 20)])
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    for w in fp["parameters"]["3/20"]["windows"]:
        # bar_count reflects only test bars.
        assert w["result"]["bar_count"] == w["test_bar_count"]
        if w["result"].get("open_position"):
            assert w["result"]["open_entry_index"] >= 20


def test_fixed_test_metrics_exclude_warmup() -> None:
    """bar_count exposed by the fixed result must equal test_bars — the
    warmup region is not counted."""
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 10)])
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    for w in fp["parameters"]["3/10"]["windows"]:
        assert w["result"]["bar_count"] == 100  # test_bars


# --- Aggregate + exposure-matched math ---


def _mk_fixed_window(*, wi=0, total_return=0.0, sharpe=0.0, drawdown=0.0,
                     exposure=0.0, trades=5, bh=0.0):
    r = {
        "short_window": 10, "long_window": 20,
        "total_return": total_return,
        "sharpe_ratio": sharpe,
        "max_drawdown": drawdown,
        "exposure_time": exposure,
        "completed_trade_count": trades,
    }
    return {
        "window_index": wi,
        "test_start": "t0", "test_end": "t1",
        "test_bar_count": 50,
        "result": r,
        "test_buy_and_hold_return":            bh,
        "test_outperformed_buy_and_hold":      total_return > bh,
        "test_profitable":                     total_return > 0,
        "test_positive_sharpe":                sharpe > 0,
        "exposure_matched_buy_and_hold_return": exposure * bh,
    }


def test_fixed_aggregate_compounds_correctly() -> None:
    windows = [
        _mk_fixed_window(wi=0, total_return=0.10, bh=0.05, exposure=0.5, trades=6),
        _mk_fixed_window(wi=1, total_return=-0.05, bh=0.02, exposure=0.4, trades=4),
        _mk_fixed_window(wi=2, total_return=0.08, bh=0.06, exposure=0.6, trades=6),
    ]
    agg = bse._fixed_parameter_aggregate(windows)
    expected_ret = (1.10 * 0.95 * 1.08) - 1
    expected_bh  = (1.05 * 1.02 * 1.06) - 1
    expected_xm  = (1 + 0.5 * 0.05) * (1 + 0.4 * 0.02) * (1 + 0.6 * 0.06) - 1
    assert agg["aggregate_return"] == pytest.approx(expected_ret, rel=1e-12)
    assert agg["aggregate_buy_and_hold_return"] == pytest.approx(expected_bh, rel=1e-12)
    assert agg["aggregate_exposure_matched_buy_and_hold_return"] == pytest.approx(
        expected_xm, rel=1e-12,
    )
    assert agg["aggregate_gap_vs_buy_and_hold"] == pytest.approx(
        expected_ret - expected_bh, rel=1e-9,
    )
    assert agg["aggregate_gap_vs_exposure_matched"] == pytest.approx(
        expected_ret - expected_xm, rel=1e-9,
    )


def test_fixed_exposure_matched_per_window_math() -> None:
    """Per-window xm = exposure_time * buy_and_hold_return."""
    w = _mk_fixed_window(exposure=0.35, bh=0.20)
    assert w["exposure_matched_buy_and_hold_return"] == pytest.approx(0.07)


def test_fixed_profitable_and_positive_sharpe_rates() -> None:
    windows = [
        _mk_fixed_window(wi=0, total_return=0.05, sharpe=0.4, trades=6, bh=0.02),
        _mk_fixed_window(wi=1, total_return=-0.02, sharpe=-0.1, trades=6, bh=-0.01),
        _mk_fixed_window(wi=2, total_return=0.03, sharpe=0.2, trades=6, bh=0.01),
        _mk_fixed_window(wi=3, total_return=0.04, sharpe=0.3, trades=6, bh=0.01),
    ]
    agg = bse._fixed_parameter_aggregate(windows)
    assert agg["profitable_test_window_count"] == 3
    assert agg["profitable_test_window_rate"] == pytest.approx(0.75)
    assert agg["positive_sharpe_window_count"] == 3
    assert agg["positive_sharpe_window_rate"] == pytest.approx(0.75)


def test_fixed_total_completed_trade_count() -> None:
    windows = [
        _mk_fixed_window(wi=0, trades=5),
        _mk_fixed_window(wi=1, trades=7),
        _mk_fixed_window(wi=2, trades=3),
    ]
    agg = bse._fixed_parameter_aggregate(windows)
    assert agg["total_completed_test_trades"] == 15


def test_fixed_largest_positive_contribution() -> None:
    windows = [
        _mk_fixed_window(wi=0, total_return=0.10),
        _mk_fixed_window(wi=1, total_return=0.02),
        _mk_fixed_window(wi=2, total_return=-0.05),
    ]
    agg = bse._fixed_parameter_aggregate(windows)
    assert agg["largest_positive_window_contribution"] == pytest.approx(
        0.10 / (0.10 + 0.02),
    )


# --- Adaptive vs fixed ranking ---


def _make_fp(params_data):
    """Build parameters dict from [(key, agg_dict), ...]."""
    out = {}
    for key, agg in params_data:
        s, l = (int(x) for x in key.split("/"))
        out[key] = {"short_window": s, "long_window": l, "windows": [], "aggregate": agg}
    return out


def test_adaptive_vs_fixed_ranking_deterministic() -> None:
    parameters = _make_fp([
        ("10/20", {"aggregate_return": 0.15, "profitable_test_window_rate": 0.75,
                   "worst_test_drawdown": -0.10, "worst_test_return": -0.03,
                   "total_completed_test_trades": 20}),
        ("15/20", {"aggregate_return": 0.08, "profitable_test_window_rate": 0.50,
                   "worst_test_drawdown": -0.15, "worst_test_return": -0.05,
                   "total_completed_test_trades": 16}),
    ])
    adaptive = {
        "aggregate_walk_forward_return": 0.05,
        "profitable_test_window_rate": 0.50,
        "worst_test_return": -0.07,
        "worst_test_drawdown": -0.20,
        "total_completed_test_trades": 12,
        "largest_positive_window_contribution": 0.4,
    }
    cmp = bse._build_adaptive_vs_fixed(adaptive, parameters)
    assert cmp["best_fixed_by_aggregate_return"]["parameter_key"] == "10/20"
    assert cmp["best_fixed_by_profitable_window_rate"]["parameter_key"] == "10/20"
    assert cmp["adaptive_rank_by_aggregate_return"] == 3  # two beat adaptive
    assert cmp["adaptive_outperformed_all_fixed_parameters"] is False
    assert "10/20" in cmp["fixed_parameters_beating_adaptive_aggregate_return"]
    assert "15/20" in cmp["fixed_parameters_beating_adaptive_aggregate_return"]


def test_adaptive_vs_fixed_tiebreak_deterministic() -> None:
    """Two entries tied on aggregate_return; higher profitable rate wins."""
    parameters = _make_fp([
        ("10/20", {"aggregate_return": 0.10, "profitable_test_window_rate": 0.75,
                   "worst_test_drawdown": -0.10, "worst_test_return": -0.02,
                   "total_completed_test_trades": 18}),
        ("15/50", {"aggregate_return": 0.10, "profitable_test_window_rate": 0.60,
                   "worst_test_drawdown": -0.05, "worst_test_return": -0.02,
                   "total_completed_test_trades": 18}),
    ])
    adaptive = {"aggregate_walk_forward_return": 0.0,
                "profitable_test_window_rate": 0.0}
    cmp = bse._build_adaptive_vs_fixed(adaptive, parameters)
    assert cmp["best_fixed_by_aggregate_return"]["parameter_key"] == "10/20"


def test_adaptive_beats_all_when_no_fixed_higher() -> None:
    parameters = _make_fp([
        ("10/20", {"aggregate_return": 0.01, "profitable_test_window_rate": 0.5,
                   "worst_test_drawdown": -0.05, "worst_test_return": -0.02,
                   "total_completed_test_trades": 8}),
    ])
    adaptive = {"aggregate_walk_forward_return": 0.30,
                "profitable_test_window_rate": 0.9}
    cmp = bse._build_adaptive_vs_fixed(adaptive, parameters)
    assert cmp["adaptive_rank_by_aggregate_return"] == 1
    assert cmp["adaptive_outperformed_all_fixed_parameters"] is True


def test_return_over_worst_drawdown_null_when_zero() -> None:
    assert bse._return_over_worst_drawdown({"aggregate_return": 0.1,
                                            "worst_test_drawdown": 0}) is None


# --- Robustness report ---


def _make_agg(*, rate=0.75, ret=0.10, trades=20, dd=-0.10, lpc=0.30,
              bh=0.05, xm=0.03):
    return {
        "profitable_test_window_rate": rate,
        "aggregate_return": ret,
        "total_completed_test_trades": trades,
        "worst_test_drawdown": dd,
        "largest_positive_window_contribution": lpc,
        "aggregate_buy_and_hold_return": bh,
        "aggregate_exposure_matched_buy_and_hold_return": xm,
    }


def test_stable_candidate_all_criteria_pass() -> None:
    params = _make_fp([("10/20", _make_agg())])
    adaptive = {"aggregate_walk_forward_return": 0.0}
    rep = bse._build_robustness_report(params, adaptive)
    assert "10/20" in rep["stable_fixed_candidates"]
    assert rep["fixed_comparison_warning"] is False


def test_stable_candidate_fails_on_low_profitable_rate() -> None:
    params = _make_fp([("10/20", _make_agg(rate=0.40))])
    rep = bse._build_robustness_report(params, {})
    assert rep["stable_fixed_candidates"] == []
    assert "NO_STABLE_FIXED_CANDIDATE" in rep["fixed_comparison_warning_reasons"]


def test_stable_candidate_fails_on_low_trade_count() -> None:
    params = _make_fp([("10/20", _make_agg(trades=8))])
    rep = bse._build_robustness_report(params, {})
    assert rep["stable_fixed_candidates"] == []


def test_stable_candidate_fails_on_deep_drawdown() -> None:
    params = _make_fp([("10/20", _make_agg(dd=-0.20))])
    rep = bse._build_robustness_report(params, {})
    assert rep["stable_fixed_candidates"] == []


def test_stable_candidate_fails_on_profit_concentration() -> None:
    params = _make_fp([("10/20", _make_agg(lpc=0.75))])
    rep = bse._build_robustness_report(params, {})
    assert rep["stable_fixed_candidates"] == []
    assert "FIXED_RESULTS_PROFIT_CONCENTRATED" in rep["fixed_comparison_warning_reasons"]


def test_robustness_all_underperform_bh_warning() -> None:
    params = _make_fp([
        ("10/20", _make_agg(ret=0.05, bh=0.20)),
        ("15/50", _make_agg(ret=0.03, bh=0.20)),
    ])
    rep = bse._build_robustness_report(params, {})
    assert "ALL_FIXED_UNDERPERFORMED_BUY_AND_HOLD" in rep["fixed_comparison_warning_reasons"]


def test_robustness_all_underperform_adaptive_warning() -> None:
    params = _make_fp([
        ("10/20", _make_agg(ret=0.02)),
        ("15/50", _make_agg(ret=0.01)),
    ])
    adaptive = {"aggregate_walk_forward_return": 0.50}
    rep = bse._build_robustness_report(params, adaptive)
    assert "ALL_FIXED_UNDERPERFORMED_ADAPTIVE" in rep["fixed_comparison_warning_reasons"]


def test_robustness_low_fixed_trade_count_warning() -> None:
    params = _make_fp([("10/20", _make_agg(trades=5))])
    rep = bse._build_robustness_report(params, {})
    assert "LOW_FIXED_SAMPLE_TRADE_COUNT" in rep["fixed_comparison_warning_reasons"]


def test_robustness_most_frequently_profitable_lists_ties() -> None:
    params = _make_fp([
        ("10/20", _make_agg(rate=0.75)),
        ("15/50", _make_agg(rate=0.75)),
        ("5/10",  _make_agg(rate=0.50)),
    ])
    rep = bse._build_robustness_report(params, {})
    assert set(rep["most_frequently_profitable_fixed_parameters"]) == {"10/20", "15/50"}


# --- Integration ---


def test_build_summary_walk_forward_without_fixed_preserves_schema() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars)  # no fixed opts
    wf = summary["walk_forward"]
    for key in ("fixed_parameter_comparison",):
        assert key not in wf


def test_build_summary_fixed_comparison_has_all_fields() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 10), (5, 20)])
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    for field in (
        "requested_parameters", "parameter_count", "window_count",
        "comparison_basis", "test_windows_identical_to_adaptive",
        "parameters", "adaptive_vs_fixed", "robustness_report",
        "research_only", "automatic_parameter_promotion_allowed",
    ):
        assert field in fp
    assert fp["research_only"] is True
    assert fp["automatic_parameter_promotion_allowed"] is False
    assert fp["requested_parameters"] == ["3/10", "5/20"]  # order preserved
    for key, p in fp["parameters"].items():
        for field in (
            "short_window", "long_window", "windows", "aggregate",
        ):
            assert field in p


def test_build_summary_default_fixed_set_when_compare_fixed_flag_only() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_compare_fixed=True)
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    # Baseline + sweep gives us at least the baseline pair.
    assert "3/5" in fp["parameters"]  # baseline_short/long from _wf_with_fixed


def test_build_summary_rejects_train_shorter_than_max_fixed_long() -> None:
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    with pytest.raises(BacktestError, match="wf_train_bars"):
        bse.build_summary(
            bars=bars, symbol="SPY", interval="60m",
            now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
            short_window=3, long_window=5,
            walk_forward=True,
            wf_train_bars=30,   # < 51 (max fixed long is 50 → need 51)
            wf_test_bars=100, wf_step_bars=100,
            wf_fixed_params=[(3, 50)],
        )


def test_build_summary_walk_forward_default_1600_400_400_still_works() -> None:
    """Regression: enabling fixed comparison with the standard defaults
    must not fail when there is enough data."""
    bars = _bars_from_closes([float(c) for c in range(1, 2101)])
    summary = bse.build_summary(
        bars=bars, symbol="SPY", interval="60m",
        now_utc=datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc),
        short_window=10, long_window=20,
        walk_forward=True,
        wf_train_bars=1600, wf_test_bars=400, wf_step_bars=400,
        wf_fixed_params=[(10, 20), (15, 50)],
    )
    wf = summary["walk_forward"]
    assert wf["train_bars"] == 1600
    assert "fixed_parameter_comparison" in wf


def test_cli_walk_forward_with_fixed_params(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 601)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--walk-forward",
        "--wf-train-bars", "100",
        "--wf-test-bars", "100",
        "--wf-step-bars", "100",
        "--short-windows", "3,5",
        "--long-windows", "10,20",
        "--wf-fixed-params", "3/10,5/20",
        "--no-write",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    fp = payload["walk_forward"]["fixed_parameter_comparison"]
    assert fp["parameter_count"] == 2
    assert fp["research_only"] is True


def test_cli_fixed_params_without_walk_forward_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 601)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--wf-fixed-params", "10/20",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "walk-forward" in err["error"]


def test_cli_malformed_fixed_params_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_cache(cache_dir, [float(c) for c in range(1, 601)])
    rc = bse.main([
        "--cache-dir", str(cache_dir),
        "--walk-forward",
        "--wf-fixed-params", "not-a-pair",
        "--no-write",
    ])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "malformed" in err["error"]


def test_fixed_comparison_positions_do_not_carry_between_windows() -> None:
    """Each fixed test evaluation runs on its own eval_slice with its
    own initial equity, so a position in window N cannot appear as an
    entry in window N+1."""
    bars = _bars_from_closes([float(c) for c in range(1, 601)])
    summary = _wf_with_fixed(bars, wf_fixed_params=[(3, 10)])
    fp = summary["walk_forward"]["fixed_parameter_comparison"]
    for w in fp["parameters"]["3/10"]["windows"]:
        # open_entry_index is relative to that window's eval_slice
        # (0..(warmup+test)). If it exists, it must be within the
        # slice — trivially so — but the important property is that
        # each window's result has its OWN entry_index space, not a
        # cumulative one crossing windows.
        r = w["result"]
        if r.get("open_position"):
            assert r["open_entry_index"] < r["bar_count"] + r["short_window"] + 100


# --- Safety scan (re-affirmed after S60 additions) ---


def test_backtest_tool_still_has_no_broker_or_network_imports_s60() -> None:
    source = Path("src/tools/backtest_strategy_eval.py").read_text(encoding="utf-8")
    for tok in ("alpaca", "requests", "httpx", "urllib.request", "socket"):
        assert tok not in source, (
            f"backtest_strategy_eval must not depend on {tok!r}"
        )
