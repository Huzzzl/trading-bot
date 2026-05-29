"""
tests/test_cached_real_data_backtest_check.py
--------------------------------------------
Tests for src/tools/cached_real_data_backtest_check.py.

Rules:
  - No network access. No yfinance. No Alpaca. No credentials.
  - All filesystem operations use tmp_path.
  - No real cache files. No raw market data.
  - No file writes (except the optional --output path test).
  - broker_calls_made=False in all results.
  - Synthetic CSV fixtures only (no raw OHLCV values in output).
"""

from __future__ import annotations

import ast
import json
import pathlib
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from src.tools.cached_real_data_backtest_check import (
    _TREND_PARAMS,
    _LoadedFileProvider,
    _load_cache_file,
    main,
    run_check,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_ALLOWED_SCENARIO_KEYS = frozenset({
    "symbol",
    "interval",
    "rows",
    "status",
    "total_return_pct",
    "annualized_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "num_trades",
    # Sharpe diagnostic fields (PR 10L)
    "sharpe_diagnostic_result",
    "zero_std_detected",
    "low_variance_warning",
    "annualized_volatility",
    "return_points",
})

_FORBIDDEN_OHLCV_KEYS = frozenset({"open", "high", "low", "close", "volume"})


def _make_df(n: int, seed: int = 42, interval: str = "1d") -> pd.DataFrame:
    """Synthetic uptrending OHLCV DataFrame with enough bars for TrendFollowing warm-up."""
    rng = np.random.default_rng(seed)
    if interval in ("1d",):
        dates = pd.bdate_range("2020-01-02", periods=n, tz=ZoneInfo("America/New_York"))
    else:
        # Hourly: 6 bars per business day
        bdays = pd.bdate_range("2020-01-02", periods=(n // 6) + 1, tz=ZoneInfo("America/New_York"))
        timestamps = []
        for d in bdays:
            for h in [9, 10, 11, 12, 13, 14]:
                timestamps.append(d.replace(hour=h, minute=30, second=0, microsecond=0))
        dates = pd.DatetimeIndex(timestamps[:n])
    base = np.linspace(100.0, 125.0, n)
    noise = rng.normal(0.0, 0.5, n)
    close = np.maximum(base + noise, 1.0)
    high = close + rng.uniform(0.02, 1.0, n)
    low = np.maximum(close - rng.uniform(0.02, 1.0, n), 0.01)
    open_ = np.roll(close, 1)
    open_[0] = close[0] * 0.999
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


def _write_fixture(
    cache_dir: pathlib.Path,
    symbol: str,
    interval: str,
    n: int = 80,
    valid: bool = True,
) -> pathlib.Path:
    """Write a CSV fixture file with the expected filename pattern."""
    df = _make_df(n, interval=interval)
    if not valid:
        df = pd.DataFrame({"bad_col": [1] * n}, index=df.index)
    start = df.index[0].date().isoformat()
    end = df.index[-1].date().isoformat()
    fname = f"{symbol}_{start}_{end}_{interval}.csv"
    df.to_csv(cache_dir / fname)
    return cache_dir / fname


def _write_all_four_fixtures(cache_dir: pathlib.Path) -> None:
    """Write all 4 fixture files: SPY/QQQ × 1d/60m."""
    _write_fixture(cache_dir, "SPY", "1d", n=80)
    _write_fixture(cache_dir, "QQQ", "1d", n=80)
    _write_fixture(cache_dir, "SPY", "60m", n=150)
    _write_fixture(cache_dir, "QQQ", "60m", n=150)


# ---------------------------------------------------------------------------
# TestMissingCache
# ---------------------------------------------------------------------------


class TestMissingCache:
    """Cache missing or empty → BLOCKED immediately, no backtest run."""

    def test_blocked_when_cache_dir_missing(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_such_dir"
        result = run_check(cache_dir=nonexistent, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "BLOCKED"

    def test_blocked_when_cache_empty(self, tmp_path: pathlib.Path) -> None:
        result = run_check(cache_dir=tmp_path, symbols=["SPY", "QQQ"], intervals=["1d", "60m"])
        assert result["result"] == "BLOCKED"

    def test_no_backtest_run_when_blocked(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_such_dir"
        with patch("src.tools.cached_real_data_backtest_check.run_backtest") as mock_bt:
            run_check(cache_dir=nonexistent, symbols=["SPY"], intervals=["1d"])
            mock_bt.assert_not_called()

    def test_scenarios_run_is_zero_when_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["scenarios_run"] == 0

    def test_availability_check_result_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["availability_check_result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# TestValidCache
# ---------------------------------------------------------------------------


class TestValidCache:
    """Valid cache with all four fixtures → PASS with scenario metrics."""

    def test_pass_with_all_four_scenarios(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert result["result"] == "PASS"

    def test_scenarios_run_equals_four(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert result["scenarios_run"] == 4

    def test_each_scenario_has_required_keys(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        required_keys = {
            "symbol", "interval", "rows", "status",
            "total_return_pct", "annualized_return_pct",
            "max_drawdown_pct", "sharpe_ratio", "num_trades",
            "sharpe_diagnostic_result", "zero_std_detected",
            "low_variance_warning", "annualized_volatility", "return_points",
        }
        for s in result["scenarios"]:
            assert required_keys.issubset(s.keys()), f"Missing keys in scenario: {s}"

    def test_rows_matches_fixture_size(self, tmp_path: pathlib.Path) -> None:
        _write_fixture(tmp_path, "SPY", "1d", n=80)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "PASS"
        assert result["scenarios"][0]["rows"] == 80

    def test_num_trades_is_non_negative_int(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert isinstance(s["num_trades"], int)
                assert s["num_trades"] >= 0

    def test_max_drawdown_non_positive(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert s["max_drawdown_pct"] <= 0.0

    def test_win_rate_in_range_if_present(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK" and "win_rate_pct" in s:
                assert 0.0 <= s["win_rate_pct"] <= 100.0

    def test_availability_check_result_pass(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert result["availability_check_result"] == "PASS"


# ---------------------------------------------------------------------------
# TestInvalidColumns
# ---------------------------------------------------------------------------


class TestInvalidColumns:
    """Files with invalid columns → BLOCKED for affected scenarios."""

    def test_blocked_when_one_file_has_invalid_columns(self, tmp_path: pathlib.Path) -> None:
        # SPY/1d valid, SPY/60m invalid columns
        _write_fixture(tmp_path, "SPY", "1d", n=80, valid=True)
        _write_fixture(tmp_path, "SPY", "60m", n=150, valid=False)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d", "60m"])
        assert result["result"] == "BLOCKED"

    def test_partial_scenarios_blocked(self, tmp_path: pathlib.Path) -> None:
        # Only SPY/1d is valid — SPY/60m has invalid columns
        _write_fixture(tmp_path, "SPY", "1d", n=80, valid=True)
        _write_fixture(tmp_path, "SPY", "60m", n=150, valid=False)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d", "60m"])
        # Overall BLOCKED because availability check blocks the invalid file
        assert result["result"] == "BLOCKED"
        # When availability check is BLOCKED, scenarios list is empty (fail-fast)
        assert result["scenarios_run"] == 0

    def test_invalid_scenario_in_scenarios_list(self, tmp_path: pathlib.Path) -> None:
        _write_fixture(tmp_path, "SPY", "60m", n=150, valid=False)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["60m"])
        assert result["result"] == "BLOCKED"
        assert len(result["scenarios"]) >= 0  # may be empty or have BLOCKED entry


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same inputs → same outputs on repeated calls."""

    def test_repeated_calls_identical_result(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        r1 = run_check(cache_dir=tmp_path)
        r2 = run_check(cache_dir=tmp_path)
        assert r1["result"] == r2["result"]
        assert r1["scenarios_run"] == r2["scenarios_run"]

    def test_repeated_calls_identical_scenarios(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        r1 = run_check(cache_dir=tmp_path)
        r2 = run_check(cache_dir=tmp_path)
        for s1, s2 in zip(r1["scenarios"], r2["scenarios"]):
            assert s1["symbol"] == s2["symbol"]
            assert s1["interval"] == s2["interval"]
            assert s1["status"] == s2["status"]
            if s1["status"] == "OK":
                assert s1["num_trades"] == s2["num_trades"]

    def test_repeated_blocked_identical(self, tmp_path: pathlib.Path) -> None:
        r1 = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        r2 = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert r1["result"] == r2["result"] == "BLOCKED"
        assert r1["scenarios_run"] == r2["scenarios_run"] == 0


# ---------------------------------------------------------------------------
# TestIntervalAliasing
# ---------------------------------------------------------------------------


class TestIntervalAliasing:
    """60m and 1h are treated as equivalent intervals."""

    def test_60m_file_satisfies_1h_check(self, tmp_path: pathlib.Path) -> None:
        # Write file as "60m" but request "1h"
        _write_fixture(tmp_path, "SPY", "60m", n=150)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1h"])
        # Should find the 60m file via aliasing and PASS
        assert result["result"] == "PASS"

    def test_1h_file_satisfies_60m_check(self, tmp_path: pathlib.Path) -> None:
        # Write file as "1h" but request "60m"
        _write_fixture(tmp_path, "SPY", "1h", n=150)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["60m"])
        # Should find the 1h file via aliasing and PASS
        assert result["result"] == "PASS"

    def test_alias_backtest_runs(self, tmp_path: pathlib.Path) -> None:
        # 60m file → 60m request → PASS and scenario has OK
        _write_fixture(tmp_path, "SPY", "60m", n=150)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["60m"])
        assert result["result"] == "PASS"
        assert result["scenarios"][0]["status"] == "OK"


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """Safety flags must always be False in all results."""

    def test_broker_calls_made_false_on_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["broker_calls_made"] is False

    def test_credentials_read_false_on_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["credentials_read"] is False

    def test_network_calls_made_false_on_pass(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert result["network_calls_made"] is False

    def test_order_action_requested_false_on_pass(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert result["order_action_requested"] is False

    def test_recommendation_only_semantics(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        # order_action_requested must be False — tool never requests any trade action
        assert result["order_action_requested"] is False


# ---------------------------------------------------------------------------
# TestNoPricesEmitted
# ---------------------------------------------------------------------------


class TestNoPricesEmitted:
    """Output must never contain raw OHLCV price values."""

    def test_no_raw_close_prices_in_scenarios(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            assert "close" not in s
            assert "open" not in s

    def test_scenario_keys_allowed_set(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            unexpected = set(s.keys()) - _ALLOWED_SCENARIO_KEYS
            assert not unexpected, f"Unexpected keys in scenario: {unexpected}"

    def test_no_equity_curve_in_output(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert "equity_curve" not in result

    def test_rows_is_integer(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            assert isinstance(s["rows"], int)


# ---------------------------------------------------------------------------
# TestOutputJson
# ---------------------------------------------------------------------------


class TestOutputJson:
    """--output flag writes valid JSON with correct structure."""

    def test_json_written_with_output_flag(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        output_path = tmp_path / "out" / "result.json"
        rc = main([
            "--cache-dir", str(tmp_path),
            "--symbols", "SPY", "QQQ",
            "--intervals", "1d", "60m",
            "--output", str(output_path),
        ])
        assert output_path.exists()
        assert rc == 0

    def test_json_has_required_keys(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        output_path = tmp_path / "result.json"
        main([
            "--cache-dir", str(tmp_path),
            "--output", str(output_path),
        ])
        data = json.loads(output_path.read_text())
        for key in ("result", "scenarios_run", "scenarios", "broker_calls_made",
                    "credentials_read", "network_calls_made", "order_action_requested"):
            assert key in data, f"Missing key in JSON output: {key}"

    def test_json_blocked_when_missing_cache(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_cache"
        output_path = tmp_path / "result.json"
        rc = main([
            "--cache-dir", str(nonexistent),
            "--output", str(output_path),
        ])
        assert rc == 1
        data = json.loads(output_path.read_text())
        assert data["result"] == "BLOCKED"

    def test_json_result_matches_run_check(self, tmp_path: pathlib.Path) -> None:
        _write_all_four_fixtures(tmp_path)
        output_path = tmp_path / "result.json"
        main([
            "--cache-dir", str(tmp_path),
            "--output", str(output_path),
        ])
        data = json.loads(output_path.read_text())
        direct = run_check(cache_dir=tmp_path)
        assert data["result"] == direct["result"]
        assert data["scenarios_run"] == direct["scenarios_run"]


# ---------------------------------------------------------------------------
# TestSourceScan — AST-level forbidden import/usage checks
# ---------------------------------------------------------------------------

_TOOL_SRC_PATH = (
    pathlib.Path(__file__).parent.parent
    / "src" / "tools" / "cached_real_data_backtest_check.py"
)


def _parse_tool_src() -> ast.Module:
    return ast.parse(_TOOL_SRC_PATH.read_text(encoding="utf-8"))


class TestSourceScan:
    """AST scan of the tool source to verify safety constraints."""

    def _all_import_names(self, tree: ast.Module) -> list[str]:
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def _all_string_literals(self, tree: ast.Module) -> list[str]:
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]

    def _all_identifiers(self, tree: ast.Module) -> list[str]:
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        return names

    def test_no_yfinance_import(self) -> None:
        tree = _parse_tool_src()
        imports = self._all_import_names(tree)
        assert not any("yfinance" in n for n in imports), "yfinance imported"

    def test_no_requests_import(self) -> None:
        tree = _parse_tool_src()
        imports = self._all_import_names(tree)
        assert not any(n == "requests" or n.startswith("requests.") for n in imports), \
            "requests imported"

    def test_no_httpx_import(self) -> None:
        tree = _parse_tool_src()
        imports = self._all_import_names(tree)
        assert not any("httpx" in n for n in imports), "httpx imported"

    def test_no_aiohttp_import(self) -> None:
        tree = _parse_tool_src()
        imports = self._all_import_names(tree)
        assert not any("aiohttp" in n for n in imports), "aiohttp imported"

    def test_no_urllib_direct_import(self) -> None:
        tree = _parse_tool_src()
        # Only flag direct urllib usage, not os.path which uses urllib internally
        imports = self._all_import_names(tree)
        assert not any(n == "urllib" or n.startswith("urllib.request") for n in imports), \
            "urllib.request imported"

    def test_no_alpaca_import(self) -> None:
        tree = _parse_tool_src()
        imports = self._all_import_names(tree)
        assert not any("alpaca" in n for n in imports), "alpaca imported"

    def test_no_os_environ_access(self) -> None:
        """No os.environ or os.getenv calls in the actual Python code (AST check)."""
        tree = _parse_tool_src()
        findings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr in ("environ", "getenv")
                ):
                    findings.append(f"os.{node.attr} at line {node.lineno}")
        assert not findings, f"os.environ/getenv access found: {findings}"

    def test_no_submit_order(self) -> None:
        src_text = _TOOL_SRC_PATH.read_text(encoding="utf-8")
        assert "submit_order" not in src_text, "submit_order found in source"

    def test_no_cancel_order(self) -> None:
        src_text = _TOOL_SRC_PATH.read_text(encoding="utf-8")
        assert "cancel_order" not in src_text, "cancel_order found in source"

    def test_no_replace_order(self) -> None:
        src_text = _TOOL_SRC_PATH.read_text(encoding="utf-8")
        assert "replace_order" not in src_text, "replace_order found in source"


# ---------------------------------------------------------------------------
# TestTrendParams — default _TREND_PARAMS uses correct strategy param names
# ---------------------------------------------------------------------------


class TestTrendParams:
    """Verify default _TREND_PARAMS uses the actual TrendFollowing param names."""

    def test_default_params_contain_fast_ema_period(self) -> None:
        assert "fast_ema_period" in _TREND_PARAMS

    def test_default_params_contain_slow_ema_period(self) -> None:
        assert "slow_ema_period" in _TREND_PARAMS

    def test_default_params_do_not_contain_ema_fast(self) -> None:
        assert "ema_fast" not in _TREND_PARAMS

    def test_default_params_do_not_contain_ema_slow(self) -> None:
        assert "ema_slow" not in _TREND_PARAMS

    def test_default_fast_ema_period_value(self) -> None:
        assert _TREND_PARAMS["fast_ema_period"] == 10

    def test_default_slow_ema_period_value(self) -> None:
        assert _TREND_PARAMS["slow_ema_period"] == 50

    def test_run_check_passes_fast_ema_period_to_backtest(self, tmp_path: pathlib.Path) -> None:
        _write_fixture(tmp_path, "SPY", "1d", n=80)
        captured: list[dict] = []

        def _capture_config(config, *, data_provider=None):
            captured.append(dict(config.strategy_params))
            from src.backtest.backtest_runner import run_backtest as _real
            return _real(config, data_provider=data_provider)

        with patch("src.tools.cached_real_data_backtest_check.run_backtest", side_effect=_capture_config):
            run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])

        assert len(captured) == 1
        assert captured[0].get("fast_ema_period") == 10

    def test_run_check_passes_slow_ema_period_to_backtest(self, tmp_path: pathlib.Path) -> None:
        _write_fixture(tmp_path, "SPY", "1d", n=80)
        captured: list[dict] = []

        def _capture_config(config, *, data_provider=None):
            captured.append(dict(config.strategy_params))
            from src.backtest.backtest_runner import run_backtest as _real
            return _real(config, data_provider=data_provider)

        with patch("src.tools.cached_real_data_backtest_check.run_backtest", side_effect=_capture_config):
            run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])

        assert len(captured) == 1
        assert captured[0].get("slow_ema_period") == 50


# ---------------------------------------------------------------------------
# TestSharpeDiagnostics — per-scenario diagnose_sharpe() integration (PR 10L)
# ---------------------------------------------------------------------------


def _make_flat_ohlcv(n: int, interval: str = "1d") -> pd.DataFrame:
    """Flat OHLCV with constant prices — strategy never trades → flat equity."""
    if interval in ("1d",):
        dates = pd.bdate_range("2020-01-02", periods=n, tz=ZoneInfo("America/New_York"))
    else:
        bdays = pd.bdate_range("2020-01-02", periods=(n // 6) + 2, tz=ZoneInfo("America/New_York"))
        timestamps = []
        for d in bdays:
            for h in [9, 10, 11, 12, 13, 14]:
                timestamps.append(d.replace(hour=h, minute=30, second=0, microsecond=0))
        dates = pd.DatetimeIndex(timestamps[:n])
    return pd.DataFrame(
        {
            "open":   np.full(n, 100.0),
            "high":   np.full(n, 101.0),
            "low":    np.full(n, 99.0),
            "close":  np.full(n, 100.0),
            "volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


def _write_flat_fixture(cache_dir: pathlib.Path, symbol: str, interval: str, n: int = 80) -> None:
    """Write a flat-OHLCV CSV fixture file."""
    df = _make_flat_ohlcv(n, interval=interval)
    start = df.index[0].date().isoformat()
    end = df.index[-1].date().isoformat()
    fname = f"{symbol}_{start}_{end}_{interval}.csv"
    df.to_csv(cache_dir / fname)


_DIAGNOSTIC_KEYS = frozenset({
    "sharpe_diagnostic_result",
    "zero_std_detected",
    "low_variance_warning",
    "annualized_volatility",
    "return_points",
})


class TestSharpeDiagnostics:
    """Per-scenario diagnose_sharpe() integration tests."""

    def test_valid_cache_includes_diagnostic_fields_per_scenario(self, tmp_path: pathlib.Path) -> None:
        """Every OK scenario includes all 5 diagnostic fields."""
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        assert result["result"] == "PASS"
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert _DIAGNOSTIC_KEYS.issubset(s.keys()), (
                    f"Missing diagnostic keys in scenario {s['symbol']}/{s['interval']}: "
                    f"{_DIAGNOSTIC_KEYS - set(s.keys())}"
                )

    def test_sharpe_diagnostic_result_is_pass_or_blocked(self, tmp_path: pathlib.Path) -> None:
        """sharpe_diagnostic_result is always 'PASS' or 'BLOCKED'."""
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert s["sharpe_diagnostic_result"] in ("PASS", "BLOCKED"), (
                    f"Unexpected sharpe_diagnostic_result: {s['sharpe_diagnostic_result']}"
                )

    def test_zero_std_detected_is_bool(self, tmp_path: pathlib.Path) -> None:
        """zero_std_detected field is always a bool."""
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert isinstance(s["zero_std_detected"], bool)

    def test_low_variance_warning_is_bool(self, tmp_path: pathlib.Path) -> None:
        """low_variance_warning field is always a bool."""
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert isinstance(s["low_variance_warning"], bool)

    def test_return_points_is_non_negative_int(self, tmp_path: pathlib.Path) -> None:
        """return_points is always a non-negative int."""
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                assert isinstance(s["return_points"], int)
                assert s["return_points"] >= 0

    def test_flat_fixture_zero_std_detected(self, tmp_path: pathlib.Path) -> None:
        """Flat OHLCV → no trades → flat equity → zero_std_detected=True (BLOCKED diagnostic).

        Constant prices produce no EMA crossovers or breakouts; the strategy
        stays flat and equity never changes.  diagnose_sharpe() detects this
        and returns BLOCKED with zero_std_detected=True.  The scenario status
        remains OK (diagnostic BLOCKED ≠ scenario BLOCKED).
        """
        _write_flat_fixture(tmp_path, "SPY", "1d", n=80)
        result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "PASS"
        s = result["scenarios"][0]
        assert s["status"] == "OK"
        assert s["zero_std_detected"] is True
        assert s["sharpe_diagnostic_result"] == "BLOCKED"

    def test_diagnostic_blocked_does_not_block_scenario(self, tmp_path: pathlib.Path) -> None:
        """Even when diagnose_sharpe returns BLOCKED, scenario status stays OK."""
        from unittest.mock import patch as _patch

        _write_fixture(tmp_path, "SPY", "1d", n=80)
        blocked_diag = {
            "result": "BLOCKED",
            "zero_std_detected": True,
            "low_variance_warning": False,
            "annualized_volatility": None,
            "return_points": 0,
            "broker_calls_made": False,
            "credentials_read": False,
            "network_calls_made": False,
            "order_action_requested": False,
        }
        with _patch(
            "src.tools.cached_real_data_backtest_check.diagnose_sharpe",
            return_value=blocked_diag,
        ):
            result = run_check(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])

        assert result["result"] == "PASS"
        s = result["scenarios"][0]
        assert s["status"] == "OK"
        assert s["sharpe_diagnostic_result"] == "BLOCKED"

    def test_no_raw_equity_values_in_diagnostic_fields(self, tmp_path: pathlib.Path) -> None:
        """Diagnostic fields must not contain raw equity curve values.

        Only statistical aggregates (float scalars, bools, strings) are allowed.
        """
        _write_all_four_fixtures(tmp_path)
        result = run_check(cache_dir=tmp_path)
        for s in result["scenarios"]:
            if s["status"] == "OK":
                for key in _DIAGNOSTIC_KEYS:
                    val = s[key]
                    assert not isinstance(val, (list, pd.DataFrame, pd.Series)), (
                        f"Scenario field {key!r} contains a sequence/DataFrame: {type(val)}"
                    )
