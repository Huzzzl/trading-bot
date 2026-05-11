"""
tests/test_strategy.py
-----------------------
Unit tests for the Opening Range Breakout strategy.
Uses stdlib unittest only — no pytest required.
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.strategy.opening_range_breakout import OpeningRangeBreakout
from src.strategy.base import SignalDirection

_EASTERN = ZoneInfo("America/New_York")
DEFAULT_PARAMS = {
    "opening_range_start": "09:30", "opening_range_end": "10:00",
    "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
}

def _bar(date, time, o, h, l, c):
    ts = pd.Timestamp(f"{date} {time}", tz=_EASTERN)
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1_000_000}

def _make_bars(rows):
    df = pd.DataFrame(rows).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index)
    return df[["open", "high", "low", "close", "volume"]]


class TestRangeFormation(unittest.TestCase):
    def test_no_signal_during_range(self):
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)
        rows = [_bar("2024-01-02", t, 480, 490, 475, 485)
                for t in ["09:30","09:35","09:40","09:45","09:50","09:55"]]
        bars = _make_bars(rows)
        for ts in bars.index:
            self.assertIsNone(strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts),
                              f"Unexpected signal at {ts}")

    def test_breakout_at_10_00_fires_signal(self):
        """10:00 is the FIRST post-range bar (range=[09:30,10:00) exclusive).
        A close above or_high at 10:00 IS a valid breakout."""
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)
        rows = [
            _bar("2024-01-02", "09:30", 480, 485, 475, 482),  # or_high=485 or_low=475
            _bar("2024-01-02", "10:00", 484, 495, 483, 493),  # close=493 > 485 → LONG
        ]
        bars = _make_bars(rows)
        sig = strat.generate_signal("SPY", bars, bars.index[-1])
        self.assertIsNotNone(sig, "Expected LONG signal on first post-range bar")

    def test_no_signal_at_10_00_when_close_below_or_high(self):
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)
        rows = [
            _bar("2024-01-02", "09:30", 480, 485, 475, 482),  # or_high=485
            _bar("2024-01-02", "10:00", 484, 486, 483, 484),  # close=484 < 485 → no signal
        ]
        bars = _make_bars(rows)
        sig = strat.generate_signal("SPY", bars, bars.index[-1])
        self.assertIsNone(sig)


class TestBreakout(unittest.TestCase):
    def _base(self):
        return [
            _bar("2024-01-02", "09:30", 480, 485, 475, 482),
            _bar("2024-01-02", "09:35", 482, 486, 480, 484),
            _bar("2024-01-02", "09:50", 487, 490, 485, 488),  # or_high=490, or_low=475
            _bar("2024-01-02", "10:00", 489, 491, 487, 490),  # range formed
        ]

    def _run(self, rows):
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)
        bars = _make_bars(rows)
        sig = None
        for ts in bars.index:
            s = strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts)
            if s: sig = s
        return sig

    def test_breakout_fires_long(self):
        rows = self._base() + [_bar("2024-01-02", "10:05", 490, 496, 489, 495)]
        sig = self._run(rows)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.direction, SignalDirection.LONG)

    def test_stop_is_or_low(self):
        rows = self._base() + [_bar("2024-01-02", "10:05", 490, 496, 489, 495)]
        sig = self._run(rows)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.stop_loss, 475.0)

    def test_no_signal_if_close_below_or_high(self):
        rows = self._base() + [_bar("2024-01-02", "10:05", 490, 492, 488, 489)]
        self.assertIsNone(self._run(rows))

    def test_no_signal_at_force_exit(self):
        rows = self._base() + [
            _bar("2024-01-02", "10:05", 489, 491, 487, 490),
            _bar("2024-01-02", "15:55", 490, 502, 489, 501),  # huge close but too late
        ]
        sig = self._run(rows)
        self.assertIsNone(sig)

    def test_no_lookahead_bias(self):
        """or_high computed from [09:30,10:00); future high of 999 must not affect it."""
        rows = [
            _bar("2024-01-02", "09:30", 480, 490, 475, 482),  # or_high=490
            _bar("2024-01-02", "09:55", 488, 490, 487, 489),
            _bar("2024-01-02", "10:05", 490, 999, 489, 495),  # high=999 in future
        ]
        sig = self._run(rows)
        self.assertIsNotNone(sig, "Expected breakout signal (495 > 490)")
        self.assertAlmostEqual(sig.meta["or_high"], 490.0)


class TestSymbolIsolation(unittest.TestCase):
    """SPY and QQQ must maintain independent opening ranges on the same date."""

    def test_spy_and_qqq_have_separate_ranges(self):
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)
        date = "2024-01-02"

        # SPY opening range: high=500, low=490
        spy_rows = [
            _bar(date, "09:30", 492, 500, 490, 495),
            _bar(date, "09:35", 495, 498, 492, 496),
        ]
        # QQQ opening range: high=420, low=410 — completely different
        qqq_rows = [
            _bar(date, "09:30", 412, 420, 410, 415),
            _bar(date, "09:35", 415, 418, 412, 416),
        ]

        spy_bars = _make_bars(spy_rows)
        qqq_bars = _make_bars(qqq_rows)

        # Feed range bars to strategy for each symbol
        for ts in spy_bars.index:
            strat.generate_signal("SPY", spy_bars.loc[spy_bars.index <= ts], ts)
        for ts in qqq_bars.index:
            strat.generate_signal("QQQ", qqq_bars.loc[qqq_bars.index <= ts], ts)

        spy_key = ("SPY", date)
        qqq_key = ("QQQ", date)

        self.assertIn(spy_key, strat._daily_range)
        self.assertIn(qqq_key, strat._daily_range)

        spy_high = strat._daily_range[spy_key]["high"]
        qqq_high = strat._daily_range[qqq_key]["high"]

        self.assertAlmostEqual(spy_high, 500.0, msg="SPY or_high should be 500")
        self.assertAlmostEqual(qqq_high, 420.0, msg="QQQ or_high should be 420")
        self.assertNotAlmostEqual(spy_high, qqq_high,
                                  msg="SPY and QQQ must not share the same opening range")


class TestBreakoutTrigger(unittest.TestCase):
    """breakout_trigger='close' vs 'high' behave differently."""

    def _base_rows(self, date="2024-01-02"):
        # or_high=490, or_low=475 after range bars
        return [
            _bar(date, "09:30", 480, 485, 475, 482),
            _bar(date, "09:35", 482, 490, 480, 484),
            _bar(date, "10:00", 489, 491, 487, 490),  # range formed (10:00 is post-range)
        ]

    def test_close_trigger_fires_on_close_above_or_high(self):
        params = {**DEFAULT_PARAMS, "breakout_trigger": "close"}
        strat = OpeningRangeBreakout(params)
        # bar high = 496 > 490, close = 492 > 490 → should fire
        rows = self._base_rows() + [_bar("2024-01-02", "10:05", 490, 496, 488, 492)]
        bars = _make_bars(rows)
        sig = None
        for ts in bars.index:
            s = strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts)
            if s:
                sig = s
        self.assertIsNotNone(sig, "Expected LONG signal when close > or_high")
        self.assertEqual(sig.meta["breakout_trigger"], "close")

    def test_close_trigger_no_signal_when_only_high_breaks(self):
        params = {**DEFAULT_PARAMS, "breakout_trigger": "close"}
        strat = OpeningRangeBreakout(params)
        # bar high = 495 > 490, but close = 488 < 490 → must NOT fire
        rows = self._base_rows() + [_bar("2024-01-02", "10:05", 490, 495, 487, 488)]
        bars = _make_bars(rows)
        sig = None
        for ts in bars.index:
            s = strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts)
            if s:
                sig = s
        self.assertIsNone(sig, "close trigger must not fire when only high breaks out")

    def test_high_trigger_fires_when_high_breaks_even_if_close_below(self):
        params = {**DEFAULT_PARAMS, "breakout_trigger": "high"}
        strat = OpeningRangeBreakout(params)
        # bar high = 495 > 490, close = 488 < 490 → should fire under 'high' trigger
        rows = self._base_rows() + [_bar("2024-01-02", "10:05", 490, 495, 487, 488)]
        bars = _make_bars(rows)
        sig = None
        for ts in bars.index:
            s = strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts)
            if s:
                sig = s
        self.assertIsNotNone(sig, "high trigger must fire when bar high > or_high")
        self.assertEqual(sig.meta["breakout_trigger"], "high")

    def test_invalid_trigger_raises(self):
        with self.assertRaises(ValueError):
            OpeningRangeBreakout({**DEFAULT_PARAMS, "breakout_trigger": "vwap"})


class TestReset(unittest.TestCase):
    """Running the same strategy instance twice must not reuse stale state."""

    def _run_once(self, strat, date="2024-01-02"):
        rows = [
            _bar(date, "09:30", 480, 485, 475, 482),
            _bar(date, "09:35", 482, 490, 480, 484),
            _bar(date, "10:00", 489, 491, 487, 490),
            _bar(date, "10:05", 490, 496, 488, 492),
        ]
        bars = _make_bars(rows)
        for ts in bars.index:
            strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts)

    def test_reset_clears_daily_range_cache(self):
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)
        self._run_once(strat, "2024-01-02")
        self.assertGreater(len(strat._daily_range), 0, "Cache should be populated after first run")

        strat.reset()
        self.assertEqual(len(strat._daily_range), 0, "reset() must clear the opening-range cache")

    def test_second_run_produces_same_signal_as_first(self):
        """Running twice with reset in between should yield identical signals."""
        strat = OpeningRangeBreakout(DEFAULT_PARAMS)

        def collect_signals(date):
            rows = [
                _bar(date, "09:30", 480, 485, 475, 482),
                _bar(date, "09:35", 482, 490, 480, 484),
                _bar(date, "10:00", 489, 491, 487, 490),
                _bar(date, "10:05", 490, 496, 488, 492),
            ]
            bars = _make_bars(rows)
            sigs = []
            for ts in bars.index:
                s = strat.generate_signal("SPY", bars.loc[bars.index <= ts], ts)
                if s:
                    sigs.append(s)
            return sigs

        run1 = collect_signals("2024-01-02")
        strat.reset()
        run2 = collect_signals("2024-01-02")

        self.assertEqual(len(run1), len(run2), "Both runs should produce the same number of signals")
        if run1 and run2:
            self.assertAlmostEqual(run1[0].entry_price, run2[0].entry_price)
            self.assertAlmostEqual(run1[0].stop_loss, run2[0].stop_loss)


if __name__ == "__main__":
    unittest.main(verbosity=2)
