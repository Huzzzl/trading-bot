"""
execution/alpaca_broker.py
--------------------------
Paper-only Alpaca broker adapter.

Current state
-------------
* Constructor enforces paper=True.
* _validate_credentials() resolves credentials from constructor args or env
  vars and fails closed if either is missing or empty.
* _get_client() lazily creates an alpaca-py TradingClient (paper=True) on
  first call, caches it, and returns it.  An injected client bypasses
  creation entirely (used by tests).
* _ensure_market_hours() gates order submission to weekday RTH
  (09:30–16:00 America/New_York).
* get_positions(), get_account(), and cancel_order() still raise
  NotImplementedError.
"""

from __future__ import annotations

import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

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
    client:
        Optional pre-built broker client (e.g. a mock).  When ``None``
        (default), :meth:`_get_client` raises :exc:`NotImplementedError`
        until a real client factory is wired in.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
        client: Any | None = None,
    ) -> None:
        if not paper:
            raise ValueError("Live trading is not supported")
        self.api_key    = api_key
        self.secret_key = secret_key
        self.paper      = paper
        self._client    = client

    # ------------------------------------------------------------------
    # Private safety helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return the broker client, creating it lazily if necessary.

        If a client was injected at construction time it is returned as-is
        (test / mock path).  Otherwise credentials are resolved via
        :meth:`_validate_credentials` and an ``alpaca.trading.client.TradingClient``
        is created with ``paper=True`` and cached in ``self._client``.

        The optional ``ALPACA_PAPER_BASE_URL`` env var overrides the SDK's
        default paper endpoint (useful for integration test stubs).

        Raises
        ------
        RuntimeError
            If credentials are missing or empty.
        """
        if self._client is not None:
            return self._client

        api_key, secret_key = self._validate_credentials()

        from alpaca.trading.client import TradingClient  # lazy import — SDK not required at module load

        url_override = os.environ.get("ALPACA_PAPER_BASE_URL") or None
        self._client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=True,
            url_override=url_override,
        )
        return self._client

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

    def _validate_order_intent(self, intent: OrderIntent) -> None:
        """Validate an :class:`OrderIntent` before any broker submission.

        Raises
        ------
        NotImplementedError
            If ``order_type`` is not ``"market"``.
        ValueError
            If any required field is missing, empty, or out of range.
        """
        if intent.order_type != "market":
            raise NotImplementedError("Only market orders are supported")
        if not intent.client_order_id or not str(intent.client_order_id).strip():
            raise ValueError("client_order_id is required")
        if intent.quantity <= 0:
            raise ValueError("quantity must be positive")
        if not intent.symbol or not intent.symbol.strip():
            raise ValueError("symbol is required")
        if intent.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")

    @staticmethod
    def _normalize_status(status: str) -> str:
        """Map an Alpaca order status string to an internal ``OrderResult.status``.

        Parameters
        ----------
        status:
            Raw status string from the Alpaca API.  Leading/trailing whitespace
            is stripped and comparison is case-insensitive.

        Returns
        -------
        str
            One of ``"accepted"``, ``"filled"``, ``"cancelled"``, or
            ``"rejected"``.

        Raises
        ------
        ValueError
            If *status* does not match any known Alpaca status.
        """
        s = status.strip().lower()
        if s in {"new", "pending_new", "accepted", "accepted_for_bidding"}:
            return "accepted"
        if s in {"filled", "partially_filled"}:
            return "filled"
        if s in {"canceled", "cancelled", "expired", "replaced"}:
            return "cancelled"
        if s in {"rejected", "stopped", "suspended", "calculated"}:
            return "rejected"
        raise ValueError(f"Unknown Alpaca order status: {status!r}")

    @staticmethod
    def _is_partial_fill(status: str) -> bool:
        """Return ``True`` if *status* indicates a partial fill.

        Parameters
        ----------
        status:
            Raw status string from the Alpaca API.  Leading/trailing whitespace
            is stripped and comparison is case-insensitive.
        """
        return status.strip().lower() == "partially_filled"

    def _build_order_payload(self, intent: OrderIntent) -> dict[str, Any]:
        """Convert an :class:`OrderIntent` into an Alpaca market order request dict.

        Calls :meth:`_validate_order_intent` first; any validation error is
        propagated to the caller unchanged.

        Returns
        -------
        dict
            Ready-to-submit Alpaca order payload (market orders only).
        """
        self._validate_order_intent(intent)
        return {
            "symbol":          intent.symbol,
            "side":            intent.side,
            "qty":             intent.quantity,
            "type":            "market",
            "time_in_force":   "day",
            "client_order_id": intent.client_order_id,
        }

    def _order_response_to_result(self, response: Any, intent: OrderIntent) -> OrderResult:
        """Convert an Alpaca order response to an :class:`OrderResult`.

        Accepts either a ``dict``-like object or an attribute-based object
        (e.g. an Alpaca SDK model).

        Parameters
        ----------
        response:
            The raw order response from Alpaca.
        intent:
            The originating :class:`OrderIntent`; used to supply ``reason``
            and as a fallback for ``client_order_id``.

        Raises
        ------
        ValueError
            If the response status is not a recognised Alpaca status string.
        """
        def _get(key: str, default: Any = None) -> Any:
            if isinstance(response, dict):
                return response.get(key, default)
            return getattr(response, key, default)

        raw_status = str(_get("status", ""))
        status     = self._normalize_status(raw_status)

        # Timestamps — accept str, datetime, or None
        def _to_ts(val: Any) -> pd.Timestamp | None:
            if val is None:
                return None
            return pd.Timestamp(val)

        submitted_at = _to_ts(_get("submitted_at"))
        filled_at    = _to_ts(_get("filled_at"))

        raw_filled_price = _get("filled_avg_price")
        filled_price     = float(raw_filled_price) if raw_filled_price is not None else None

        raw_qty    = _get("qty")
        filled_qty = _get("filled_qty")

        response_cid = _get("client_order_id")
        client_order_id = response_cid if response_cid is not None else intent.client_order_id

        metadata: dict[str, Any] = {"raw_status": raw_status}
        if filled_qty is not None:
            metadata["filled_qty"] = filled_qty
        metadata["partial_fill"] = self._is_partial_fill(raw_status)

        return OrderResult(
            order_id        = str(_get("id", "")),
            symbol          = str(_get("symbol", "")),
            side            = str(_get("side", "")),
            quantity        = float(raw_qty) if raw_qty is not None else intent.quantity,
            status          = status,
            submitted_at    = submitted_at or intent.timestamp,
            reason          = intent.reason,
            filled_at       = filled_at,
            filled_price    = filled_price,
            metadata        = metadata,
            client_order_id = client_order_id,
        )

    def _account_response_to_dict(self, response: Any) -> dict[str, Any]:
        """Convert an Alpaca account response to a plain dict.

        Accepts either a ``dict``-like object or an attribute-based object.
        Numeric fields are cast to ``float``; boolean fields to ``bool``.
        Missing fields become ``None``.
        """
        def _get(key: str) -> Any:
            if isinstance(response, dict):
                return response.get(key)
            return getattr(response, key, None)

        def _float(val: Any) -> float | None:
            return float(val) if val is not None else None

        def _bool(val: Any) -> bool | None:
            if val is None:
                return None
            if isinstance(val, bool):
                return val
            if isinstance(val, int):
                if val == 1:
                    return True
                if val == 0:
                    return False
            if isinstance(val, str):
                if val.lower() in {"true", "1"}:
                    return True
                if val.lower() in {"false", "0"}:
                    return False
            raise ValueError("Invalid boolean value in Alpaca account response")

        return {
            "id":              _get("id"),
            "status":          _get("status"),
            "currency":        _get("currency"),
            "cash":            _float(_get("cash")),
            "equity":          _float(_get("equity")),
            "buying_power":    _float(_get("buying_power")),
            "trading_blocked": _bool(_get("trading_blocked")),
            "account_blocked": _bool(_get("account_blocked")),
        }

    def _position_response_to_dict(self, response: Any) -> dict[str, Any]:
        """Convert an Alpaca position response to a plain dict.

        Accepts either a ``dict``-like object or an attribute-based object.
        Numeric fields are cast to ``float``.  Missing fields become ``None``.
        """
        def _get(key: str) -> Any:
            if isinstance(response, dict):
                return response.get(key)
            return getattr(response, key, None)

        def _float(val: Any) -> float | None:
            return float(val) if val is not None else None

        return {
            "symbol":          _get("symbol"),
            "qty":             _float(_get("qty")),
            "market_value":    _float(_get("market_value")),
            "avg_entry_price": _float(_get("avg_entry_price")),
            "current_price":   _float(_get("current_price")),
            "unrealized_pl":   _float(_get("unrealized_pl")),
        }

    def _validate_startup_state(
        self,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        symbols: list[str],
    ) -> None:
        """Validate account state and open positions before trading begins.

        Raises
        ------
        RuntimeError
            If the account is not active, trading is blocked, or any open
            position overlaps the target symbol list.
        """
        if account.get("status") != "ACTIVE":
            raise RuntimeError("Alpaca account is not active")
        if account.get("trading_blocked") is True:
            raise RuntimeError("Alpaca account trading is blocked")
        if account.get("account_blocked") is True:
            raise RuntimeError("Alpaca account is blocked")

        target = {s.upper() for s in symbols}
        for pos in positions:
            sym = pos.get("symbol")
            if sym and sym.upper() in target:
                raise RuntimeError("Unexpected open positions for target symbols")

    # ------------------------------------------------------------------
    # BrokerAdapter interface
    # ------------------------------------------------------------------

    def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Submit a market order through the injected client.

        Raises
        ------
        NotImplementedError
            If no client was injected (``self._client is None``).
        RuntimeError
            If the current time is outside regular market hours.
        NotImplementedError
            If ``intent.order_type`` is not ``"market"``.
        ValueError
            If any required field on *intent* is invalid.
        """
        if self._client is None:
            raise NotImplementedError("Alpaca client is not implemented yet.")

        self._validate_order_intent(intent)
        self._ensure_market_hours()
        payload = self._build_order_payload(intent)

        client = self._get_client()
        if hasattr(client, "submit_order"):
            response = client.submit_order(payload)
        elif hasattr(client, "create_order"):
            response = client.create_order(payload)
        else:
            raise NotImplementedError(
                "Injected Alpaca client must provide submit_order or create_order"
            )

        return self._order_response_to_result(response, intent)

    def get_positions(self) -> dict[str, Any]:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def get_account(self) -> dict[str, Any]:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("AlpacaBrokerAdapter is not implemented yet.")
