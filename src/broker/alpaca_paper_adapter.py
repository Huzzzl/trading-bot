"""Minimal Alpaca paper trading adapter — MVP scope.

Supports the six operations needed for a basic automated paper-trading loop:
account/position/order/clock reads, market-order submission, and order
cancellation. SPY only, long only, market orders only, buy and sell.

The adapter uses an injected Alpaca client so unit tests stay mocked.
A small `from_environment()` factory reads paper credentials from env vars
and constructs a real `alpaca.trading.client.TradingClient` configured for
the paper environment. Live trading is not supported — constructing the
adapter with `paper=False` or with a live base URL raises immediately.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

_ALLOWED_SYMBOLS = frozenset({"SPY"})
_ALLOWED_SIDES = frozenset({"buy", "sell"})
_LIVE_HOSTS = ("api.alpaca.markets",)
_PAPER_HOST = "paper-api.alpaca.markets"


class AlpacaPaperAdapterError(RuntimeError):
    """Raised when the adapter cannot satisfy a request from the broker."""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_str(val: Any) -> str | None:
    if val is None:
        return None
    # Handle generic Python Enum instances: real Alpaca SDK enum values like
    # AccountStatus.ACTIVE expose the broker's underlying string via .value
    # (e.g. "ACTIVE"). Returning str(val.value) gives the canonical broker
    # string without relying on a hard-coded class-name prefix list.
    if isinstance(val, Enum):
        return str(val.value)
    s = str(val)
    if "." in s and s.startswith(("OrderSide.", "OrderStatus.", "TimeInForce.", "OrderType.")):
        s = s.rsplit(".", 1)[-1]
    return s


_NOT_FOUND_MARKERS = (
    "not found", "no position", "does not exist",
    "404", "no order", "resource not found",
)


def _looks_like_not_found(exc: BaseException) -> bool:
    """Return True if *exc* is a broker-not-found response for a
    lookup call. Position and order lookups on Alpaca raise on absence
    rather than returning ``None``; this helper lets adapter methods
    normalize that surface into a proper ``None`` return.
    """
    if isinstance(exc, AlpacaPaperAdapterError):
        return False
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    return any(marker in msg for marker in _NOT_FOUND_MARKERS)


def _reject_live_base_url(base_url: str | None) -> None:
    if base_url is None or not base_url:
        return
    lowered = base_url.lower()
    for host in _LIVE_HOSTS:
        if host in lowered and _PAPER_HOST not in lowered:
            raise AlpacaPaperAdapterError(
                "live Alpaca base URL is not permitted by this adapter"
            )


class AlpacaPaperAdapter:
    """Paper-only Alpaca adapter.

    Parameters
    ----------
    client:
        Injected Alpaca client (e.g. ``alpaca.trading.client.TradingClient``).
        Tests pass a mock object; production callers use
        :meth:`from_environment` to build a real client.
    paper:
        Must be ``True``. Passing ``False`` raises immediately.
    base_url:
        Informational only; the live host substring is rejected to prevent
        accidental live wiring. The actual URL the SDK uses is controlled
        by the underlying client.
    """

    def __init__(
        self,
        *,
        client: Any,
        paper: bool = True,
        base_url: str | None = None,
    ) -> None:
        if not paper:
            raise AlpacaPaperAdapterError("live trading is not supported")
        if client is None:
            raise AlpacaPaperAdapterError("client is required")
        _reject_live_base_url(base_url)
        self._client = client
        self._base_url = base_url

    @classmethod
    def from_environment(cls) -> "AlpacaPaperAdapter":
        """Build an adapter from paper credentials in environment variables.

        Reads ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY``. Optionally
        respects ``ALPACA_PAPER_BASE_URL`` for endpoint override
        (useful for integration test stubs); the value must not point at
        the live host.
        """
        api_key = os.environ.get("ALPACA_API_KEY", "").strip()
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
        if not api_key or not secret_key:
            raise AlpacaPaperAdapterError(
                "missing ALPACA_API_KEY or ALPACA_SECRET_KEY in environment"
            )
        base_url = os.environ.get("ALPACA_PAPER_BASE_URL") or None
        _reject_live_base_url(base_url)

        from alpaca.trading.client import TradingClient  # lazy import

        client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=True,
            url_override=base_url,
        )
        return cls(client=client, paper=True, base_url=base_url)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        try:
            raw = self._client.get_account()
        except Exception as exc:
            raise AlpacaPaperAdapterError(f"get_account failed: {exc}") from exc
        return {
            "status": _to_str(_get(raw, "status")),
            "cash": _to_float(_get(raw, "cash")),
            "buying_power": _to_float(_get(raw, "buying_power")),
            "equity": _to_float(_get(raw, "equity")),
            "currency": _to_str(_get(raw, "currency")),
            "pattern_day_trader": bool(_get(raw, "pattern_day_trader", False)),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        try:
            raw = self._client.get_all_positions()
        except Exception as exc:
            raise AlpacaPaperAdapterError(f"get_positions failed: {exc}") from exc
        return [self._normalize_position(p) for p in (raw or [])]

    def get_open_orders(self) -> list[dict[str, Any]]:
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            raw = self._client.get_orders(filter=request)
        except ImportError:
            try:
                raw = self._client.get_orders()
            except Exception as exc:
                raise AlpacaPaperAdapterError(
                    f"get_open_orders failed: {exc}"
                ) from exc
        except Exception as exc:
            raise AlpacaPaperAdapterError(
                f"get_open_orders failed: {exc}"
            ) from exc
        return [self._normalize_order(o) for o in (raw or [])]

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Return the normalized position for ``symbol``, or ``None`` if none.

        The Alpaca SDK raises when the account has no position for the
        requested symbol; we normalize that into a ``None`` return so
        callers don't need to guess the exception shape.
        """
        if not isinstance(symbol, str) or symbol not in _ALLOWED_SYMBOLS:
            raise AlpacaPaperAdapterError(
                f"symbol must be one of {sorted(_ALLOWED_SYMBOLS)}"
            )
        try:
            raw = self._client.get_open_position(symbol)
        except AttributeError:
            try:
                raw = self._client.get_position(symbol)
            except Exception as exc:
                if _looks_like_not_found(exc):
                    return None
                raise AlpacaPaperAdapterError(
                    f"get_position failed: {exc}"
                ) from exc
        except Exception as exc:
            if _looks_like_not_found(exc):
                return None
            raise AlpacaPaperAdapterError(
                f"get_position failed: {exc}"
            ) from exc
        return self._normalize_position(raw)

    def list_open_orders(
        self, *, symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the currently-open orders, optionally filtered to ``symbol``.

        Thin wrapper around ``get_open_orders`` that adds server-side
        request filtering when available and client-side filtering
        otherwise.
        """
        orders = self.get_open_orders()
        if symbol is None:
            return orders
        return [o for o in orders if o.get("symbol") == symbol]

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch a single order by its broker order id."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise AlpacaPaperAdapterError("order_id must be a non-empty string")
        try:
            raw = self._client.get_order_by_id(order_id)
        except AttributeError:
            try:
                raw = self._client.get_order(order_id)
            except Exception as exc:
                raise AlpacaPaperAdapterError(
                    f"get_order failed: {exc}"
                ) from exc
        except Exception as exc:
            raise AlpacaPaperAdapterError(
                f"get_order failed: {exc}"
            ) from exc
        return self._normalize_order(raw)

    def get_order_by_client_order_id(
        self, client_order_id: str,
    ) -> dict[str, Any] | None:
        """Reconcile a possibly-submitted order by its ``client_order_id``.

        Returns ``None`` when the broker reports no matching order.
        Raises :class:`AlpacaPaperAdapterError` on any other failure.
        """
        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise AlpacaPaperAdapterError(
                "client_order_id must be a non-empty string"
            )
        try:
            raw = self._client.get_order_by_client_order_id(client_order_id)
        except AttributeError:
            try:
                raw = self._client.get_order_by_client_id(client_order_id)
            except Exception as exc:
                if _looks_like_not_found(exc):
                    return None
                raise AlpacaPaperAdapterError(
                    f"get_order_by_client_order_id failed: {exc}"
                ) from exc
        except Exception as exc:
            if _looks_like_not_found(exc):
                return None
            raise AlpacaPaperAdapterError(
                f"get_order_by_client_order_id failed: {exc}"
            ) from exc
        return self._normalize_order(raw)

    def get_clock(self) -> dict[str, Any]:
        try:
            raw = self._client.get_clock()
        except Exception as exc:
            raise AlpacaPaperAdapterError(f"get_clock failed: {exc}") from exc
        return {
            "timestamp": _to_str(_get(raw, "timestamp")),
            "is_open": bool(_get(raw, "is_open", False)),
            "next_open": _to_str(_get(raw, "next_open")),
            "next_close": _to_str(_get(raw, "next_close")),
        }

    # ------------------------------------------------------------------
    # Order operations
    # ------------------------------------------------------------------

    def submit_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        *,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_order(symbol=symbol, qty=qty, side=side)
        if side == "sell":
            self._ensure_sufficient_long_position(symbol=symbol, qty=qty)
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
        except ImportError as exc:
            raise AlpacaPaperAdapterError(
                f"submit_market_order failed: {exc}"
            ) from exc
        try:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
            raw = self._client.submit_order(order_data=request)
        except Exception as exc:
            raise AlpacaPaperAdapterError(
                f"submit_market_order failed: {exc}"
            ) from exc
        return self._normalize_order(raw)

    def _ensure_sufficient_long_position(self, *, symbol: str, qty: float) -> None:
        try:
            positions = self._client.get_all_positions() or []
        except Exception as exc:
            raise AlpacaPaperAdapterError(
                f"sell rejected: cannot read positions: {exc}"
            ) from exc
        held: float | None = None
        for raw in positions:
            raw_symbol = _to_str(_get(raw, "symbol"))
            if raw_symbol != symbol:
                continue
            raw_side = _to_str(_get(raw, "side"))
            if raw_side is None or raw_side.lower() != "long":
                raise AlpacaPaperAdapterError(
                    f"sell rejected: {symbol} position side is not long"
                )
            raw_qty = _to_float(_get(raw, "qty"))
            if (
                raw_qty is None
                or isinstance(raw_qty, bool)
                or raw_qty != raw_qty
                or raw_qty in (float("inf"), float("-inf"))
                or raw_qty <= 0
            ):
                raise AlpacaPaperAdapterError(
                    f"sell rejected: {symbol} position qty is malformed"
                )
            held = raw_qty
            break
        if held is None:
            raise AlpacaPaperAdapterError(
                f"sell rejected: no {symbol} position to sell"
            )
        if qty > held:
            raise AlpacaPaperAdapterError(
                f"sell rejected: requested qty exceeds held {symbol} qty"
            )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if not isinstance(order_id, str) or not order_id.strip():
            raise AlpacaPaperAdapterError("order_id must be a non-empty string")
        try:
            self._client.cancel_order_by_id(order_id)
        except AttributeError:
            try:
                self._client.cancel_order(order_id)
            except Exception as exc:
                raise AlpacaPaperAdapterError(
                    f"cancel_order failed: {exc}"
                ) from exc
        except Exception as exc:
            raise AlpacaPaperAdapterError(f"cancel_order failed: {exc}") from exc
        return {"order_id": order_id, "status": "cancel_requested"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_order(self, *, symbol: str, qty: float, side: str) -> None:
        if not isinstance(symbol, str) or symbol not in _ALLOWED_SYMBOLS:
            raise AlpacaPaperAdapterError(
                f"symbol must be one of {sorted(_ALLOWED_SYMBOLS)}"
            )
        if isinstance(qty, bool) or not isinstance(qty, (int, float)):
            raise AlpacaPaperAdapterError("qty must be a positive number")
        if qty != qty or qty in (float("inf"), float("-inf")) or qty <= 0:
            raise AlpacaPaperAdapterError("qty must be a positive finite number")
        if not isinstance(side, str) or side not in _ALLOWED_SIDES:
            raise AlpacaPaperAdapterError("side must be 'buy' or 'sell'")

    @staticmethod
    def _normalize_position(raw: Any) -> dict[str, Any]:
        return {
            "symbol": _to_str(_get(raw, "symbol")),
            "qty": _to_float(_get(raw, "qty")),
            "side": _to_str(_get(raw, "side")),
            "avg_entry_price": _to_float(_get(raw, "avg_entry_price")),
            "market_value": _to_float(_get(raw, "market_value")),
            "unrealized_pl": _to_float(_get(raw, "unrealized_pl")),
            "current_price": _to_float(_get(raw, "current_price")),
        }

    @staticmethod
    def _normalize_order(raw: Any) -> dict[str, Any]:
        return {
            "id": _to_str(_get(raw, "id")),
            "client_order_id": _to_str(_get(raw, "client_order_id")),
            "symbol": _to_str(_get(raw, "symbol")),
            "side": _to_str(_get(raw, "side")),
            "type": _to_str(_get(raw, "type") or _get(raw, "order_type")),
            "time_in_force": _to_str(_get(raw, "time_in_force")),
            "qty": _to_float(_get(raw, "qty")),
            "filled_qty": _to_float(_get(raw, "filled_qty")),
            "filled_avg_price": _to_float(_get(raw, "filled_avg_price")),
            "status": _to_str(_get(raw, "status")),
            "submitted_at": _to_str(_get(raw, "submitted_at")),
            "filled_at": _to_str(_get(raw, "filled_at")),
        }
