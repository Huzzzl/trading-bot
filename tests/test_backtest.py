"""
tests/test_backtest.py
----------------------
Integration tests for Portfolio, RiskManager, BacktestEngine, and metrics.
Uses stdlib unittest only.
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics
from src.backtest.trade import Trade
from src.config.loader import RiskConfig
from src.data.base import BaseDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout

_EASTERN = ZoneInfo("America/New_York")

DEFAULT_PARAMS = {
    "opening_range_start": "09:30", "opening_range_end": "10:00",
    "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
}

def _ts(date, time):
    return pd.Timestamp(f"{date} {time}", tz=_EASTERN)

def _make_day(date, or_high=490.0, or_low=475.0, breakout_close=495.0, eod_close=493.0):
    rows = {
        _ts(date,"09:30"): (480, or_high-5, or_low,    482),
        _ts(date,"09:35"): (482, or_high-3, or_low+2,  484),
        _ts(date,"09:40"): (484, or_high-2, or_low+3,  486),
        _ts(date,"09:50"): (487, or_high,   or_low+5,  488),
        _ts(date,"09:55"): (488, or_high,   or_low+6,  489),
        _ts(date,"10:00"): (489, or_high+1, or_low+7,  490),
        _ts(date,"10:05"): (490, breakout_close+1, 488, breakout_close),
        _ts(date,"15:55"): (eod_close-2, eod_close+1, eod_close-3, eod_close),
    }
    df = pd.DataFrame.from_dict(rows, orient="index", columns=["open","high","low","close"])
    df["volume"] = 1_000_000
    df.index = pd.DatetimeIndex(df.index)
    return df.sort_index()

class FakeDataProvider(BaseDataProvider):
    def __init__(self, bars):
        self._bars = bars
    def fetch_bars(self, symbol, start, end, interval):
        return self._bars.get(symbol, pd.DataFrame())

def _build_engine(bars, capital=100_000):
    return BacktestEngine(
        strategy=OpeningRangeBreakout(DEFAULT_PARAMS),
        data_provider=FakeDataProvider(bars),
        portfolio=Portfolio(capital, commission_per_share=0.0, slippage_per_share=0.0),
        risk_manager=RiskManager(force_exit_time="15:55"),
        symbols=list(bars.keys()),
        start_date="2024-01-02", end_date="2024-01-02",
        position_size_pct=0.95,
    )


class TestPortfolio(unittest.TestCase):
    def test_open_long_deducts_cash(self):
        port = Portfolio(100_000, 0.005, 0.01)
        ts = _ts("2024-01-02","10:05")
        pos = port.open_long("SPY", 490, ts, 0.95)
        self.assertIsNotNone(pos)
        self.assertLess(port.cash, 100_000)

    def test_no_duplicate_positions(self):
        port = Portfolio(100_000)
        ts = _ts("2024-01-02","10:05")
        port.open_long("SPY", 490, ts, 0.95)
        p2 = port.open_long("SPY", 492, ts, 0.95)
        self.assertIsNone(p2)
        self.assertEqual(len(port.positions), 1)

    def test_close_adds_cash(self):
        port = Portfolio(100_000, 0.0, 0.0)
        t1, t2 = _ts("2024-01-02","10:05"), _ts("2024-01-02","15:55")
        port.open_long("SPY", 490, t1, 0.95)
        cash_mid = port.cash
        trade = port.close_position("SPY", 500, t2, "force_exit")
        self.assertIsNotNone(trade)
        self.assertGreater(port.cash, cash_mid)

    def test_winning_trade_pnl_positive(self):
        port = Portfolio(100_000, 0.0, 0.0)
        port.open_long("SPY", 490, _ts("2024-01-02","10:05"), 0.95)
        trade = port.close_position("SPY", 500, _ts("2024-01-02","15:55"))
        self.assertGreater(trade.pnl, 0)

    def test_losing_trade_pnl_negative(self):
        port = Portfolio(100_000, 0.0, 0.0)
        port.open_long("SPY", 490, _ts("2024-01-02","10:05"), 0.95)
        trade = port.close_position("SPY", 470, _ts("2024-01-02","10:15"))
        self.assertLess(trade.pnl, 0)


class TestRiskManager(unittest.TestCase):
    def _sig(self, sym="SPY"):
        s = MagicMock(); s.symbol = sym; return s

    def test_approves_clean_entry(self):
        rm = RiskManager("15:55")
        port = Portfolio(100_000)
        self.assertTrue(rm.approve_entry(self._sig(), port, _ts("2024-01-02","10:05")))

    def test_rejects_after_force_exit(self):
        rm = RiskManager("15:55")
        port = Portfolio(100_000)
        self.assertFalse(rm.approve_entry(self._sig(), port, _ts("2024-01-02","15:55")))

    def test_rejects_duplicate_position(self):
        rm = RiskManager("15:55")
        port = Portfolio(100_000)
        port.open_long("SPY", 490, _ts("2024-01-02","10:05"), 0.95)
        self.assertFalse(rm.approve_entry(self._sig("SPY"), port, _ts("2024-01-02","10:10")))

    def test_rejects_when_max_trades_reached(self):
        rm = RiskManager("15:55", max_trades_per_symbol_per_day=1)
        port = Portfolio(100_000)
        rm.record_trade_taken("SPY","2024-01-02")
        self.assertFalse(rm.approve_entry(self._sig("SPY"), port, _ts("2024-01-02","11:00")))

    def test_stop_loss_triggers(self):
        rm = RiskManager("15:55")
        port = Portfolio(100_000)
        port.open_long("SPY", 490, _ts("2024-01-02","10:05"), 0.95, stop_loss=480)
        exits = rm.check_exits(port, _ts("2024-01-02","10:10"),
                               {"SPY":{"open":488,"high":489,"low":478,"close":479}})
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0][2], "stop_loss")

    def test_force_exit_at_eod(self):
        rm = RiskManager("15:55")
        port = Portfolio(100_000)
        port.open_long("SPY", 490, _ts("2024-01-02","10:05"), 0.95, stop_loss=475)
        exits = rm.check_exits(port, _ts("2024-01-02","15:55"),
                               {"SPY":{"open":492,"high":494,"low":491,"close":493}})
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0][2], "force_exit")


class TestEngine(unittest.TestCase):
    def test_winning_trade_executed(self):
        bars = {"SPY": _make_day("2024-01-02", or_high=490, breakout_close=500, eod_close=510)}
        r = _build_engine(bars).run()
        self.assertGreaterEqual(len(r["trades"]), 1)
        self.assertGreater(r["trades"][0].pnl, 0)

    def test_no_trade_without_breakout(self):
        bars = {"SPY": _make_day("2024-01-02", or_high=490, breakout_close=488)}
        r = _build_engine(bars).run()
        self.assertEqual(len(r["trades"]), 0)

    def test_equity_curve_populated(self):
        bars = {"SPY": _make_day("2024-01-02")}
        r = _build_engine(bars).run()
        self.assertFalse(r["equity_curve"].empty)

    def test_no_open_positions_at_end(self):
        bars = {"SPY": _make_day("2024-01-02")}
        eng = _build_engine(bars)
        eng.run()
        self.assertEqual(len(eng._portfolio.positions), 0)

    def test_stop_loss_exit(self):
        date = "2024-01-02"
        rows = {
            _ts(date,"09:30"): (480,490,475,482),
            _ts(date,"09:35"): (482,487,477,484),
            _ts(date,"09:50"): (487,490,482,488),
            _ts(date,"09:55"): (488,490,483,489),
            _ts(date,"10:00"): (489,491,484,490),
            _ts(date,"10:05"): (490,497,488,495),   # entry
            _ts(date,"10:10"): (495,496,470,471),   # low < or_low 475 → stop
            _ts(date,"15:55"): (490,492,488,491),
        }
        df = pd.DataFrame.from_dict(rows, orient="index", columns=["open","high","low","close"])
        df["volume"]=1_000_000
        df.index = pd.DatetimeIndex(df.index)
        r = _build_engine({"SPY": df.sort_index()}).run()
        stops = [t for t in r["trades"] if t.exit_reason == "stop_loss"]
        self.assertGreaterEqual(len(stops), 1)


class TestSessionBoundary(unittest.TestCase):
    """Position must be closed at session boundary even when the 15:55 bar is missing."""

    def test_missing_eod_bar_closes_at_prev_session_price(self):
        """Day 1 has no 15:55 bar.  An entry at 10:05 (close=495) must be closed
        at Day 1's last close (495), NOT at Day 2's first close (550).
        exit_time must fall on Day 1."""

        date1, date2 = "2024-01-02", "2024-01-03"

        # Day 1: breakout entry at 10:05, last close = 495.  No 15:55 bar.
        day1 = {
            _ts(date1, "09:30"): (480, 485, 475, 482),
            _ts(date1, "09:35"): (482, 490, 480, 484),
            _ts(date1, "09:50"): (487, 490, 482, 488),
            _ts(date1, "09:55"): (488, 490, 483, 489),
            _ts(date1, "10:00"): (489, 491, 484, 490),
            _ts(date1, "10:05"): (490, 497, 488, 495),   # entry bar; last close = 495
            # no 15:55 bar
        }
        # Day 2: first close is 550 — very different from Day 1 last close.
        # If exit_price were 550 the test would catch the regression.
        day2 = {
            _ts(date2, "09:30"): (548, 555, 545, 550),
        }

        rows = {**day1, **day2}
        df = pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "close"])
        df["volume"] = 1_000_000
        df.index = pd.DatetimeIndex(df.index)
        df = df.sort_index()

        # slippage=0 so exit_price == close of the exit bar exactly
        engine = _build_engine({"SPY": df})
        result = engine.run()

        session_end_trades = [t for t in result["trades"] if t.exit_reason == "session_end"]
        self.assertGreaterEqual(len(session_end_trades), 1,
                                "Expected at least one trade with reason='session_end'")

        trade = session_end_trades[0]

        # exit_time must be on Day 1, not Day 2
        self.assertEqual(
            trade.exit_time.date().isoformat(), date1,
            f"exit_time {trade.exit_time.date()} must be Day 1 ({date1})",
        )

        # exit_price must be based on Day 1's last close (495), not Day 2's (550)
        self.assertAlmostEqual(
            trade.exit_price, 495.0, places=2,
            msg=f"exit_price {trade.exit_price} should be Day 1 last close 495, not Day 2 close 550",
        )

        # no positions left open
        self.assertEqual(len(engine._portfolio.positions), 0,
                         "No positions should remain open after the backtest")


class TestMaxOpenPositions(unittest.TestCase):
    """max_open_positions=1 must prevent a second symbol from entering simultaneously."""

    def test_max_1_blocks_second_concurrent_entry(self):
        """Both SPY and QQQ trigger a breakout on the same bar.
        With max_open_positions=1, only the first symbol (by iteration order)
        should have a trade; the second is blocked."""

        date = "2024-01-02"
        # Both symbols: identical day with a breakout at 10:05
        spy_df = _make_day(date, or_high=490, breakout_close=500, eod_close=505)
        qqq_df = _make_day(date, or_high=490, breakout_close=500, eod_close=505)

        bars = {"SPY": spy_df, "QQQ": qqq_df}

        engine = BacktestEngine(
            strategy=OpeningRangeBreakout(DEFAULT_PARAMS),
            data_provider=FakeDataProvider(bars),
            portfolio=Portfolio(200_000, commission_per_share=0.0, slippage_per_share=0.0),
            risk_manager=RiskManager(force_exit_time="15:55", max_open_positions=1),
            symbols=["SPY", "QQQ"],
            start_date=date, end_date=date,
            position_size_pct=0.95,
        )
        result = engine.run()

        # With max_open_positions=1, at most one trade should occur on any day.
        self.assertEqual(
            len(result["trades"]), 1,
            f"Expected exactly 1 trade with max_open_positions=1, got {len(result['trades'])}",
        )


class TestMetrics(unittest.TestCase):
    def _trade(self, win=True):
        t1, t2 = _ts("2024-01-02","10:05"), _ts("2024-01-02","15:55")
        return Trade(symbol="SPY", entry_time=t1, exit_time=t2,
                     entry_price=490, exit_price=500 if win else 480,
                     shares=100, commission=0, direction="LONG", exit_reason="force_exit")

    def test_all_winners_100pct_winrate(self):
        m = compute_metrics([self._trade(True)]*5, pd.DataFrame(), 100_000)
        self.assertAlmostEqual(m["win_rate_pct"], 100.0)

    def test_all_losers_0pct_winrate(self):
        m = compute_metrics([self._trade(False)]*5, pd.DataFrame(), 100_000)
        self.assertAlmostEqual(m["win_rate_pct"], 0.0)

    def test_num_trades_count(self):
        m = compute_metrics([self._trade()]*7, pd.DataFrame(), 100_000)
        self.assertEqual(m["num_trades"], 7)

    def test_empty_returns_zeros(self):
        m = compute_metrics([], pd.DataFrame(), 100_000)
        self.assertEqual(m["num_trades"], 0)
        self.assertAlmostEqual(m["total_return_pct"], 0.0)


class TestRiskConfig(unittest.TestCase):
    """RiskConfig dataclass and loader integration."""

    def test_risk_config_defaults_to_none(self):
        cfg = RiskConfig()
        self.assertIsNone(cfg.max_open_positions)

    def test_risk_config_stores_value(self):
        cfg = RiskConfig(max_open_positions=1)
        self.assertEqual(cfg.max_open_positions, 1)

    def test_load_config_parses_max_open_positions(self):
        """load_config must parse the risk section from settings.yaml."""
        from src.config.loader import load_config
        cfg = load_config()   # uses config/settings.yaml which has max_open_positions: 1
        self.assertIsNotNone(cfg.risk)
        self.assertEqual(cfg.risk.max_open_positions, 1)

    def test_load_config_risk_absent_gives_none(self):
        """When the risk section is missing, max_open_positions defaults to None."""
        import tempfile, textwrap, yaml
        yaml_text = textwrap.dedent("""
            backtest:
              start_date: "2026-04-10"
              end_date:   "2026-05-09"
              initial_capital: 100000.0
              commission_per_share: 0.005
              slippage_per_share: 0.01
            symbols: [SPY]
            data:
              provider: yahoo
              bar_interval: "5m"
              timezone: "America/New_York"
            strategy:
              name: opening_range_breakout
              params: {}
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_text)
            tmp_path = f.name

        from src.config.loader import load_config
        cfg = load_config(tmp_path)
        self.assertIsNone(cfg.risk.max_open_positions)



if __name__ == "__main__":
    unittest.main(verbosity=2)
