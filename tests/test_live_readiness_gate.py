"""
tests/test_live_readiness_gate.py
-----------------------------------
Tests for src/tools/live_readiness_gate.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no ledger writes.
"""

from __future__ import annotations

import csv
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
    live_sizing_mode: str = "quantity",
    live_order_notional_override: float | None = None,
    live_max_order_notional: float = 100.0,
) -> MagicMock:
    cfg = MagicMock()
    cfg.symbols                                    = symbols or ["SPY"]
    cfg.execution.live_max_quantity                = live_max_quantity
    cfg.execution.live_max_notional                = live_max_notional
    cfg.execution.live_quantity_override           = live_quantity_override
    cfg.execution.live_shadow_screen_symbols       = screen_symbols or ["SPY"]
    cfg.execution.live_sizing_mode                 = live_sizing_mode
    cfg.execution.live_order_notional_override     = live_order_notional_override
    cfg.execution.live_max_order_notional          = live_max_order_notional
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
    extra_argv: list[str] | None = None,
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
    ] + (extra_argv or [])

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
        # All stages default to PASS with no blockers; override per-test as needed.
        base = {
            "account_check":        {"status": "PASS", "blockers": []},
            "shadow_preflight":     {"status": "PASS", "blockers": []},
            "shadow_review":        {"status": "PASS", "blockers": []},
            "symbol_screen":        {"status": "PASS", "blockers": []},
            "symbol_screen_review": {"status": "PASS", "blockers": []},
        }
        base.update(kw)
        return base

    def test_all_pass_returns_empty(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        assert collect_top_blockers(self._results()) == []

    def test_pass_stage_with_blockers_ignored(self):
        # A PASS stage that carries a blocker list must not surface it.
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            symbol_screen_review={"status": "PASS", "blockers": ["some suggested action"]},
        )
        assert collect_top_blockers(results) == []

    def test_account_check_blocker_included(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(account_check={"status": "WARN", "blockers": ["buying_power=0"]})
        blockers = collect_top_blockers(results)
        assert any("account_check" in b for b in blockers)
        assert any("buying_power=0" in b for b in blockers)

    def test_account_check_blocker_trimmed_at_dash(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        raw = "status=active buying_power=0 — buying_power=0, portfolio_value=0"
        results = self._results(account_check={"status": "FAIL", "blockers": [raw]})
        blockers = collect_top_blockers(results)
        assert any("buying_power=0, portfolio_value=0" in b for b in blockers)
        assert not any("status=active" in b for b in blockers)

    def test_shadow_review_preferred_over_shadow_preflight(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            shadow_preflight={"status": "FAIL", "blockers": ["very verbose raw sizing detail A", "B"]},
            shadow_review={"status": "FAIL", "blockers": ["[live_sizing] compact summary"]},
        )
        blockers = collect_top_blockers(results)
        assert any("shadow_review" in b for b in blockers)
        assert not any("shadow_preflight" in b for b in blockers)
        assert any("compact summary" in b for b in blockers)

    def test_raw_shadow_preflight_used_when_review_passes(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            shadow_preflight={"status": "FAIL", "blockers": ["preflight blocker"]},
            shadow_review={"status": "PASS", "blockers": []},
        )
        blockers = collect_top_blockers(results)
        assert any("shadow_preflight" in b for b in blockers)
        assert any("preflight blocker" in b for b in blockers)

    def test_shadow_preflight_skipped_when_review_is_blocking(self):
        # review FAIL but empty blocker list — preflight fallback must not appear
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            shadow_preflight={"status": "FAIL", "blockers": ["preflight blocker"]},
            shadow_review={"status": "FAIL", "blockers": []},
        )
        blockers = collect_top_blockers(results)
        assert not any("shadow_preflight" in b for b in blockers)

    def test_symbol_screen_review_preferred_over_symbol_screen(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            symbol_screen={"status": "FAIL", "blockers": ["SPY: verbose per-symbol blocker A", "QQQ: verbose B"]},
            symbol_screen_review={"status": "FAIL", "blockers": ["No symbols currently suitable."]},
        )
        blockers = collect_top_blockers(results)
        assert any("symbol_screen_review" in b for b in blockers)
        assert not any("symbol_screen" in b and "symbol_screen_review" not in b
                       for b in blockers)
        assert any("No symbols currently suitable" in b for b in blockers)

    def test_raw_symbol_screen_used_when_review_passes(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            symbol_screen={"status": "FAIL", "blockers": ["SPY: no candidates"]},
            symbol_screen_review={"status": "PASS", "blockers": []},
        )
        blockers = collect_top_blockers(results)
        assert any("symbol_screen" in b for b in blockers)

    def test_capped_at_five(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(
            account_check={"status": "FAIL", "blockers": ["acct error"]},
            shadow_review={"status": "FAIL", "blockers": ["r1", "r2"]},
            symbol_screen_review={"status": "FAIL", "blockers": ["s1", "s2"]},
        )
        assert len(collect_top_blockers(results)) <= 5

    def test_skips_empty_blocker_strings(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(account_check={"status": "WARN", "blockers": ["", "real error"]})
        blockers = collect_top_blockers(results)
        assert not any(b.endswith("] ") for b in blockers)

    def test_warn_stage_contributes_blockers(self):
        from src.tools.live_readiness_gate import collect_top_blockers
        results = self._results(account_check={"status": "WARN", "blockers": ["warn detail"]})
        blockers = collect_top_blockers(results)
        assert any("warn detail" in b for b in blockers)


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
        data = json.loads(path.read_text(encoding="utf-8"))
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
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
        assert report["decision"] == "GO"

    def test_go_report_has_empty_top_blockers(self, tmp_path):
        code, _ = _run_main(tmp_path)
        assert code in (0, None)
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
        assert report["decision"] == "GO"
        assert report["top_blockers"] == []

    def test_account_warn_exits_1_no_go(self, tmp_path):
        code, _ = _run_main(tmp_path, account_raw=_mock_account_raw(buying_power="0"))
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
        assert report["decision"] == "NO-GO"

    def test_preflight_fail_exits_1_no_go(self, tmp_path):
        """No candidates for preflight symbol → preflight FAIL."""
        code, _ = _run_main(
            tmp_path,
            intents_by_symbol={"SPY": [], "QQQ": []},
        )
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
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
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
        assert report["decision"] == "NO-GO"

    def test_missing_credentials_exits_1_no_go(self, tmp_path):
        code, _ = _run_main(tmp_path, creds_ok=False)
        assert code == 1
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
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
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
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
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
        assert report["decision"] == "GO"


# ---------------------------------------------------------------------------
# 4. append_history_row (unit)
# ---------------------------------------------------------------------------

class TestAppendHistoryRow:
    def test_creates_file_with_header_on_first_write(self, tmp_path):
        from src.tools.live_readiness_gate import append_history_row
        hist = tmp_path / "history.csv"
        append_history_row(hist, "2026-01-01T00:00:00+00:00", "GO",
                           {"account_check": "PASS"}, [])
        assert hist.exists()
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["decision"] == "GO"
        assert rows[0]["checked_at_utc"] == "2026-01-01T00:00:00+00:00"

    def test_appends_second_row_without_duplicate_header(self, tmp_path):
        from src.tools.live_readiness_gate import append_history_row
        hist = tmp_path / "history.csv"
        append_history_row(hist, "2026-01-01T00:00:00+00:00", "GO",
                           {"account_check": "PASS"}, [])
        append_history_row(hist, "2026-01-02T00:00:00+00:00", "NO-GO",
                           {"account_check": "WARN"}, ["[account_check] warn"])
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[1]["decision"] == "NO-GO"
        assert rows[1]["checked_at_utc"] == "2026-01-02T00:00:00+00:00"

    def test_stage_statuses_recorded(self, tmp_path):
        from src.tools.live_readiness_gate import append_history_row
        hist = tmp_path / "history.csv"
        stages = {
            "account_check":        "PASS",
            "shadow_preflight":     "PASS",
            "shadow_review":        "FAIL",
            "symbol_screen":        "WARN",
            "symbol_screen_review": "FAIL",
        }
        append_history_row(hist, "2026-01-01T00:00:00+00:00", "NO-GO", stages, [])
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["account_check"]        == "PASS"
        assert rows[0]["shadow_preflight"]     == "PASS"
        assert rows[0]["shadow_review"]        == "FAIL"
        assert rows[0]["symbol_screen"]        == "WARN"
        assert rows[0]["symbol_screen_review"] == "FAIL"

    def test_top_blockers_joined_with_pipe(self, tmp_path):
        from src.tools.live_readiness_gate import append_history_row
        hist = tmp_path / "history.csv"
        blockers = ["[account_check] buying_power=0", "[shadow_review] sizing exceeded"]
        append_history_row(hist, "2026-01-01T00:00:00+00:00", "NO-GO", {}, blockers)
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["top_blockers"] == (
            "[account_check] buying_power=0 | [shadow_review] sizing exceeded"
        )

    def test_creates_parent_dirs(self, tmp_path):
        from src.tools.live_readiness_gate import append_history_row
        hist = tmp_path / "sub" / "deep" / "history.csv"
        append_history_row(hist, "2026-01-01T00:00:00+00:00", "GO", {}, [])
        assert hist.exists()

    def test_silently_skips_on_write_error(self, tmp_path):
        from src.tools.live_readiness_gate import append_history_row
        hist = tmp_path / "history.csv"
        hist.mkdir()  # make it a directory so open() fails
        append_history_row(hist, "2026-01-01T00:00:00+00:00", "GO", {}, [])
        # should not raise


# ---------------------------------------------------------------------------
# 5. CLI: --append-history flag
# ---------------------------------------------------------------------------

class TestAppendHistory:
    def test_default_no_history_written(self, tmp_path):
        """Without --append-history, no CSV beyond the standard artifacts is written."""
        _run_main(tmp_path)
        files = {f.name for f in tmp_path.iterdir()}
        assert not any("history" in name for name in files)

    def test_append_history_creates_csv(self, tmp_path):
        hist = tmp_path / "history.csv"
        _run_main(tmp_path, extra_argv=["--append-history", str(hist)])
        assert hist.exists()

    def test_append_history_csv_has_header_and_row(self, tmp_path):
        hist = tmp_path / "history.csv"
        _run_main(tmp_path, extra_argv=["--append-history", str(hist)])
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert "decision" in rows[0]
        assert "checked_at_utc" in rows[0]

    def test_second_run_appends_row_no_duplicate_header(self, tmp_path):
        hist = tmp_path / "history.csv"
        extra = ["--append-history", str(hist)]
        _run_main(tmp_path, extra_argv=extra)
        _run_main(tmp_path, extra_argv=extra)
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2

    def test_decision_and_stages_recorded_correctly(self, tmp_path):
        hist = tmp_path / "history.csv"
        _run_main(tmp_path, extra_argv=["--append-history", str(hist)])
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        row = rows[0]
        assert row["decision"] in ("GO", "NO-GO")
        for stage in ("account_check", "shadow_preflight", "shadow_review",
                      "symbol_screen", "symbol_screen_review"):
            assert row[stage] in ("PASS", "FAIL", "WARN"), \
                f"stage {stage!r} has unexpected value: {row[stage]!r}"

    def test_timestamp_matches_gate_report(self, tmp_path):
        """checked_at_utc in history CSV matches the gate report JSON."""
        hist = tmp_path / "history.csv"
        _run_main(tmp_path, extra_argv=["--append-history", str(hist)])
        report = json.loads((tmp_path / "live_readiness_gate_report.json").read_text(encoding="utf-8"))
        with hist.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["checked_at_utc"] == report["checked_at_utc"]

    def test_no_ledger_writes_with_history(self, tmp_path):
        hist = tmp_path / "history.csv"
        _run_main(tmp_path, extra_argv=["--append-history", str(hist)])
        files = {f.name for f in tmp_path.iterdir()}
        assert not any("ledger" in name for name in files)

    def test_submit_order_never_called_with_history(self, tmp_path):
        hist = tmp_path / "history.csv"
        _, client = _run_main(tmp_path, extra_argv=["--append-history", str(hist)])
        client.submit_order.assert_not_called()

    def test_history_outside_output_dir(self, tmp_path):
        """--append-history path may be outside --output-dir."""
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        hist = tmp_path / "history" / "log.csv"
        mock_client = _mock_client(account=_mock_account_raw())
        mock_cfg    = _mock_cfg(symbols=["SPY"], screen_symbols=["SPY"])
        with patch.dict(os.environ,
                        {"ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s"}, clear=True), \
             patch("src.tools.live_readiness_gate.check_config",
                   return_value=({"label": "config", "status": "PASS", "detail": ""}, mock_cfg)), \
             patch("src.tools.live_readiness_gate.check_credentials",
                   return_value=({"label": "creds", "status": "PASS", "detail": ""}, ("k", "s"))), \
             patch("src.tools.live_readiness_gate._make_live_client",
                   return_value=mock_client), \
             patch("src.tools.live_readiness_gate._run_strategy_preview",
                   return_value=[_mock_intent("SPY")]):
            try:
                from src.tools.live_readiness_gate import main
                main([
                    "--config",         "cfg.yaml",
                    "--output-dir",     str(out_dir),
                    "--append-history", str(hist),
                ])
            except SystemExit:
                pass
        assert hist.exists()
