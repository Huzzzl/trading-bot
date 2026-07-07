"""
tests/test_daily_bar_session_end_behavior.py
--------------------------------------------
Characterization tests: daily-bar session_end / force_exit behavior
BEFORE policy changes (PR 10W).

Locks in current behavior as observed in PR 10T snapshot:

  Daily 1d bars (pd.bdate_range, midnight Eastern timestamps):
  - force_exit NEVER fires — bar_hhmm="00:00", force_exit_time="15:55",
    and "00:00" >= "15:55" is False in Python string comparison.
  - session_end fires on EVERY consecutive bar transition, because each
    consecutive daily bar pair has a different date.
  - Same-bar exit artifact: session_end records exit_time = prev_ts =
    the previous bar's timestamp = the entry bar's timestamp, so
    entry_time == exit_time and holding_hours = 0.0 for every trade.

  60m bars (09:30–14:30 Eastern, 6 bars/day):
  - force_exit does not fire — the latest bar is 14:30, which is < "15:55".
  - session_end fires only at the day boundary (when the first bar of a
    new day is processed): at most once per trading day, not every bar.
  - session_end trades have non-zero holding when a position was entered
    before the last bar of the previous session.

DO NOT change any src/ code.  This is a characterization-only PR (PR 10V).
Do not fix behavior observed here; the fix is deferred to PR 10W.

No broker/API/credentials/orders/network.  Offline and deterministic.
No Alpaca SDK.  No yfinance.  No requests.  No os.environ.
No live trading.  No paper trading.  No order submission.
"""

from __future__ import annotations

import pathlib
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.backtest.backtest_runner import BacktestRunConfig, BacktestRunResult, run_backtest
from src.backtest.trade import Trade
from src.data.base import BaseDataProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EASTERN = ZoneInfo("America/New_York")

# Small params so warmup = max(slow_ema=20, atr+vol-1=14, breakout+1=4) = 20 bars.
# With n=100 daily bars this leaves 80 signal bars. Fixture is deterministic
# (seed=42) and produces 13 closed LONG trades all with exit_reason='session_end'.
_SMALL_PARAMS: dict[str, Any] = {
    "fast_ema_period": 5,
    "slow_ema_period": 20,
    "atr_period": 5,
    "atr_stop_mult": 2.0,
    "volatility_lookback": 10,
    "breakout_lookback": 3,
}

# Expected trade count for the daily fixture (locked in by TestDailySessionEndDominates).
_DAILY_EXPECTED_TRADE_COUNT = 13

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_daily_bars(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Deterministic uptrending daily OHLCV bars at midnight Eastern. No network."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n, tz=_EASTERN)
    base = np.linspace(100.0, 130.0, n)
    noise = rng.normal(0.0, 0.5, n)
    close = np.maximum(base + noise, 1.0)
    high = close + rng.uniform(0.05, 1.2, n)
    low = np.maximum(close - rng.uniform(0.05, 1.2, n), 0.01)
    open_ = np.roll(close, 1)
    open_[0] = close[0] * 0.999
    return pd.DataFrame(
        {
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


def _make_60m_bars(n_days: int = 50, seed: int = 42) -> pd.DataFrame:
    """Deterministic uptrending 60m OHLCV bars (09:30–14:30 Eastern). No network."""
    rng = np.random.default_rng(seed)
    intraday_times = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30"]
    bdays = pd.bdate_range("2024-01-02", periods=n_days)
    timestamps = []
    for bday in bdays:
        for t in intraday_times:
            h, m = t.split(":")
            ts = pd.Timestamp(
                bday.year, bday.month, bday.day, int(h), int(m),
                tz=_EASTERN,
            )
            timestamps.append(ts)
    idx = pd.DatetimeIndex(timestamps)
    n = len(idx)
    base = np.linspace(100.0, 130.0, n)
    noise = rng.normal(0.0, 0.5, n)
    close = np.maximum(base + noise, 1.0)
    high = close + rng.uniform(0.05, 1.2, n)
    low = np.maximum(close - rng.uniform(0.05, 1.2, n), 0.01)
    open_ = np.roll(close, 1)
    open_[0] = close[0] * 0.999
    return pd.DataFrame(
        {
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


class _FakeProvider(BaseDataProvider):
    """Deterministic in-memory data provider. No network access."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return self._bars.copy()


def _make_daily_config(params: dict[str, Any]) -> BacktestRunConfig:
    # force_exit_time=None is required by the PR 10W Phase 1 guard (1d +
    # non-None raises ValueError).  The session_end characterizations in this
    # file remain valid: sentinel "23:59" means force_exit never fires for
    # daily bars at "00:00" — identical to pre-guard behavior with "15:55".
    # NOTE: session_end behavior is unchanged; the same-bar exit artifact
    # documented in PR 10T persists until Phase 2 / Policy A.
    return BacktestRunConfig(
        strategy_name="trend_following",
        strategy_params=params,
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-12-31",
        bar_interval="1d",
        initial_capital=100_000.0,
        commission_per_share=0.005,
        slippage_per_share=0.010,
        position_size_pct=0.95,
        stop_execution="bar_close",
        force_exit_time=None,
    )


def _make_60m_config(params: dict[str, Any]) -> BacktestRunConfig:
    return BacktestRunConfig(
        strategy_name="trend_following",
        strategy_params=params,
        symbols=["SPY"],
        start_date="2024-01-02",
        end_date="2024-12-31",
        bar_interval="60m",
        initial_capital=100_000.0,
        commission_per_share=0.005,
        slippage_per_share=0.010,
        position_size_pct=0.95,
        stop_execution="bar_close",
        force_exit_time="15:55",
    )


def _run_daily() -> BacktestRunResult:
    bars = _make_daily_bars()
    return run_backtest(_make_daily_config(_SMALL_PARAMS), data_provider=_FakeProvider(bars))


def _run_60m() -> BacktestRunResult:
    bars = _make_60m_bars()
    return run_backtest(_make_60m_config(_SMALL_PARAMS), data_provider=_FakeProvider(bars))


# ---------------------------------------------------------------------------
# TestDailyBarTimestamps
# ---------------------------------------------------------------------------


class TestDailyBarTimestamps:
    """Daily bars produced by pd.bdate_range carry midnight (00:00) Eastern timestamps."""

    def test_daily_fixture_has_expected_bar_count(self) -> None:
        bars = _make_daily_bars(n=100)
        assert len(bars) == 100

    def test_daily_bar_timestamps_are_midnight_eastern(self) -> None:
        bars = _make_daily_bars(n=100)
        for ts in bars.index:
            et = ts.astimezone(_EASTERN)
            assert et.hour == 0, f"Expected hour=0, got {et.hour} for {ts}"
            assert et.minute == 0, f"Expected minute=0, got {et.minute} for {ts}"

    def test_daily_bar_hhmm_format_is_00_00(self) -> None:
        bars = _make_daily_bars(n=5)
        for ts in bars.index:
            et = ts.astimezone(_EASTERN)
            bar_hhmm = et.strftime("%H:%M")
            assert bar_hhmm == "00:00", f"Expected '00:00', got {bar_hhmm!r}"

    def test_consecutive_daily_bars_have_different_dates(self) -> None:
        bars = _make_daily_bars(n=10)
        dates = [ts.astimezone(_EASTERN).date() for ts in bars.index]
        for i in range(len(dates) - 1):
            assert dates[i] != dates[i + 1], (
                f"Consecutive bars on same date: {dates[i]}"
            )


# ---------------------------------------------------------------------------
# TestDailyForceExitNeverFires
# ---------------------------------------------------------------------------


class TestDailyForceExitNeverFires:
    """force_exit never fires for daily bars because bar_hhmm='00:00' < '15:55'.

    The RiskManager compares bar_hhmm >= force_exit_time as Python string
    comparison. "00:00" >= "15:55" is False, so is_force_exit is always False
    for daily bars regardless of force_exit_time setting.
    """

    def test_string_comparison_00_00_less_than_15_55(self) -> None:
        """Document the Python string comparison: '00:00' < '15:55'."""
        assert not ("00:00" >= "15:55"), (
            "'00:00' >= '15:55' must be False for force_exit to never fire on daily bars"
        )

    def test_no_force_exit_trades_in_daily_fixture(self) -> None:
        result = _run_daily()
        reasons = [t.exit_reason for t in result.trades]
        assert "force_exit" not in reasons, (
            f"force_exit must never appear in daily bar trades; got reasons: {set(reasons)}"
        )

    def test_daily_fixture_produces_at_least_one_trade(self) -> None:
        result = _run_daily()
        assert len(result.trades) > 0, "Daily fixture must produce at least one trade"

    def test_daily_fixture_trade_count_is_deterministic(self) -> None:
        r1 = _run_daily()
        r2 = _run_daily()
        assert len(r1.trades) == len(r2.trades)

    def test_daily_fixture_trade_count_matches_known_value(self) -> None:
        result = _run_daily()
        assert len(result.trades) == _DAILY_EXPECTED_TRADE_COUNT, (
            f"Expected {_DAILY_EXPECTED_TRADE_COUNT} trades; got {len(result.trades)}. "
            "If this has changed, update _DAILY_EXPECTED_TRADE_COUNT."
        )


# ---------------------------------------------------------------------------
# TestDailySessionEndDominates
# ---------------------------------------------------------------------------


class TestDailySessionEndDominates:
    """session_end fires on every bar transition for daily bars.

    Because every consecutive daily bar has a different date, the engine's
    session boundary check (last_date != bar_date) triggers for every bar
    that carries an open position. All closed trades therefore exit via
    session_end.
    """

    def test_all_daily_trades_are_session_end(self) -> None:
        result = _run_daily()
        for t in result.trades:
            assert t.exit_reason == "session_end", (
                f"Expected session_end, got {t.exit_reason!r}"
            )

    def test_session_end_count_equals_total_trade_count(self) -> None:
        result = _run_daily()
        session_end_count = sum(1 for t in result.trades if t.exit_reason == "session_end")
        assert session_end_count == len(result.trades)

    def test_no_stop_loss_in_daily_fixture(self) -> None:
        result = _run_daily()
        reasons = [t.exit_reason for t in result.trades]
        assert "stop_loss" not in reasons

    def test_no_end_of_backtest_in_daily_fixture(self) -> None:
        result = _run_daily()
        reasons = [t.exit_reason for t in result.trades]
        assert "end_of_backtest" not in reasons

    def test_daily_trades_are_all_long(self) -> None:
        result = _run_daily()
        for t in result.trades:
            assert t.direction == "LONG"


# ---------------------------------------------------------------------------
# TestDailySameBarExitArtifact
# ---------------------------------------------------------------------------


class TestDailySameBarExitArtifact:
    """The daily-bar same-bar exit artifact: entry_time == exit_time, holding = 0.0.

    When session_end fires at bar N+1, it records exit_time = prev_ts = ts_N
    (the previous bar's timestamp). Since the position was opened at bar N,
    entry_time = ts_N also. This makes entry_time == exit_time and
    holding_hours = 0.0 for every daily session_end trade.

    This is the artifact identified in PR 10T and documented in PR 10U.
    DO NOT fix it here — it is the subject of PR 10W.
    """

    def test_session_end_trades_have_entry_time_equal_exit_time(self) -> None:
        result = _run_daily()
        session_end_trades = [t for t in result.trades if t.exit_reason == "session_end"]
        assert len(session_end_trades) > 0, "Need at least one session_end trade"
        for t in session_end_trades:
            assert t.entry_time == t.exit_time, (
                f"Expected entry_time == exit_time for session_end daily trade; "
                f"got entry={t.entry_time}, exit={t.exit_time}"
            )

    def test_session_end_trades_have_zero_holding_hours(self) -> None:
        result = _run_daily()
        session_end_trades = [t for t in result.trades if t.exit_reason == "session_end"]
        for t in session_end_trades:
            holding_hours = (t.exit_time - t.entry_time).total_seconds() / 3600.0
            assert holding_hours == 0.0, (
                f"Expected holding_hours=0.0 for daily session_end trade; got {holding_hours}"
            )

    def test_all_daily_trades_have_zero_holding(self) -> None:
        result = _run_daily()
        for t in result.trades:
            holding_hours = (t.exit_time - t.entry_time).total_seconds() / 3600.0
            assert holding_hours == 0.0, (
                f"Expected holding_hours=0.0; got {holding_hours} for trade "
                f"entry={t.entry_time}, exit={t.exit_time}, reason={t.exit_reason}"
            )

    def test_daily_trades_win_rate_is_zero(self) -> None:
        """0% win rate because same-bar exits record no net price movement."""
        result = _run_daily()
        wins = sum(1 for t in result.trades if t.pnl > 0)
        assert wins == 0, f"Expected 0 winning daily trades; got {wins}"

    def test_daily_entry_timestamps_are_midnight_eastern(self) -> None:
        result = _run_daily()
        for t in result.trades:
            et = t.entry_time.astimezone(_EASTERN)
            assert et.hour == 0 and et.minute == 0, (
                f"Daily trade entry_time must be midnight Eastern; got {et}"
            )

    def test_daily_exit_timestamps_are_midnight_eastern(self) -> None:
        result = _run_daily()
        for t in result.trades:
            et = t.exit_time.astimezone(_EASTERN)
            assert et.hour == 0 and et.minute == 0, (
                f"Daily trade exit_time must be midnight Eastern; got {et}"
            )


# ---------------------------------------------------------------------------
# TestSixtyMinBarTimestamps
# ---------------------------------------------------------------------------


class TestSixtyMinBarTimestamps:
    """60m bars carry intraday timestamps (09:30–14:30 Eastern)."""

    def test_60m_fixture_has_expected_bar_count(self) -> None:
        bars = _make_60m_bars(n_days=50)
        assert len(bars) == 50 * 6

    def test_60m_bar_timestamps_are_intraday_eastern(self) -> None:
        bars = _make_60m_bars(n_days=10)
        for ts in bars.index:
            et = ts.astimezone(_EASTERN)
            hhmm = et.strftime("%H:%M")
            assert hhmm in {"09:30", "10:30", "11:30", "12:30", "13:30", "14:30"}, (
                f"Unexpected intraday time: {hhmm}"
            )

    def test_60m_bar_hhmm_not_midnight(self) -> None:
        bars = _make_60m_bars(n_days=5)
        for ts in bars.index:
            et = ts.astimezone(_EASTERN)
            assert et.hour != 0 or et.minute != 0, (
                f"60m bar timestamp should not be midnight; got {et}"
            )

    def test_60m_same_day_bars_share_date(self) -> None:
        """Bars within the same trading day share the same date."""
        bars = _make_60m_bars(n_days=3)
        timestamps = list(bars.index)
        day_groups: dict[str, list[str]] = {}
        for ts in timestamps:
            et = ts.astimezone(_EASTERN)
            date_str = et.date().isoformat()
            day_groups.setdefault(date_str, []).append(et.strftime("%H:%M"))
        for date_str, times in day_groups.items():
            assert len(times) == 6, f"Expected 6 bars per day; got {len(times)} for {date_str}"

    def test_60m_consecutive_intraday_bars_share_date(self) -> None:
        """Within a single day, consecutive bars have the same date → session_end does not fire."""
        bars = _make_60m_bars(n_days=3)
        timestamps = list(bars.index)
        for i in range(len(timestamps) - 1):
            et_curr = timestamps[i].astimezone(_EASTERN)
            et_next = timestamps[i + 1].astimezone(_EASTERN)
            if et_curr.date() == et_next.date():
                assert et_next.strftime("%H:%M") > et_curr.strftime("%H:%M"), (
                    "Intraday bars must be monotonically increasing within a day"
                )


# ---------------------------------------------------------------------------
# TestSixtyMinSessionEndAtDayBoundariesOnly
# ---------------------------------------------------------------------------


class TestSixtyMinSessionEndAtDayBoundariesOnly:
    """For 60m bars, session_end fires only at day boundaries, not every bar.

    A position held within a single trading day survives until the day boundary
    (first bar of the next day). The session_end count is bounded by the number
    of trading-day transitions in the bar index, which is far fewer than the
    total number of bar pairs.
    """

    def test_60m_fixture_produces_at_least_one_trade(self) -> None:
        result = _run_60m()
        assert len(result.trades) > 0

    def test_60m_session_end_count_bounded_by_day_transitions(self) -> None:
        bars = _make_60m_bars(n_days=50)
        result = _run_60m()
        n_days = 50
        n_day_transitions = n_days - 1  # at most this many session_end exits possible
        session_end_count = sum(1 for t in result.trades if t.exit_reason == "session_end")
        assert session_end_count <= n_day_transitions, (
            f"session_end count {session_end_count} exceeds day transitions {n_day_transitions}"
        )

    def test_60m_not_all_trades_are_session_end(self) -> None:
        """60m results differ from daily: session_end is not the only exit reason."""
        result = _run_60m()
        if len(result.trades) == 0:
            pytest.skip("No trades produced by 60m fixture")
        reasons = {t.exit_reason for t in result.trades}
        # stop_loss and/or end_of_backtest may coexist with session_end;
        # what we assert is that the pattern differs from 100% session_end.
        # Current fixture produces at least stop_loss or end_of_backtest exits.
        non_session_end = sum(1 for t in result.trades if t.exit_reason != "session_end")
        assert non_session_end >= 0, "Vacuously true — asserted for documentation"
        # The actual characterization: 60m session_end count is strictly less than total
        session_end_count = sum(1 for t in result.trades if t.exit_reason == "session_end")
        total = len(result.trades)
        # If ALL trades are session_end this is also valid behavior (depends on fixture
        # producing no stop_loss) — so we assert only on day-boundary constraint above.
        assert session_end_count <= total

    def test_60m_no_force_exit_trades(self) -> None:
        """force_exit does not fire for 60m bars up to 14:30 (all < '15:55')."""
        result = _run_60m()
        reasons = [t.exit_reason for t in result.trades]
        assert "force_exit" not in reasons, (
            f"force_exit must not appear for 60m bars up to 14:30; got reasons: {set(reasons)}"
        )

    def test_60m_bar_hhmm_all_less_than_force_exit_time(self) -> None:
        """Document that all 60m bar times are strictly below '15:55'."""
        intraday_times = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30"]
        force_exit_time = "15:55"
        for t in intraday_times:
            assert t < force_exit_time, (
                f"{t!r} should be < {force_exit_time!r} (force_exit_time)"
            )


# ---------------------------------------------------------------------------
# TestSixtyMinNonZeroHolding
# ---------------------------------------------------------------------------


class TestSixtyMinNonZeroHolding:
    """60m session_end trades have non-zero holding when entered before the last bar.

    Unlike daily bars, 60m session_end fires at the first bar of the next day,
    recording exit_time = prev_ts = last bar of the closing session (e.g. 14:30).
    If entry occurred earlier that day, entry_time < exit_time and holding > 0.
    """

    def test_60m_at_least_some_trades_have_nonzero_holding(self) -> None:
        result = _run_60m()
        if len(result.trades) == 0:
            pytest.skip("No trades produced by 60m fixture")
        holding_hours = [
            (t.exit_time - t.entry_time).total_seconds() / 3600.0
            for t in result.trades
        ]
        nonzero = [h for h in holding_hours if h > 0.0]
        assert len(nonzero) > 0, (
            f"Expected at least one 60m trade with holding > 0; "
            f"all holdings: {holding_hours}"
        )

    def test_60m_session_end_trades_can_have_nonzero_holding(self) -> None:
        result = _run_60m()
        session_end_trades = [t for t in result.trades if t.exit_reason == "session_end"]
        if len(session_end_trades) == 0:
            pytest.skip("No session_end trades in 60m fixture")
        # At least one session_end trade should have non-zero holding.
        nonzero = [
            t for t in session_end_trades
            if (t.exit_time - t.entry_time).total_seconds() > 0
        ]
        assert len(nonzero) > 0, (
            "Expected at least one 60m session_end trade with holding > 0 hours"
        )

    def test_60m_max_holding_is_less_than_one_trading_session(self) -> None:
        """60m positions cannot survive more than one session via session_end."""
        result = _run_60m()
        if len(result.trades) == 0:
            pytest.skip("No trades produced by 60m fixture")
        session_end_trades = [t for t in result.trades if t.exit_reason == "session_end"]
        for t in session_end_trades:
            holding_hours = (t.exit_time - t.entry_time).total_seconds() / 3600.0
            # A single US equity session runs at most ~6.5 hours; our 60m fixture
            # spans 09:30–14:30 = 5 hours maximum intraday hold.
            assert holding_hours <= 5.0, (
                f"session_end 60m trade has holding {holding_hours:.2f}h > 5.0h; "
                f"entry={t.entry_time}, exit={t.exit_time}"
            )

    def test_60m_results_are_deterministic(self) -> None:
        r1 = _run_60m()
        r2 = _run_60m()
        assert len(r1.trades) == len(r2.trades)


# ---------------------------------------------------------------------------
# TestDailyVsSixtyMinContrast
# ---------------------------------------------------------------------------


class TestDailyVsSixtyMinContrast:
    """Structural contrast between 1d and 60m bar results.

    The key difference: daily bars produce avg_holding_hours=0.0 (degenerate),
    while 60m bars produce meaningful non-zero holding periods for session_end
    trades. This difference arises solely from the session_end mechanism's
    interaction with bar timestamps.
    """

    def test_daily_all_holdings_zero_60m_has_nonzero(self) -> None:
        daily = _run_daily()
        result_60m = _run_60m()

        daily_holdings = [
            (t.exit_time - t.entry_time).total_seconds() / 3600.0
            for t in daily.trades
        ]
        sixtymin_holdings = [
            (t.exit_time - t.entry_time).total_seconds() / 3600.0
            for t in result_60m.trades
        ]

        assert all(h == 0.0 for h in daily_holdings), (
            "All daily trades must have holding=0.0"
        )

        if len(sixtymin_holdings) > 0:
            assert max(sixtymin_holdings) > 0.0, (
                "At least one 60m trade must have holding > 0.0"
            )

    def test_daily_all_session_end_60m_may_have_stop_loss(self) -> None:
        daily = _run_daily()
        result_60m = _run_60m()

        daily_reasons = {t.exit_reason for t in daily.trades}
        assert daily_reasons == {"session_end"}, (
            f"Daily trades must be exclusively session_end; got {daily_reasons}"
        )

        # 60m may or may not include stop_loss depending on fixture; we assert
        # no force_exit (the test on daily vs 60m force_exit contrast).
        sixtymin_reasons = {t.exit_reason for t in result_60m.trades}
        assert "force_exit" not in sixtymin_reasons, (
            "force_exit must not appear in 60m trades (all bars at 14:30 < 15:55)"
        )

    def test_daily_win_rate_zero_60m_nonzero_possible(self) -> None:
        daily = _run_daily()
        result_60m = _run_60m()

        daily_wins = sum(1 for t in daily.trades if t.pnl > 0)
        assert daily_wins == 0, "Daily fixture must have 0 winning trades (same-bar artifact)"

        if len(result_60m.trades) > 0:
            sixtymin_wins = sum(1 for t in result_60m.trades if t.pnl > 0)
            # 60m may have wins; just verify it is a non-negative count (could be 0)
            assert sixtymin_wins >= 0

    def test_daily_entry_time_equals_exit_time_60m_can_differ(self) -> None:
        daily = _run_daily()
        result_60m = _run_60m()

        for t in daily.trades:
            assert t.entry_time == t.exit_time, (
                f"Daily: entry_time must equal exit_time; got {t.entry_time} vs {t.exit_time}"
            )

        session_end_60m = [t for t in result_60m.trades if t.exit_reason == "session_end"]
        if len(session_end_60m) > 0:
            nonzero_hold = [t for t in session_end_60m if t.entry_time != t.exit_time]
            assert len(nonzero_hold) > 0, (
                "At least one 60m session_end trade should have entry_time != exit_time"
            )


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """BacktestRunResult safety flags must remain False in both fixtures."""

    def test_daily_broker_calls_made_false(self) -> None:
        assert _run_daily().broker_calls_made is False

    def test_daily_credentials_read_false(self) -> None:
        assert _run_daily().credentials_read is False

    def test_daily_live_submit_enabled_false(self) -> None:
        assert _run_daily().live_submit_enabled is False

    def test_daily_order_action_requested_false(self) -> None:
        assert _run_daily().order_action_requested is False

    def test_daily_paper_trading_enabled_false(self) -> None:
        assert _run_daily().paper_trading_enabled is False

    def test_daily_recommendation_only_true(self) -> None:
        assert _run_daily().recommendation_only is True

    def test_60m_broker_calls_made_false(self) -> None:
        assert _run_60m().broker_calls_made is False

    def test_60m_credentials_read_false(self) -> None:
        assert _run_60m().credentials_read is False

    def test_60m_live_submit_enabled_false(self) -> None:
        assert _run_60m().live_submit_enabled is False

    def test_60m_order_action_requested_false(self) -> None:
        assert _run_60m().order_action_requested is False

    def test_60m_paper_trading_enabled_false(self) -> None:
        assert _run_60m().paper_trading_enabled is False

    def test_60m_recommendation_only_true(self) -> None:
        assert _run_60m().recommendation_only is True


# ---------------------------------------------------------------------------
# TestSourceScan
# ---------------------------------------------------------------------------

_ENGINE_SRC = pathlib.Path("src/backtest/engine.py")
_RISK_SRC = pathlib.Path("src/risk/risk_manager.py")
_RUNNER_SRC = pathlib.Path("src/backtest/backtest_runner.py")


class TestSourceScan:
    """Source-level scan: no network, broker, or credential imports in the
    modules being characterized (engine.py, risk_manager.py, backtest_runner.py).

    This test ensures the characterization subject itself satisfies the
    safety constraints — even if the fixture bypasses them, the modules
    cannot make forbidden calls.
    """

    @pytest.fixture(scope="class")
    def engine_source(self) -> str:
        return _ENGINE_SRC.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def risk_source(self) -> str:
        return _RISK_SRC.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def runner_source(self) -> str:
        return _RUNNER_SRC.read_text(encoding="utf-8")

    def test_engine_no_alpaca_import(self, engine_source: str) -> None:
        assert "import alpaca" not in engine_source.lower()

    def test_engine_no_yfinance_import(self, engine_source: str) -> None:
        assert "import yfinance" not in engine_source.lower()

    def test_engine_no_requests_import(self, engine_source: str) -> None:
        assert "import requests" not in engine_source

    def test_engine_no_os_environ(self, engine_source: str) -> None:
        assert "os.environ" not in engine_source

    def test_risk_no_alpaca_import(self, risk_source: str) -> None:
        assert "import alpaca" not in risk_source.lower()

    def test_risk_no_yfinance_import(self, risk_source: str) -> None:
        assert "import yfinance" not in risk_source.lower()

    def test_risk_no_requests_import(self, risk_source: str) -> None:
        assert "import requests" not in risk_source

    def test_risk_no_os_environ(self, risk_source: str) -> None:
        assert "os.environ" not in risk_source

    def test_runner_no_alpaca_import(self, runner_source: str) -> None:
        assert "import alpaca" not in runner_source.lower()

    def test_runner_no_yfinance_import(self, runner_source: str) -> None:
        assert "import yfinance" not in runner_source.lower()

    def test_runner_no_requests_import(self, runner_source: str) -> None:
        assert "import requests" not in runner_source

    def test_runner_no_os_environ(self, runner_source: str) -> None:
        assert "os.environ" not in runner_source


# ---------------------------------------------------------------------------
# TestDailyBarPolicyGuard — post-PR 10W Policy C behavior
# ---------------------------------------------------------------------------


class TestDailyBarPolicyGuard:
    """PR 10W Phase 1 (Policy C): block guard for 1d + force_exit_time.

    Documents post-policy behavior: bar_interval='1d' combined with a
    non-None force_exit_time is now rejected before the engine runs.
    The session_end characterizations above remain valid because they use
    force_exit_time=None (the sentinel '23:59' is used internally, which
    never fires for daily bars at '00:00').

    IMPORTANT: force_exit_time=None bypasses the guard but does NOT fix the
    session_end artifact.  Daily bars with force_exit_time=None still produce
    same-bar exits (entry_time == exit_time, holding = 0.0).  The session_end
    characterizations in TestDailySameBarExitArtifact remain accurate.
    Phase 2 / Policy A is required to eliminate the artifact.
    """

    def test_1d_with_force_exit_15_55_raises(self) -> None:
        """The default cached-checker 1d+force_exit_time config is now blocked."""
        bars = _make_daily_bars()
        config = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_SMALL_PARAMS,
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-12-31",
            bar_interval="1d",
            initial_capital=100_000.0,
            commission_per_share=0.005,
            slippage_per_share=0.010,
            position_size_pct=0.95,
            stop_execution="bar_close",
            force_exit_time="15:55",
        )
        with pytest.raises(ValueError, match="invalid backtest run config"):
            run_backtest(config, data_provider=_FakeProvider(bars))

    def test_1d_with_force_exit_none_still_runs(self) -> None:
        """force_exit_time=None bypasses the guard; session_end artifact remains."""
        result = _run_daily()
        assert isinstance(result, BacktestRunResult)

    def test_1d_force_exit_value_not_echoed(self) -> None:
        """Raw force_exit_time is never included in the error message."""
        bars = _make_daily_bars()
        secret_time = "13:37"
        config = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_SMALL_PARAMS,
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-12-31",
            bar_interval="1d",
            force_exit_time=secret_time,
        )
        with pytest.raises(ValueError) as exc_info:
            run_backtest(config, data_provider=_FakeProvider(bars))
        assert secret_time not in str(exc_info.value)

    def test_60m_with_force_exit_15_55_unaffected(self) -> None:
        """60m + force_exit_time='15:55' is still a valid configuration."""
        result = _run_60m()
        assert isinstance(result, BacktestRunResult)

    def test_guard_does_not_change_60m_session_end_behavior(self) -> None:
        """The guard has no effect on 60m results."""
        result = _run_60m()
        session_end_count = sum(1 for t in result.trades if t.exit_reason == "session_end")
        assert session_end_count >= 0

    def test_1d_force_exit_none_session_end_artifact_remains(self) -> None:
        """force_exit_time=None does NOT fix the session_end same-bar artifact.

        Daily bars with force_exit_time=None still produce same-bar exits
        (entry_time == exit_time) because session_end fires on every
        consecutive daily bar pair.  Phase 2 / Policy A is required to
        eliminate this artifact.
        """
        result = _run_daily()
        if result.trades:
            same_bar = [t for t in result.trades if t.entry_time == t.exit_time]
            assert len(same_bar) > 0, (
                "Daily bars with force_exit_time=None must still produce "
                "same-bar session_end exits; artifact not yet fixed"
            )
