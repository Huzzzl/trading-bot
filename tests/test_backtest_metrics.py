"""
tests/test_backtest_metrics.py
-------------------------------
Unit tests for src/backtest/metrics.py.

All tests are pure/offline — no broker calls, no credentials, no network.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import bars_per_year_for_interval, compute_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_equity_curve(
    periods: int = 100,
    freq: str = "5min",
    start: float = 100_000.0,
    end: float = 110_000.0,
) -> pd.DataFrame:
    """Return a linear equity curve over *periods* bars."""
    dates = pd.date_range("2023-01-02 09:30", periods=periods, freq=freq)
    equity = np.linspace(start, end, periods)
    return pd.DataFrame({"equity": equity}, index=dates)


_INITIAL_CAPITAL = 100_000.0


# ---------------------------------------------------------------------------
# TestBarsPerYearForInterval
# ---------------------------------------------------------------------------


class TestBarsPerYearForInterval:
    def test_1m(self) -> None:
        assert bars_per_year_for_interval("1m") == 252 * 390

    def test_2m(self) -> None:
        # Yahoo-supported 2-minute interval
        assert bars_per_year_for_interval("2m") == 252 * 195

    def test_5m(self) -> None:
        assert bars_per_year_for_interval("5m") == 252 * 78

    def test_15m(self) -> None:
        assert bars_per_year_for_interval("15m") == 252 * 26

    def test_30m(self) -> None:
        assert bars_per_year_for_interval("30m") == 252 * 13

    def test_60m(self) -> None:
        # Yahoo-compatible alias for "1h"; must equal "1h"
        assert bars_per_year_for_interval("60m") == 252 * 6

    def test_1h(self) -> None:
        # 6 complete 1-h bars per 6.5-h session (last 30 min is a partial bar)
        assert bars_per_year_for_interval("1h") == 252 * 6

    def test_90m(self) -> None:
        # floor(390 / 90) = 4 complete 90-min bars per day
        assert bars_per_year_for_interval("90m") == 252 * 4

    def test_2h(self) -> None:
        assert bars_per_year_for_interval("2h") == 252 * 3

    def test_4h(self) -> None:
        assert bars_per_year_for_interval("4h") == 252 * 1

    def test_1d(self) -> None:
        assert bars_per_year_for_interval("1d") == 252

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid interval"):
            bars_per_year_for_interval("3h")

    def test_invalid_secret_not_echoed(self) -> None:
        secret = "secret_interval_value"
        with pytest.raises(ValueError) as exc_info:
            bars_per_year_for_interval(secret)
        assert secret not in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestComputeMetricsInterval
# ---------------------------------------------------------------------------


class TestComputeMetricsInterval:
    def test_default_interval_is_5m(self) -> None:
        """Omitting interval= must behave identically to passing interval='5m'."""
        curve = _make_equity_curve()
        m_default  = compute_metrics([], curve.copy(), _INITIAL_CAPITAL)
        m_explicit = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="5m")
        assert m_default["sharpe_ratio"] == pytest.approx(m_explicit["sharpe_ratio"])

    def test_sharpe_changes_with_interval(self) -> None:
        """Same equity curve with different intervals gives different Sharpe ratios."""
        curve = _make_equity_curve()
        m_5m = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="5m")
        m_1d = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1d")
        assert m_5m["sharpe_ratio"] != pytest.approx(m_1d["sharpe_ratio"])

    def test_sharpe_1d_uses_252_not_5m_factor(self) -> None:
        """Sharpe for interval='1d' must use bars_per_year=252, not 19656."""
        curve = _make_equity_curve(periods=252, freq="B")
        bpy = 252
        rf = 0.05
        eq_vals  = curve["equity"].values
        bar_rets = np.diff(eq_vals) / eq_vals[:-1]
        excess   = bar_rets - (rf / bpy)
        std      = float(np.std(excess, ddof=1))
        expected = round(float(np.mean(excess) / std * math.sqrt(bpy)), 4)

        m = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1d")
        assert m["sharpe_ratio"] == pytest.approx(expected, abs=1e-4)

    def test_sharpe_1h_uses_1h_factor(self) -> None:
        """Sharpe for interval='1h' must use bars_per_year=252*6=1512."""
        curve = _make_equity_curve(periods=100, freq="h")
        bpy = 252 * 6
        rf = 0.05
        eq_vals  = curve["equity"].values
        bar_rets = np.diff(eq_vals) / eq_vals[:-1]
        excess   = bar_rets - (rf / bpy)
        std      = float(np.std(excess, ddof=1))
        expected = round(float(np.mean(excess) / std * math.sqrt(bpy)), 4)

        m = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1h")
        assert m["sharpe_ratio"] == pytest.approx(expected, abs=1e-4)

    def test_total_return_unchanged_across_intervals(self) -> None:
        """total_return_pct is identical regardless of interval."""
        curve = _make_equity_curve()
        m_5m = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="5m")
        m_1d = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1d")
        assert m_5m["total_return_pct"] == pytest.approx(m_1d["total_return_pct"])

    def test_max_drawdown_unchanged_across_intervals(self) -> None:
        """max_drawdown_pct is identical regardless of interval."""
        dates = pd.date_range("2023-01-02 09:30", periods=60, freq="5min")
        vals  = [100_000.0] * 20 + [95_000.0] * 20 + [100_000.0] * 20
        curve = pd.DataFrame({"equity": vals}, index=dates)
        m_5m = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="5m")
        m_1d = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1d")
        assert m_5m["max_drawdown_pct"] == pytest.approx(m_1d["max_drawdown_pct"])

    def test_trade_count_unchanged_across_intervals(self) -> None:
        """num_trades is identical regardless of interval."""
        m_5m = compute_metrics([], pd.DataFrame(), _INITIAL_CAPITAL, interval="5m")
        m_1d = compute_metrics([], pd.DataFrame(), _INITIAL_CAPITAL, interval="1d")
        assert m_5m["num_trades"] == m_1d["num_trades"] == 0

    def test_deterministic_repeated_calls(self) -> None:
        """Identical inputs must always produce identical outputs."""
        curve = _make_equity_curve()
        m1 = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1h")
        m2 = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1h")
        assert m1["sharpe_ratio"]     == m2["sharpe_ratio"]
        assert m1["total_return_pct"] == m2["total_return_pct"]

    def test_equity_curve_not_mutated(self) -> None:
        """compute_metrics must not modify the caller's equity curve."""
        curve          = _make_equity_curve()
        original_vals  = curve["equity"].values.copy()
        original_index = list(curve.index)
        compute_metrics([], curve, _INITIAL_CAPITAL, interval="5m")
        np.testing.assert_array_equal(curve["equity"].values, original_vals)
        assert list(curve.index) == original_index

    def test_empty_equity_curve_gives_zero_sharpe(self) -> None:
        """Empty equity_curve returns sharpe=0.0 regardless of interval."""
        m_5m = compute_metrics([], pd.DataFrame(), _INITIAL_CAPITAL, interval="5m")
        m_1d = compute_metrics([], pd.DataFrame(), _INITIAL_CAPITAL, interval="1d")
        assert m_5m["sharpe_ratio"] == pytest.approx(0.0)
        assert m_1d["sharpe_ratio"] == pytest.approx(0.0)

    def test_60m_matches_1h_sharpe(self) -> None:
        """interval='60m' must produce identical Sharpe to interval='1h'."""
        curve = _make_equity_curve()
        m_60m = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="60m")
        m_1h  = compute_metrics([], curve.copy(), _INITIAL_CAPITAL, interval="1h")
        assert m_60m["sharpe_ratio"] == pytest.approx(m_1h["sharpe_ratio"])


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

_SOURCE = Path("src/backtest/metrics.py")


def _read_code(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#")[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


class TestSourceScans:
    def _code(self) -> str:
        return _read_code(_SOURCE)

    def test_no_alpaca_import(self) -> None:
        code = self._code()
        assert "import alpaca" not in code
        assert "from alpaca" not in code

    def test_no_requests_import(self) -> None:
        assert "import requests" not in self._code()

    def test_no_httpx_import(self) -> None:
        assert "import httpx" not in self._code()

    def test_no_aiohttp_import(self) -> None:
        assert "import aiohttp" not in self._code()

    def test_no_urllib_request(self) -> None:
        assert "urllib.request" not in self._code()

    def test_no_os_environ(self) -> None:
        assert "os.environ" not in self._code()

    def test_no_execution_imports(self) -> None:
        code = self._code()
        assert "from src.execution" not in code
        assert "import src.execution" not in code

    def test_no_submit_order(self) -> None:
        assert "submit_order(" not in self._code()

    def test_no_cancel_order(self) -> None:
        assert "cancel_order(" not in self._code()

    def test_no_replace_order(self) -> None:
        assert "replace_order(" not in self._code()

    def test_no_close_position(self) -> None:
        assert "close_position(" not in self._code()

    def test_no_close_all_positions(self) -> None:
        assert "close_all_positions(" not in self._code()

    def test_no_post_mutation(self) -> None:
        assert ".post(" not in self._code().lower()

    def test_no_patch_mutation(self) -> None:
        assert ".patch(" not in self._code().lower()

    def test_no_delete_mutation(self) -> None:
        assert ".delete(" not in self._code().lower()

    def test_no_trade_record_write(self) -> None:
        assert "ledger" not in self._code()

    def test_no_config_mutation(self) -> None:
        assert "config" not in self._code()
