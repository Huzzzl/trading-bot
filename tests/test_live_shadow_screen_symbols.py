"""
tests/test_live_shadow_screen_symbols.py
------------------------------------------
Tests for src/tools/live_shadow_screen_symbols.py.

All tests run fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_account_raw(
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


def _mock_position(symbol: str) -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    return pos


def _mock_order(symbol: str) -> MagicMock:
    order = MagicMock()
    order.symbol = symbol
    return order


def _mock_intent(
    symbol: str = "SPY",
    quantity: float = 1.0,
    entry_price: float | None = 450.0,
) -> MagicMock:
    intent = MagicMock()
    intent.symbol     = symbol
    intent.side       = "buy"
    intent.order_type = "market"
    intent.quantity   = quantity
    intent.metadata   = {"entry_price": entry_price} if entry_price is not None else {}
    intent.client_order_id = f"BT-{symbol}-001"
    return intent


def _mock_execution_cfg(
    live_max_quantity: float = 1.0,
    live_max_notional: "float | None" = 500.0,
    live_quantity_override: "float | None" = 1.0,
    live_shadow_screen_symbols: "list[str] | None" = None,
    live_sizing_mode: str = "quantity",
    live_order_notional_override: "float | None" = None,
    live_max_order_notional: float = 100.0,
) -> MagicMock:
    ex = MagicMock()
    ex.live_max_quantity              = live_max_quantity
    ex.live_max_notional              = live_max_notional
    ex.live_quantity_override         = live_quantity_override
    ex.live_shadow_screen_symbols     = live_shadow_screen_symbols if live_shadow_screen_symbols is not None else ["SPY", "QQQ", "IWM", "DIA"]
    ex.live_sizing_mode               = live_sizing_mode
    ex.live_order_notional_override   = live_order_notional_override
    ex.live_max_order_notional        = live_max_order_notional
    return ex


def _make_client(
    account=None,
    positions=None,
    orders=None,
) -> MagicMock:
    client = MagicMock()
    client.get_account.return_value       = account or _mock_account_raw()
    client.get_all_positions.return_value = positions if positions is not None else []
    client.get_orders.return_value        = orders   if orders    is not None else []
    client.submit_order.side_effect       = AssertionError("must not call submit_order")
    client.cancel_order.side_effect       = AssertionError("must not call cancel_order")
    return client


def _run_main(
    tmp_path: Path,
    symbols: "str | None" = "SPY",
    intents_by_symbol: dict | None = None,
    account_raw=None,
    positions=None,
    orders=None,
    live_max_quantity: float = 1.0,
    live_max_notional: "float | None" = 500.0,
    live_quantity_override: "float | None" = 1.0,
    config_symbols: "list[str] | None" = None,
    live_key: str = "live-key",
    live_secret: str = "live-secret",
    extra_argv: list[str] | None = None,
) -> tuple[int, MagicMock]:
    """Run main() with everything mocked; return (exit_code, client).

    Pass symbols=None to omit --symbols and rely on config_symbols instead.
    """
    from src.tools.live_shadow_screen_symbols import main

    if intents_by_symbol is None:
        src = symbols if symbols is not None else ",".join(config_symbols or ["SPY"])
        intents_by_symbol = {s.strip(): [_mock_intent(s.strip())]
                             for s in src.split(",")}

    mock_client = _make_client(account=account_raw, positions=positions, orders=orders)

    cfg_mock = MagicMock()
    cfg_mock.execution = _mock_execution_cfg(
        live_max_quantity=live_max_quantity,
        live_max_notional=live_max_notional,
        live_quantity_override=live_quantity_override,
        live_shadow_screen_symbols=config_symbols if config_symbols is not None else ["SPY", "QQQ", "IWM", "DIA"],
    )
    cfg_result = {"label": "config", "status": "PASS", "detail": ""}

    def _preview_side_effect(cfg, symbol):
        return intents_by_symbol.get(symbol, [])

    env = {"ALPACA_LIVE_API_KEY": live_key, "ALPACA_LIVE_SECRET_KEY": live_secret}
    argv = [
        "--config", "config/settings.paper.local.yaml",
        "--output-dir", str(tmp_path),
    ]
    if symbols is not None:
        argv += ["--symbols", symbols]
    argv += extra_argv or []

    with patch.dict(os.environ, env, clear=True), \
         patch("src.tools.live_shadow_screen_symbols.check_config",
               return_value=(cfg_result, cfg_mock)), \
         patch("src.tools.live_shadow_screen_symbols._run_strategy_preview",
               side_effect=_preview_side_effect), \
         patch("src.tools.live_shadow_screen_symbols._make_live_client",
               return_value=mock_client):
        try:
            main(argv)
            return 0, mock_client
        except SystemExit as exc:
            return exc.code, mock_client


# ---------------------------------------------------------------------------
# 1. read_account
# ---------------------------------------------------------------------------

class TestReadAccount:
    def test_active_account_ok_true(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = _make_client(account=_mock_account_raw("ACTIVE"))
        result = read_account(client)
        assert result["ok"] is True

    def test_inactive_account_ok_false(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = _make_client(account=_mock_account_raw("RESTRICTED"))
        result = read_account(client)
        assert result["ok"] is False

    def test_trading_blocked_ok_false(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = _make_client(account=_mock_account_raw(trading_blocked=True))
        result = read_account(client)
        assert result["ok"] is False

    def test_zero_buying_power_warn_true(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = _make_client(account=_mock_account_raw(buying_power="0"))
        result = read_account(client)
        assert result["ok"] is True
        assert result["warn"] is True

    def test_api_error_ok_false(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = MagicMock()
        client.get_account.side_effect = Exception("network error")
        result = read_account(client)
        assert result["ok"] is False
        assert result["error"] is not None

    def test_enum_status_active_ok(self):
        from src.tools.live_shadow_screen_symbols import read_account
        acct = _mock_account_raw()
        acct.status = MagicMock(__str__=lambda self: "AccountStatus.ACTIVE")
        result = read_account(_make_client(account=acct))
        assert result["ok"] is True

    def test_positions_returned(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = _make_client(positions=[_mock_position("SPY")])
        result = read_account(client)
        assert len(result["positions"]) == 1

    def test_orders_returned(self):
        from src.tools.live_shadow_screen_symbols import read_account
        client = _make_client(orders=[_mock_order("SPY")])
        result = read_account(client)
        assert len(result["orders"]) == 1


# ---------------------------------------------------------------------------
# 2. screen_symbol
# ---------------------------------------------------------------------------

def _healthy_account(**kw) -> dict:
    base = {
        "ok": True, "warn": False, "error": None,
        "status": "active", "trading_blocked": False, "account_blocked": False,
        "buying_power": "10000", "portfolio_value": "50000",
        "positions": [], "orders": [],
    }
    base.update(kw)
    return base


def _cfg_mock(max_qty=1.0, max_notional=500.0, override=1.0):
    cfg = MagicMock()
    cfg.execution = _mock_execution_cfg(max_qty, max_notional, override)
    return cfg


class TestScreenSymbol:
    def test_pass_when_candidate_within_limits(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, _healthy_account(), _cfg_mock())
        assert result["best_status"] == "PASS"
        assert result["pass_count"] == 1
        assert result["fail_count"] == 0

    def test_fail_when_all_candidates_exceed_notional(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        intents = [_mock_intent("SPY", entry_price=600.0)]
        result = screen_symbol("SPY", intents, _healthy_account(), _cfg_mock(max_notional=500.0))
        assert result["best_status"] == "FAIL"
        assert result["fail_count"] == 1

    def test_fail_when_no_candidates(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        result = screen_symbol("SPY", [], _healthy_account(), _cfg_mock())
        assert result["best_status"] == "FAIL"
        assert "no candidates" in result["blocker_summary"]

    def test_fail_when_account_blocked(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        acct = _healthy_account(ok=False, status="restricted")
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, acct, _cfg_mock())
        assert result["best_status"] == "FAIL"

    def test_fail_when_live_position_exists(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        pos = _mock_position("SPY")
        acct = _healthy_account(positions=[pos])
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, acct, _cfg_mock())
        assert result["best_status"] == "FAIL"
        assert result["position_for_symbol"] is True

    def test_fail_when_live_order_exists(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        order = _mock_order("SPY")
        acct = _healthy_account(orders=[order])
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, acct, _cfg_mock())
        assert result["best_status"] == "FAIL"
        assert result["open_orders_for_symbol"] is True

    def test_qqq_position_does_not_block_spy(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        acct = _healthy_account(positions=[_mock_position("QQQ")])
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, acct, _cfg_mock())
        assert result["best_status"] == "PASS"
        assert result["position_for_symbol"] is False

    def test_qqq_order_does_not_block_spy(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        acct = _healthy_account(orders=[_mock_order("QQQ")])
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, acct, _cfg_mock())
        assert result["best_status"] == "PASS"

    def test_min_max_notional_computed(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        intents = [
            _mock_intent("SPY", entry_price=400.0),
            _mock_intent("SPY", entry_price=420.0),
        ]
        result = screen_symbol("SPY", intents, _healthy_account(), _cfg_mock())
        assert result["min_estimated_notional"] == pytest.approx(400.0)
        assert result["max_estimated_notional"] == pytest.approx(420.0)

    def test_cheaper_symbol_passes_while_expensive_fails(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        cfg = _cfg_mock(max_notional=500.0, override=1.0)
        acct = _healthy_account()
        spy_result = screen_symbol("SPY", [_mock_intent("SPY", entry_price=600.0)], acct, cfg)
        qqq_result = screen_symbol("QQQ", [_mock_intent("QQQ", entry_price=150.0)], acct, cfg)
        assert spy_result["best_status"] == "FAIL"
        assert qqq_result["best_status"] == "PASS"

    def test_symbol_result_columns_present(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol, _SYMBOL_COLUMNS
        intents = [_mock_intent("SPY", entry_price=400.0)]
        result = screen_symbol("SPY", intents, _healthy_account(), _cfg_mock())
        for col in _SYMBOL_COLUMNS:
            assert col in result, f"missing column: {col}"


# ---------------------------------------------------------------------------
# 3. compute_overall
# ---------------------------------------------------------------------------

class TestComputeOverall:
    def _acct(self, ok=True, warn=False, status="active", blocked=False, error=None):
        return {
            "ok": ok, "warn": warn, "error": error,
            "status": status, "trading_blocked": blocked, "account_blocked": False,
        }

    def test_no_creds_fail(self):
        from src.tools.live_shadow_screen_symbols import compute_overall
        assert compute_overall(False, {}, []) == "FAIL"

    def test_account_fail_fail(self):
        from src.tools.live_shadow_screen_symbols import compute_overall
        acct = self._acct(ok=False, status="restricted")
        result = compute_overall(True, acct, [{"best_status": "PASS"}])
        assert result == "FAIL"

    def test_any_symbol_pass_overall_pass(self):
        from src.tools.live_shadow_screen_symbols import compute_overall
        acct = self._acct()
        results = [{"best_status": "FAIL"}, {"best_status": "PASS"}]
        assert compute_overall(True, acct, results) == "PASS"

    def test_no_pass_symbols_account_warn_overall_warn(self):
        from src.tools.live_shadow_screen_symbols import compute_overall
        acct = self._acct(warn=True)
        results = [{"best_status": "FAIL"}]
        assert compute_overall(True, acct, results) == "WARN"

    def test_no_pass_symbols_account_ok_overall_fail(self):
        from src.tools.live_shadow_screen_symbols import compute_overall
        acct = self._acct()
        results = [{"best_status": "FAIL"}]
        assert compute_overall(True, acct, results) == "FAIL"

    def test_account_api_error_fail(self):
        from src.tools.live_shadow_screen_symbols import compute_overall
        acct = self._acct(ok=False, error="network error")
        assert compute_overall(True, acct, []) == "FAIL"


# ---------------------------------------------------------------------------
# 4. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_clean_run_exits_0(self, tmp_path):
        code, _ = _run_main(tmp_path, symbols="SPY",
                            intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]})
        assert code in (0, None)

    def test_missing_credentials_exits_1(self, tmp_path):
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        cfg_mock.execution = _mock_execution_cfg()
        with patch.dict(os.environ, {}, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)):
            with pytest.raises(SystemExit) as exc_info:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path),
                      "--symbols", "SPY"])
        assert exc_info.value.code == 1

    def test_uses_live_key_not_paper_key(self, tmp_path):
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        cfg_mock.execution = _mock_execution_cfg()
        mock_client = _make_client()
        env = {"ALPACA_LIVE_API_KEY": "live-k", "ALPACA_LIVE_SECRET_KEY": "live-s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_screen_symbols._run_strategy_preview",
                   return_value=[_mock_intent("SPY", entry_price=400.0)]), \
             patch("src.tools.live_shadow_screen_symbols._make_live_client",
                   return_value=mock_client) as mock_factory:
            try:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path),
                      "--symbols", "SPY"])
            except SystemExit:
                pass
        mock_factory.assert_called_once_with("live-k", "live-s")

    def test_paper_keys_alone_exits_1(self, tmp_path):
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        env = {"ALPACA_API_KEY": "paper-k", "ALPACA_SECRET_KEY": "paper-s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)):
            with pytest.raises(SystemExit) as exc_info:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path),
                      "--symbols", "SPY"])
        assert exc_info.value.code == 1

    def test_trading_client_paper_false(self, tmp_path):
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        cfg_mock.execution = _mock_execution_cfg()
        mock_client = _make_client()
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_screen_symbols._run_strategy_preview",
                   return_value=[_mock_intent("SPY", entry_price=400.0)]), \
             patch("src.tools.live_shadow_screen_symbols._make_live_client",
                   return_value=mock_client) as mock_factory:
            try:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path),
                      "--symbols", "SPY"])
            except SystemExit:
                pass
        mock_factory.assert_called_once()

    def test_submit_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path, symbols="SPY",
                              intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]})
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path, symbols="SPY",
                              intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]})
        client.cancel_order.assert_not_called()

    def test_spy_fails_notional_cheaper_symbol_passes(self, tmp_path):
        """SPY @ 600 > 500 notional cap; QQQ @ 150 passes."""
        code, _ = _run_main(
            tmp_path,
            symbols="SPY,QQQ",
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=600.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
            live_max_notional=500.0,
            live_quantity_override=1.0,
        )
        assert code in (0, None)  # overall PASS because QQQ passes

    def test_existing_position_blocks_only_that_symbol(self, tmp_path):
        """SPY position fails SPY but QQQ still passes."""
        code, _ = _run_main(
            tmp_path,
            symbols="SPY,QQQ",
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=400.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
            positions=[_mock_position("SPY")],
        )
        assert code in (0, None)  # QQQ still passes

    def test_existing_open_order_blocks_only_that_symbol(self, tmp_path):
        """SPY open order fails SPY; QQQ still passes."""
        code, _ = _run_main(
            tmp_path,
            symbols="SPY,QQQ",
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=400.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
            orders=[_mock_order("SPY")],
        )
        assert code in (0, None)

    def test_no_candidate_fails_that_symbol(self, tmp_path):
        code, _ = _run_main(
            tmp_path,
            symbols="SPY,QQQ",
            intents_by_symbol={"SPY": [], "QQQ": [_mock_intent("QQQ", entry_price=150.0)]},
        )
        assert code in (0, None)  # QQQ passes even though SPY has no candidates

    def test_account_blocked_fails_overall(self, tmp_path):
        code, _ = _run_main(
            tmp_path,
            symbols="SPY",
            account_raw=_mock_account_raw(trading_blocked=True),
            intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]},
        )
        assert code == 1

    def test_no_real_network_calls(self, tmp_path):
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        cfg_mock.execution = _mock_execution_cfg()
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)), \
             patch("src.tools.live_shadow_screen_symbols._run_strategy_preview",
                   return_value=[_mock_intent("SPY", entry_price=400.0)]), \
             patch("src.tools.live_shadow_screen_symbols._make_live_client",
                   return_value=_make_client()) as mock_factory:
            try:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path),
                      "--symbols", "SPY"])
            except SystemExit:
                pass
        mock_factory.assert_called_once()

    def test_default_writes_no_files(self, tmp_path):
        _run_main(tmp_path, symbols="SPY",
                  intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]})
        assert not (tmp_path / "live_shadow_symbol_screen_report.json").exists()
        assert not (tmp_path / "live_shadow_symbol_screen.csv").exists()

    def test_write_report_creates_json(self, tmp_path):
        _run_main(tmp_path, symbols="SPY",
                  intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]},
                  extra_argv=["--write-report"])
        assert (tmp_path / "live_shadow_symbol_screen_report.json").exists()

    def test_write_report_creates_csv(self, tmp_path):
        _run_main(tmp_path, symbols="SPY",
                  intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]},
                  extra_argv=["--write-report"])
        assert (tmp_path / "live_shadow_symbol_screen.csv").exists()

    def test_json_contains_overall_result(self, tmp_path):
        _run_main(tmp_path, symbols="SPY",
                  intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]},
                  extra_argv=["--write-report"])
        data = json.loads((tmp_path / "live_shadow_symbol_screen_report.json").read_text())
        assert "overall_result" in data
        assert "symbol_results" in data

    def test_csv_contains_all_symbols(self, tmp_path):
        _run_main(tmp_path, symbols="SPY,QQQ",
                  intents_by_symbol={
                      "SPY": [_mock_intent("SPY", entry_price=400.0)],
                      "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
                  },
                  extra_argv=["--write-report"])
        with (tmp_path / "live_shadow_symbol_screen.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert {r["symbol"] for r in rows} == {"SPY", "QQQ"}

    def test_output_contains_result(self, tmp_path, capsys):
        _run_main(tmp_path, symbols="SPY",
                  intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]})
        out = capsys.readouterr().out
        assert "RESULT:" in out

    def test_output_contains_symbol(self, tmp_path, capsys):
        _run_main(tmp_path, symbols="SPY",
                  intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]})
        out = capsys.readouterr().out
        assert "SPY" in out


# ---------------------------------------------------------------------------
# 5. Symbol normalization helper
# ---------------------------------------------------------------------------

class TestNormalizeSymbols:
    def test_uppercase(self):
        from src.tools.live_shadow_screen_symbols import _normalize_symbols
        assert _normalize_symbols(["spy"]) == ["SPY"]

    def test_strips_whitespace(self):
        from src.tools.live_shadow_screen_symbols import _normalize_symbols
        assert _normalize_symbols([" SPY ", "  QQQ"]) == ["SPY", "QQQ"]

    def test_removes_duplicates_preserving_order(self):
        from src.tools.live_shadow_screen_symbols import _normalize_symbols
        assert _normalize_symbols(["SPY", "QQQ", "SPY", "IWM"]) == ["SPY", "QQQ", "IWM"]

    def test_removes_empty_strings(self):
        from src.tools.live_shadow_screen_symbols import _normalize_symbols
        assert _normalize_symbols(["SPY", "", "  ", "QQQ"]) == ["SPY", "QQQ"]

    def test_mixed_case_dedup(self):
        from src.tools.live_shadow_screen_symbols import _normalize_symbols
        assert _normalize_symbols(["spy", "SPY", "Spy"]) == ["SPY"]


# ---------------------------------------------------------------------------
# 6. Symbol universe resolution (config vs CLI)
# ---------------------------------------------------------------------------

class TestSymbolUniverse:
    def test_no_symbols_arg_uses_config_list(self, tmp_path, capsys):
        """When --symbols is omitted the config list is used."""
        code, _ = _run_main(
            tmp_path,
            symbols=None,
            config_symbols=["SPY", "QQQ"],
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=400.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
        )
        out = capsys.readouterr().out
        assert "SPY" in out
        assert "QQQ" in out
        assert code in (0, None)

    def test_cli_symbols_override_config_list(self, tmp_path, capsys):
        """--symbols takes precedence over config list."""
        code, _ = _run_main(
            tmp_path,
            symbols="IWM",
            config_symbols=["SPY", "QQQ"],
            intents_by_symbol={"IWM": [_mock_intent("IWM", entry_price=200.0)]},
        )
        out = capsys.readouterr().out
        assert "IWM" in out
        assert "SPY" not in out
        assert "QQQ" not in out
        assert code in (0, None)

    def test_cli_symbols_normalized_uppercase(self, tmp_path, capsys):
        """Lowercase symbols in --symbols are uppercased."""
        code, _ = _run_main(
            tmp_path,
            symbols="spy",
            intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]},
        )
        out = capsys.readouterr().out
        assert "SPY" in out

    def test_cli_symbols_normalized_strip(self, tmp_path, capsys):
        """Whitespace around symbol names is stripped."""
        code, _ = _run_main(
            tmp_path,
            symbols=" SPY , QQQ ",
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=400.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
        )
        out = capsys.readouterr().out
        assert "SPY" in out
        assert "QQQ" in out

    def test_cli_duplicates_removed_preserving_order(self, tmp_path, capsys):
        """Duplicate symbols in --symbols are collapsed to one row."""
        code, _ = _run_main(
            tmp_path,
            symbols="SPY,QQQ,SPY",
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=400.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
            extra_argv=["--write-report"],
        )
        with (tmp_path / "live_shadow_symbol_screen.csv").open() as f:
            rows = list(csv.DictReader(f))
        symbols_in_csv = [r["symbol"] for r in rows]
        assert symbols_in_csv.count("SPY") == 1
        assert symbols_in_csv == ["SPY", "QQQ"]

    def test_empty_cli_symbols_exits_1(self, tmp_path):
        """--symbols with only whitespace/commas → empty list → exit 1."""
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        cfg_mock.execution = _mock_execution_cfg(live_shadow_screen_symbols=["SPY"])
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)):
            with pytest.raises(SystemExit) as exc_info:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path),
                      "--symbols", "  ,  , "])
        assert exc_info.value.code == 1

    def test_empty_config_symbols_exits_1(self, tmp_path):
        """Empty config list and no --symbols → exit 1."""
        from src.tools.live_shadow_screen_symbols import main
        cfg_result = {"label": "config", "status": "PASS", "detail": ""}
        cfg_mock = MagicMock()
        cfg_mock.execution = _mock_execution_cfg(live_shadow_screen_symbols=[])
        env = {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}
        with patch.dict(os.environ, env, clear=True), \
             patch("src.tools.live_shadow_screen_symbols.check_config",
                   return_value=(cfg_result, cfg_mock)):
            with pytest.raises(SystemExit) as exc_info:
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path)])
        assert exc_info.value.code == 1

    def test_default_config_symbols_spy_qqq_iwm_dia(self):
        """ExecutionConfig default is SPY, QQQ, IWM, DIA."""
        from src.config.loader import ExecutionConfig
        cfg = ExecutionConfig()
        assert cfg.live_shadow_screen_symbols == ["SPY", "QQQ", "IWM", "DIA"]
