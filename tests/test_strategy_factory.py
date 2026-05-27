"""
Tests for src/strategy/factory.py.

All tests are offline-only — no broker calls, no credentials, no network
access, no Alpaca import, no config files read. The factory is a pure
construction helper: it does not execute trades or call any broker.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from src.strategy.factory import build_strategy, supported_strategy_names
from src.strategy.opening_range_breakout import OpeningRangeBreakout


# ---------------------------------------------------------------------------
# TestSupportedStrategyNames
# ---------------------------------------------------------------------------

class TestSupportedStrategyNames:
    def test_returns_tuple(self) -> None:
        assert isinstance(supported_strategy_names(), tuple)

    def test_includes_opening_range_breakout(self) -> None:
        assert "opening_range_breakout" in supported_strategy_names()

    def test_includes_orb_alias(self) -> None:
        assert "orb" in supported_strategy_names()

    def test_non_empty(self) -> None:
        assert len(supported_strategy_names()) >= 1

    def test_all_strings(self) -> None:
        for name in supported_strategy_names():
            assert isinstance(name, str)

    def test_stable_across_calls(self) -> None:
        assert supported_strategy_names() == supported_strategy_names()


# ---------------------------------------------------------------------------
# TestBuildStrategyOpeningRangeBreakout
# ---------------------------------------------------------------------------

class TestBuildStrategyOpeningRangeBreakout:
    def test_returns_orb_by_full_name(self) -> None:
        result = build_strategy("opening_range_breakout", {})
        assert isinstance(result, OpeningRangeBreakout)

    def test_returns_orb_by_alias(self) -> None:
        result = build_strategy("orb", {})
        assert isinstance(result, OpeningRangeBreakout)

    def test_full_name_and_alias_return_same_type(self) -> None:
        r1 = build_strategy("opening_range_breakout", {})
        r2 = build_strategy("orb", {})
        assert type(r1) is type(r2)

    def test_params_none_works(self) -> None:
        result = build_strategy("opening_range_breakout", None)
        assert isinstance(result, OpeningRangeBreakout)

    def test_params_none_alias_works(self) -> None:
        result = build_strategy("orb", None)
        assert isinstance(result, OpeningRangeBreakout)

    def test_empty_params_uses_orb_defaults(self) -> None:
        result = build_strategy("opening_range_breakout", {})
        assert result._range_start_str == "09:30"
        assert result._range_end_str == "10:00"
        assert result._force_exit_str == "15:55"
        assert result._breakout_trigger == "close"
        assert result._long_only is True
        assert result._entry_cutoff is None

    def test_explicit_valid_params_passed_through(self) -> None:
        params = {
            "opening_range_start": "09:35",
            "opening_range_end": "10:05",
            "force_exit_time": "15:45",
            "breakout_trigger": "high",
            "long_only": False,
            "entry_cutoff_time": "14:00",
            "position_size_pct": 0.5,
        }
        result = build_strategy("opening_range_breakout", params)
        assert result._range_start_str == "09:35"
        assert result._range_end_str == "10:05"
        assert result._force_exit_str == "15:45"
        assert result._breakout_trigger == "high"
        assert result._long_only is False
        assert result._entry_cutoff == "14:00"

    def test_explicit_params_via_alias(self) -> None:
        params = {"opening_range_start": "09:40", "breakout_trigger": "high"}
        result = build_strategy("orb", params)
        assert result._range_start_str == "09:40"
        assert result._breakout_trigger == "high"

    def test_unknown_extra_params_silently_ignored(self) -> None:
        params = {"some_future_param": 42, "another_unknown": "value"}
        result = build_strategy("opening_range_breakout", params)
        assert isinstance(result, OpeningRangeBreakout)


# ---------------------------------------------------------------------------
# TestParamsNotMutated
# ---------------------------------------------------------------------------

class TestParamsNotMutated:
    def test_params_dict_not_mutated(self) -> None:
        params: dict[str, Any] = {"opening_range_start": "09:30", "breakout_trigger": "close"}
        original = copy.deepcopy(params)
        build_strategy("opening_range_breakout", params)
        assert params == original

    def test_params_dict_not_mutated_alias(self) -> None:
        params: dict[str, Any] = {"breakout_trigger": "high"}
        original = copy.deepcopy(params)
        build_strategy("orb", params)
        assert params == original

    def test_params_length_unchanged(self) -> None:
        params: dict[str, Any] = {"breakout_trigger": "close", "long_only": True}
        original_len = len(params)
        build_strategy("opening_range_breakout", params)
        assert len(params) == original_len


# ---------------------------------------------------------------------------
# TestUnknownStrategyName
# ---------------------------------------------------------------------------

class TestUnknownStrategyName:
    def test_unknown_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_strategy("nonexistent_strategy", {})

    def test_error_message_is_safe(self) -> None:
        raw_name = "totally_secret_strategy_name_xyz"
        with pytest.raises(ValueError) as exc_info:
            build_strategy(raw_name, {})
        assert raw_name not in str(exc_info.value), (
            "raw strategy name must not appear in error message"
        )

    def test_error_message_text(self) -> None:
        with pytest.raises(ValueError, match="unknown strategy name"):
            build_strategy("not_a_strategy", {})

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            build_strategy("", {})

    def test_case_sensitive_uppercase_raises(self) -> None:
        with pytest.raises(ValueError):
            build_strategy("OpeningRangeBreakout", {})

    def test_case_sensitive_mixed_raises(self) -> None:
        with pytest.raises(ValueError):
            build_strategy("ORB", {})

    def test_whitespace_name_raises(self) -> None:
        with pytest.raises(ValueError):
            build_strategy("opening_range_breakout ", {})


# ---------------------------------------------------------------------------
# TestInvalidParams
# ---------------------------------------------------------------------------

class TestInvalidParams:
    def test_invalid_breakout_trigger_raises(self) -> None:
        with pytest.raises(ValueError):
            build_strategy("opening_range_breakout", {"breakout_trigger": "invalid_trigger"})

    def test_invalid_params_error_message_safe(self) -> None:
        secret_value = "super_secret_trigger_value"
        with pytest.raises(ValueError) as exc_info:
            build_strategy("opening_range_breakout", {"breakout_trigger": secret_value})
        assert secret_value not in str(exc_info.value), (
            "raw param value must not appear in error message"
        )

    def test_invalid_params_error_message_text(self) -> None:
        with pytest.raises(ValueError, match="invalid strategy parameters"):
            build_strategy("opening_range_breakout", {"breakout_trigger": "bad"})

    def test_invalid_params_via_alias(self) -> None:
        with pytest.raises(ValueError, match="invalid strategy parameters"):
            build_strategy("orb", {"breakout_trigger": "bad"})


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

def _factory_source_lines() -> list[str]:
    src_path = Path(__file__).parent.parent / "src" / "strategy" / "factory.py"
    lines = []
    for line in src_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_part = line.split("#")[0]
        if code_part.strip():
            lines.append(code_part)
    return lines


class TestSourceScans:
    def test_no_alpaca_import(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            stripped = line.strip()
            assert "import alpaca" not in stripped, f"alpaca import found: {line!r}"
            assert "from alpaca" not in stripped, f"alpaca import found: {line!r}"

    def test_no_requests_import(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "import requests" not in line, f"requests import: {line!r}"

    def test_no_httpx_import(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "import httpx" not in line, f"httpx import: {line!r}"

    def test_no_aiohttp_import(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "import aiohttp" not in line, f"aiohttp import: {line!r}"

    def test_no_urllib_import(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            stripped = line.strip()
            assert not (
                stripped.startswith("import urllib") or stripped.startswith("from urllib")
            ), f"urllib import: {line!r}"

    def test_no_os_environ(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "os.environ" not in line, f"os.environ found: {line!r}"

    def test_no_os_import(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            stripped = line.strip()
            assert stripped != "import os", f"import os found: {line!r}"
            assert not stripped.startswith("from os "), f"from os found: {line!r}"

    def test_no_submit_order(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "submit_order(" not in line, f"submit_order( found: {line!r}"

    def test_no_cancel_order(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "cancel_order(" not in line, f"cancel_order( found: {line!r}"

    def test_no_replace_order(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "replace_order(" not in line, f"replace_order( found: {line!r}"

    def test_no_close_position(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "close_position(" not in line, f"close_position( found: {line!r}"

    def test_no_close_all_positions(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert "close_all_positions(" not in line, f"close_all_positions( found: {line!r}"

    def test_no_post_mutation(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert ".post(" not in line.lower(), f"POST mutation: {line!r}"

    def test_no_patch_mutation(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert ".patch(" not in line.lower(), f"PATCH mutation: {line!r}"

    def test_no_delete_mutation(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            assert ".delete(" not in line.lower(), f"DELETE mutation: {line!r}"

    def test_no_execution_imports(self) -> None:
        lines = _factory_source_lines()
        for line in lines:
            stripped = line.strip()
            assert "from src.execution" not in stripped, f"execution import: {line!r}"
            assert "import src.execution" not in stripped, f"execution import: {line!r}"
