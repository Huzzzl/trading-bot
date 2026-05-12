"""
execution/alpaca_broker.py
--------------------------
Skeleton for a future Alpaca paper/live broker adapter.

This file defines the class structure only.  Every method raises
``NotImplementedError``.  No Alpaca SDK is imported, no network
connections are made, and no environment variables are read.
"""

from __future__ import annotations

from typing import Any

from src.execution.broker import BrokerAdapter, OrderResult
from src.execution.order_intent import OrderIntent


class AlpacaBrokerAdapter(BrokerAdapter):
    """Placeholder adapter for Alpaca Markets.

    Not yet implemented.  All methods raise ``NotImplementedError``.

    Parameters
    ----------
    api_key:
        Alpaca API key ID.  Accepted but ignored until implementation.
    secret_key:
        Alpaca secret key.  Accepted but ignored until implementation.
    paper:
        When ``True`` (default), targets the Alpaca paper trading endpoint.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
    ) -> None:
        self.api_key    = api_key
        self.secret_key = secret_key
        self.paper      = paper

    def submit_order(self, intent: OrderIntent) -> OrderResult:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def get_positions(self) -> dict[str, Any]:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def get_account(self) -> dict[str, Any]:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")
