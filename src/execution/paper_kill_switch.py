"""
execution/paper_kill_switch.py
--------------------------------
Emergency kill switch for paper order submissions.

Before any real paper buy-submit or close-submit, main.py calls
assert_kill_switch_disabled() to check the kill switch flag.  If the switch
is enabled, the guard raises before submit_order is ever called.

Set execution.paper_kill_switch_enabled=true in config (or patch it at
runtime) to immediately halt all paper order submissions without changing
any other configuration.

What this module never does
---------------------------
* Never calls submit_order or cancel_order.
* Never writes to the ledger.
* Never reads Alpaca credentials.
* Never enforces during preview-only flows (caller responsibility).
"""

from __future__ import annotations

from src.utils.logger import get_logger

_logger = get_logger(__name__)


def assert_kill_switch_disabled(enabled: bool) -> None:
    """Raise RuntimeError if the paper kill switch is enabled.

    Parameters
    ----------
    enabled:
        The value of ``execution.paper_kill_switch_enabled`` from config.
        When ``True`` the guard raises immediately, blocking submit_order.
        When ``False`` the guard is a no-op.

    Raises
    ------
    RuntimeError
        If *enabled* is ``True``.  The message always contains
        "paper kill switch enabled" and "no order was submitted".
    """
    if enabled:
        raise RuntimeError(
            "paper kill switch enabled — no order was submitted. "
            "Set execution.paper_kill_switch_enabled=false to re-enable submissions."
        )

    _logger.info("Kill-switch check passed: paper_kill_switch_enabled=false.")
