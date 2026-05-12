"""
execution/alpaca_broker.py
--------------------------
Safety-only layer for a future Alpaca paper broker adapter.

Current state
-------------
* Constructor enforces paper=True.
* _validate_credentials() resolves credentials from constructor args or env
  vars and fails closed if either is missing or empty.
* _ensure_market_hours() gates order submission to weekday RTH
  (09:30–16:00 America/New_York).
* All four BrokerAdapter methods still raise NotImplementedError.
* No Alpaca SDK is imported, no network connections are made.
"""

from __future__ import annotations

import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from src.execution.broker import BrokerAdapter, OrderResult
from src.execution.order_intent import OrderIntent

_EASTERN = ZoneInfo("America/New_York")
_MARKET_OPEN  = time(9, 30)
_MARKET_CLOSE = time(16, 0)


class AlpacaBrokerAdapter(BrokerAdapter):
    """Alpaca paper trading adapter — safety layer only, not yet connected.

    Parameters
    ----------
    api_key:
        Alpaca API key ID.  When provided, used by :meth:`_validate_credentials`
        instead of ``ALPACA_API_KEY`` env var.
    secret_key:
        Alpaca secret key.  When provided, used by :meth:`_validate_credentials`
        instead of ``ALPACA_SECRET_KEY`` env var.
    paper:
        Must be ``True`` (default).  ``False`` raises :exc:`ValueError`
        immediately — live trading is not supported.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
    ) -> None:
        if not paper:
            raise ValueError("Live trading is not supported")
        self.api_key    = api_key
        self.secret_key = secret_key
        self.paper      = paper

    # ------------------------------------------------------------------
    # Private safety helpers
    # ------------------------------------------------------------------

    def _validate_credentials(self) -> tuple[str, str]:
        """Resolve and validate Alpaca API credentials.

        Returns
        -------
        tuple[str, str]
            ``(api_key, secret_key)`` — both guaranteed non-empty.

        Raises
        ------
        RuntimeError
            If either credential is missing or empty.

        Notes
        -----
        Credential values are never logged.
        """
        key    = self.api_key    or os.environ.get("ALPACA_API_KEY",    "")
        secret = self.secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        if not key or not secret:
            raise RuntimeError("Missing Alpaca paper API credentials")
        return key, secret

    def _ensure_market_hours(self, now: datetime | None = None) -> None:
        """Raise if the current time is outside regular trading hours.

        Parameters
        ----------
        now:
            Override the current time (for tests).  When ``None``, uses
            ``datetime.now(tz=America/New_York)``.

        Raises
        ------
        RuntimeError
            If the current time is outside Monday–Friday 09:30–16:00 ET.
        """
        if now is None:
            now = datetime.now(tz=_EASTERN)
        else:
            if now.tzinfo is None:
                now = now.replace(tzinfo=_EASTERN)
            else:
                now = now.astimezone(_EASTERN)

        # 0=Monday … 6=Sunday
        if now.weekday() >= 5:
            raise RuntimeError("Outside regular market hours")

        t = now.time().replace(tzinfo=None)
        if not (_MARKET_OPEN <= t < _MARKET_CLOSE):
            raise RuntimeError("Outside regular market hours")

    # ------------------------------------------------------------------
    # BrokerAdapter interface (not yet implemented)
    # ------------------------------------------------------------------

    def submit_order(self, intent: OrderIntent) -> OrderResult:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def get_positions(self) -> dict[str, Any]:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def get_account(self) -> dict[str, Any]:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")
