"""
tests/conftest.py
------------------
Shared pytest fixtures and autouse patches.

The paper ledger functions and daily-limit check are auto-patched for every
test that is NOT in the dedicated test files so that:
- Existing tests never write to the real ledger file on disk.
- Existing tests don't interfere with each other via shared ledger state.
- Existing tests don't trip over a real local ledger that already has today's orders.
- Only the dedicated tests exercise the real implementations.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


_LEDGER_TEST_FILES = {
    "test_paper_ledger.py",
    "test_paper_status.py",
    "test_paper_ledger_import.py",
    "test_paper_ledger_verify.py",
}

_DAILY_LIMITS_TEST_FILES = {
    "test_paper_daily_limits.py",
}

_OPEN_ORDER_GUARD_TEST_FILES = {
    "test_paper_open_order_guard.py",
}

_MARKET_HOURS_GUARD_TEST_FILES = {
    "test_paper_market_hours_guard.py",
}

_KILL_SWITCH_TEST_FILES = {
    "test_paper_kill_switch.py",
}


@pytest.fixture(autouse=True)
def _auto_patch_paper_ledger(request):
    """No-op the ledger functions for all tests outside the ledger test files."""
    if request.fspath.basename in _LEDGER_TEST_FILES:
        yield  # these tests use the real implementation
        return

    with (
        patch("src.execution.paper_ledger.assert_client_order_id_unused"),
        patch("src.execution.paper_ledger.append_ledger_row"),
    ):
        yield


@pytest.fixture(autouse=True)
def _auto_patch_paper_daily_limits(request):
    """No-op assert_within_daily_limits for all tests outside test_paper_daily_limits.py."""
    if request.fspath.basename in _DAILY_LIMITS_TEST_FILES:
        yield  # these tests use the real implementation
        return

    with patch("src.execution.paper_daily_limits.assert_within_daily_limits"):
        yield


@pytest.fixture(autouse=True)
def _auto_patch_paper_open_order_guard(request):
    """No-op assert_no_open_orders_for_symbol for all tests outside test_paper_open_order_guard.py."""
    if request.fspath.basename in _OPEN_ORDER_GUARD_TEST_FILES:
        yield  # these tests use the real implementation
        return

    with patch("src.execution.paper_open_order_guard.assert_no_open_orders_for_symbol"):
        yield


@pytest.fixture(autouse=True)
def _auto_patch_paper_market_hours_guard(request):
    """No-op assert_regular_market_hours for all tests outside test_paper_market_hours_guard.py."""
    if request.fspath.basename in _MARKET_HOURS_GUARD_TEST_FILES:
        yield  # these tests use the real implementation
        return

    with patch("src.execution.paper_market_hours_guard.assert_regular_market_hours"):
        yield


@pytest.fixture(autouse=True)
def _auto_patch_paper_kill_switch(request):
    """No-op assert_kill_switch_disabled for all tests outside test_paper_kill_switch.py."""
    if request.fspath.basename in _KILL_SWITCH_TEST_FILES:
        yield  # these tests use the real implementation
        return

    with patch("src.execution.paper_kill_switch.assert_kill_switch_disabled"):
        yield
