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


# ---------------------------------------------------------------------------
# Alpaca credential isolation
# ---------------------------------------------------------------------------
#
# Developer machines that run real Alpaca paper trading store credentials in
# environment variables. Broker unit tests must NOT inherit those — otherwise
# tests that assert "no credentials" behaviour silently start a real SDK client
# and touch the paper API (submitting/reading real orders). The autouse
# fixture below clears every known Alpaca env var for every test in
# `_BROKER_UNIT_TEST_FILES`. Real values remain in the developer's shell for
# manual use, but the pytest process never sees them for these files.
#
# The paired fixture patches the Alpaca SDK client constructors so any
# accidental path that still tries to create a real client during unit tests
# fails loudly instead of hitting the network.

# Every Alpaca-related env var name the repository (or the SDK) may consult.
_ALPACA_ENV_VARS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "APCA_API_BASE_URL",
    "APCA_API_DATA_URL",
    "ALPACA_PAPER_BASE_URL",
    "ALPACA_BASE_URL",
    "ALPACA_DATA_URL",
)

# All broker/adapter/runner test files must run without inherited Alpaca
# credentials so nothing here can ever hit the paper API.
_BROKER_UNIT_TEST_FILES = {
    "test_alpaca_broker_skeleton.py",
    "test_alpaca_paper_adapter.py",
    "test_run_paper_cycle.py",
    "test_run_automated_paper_cycle.py",
    "test_live_broker_preflight_readonly.py",
    "test_live_account_check.py",
    "test_live_submit.py",
    "test_live_ledger.py",
    "test_live_ledger_verify.py",
    "test_live_shadow_preflight.py",
    "test_live_shadow_screen_symbols.py",
    "test_paper_status.py",
    "test_paper_status_summary.py",
}


@pytest.fixture(autouse=True)
def _clear_alpaca_credentials(request, monkeypatch):
    """Strip every Alpaca env var for broker unit tests.

    This never touches the developer's shell — pytest's monkeypatch only
    mutates the current process env and restores it on teardown. Tests that
    still want to inject explicit env values do so via their own
    ``mock.patch.dict``/``monkeypatch.setenv`` calls, which continue to
    take precedence.
    """
    if request.fspath.basename not in _BROKER_UNIT_TEST_FILES:
        yield
        return

    for name in _ALPACA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _block_real_alpaca_sdk_construction(request):
    """Patch the Alpaca SDK client so tests can't accidentally build one.

    Applies only to files listed in `_BROKER_UNIT_TEST_FILES`. Each test that
    wants to observe a *successful* SDK construction (e.g.
    ``TestFromEnvironment.test_factory_constructs_with_real_sdk_mocked``)
    installs its own ``patch()`` inside the test body, and the inner patch
    wins.
    """
    if request.fspath.basename not in _BROKER_UNIT_TEST_FILES:
        yield
        return

    def _fail(*_args, **_kwargs):
        raise AssertionError(
            "Alpaca TradingClient constructor was invoked from a unit test — "
            "tests must inject a mock client. If this test intentionally "
            "exercises the factory, patch the constructor inside the test."
        )

    with (
        patch("alpaca.trading.client.TradingClient", side_effect=_fail, create=True),
    ):
        yield
