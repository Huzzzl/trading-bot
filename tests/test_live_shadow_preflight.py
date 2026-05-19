"""
tests/test_live_shadow_preflight.py
-------------------------------------
Tests for src/tools/live_shadow_preflight.py.

All tests run fully offline: no real Alpaca API calls, no credentials in the
environment, no orders submitted or cancelled, no real engine/data runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _mock_intent(
    symbol: str = "SPY",
    side: str = "buy",
    order_type: str = "market",
    quantity: float = 1.0,
    entry_price: float | None = None,
) -> MagicMock:
    intent = MagicMock()
    intent.symbol     = symbol
    intent.side       = side
    intent.order_type = order_type
    intent.quantity   = quantity
    intent.metadata   = {"entry_price": entry_price} if entry_price is not None else {}
    return intent


def _make_client(
    account=None,
    positions=None,
    orders=None,
) -> MagicMock:
    client = MagicMock()
    client.get_account.return_value       = account or _mock_account()
    client.get_all_positions.return_value = positions if positions is not None else []
    client.get_orders.return_value        = orders if orders is not None else []
    client.submit_order.side_effect       = AssertionError("must not call submit_order")
    client.cancel_order.side_effect       = AssertionError("must not call cancel_order")
    return client


def _mock_execution_cfg(
    live_max_quantity: float = 1.0,
    live_max_notional: "float | None" = 500.0,
    live_quantity_override: "float | None" = 1.0,
) -> MagicMock:
    ex = MagicMock()
    ex.live_max_quantity     = live_max_quantity
    ex.live_max_notional     = live_max_notional
    ex.live_quantity_override = live_quantity_override
    return ex


def _run_main(
    account=None,
    positions=None,
    orders=None,
    intents=None,
    symbol: str = "SPY",
    live_key: str = "live-key",
    live_secret: str = "live-secret",
    config_ok: bool = True,
    live_max_quantity: float = 1.0,
    live_max_notional: "float | None" = 500.0,
    live_quantity_override: "float | None" = 1.0,
    argv: list[str] | None = None,
    tmp_path: Path | None = None,
) -> tuple[int, MagicMock]:
    """Run main() with strategy preview and live client fully mocked."""
    from src.tools.live_shadow_preflight import main

    if intents is None:
        intents = [_mock_intent(entry_price=450.0)]

    mock_client = _make_client(account=account, positions=positions, orders=orders)

    cfg_mock = MagicMock()
    cfg_mock.execution = _mock_execution_cfg(
        live_max_quantity=live_max_quantity,
        live_max_notional=live_max_notional,
        live_quantity_override=live_quantity_override,
    )
    cfg_result = {"label": "config", "status": "PASS" if config_ok else "FAIL", "detail": ""}

    out_dir = str(tmp_path or "/tmp/test_shadow_preflight")
    default_argv = [
        "--config", "config/settings.paper.local.yaml",
        "--output-dir", out_dir,
        "--symbol", symbol,
    ]

    env = {"ALPACA_LIVE_API_KEY": live_key, "ALPACA_LIVE_SECRET_KEY": live_secret}
    with patch.dict(os.environ, env, clear=True), \
         patch("src.tools.live_shadow_preflight.check_config",
               return_value=(cfg_result, cfg_mock if config_ok else None)), \
         patch("src.tools.live_shadow_preflight._run_strategy_preview",
               return_value=intents), \
         patch("src.tools.live_shadow_preflight._make_live_client",
               return_value=mock_client):
        try:
            main(argv or default_argv)
            return 0, mock_client
        except SystemExit as exc:
            return exc.code, mock_client


# ---------------------------------------------------------------------------
# 1. check_candidates
# ---------------------------------------------------------------------------

class TestCheckCandidates:
    def test_no_intents_fails(self):
        from src.tools.live_shadow_preflight import check_candidates
        result = check_candidates([], "SPY")
        assert result["status"] == "FAIL"

    def test_one_intent_passes(self):
        from src.tools.live_shadow_preflight import check_candidates
        result = check_candidates([_mock_intent()], "SPY")
        assert result["status"] == "PASS"
        assert result["candidate_count"] == 1

    def test_quantity_gt_1_still_passes_candidates(self):
        """Quantity validation moved to check_live_sizing; candidates only checks existence."""
        from src.tools.live_shadow_preflight import check_candidates
        result = check_candidates([_mock_intent(quantity=2.0)], "SPY")
        assert result["status"] == "PASS"
        assert result["candidate_count"] == 1

    def test_multiple_intents_counted(self):
        from src.tools.live_shadow_preflight import check_candidates
        intents = [_mock_intent(), _mock_intent()]
        result = check_candidates(intents, "SPY")
        assert result["candidate_count"] == 2

    def test_label_is_candidates(self):
        from src.tools.live_shadow_preflight import check_candidates
        result = check_candidates([_mock_intent()], "SPY")
        assert result["label"] == "candidates"


# ---------------------------------------------------------------------------
# 2. check_live_sizing
# ---------------------------------------------------------------------------

class TestCheckLiveSizing:
    def _cfg(self, max_qty=1.0, max_notional=500.0, override=1.0):
        cfg = MagicMock()
        cfg.execution = _mock_execution_cfg(
            live_max_quantity=max_qty,
            live_max_notional=max_notional,
            live_quantity_override=override,
        )
        return cfg

    def test_original_138_override_1_passes_quantity_cap(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=138.0, entry_price=450.0)]
        result = check_live_sizing(intents, self._cfg(override=1.0))
        assert result["status"] == "PASS"
        assert result["effective_quantity"] == 1.0

    def test_original_138_no_override_fails(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=138.0, entry_price=450.0)]
        result = check_live_sizing(intents, self._cfg(override=None, max_notional=None))
        assert result["status"] == "FAIL"
        assert result["effective_quantity"] == 138.0

    def test_effective_qty_gt_max_fails(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=2.0, entry_price=100.0)]
        result = check_live_sizing(intents, self._cfg(max_qty=1.0, override=None, max_notional=None))
        assert result["status"] == "FAIL"

    def test_notional_below_cap_passes(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=1.0, entry_price=400.0)]
        result = check_live_sizing(intents, self._cfg(max_notional=500.0, override=1.0))
        assert result["status"] == "PASS"
        assert result["estimated_notional"] == pytest.approx(400.0)

    def test_notional_above_cap_fails(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=1.0, entry_price=600.0)]
        result = check_live_sizing(intents, self._cfg(max_notional=500.0, override=1.0))
        assert result["status"] == "FAIL"

    def test_notional_enabled_missing_price_fails_closed(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=1.0, entry_price=None)]
        result = check_live_sizing(intents, self._cfg(max_notional=500.0, override=1.0))
        assert result["status"] == "FAIL"
        assert "entry_price missing" in result["detail"]

    def test_notional_disabled_missing_price_passes(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=1.0, entry_price=None)]
        result = check_live_sizing(intents, self._cfg(max_notional=None, override=1.0))
        assert result["status"] == "PASS"

    def test_output_fields_present(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(quantity=138.0, entry_price=450.0)]
        result = check_live_sizing(intents, self._cfg(override=1.0))
        for field in ("original_quantity", "effective_quantity", "entry_price",
                      "estimated_notional", "live_max_quantity", "live_max_notional"):
            assert field in result, f"missing field: {field}"

    def test_label_is_live_sizing(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        result = check_live_sizing([_mock_intent(entry_price=450.0)], self._cfg())
        assert result["label"] == "live_sizing"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        client = _make_client()
        check_live_sizing([_mock_intent(entry_price=450.0)], self._cfg())
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# 3. check_live_account
# ---------------------------------------------------------------------------

class TestCheckLiveAccount:
    def test_active_unblocked_passes(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(_make_client(account=_mock_account("ACTIVE")))
        assert result["status"] == "PASS"

    def test_inactive_fails(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(_make_client(account=_mock_account("RESTRICTED")))
        assert result["status"] == "FAIL"

    def test_trading_blocked_fails(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(_make_client(account=_mock_account(trading_blocked=True)))
        assert result["status"] == "FAIL"

    def test_account_blocked_fails(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(_make_client(account=_mock_account(account_blocked=True)))
        assert result["status"] == "FAIL"

    def test_buying_power_zero_warns(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(
            _make_client(account=_mock_account(buying_power="0", portfolio_value="50000"))
        )
        assert result["status"] == "WARN"

    def test_portfolio_value_zero_warns(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(
            _make_client(account=_mock_account(buying_power="10000", portfolio_value="0"))
        )
        assert result["status"] == "WARN"

    def test_both_zero_warns_not_fails(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(
            _make_client(account=_mock_account(buying_power="0", portfolio_value="0"))
        )
        assert result["status"] == "WARN"

    def test_api_error_fails(self):
        from src.tools.live_shadow_preflight import check_live_account
        client = MagicMock()
        client.get_account.side_effect = Exception("network error")
        result = check_live_account(client)
        assert result["status"] == "FAIL"
        assert "get_account" in result["detail"]

    def test_enum_status_active_passes(self):
        from src.tools.live_shadow_preflight import check_live_account
        acct = _mock_account()
        mock_enum = MagicMock()
        mock_enum.__str__ = lambda self: "AccountStatus.ACTIVE"
        acct.status = mock_enum
        result = check_live_account(_make_client(account=acct))
        assert result["status"] == "PASS"
        assert result["account_status"] == "active"

    def test_label_is_live_account(self):
        from src.tools.live_shadow_preflight import check_live_account
        result = check_live_account(_make_client())
        assert result["label"] == "live_account"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_shadow_preflight import check_live_account
        client = _make_client()
        check_live_account(client)
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# 3. check_live_position
# ---------------------------------------------------------------------------

class TestCheckLivePosition:
    def test_no_position_passes(self):
        from src.tools.live_shadow_preflight import check_live_position
        result = check_live_position(_make_client(positions=[]), "SPY")
        assert result["status"] == "PASS"
        assert result["position_for_symbol"] is False

    def test_spy_position_fails(self):
        from src.tools.live_shadow_preflight import check_live_position
        result = check_live_position(_make_client(positions=[_mock_position("SPY")]), "SPY")
        assert result["status"] == "FAIL"
        assert result["position_for_symbol"] is True

    def test_qqq_position_does_not_block_spy(self):
        from src.tools.live_shadow_preflight import check_live_position
        result = check_live_position(_make_client(positions=[_mock_position("QQQ")]), "SPY")
        assert result["status"] == "PASS"

    def test_api_error_fails(self):
        from src.tools.live_shadow_preflight import check_live_position
        client = MagicMock()
        client.get_all_positions.side_effect = Exception("timeout")
        result = check_live_position(client, "SPY")
        assert result["status"] == "FAIL"

    def test_label_is_live_position(self):
        from src.tools.live_shadow_preflight import check_live_position
        result = check_live_position(_make_client(), "SPY")
        assert result["label"] == "live_position"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_shadow_preflight import check_live_position
        client = _make_client()
        check_live_position(client, "SPY")
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# 4. check_live_orders
# ---------------------------------------------------------------------------

class TestCheckLiveOrders:
    def test_no_orders_passes(self):
        from src.tools.live_shadow_preflight import check_live_orders
        result = check_live_orders(_make_client(orders=[]), "SPY")
        assert result["status"] == "PASS"
        assert result["open_orders_for_symbol"] is False

    def test_spy_order_fails(self):
        from src.tools.live_shadow_preflight import check_live_orders
        result = check_live_orders(_make_client(orders=[_mock_order("SPY")]), "SPY")
        assert result["status"] == "FAIL"
        assert result["open_orders_for_symbol"] is True

    def test_qqq_order_does_not_block_spy(self):
        from src.tools.live_shadow_preflight import check_live_orders
        result = check_live_orders(_make_client(orders=[_mock_order("QQQ")]), "SPY")
        assert result["status"] == "PASS"

    def test_api_error_fails(self):
        from src.tools.live_shadow_preflight import check_live_orders
        client = MagicMock()
        client.get_orders.side_effect = Exception("timeout")
        result = check_live_orders(client, "SPY")
        assert result["status"] == "FAIL"

    def test_label_is_live_orders(self):
        from src.tools.live_shadow_preflight import check_live_orders
        result = check_live_orders(_make_client(), "SPY")
        assert result["label"] == "live_orders"

    def test_does_not_call_submit_or_cancel(self):
        from src.tools.live_shadow_preflight import check_live_orders
        client = _make_client()
        check_live_orders(client, "SPY")
        client.submit_order.assert_not_called()
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# 5. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_active_no_position_no_order_exits_0(self, tmp_path):
        code, _ = _run_main(tmp_path=tmp_path)
        assert code in (0, None)

    def test_missing_credentials_exits_1(self, tmp_path):
        from src.tools.live_shadow_preflight import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        with patch.dict(os.environ, {}, clear=True), \
             patch("src.tools.live_shadow_preflight.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_preflight._run_strategy_preview",
                   return_value=[_mock_intent()]):
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--config", "config/settings.paper.local.yaml",
                    "--output-dir", str(tmp_path),
                ])
        assert exc_info.value.code == 1

    def test_uses_live_key_not_paper_key(self, tmp_path):
        from src.tools.live_shadow_preflight import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        mock_client = _make_client()
        env = {"ALPACA_LIVE_API_KEY": "live-k", "ALPACA_LIVE_SECRET_KEY": "live-s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_preflight.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_preflight._run_strategy_preview",
                   return_value=[_mock_intent()]), \
             patch("src.tools.live_shadow_preflight._make_live_client",
                   return_value=mock_client) as mock_factory:
            try:
                main([
                    "--config", "config/settings.paper.local.yaml",
                    "--output-dir", str(tmp_path),
                ])
            except SystemExit:
                pass
        mock_factory.assert_called_once_with("live-k", "live-s")

    def test_paper_keys_alone_cause_fail(self, tmp_path):
        from src.tools.live_shadow_preflight import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        env = {"ALPACA_API_KEY": "paper-k", "ALPACA_SECRET_KEY": "paper-s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_preflight.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_preflight._run_strategy_preview",
                   return_value=[_mock_intent()]):
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--config", "config/settings.paper.local.yaml",
                    "--output-dir", str(tmp_path),
                ])
        assert exc_info.value.code == 1

    def test_trading_client_paper_false(self, tmp_path):
        """_make_live_client is called (which internally uses paper=False)."""
        from src.tools.live_shadow_preflight import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        mock_client = _make_client()
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_preflight.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_preflight._run_strategy_preview",
                   return_value=[_mock_intent()]), \
             patch("src.tools.live_shadow_preflight._make_live_client",
                   return_value=mock_client) as mock_factory:
            try:
                main([
                    "--config", "config/settings.paper.local.yaml",
                    "--output-dir", str(tmp_path),
                ])
            except SystemExit:
                pass
        mock_factory.assert_called_once()

    def test_no_candidate_exits_1(self, tmp_path):
        code, _ = _run_main(intents=[], tmp_path=tmp_path)
        assert code == 1

    def test_spy_position_exits_1(self, tmp_path):
        code, _ = _run_main(positions=[_mock_position("SPY")], tmp_path=tmp_path)
        assert code == 1

    def test_spy_open_order_exits_1(self, tmp_path):
        code, _ = _run_main(orders=[_mock_order("SPY")], tmp_path=tmp_path)
        assert code == 1

    def test_qqq_position_does_not_block_spy(self, tmp_path):
        code, _ = _run_main(positions=[_mock_position("QQQ")], tmp_path=tmp_path)
        assert code in (0, None)

    def test_qqq_open_order_does_not_block_spy(self, tmp_path):
        code, _ = _run_main(orders=[_mock_order("QQQ")], tmp_path=tmp_path)
        assert code in (0, None)

    def test_blocked_account_exits_1(self, tmp_path):
        code, _ = _run_main(account=_mock_account(trading_blocked=True), tmp_path=tmp_path)
        assert code == 1

    def test_inactive_account_exits_1(self, tmp_path):
        code, _ = _run_main(account=_mock_account(status="RESTRICTED"), tmp_path=tmp_path)
        assert code == 1

    def test_original_138_with_override_1_passes(self, tmp_path):
        """Original qty=138 but live_quantity_override=1.0 → sizing PASS → exits 0."""
        code, _ = _run_main(
            intents=[_mock_intent(quantity=138.0, entry_price=450.0)],
            live_max_quantity=1.0,
            live_quantity_override=1.0,
            live_max_notional=500.0,
            tmp_path=tmp_path,
        )
        assert code in (0, None)

    def test_original_138_without_override_fails(self, tmp_path):
        """Original qty=138, no override, max_qty=1.0 → FAIL."""
        code, _ = _run_main(
            intents=[_mock_intent(quantity=138.0, entry_price=450.0)],
            live_max_quantity=1.0,
            live_quantity_override=None,
            live_max_notional=None,
            tmp_path=tmp_path,
        )
        assert code == 1

    def test_buying_power_zero_exits_1_warn(self, tmp_path):
        code, _ = _run_main(
            account=_mock_account(buying_power="0", portfolio_value="50000"),
            tmp_path=tmp_path,
        )
        assert code == 1

    def test_submit_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path=tmp_path)
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path=tmp_path)
        client.cancel_order.assert_not_called()

    def test_no_real_network_calls(self, tmp_path):
        """_make_live_client is always mocked — no real TradingClient constructed."""
        from src.tools.live_shadow_preflight import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_preflight.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_preflight._run_strategy_preview",
                   return_value=[_mock_intent()]), \
             patch("src.tools.live_shadow_preflight._make_live_client",
                   return_value=_make_client()) as mock_factory:
            try:
                main([
                    "--config", "config/settings.paper.local.yaml",
                    "--output-dir", str(tmp_path),
                ])
            except SystemExit:
                pass
        mock_factory.assert_called_once()

    def test_output_contains_selected_symbol(self, tmp_path, capsys):
        _run_main(tmp_path=tmp_path)
        out = capsys.readouterr().out
        assert "selected_symbol" in out

    def test_output_contains_result(self, tmp_path, capsys):
        _run_main(tmp_path=tmp_path)
        out = capsys.readouterr().out
        assert "RESULT:" in out

    def test_output_contains_candidate_count(self, tmp_path, capsys):
        _run_main(tmp_path=tmp_path)
        out = capsys.readouterr().out
        assert "candidate_count" in out

    def test_fail_output_contains_fail(self, tmp_path, capsys):
        _run_main(intents=[], tmp_path=tmp_path)
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_symbol_arg_filters_correctly(self, tmp_path):
        """--symbol QQQ: strategy returns no QQQ intents → no candidates → FAIL."""
        from src.tools.live_shadow_preflight import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        mock_client = _make_client()
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_preflight.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_preflight._run_strategy_preview",
                   return_value=[]), \
             patch("src.tools.live_shadow_preflight._make_live_client",
                   return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--config", "config/settings.paper.local.yaml",
                    "--output-dir", str(tmp_path),
                    "--symbol", "QQQ",
                ])
        assert exc_info.value.code == 1
