"""
tests/test_live_readiness_gate.py
-----------------------------------
Tests for src/tools/live_readiness_gate.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no ledger writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mock_account_raw(
    status: str = "ACTIVE",
    buying_power: str = "10000.00",
    portfolio_value: str = "50000.00",
    trading_blocked: bool = False,
    account_blocked: bool = False,
) -> MagicMock:
    acct = MagicMock()
    acct.status          = status
    acct.buying_power    = buying_power
    acct.portfolio_value = portfolio_value
    acct.trading_blocked = trading_blocked
    acct.account_blocked = account_blocked
    return acct


def _mock_intent(
    symbol: str = "SPY",
    quantity: float = 1.0,
    entry_price: float | None = 400.0,
) -> MagicMock:
    intent = MagicMock()
    intent.symbol          = symbol
    intent.side            = "buy"
    intent.order_type      = "market"
    intent.quantity        = quantity
    intent.metadata        = {"entry_price": entry_price} if entry_price is not None else {}
    intent.client_order_id = f"BT-{symbol}-001"
    return intent


def _mock_client(
    account: MagicMock | None = None,
    positions: list | None = None,
    orders: list | None = None,
) -> MagicMock:
    client = MagicMock()
    client.get_account.return_value       = account or _mock_account_raw()
    client.get_all_positions.return_value = positions if positions is not None else []
    client.get_orders.return_value        = orders   if orders    is not None else []
    client.submit_order.side_effect       = AssertionError("must not call submit_order")
    client.cancel_order.side_effect       = AssertionError("must not call cancel_order")
    return client


def _mock_cfg(
    symbols: list[str] | None = None,
    live_max_quantity: float = 1.0,
    live_max_notional: float | None = 500.0,
    live_quantity_override: float | None = 1.0,
    screen_symbols: list[str] | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.symbols                                = symbols or ["SPY"]
    cfg.execution.live_max_quantity            = live_max_quantity
    cfg.execution.live_max_notional            = live_max_notional
    cfg.execution.live_quantity_override       = live_quantity_override
    cfg.execution.live_shadow_screen_symbols   = screen_symbols or ["SPY"]
    return cfg


def _run_main(
    tmp_path: Path,
    account_raw: MagicMock | None = None,
    positions: list | None = None,
    orders: list | None = None,
    intents_by_symbol: dict | None = None,
    live_max_quantity: float = 1.0,
    live_max_notional: float | None = 500.0,
    live_quantity_override: float | None = 1.0,
    symbols: list[str] | None = None,
    screen_symbols: list[str] | None = None,
    creds_ok: bool = True,
    live_key: str = "live-key",
    live_secret: str = "live-secret",
) -> tuple[int, MagicMock]:
    """Run main() with everything mocked; return (exit_code, mock_client)."""
    from src.tools.live_readiness_gate import main

    _symbols        = symbols or ["SPY"]
    _screen_symbols = screen_symbols or ["SPY"]

    if intents_by_symbol is None:
        intents_by_symbol = {s: [_mock_intent(s)] for s in set(_symbols + _screen_symbols)}

    mock_client = _mock_client(account=account_raw, positions=positions, orders=orders)
    mock_cfg    = _mock_cfg(
        symbols=_symbols,
        live_max_quantity=live_max_quantity,
        live_max_notional=live_max_notional,
        live_quantity_override=live_quantity_override,
        screen_symbols=_screen_symbols,
    )

    cfg_result  = {"label": "config", "status": "PASS", "detail": ""}
    cred_result = (
        ({"label": "credentials", "status": "PASS", "detail": ""}, (live_key, live_secret))
        if creds_ok else
        ({"label": "credentials", "status": "FAIL", "detail": "missing live credentials"}, None)
    )

    def _preview_side_effect(cfg, symbol):
        return intents_by_symbol.get(symbol, [])

    env = (
        {"ALPACA_LIVE_API_KEY": live_key, "ALPACA_LIVE_SECRET_KEY": live_secret}
        if creds_ok else {}
    )

    argv = [
        "--config",     "config/settings.paper.local.yaml",
        "--output-dir", str(tmp_path),
    ]

    with patch.dict(os.environ, env, clear=True), \
         patch("src.tools.live_readiness_gate.check_config",
               return_value=(cfg_result, mock_cfg)), \
         patch("src.tools.live_readiness_gate.check_credentials",
               return_value=cred_result), \
         patch("src.tools.live_readiness_gate._make_live_client",
               return_value=mock_client), \
         patch("src.tools.live_readiness_gate._run_strategy_preview",
               side_effect=_preview_side_effect):
        try:
            main(argv)
            return 0, mock_client
        except SystemExit as exc:
            return exc.code, mock_client


# ---------------------------------------------------------------------------
# 1. Stage functions (unit)
# ---------------------------------------------------------------------------

class TestStageAccountCheck:
    def test_pass_on_healthy_account(self):
        from src.tools.live_readiness_gate import _stage_account_check
        client = _mock_client(account=_mock_account_raw())
        result = _stage_account_check(client)
        assert result["status"] == "PASS"
        assert result["blockers"] == []

    def test_warn_on_zero_buying_power(self):
        from src.tools.live_readiness_gate import _stage_account_check
        client = _mock_client(account=_mock_account_raw(buying_power="0"))
        result = _stage_account_check(client)
        assert result["status"] == "WARN"
        assert result["blockers"]

    def test_fail_on_trading_blocked(self):
        from src.tools.live_readiness_gate import _stage_account_check
        client = _mock_client(account=_mock_account_raw(trading_blocked=True))
        result = _stage_account_check(client)
        assert result["status"] == "FAIL"

    def test_fail_on_inactive_account(self):
        from src.tools.live_readiness_gate import _stage_account_check
        client = _mock_client(account=_mock_account_raw(status="RESTRICTED"))
        result = _stage_account_check(client)
        assert result["status"] == "FAIL"


class TestComputeDecision:
    def test_go_when_all_pass(self):
        from src.tools.live_readiness_gate import compute_decision
        stages = {s: "PASS" for s in ("a", "b", "c")}
        assert compute_decision(stages) == "GO"

    def test_no_go_on_any_fail(self):
        from src.tools.live_readiness_gate import compute_decision
        stages = {"a": "PASS", "b": "FAIL", "c": "PASS"}
        assert compute_decision(stages) == "NO-GO"

    def test_no_go_on_any_warn(self):
        from src.tools.live_readiness_gate import compute_decision
        stages = {"a": "PASS", "b": "WARN", "c": "PASS"}
        assert compute_decision(stages) == "NO-GO"

    def test_go_empty_stages(self):
        from src.tools.live_readiness_gate import compute_decision
        assert compute_decision({}) == "GO"


class TestTrimBlocker:
    def test_trims_at_dash_separator(self):
        from src.tools.live_readiness_gate import _trim_blocker
        raw = "status=active buying_power=0 — buying_power=0, portfolio_value=0"
        assert _trim_blocker(raw) == "buying_power=0, portfolio_value=0"

    def test_no_dash_returns_original(self):
        from src.tools.live_readiness_gate import _trim_blocker
        assert _trim_blocker("buying_power=0") == "buying_power=0"

    def test_truncates_long_strings(self):
        from src.tools.live_readiness_gate import _trim_blocker
        long = "x" * 200
        result = _trim_blocker(long)
        assert len(result) <= 120
        assert result.endswith("...")

    def test_short_string_not_truncated(self):
        from src.tools.live_readiness_gate import _trim_blocker
        short = "some short blocker"
        assert _trim_blocker(short) == short


class TestCollectTopBlockers:
    def _results(self, **kw) -> dict:
        base = {
            "account_check":        {"blockers": []},
            "shadow_preflight":     {"blockers": []},
            "shadow_review":        {"blockers": []},
            "symbol_screen":        {"blockers": []},
            "symbol_screen_review": {"blockers": []},
        }
        base.update(kw)
        return base

    def test_account_check_blocker_included(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(account_check={"blockers": ["buying_power=0"]})
        blockers = collect_top_blockers(results)
        assert any("account_check" in b for b in blockers)
        assert any("buying_power=0" in b for b in blockers)

    def test_account_check_blocker_trimmed_at_dash(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        raw = "status=active buying_power=0 — buying_power=0, portfolio_value=0"
        results = self._results(account_check={"blockers": [raw]})
        blockers = collect_top_blockers(results)
        assert any("buying_power=0, portfolio_value=0" in b for b in blockers)
        assert not any("status=active" in b for b in blockers)

    def test_shadow_review_preferred_over_shadow_preflight(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            shadow_preflight={"blockers": ["very verbose raw sizing detail A", "B"]},
            shadow_review={"blockers": ["[live_sizing] compact summary"]},
        )
        blockers = collect_top_blockers(results)
        assert any("shadow_review" in b for b in blockers)
        assert not any("shadow_preflight" in b for b in blockers)
        assert any("compact summary" in b for b in blockers)

    def test_raw_shadow_preflight_used_when_review_has_no_blockers(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            shadow_preflight={"blockers": ["preflight blocker"]},
            shadow_review={"blockers": []},
        )
        blockers = collect_top_blockers(results)
        assert any("shadow_preflight" in b for b in blockers)
        assert any("preflight blocker" in b for b in blockers)

    def test_symbol_screen_review_preferred_over_symbol_screen(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            symbol_screen={"blockers": ["SPY: verbose per-symbol blocker A", "QQQ: verbose B"]},
            symbol_screen_review={"blockers": ["No symbols currently suitable."]},
        )
        blockers = collect_top_blockers(results)
        assert any("symbol_screen_review" in b for b in blockers)
        assert not any("symbol_screen" in b and "symbol_screen_review" not in b
                       for b in blockers)
        assert any("No symbols currently suitable" in b for b in blockers)

    def test_raw_symbol_screen_used_when_review_has_no_blockers(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            symbol_screen={"blockers": ["SPY: no candidates"]},
            symbol_screen_review={"blockers": []},
        )
        blockers = collect_top_blockers(results)
        assert any("symbol_screen" in b for b in blockers)

    def test_capped_at_five(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            account_check={"blockers": ["acct error"]},
            shadow_review={"blockers": ["r1", "r2"]},
            symbol_screen_review={"blockers": ["s1", "s2"]},
        )
        assert len(collect_top_blockers(results)) <= 5

    def test_skips_empty_blockers(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(account_check={"blockers": ["", "real error"]})
        blockers = collect_top_blockers(results)
        assert not any(b.endswith("] ") for b in blockers)


# ---------------------------------------------------------------------------
# 2. write_gate_report
# ---------------------------------------------------------------------------

class TestWriteGateReport:
    def test_writes_json(self, tmp_path):
        from src.tools.live_readiness_gate import write_gate_report
        path = write_gate_report(
            tmp_path,
            {"account_check": "PASS"},
            "GO",
            [],
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["decision"] == "GO"
        assert "checked_at_utc" in data
        assert "stages" in data
        assert "top_blockers" in data

    def test_filename(self, tmp_path):
        from src.tools.live_readiness_gate import write_gate_report
        path = write_gate_report(tmp_path, {}, "NO-GO", [])
        assert path.name == "live_readiness_gate_report.json"


# ---------------------------------------------------------------------------
# 3. CLI: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_all_pass_exits_0_decision_go(self, tmp_path):
        code, _ = _run_main(tmp_path)
        assert code in (0, None)
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        assert report["decision"] == "GO"

    def test_account_warn_exits_1_no_go(self, tmp_path):
        code, _ = _run_main(tmp_path, account_raw=_mock_account_raw(buying_power="0"))
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        assert report["decision"] == "NO-GO"

    def test_preflight_fail_exits_1_no_go(self, tmp_path):
        """No candidates for preflight symbol → preflight FAIL."""
        code, _ = _run_main(
            tmp_path,
            intents_by_symbol={"SPY": [], "QQQ": []},
        )
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        assert report["decision"] == "NO-GO"
        assert report["stages"]["shadow_preflight"] == "FAIL"

    def test_symbol_screen_zero_suitable_exits_1_no_go(self, tmp_path):
        """All screen symbols fail notional cap → symbol_screen FAIL."""
        code, _ = _run_main(
            tmp_path,
            live_max_notional=100.0,
            intents_by_symbol={"SPY": [_mock_intent("SPY", entry_price=400.0)]},
        )
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        assert report["decision"] == "NO-GO"

    def test_missing_credentials_exits_1_no_go(self, tmp_path):
        code, _ = _run_main(tmp_path, creds_ok=False)
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        assert report["decision"] == "NO-GO"
        assert all(s == "FAIL" for s in report["stages"].values())

    def test_audit_report_written(self, tmp_path):
        _run_main(tmp_path)
        assert (tmp_path / "live_readiness_gate_report.json").exists()

    def test_preflight_artifacts_written(self, tmp_path):
        _run_main(tmp_path)
        assert (tmp_path / "live_shadow_preflight_report.json").exists()
        assert (tmp_path / "live_shadow_candidates.csv").exists()

    def test_screen_artifacts_written(self, tmp_path):
        _run_main(tmp_path)
        assert (tmp_path / "live_shadow_symbol_screen_report.json").exists()
        assert (tmp_path / "live_shadow_symbol_screen.csv").exists()

    def test_submit_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path)
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path)
        client.cancel_order.assert_not_called()

    def test_no_real_network_calls(self, tmp_path):
        """_make_live_client is always mocked — no real TradingClient instantiated."""
        with patch("src.tools.live_readiness_gate._make_live_client",
                   return_value=_mock_client()) as mock_factory, \
             patch("src.tools.live_readiness_gate.check_config",
                   return_value=({"label": "config", "status": "PASS", "detail": ""}, _mock_cfg())), \
             patch("src.tools.live_readiness_gate.check_credentials",
                   return_value=({"label": "creds", "status": "PASS", "detail": ""}, ("k", "s"))), \
             patch("src.tools.live_readiness_gate._run_strategy_preview",
                   return_value=[_mock_intent("SPY")]), \
             patch.dict(os.environ, {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"},
                        clear=True):
            try:
                from src.tools.live_readiness_gate import main
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path)])
            except SystemExit:
                pass
        mock_factory.assert_called_once()

    def test_no_ledger_writes(self, tmp_path):
        """No ledger CSV should be written — only the five audit artifacts."""
        _run_main(tmp_path)
        files = {f.name for f in tmp_path.iterdir()}
        assert not any("ledger" in name for name in files)

    def test_output_contains_decision(self, tmp_path, capsys):
        _run_main(tmp_path)
        out = capsys.readouterr().out
        assert "decision:" in out

    def test_output_contains_all_stage_names(self, tmp_path, capsys):
        _run_main(tmp_path)
        out = capsys.readouterr().out
        for stage in ("account_check", "shadow_preflight", "shadow_review",
                      "symbol_screen", "symbol_screen_review"):
            assert stage in out, f"missing stage in output: {stage}"

    def test_gate_report_contains_all_stages(self, tmp_path):
        _run_main(tmp_path)
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        for stage in ("account_check", "shadow_preflight", "shadow_review",
                      "symbol_screen", "symbol_screen_review"):
            assert stage in report["stages"], f"missing stage in report: {stage}"

    def test_uses_live_keys_not_paper_keys(self, tmp_path):
        """_make_live_client must be called with LIVE keys."""
        with patch("src.tools.live_readiness_gate._make_live_client",
                   return_value=_mock_client()) as mock_factory, \
             patch("src.tools.live_readiness_gate.check_config",
                   return_value=({"label": "config", "status": "PASS", "detail": ""}, _mock_cfg())), \
             patch("src.tools.live_readiness_gate.check_credentials",
                   return_value=({"label": "creds", "status": "PASS", "detail": ""}, ("LIVE_K", "LIVE_S"))), \
             patch("src.tools.live_readiness_gate._run_strategy_preview",
                   return_value=[_mock_intent("SPY")]), \
             patch.dict(os.environ, {"ALPACA_LIVE_API_KEY": "LIVE_K",
                                     "ALPACA_LIVE_SECRET_KEY": "LIVE_S"}, clear=True):
            try:
                from src.tools.live_readiness_gate import main
                main(["--config", "cfg.yaml", "--output-dir", str(tmp_path)])
            except SystemExit:
                pass
        mock_factory.assert_called_once_with("LIVE_K", "LIVE_S")

    def test_multi_symbol_screen_pass(self, tmp_path):
        """With two passing symbols the gate is GO."""
        code, _ = _run_main(
            tmp_path,
            screen_symbols=["SPY", "QQQ"],
            intents_by_symbol={
                "SPY": [_mock_intent("SPY", entry_price=400.0)],
                "QQQ": [_mock_intent("QQQ", entry_price=150.0)],
            },
        )
        assert code in (0, None)
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text())
        assert report["decision"] == "GO"
