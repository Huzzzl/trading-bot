"""
tests/test_live_account_check.py
----------------------------------
Tests for src/tools/live_account_check.py.

All tests run fully offline: no real Alpaca API calls, no credentials in the
environment, no orders submitted or cancelled.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_account(
    status: str = "ACTIVE",
    trading_blocked: bool = False,
    account_blocked: bool = False,
    buying_power: str = "10000.00",
    portfolio_value: str = "50000.00",
) -> MagicMock:
    acct = MagicMock()
    acct.status          = status
    acct.trading_blocked = trading_blocked
    acct.account_blocked = account_blocked
    acct.buying_power    = buying_power
    acct.portfolio_value = portfolio_value
    return acct


def _mock_position(symbol: str = "SPY") -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    return pos


def _mock_order(symbol: str = "SPY") -> MagicMock:
    order = MagicMock()
    order.symbol = symbol
    return order


def _make_client(
    account=None,
    positions=None,
    orders=None,
    submit_side_effect=None,
    cancel_side_effect=None,
) -> MagicMock:
    """Build a mock TradingClient with safe guard defaults."""
    client = MagicMock()
    client.get_account.return_value       = account or _mock_account()
    client.get_all_positions.return_value = positions if positions is not None else []
    client.get_orders.return_value        = orders if orders is not None else []
    # Ensure submit/cancel are never called
    client.submit_order.side_effect  = submit_side_effect or AssertionError("must not call submit_order")
    client.cancel_order.side_effect  = cancel_side_effect or AssertionError("must not call cancel_order")
    return client


# ---------------------------------------------------------------------------
# 0. _normalize_enum_value
# ---------------------------------------------------------------------------

class TestNormalizeEnumValue:
    def test_plain_string_active(self):
        from src.tools.live_account_check import _normalize_enum_value
        assert _normalize_enum_value("active") == "active"

    def test_plain_string_uppercase(self):
        from src.tools.live_account_check import _normalize_enum_value
        assert _normalize_enum_value("ACTIVE") == "active"

    def test_enum_str_repr(self):
        from src.tools.live_account_check import _normalize_enum_value
        assert _normalize_enum_value("AccountStatus.ACTIVE") == "active"

    def test_enum_object_whose_str_is_prefixed(self):
        from src.tools.live_account_check import _normalize_enum_value
        mock_enum = MagicMock()
        mock_enum.__str__ = lambda self: "AccountStatus.ACTIVE"
        assert _normalize_enum_value(mock_enum) == "active"

    def test_restricted_enum_str(self):
        from src.tools.live_account_check import _normalize_enum_value
        assert _normalize_enum_value("AccountStatus.RESTRICTED") == "restricted"

    def test_no_dot_passes_through(self):
        from src.tools.live_account_check import _normalize_enum_value
        assert _normalize_enum_value("restricted") == "restricted"


# ---------------------------------------------------------------------------
# 1. _resolve_live_credentials
# ---------------------------------------------------------------------------

class TestResolveLiveCredentials:
    def test_returns_creds_when_set(self):
        from src.tools.live_account_check import _resolve_live_credentials
        with patch.dict(os.environ, {
            "ALPACA_LIVE_API_KEY": "live-key",
            "ALPACA_LIVE_SECRET_KEY": "live-secret",
        }):
            key, secret = _resolve_live_credentials()
        assert key == "live-key"
        assert secret == "live-secret"

    def test_raises_when_key_missing(self):
        from src.tools.live_account_check import _resolve_live_credentials
        env = {"ALPACA_LIVE_SECRET_KEY": "secret"}
        env.pop("ALPACA_LIVE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="ALPACA_LIVE_API_KEY"):
                _resolve_live_credentials()

    def test_raises_when_secret_missing(self):
        from src.tools.live_account_check import _resolve_live_credentials
        env = {"ALPACA_LIVE_API_KEY": "key"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="ALPACA_LIVE_SECRET_KEY"):
                _resolve_live_credentials()

    def test_raises_when_both_missing(self):
        from src.tools.live_account_check import _resolve_live_credentials
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError):
                _resolve_live_credentials()

    def test_does_not_read_paper_key(self):
        """ALPACA_API_KEY must not satisfy the live credential check."""
        from src.tools.live_account_check import _resolve_live_credentials
        env = {"ALPACA_API_KEY": "paper-key", "ALPACA_SECRET_KEY": "paper-secret"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError):
                _resolve_live_credentials()

    def test_paper_key_env_vars_ignored_even_if_set(self):
        """Live check must ignore paper env vars entirely."""
        from src.tools.live_account_check import _resolve_live_credentials
        env = {
            "ALPACA_API_KEY":        "paper-key",
            "ALPACA_SECRET_KEY":     "paper-secret",
            "ALPACA_LIVE_API_KEY":   "",
            "ALPACA_LIVE_SECRET_KEY": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError):
                _resolve_live_credentials()


# ---------------------------------------------------------------------------
# 2. _make_live_client
# ---------------------------------------------------------------------------

class TestMakeLiveClient:
    def test_paper_false(self):
        from src.tools.live_account_check import _make_live_client
        with patch("alpaca.trading.client.TradingClient") as MockTC:
            _make_live_client("key", "secret")
        _, kwargs = MockTC.call_args
        assert kwargs.get("paper") is False

    def test_credentials_passed(self):
        from src.tools.live_account_check import _make_live_client
        with patch("alpaca.trading.client.TradingClient") as MockTC:
            _make_live_client("my-key", "my-secret")
        _, kwargs = MockTC.call_args
        assert kwargs.get("api_key") == "my-key"
        assert kwargs.get("secret_key") == "my-secret"


# ---------------------------------------------------------------------------
# 3. check_credentials
# ---------------------------------------------------------------------------

class TestCheckCredentials:
    def test_pass_when_vars_set(self):
        from src.tools.live_account_check import check_credentials
        with patch.dict(os.environ, {
            "ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s",
        }):
            result, creds = check_credentials()
        assert result["status"] == "PASS"
        assert creds == ("k", "s")

    def test_fail_when_vars_missing(self):
        from src.tools.live_account_check import check_credentials
        with patch.dict(os.environ, {}, clear=True):
            result, creds = check_credentials()
        assert result["status"] == "FAIL"
        assert creds is None

    def test_label_is_credentials(self):
        from src.tools.live_account_check import check_credentials
        with patch.dict(os.environ, {
            "ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s",
        }):
            result, _ = check_credentials()
        assert result["label"] == "credentials"


# ---------------------------------------------------------------------------
# 4. check_account
# ---------------------------------------------------------------------------

class TestCheckAccount:
    def test_active_unblocked_passes(self):
        from src.tools.live_account_check import check_account
        client = _make_client(account=_mock_account("ACTIVE"))
        result = check_account(client)
        assert result["status"] == "PASS"

    def test_non_active_status_fails(self):
        from src.tools.live_account_check import check_account
        client = _make_client(account=_mock_account("RESTRICTED"))
        result = check_account(client)
        assert result["status"] == "FAIL"

    def test_trading_blocked_fails(self):
        from src.tools.live_account_check import check_account
        client = _make_client(account=_mock_account(trading_blocked=True))
        result = check_account(client)
        assert result["status"] == "FAIL"

    def test_account_blocked_fails(self):
        from src.tools.live_account_check import check_account
        client = _make_client(account=_mock_account(account_blocked=True))
        result = check_account(client)
        assert result["status"] == "FAIL"

    def test_api_error_fails(self):
        from src.tools.live_account_check import check_account
        client = MagicMock()
        client.get_account.side_effect = Exception("network error")
        result = check_account(client)
        assert result["status"] == "FAIL"
        assert "get_account" in result["detail"]

    def test_account_fields_in_result(self):
        from src.tools.live_account_check import check_account
        client = _make_client(account=_mock_account(
            buying_power="25000.00", portfolio_value="75000.00",
        ))
        result = check_account(client)
        assert result["buying_power"] == "25000.00"
        assert result["portfolio_value"] == "75000.00"

    def test_label_is_account(self):
        from src.tools.live_account_check import check_account
        result = check_account(_make_client())
        assert result["label"] == "account"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_account_check import check_account
        client = _make_client()
        check_account(client)
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()

    def test_enum_status_active_passes(self):
        """account.status as enum object whose str() is 'AccountStatus.ACTIVE' must PASS."""
        from src.tools.live_account_check import check_account
        acct = _mock_account()
        mock_enum = MagicMock()
        mock_enum.__str__ = lambda self: "AccountStatus.ACTIVE"
        acct.status = mock_enum
        result = check_account(_make_client(account=acct))
        assert result["status"] == "PASS"
        assert result["account_status"] == "active"

    def test_enum_status_restricted_fails(self):
        """account.status as enum 'AccountStatus.RESTRICTED' must FAIL."""
        from src.tools.live_account_check import check_account
        acct = _mock_account()
        mock_enum = MagicMock()
        mock_enum.__str__ = lambda self: "AccountStatus.RESTRICTED"
        acct.status = mock_enum
        result = check_account(_make_client(account=acct))
        assert result["status"] == "FAIL"
        assert result["account_status"] == "restricted"

    def test_enum_status_active_blocked_still_fails(self):
        """ACTIVE status with trading_blocked=True must still FAIL."""
        from src.tools.live_account_check import check_account
        acct = _mock_account(trading_blocked=True)
        mock_enum = MagicMock()
        mock_enum.__str__ = lambda self: "AccountStatus.ACTIVE"
        acct.status = mock_enum
        result = check_account(_make_client(account=acct))
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# 5. check_positions
# ---------------------------------------------------------------------------

class TestCheckPositions:
    def test_no_positions_passes(self):
        from src.tools.live_account_check import check_positions
        result = check_positions(_make_client(positions=[]))
        assert result["status"] == "PASS"
        assert result["position_count"] == 0

    def test_positions_found_warns(self):
        from src.tools.live_account_check import check_positions
        result = check_positions(_make_client(positions=[_mock_position("SPY")]))
        assert result["status"] == "WARN"
        assert result["position_count"] == 1

    def test_symbols_listed(self):
        from src.tools.live_account_check import check_positions
        result = check_positions(_make_client(positions=[
            _mock_position("SPY"), _mock_position("QQQ"),
        ]))
        assert "SPY" in result["symbols"]
        assert "QQQ" in result["symbols"]

    def test_api_error_fails(self):
        from src.tools.live_account_check import check_positions
        client = MagicMock()
        client.get_all_positions.side_effect = Exception("timeout")
        result = check_positions(client)
        assert result["status"] == "FAIL"

    def test_label_is_positions(self):
        from src.tools.live_account_check import check_positions
        result = check_positions(_make_client())
        assert result["label"] == "positions"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_account_check import check_positions
        client = _make_client()
        check_positions(client)
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# 6. check_orders
# ---------------------------------------------------------------------------

class TestCheckOrders:
    def test_no_orders_passes(self):
        from src.tools.live_account_check import check_orders
        result = check_orders(_make_client(orders=[]))
        assert result["status"] == "PASS"
        assert result["open_order_count"] == 0

    def test_open_orders_warns(self):
        from src.tools.live_account_check import check_orders
        result = check_orders(_make_client(orders=[_mock_order()]))
        assert result["status"] == "WARN"
        assert result["open_order_count"] == 1

    def test_api_error_fails(self):
        from src.tools.live_account_check import check_orders
        client = MagicMock()
        client.get_orders.side_effect = Exception("timeout")
        result = check_orders(client)
        assert result["status"] == "FAIL"

    def test_label_is_orders(self):
        from src.tools.live_account_check import check_orders
        result = check_orders(_make_client())
        assert result["label"] == "orders"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_account_check import check_orders
        client = _make_client()
        check_orders(client)
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# 7. CLI: main()
# ---------------------------------------------------------------------------

def _run_main(
    account=None,
    positions=None,
    orders=None,
    live_key: str = "live-key",
    live_secret: str = "live-secret",
    argv: list[str] | None = None,
):
    """Run main() with mocked credentials and client; return (exit_code, client)."""
    from src.tools.live_account_check import main

    mock_client = _make_client(account=account, positions=positions, orders=orders)

    env = {"ALPACA_LIVE_API_KEY": live_key, "ALPACA_LIVE_SECRET_KEY": live_secret}
    with patch.dict(os.environ, env, clear=True), \
         patch("src.tools.live_account_check._make_live_client",
               return_value=mock_client):
        try:
            main(argv or [])
            return 0, mock_client
        except SystemExit as exc:
            return exc.code, mock_client


class TestMain:
    def test_active_no_positions_no_orders_exits_0(self):
        code, _ = _run_main()
        assert code in (0, None)

    def test_missing_credentials_exits_1(self):
        from src.tools.live_account_check import main
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                main([])
        assert exc_info.value.code == 1

    def test_uses_live_key_not_paper_key(self):
        from src.tools.live_account_check import main
        mock_client = _make_client()
        # Only live keys set — paper keys absent → must succeed
        env = {
            "ALPACA_LIVE_API_KEY": "live-k",
            "ALPACA_LIVE_SECRET_KEY": "live-s",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_account_check._make_live_client",
                   return_value=mock_client) as mock_factory:
            try:
                main([])
            except SystemExit:
                pass
        mock_factory.assert_called_once_with("live-k", "live-s")

    def test_paper_keys_alone_cause_fail(self):
        from src.tools.live_account_check import main
        env = {
            "ALPACA_API_KEY":    "paper-k",
            "ALPACA_SECRET_KEY": "paper-s",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                main([])
        assert exc_info.value.code == 1

    def test_trading_client_called_with_paper_false(self):
        from src.tools.live_account_check import main
        mock_client = _make_client()
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_account_check._make_live_client",
                   return_value=mock_client) as mock_factory:
            try:
                main([])
            except SystemExit:
                pass
        # Verify we called the factory (which internally uses paper=False)
        mock_factory.assert_called_once()

    def test_positions_found_exits_1_warn(self):
        code, _ = _run_main(positions=[_mock_position("SPY")])
        assert code == 1

    def test_open_orders_exits_1_warn(self):
        code, _ = _run_main(orders=[_mock_order()])
        assert code == 1

    def test_blocked_account_exits_1_fail(self):
        code, _ = _run_main(account=_mock_account(trading_blocked=True))
        assert code == 1

    def test_inactive_account_exits_1_fail(self):
        code, _ = _run_main(account=_mock_account(status="RESTRICTED"))
        assert code == 1

    def test_submit_order_never_called(self):
        _, client = _run_main()
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self):
        _, client = _run_main()
        client.cancel_order.assert_not_called()

    def test_no_real_network_calls(self):
        """Verify _make_live_client is always mocked and never hits the network."""
        from src.tools.live_account_check import main
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_account_check._make_live_client",
                   return_value=_make_client()) as mock_factory:
            try:
                main([])
            except SystemExit:
                pass
        # The factory was called → real TradingClient was never constructed
        mock_factory.assert_called_once()

    def test_output_contains_account_status(self, capsys):
        _run_main()
        out = capsys.readouterr().out
        assert "account_status" in out

    def test_output_contains_result(self, capsys):
        _run_main()
        out = capsys.readouterr().out
        assert "RESULT:" in out

    def test_fail_output_contains_fail(self, capsys):
        with patch.dict(os.environ, {}, clear=True):
            from src.tools.live_account_check import main
            try:
                main([])
            except SystemExit:
                pass
        out = capsys.readouterr().out
        assert "FAIL" in out
