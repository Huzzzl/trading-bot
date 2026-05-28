"""
tests/test_cached_data_availability_check.py
--------------------------------------------
Tests for src/tools/cached_data_availability_check.py.

Rules:
  - No network access. No yfinance. No Alpaca. No credentials.
  - All filesystem operations use tmp_path.
  - No real cache files. No raw market data.
  - No file writes (except the optional --output path test).
  - broker_calls_made=False in all results.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pandas as pd
import pytest

from src.tools.cached_data_availability_check import check_cache, main


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------


def _write_csv_fixture(
    path: pathlib.Path,
    symbol: str,
    interval: str,
    valid: bool = True,
) -> None:
    """Write a small CSV fixture to *path* using the expected filename pattern."""
    from zoneinfo import ZoneInfo

    dates = pd.bdate_range("2024-01-02", periods=5, tz=ZoneInfo("America/New_York"))
    if valid:
        df = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "high": [101.0] * 5,
                "low": [99.0] * 5,
                "close": [100.5] * 5,
                "volume": [1000.0] * 5,
            },
            index=dates,
        )
    else:
        df = pd.DataFrame({"bad_col": [1] * 5}, index=dates)
    # filename pattern: {symbol}_{start}_{end}_{interval}.csv
    fname = f"{symbol}_2024-01-02_2024-01-08_{interval}.csv"
    df.to_csv(path / fname)


# ---------------------------------------------------------------------------
# TestMissingCacheDir
# ---------------------------------------------------------------------------


class TestMissingCacheDir:
    """Missing cache directory → BLOCKED with descriptive blocked entries."""

    def test_result_is_blocked(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_such_dir"
        result = check_cache(cache_dir=nonexistent, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "BLOCKED"

    def test_blocked_entries_populated(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_such_dir"
        result = check_cache(cache_dir=nonexistent, symbols=["SPY"], intervals=["1d"])
        assert len(result["blocked_entries"]) >= 1

    def test_blocked_message_contains_cache_dir(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_such_dir"
        result = check_cache(cache_dir=nonexistent, symbols=["SPY"], intervals=["1d"])
        reasons = [e["reason"] for e in result["blocked_entries"]]
        assert any("cache directory" in r or "not found" in r for r in reasons)

    def test_missing_entries_populated(self, tmp_path: pathlib.Path) -> None:
        nonexistent = tmp_path / "no_such_dir"
        result = check_cache(cache_dir=nonexistent, symbols=["SPY"], intervals=["1d"])
        assert {"symbol": "SPY", "interval": "1d"} in result["missing_entries"]


# ---------------------------------------------------------------------------
# TestMissingFiles
# ---------------------------------------------------------------------------


class TestMissingFiles:
    """Cache dir exists but no files → BLOCKED, missing_entries populated."""

    def test_result_is_blocked_when_empty_dir(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(
            cache_dir=tmp_path, symbols=["SPY", "QQQ"], intervals=["1d", "60m"]
        )
        assert result["result"] == "BLOCKED"

    def test_all_pairs_in_missing_entries(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(
            cache_dir=tmp_path, symbols=["SPY", "QQQ"], intervals=["1d", "60m"]
        )
        missing_pairs = {(e["symbol"], e["interval"]) for e in result["missing_entries"]}
        assert ("SPY", "1d") in missing_pairs
        assert ("SPY", "60m") in missing_pairs
        assert ("QQQ", "1d") in missing_pairs
        assert ("QQQ", "60m") in missing_pairs

    def test_files_checked_status_not_found(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        statuses = {f["status"] for f in result["files_checked"]}
        assert statuses == {"NOT_FOUND"}


# ---------------------------------------------------------------------------
# TestValidCache
# ---------------------------------------------------------------------------


class TestValidCache:
    """Write valid CSV fixtures → PASS."""

    def test_pass_when_all_files_present(self, tmp_path: pathlib.Path) -> None:
        for symbol in ["SPY", "QQQ"]:
            for interval in ["1d", "60m"]:
                _write_csv_fixture(tmp_path, symbol, interval)
        result = check_cache(
            cache_dir=tmp_path, symbols=["SPY", "QQQ"], intervals=["1d", "60m"]
        )
        assert result["result"] == "PASS"

    def test_pass_single_pair(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "PASS"

    def test_ok_file_has_row_count(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        ok_entries = [f for f in result["files_checked"] if f["status"] == "OK"]
        assert len(ok_entries) == 1
        assert ok_entries[0]["rows"] == 5

    def test_missing_entries_empty_on_pass(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["missing_entries"] == []

    def test_blocked_entries_empty_on_pass(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["blocked_entries"] == []


# ---------------------------------------------------------------------------
# TestIntervalAliasing
# ---------------------------------------------------------------------------


class TestIntervalAliasing:
    """60m file found when checking 1h (and vice versa) → PASS."""

    def test_60m_file_satisfies_1h_check(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "60m")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1h"])
        assert result["result"] == "PASS"

    def test_1h_file_satisfies_60m_check(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1h")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["60m"])
        assert result["result"] == "PASS"

    def test_60m_file_present_directly_satisfies_60m_check(
        self, tmp_path: pathlib.Path
    ) -> None:
        _write_csv_fixture(tmp_path, "SPY", "60m")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["60m"])
        assert result["result"] == "PASS"

    def test_interval_alias_does_not_affect_non_alias_intervals(
        self, tmp_path: pathlib.Path
    ) -> None:
        # 1d should not be affected by 60m/1h aliasing
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "PASS"
        assert result["missing_entries"] == []


# ---------------------------------------------------------------------------
# TestInvalidColumns
# ---------------------------------------------------------------------------


class TestInvalidColumns:
    """File exists but missing 'close' column → BLOCKED."""

    def test_invalid_columns_causes_blocked(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d", valid=False)
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["result"] == "BLOCKED"

    def test_invalid_file_in_missing_entries(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d", valid=False)
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert {"symbol": "SPY", "interval": "1d"} in result["missing_entries"]

    def test_invalid_file_status_is_invalid_columns(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d", valid=False)
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        invalid_entries = [
            f for f in result["files_checked"] if f["status"] == "INVALID_COLUMNS"
        ]
        assert len(invalid_entries) >= 1

    def test_valid_file_not_blocked_by_other_invalid_pair(
        self, tmp_path: pathlib.Path
    ) -> None:
        """SPY/1d valid; SPY/60m invalid → BLOCKED overall but SPY/1d shows OK."""
        _write_csv_fixture(tmp_path, "SPY", "1d", valid=True)
        _write_csv_fixture(tmp_path, "SPY", "60m", valid=False)
        result = check_cache(
            cache_dir=tmp_path, symbols=["SPY"], intervals=["1d", "60m"]
        )
        assert result["result"] == "BLOCKED"
        ok_entries = [f for f in result["files_checked"] if f["status"] == "OK"]
        assert any(e["interval"] == "1d" for e in ok_entries)


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """Safety flags must always be False."""

    def test_broker_calls_made_false(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(cache_dir=tmp_path)
        assert result["broker_calls_made"] is False

    def test_credentials_read_false(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(cache_dir=tmp_path)
        assert result["credentials_read"] is False

    def test_network_calls_made_false(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(cache_dir=tmp_path)
        assert result["network_calls_made"] is False

    def test_order_action_requested_false(self, tmp_path: pathlib.Path) -> None:
        result = check_cache(cache_dir=tmp_path)
        assert result["order_action_requested"] is False

    def test_safety_flags_false_on_pass(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result["broker_calls_made"] is False
        assert result["credentials_read"] is False
        assert result["network_calls_made"] is False
        assert result["order_action_requested"] is False


# ---------------------------------------------------------------------------
# TestNoPricesEmitted
# ---------------------------------------------------------------------------


class TestNoPricesEmitted:
    """Output dict must not contain raw price floats; numeric values are row counts only."""

    def test_no_price_floats_in_output(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])

        def _collect_floats(obj: object) -> list[float]:
            floats: list[float] = []
            if isinstance(obj, float):
                floats.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    floats.extend(_collect_floats(v))
            elif isinstance(obj, list):
                for item in obj:
                    floats.extend(_collect_floats(item))
            return floats

        found_floats = _collect_floats(result)
        assert not found_floats, (
            f"Result dict contains raw float values: {found_floats}. "
            "Only integer row counts are permitted."
        )

    def test_row_count_is_integer(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        ok_entries = [f for f in result["files_checked"] if "rows" in f]
        for entry in ok_entries:
            assert isinstance(entry["rows"], int)


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same call twice → identical result dict."""

    def test_same_result_on_repeat_call(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        result_a = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        result_b = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result_a == result_b

    def test_same_result_blocked_on_repeat_call(self, tmp_path: pathlib.Path) -> None:
        result_a = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        result_b = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert result_a == result_b


# ---------------------------------------------------------------------------
# TestOutputJson
# ---------------------------------------------------------------------------


class TestOutputJson:
    """With a tmp output path, JSON file is written correctly."""

    def test_json_file_written(self, tmp_path: pathlib.Path) -> None:
        output_path = tmp_path / "result.json"
        main(["--cache-dir", str(tmp_path), "--output", str(output_path)])
        assert output_path.is_file()

    def test_json_content_is_valid(self, tmp_path: pathlib.Path) -> None:
        output_path = tmp_path / "result.json"
        main(["--cache-dir", str(tmp_path), "--output", str(output_path)])
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert "result" in data
        assert "broker_calls_made" in data

    def test_json_result_matches_check_cache(self, tmp_path: pathlib.Path) -> None:
        _write_csv_fixture(tmp_path, "SPY", "1d")
        output_path = tmp_path / "out.json"
        main(
            [
                "--cache-dir",
                str(tmp_path),
                "--symbols",
                "SPY",
                "--intervals",
                "1d",
                "--output",
                str(output_path),
            ]
        )
        data = json.loads(output_path.read_text(encoding="utf-8"))
        expected = check_cache(cache_dir=tmp_path, symbols=["SPY"], intervals=["1d"])
        assert data["result"] == expected["result"]
        assert data["missing_entries"] == expected["missing_entries"]


# ---------------------------------------------------------------------------
# TestSourceScan
# ---------------------------------------------------------------------------


class TestSourceScan:
    """AST-based source scan: forbidden imports and patterns must be absent."""

    _SRC_PATH = (
        pathlib.Path(__file__).parent.parent
        / "src"
        / "tools"
        / "cached_data_availability_check.py"
    )

    def _get_tree(self) -> ast.Module:
        return ast.parse(self._SRC_PATH.read_text(encoding="utf-8"))

    def _all_import_names(self) -> list[str]:
        """Collect all imported module names (top-level) from the source."""
        tree = self._get_tree()
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_yfinance_import(self) -> None:
        names = self._all_import_names()
        assert not any("yfinance" in n for n in names), "yfinance imported"

    def test_no_requests_import(self) -> None:
        names = self._all_import_names()
        assert not any(n == "requests" or n.startswith("requests.") for n in names)

    def test_no_httpx_import(self) -> None:
        names = self._all_import_names()
        assert not any("httpx" in n for n in names)

    def test_no_aiohttp_import(self) -> None:
        names = self._all_import_names()
        assert not any("aiohttp" in n for n in names)

    def test_no_urllib_import(self) -> None:
        names = self._all_import_names()
        assert not any("urllib" in n for n in names)

    def test_no_alpaca_import(self) -> None:
        names = self._all_import_names()
        assert not any("alpaca" in n for n in names)

    def test_no_os_environ(self) -> None:
        """Check via AST that os.environ is never accessed (docstring mentions are OK)."""
        tree = self._get_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr in ("environ", "getenv")
                ):
                    raise AssertionError(
                        f"os.{node.attr} used at line {node.lineno} — forbidden"
                    )

    def test_no_submit_order(self) -> None:
        src = self._SRC_PATH.read_text(encoding="utf-8")
        assert "submit_order" not in src

    def test_no_cancel_order(self) -> None:
        src = self._SRC_PATH.read_text(encoding="utf-8")
        assert "cancel_order" not in src

    def test_no_replace_order(self) -> None:
        src = self._SRC_PATH.read_text(encoding="utf-8")
        assert "replace_order" not in src
