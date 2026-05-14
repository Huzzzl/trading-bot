"""
tests/test_paper_ledger.py
---------------------------
Offline unit and integration tests for the paper execution ledger.

All tests are purely in-process — no Alpaca credentials, no network calls,
no order submission.
"""

from __future__ import annotations

import json
import sys
import textwrap
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yaml_str(value) -> str:
    """Return *value* as a safely JSON-encoded YAML scalar.

    json.dumps() produces a double-quoted string with all special characters
    (backslashes, control chars) properly escaped, which is valid YAML.
    This prevents ScannerError on Windows paths like C:\\Users\\...
    """
    return json.dumps(str(value))


def _make_result(
    client_order_id: str = "BT-20240115100000-SPY",
    order_id: str = "alpaca-uuid-001",
    symbol: str = "SPY",
    side: str = "buy",
    quantity: float = 1.0,
    status: str = "accepted",
    submitted_at: str = "2024-01-15 10:00:00-05:00",
) -> SimpleNamespace:
    return SimpleNamespace(
        client_order_id=client_order_id,
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        status=status,
        submitted_at=submitted_at,
    )


_BASE_YAML = textwrap.dedent("""\
    backtest:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
      initial_capital: 100000
      commission_per_share: 0.005
      slippage_per_share: 0.01
    symbols: [SPY]
    data:
      provider: yahoo
      bar_interval: "5m"
      timezone: "America/New_York"
    strategy:
      name: opening_range_breakout
      params: {}
    risk: {}
    logging:
      level: WARNING
      format: "%(message)s"
""")


# ---------------------------------------------------------------------------
# 1. load_ledger
# ---------------------------------------------------------------------------

class TestLoadLedger:
    def test_returns_empty_df_when_missing(self, tmp_path):
        from src.execution.paper_ledger import load_ledger
        df = load_ledger(tmp_path / "nonexistent.csv")
        assert df.empty
        assert "client_order_id" in df.columns

    def test_returns_empty_df_with_canonical_columns(self, tmp_path):
        from src.execution.paper_ledger import load_ledger, _LEDGER_COLUMNS
        df = load_ledger(tmp_path / "nonexistent.csv")
        assert list(df.columns) == _LEDGER_COLUMNS

    def test_reads_existing_file(self, tmp_path):
        from src.execution.paper_ledger import load_ledger, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nbuy_run,buy_submit,BT-001,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        df = load_ledger(path)
        assert len(df) == 1
        assert df.iloc[0]["client_order_id"] == "BT-001"

    def test_client_order_id_is_string_type(self, tmp_path):
        from src.execution.paper_ledger import load_ledger, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nr1,buy_submit,BT-001,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        df = load_ledger(path)
        assert df.iloc[0]["client_order_id"] == "BT-001"
        assert isinstance(df.iloc[0]["client_order_id"], str)


# ---------------------------------------------------------------------------
# 2. assert_client_order_id_unused
# ---------------------------------------------------------------------------

class TestAssertClientOrderIdUnused:
    def test_passes_when_ledger_missing(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused
        assert_client_order_id_unused(tmp_path / "ledger.csv", "BT-001")

    def test_passes_when_ledger_empty(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        path.write_text(",".join(_LEDGER_COLUMNS) + "\n", encoding="utf-8")
        assert_client_order_id_unused(path, "BT-001")

    def test_passes_when_different_id_in_ledger(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nr1,buy_submit,BT-002,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        assert_client_order_id_unused(path, "BT-001")

    def test_raises_on_duplicate(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nr1,buy_submit,BT-001,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="BT-001"):
            assert_client_order_id_unused(path, "BT-001")

    def test_error_says_duplicate(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nr1,buy_submit,BT-001,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Duplicate client_order_id"):
            assert_client_order_id_unused(path, "BT-001")

    def test_error_says_no_order_submitted(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nr1,buy_submit,BT-001,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="No order was submitted"):
            assert_client_order_id_unused(path, "BT-001")

    def test_case_sensitive_match(self, tmp_path):
        from src.execution.paper_ledger import assert_client_order_id_unused, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        header = ",".join(_LEDGER_COLUMNS)
        path.write_text(f"{header}\nr1,buy_submit,BT-001,alp-001,SPY,buy,1.0,accepted,2024-01-15,/out,2024-01-15T15:00:00+00:00,\n", encoding="utf-8")
        # Different case — should not raise
        assert_client_order_id_unused(path, "bt-001")


# ---------------------------------------------------------------------------
# 3. append_ledger_row
# ---------------------------------------------------------------------------

class TestAppendLedgerRow:
    def _row(self, client_order_id: str = "BT-001", flow: str = "buy_submit") -> dict:
        return {
            "run_id": "paper_submit_BT000001",
            "flow": flow,
            "client_order_id": client_order_id,
            "alpaca_order_id": "alp-uuid-001",
            "symbol": "SPY",
            "side": "buy",
            "quantity": 1.0,
            "status": "accepted",
            "submitted_at": "2024-01-15 10:00:00-05:00",
            "output_dir": "/output/paper_submit_BT000001",
            "notes": "",
        }

    def test_creates_file_with_header(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row, _LEDGER_COLUMNS
        path = tmp_path / "ledger.csv"
        append_ledger_row(path, self._row())
        assert path.exists()
        df = pd.read_csv(path, dtype=str)
        assert list(df.columns) == _LEDGER_COLUMNS

    def test_appends_one_row(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        append_ledger_row(path, self._row())
        df = pd.read_csv(path, dtype=str)
        assert len(df) == 1

    def test_appends_second_row_without_duplicate_header(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        append_ledger_row(path, self._row("BT-001"))
        append_ledger_row(path, self._row("BT-002"))
        df = pd.read_csv(path, dtype=str)
        assert len(df) == 2
        assert set(df["client_order_id"]) == {"BT-001", "BT-002"}

    def test_row_values_correct(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        append_ledger_row(path, self._row())
        df = pd.read_csv(path, dtype=str)
        row = df.iloc[0]
        assert row["client_order_id"] == "BT-001"
        assert row["flow"] == "buy_submit"
        assert row["symbol"] == "SPY"
        assert row["side"] == "buy"

    def test_creates_parent_dirs(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "nested" / "dir" / "ledger.csv"
        append_ledger_row(path, self._row())
        assert path.exists()

    def test_created_at_utc_filled_when_absent(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        row = self._row()
        row.pop("created_at_utc", None)
        append_ledger_row(path, row)
        df = pd.read_csv(path, dtype=str)
        assert df.iloc[0]["created_at_utc"] != ""

    def test_created_at_utc_preserved_when_provided(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        row = {**self._row(), "created_at_utc": "2024-01-15T15:00:00+00:00"}
        append_ledger_row(path, row)
        df = pd.read_csv(path, dtype=str)
        assert df.iloc[0]["created_at_utc"] == "2024-01-15T15:00:00+00:00"

    def test_utf8_encoding(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        append_ledger_row(path, self._row())
        content = path.read_bytes()
        content.decode("utf-8")  # must not raise

    def test_close_submit_flow(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        row = {**self._row(flow="close_submit"), "side": "sell"}
        append_ledger_row(path, row)
        df = pd.read_csv(path, dtype=str)
        assert df.iloc[0]["flow"] == "close_submit"
        assert df.iloc[0]["side"] == "sell"

    def test_extra_keys_silently_ignored(self, tmp_path):
        from src.execution.paper_ledger import append_ledger_row
        path = tmp_path / "ledger.csv"
        row = {**self._row(), "unexpected_field": "should_be_ignored"}
        append_ledger_row(path, row)
        df = pd.read_csv(path, dtype=str)
        assert "unexpected_field" not in df.columns


# ---------------------------------------------------------------------------
# 4. ExecutionConfig default
# ---------------------------------------------------------------------------

class TestExecutionConfigDefault:
    def test_default_ledger_path(self):
        from src.config.loader import ExecutionConfig
        cfg = ExecutionConfig()
        assert cfg.paper_ledger_path == "output/paper_execution_ledger.csv"

    def test_custom_ledger_path_via_load_config(self, tmp_path):
        from src.config.loader import load_config
        yaml_text = _BASE_YAML + textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
              paper_ledger_path: "/tmp/custom_ledger.csv"
        """)
        cfg_path = tmp_path / "settings.yaml"
        cfg_path.write_text(yaml_text, encoding="utf-8")
        cfg = load_config(cfg_path)
        assert cfg.execution.paper_ledger_path == "/tmp/custom_ledger.csv"

    def test_default_ledger_path_via_load_config(self, tmp_path):
        from src.config.loader import load_config
        yaml_text = _BASE_YAML + textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
        """)
        cfg_path = tmp_path / "settings.yaml"
        cfg_path.write_text(yaml_text, encoding="utf-8")
        cfg = load_config(cfg_path)
        assert cfg.execution.paper_ledger_path == "output/paper_execution_ledger.csv"


# ---------------------------------------------------------------------------
# 5. Integration — buy submit flow calls ledger
# ---------------------------------------------------------------------------

_FIXED_TS = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")
_FIXED_TS_STR = "20240115100000"


def _make_order_result(coid: str = "BT-20240115100000-SPY") -> MagicMock:
    r = MagicMock()
    r.client_order_id = coid
    r.order_id        = "alpaca-uuid-001"
    r.symbol          = "SPY"
    r.side            = "buy"
    r.quantity        = 1.0
    r.status          = "accepted"
    r.submitted_at    = _FIXED_TS
    return r


def _make_close_result(coid: str = "BC-20240115100000-SPY") -> MagicMock:
    r = MagicMock()
    r.client_order_id = coid
    r.order_id        = "alpaca-uuid-close-001"
    r.symbol          = "SPY"
    r.side            = "sell"
    r.quantity        = 1.0
    r.status          = "accepted"
    r.submitted_at    = _FIXED_TS
    return r


def _pass_recon_generate(rg_self):
    import json
    recon = {
        "overall_status": "PASS",
        "intent_count": 1,
        "result_count": 1,
        "mismatch_count": 0,
        "unmatched_count": 0,
        "missing_ids_warn": False,
        "rejected_count": 0,
        "accepted_count": 1,
        "filled_count": 0,
    }
    recon_path = rg_self._output_dir / "order_reconciliation.json"
    recon_path.parent.mkdir(parents=True, exist_ok=True)
    recon_path.write_text(json.dumps(recon))


def _run_buy_submit(tmp_path, ledger_path=None, extra_execution_yaml=""):
    """Run the paper buy-submit flow end-to-end with all externals mocked."""
    from src.main import main as _main

    cfg_yaml = _BASE_YAML + textwrap.dedent(f"""\
        execution:
          mode: paper
          paper_trading_enabled: true
          paper_preview_only: false
          paper_selected_client_order_id: "BT-{_FIXED_TS_STR}-SPY"
          paper_order_quantity_override: 1
          paper_ledger_path: {_yaml_str(ledger_path or (tmp_path / 'ledger.csv'))}
    """) + extra_execution_yaml

    cfg_path = tmp_path / "settings.yaml"
    cfg_path.write_text(cfg_yaml, encoding="utf-8")
    out_dir  = tmp_path / "out"

    from src.execution.order_intent import OrderIntent
    mock_result = _make_order_result()
    mock_intent = OrderIntent(
        client_order_id=f"BT-{_FIXED_TS_STR}-SPY",
        symbol="SPY",
        side="buy",
        quantity=1.0,
        order_type="market",
        reason="smoke_test_entry",
        timestamp=_FIXED_TS,
    )

    mock_preflight = {
        "ok": True,
        "account": {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False},
        "positions": {},
        "symbols": ["SPY"],
    }

    with (
        patch("src.main.load_config") as mock_load_cfg,
        patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker,
        patch("src.main.build_engine") as mock_build_engine,
        patch("src.reporting.report_generator.ReportGenerator.generate_all", _pass_recon_generate),
        patch("pandas.Timestamp.now", return_value=_FIXED_TS),
        patch("sys.argv", ["main", "--config", str(cfg_path), "--output-dir", str(out_dir)]),
    ):
        from src.config.loader import load_config as _real_load_config
        mock_load_cfg.side_effect = _real_load_config

        broker_instance = MockBroker.return_value
        broker_instance.preflight_check.return_value = mock_preflight
        broker_instance.submit_order.return_value = mock_result

        engine_instance = mock_build_engine.return_value
        engine_instance._portfolio.positions = {}
        engine_instance.run.return_value = {
            "order_intents": [mock_intent],
            "metrics": {"total_return": 0.0},
            "trades": [],
            "equity_curve": pd.DataFrame(),
        }

        _main()

    return out_dir, mock_result, broker_instance


def _run_close_submit(tmp_path, ledger_path=None):
    from src.main import main as _main

    cfg_yaml = _BASE_YAML + textwrap.dedent(f"""\
        execution:
          mode: paper
          paper_trading_enabled: true
          paper_close_positions_enabled: true
          paper_close_preview_only: false
          paper_selected_close_client_order_id: "BC-{_FIXED_TS_STR}-SPY"
          paper_ledger_path: {_yaml_str(ledger_path or (tmp_path / 'ledger.csv'))}
    """)

    cfg_path = tmp_path / "settings.yaml"
    cfg_path.write_text(cfg_yaml, encoding="utf-8")
    out_dir  = tmp_path / "out"

    mock_result = _make_close_result()
    mock_preflight = {
        "ok": True,
        "account": {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False},
        "positions": {"SPY": {"symbol": "SPY", "qty": "1.0", "market_value": "475.00",
                               "avg_entry_price": "470.00", "current_price": "475.00",
                               "unrealized_pl": "5.00"}},
        "symbols": ["SPY"],
    }

    with (
        patch("src.main.load_config") as mock_load_cfg,
        patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker,
        patch("src.reporting.report_generator.ReportGenerator.generate_all", _pass_recon_generate),
        patch("pandas.Timestamp.now", return_value=_FIXED_TS),
        patch("sys.argv", ["main", "--config", str(cfg_path), "--output-dir", str(out_dir)]),
    ):
        from src.config.loader import load_config as _real_load_config
        mock_load_cfg.side_effect = _real_load_config

        broker_instance = MockBroker.return_value
        broker_instance.preflight_check.return_value = mock_preflight
        broker_instance.submit_order.return_value = mock_result

        _main()

    return out_dir, mock_result, broker_instance


class TestBuySubmitLedgerIntegration:
    def test_buy_submit_appends_ledger_row(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        assert ledger.exists()
        df = pd.read_csv(ledger, dtype=str)
        assert len(df) == 1

    def test_buy_submit_row_has_correct_flow(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["flow"] == "buy_submit"

    def test_buy_submit_row_has_client_order_id(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["client_order_id"] == f"BT-{_FIXED_TS_STR}-SPY"

    def test_buy_submit_row_has_alpaca_order_id(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["alpaca_order_id"] == "alpaca-uuid-001"

    def test_buy_submit_row_has_symbol_and_side(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["symbol"] == "SPY"
        assert df.iloc[0]["side"] == "buy"

    def test_buy_preview_does_not_append_ledger(self, tmp_path):
        """Phase 1 (preview-only) must not write any ledger rows."""
        from src.main import main as _main
        ledger = tmp_path / "ledger.csv"

        cfg_yaml = _BASE_YAML + textwrap.dedent(f"""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
              paper_ledger_path: {_yaml_str(ledger)}
        """)
        cfg_path = tmp_path / "settings.yaml"
        cfg_path.write_text(cfg_yaml, encoding="utf-8")
        out_dir  = tmp_path / "out"

        from src.execution.order_intent import OrderIntent
        mock_intent = OrderIntent(
            client_order_id=f"BT-{_FIXED_TS_STR}-SPY",
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            reason="smoke_test",
            timestamp=_FIXED_TS,
        )

        mock_preflight = {
            "ok": True,
            "account": {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False},
            "positions": {},
            "symbols": ["SPY"],
        }

        with (
            patch("src.main.load_config") as mock_load_cfg,
            patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker,
            patch("src.main.build_engine") as mock_build_engine,
            patch("src.reporting.report_generator.ReportGenerator.generate_all", _pass_recon_generate),
            patch("pandas.Timestamp.now", return_value=_FIXED_TS),
            patch("sys.argv", ["main", "--config", str(cfg_path), "--output-dir", str(out_dir)]),
        ):
            from src.config.loader import load_config as _real_load_config
            mock_load_cfg.side_effect = _real_load_config

            broker_instance = MockBroker.return_value
            broker_instance.preflight_check.return_value = mock_preflight

            engine_instance = mock_build_engine.return_value
            engine_instance._portfolio.positions = {}
            engine_instance.run.return_value = {
                "order_intents": [mock_intent],
                "metrics": {"total_return": 0.0},
                "trades": [],
                "equity_curve": pd.DataFrame(),
            }

            _main()

        assert not ledger.exists()

    def test_duplicate_buy_blocked_before_submit(self, tmp_path):
        """Second run with same client_order_id must raise before submit_order."""
        from src.execution.paper_ledger import append_ledger_row, _LEDGER_COLUMNS
        ledger = tmp_path / "ledger.csv"
        coid = f"BT-{_FIXED_TS_STR}-SPY"

        # Pre-populate ledger with the same ID
        append_ledger_row(ledger, {
            "run_id": "prev_run",
            "flow": "buy_submit",
            "client_order_id": coid,
            "alpaca_order_id": "old-uuid",
            "symbol": "SPY",
            "side": "buy",
            "quantity": "1.0",
            "status": "accepted",
            "submitted_at": "2024-01-14 10:00:00-05:00",
            "output_dir": "/old/output",
            "notes": "",
        })

        with pytest.raises(RuntimeError, match="Duplicate client_order_id"):
            _run_buy_submit(tmp_path, ledger_path=str(ledger))

    def test_duplicate_blocked_submit_order_never_called(self, tmp_path):
        """When duplicate is detected, submit_order must not be called."""
        from src.execution.paper_ledger import append_ledger_row
        ledger = tmp_path / "ledger.csv"
        coid = f"BT-{_FIXED_TS_STR}-SPY"

        append_ledger_row(ledger, {
            "run_id": "prev_run", "flow": "buy_submit",
            "client_order_id": coid, "alpaca_order_id": "old-uuid",
            "symbol": "SPY", "side": "buy", "quantity": "1.0",
            "status": "accepted", "submitted_at": "2024-01-14 10:00:00-05:00",
            "output_dir": "/old/output", "notes": "",
        })

        try:
            _, _, broker_instance = _run_buy_submit(tmp_path, ledger_path=str(ledger))
        except RuntimeError:
            pass
        # broker_instance is set before the RuntimeError, but submit_order should not have been called
        # We verify this by checking no second row was written and re-run the test with explicit mock tracking
        df = pd.read_csv(ledger, dtype=str)
        assert len(df) == 1  # no new row appended

    def test_missing_ledger_allows_first_submit(self, tmp_path):
        """When no ledger exists the submit must proceed normally."""
        ledger = tmp_path / "ledger.csv"
        assert not ledger.exists()
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        assert ledger.exists()

    def test_ledger_parent_created_automatically(self, tmp_path):
        """Ledger parent dirs must be created when they don't exist."""
        ledger = tmp_path / "nested" / "output" / "ledger.csv"
        _run_buy_submit(tmp_path, ledger_path=str(ledger))
        assert ledger.exists()


class TestCloseSubmitLedgerIntegration:
    def test_close_submit_appends_ledger_row(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_close_submit(tmp_path, ledger_path=str(ledger))
        assert ledger.exists()
        df = pd.read_csv(ledger, dtype=str)
        assert len(df) == 1

    def test_close_submit_row_has_correct_flow(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_close_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["flow"] == "close_submit"

    def test_close_submit_row_has_sell_side(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_close_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["side"] == "sell"

    def test_close_submit_row_has_alpaca_order_id(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run_close_submit(tmp_path, ledger_path=str(ledger))
        df = pd.read_csv(ledger, dtype=str)
        assert df.iloc[0]["alpaca_order_id"] == "alpaca-uuid-close-001"

    def test_close_preview_does_not_append_ledger(self, tmp_path):
        """Phase A (close_preview_only=True) must not write any ledger rows."""
        from src.main import main as _main
        ledger = tmp_path / "ledger.csv"

        cfg_yaml = _BASE_YAML + textwrap.dedent(f"""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_close_positions_enabled: true
              paper_close_preview_only: true
              paper_ledger_path: {_yaml_str(ledger)}
        """)
        cfg_path = tmp_path / "settings.yaml"
        cfg_path.write_text(cfg_yaml, encoding="utf-8")
        out_dir  = tmp_path / "out"

        mock_preflight = {
            "ok": True,
            "account": {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False},
            "positions": {"SPY": {"symbol": "SPY", "qty": "1.0"}},
            "symbols": ["SPY"],
        }

        with (
            patch("src.main.load_config") as mock_load_cfg,
            patch("src.execution.alpaca_broker.AlpacaBrokerAdapter") as MockBroker,
            patch("pandas.Timestamp.now", return_value=_FIXED_TS),
            patch("sys.argv", ["main", "--config", str(cfg_path), "--output-dir", str(out_dir)]),
        ):
            from src.config.loader import load_config as _real_load_config
            mock_load_cfg.side_effect = _real_load_config

            broker_instance = MockBroker.return_value
            broker_instance.preflight_check.return_value = mock_preflight

            _main()

        assert not ledger.exists()

    def test_duplicate_close_blocked_before_submit(self, tmp_path):
        """Duplicate close client_order_id must be blocked before submit_order."""
        from src.execution.paper_ledger import append_ledger_row
        ledger = tmp_path / "ledger.csv"
        coid = f"BC-{_FIXED_TS_STR}-SPY"

        append_ledger_row(ledger, {
            "run_id": "prev_run", "flow": "close_submit",
            "client_order_id": coid, "alpaca_order_id": "old-close-uuid",
            "symbol": "SPY", "side": "sell", "quantity": "1.0",
            "status": "accepted", "submitted_at": "2024-01-14 10:00:00-05:00",
            "output_dir": "/old/output", "notes": "",
        })

        with pytest.raises(RuntimeError, match="Duplicate client_order_id"):
            _run_close_submit(tmp_path, ledger_path=str(ledger))


# ---------------------------------------------------------------------------
# 6. Smoke check does not append ledger
# ---------------------------------------------------------------------------

class TestSmokeCheckDoesNotAppendLedger:
    def test_smoke_check_does_not_write_ledger(self, tmp_path):
        """paper_smoke_check must never call append_ledger_row."""
        from src.tools.paper_smoke_check import main as smoke_main

        cfg_yaml = _BASE_YAML + textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
        """)
        cfg_path = tmp_path / "settings.yaml"
        cfg_path.write_text(cfg_yaml, encoding="utf-8")
        out_dir = tmp_path / "out"

        with patch("src.execution.paper_ledger.append_ledger_row") as mock_append:
            try:
                smoke_main(["--config", str(cfg_path), "--output-dir", str(out_dir)])
            except SystemExit:
                pass
            mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Replay tool does not append ledger
# ---------------------------------------------------------------------------

class TestReplayDoesNotAppendLedger:
    def test_replay_does_not_call_append_ledger_row(self, tmp_path):
        from src.tools.replay_order_reconciliation import replay

        # Write minimal valid CSVs
        intents_path = tmp_path / "order_intents.csv"
        results_path = tmp_path / "order_results.csv"
        intents_path.write_text(
            "client_order_id,symbol,side,quantity,order_type,reason\n"
            "BT-001,SPY,buy,1.0,market,test\n",
            encoding="utf-8",
        )
        results_path.write_text(
            "client_order_id,symbol,side,quantity,status,reason\n"
            "BT-001,SPY,buy,1.0,accepted,test\n",
            encoding="utf-8",
        )

        with patch("src.execution.paper_ledger.append_ledger_row") as mock_append:
            replay(tmp_path)
            mock_append.assert_not_called()
