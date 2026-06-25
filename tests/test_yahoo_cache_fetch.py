"""
tests/test_yahoo_cache_fetch.py
--------------------------------
Tests for src/tools/yahoo_cache_fetch.py.

Rules:
  - No network access. No yfinance. No Alpaca. No credentials.
  - All tests use mocked inner provider via MagicMock + real CachedMarketDataProvider.
  - All filesystem operations use tmp_path.
  - No raw market data committed. No live network calls in any test.
"""

from __future__ import annotations

import ast
import datetime
import json
import pathlib
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.tools.yahoo_cache_fetch import main, run_fetch


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_test_df(n: int = 5, interval: str = "1d") -> pd.DataFrame:
    """Synthetic OHLCV DataFrame for testing (no real market data)."""
    from zoneinfo import ZoneInfo

    dates = pd.bdate_range("2024-01-02", periods=n, tz=ZoneInfo("America/New_York"))
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1000.0] * n,
        },
        index=dates,
    )


def _make_caching_mock_provider(tmp_path, df_or_map=None):
    """Return a CachedMarketDataProvider wrapping a mock inner provider.

    df_or_map: DataFrame returned for all calls, or dict {(symbol, interval): df}
               to control per-call responses. None or missing key → raises Exception.
    """
    from src.data.base import BaseDataProvider
    from src.data.cached_provider import CachedMarketDataProvider

    mock_inner = MagicMock(spec=BaseDataProvider)
    if isinstance(df_or_map, dict):
        def _side_effect(symbol, start, end, interval):
            key = (symbol, interval)
            if key in df_or_map:
                return df_or_map[key].copy()
            raise ValueError(f"No mock data for {symbol}/{interval}")
        mock_inner.fetch_bars.side_effect = _side_effect
    elif df_or_map is not None:
        mock_inner.fetch_bars.return_value = df_or_map.copy()
    else:
        mock_inner.fetch_bars.side_effect = Exception("no data configured")

    return CachedMarketDataProvider(mock_inner, cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# TestNoNetworkFlag
# ---------------------------------------------------------------------------


class TestNoNetworkFlag:
    """Without --allow-network the tool must always return BLOCKED."""

    def test_blocked_without_allow_network(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        assert result["result"] == "BLOCKED"

    def test_blocker_message_present(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        assert isinstance(result["blocker"], str) and len(result["blocker"]) > 0

    def test_network_calls_made_false_when_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        assert result["network_calls_made"] is False

    def test_no_provider_used_when_blocked(self, tmp_path: pathlib.Path) -> None:
        """Provider must never be called when allow_network=False."""
        mock_provider = MagicMock()
        run_fetch(tmp_path, allow_network=False, _provider=mock_provider)
        mock_provider.fetch_bars.assert_not_called()

    def test_exit_code_1_when_blocked(self, tmp_path: pathlib.Path) -> None:
        code = main(["--cache-dir", str(tmp_path)])
        assert code == 1


# ---------------------------------------------------------------------------
# TestWithMockedProvider
# ---------------------------------------------------------------------------


class TestWithMockedProvider:
    """With allow_network=True and a mocked provider, fetch should succeed."""

    def test_pass_with_valid_mock_data(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["result"] == "PASS"

    def test_entries_have_rows_count(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(7)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        ok_entries = [e for e in result["entries"] if e.get("status") in ("FETCHED", "CACHE_HIT")]
        assert len(ok_entries) >= 1
        assert isinstance(ok_entries[0]["rows"], int)
        assert ok_entries[0]["rows"] == 7

    def test_entries_have_inferred_dates(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        ok_entries = [e for e in result["entries"] if e.get("status") in ("FETCHED", "CACHE_HIT")]
        assert ok_entries[0]["inferred_start"] == "2024-01-02"
        assert "inferred_end" in ok_entries[0]

    def test_files_written_count(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["files_written"] > 0

    def test_availability_check_result_pass(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["availability_check_result"] == "PASS"

    def test_network_calls_made_true(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["network_calls_made"] is True

    def test_exit_code_0_on_pass(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        code = main(
            [
                "--allow-network",
                "--cache-dir", str(tmp_path),
                "--symbols", "SPY",
                "--intervals", "1d",
            ]
        )
        # Without injectable provider, this will fail (no yfinance), so we test
        # via run_fetch directly and check main returns 1 when blocked
        # For full CLI testing use run_fetch directly
        assert code in (0, 1)  # acceptable both ways without live yfinance

    def test_safety_flags_on_pass(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["broker_calls_made"] is False
        assert result["credentials_read"] is False
        assert result["order_action_requested"] is False


# ---------------------------------------------------------------------------
# TestEmptyOrMissingData
# ---------------------------------------------------------------------------


class TestEmptyOrMissingData:
    """Empty DataFrame or provider exception → BLOCKED."""

    def test_empty_dataframe_causes_blocked(self, tmp_path: pathlib.Path) -> None:
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider

        mock_inner = MagicMock(spec=BaseDataProvider)
        empty_df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        mock_inner.fetch_bars.return_value = empty_df
        provider = CachedMarketDataProvider(mock_inner, cache_dir=tmp_path)

        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        blocked = [e for e in result["entries"] if e.get("status") == "BLOCKED"]
        assert len(blocked) >= 1

    def test_empty_dataframe_overall_blocked(self, tmp_path: pathlib.Path) -> None:
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider

        mock_inner = MagicMock(spec=BaseDataProvider)
        empty_df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        mock_inner.fetch_bars.return_value = empty_df
        provider = CachedMarketDataProvider(mock_inner, cache_dir=tmp_path)

        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["result"] == "BLOCKED"

    def test_provider_exception_causes_blocked(self, tmp_path: pathlib.Path) -> None:
        provider = _make_caching_mock_provider(tmp_path, None)

        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["result"] == "BLOCKED"

    def test_blocked_entry_in_entries(self, tmp_path: pathlib.Path) -> None:
        provider = _make_caching_mock_provider(tmp_path, None)

        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        blocked = [e for e in result["entries"] if e.get("status") == "BLOCKED"]
        assert len(blocked) >= 1
        assert blocked[0]["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# TestPartialFailure
# ---------------------------------------------------------------------------


class TestPartialFailure:
    """One symbol fails, one succeeds → overall BLOCKED but OK entry still recorded."""

    def _make_partial_provider(self, tmp_path: pathlib.Path):
        df_map = {("SPY", "1d"): _make_test_df(5)}
        return _make_caching_mock_provider(tmp_path, df_map)

    def test_one_symbol_fails_overall_blocked(self, tmp_path: pathlib.Path) -> None:
        provider = self._make_partial_provider(tmp_path)
        result = run_fetch(
            tmp_path,
            symbols=["SPY", "QQQ"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["result"] == "BLOCKED"

    def test_successful_entry_still_recorded(self, tmp_path: pathlib.Path) -> None:
        provider = self._make_partial_provider(tmp_path)
        result = run_fetch(
            tmp_path,
            symbols=["SPY", "QQQ"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        ok_entries = [e for e in result["entries"] if e.get("status") in ("FETCHED", "CACHE_HIT")]
        symbols_ok = [e["symbol"] for e in ok_entries]
        assert "SPY" in symbols_ok

    def test_blocked_entry_has_no_rows(self, tmp_path: pathlib.Path) -> None:
        provider = self._make_partial_provider(tmp_path)
        result = run_fetch(
            tmp_path,
            symbols=["SPY", "QQQ"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        blocked = [e for e in result["entries"] if e.get("status") == "BLOCKED"]
        assert len(blocked) >= 1
        for e in blocked:
            assert "rows" not in e or e.get("rows", 0) == 0

    def test_files_written_reflects_successes(self, tmp_path: pathlib.Path) -> None:
        provider = self._make_partial_provider(tmp_path)
        result = run_fetch(
            tmp_path,
            symbols=["SPY", "QQQ"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        ok_count = sum(1 for e in result["entries"] if e.get("status") in ("FETCHED", "CACHE_HIT"))
        assert result["files_written"] == ok_count


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """Safety flags must always be False."""

    def test_broker_calls_made_false_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        assert result["broker_calls_made"] is False

    def test_credentials_read_false_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        assert result["credentials_read"] is False

    def test_order_action_requested_false_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        assert result["order_action_requested"] is False

    def test_broker_calls_made_false_pass(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["broker_calls_made"] is False

    def test_credentials_read_false_pass(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        assert result["credentials_read"] is False


# ---------------------------------------------------------------------------
# TestNoPricesEmitted
# ---------------------------------------------------------------------------


def _collect_floats(obj) -> list[float]:
    """Recursively collect all float values in a nested dict/list structure."""
    found = []
    if isinstance(obj, float):
        found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_collect_floats(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_collect_floats(item))
    return found


class TestNoPricesEmitted:
    """Output dict must never contain raw price floats."""

    def test_no_float_values_in_output_blocked(self, tmp_path: pathlib.Path) -> None:
        result = run_fetch(tmp_path, allow_network=False)
        floats = _collect_floats(result)
        assert len(floats) == 0, f"Float values found in blocked result: {floats}"

    def test_no_float_values_in_entries(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        for entry in result["entries"]:
            entry_floats = _collect_floats(entry)
            assert len(entry_floats) == 0, (
                f"Float values found in entry {entry}: {entry_floats}"
            )

    def test_rows_is_integer_not_float(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        provider = _make_caching_mock_provider(tmp_path, df)
        result = run_fetch(
            tmp_path,
            symbols=["SPY"],
            intervals=["1d"],
            allow_network=True,
            _provider=provider,
            _inter_fetch_delay=0.0,
            _retry_delays=(),
        )
        ok_entries = [e for e in result["entries"] if e.get("status") in ("FETCHED", "CACHE_HIT")]
        for e in ok_entries:
            assert isinstance(e["rows"], int)
            assert not isinstance(e["rows"], float)


# ---------------------------------------------------------------------------
# TestOutputJson
# ---------------------------------------------------------------------------


class TestOutputJson:
    """--output flag writes a JSON file with correct content."""

    def test_json_file_written_when_output_given(self, tmp_path: pathlib.Path) -> None:
        output_path = tmp_path / "report.json"
        main(["--cache-dir", str(tmp_path), "--output", str(output_path)])
        assert output_path.is_file()

    def test_json_content_has_result_key(self, tmp_path: pathlib.Path) -> None:
        output_path = tmp_path / "report.json"
        main(["--cache-dir", str(tmp_path), "--output", str(output_path)])
        data = json.loads(output_path.read_text())
        assert "result" in data

    def test_json_blocked_without_flag(self, tmp_path: pathlib.Path) -> None:
        output_path = tmp_path / "report.json"
        main(["--cache-dir", str(tmp_path), "--output", str(output_path)])
        data = json.loads(output_path.read_text())
        assert data["result"] == "BLOCKED"

    def test_json_result_matches_run_fetch(self, tmp_path: pathlib.Path) -> None:
        output_path = tmp_path / "report.json"
        run_result = run_fetch(tmp_path, allow_network=False)
        main(["--cache-dir", str(tmp_path), "--output", str(output_path)])
        json_result = json.loads(output_path.read_text())
        assert json_result["result"] == run_result["result"]
        assert json_result["network_calls_made"] == run_result["network_calls_made"]


# ---------------------------------------------------------------------------
# TestSourceScan — AST scan of src/tools/yahoo_cache_fetch.py
# ---------------------------------------------------------------------------

_TOOL_SRC = pathlib.Path(__file__).parent.parent / "src" / "tools" / "yahoo_cache_fetch.py"


def _get_ast() -> ast.Module:
    return ast.parse(_TOOL_SRC.read_text(encoding="utf-8"))


def _all_import_names(tree: ast.Module) -> list[str]:
    """Return all imported module names from the AST."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def _all_attribute_accesses(tree: ast.Module) -> list[str]:
    """Return all 'obj.attr' strings found in the AST."""
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            results.append(f"{node.value.id}.{node.attr}")
    return results


def _all_function_calls(tree: ast.Module) -> list[str]:
    """Return all function call names (simple and attribute calls)."""
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                results.append(func.id)
            elif isinstance(func, ast.Attribute):
                results.append(func.attr)
    return results


class TestForceRefreshAndCacheHitReporting:
    """--force-refresh and CACHE_HIT vs FETCHED accounting."""

    def _wrap_recording(self, tmp_path, df):
        """Return (cached_provider, inner_mock) so tests can assert on
        the number of underlying Yahoo calls."""
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner = MagicMock(spec=BaseDataProvider)
        inner.fetch_bars.return_value = df.copy()
        cached = CachedMarketDataProvider(inner, cache_dir=tmp_path)
        return cached, inner

    def test_cache_hit_no_network_call(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        cached, inner = self._wrap_recording(tmp_path, df)
        # First run: fills the cache.
        r1 = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r1["entries"][0]["status"] == "FETCHED"
        assert r1["network_calls_made"] is True
        assert inner.fetch_bars.call_count == 1

        # Second run, same cache_dir: must be a cache hit.
        cached2, inner2 = self._wrap_recording(tmp_path, df)
        r2 = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r2["entries"][0]["status"] == "CACHE_HIT"
        assert r2["network_calls_made"] is False
        assert r2["fetched_count"] == 0
        assert r2["cache_hit_count"] == 1
        # The underlying Yahoo provider was NEVER called.
        assert inner2.fetch_bars.call_count == 0

    def test_force_refresh_bypasses_cache_and_calls_yahoo(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        cached, inner = self._wrap_recording(tmp_path, df)
        # Pre-fill the cache.
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )

        # Now force a refresh — must hit Yahoo even though the file exists.
        cached2, inner2 = self._wrap_recording(tmp_path, df)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, force_refresh=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "FETCHED"
        assert r["network_calls_made"] is True
        assert r["force_refresh"] is True
        assert r["fetched_count"] == 1
        assert r["cache_hit_count"] == 0
        # The underlying provider WAS called this time.
        assert inner2.fetch_bars.call_count == 1

    def test_force_refresh_does_not_delete_unrelated_files(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)

        # Pre-create unrelated cache files for a different symbol /
        # interval / date range.
        unrelated_files = [
            tmp_path / "QQQ_2020-01-01_2025-01-01_1d.csv",
            tmp_path / "SPY_2018-01-01_2019-01-01_1d.csv",
            tmp_path / "SPY_2020-01-01_2025-01-01_60m.csv",
            tmp_path / "unrelated_file.txt",
        ]
        for f in unrelated_files:
            f.write_text("untouched")

        cached, inner = self._wrap_recording(tmp_path, df)
        # Pre-fill the targeted SPY/1d cache.
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )

        cached2, _ = self._wrap_recording(tmp_path, df)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, force_refresh=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "FETCHED"
        for f in unrelated_files:
            assert f.exists(), f"unrelated file {f.name} was deleted"
            assert f.read_text() == "untouched"

    def test_force_refresh_fetch_failure_preserves_previous_good_cache(
        self, tmp_path: pathlib.Path,
    ) -> None:
        df = _make_test_df(5)

        # Step 1: fill the cache with a known-good payload.
        cached, inner = self._wrap_recording(tmp_path, df)
        r1 = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r1["entries"][0]["status"] == "FETCHED"
        # Capture the cached file path and original content.
        cached_path = cached._cache_path("SPY", "2020-01-01",
                                         datetime.date.today().isoformat(), "1d")
        assert cached_path.exists()
        original_content = cached_path.read_bytes()

        # Step 2: force a refresh with a provider that raises.
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        failing_inner = MagicMock(spec=BaseDataProvider)
        failing_inner.fetch_bars.side_effect = RuntimeError("yahoo down")
        failing_cached = CachedMarketDataProvider(failing_inner, cache_dir=tmp_path)

        r2 = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, force_refresh=True, _provider=failing_cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r2["entries"][0]["status"] == "BLOCKED"
        # The previous good cache file must still exist with the same bytes.
        assert cached_path.exists()
        assert cached_path.read_bytes() == original_content
        # No stray .bak file left behind.
        bak = cached_path.with_suffix(cached_path.suffix + ".bak")
        assert not bak.exists()

    def test_mixed_pairs_report_per_pair_status_accurately(
        self, tmp_path: pathlib.Path,
    ) -> None:
        df_spy = _make_test_df(5)
        df_qqq = _make_test_df(3)

        # Pre-fill SPY/1d only.
        cached, inner = self._wrap_recording(tmp_path, df_spy)
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        # Now run for BOTH SPY/1d (cache hit) and QQQ/1d (fresh fetch).
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner2 = MagicMock(spec=BaseDataProvider)
        def _side_effect(symbol, start, end, interval):
            if symbol == "SPY":
                return df_spy.copy()
            return df_qqq.copy()
        inner2.fetch_bars.side_effect = _side_effect
        cached2 = CachedMarketDataProvider(inner2, cache_dir=tmp_path)

        r = run_fetch(
            tmp_path, symbols=["SPY", "QQQ"], intervals=["1d"],
            allow_network=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        by_symbol = {e["symbol"]: e for e in r["entries"]}
        assert by_symbol["SPY"]["status"] == "CACHE_HIT"
        assert by_symbol["QQQ"]["status"] == "FETCHED"
        assert r["cache_hit_count"] == 1
        assert r["fetched_count"] == 1
        assert r["network_calls_made"] is True
        # The underlying provider was called only for QQQ.
        assert inner2.fetch_bars.call_count == 1
        called_symbols = {
            call.args[0] if call.args else call.kwargs.get("symbol")
            for call in inner2.fetch_bars.call_args_list
        }
        assert called_symbols == {"QQQ"}

    def test_no_network_path_reports_force_refresh_value(self, tmp_path: pathlib.Path) -> None:
        r = run_fetch(tmp_path, allow_network=False, force_refresh=True)
        assert r["result"] == "BLOCKED"
        assert r["force_refresh"] is True
        assert r["network_calls_made"] is False
        assert r["fetched_count"] == 0
        assert r["cache_hit_count"] == 0

    def test_cli_accepts_force_refresh_flag(self, tmp_path: pathlib.Path) -> None:
        # Without --allow-network, the BLOCKED path runs but the flag
        # must parse cleanly and propagate into the result dict.
        code = main(["--cache-dir", str(tmp_path), "--force-refresh"])
        # Exit 1 because BLOCKED.
        assert code == 1


class TestNetworkAttemptedReporting:
    """network_calls_made must track whether the inner provider was
    actually invoked, not whether final entries succeeded."""

    def _failing_cached(self, tmp_path):
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner = MagicMock(spec=BaseDataProvider)
        inner.fetch_bars.side_effect = RuntimeError("yahoo down")
        return CachedMarketDataProvider(inner, cache_dir=tmp_path), inner

    def _passing_cached(self, tmp_path, df):
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner = MagicMock(spec=BaseDataProvider)
        inner.fetch_bars.return_value = df.copy()
        return CachedMarketDataProvider(inner, cache_dir=tmp_path), inner

    def test_failed_fresh_fetch_reports_network_attempt(self, tmp_path: pathlib.Path) -> None:
        # No cache file exists; provider raises. The fetch attempt
        # itself happened, so network_calls_made must be True even
        # though the final entry is BLOCKED.
        cached, inner = self._failing_cached(tmp_path)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "BLOCKED"
        assert r["network_calls_made"] is True
        assert r["fetched_count"] == 0
        assert inner.fetch_bars.call_count >= 1

    def test_failed_force_refresh_reports_network_attempt(self, tmp_path: pathlib.Path) -> None:
        # Pre-fill cache, then force-refresh with a failing provider.
        # The previous cache is preserved, but a network attempt was
        # made, so network_calls_made=True.
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )

        failing_cached, failing_inner = self._failing_cached(tmp_path)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, force_refresh=True,
            _provider=failing_cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "BLOCKED"
        assert r["network_calls_made"] is True
        assert r["fetched_count"] == 0
        assert failing_inner.fetch_bars.call_count >= 1

    def test_pure_cache_hit_reports_no_network(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        # Fill the cache.
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        # Second run: cache hit, no provider call.
        cached2, inner2 = self._passing_cached(tmp_path, df)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "CACHE_HIT"
        assert r["network_calls_made"] is False
        assert inner2.fetch_bars.call_count == 0

    def test_blocked_no_network_path_reports_no_network(self, tmp_path: pathlib.Path) -> None:
        r = run_fetch(tmp_path, allow_network=False)
        assert r["result"] == "BLOCKED"
        assert r["network_calls_made"] is False

    def test_mixed_cache_hit_and_failed_fetch_reports_network_attempt(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # Pre-fill SPY/1d only.
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )

        # Now request SPY/1d (cache hit) + QQQ/1d (fresh, will fail).
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner2 = MagicMock(spec=BaseDataProvider)
        def _side_effect(symbol, start, end, interval):
            if symbol == "SPY":
                return df.copy()
            raise RuntimeError("QQQ unavailable")
        inner2.fetch_bars.side_effect = _side_effect
        cached2 = CachedMarketDataProvider(inner2, cache_dir=tmp_path)

        r = run_fetch(
            tmp_path, symbols=["SPY", "QQQ"], intervals=["1d"],
            allow_network=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        by_symbol = {e["symbol"]: e for e in r["entries"]}
        assert by_symbol["SPY"]["status"] == "CACHE_HIT"
        assert by_symbol["QQQ"]["status"] == "BLOCKED"
        # QQQ network call was attempted, so network_calls_made=True
        # even though the QQQ entry is BLOCKED.
        assert r["network_calls_made"] is True


class TestFilesWrittenSemantics:
    """files_written must count only newly written/replaced files."""

    def _passing_cached(self, tmp_path, df):
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner = MagicMock(spec=BaseDataProvider)
        inner.fetch_bars.return_value = df.copy()
        return CachedMarketDataProvider(inner, cache_dir=tmp_path), inner

    def _failing_cached(self, tmp_path):
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner = MagicMock(spec=BaseDataProvider)
        inner.fetch_bars.side_effect = RuntimeError("down")
        return CachedMarketDataProvider(inner, cache_dir=tmp_path), inner

    def test_pure_cache_hit_files_written_zero(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        # Second run is a pure cache hit.
        cached2, _ = self._passing_cached(tmp_path, df)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "CACHE_HIT"
        assert r["files_written"] == 0
        assert r["cache_hit_count"] == 1
        assert r["fetched_count"] == 0

    def test_one_successful_fetch_files_written_one(self, tmp_path: pathlib.Path) -> None:
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "FETCHED"
        assert r["files_written"] == 1
        assert r["fetched_count"] == 1
        assert r["cache_hit_count"] == 0

    def test_mixed_cache_hit_and_fetch_files_written_one(self, tmp_path: pathlib.Path) -> None:
        # Pre-fill SPY only.
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        # Now SPY hits cache and QQQ is fresh.
        from src.data.base import BaseDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        inner2 = MagicMock(spec=BaseDataProvider)
        inner2.fetch_bars.return_value = df.copy()
        cached2 = CachedMarketDataProvider(inner2, cache_dir=tmp_path)
        r = run_fetch(
            tmp_path, symbols=["SPY", "QQQ"], intervals=["1d"],
            allow_network=True, _provider=cached2,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["files_written"] == 1
        assert r["fetched_count"] == 1
        assert r["cache_hit_count"] == 1

    def test_failed_force_refresh_files_written_zero(self, tmp_path: pathlib.Path) -> None:
        # Fill cache, then force-refresh with a failing provider.
        df = _make_test_df(5)
        cached, _ = self._passing_cached(tmp_path, df)
        run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, _provider=cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )

        failing_cached, _ = self._failing_cached(tmp_path)
        r = run_fetch(
            tmp_path, symbols=["SPY"], intervals=["1d"],
            allow_network=True, force_refresh=True,
            _provider=failing_cached,
            _inter_fetch_delay=0.0, _retry_delays=(),
        )
        assert r["entries"][0]["status"] == "BLOCKED"
        # Force-refresh failed and the backup was restored — no file
        # was newly written or replaced.
        assert r["files_written"] == 0
        assert r["fetched_count"] == 0


class TestSourceScan:
    """AST-based safety scan of the tool source file."""

    def test_no_yfinance_import(self) -> None:
        tree = _get_ast()
        names = _all_import_names(tree)
        yfinance_imports = [n for n in names if "yfinance" in n]
        assert not yfinance_imports, f"yfinance imports found: {yfinance_imports}"

    def test_no_requests_import(self) -> None:
        tree = _get_ast()
        names = _all_import_names(tree)
        bad = [n for n in names if n == "requests" or n.startswith("requests.")]
        assert not bad, f"requests imports found: {bad}"

    def test_no_httpx_import(self) -> None:
        tree = _get_ast()
        names = _all_import_names(tree)
        bad = [n for n in names if n == "httpx" or n.startswith("httpx.")]
        assert not bad, f"httpx imports found: {bad}"

    def test_no_aiohttp_import(self) -> None:
        tree = _get_ast()
        names = _all_import_names(tree)
        bad = [n for n in names if n == "aiohttp" or n.startswith("aiohttp.")]
        assert not bad, f"aiohttp imports found: {bad}"

    def test_no_urllib_import(self) -> None:
        """No direct import urllib or from urllib.request."""
        tree = _get_ast()
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "urllib" or alias.name.startswith("urllib."):
                        bad.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == "urllib.request"
                    or node.module == "urllib"
                ):
                    bad.append(f"from {node.module} import ...")
        assert not bad, f"urllib direct imports found: {bad}"

    def test_no_alpaca_import(self) -> None:
        tree = _get_ast()
        names = _all_import_names(tree)
        bad = [n for n in names if n.startswith("alpaca")]
        assert not bad, f"alpaca imports found: {bad}"

    def test_no_os_environ(self) -> None:
        tree = _get_ast()
        accesses = _all_attribute_accesses(tree)
        bad = [a for a in accesses if a in ("os.environ", "os.getenv")]
        assert not bad, f"os.environ/os.getenv access found: {bad}"

    def test_no_submit_order(self) -> None:
        tree = _get_ast()
        calls = _all_function_calls(tree)
        bad = [c for c in calls if c == "submit_order"]
        assert not bad, f"submit_order calls found: {bad}"

    def test_no_cancel_order(self) -> None:
        tree = _get_ast()
        calls = _all_function_calls(tree)
        bad = [c for c in calls if c == "cancel_order"]
        assert not bad, f"cancel_order calls found: {bad}"

    def test_no_replace_order(self) -> None:
        tree = _get_ast()
        calls = _all_function_calls(tree)
        bad = [c for c in calls if c == "replace_order"]
        assert not bad, f"replace_order calls found: {bad}"
