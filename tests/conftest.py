"""
tests/conftest.py
------------------
Shared pytest fixtures and autouse patches.

The paper ledger functions are auto-patched for every test that is NOT in
test_paper_ledger.py so that:
- Existing tests never write to the real ledger file on disk.
- Existing tests don't interfere with each other via shared ledger state.
- Only the dedicated ledger tests exercise the real implementation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _auto_patch_paper_ledger(request):
    """No-op the ledger functions for all tests outside test_paper_ledger.py."""
    if "test_paper_ledger" in request.fspath.basename:
        yield  # ledger tests use the real implementation
        return

    with (
        patch("src.execution.paper_ledger.assert_client_order_id_unused"),
        patch("src.execution.paper_ledger.append_ledger_row"),
    ):
        yield
