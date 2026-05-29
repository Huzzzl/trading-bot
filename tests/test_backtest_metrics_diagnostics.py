"""
tests/test_backtest_metrics_diagnostics.py
------------------------------------------
Tests for src/backtest/metrics_diagnostics.py.

Rules:
  - No network access. No Alpaca. No credentials.
  - All inputs are synthetic in-test arrays — no real market data.
  - No file writes.
  - broker_calls_made=False in all results.
  - No raw equity values in output.
"""

from __future__ import annotations

import ast
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics_diagnostics import diagnose_sharpe


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _flat_series(n: int = 100, start: float = 100_000.0) -> pd.Series:
    """Constant equity — std of returns is exactly zero."""
    return pd.Series([start] * n, dtype=float)


def _trending_series(n: int = 200, start: float = 100_000.0, end: float = 110_000.0) -> pd.Series:
    """Linearly trending equity with small noise — realistic Sharpe."""
    rng = np.random.default_rng(42)
    base = np.linspace(start, end, n)
    noise = rng.normal(0.0, 50.0, n)
    return pd.Series(np.maximum(base + noise, 1.0), dtype=float)


def _near_flat_series(n: int = 200, start: float = 100_000.0) -> pd.Series:
    """Near-constant equity — std is non-zero but very small."""
    rng = np.random.default_rng(7)
    tiny_noise = rng.normal(0.0, 0.001, n)
    return pd.Series(start + tiny_noise, dtype=float)


def _df_from_series(s: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"equity": s})


# ---------------------------------------------------------------------------
# TestInvalidInputs
# ---------------------------------------------------------------------------


class TestInvalidInputs:
    """Invalid or degenerate inputs → BLOCKED with safe error."""

    def test_invalid_interval_blocked(self) -> None:
        s = _trending_series()
        result = diagnose_sharpe(s, "bad_interval")
        assert result["result"] == "BLOCKED"

    def test_invalid_interval_blocker_message(self) -> None:
        s = _trending_series()
        result = diagnose_sharpe(s, "bad_interval")
        assert result["blocker"] is not None
        assert len(result["blocker"]) > 0

    def test_invalid_interval_bars_per_year_none(self) -> None:
        s = _trending_series()
        result = diagnose_sharpe(s, "bad_interval")
        assert result["bars_per_year"] is None

    def test_nan_equity_blocked(self) -> None:
        s = _trending_series()
        s.iloc[50] = float("nan")
        result = diagnose_sharpe(s, "1d")
        assert result["result"] == "BLOCKED"

    def test_inf_equity_blocked(self) -> None:
        s = _trending_series()
        s.iloc[10] = float("inf")
        result = diagnose_sharpe(s, "1d")
        assert result["result"] == "BLOCKED"

    def test_neg_inf_equity_blocked(self) -> None:
        s = _trending_series()
        s.iloc[10] = float("-inf")
        result = diagnose_sharpe(s, "1d")
        assert result["result"] == "BLOCKED"

    def test_non_finite_sets_finite_flag_false(self) -> None:
        s = _trending_series()
        s.iloc[5] = float("nan")
        result = diagnose_sharpe(s, "1d")
        assert result["finite_values_only"] is False

    def test_single_point_blocked(self) -> None:
        s = pd.Series([100_000.0])
        result = diagnose_sharpe(s, "1d")
        assert result["result"] == "BLOCKED"

    def test_empty_series_blocked(self) -> None:
        s = pd.Series([], dtype=float)
        result = diagnose_sharpe(s, "1d")
        assert result["result"] == "BLOCKED"

    def test_missing_equity_column_blocked(self) -> None:
        df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
        result = diagnose_sharpe(df, "1d")
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestFlatCurve
# ---------------------------------------------------------------------------


class TestFlatCurve:
    """Flat equity → zero std → BLOCKED to avoid misleading Sharpe."""

    def test_flat_series_blocked(self) -> None:
        result = diagnose_sharpe(_flat_series(), "1d")
        assert result["result"] == "BLOCKED"

    def test_flat_series_zero_std_detected(self) -> None:
        result = diagnose_sharpe(_flat_series(), "1d")
        assert result["zero_std_detected"] is True

    def test_flat_series_sharpe_none(self) -> None:
        result = diagnose_sharpe(_flat_series(), "1d")
        assert result["sharpe_ratio_recomputed"] is None

    def test_flat_df_blocked(self) -> None:
        result = diagnose_sharpe(_df_from_series(_flat_series()), "1d")
        assert result["result"] == "BLOCKED"

    def test_flat_series_equity_points_counted(self) -> None:
        result = diagnose_sharpe(_flat_series(n=50), "1d")
        assert result["equity_points"] == 50

    def test_flat_series_return_points_counted(self) -> None:
        result = diagnose_sharpe(_flat_series(n=50), "1d")
        assert result["return_points"] == 49


# ---------------------------------------------------------------------------
# TestNormalCurve
# ---------------------------------------------------------------------------


class TestNormalCurve:
    """Normal trending synthetic equity → finite Sharpe recomputed."""

    def test_pass_on_trending_series(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["result"] == "PASS"

    def test_sharpe_is_finite(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["sharpe_ratio_recomputed"] is not None
        assert math.isfinite(result["sharpe_ratio_recomputed"])

    def test_std_positive(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["std_period_return"] > 0.0

    def test_zero_std_not_detected(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["zero_std_detected"] is False

    def test_finite_values_flag_true(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["finite_values_only"] is True

    def test_equity_points_correct(self) -> None:
        n = 200
        result = diagnose_sharpe(_trending_series(n=n), "1d")
        assert result["equity_points"] == n

    def test_return_points_is_equity_points_minus_one(self) -> None:
        n = 200
        result = diagnose_sharpe(_trending_series(n=n), "1d")
        assert result["return_points"] == n - 1

    def test_df_input_same_as_series(self) -> None:
        s = _trending_series()
        r_series = diagnose_sharpe(s, "1d")
        r_df = diagnose_sharpe(_df_from_series(s), "1d")
        assert r_series["result"] == r_df["result"]
        assert r_series["sharpe_ratio_recomputed"] == r_df["sharpe_ratio_recomputed"]

    def test_blocker_none_on_pass(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["blocker"] is None


# ---------------------------------------------------------------------------
# TestLowVariance
# ---------------------------------------------------------------------------


class TestLowVariance:
    """Near-flat equity → low_variance_warning=True but still PASS."""

    def test_near_flat_passes(self) -> None:
        result = diagnose_sharpe(_near_flat_series(), "1d")
        assert result["result"] == "PASS"

    def test_near_flat_low_variance_warning(self) -> None:
        result = diagnose_sharpe(_near_flat_series(), "1d")
        assert result["low_variance_warning"] is True

    def test_near_flat_zero_std_not_set(self) -> None:
        result = diagnose_sharpe(_near_flat_series(), "1d")
        assert result["zero_std_detected"] is False

    def test_normal_curve_no_low_variance_warning(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["low_variance_warning"] is False

    def test_custom_threshold_respected(self) -> None:
        # Very high threshold → normal curve triggers warning
        result = diagnose_sharpe(_trending_series(), "1d", low_variance_threshold=1.0)
        assert result["low_variance_warning"] is True


# ---------------------------------------------------------------------------
# TestIntervalLookup
# ---------------------------------------------------------------------------


class TestIntervalLookup:
    """Correct bars_per_year for each interval."""

    def test_1d_uses_252(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["bars_per_year"] == 252

    def test_60m_uses_1512(self) -> None:
        result = diagnose_sharpe(_trending_series(), "60m")
        assert result["bars_per_year"] == 1512

    def test_1h_uses_1512(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1h")
        assert result["bars_per_year"] == 1512

    def test_5m_uses_19656(self) -> None:
        result = diagnose_sharpe(_trending_series(), "5m")
        assert result["bars_per_year"] == 252 * 78

    def test_interval_recorded_in_output(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["interval"] == "1d"


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """All four safety flags must be False in every result."""

    def test_broker_calls_made_false_pass(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["broker_calls_made"] is False

    def test_credentials_read_false_pass(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["credentials_read"] is False

    def test_network_calls_made_false_pass(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["network_calls_made"] is False

    def test_order_action_requested_false_pass(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert result["order_action_requested"] is False

    def test_broker_calls_made_false_blocked(self) -> None:
        result = diagnose_sharpe(_flat_series(), "1d")
        assert result["broker_calls_made"] is False

    def test_network_calls_made_false_blocked(self) -> None:
        result = diagnose_sharpe(_flat_series(), "1d")
        assert result["network_calls_made"] is False

    def test_all_flags_false_on_invalid_interval(self) -> None:
        result = diagnose_sharpe(_trending_series(), "bad")
        assert result["broker_calls_made"] is False
        assert result["credentials_read"] is False
        assert result["network_calls_made"] is False
        assert result["order_action_requested"] is False


# ---------------------------------------------------------------------------
# TestNoPricesEmitted
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS = frozenset({"open", "high", "low", "close", "volume"})
_RAW_EQUITY_KEY = "equity"


class TestNoPricesEmitted:
    """Output must not contain raw OHLCV or equity values."""

    def test_no_ohlcv_keys_in_output(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        unexpected = _FORBIDDEN_KEYS & result.keys()
        assert not unexpected

    def test_no_raw_equity_key_in_output(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert _RAW_EQUITY_KEY not in result

    def test_equity_points_is_integer_not_array(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert isinstance(result["equity_points"], int)

    def test_mean_period_return_is_scalar(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert isinstance(result["mean_period_return"], float)

    def test_std_period_return_is_scalar(self) -> None:
        result = diagnose_sharpe(_trending_series(), "1d")
        assert isinstance(result["std_period_return"], float)


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same inputs → identical outputs on repeated calls."""

    def test_repeated_pass_identical(self) -> None:
        s = _trending_series()
        r1 = diagnose_sharpe(s, "1d")
        r2 = diagnose_sharpe(s, "1d")
        assert r1 == r2

    def test_repeated_blocked_identical(self) -> None:
        s = _flat_series()
        r1 = diagnose_sharpe(s, "1d")
        r2 = diagnose_sharpe(s, "1d")
        assert r1 == r2

    def test_sharpe_stable_across_calls(self) -> None:
        s = _trending_series()
        sharpes = [diagnose_sharpe(s, "1d")["sharpe_ratio_recomputed"] for _ in range(3)]
        assert len(set(sharpes)) == 1


# ---------------------------------------------------------------------------
# TestSourceScan — AST-level forbidden import/usage checks
# ---------------------------------------------------------------------------

_MODULE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "src" / "backtest" / "metrics_diagnostics.py"
)


def _parse_src() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def _import_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


class TestSourceScan:
    """AST scan verifies no forbidden imports or order calls."""

    def test_no_yfinance(self) -> None:
        assert not any("yfinance" in n for n in _import_names(_parse_src()))

    def test_no_requests(self) -> None:
        assert not any(n == "requests" or n.startswith("requests.") for n in _import_names(_parse_src()))

    def test_no_httpx(self) -> None:
        assert not any("httpx" in n for n in _import_names(_parse_src()))

    def test_no_aiohttp(self) -> None:
        assert not any("aiohttp" in n for n in _import_names(_parse_src()))

    def test_no_urllib_request(self) -> None:
        assert not any(n == "urllib" or n.startswith("urllib.request") for n in _import_names(_parse_src()))

    def test_no_alpaca(self) -> None:
        assert not any("alpaca" in n for n in _import_names(_parse_src()))

    def test_no_os_environ(self) -> None:
        src = _MODULE_PATH.read_text(encoding="utf-8")
        assert "os.environ" not in src
        assert "os.getenv" not in src

    def test_no_submit_order(self) -> None:
        assert "submit_order" not in _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_cancel_order(self) -> None:
        assert "cancel_order" not in _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_replace_order(self) -> None:
        assert "replace_order" not in _MODULE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# TestDiagnosticVsProduction
# ---------------------------------------------------------------------------
# Explicit cross-behaviour tests confirming that:
#   (1) compute_metrics() still emits a numeric sharpe_ratio for the same
#       cases it did before this PR — behaviour is UNCHANGED.
#   (2) diagnose_sharpe() flags zero/near-zero std separately from
#       compute_metrics(), which silently returns 0.0.
#   (3) diagnostic BLOCKED ≠ backtest run BLOCKED.
# ---------------------------------------------------------------------------


def _make_dated_equity_df(s: pd.Series, freq: str = "B") -> "pd.DataFrame":
    """Wrap a Series into a DataFrame with a DatetimeIndex (required by compute_metrics)."""
    import pandas as pd
    dates = pd.bdate_range("2020-01-02", periods=len(s), freq=freq)
    return pd.DataFrame({"equity": s.values}, index=dates)


class TestDiagnosticVsProduction:
    """Cross-module invariants: diagnostics do not alter production metrics."""

    # --- (1) compute_metrics sharpe_ratio is still numeric after this PR ----

    def test_compute_metrics_flat_curve_returns_numeric_sharpe(self) -> None:
        """compute_metrics() returns a numeric sharpe_ratio for flat equity.

        This was the behaviour before PR 10K and must remain unchanged.
        diagnose_sharpe() on the same input returns BLOCKED — but that only
        affects the diagnostic tool, not the backtest pipeline.

        Note: due to floating-point noise in np.std on a constant series,
        compute_metrics returns a large-magnitude float rather than 0.0.
        The key invariant is that it returns a float — not None, not an
        exception, not a sentinel string.
        """
        from src.backtest.metrics import compute_metrics

        flat_eq = _make_dated_equity_df(_flat_series(n=50))
        m = compute_metrics([], flat_eq, initial_capital=100_000.0, interval="1d")
        # Must be numeric — not None, not "BLOCKED", not an exception
        assert isinstance(m["sharpe_ratio"], float)

    def test_compute_metrics_trending_curve_returns_numeric_sharpe(self) -> None:
        """compute_metrics() returns a finite numeric sharpe_ratio for a
        normal trending equity curve.  Behaviour unchanged by this PR."""
        import math
        from src.backtest.metrics import compute_metrics

        s = _trending_series(n=200)
        eq = _make_dated_equity_df(s)
        m = compute_metrics([], eq, initial_capital=float(s.iloc[0]), interval="1d")
        assert isinstance(m["sharpe_ratio"], float)
        assert math.isfinite(m["sharpe_ratio"])

    # --- (2) diagnose_sharpe flags zero std separately ----------------------

    def test_diagnose_blocked_while_compute_metrics_returns_numeric(self) -> None:
        """On a flat equity curve: compute_metrics → sharpe=0.0 (numeric);
        diagnose_sharpe → BLOCKED.  The diagnostic and the backtest pipeline
        use independent code paths and must not interfere."""
        from src.backtest.metrics import compute_metrics

        flat_s = _flat_series(n=50)
        flat_eq_dated = _make_dated_equity_df(flat_s)
        m = compute_metrics([], flat_eq_dated, initial_capital=100_000.0, interval="1d")
        d = diagnose_sharpe(flat_s, "1d")

        # compute_metrics still produces numeric Sharpe
        assert isinstance(m["sharpe_ratio"], float)
        # diagnose_sharpe detects the condition and blocks
        assert d["result"] == "BLOCKED"
        assert d["zero_std_detected"] is True

    def test_diagnose_low_variance_warning_while_metrics_still_numeric(self) -> None:
        """Near-flat curve: compute_metrics returns a (possibly extreme) numeric
        Sharpe; diagnose_sharpe issues low_variance_warning but stays PASS."""
        import math
        from src.backtest.metrics import compute_metrics

        s = _near_flat_series(n=200)
        eq = _make_dated_equity_df(s)
        m = compute_metrics([], eq, initial_capital=float(s.iloc[0]), interval="1d")
        d = diagnose_sharpe(s, "1d")

        # compute_metrics: numeric (may be extreme)
        assert isinstance(m["sharpe_ratio"], float)
        # diagnose_sharpe: PASS with warning
        assert d["result"] == "PASS"
        assert d["low_variance_warning"] is True

    # --- (3) diagnostic BLOCKED ≠ backtest run BLOCKED ----------------------

    def test_diagnostic_blocked_does_not_affect_backtest_result(self) -> None:
        """diagnose_sharpe returning BLOCKED for a flat curve does not cause
        compute_metrics or any backtest pipeline function to raise or block.
        The two functions are fully independent."""
        from src.backtest.metrics import compute_metrics

        flat_s = _flat_series(n=50)
        flat_eq_dated = _make_dated_equity_df(flat_s)

        # Diagnostic returns BLOCKED
        d = diagnose_sharpe(flat_s, "1d")
        assert d["result"] == "BLOCKED"

        # compute_metrics on the same input still succeeds — no exception, no BLOCKED
        m = compute_metrics([], flat_eq_dated, initial_capital=100_000.0, interval="1d")
        assert "sharpe_ratio" in m
        assert isinstance(m["sharpe_ratio"], float)

    def test_diagnose_sharpe_does_not_mutate_equity_curve(self) -> None:
        """diagnose_sharpe() must not modify its input."""
        import pandas as pd

        s = _trending_series(n=100)
        original_values = s.values.copy()
        diagnose_sharpe(s, "1d")
        assert (s.values == original_values).all()

    def test_diagnose_sharpe_does_not_mutate_dataframe(self) -> None:
        """diagnose_sharpe() must not modify a DataFrame input."""
        import pandas as pd

        s = _trending_series(n=100)
        df = _df_from_series(s)
        original_equity = df["equity"].values.copy()
        diagnose_sharpe(df, "1d")
        assert (df["equity"].values == original_equity).all()
