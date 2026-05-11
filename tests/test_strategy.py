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


if __name__ == "__main__":
    unittest.main(verbosity=2)
