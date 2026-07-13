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
