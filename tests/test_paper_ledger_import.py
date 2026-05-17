"""
tests/test_paper_ledger_import.py
-----------------------------------
Offline tests for src/tools/paper_ledger_import.py.

All tests run without Alpaca credentials, network access, or real orders.
The conftest autouse fixture does NOT patch the ledger functions for this
file (it is in the exclusion list alongside test_paper_ledger.py and
test_paper_status.py), so real ledger writes hit the tmp_path filesystem.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# CSV writing helpers
# ---------------------------------------------------------------------------

def _write_results_csv(path: Path, rows: list[dict] | None = None) -> None:
    if rows is None:
        rows = [
            {
                "client_order_id": "BT-000035",
                "alpaca_order_id": "alp-aaa-111",
                "symbol": "SPY",
                "side": "buy",
                "quantity": "1.0",
                "status": "filled",
                "submitted_at": "2024-01-15 10:05:00-05:00",
            }
        ]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def _write_intents_csv(path: Path, rows: list[dict] | None = None) -> None:
    if rows is None:
        rows = [
            {
                "client_order_id": "BT-000035",
                "symbol": "SPY",
                "side": "buy",
                "quantity": "1.0",
                "order_type": "market",
                "reason": "breakout",
            }
        ]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def _write_manual_csv(path: Path, rows: list[dict] | None = None) -> None:
    if rows is None:
        rows = [
            {
                "client_order_id": "BT-MANUAL-001",
                "alpaca_order_id": "alp-manual-aaa",
                "symbol": "SPY",
                "side": "buy",
                "quantity": "1.0",
                "status": "filled",
                "submitted_at": "2024-01-10 10:00:00-05:00",
                "flow": "buy_submit",
            }
        ]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


# ---------------------------------------------------------------------------
# _rows_from_artifacts
# ---------------------------------------------------------------------------

class TestRowsFromArtifacts:
    def test_raises_when_results_missing(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        with pytest.raises(FileNotFoundError, match="order_results.csv"):
            _rows_from_artifacts(tmp_path)

    def test_returns_one_row_per_result(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(tmp_path / "order_results.csv")
        rows = _rows_from_artifacts(tmp_path)
        assert len(rows) == 1

    def test_client_order_id_populated(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(tmp_path / "order_results.csv")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["client_order_id"] == "BT-000035"

    def test_buy_side_maps_to_buy_submit(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(tmp_path / "order_results.csv")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["flow"] == "buy_submit"

    def test_sell_side_maps_to_close_submit(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(
            tmp_path / "order_results.csv",
            rows=[{
                "client_order_id": "BC-001",
                "alpaca_order_id": "alp-sell-001",
                "symbol": "SPY",
                "side": "sell",
                "quantity": "1.0",
                "status": "filled",
                "submitted_at": "2024-01-16 14:00:00-05:00",
            }],
        )
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["flow"] == "close_submit"

    def test_run_id_is_dir_name(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        artifact_dir = tmp_path / "paper_submit_BT000035"
        artifact_dir.mkdir()
        _write_results_csv(artifact_dir / "order_results.csv")
        rows = _rows_from_artifacts(artifact_dir)
        assert rows[0]["run_id"] == "paper_submit_BT000035"

    def test_output_dir_is_absolute_path(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(tmp_path / "order_results.csv")
        rows = _rows_from_artifacts(tmp_path)
        assert Path(rows[0]["output_dir"]).is_absolute()

    def test_symbol_filled_from_intents_when_missing_in_results(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        # Results row has no symbol column
        pd.DataFrame([{
            "client_order_id": "BT-000035",
            "alpaca_order_id": "alp-aaa-111",
            "side": "buy",
            "quantity": "1.0",
            "status": "filled",
            "submitted_at": "2024-01-15 10:05:00-05:00",
        }]).to_csv(tmp_path / "order_results.csv", index=False, encoding="utf-8")
        _write_intents_csv(tmp_path / "order_intents.csv")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["symbol"] == "SPY"

    def test_notes_set_to_backfilled(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(tmp_path / "order_results.csv")
        rows = _rows_from_artifacts(tmp_path)
        assert "backfill" in rows[0]["notes"]

    def test_multiple_results_produces_multiple_rows(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(
            tmp_path / "order_results.csv",
            rows=[
                {"client_order_id": "BT-000035", "alpaca_order_id": "a1",
                 "symbol": "SPY", "side": "buy", "quantity": "1.0",
                 "status": "filled", "submitted_at": "2024-01-15 10:05:00-05:00"},
                {"client_order_id": "BT-000039", "alpaca_order_id": "a2",
                 "symbol": "SPY", "side": "buy", "quantity": "1.0",
                 "status": "filled", "submitted_at": "2024-01-16 10:05:00-05:00"},
            ],
        )
        rows = _rows_from_artifacts(tmp_path)
        assert len(rows) == 2

    def test_order_id_column_used_as_fallback_for_alpaca_order_id(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        pd.DataFrame([{
            "client_order_id": "BT-000035",
            "order_id": "alp-legacy-999",
            "symbol": "SPY",
            "side": "buy",
            "quantity": "1.0",
            "status": "filled",
            "submitted_at": "2024-01-15 10:05:00-05:00",
        }]).to_csv(tmp_path / "order_results.csv", index=False, encoding="utf-8")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["alpaca_order_id"] == "alp-legacy-999"

    def test_alpaca_order_id_preferred_over_order_id(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        pd.DataFrame([{
            "client_order_id": "BT-000035",
            "alpaca_order_id": "alp-new-111",
            "order_id": "alp-old-999",
            "symbol": "SPY",
            "side": "buy",
            "quantity": "1.0",
            "status": "filled",
            "submitted_at": "2024-01-15 10:05:00-05:00",
        }]).to_csv(tmp_path / "order_results.csv", index=False, encoding="utf-8")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["alpaca_order_id"] == "alp-new-111"

    def test_empty_alpaca_order_id_falls_back_to_order_id(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        pd.DataFrame([{
            "client_order_id": "BT-000035",
            "alpaca_order_id": "",
            "order_id": "alp-legacy-999",
            "symbol": "SPY",
            "side": "buy",
            "quantity": "1.0",
            "status": "filled",
            "submitted_at": "2024-01-15 10:05:00-05:00",
        }]).to_csv(tmp_path / "order_results.csv", index=False, encoding="utf-8")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["alpaca_order_id"] == "alp-legacy-999"

    def test_enum_side_buy_normalised(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        pd.DataFrame([{
            "client_order_id": "BT-000035",
            "alpaca_order_id": "alp-aaa-111",
            "symbol": "SPY",
            "side": "OrderSide.BUY",
            "quantity": "1.0",
            "status": "filled",
            "submitted_at": "2024-01-15 10:05:00-05:00",
        }]).to_csv(tmp_path / "order_results.csv", index=False, encoding="utf-8")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["side"] == "buy"
        assert rows[0]["flow"] == "buy_submit"

    def test_enum_side_sell_normalised(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_artifacts
        pd.DataFrame([{
            "client_order_id": "BC-001",
            "alpaca_order_id": "alp-bbb-222",
            "symbol": "SPY",
            "side": "OrderSide.SELL",
            "quantity": "1.0",
            "status": "filled",
            "submitted_at": "2024-01-16 14:00:00-05:00",
        }]).to_csv(tmp_path / "order_results.csv", index=False, encoding="utf-8")
        rows = _rows_from_artifacts(tmp_path)
        assert rows[0]["side"] == "sell"
        assert rows[0]["flow"] == "close_submit"


# ---------------------------------------------------------------------------
# _rows_from_manual_csv
# ---------------------------------------------------------------------------

class TestRowsFromManualCsv:
    def test_raises_when_file_missing(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        with pytest.raises(FileNotFoundError):
            _rows_from_manual_csv(tmp_path / "nonexistent.csv")

    def test_raises_when_no_client_order_id_column(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "bad.csv"
        pd.DataFrame([{"symbol": "SPY"}]).to_csv(path, index=False, encoding="utf-8")
        with pytest.raises(ValueError, match="client_order_id"):
            _rows_from_manual_csv(path)

    def test_returns_one_row_per_csv_row(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        _write_manual_csv(path)
        rows = _rows_from_manual_csv(path)
        assert len(rows) == 1

    def test_client_order_id_populated(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        _write_manual_csv(path)
        rows = _rows_from_manual_csv(path)
        assert rows[0]["client_order_id"] == "BT-MANUAL-001"

    def test_flow_inferred_from_side_when_absent(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        pd.DataFrame([{
            "client_order_id": "BT-X",
            "side": "sell",
            "symbol": "SPY",
        }]).to_csv(path, index=False, encoding="utf-8")
        rows = _rows_from_manual_csv(path)
        assert rows[0]["flow"] == "close_submit"

    def test_explicit_flow_preserved(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        _write_manual_csv(path)
        rows = _rows_from_manual_csv(path)
        assert rows[0]["flow"] == "buy_submit"

    def test_enum_side_buy_normalised(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        pd.DataFrame([{
            "client_order_id": "BT-X",
            "side": "OrderSide.BUY",
            "symbol": "SPY",
        }]).to_csv(path, index=False, encoding="utf-8")
        rows = _rows_from_manual_csv(path)
        assert rows[0]["side"] == "buy"
        assert rows[0]["flow"] == "buy_submit"

    def test_notes_set_to_manual_import_when_absent(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        pd.DataFrame([{"client_order_id": "BT-X", "side": "buy"}]).to_csv(
            path, index=False, encoding="utf-8"
        )
        rows = _rows_from_manual_csv(path)
        assert "manual" in rows[0]["notes"]

    def test_explicit_notes_preserved(self, tmp_path):
        from src.tools.paper_ledger_import import _rows_from_manual_csv
        path = tmp_path / "manual.csv"
        pd.DataFrame([{
            "client_order_id": "BT-X",
            "side": "buy",
            "notes": "my custom note",
        }]).to_csv(path, index=False, encoding="utf-8")
        rows = _rows_from_manual_csv(path)
        assert rows[0]["notes"] == "my custom note"


# ---------------------------------------------------------------------------
# import_rows
# ---------------------------------------------------------------------------

class TestImportRows:
    def _artifact_rows(self, tmp_path: Path) -> list:
        from src.tools.paper_ledger_import import _rows_from_artifacts
        _write_results_csv(tmp_path / "order_results.csv")
        return _rows_from_artifacts(tmp_path)

    def test_appends_row_to_new_ledger(self, tmp_path):
        from src.tools.paper_ledger_import import import_rows
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        import_rows(ledger, rows)
        df = load_ledger(ledger)
        assert len(df) == 1
        assert df.iloc[0]["client_order_id"] == "BT-000035"

    def test_duplicate_raises_by_default(self, tmp_path):
        from src.tools.paper_ledger_import import import_rows
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        import_rows(ledger, rows)
        with pytest.raises(RuntimeError, match="Duplicate"):
            import_rows(ledger, rows)

    def test_skip_duplicates_skips_not_raises(self, tmp_path):
        from src.tools.paper_ledger_import import import_rows
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        import_rows(ledger, rows)
        appended, skipped = import_rows(ledger, rows, skip_duplicates=True)
        assert appended == 0
        assert skipped == 1
        assert len(load_ledger(ledger)) == 1  # still only one row

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        from src.tools.paper_ledger_import import import_rows
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        appended, skipped = import_rows(ledger, rows, dry_run=True)
        assert not ledger.exists()
        assert appended == 1
        assert skipped == 0

    def test_dry_run_prints_row(self, tmp_path, capsys):
        from src.tools.paper_ledger_import import import_rows
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        import_rows(ledger, rows, dry_run=True)
        out = capsys.readouterr().out
        assert "BT-000035" in out

    def test_returns_appended_count(self, tmp_path):
        from src.tools.paper_ledger_import import import_rows
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        appended, skipped = import_rows(ledger, rows)
        assert appended == 1
        assert skipped == 0

    def test_no_broker_no_network(self, tmp_path):
        """Confirm no AlpacaBrokerAdapter is ever touched."""
        from src.tools.paper_ledger_import import import_rows
        ledger = tmp_path / "ledger.csv"
        rows = self._artifact_rows(tmp_path)
        # If broker is imported/instantiated, the patch raises AttributeError
        with patch(
            "src.execution.alpaca_broker.AlpacaBrokerAdapter",
            side_effect=AssertionError("broker must not be instantiated"),
        ):
            import_rows(ledger, rows)  # must not raise


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

class TestMain:
    def _artifact_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "paper_submit_BT000035"
        d.mkdir()
        _write_results_csv(d / "order_results.csv")
        return d

    def test_artifact_import_appends_row(self, tmp_path):
        from src.tools.paper_ledger_import import main
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        d = self._artifact_dir(tmp_path)
        main(["--ledger", str(ledger), "--from-artifacts", str(d)])
        df = load_ledger(ledger)
        assert len(df) == 1
        assert df.iloc[0]["client_order_id"] == "BT-000035"

    def test_duplicate_exits_1(self, tmp_path):
        from src.tools.paper_ledger_import import main
        ledger = tmp_path / "ledger.csv"
        d = self._artifact_dir(tmp_path)
        main(["--ledger", str(ledger), "--from-artifacts", str(d)])
        with pytest.raises(SystemExit) as exc:
            main(["--ledger", str(ledger), "--from-artifacts", str(d)])
        assert exc.value.code == 1

    def test_skip_duplicates_exits_0(self, tmp_path):
        from src.tools.paper_ledger_import import main
        ledger = tmp_path / "ledger.csv"
        d = self._artifact_dir(tmp_path)
        main(["--ledger", str(ledger), "--from-artifacts", str(d)])
        # Second import with --skip-duplicates must not raise
        main(["--ledger", str(ledger), "--from-artifacts", str(d), "--skip-duplicates"])

    def test_dry_run_writes_nothing(self, tmp_path):
        from src.tools.paper_ledger_import import main
        ledger = tmp_path / "ledger.csv"
        d = self._artifact_dir(tmp_path)
        main(["--ledger", str(ledger), "--from-artifacts", str(d), "--dry-run"])
        assert not ledger.exists()

    def test_manual_csv_import(self, tmp_path):
        from src.tools.paper_ledger_import import main
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        manual = tmp_path / "manual.csv"
        _write_manual_csv(manual)
        main(["--ledger", str(ledger), "--manual-row", str(manual)])
        df = load_ledger(ledger)
        assert len(df) == 1
        assert df.iloc[0]["client_order_id"] == "BT-MANUAL-001"

    def test_missing_artifacts_exits_1(self, tmp_path):
        from src.tools.paper_ledger_import import main
        ledger = tmp_path / "ledger.csv"
        with pytest.raises(SystemExit) as exc:
            main(["--ledger", str(ledger), "--from-artifacts", str(tmp_path / "nonexistent")])
        assert exc.value.code == 1

    def test_missing_manual_csv_exits_1(self, tmp_path):
        from src.tools.paper_ledger_import import main
        ledger = tmp_path / "ledger.csv"
        with pytest.raises(SystemExit) as exc:
            main(["--ledger", str(ledger), "--manual-row", str(tmp_path / "nonexistent.csv")])
        assert exc.value.code == 1

    def test_run_id_is_artifact_dir_name(self, tmp_path):
        from src.tools.paper_ledger_import import main
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        d = self._artifact_dir(tmp_path)
        main(["--ledger", str(ledger), "--from-artifacts", str(d)])
        df = load_ledger(ledger)
        assert df.iloc[0]["run_id"] == "paper_submit_BT000035"

    def test_no_broker_import(self, tmp_path):
        """CLI must not import or instantiate AlpacaBrokerAdapter."""
        from src.tools.paper_ledger_import import main
        ledger = tmp_path / "ledger.csv"
        d = self._artifact_dir(tmp_path)
        with patch(
            "src.execution.alpaca_broker.AlpacaBrokerAdapter",
            side_effect=AssertionError("broker must not be instantiated"),
        ):
            main(["--ledger", str(ledger), "--from-artifacts", str(d)])
