"""
tests/test_backtest_trade_diagnostics.py
-----------------------------------------
Tests for src/backtest/trade_diagnostics.trade_summary_diagnostics().

Fixture trades are hand-crafted Trade objects with known prices/pnl so
that aggregate outputs can be verified to exact numeric values.

Where:
  WIN:  entry=100, exit=102, shares=10, commission=0.1, direction=LONG
        pnl = (102-100)*10 - 0.1 = 19.9
        return% = 19.9 / 1000 * 100 = 1.99%

  LOSS: entry=100, exit=98,  shares=10, commission=0.1, direction=LONG
        pnl = (98-100)*10  - 0.1 = -20.1
        return% = -20.1 / 1000 * 100 = -2.01%

No broker/API/credentials. No network. All tests are offline and deterministic.
No Alpaca SDK imported. No network library imported. No credentials read.
No orders submitted. No live trading approved. No paper trading approved.
"""

from __future__ import annotations

import math
import pathlib
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.backtest.trade import Trade
from src.backtest.trade_diagnostics import trade_summary_diagnostics

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EASTERN = ZoneInfo("America/New_York")
_T0 = pd.Timestamp("2024-01-02 10:00:00", tz=_EASTERN)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T0 + timedelta(hours=2)
_T3 = _T0 + timedelta(hours=3)

_DIAG_SRC = pathlib.Path("src/backtest/trade_diagnostics.py")

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _win(
    entry_time: pd.Timestamp = _T0,
    exit_time: pd.Timestamp = _T1,
    exit_reason: str = "session_end",
) -> Trade:
    """Winning LONG trade: pnl = 19.9, return% = 1.99%."""
    return Trade(
        symbol="TEST",
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=102.0,
        shares=10.0,
        commission=0.1,
        direction="LONG",
        exit_reason=exit_reason,
    )


def _loss(
    entry_time: pd.Timestamp = _T0,
    exit_time: pd.Timestamp = _T1,
    exit_reason: str = "stop_loss",
) -> Trade:
    """Losing LONG trade: pnl = -20.1, return% = -2.01%."""
    return Trade(
        symbol="TEST",
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=98.0,
        shares=10.0,
        commission=0.1,
        direction="LONG",
        exit_reason=exit_reason,
    )


def _diag(trades: list[Trade], **kwargs: Any) -> dict[str, Any]:
    return trade_summary_diagnostics(trades, **kwargs)


# ---------------------------------------------------------------------------
# TestEmptyTrades — empty list → PASS with zero counts
# ---------------------------------------------------------------------------


class TestEmptyTrades:
    """Empty trades list returns PASS with zero counts and None stats."""

    def test_result_pass(self) -> None:
        assert _diag([])["result"] == "PASS"

    def test_blocker_none(self) -> None:
        assert _diag([])["blocker"] is None

    def test_trade_count_zero(self) -> None:
        assert _diag([])["trade_count"] == 0

    def test_entry_exit_counts_zero(self) -> None:
        r = _diag([])
        assert r["entry_count"] == 0
        assert r["exit_count"] == 0

    def test_stats_none(self) -> None:
        r = _diag([])
        assert r["win_rate_pct"] is None
        assert r["avg_trade_return_pct"] is None
        assert r["avg_win_pct"] is None
        assert r["avg_loss_pct"] is None
        assert r["profit_factor"] is None

    def test_holding_none(self) -> None:
        r = _diag([])
        assert r["avg_holding_bars"] is None
        assert r["median_holding_bars"] is None
        assert r["min_holding_bars"] is None
        assert r["max_holding_bars"] is None

    def test_exit_reason_counts_empty_dict(self) -> None:
        assert _diag([])["exit_reason_counts"] == {}

    def test_frequency_none_without_total_bars(self) -> None:
        r = _diag([])
        assert r["trades_per_100_bars"] is None
        assert r["exposure_pct"] is None

    def test_frequency_zero_with_total_bars(self) -> None:
        r = _diag([], total_bars=100)
        assert r["trades_per_100_bars"] == 0.0
        assert r["exposure_pct"] == 0.0


# ---------------------------------------------------------------------------
# TestSingleWinningTrade
# ---------------------------------------------------------------------------


class TestSingleWinningTrade:
    """One winning trade: all stats correct."""

    def test_result_pass(self) -> None:
        assert _diag([_win()])["result"] == "PASS"

    def test_trade_count(self) -> None:
        assert _diag([_win()])["trade_count"] == 1

    def test_win_rate_100(self) -> None:
        assert _diag([_win()])["win_rate_pct"] == pytest.approx(100.0)

    def test_avg_win_pct(self) -> None:
        # 19.9 / 1000 * 100 = 1.99%
        assert _diag([_win()])["avg_win_pct"] == pytest.approx(1.99)

    def test_avg_loss_pct_none(self) -> None:
        assert _diag([_win()])["avg_loss_pct"] is None

    def test_profit_factor_none_no_losses(self) -> None:
        assert _diag([_win()])["profit_factor"] is None

    def test_avg_trade_return_pct(self) -> None:
        assert _diag([_win()])["avg_trade_return_pct"] == pytest.approx(1.99)

    def test_holding_one_hour(self) -> None:
        r = _diag([_win(entry_time=_T0, exit_time=_T1)])
        assert r["avg_holding_bars"] == pytest.approx(1.0)
        assert r["min_holding_bars"] == pytest.approx(1.0)
        assert r["max_holding_bars"] == pytest.approx(1.0)

    def test_exit_reason_counts(self) -> None:
        r = _diag([_win(exit_reason="session_end")])
        assert r["exit_reason_counts"] == {"session_end": 1}


# ---------------------------------------------------------------------------
# TestSingleLosingTrade
# ---------------------------------------------------------------------------


class TestSingleLosingTrade:
    """One losing trade: all stats correct."""

    def test_result_pass(self) -> None:
        assert _diag([_loss()])["result"] == "PASS"

    def test_trade_count(self) -> None:
        assert _diag([_loss()])["trade_count"] == 1

    def test_win_rate_zero(self) -> None:
        assert _diag([_loss()])["win_rate_pct"] == pytest.approx(0.0)

    def test_avg_loss_pct(self) -> None:
        # -20.1 / 1000 * 100 = -2.01%
        assert _diag([_loss()])["avg_loss_pct"] == pytest.approx(-2.01)

    def test_avg_win_pct_none(self) -> None:
        assert _diag([_loss()])["avg_win_pct"] is None

    def test_profit_factor_zero_no_wins(self) -> None:
        # gross_profit=0, gross_loss=20.1 → 0/20.1 = 0.0
        assert _diag([_loss()])["profit_factor"] == pytest.approx(0.0)

    def test_avg_trade_return_pct(self) -> None:
        assert _diag([_loss()])["avg_trade_return_pct"] == pytest.approx(-2.01)

    def test_exit_reason_counts(self) -> None:
        r = _diag([_loss(exit_reason="stop_loss")])
        assert r["exit_reason_counts"] == {"stop_loss": 1}


# ---------------------------------------------------------------------------
# TestMixedTrades — wins and losses together
# ---------------------------------------------------------------------------


class TestMixedTrades:
    """One win and one loss: mixed statistics correct."""

    def _trades(self) -> list[Trade]:
        return [
            _win(entry_time=_T0, exit_time=_T1, exit_reason="session_end"),
            _loss(entry_time=_T1, exit_time=_T2, exit_reason="stop_loss"),
        ]

    def test_trade_count(self) -> None:
        assert _diag(self._trades())["trade_count"] == 2

    def test_win_rate_50(self) -> None:
        assert _diag(self._trades())["win_rate_pct"] == pytest.approx(50.0)

    def test_avg_trade_return(self) -> None:
        # mean(1.99, -2.01) = -0.01
        assert _diag(self._trades())["avg_trade_return_pct"] == pytest.approx(-0.01)

    def test_avg_win_pct(self) -> None:
        assert _diag(self._trades())["avg_win_pct"] == pytest.approx(1.99)

    def test_avg_loss_pct(self) -> None:
        assert _diag(self._trades())["avg_loss_pct"] == pytest.approx(-2.01)

    def test_profit_factor(self) -> None:
        # gross_profit=19.9, gross_loss=20.1 → ≈0.9900...
        assert _diag(self._trades())["profit_factor"] == pytest.approx(19.9 / 20.1)

    def test_holding_avg_one_hour(self) -> None:
        # both trades hold for 1 hour
        assert _diag(self._trades())["avg_holding_bars"] == pytest.approx(1.0)

    def test_exit_reason_counts_both(self) -> None:
        r = _diag(self._trades())
        assert r["exit_reason_counts"]["session_end"] == 1
        assert r["exit_reason_counts"]["stop_loss"] == 1

    def test_entry_exit_counts_match_trade_count(self) -> None:
        r = _diag(self._trades())
        assert r["entry_count"] == r["exit_count"] == r["trade_count"]


# ---------------------------------------------------------------------------
# TestProfitFactor — edge cases
# ---------------------------------------------------------------------------


class TestProfitFactor:
    """profit_factor edge cases."""

    def test_none_when_no_losses(self) -> None:
        assert _diag([_win(), _win(entry_time=_T1, exit_time=_T2)])["profit_factor"] is None

    def test_zero_when_no_wins(self) -> None:
        r = _diag([_loss(), _loss(entry_time=_T1, exit_time=_T2)])
        assert r["profit_factor"] == pytest.approx(0.0)

    def test_correct_ratio(self) -> None:
        # 3 wins (pnl=19.9 each), 1 loss (pnl=-20.1)
        trades = [
            _win(entry_time=_T0, exit_time=_T1),
            _win(entry_time=_T1, exit_time=_T2),
            _win(entry_time=_T2, exit_time=_T3),
            _loss(entry_time=_T3, exit_time=_T3 + timedelta(hours=1)),
        ]
        expected = (3 * 19.9) / 20.1
        assert _diag(trades)["profit_factor"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# TestExitReasonCounts — aggregation
# ---------------------------------------------------------------------------


class TestExitReasonCounts:
    """exit_reason_counts aggregates correctly."""

    def test_single_reason(self) -> None:
        trades = [_win(exit_reason="session_end") for _ in range(3)]
        r = _diag(trades)
        assert r["exit_reason_counts"] == {"session_end": 3}

    def test_multiple_reasons(self) -> None:
        trades = [
            _win(exit_reason="session_end"),
            _win(exit_reason="session_end"),
            _loss(exit_reason="stop_loss"),
            _win(exit_reason="force_exit"),
        ]
        r = _diag(trades)
        assert r["exit_reason_counts"]["session_end"] == 2
        assert r["exit_reason_counts"]["stop_loss"] == 1
        assert r["exit_reason_counts"]["force_exit"] == 1
        assert sum(r["exit_reason_counts"].values()) == len(trades)

    def test_counts_sum_equals_trade_count(self) -> None:
        trades = [
            _win(exit_reason="session_end"),
            _loss(exit_reason="stop_loss"),
        ]
        r = _diag(trades)
        assert sum(r["exit_reason_counts"].values()) == r["trade_count"]


# ---------------------------------------------------------------------------
# TestTotalBarsParameter — trades_per_100_bars and exposure_pct
# ---------------------------------------------------------------------------


class TestTotalBarsParameter:
    """total_bars controls trades_per_100_bars and exposure_pct."""

    def test_none_without_total_bars(self) -> None:
        r = _diag([_win()])
        assert r["trades_per_100_bars"] is None
        assert r["exposure_pct"] is None

    def test_correct_with_total_bars(self) -> None:
        # 1 trade / 100 bars = 1.0%
        r = _diag([_win()], total_bars=100)
        assert r["trades_per_100_bars"] == pytest.approx(1.0)
        assert r["exposure_pct"] == pytest.approx(1.0)

    def test_13_trades_100_bars(self) -> None:
        trades = [
            _win(
                entry_time=_T0 + timedelta(hours=i),
                exit_time=_T0 + timedelta(hours=i + 1),
            )
            for i in range(13)
        ]
        r = _diag(trades, total_bars=100)
        assert r["trades_per_100_bars"] == pytest.approx(13.0)
        assert r["exposure_pct"] == pytest.approx(13.0)

    def test_none_when_total_bars_zero(self) -> None:
        r = _diag([_win()], total_bars=0)
        assert r["trades_per_100_bars"] is None
        assert r["exposure_pct"] is None


# ---------------------------------------------------------------------------
# TestBlockedOnInvalidTrades — non-finite values
# ---------------------------------------------------------------------------


class TestBlockedOnInvalidTrades:
    """Non-finite entry_price, exit_price, or commission → BLOCKED."""

    def _bad_trade(self, field_values: dict[str, float]) -> Trade:
        defaults: dict[str, Any] = {
            "symbol": "TEST",
            "entry_time": _T0,
            "exit_time": _T1,
            "entry_price": 100.0,
            "exit_price": 102.0,
            "shares": 10.0,
            "commission": 0.1,
            "direction": "LONG",
            "exit_reason": "session_end",
        }
        defaults.update(field_values)
        return Trade(**defaults)

    def test_blocked_on_nan_entry_price(self) -> None:
        t = self._bad_trade({"entry_price": float("nan")})
        r = _diag([t])
        assert r["result"] == "BLOCKED"
        assert r["blocker"] is not None

    def test_blocked_on_inf_exit_price(self) -> None:
        t = self._bad_trade({"exit_price": float("inf")})
        r = _diag([t])
        assert r["result"] == "BLOCKED"

    def test_blocked_on_nan_commission(self) -> None:
        t = self._bad_trade({"commission": float("nan")})
        r = _diag([t])
        assert r["result"] == "BLOCKED"

    def test_blocked_result_has_safety_flags_false(self) -> None:
        t = self._bad_trade({"entry_price": float("nan")})
        r = _diag([t])
        assert r["broker_calls_made"] is False
        assert r["credentials_read"] is False
        assert r["network_calls_made"] is False
        assert r["order_action_requested"] is False

    def test_valid_trade_not_blocked(self) -> None:
        assert _diag([_win()])["result"] == "PASS"

    def test_blocked_shares_zero(self) -> None:
        t = self._bad_trade({"shares": 0.0})
        assert _diag([t])["result"] == "BLOCKED"

    def test_blocked_shares_negative(self) -> None:
        t = self._bad_trade({"shares": -1.0})
        assert _diag([t])["result"] == "BLOCKED"

    def test_blocked_entry_price_zero(self) -> None:
        t = self._bad_trade({"entry_price": 0.0})
        assert _diag([t])["result"] == "BLOCKED"

    def test_blocked_entry_price_negative(self) -> None:
        t = self._bad_trade({"entry_price": -5.0})
        assert _diag([t])["result"] == "BLOCKED"

    def test_blocked_exit_time_before_entry_time(self) -> None:
        t = Trade(
            symbol="TEST",
            entry_time=_T1,   # later
            exit_time=_T0,    # earlier — invalid
            entry_price=100.0,
            exit_price=102.0,
            shares=10.0,
            commission=0.1,
            direction="LONG",
            exit_reason="session_end",
        )
        assert _diag([t])["result"] == "BLOCKED"

    def test_same_bar_trade_still_valid(self) -> None:
        """exit_time == entry_time is valid; holding = 0.0, not BLOCKED."""
        t = _win(entry_time=_T0, exit_time=_T0)
        r = _diag([t])
        assert r["result"] == "PASS"
        assert r["avg_holding_bars"] == pytest.approx(0.0)

    def test_blocker_message_contains_no_raw_price(self) -> None:
        """Blocker for entry_price=-5 must not echo '-5' or '5.0'."""
        t = self._bad_trade({"entry_price": -5.0})
        r = _diag([t])
        assert r["result"] == "BLOCKED"
        assert "-5" not in r["blocker"]
        assert "5.0" not in r["blocker"]

    def test_blocker_message_contains_no_raw_shares(self) -> None:
        """Blocker for shares=0 must not echo '0' as a raw value."""
        t = self._bad_trade({"shares": 0.0})
        r = _diag([t])
        assert r["result"] == "BLOCKED"
        # message should describe the issue generically, not echo the value
        assert r["blocker"] == "shares must be positive"


# ---------------------------------------------------------------------------
# TestSafetyFlags — all four safety flags False in all code paths
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """All four safety flags are False in all output paths."""

    def test_empty_trades(self) -> None:
        r = _diag([])
        assert r["broker_calls_made"] is False
        assert r["credentials_read"] is False
        assert r["network_calls_made"] is False
        assert r["order_action_requested"] is False

    def test_passing_trades(self) -> None:
        r = _diag([_win(), _loss()])
        assert r["broker_calls_made"] is False
        assert r["credentials_read"] is False
        assert r["network_calls_made"] is False
        assert r["order_action_requested"] is False


# ---------------------------------------------------------------------------
# TestDeterminism — same inputs always produce identical outputs
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Identical inputs always produce identical outputs."""

    def test_empty_deterministic(self) -> None:
        assert _diag([]) == _diag([])

    def test_single_trade_deterministic(self) -> None:
        t = _win()
        r1 = _diag([t])
        r2 = _diag([t])
        assert r1["trade_count"] == r2["trade_count"]
        assert r1["win_rate_pct"] == r2["win_rate_pct"]
        assert r1["profit_factor"] == r2["profit_factor"]
        assert r1["exit_reason_counts"] == r2["exit_reason_counts"]

    def test_mixed_trades_deterministic(self) -> None:
        trades = [_win(), _loss()]
        r1 = _diag(trades)
        r2 = _diag(trades)
        assert r1["avg_trade_return_pct"] == r2["avg_trade_return_pct"]
        assert r1["profit_factor"] == r2["profit_factor"]


# ---------------------------------------------------------------------------
# TestNoRawDataInOutput — no raw prices or trade-by-trade data in result
# ---------------------------------------------------------------------------


class TestNoRawDataInOutput:
    """Output dict contains no raw trade prices or individual trade records."""

    def test_output_values_not_lists_of_floats(self) -> None:
        r = _diag([_win(), _loss()])
        for key, val in r.items():
            if key == "exit_reason_counts":
                continue
            assert not isinstance(val, list), (
                f"Key {key!r} should not be a list of raw values"
            )

    def test_no_pnl_series_in_output(self) -> None:
        r = _diag([_win(), _loss()])
        assert "pnl_series" not in r
        assert "pnl_list" not in r
        assert "pnls" not in r

    def test_no_raw_price_fields_in_output(self) -> None:
        r = _diag([_win(), _loss()])
        for key in r:
            assert "entry_price" not in key
            assert "exit_price" not in key

    def test_no_per_trade_records_in_output(self) -> None:
        r = _diag([_win(), _loss()])
        assert "trades" not in r
        assert "trade_records" not in r


# ---------------------------------------------------------------------------
# TestHoldingPeriod — holding period calculation in hours
# ---------------------------------------------------------------------------


class TestHoldingPeriod:
    """Holding period fields are in approximate hours."""

    def test_same_timestamp_gives_zero_holding(self) -> None:
        t = _win(entry_time=_T0, exit_time=_T0)
        r = _diag([t])
        assert r["avg_holding_bars"] == pytest.approx(0.0)
        assert r["min_holding_bars"] == pytest.approx(0.0)
        assert r["max_holding_bars"] == pytest.approx(0.0)

    def test_two_hour_holding(self) -> None:
        t = _win(entry_time=_T0, exit_time=_T0 + timedelta(hours=2))
        r = _diag([t])
        assert r["avg_holding_bars"] == pytest.approx(2.0)

    def test_varied_holding_stats(self) -> None:
        # trade 1: 1 hour, trade 2: 3 hours
        trades = [
            _win(entry_time=_T0, exit_time=_T0 + timedelta(hours=1)),
            _win(entry_time=_T1, exit_time=_T1 + timedelta(hours=3)),
        ]
        r = _diag(trades)
        assert r["min_holding_bars"] == pytest.approx(1.0)
        assert r["max_holding_bars"] == pytest.approx(3.0)
        assert r["avg_holding_bars"] == pytest.approx(2.0)
        assert r["median_holding_bars"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# TestSafetySourceScan — scan trade_diagnostics.py for forbidden imports
# ---------------------------------------------------------------------------


class TestSafetySourceScan:
    """Source-level scan: no network, broker, or credential imports in
    trade_diagnostics.py."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return _DIAG_SRC.read_text(encoding="utf-8")

    def test_no_alpaca_import(self, source: str) -> None:
        assert "import alpaca" not in source.lower()

    def test_no_yfinance_import(self, source: str) -> None:
        assert "import yfinance" not in source.lower()

    def test_no_requests_import(self, source: str) -> None:
        assert "import requests" not in source

    def test_no_httpx_import(self, source: str) -> None:
        assert "import httpx" not in source

    def test_no_aiohttp_import(self, source: str) -> None:
        assert "import aiohttp" not in source

    def test_no_os_environ(self, source: str) -> None:
        assert "os.environ" not in source

    def test_no_submit_order(self, source: str) -> None:
        assert "submit_order" not in source

    def test_no_cancel_order(self, source: str) -> None:
        assert "cancel_order" not in source
