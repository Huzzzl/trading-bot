"""
tests/test_live_dry_run_intents.py
------------------------------------
Tests for src/tools/live_dry_run_intents.py.

Fully offline: no Alpaca calls, no credentials, no orders submitted or
cancelled, no ledger writes.
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
    live_sizing_mode: str = "quantity",
    live_order_notional_override: float | None = None,
    live_max_order_notional: float = 100.0,
) -> MagicMock:
    cfg = MagicMock()
    cfg.symbols                                = symbols or ["SPY"]
    cfg.execution.live_max_quantity            = live_max_quantity
    cfg.execution.live_max_notional            = live_max_notional
    cfg.execution.live_quantity_override       = live_quantity_override
    cfg.execution.live_sizing_mode             = live_sizing_mode
    cfg.execution.live_order_notional_override = live_order_notional_override
    cfg.execution.live_max_order_notional      = live_max_order_notional
    return cfg


def _run_main(
    tmp_path: Path,
    account_raw: MagicMock | None = None,
    positions: list | None = None,
    orders: list | None = None,
    intents: list | None = None,
    symbol: str = "SPY",
    cfg: MagicMock | None = None,
    creds_ok: bool = True,
    live_key: str = "live-key",
    live_secret: str = "live-secret",
) -> tuple[int | None, MagicMock]:
    from src.tools.live_dry_run_intents import main

    mock_client = _mock_client(account=account_raw, positions=positions, orders=orders)
    mock_cfg    = cfg or _mock_cfg()
    _intents    = intents if intents is not None else [_mock_intent(symbol)]

    cfg_result  = {"label": "config", "status": "PASS", "detail": ""}
    cred_result = (
        ({"label": "credentials", "status": "PASS", "detail": ""}, (live_key, live_secret))
        if creds_ok else
        ({"label": "credentials", "status": "FAIL", "detail": "missing live credentials"}, None)
    )

    env = (
        {"ALPACA_LIVE_API_KEY": live_key, "ALPACA_LIVE_SECRET_KEY": live_secret}
        if creds_ok else {}
    )

    argv = ["--config", "config/settings.paper.local.yaml",
            "--output-dir", str(tmp_path), "--symbol", symbol]

    with patch.dict(os.environ, env, clear=True), \
         patch("src.tools.live_dry_run_intents.check_config",
               return_value=(cfg_result, mock_cfg)), \
         patch("src.tools.live_dry_run_intents.check_credentials",
               return_value=cred_result), \
         patch("src.tools.live_dry_run_intents._make_live_client",
               return_value=mock_client), \
         patch("src.tools.live_dry_run_intents._run_strategy_preview",
               return_value=_intents):
        try:
            main(argv)
            return None, mock_client
        except SystemExit as exc:
            return exc.code, mock_client


def _read_summary(tmp_path: Path) -> dict:
    path = tmp_path / "live_dry_run_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _read_intents_json(tmp_path: Path) -> list:
    path = tmp_path / "live_order_intents.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _read_intents_csv(tmp_path: Path) -> list[dict]:
    path = tmp_path / "live_order_intents.csv"
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Unit: build_intent_rows
# ---------------------------------------------------------------------------

class TestBuildIntentRows:
    def test_basic_fields_present(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg()
        rows   = build_intent_rows([intent], cfg, "2026-01-01T00:00:00+00:00", "GO")
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"]          == "SPY"
        assert r["side"]            == "buy"
        assert r["order_type"]      == "market"
        assert r["readiness_decision"] == "GO"

    def test_dry_run_only_and_submit_allowed(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=400.0)
        rows   = build_intent_rows([intent], _mock_cfg(), "2026-01-01T00:00:00+00:00", "GO")
        assert rows[0]["dry_run_only"]   is True
        assert rows[0]["submit_allowed"] is False

    def test_quantity_mode_effective_quantity(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", quantity=1.0, entry_price=400.0)
        cfg    = _mock_cfg(live_quantity_override=1.0)
        rows   = build_intent_rows([intent], cfg, "ts", "GO")
        assert float(rows[0]["effective_quantity"]) == 1.0

    def test_notional_mode_effective_quantity_computed(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        rows = build_intent_rows([intent], cfg, "ts", "GO")
        # 100 / 400 = 0.25
        assert abs(float(rows[0]["effective_quantity"]) - 0.25) < 1e-9

    def test_notional_mode_effective_notional(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        rows = build_intent_rows([intent], cfg, "ts", "GO")
        assert float(rows[0]["effective_notional"]) == 100.0

    def test_missing_entry_price_fails_closed(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=None)
        cfg    = _mock_cfg(live_max_notional=500.0)
        rows   = build_intent_rows([intent], cfg, "ts", "GO")
        assert rows[0]["sizing_status"] == "FAIL"

    def test_invalid_sizing_mode_fails_closed(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="bad_mode")
        rows   = build_intent_rows([intent], cfg, "ts", "GO")
        assert rows[0]["sizing_status"] == "FAIL"
        assert "invalid" in rows[0]["sizing_reason"].lower()

    def test_checked_at_utc_propagated(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        intent = _mock_intent("SPY", entry_price=400.0)
        rows   = build_intent_rows([intent], _mock_cfg(), "2026-05-20T00:00:00+00:00", "GO")
        assert rows[0]["checked_at_utc"] == "2026-05-20T00:00:00+00:00"

    def test_empty_intents_returns_empty(self):
        from src.tools.live_dry_run_intents import build_intent_rows
        rows = build_intent_rows([], _mock_cfg(), "ts", "GO")
        assert rows == []


# ---------------------------------------------------------------------------
# Unit: build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_go_summary(self):
        from src.tools.live_dry_run_intents import build_intent_rows, build_summary
        intent = _mock_intent("SPY", entry_price=400.0)
        rows   = build_intent_rows([intent], _mock_cfg(), "ts", "GO")
        s = build_summary("ts", "SPY", "GO", [], rows)
        assert s["decision"]       == "GO"
        assert s["dry_run_only"]   is True
        assert s["submit_allowed"] is False
        assert s["intent_count"]   == 1

    def test_no_go_summary_empty_intents(self):
        from src.tools.live_dry_run_intents import build_summary
        s = build_summary("ts", "SPY", "NO-GO", ["[live_account] bp=0"], [])
        assert s["decision"]     == "NO-GO"
        assert s["intent_count"] == 0
        assert s["top_blockers"] == ["[live_account] bp=0"]

    def test_pass_fail_counts(self):
        from src.tools.live_dry_run_intents import build_intent_rows, build_summary
        intents = [
            _mock_intent("SPY", entry_price=400.0),
            _mock_intent("SPY", entry_price=None),
        ]
        cfg  = _mock_cfg(live_max_notional=500.0)
        rows = build_intent_rows(intents, cfg, "ts", "GO")
        s    = build_summary("ts", "SPY", "GO", [], rows)
        assert s["pass_count"] == 1
        assert s["fail_count"] == 1


# ---------------------------------------------------------------------------
# Unit: write_artifacts
# ---------------------------------------------------------------------------

class TestWriteArtifacts:
    def test_creates_three_files(self, tmp_path):
        from src.tools.live_dry_run_intents import build_intent_rows, build_summary, write_artifacts
        intent = _mock_intent("SPY", entry_price=400.0)
        rows   = build_intent_rows([intent], _mock_cfg(), "ts", "GO")
        s      = build_summary("ts", "SPY", "GO", [], rows)
        csv_p, json_p, sum_p = write_artifacts(tmp_path, rows, s)
        assert csv_p.exists()
        assert json_p.exists()
        assert sum_p.exists()

    def test_csv_has_correct_columns(self, tmp_path):
        from src.tools.live_dry_run_intents import (
            _INTENT_COLUMNS, build_intent_rows, build_summary, write_artifacts,
        )
        intent = _mock_intent("SPY", entry_price=400.0)
        rows   = build_intent_rows([intent], _mock_cfg(), "ts", "GO")
        s      = build_summary("ts", "SPY", "GO", [], rows)
        csv_p, _, _ = write_artifacts(tmp_path, rows, s)
        with csv_p.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames or []) == set(_INTENT_COLUMNS)

    def test_json_intents_dry_run_flags(self, tmp_path):
        from src.tools.live_dry_run_intents import build_intent_rows, build_summary, write_artifacts
        intent = _mock_intent("SPY", entry_price=400.0)
        rows   = build_intent_rows([intent], _mock_cfg(), "ts", "GO")
        s      = build_summary("ts", "SPY", "GO", [], rows)
        _, json_p, _ = write_artifacts(tmp_path, rows, s)
        data = json.loads(json_p.read_text(encoding="utf-8"))
        assert data[0]["dry_run_only"]   is True
        assert data[0]["submit_allowed"] is False

    def test_empty_rows_writes_csv_header_only(self, tmp_path):
        from src.tools.live_dry_run_intents import build_summary, write_artifacts
        s = build_summary("ts", "SPY", "NO-GO", [], [])
        csv_p, _, _ = write_artifacts(tmp_path, [], s)
        rows = _read_intents_csv(tmp_path)
        assert rows == []

    def test_creates_output_dir(self, tmp_path):
        from src.tools.live_dry_run_intents import build_summary, write_artifacts
        deep = tmp_path / "a" / "b" / "c"
        s = build_summary("ts", "SPY", "GO", [], [])
        write_artifacts(deep, [], s)
        assert deep.exists()


# ---------------------------------------------------------------------------
# Integration: main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_go_exits_0(self, tmp_path):
        code, _ = _run_main(tmp_path)
        assert code in (0, None)

    def test_go_writes_all_three_artifacts(self, tmp_path):
        _run_main(tmp_path)
        assert (tmp_path / "live_order_intents.csv").exists()
        assert (tmp_path / "live_order_intents.json").exists()
        assert (tmp_path / "live_dry_run_summary.json").exists()

    def test_go_summary_decision_go(self, tmp_path):
        _run_main(tmp_path)
        s = _read_summary(tmp_path)
        assert s["decision"] == "GO"

    def test_go_summary_dry_run_flags(self, tmp_path):
        _run_main(tmp_path)
        s = _read_summary(tmp_path)
        assert s["dry_run_only"]   is True
        assert s["submit_allowed"] is False

    def test_go_intents_json_dry_run_flags(self, tmp_path):
        _run_main(tmp_path)
        data = _read_intents_json(tmp_path)
        assert len(data) >= 1
        assert all(r["dry_run_only"]   is True  for r in data)
        assert all(r["submit_allowed"] is False for r in data)

    def test_go_intents_csv_dry_run_flags(self, tmp_path):
        _run_main(tmp_path)
        rows = _read_intents_csv(tmp_path)
        assert len(rows) >= 1
        assert all(r["dry_run_only"]   == "True"  for r in rows)
        assert all(r["submit_allowed"] == "False" for r in rows)

    def test_go_notional_mode_quantity_computed(self, tmp_path):
        cfg = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        intent = _mock_intent("SPY", entry_price=400.0)
        _run_main(tmp_path, intents=[intent], cfg=cfg)
        data = _read_intents_json(tmp_path)
        assert abs(float(data[0]["effective_quantity"]) - 0.25) < 1e-9

    def test_no_go_account_warn_exits_1(self, tmp_path):
        code, _ = _run_main(tmp_path, account_raw=_mock_account_raw(buying_power="0"))
        assert code == 1

    def test_no_go_writes_summary_only(self, tmp_path):
        _run_main(tmp_path, account_raw=_mock_account_raw(buying_power="0"))
        assert (tmp_path / "live_dry_run_summary.json").exists()
        summary = _read_summary(tmp_path)
        assert summary["decision"] == "NO-GO"

    def test_no_go_summary_empty_intents(self, tmp_path):
        _run_main(tmp_path, account_raw=_mock_account_raw(buying_power="0"))
        summary = _read_summary(tmp_path)
        assert summary["intent_count"] == 0

    def test_no_go_no_candidates_exits_1(self, tmp_path):
        code, _ = _run_main(tmp_path, intents=[])
        assert code == 1

    def test_no_go_existing_position_exits_1(self, tmp_path):
        pos = MagicMock()
        pos.symbol = "SPY"
        code, _ = _run_main(tmp_path, positions=[pos])
        assert code == 1

    def test_no_go_existing_open_order_exits_1(self, tmp_path):
        order = MagicMock()
        order.symbol = "SPY"
        code, _ = _run_main(tmp_path, orders=[order])
        assert code == 1

    def test_submit_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path)
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        _, client = _run_main(tmp_path)
        client.cancel_order.assert_not_called()

    def test_submit_order_never_called_on_no_go(self, tmp_path):
        _, client = _run_main(tmp_path, intents=[])
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called_on_no_go(self, tmp_path):
        _, client = _run_main(tmp_path, intents=[])
        client.cancel_order.assert_not_called()

    def test_missing_entry_price_fails_closed(self, tmp_path):
        # entry_price=None with live_max_notional set causes sizing FAIL → NO-GO
        intent = _mock_intent("SPY", entry_price=None)
        cfg    = _mock_cfg(live_max_notional=500.0)
        code, _ = _run_main(tmp_path, intents=[intent], cfg=cfg)
        assert code == 1
        summary = _read_summary(tmp_path)
        assert summary["decision"] == "NO-GO"

    def test_invalid_sizing_mode_produces_fail_intent(self, tmp_path):
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="bad_mode")
        # Sizing FAIL causes NO-GO in readiness checks
        code, _ = _run_main(tmp_path, intents=[intent], cfg=cfg)
        assert code == 1

    def test_creds_fail_exits_1(self, tmp_path):
        code, _ = _run_main(tmp_path, creds_ok=False)
        assert code == 1

    def test_no_ledger_written(self, tmp_path):
        _run_main(tmp_path)
        ledger_files = list(tmp_path.glob("**/*ledger*"))
        assert ledger_files == []

    def test_symbol_uppercased(self, tmp_path):
        intent = _mock_intent("spy", entry_price=400.0)
        intent.symbol = "SPY"
        _run_main(tmp_path, symbol="spy", intents=[intent])
        s = _read_summary(tmp_path)
        assert s["symbol"] == "SPY"
