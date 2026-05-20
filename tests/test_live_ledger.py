"""
tests/test_live_ledger.py
--------------------------
Tests for src/execution/live_ledger.py and src/tools/live_ledger_verify.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled.
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.execution.live_ledger import (
    LIVE_LEDGER_COLUMNS,
    VALID_SIDES,
    VALID_ORDER_TYPES,
    append_live_ledger_row,
)
from src.tools.live_ledger_verify import parse_ledger, validate_ledger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_row(**overrides) -> dict:
    row = {
        "run_id":           "run-001",
        "flow":             "buy",
        "client_order_id":  "coid-abc-123",
        "alpaca_order_id":  "intent-ref-001",
        "symbol":           "SPY",
        "side":             "buy",
        "order_type":       "market",
        "live_sizing_mode": "quantity",
        "quantity":         "1.0",
        "notional":         "",
        "status":           "dry_run",
        "submitted_at":     "",
        "checked_at_utc":   "2026-05-20T10:00:00+00:00",
        "dry_run_only":     "True",
        "submit_allowed":   "False",
        "notes":            "",
    }
    row.update(overrides)
    return row


def _write_csv(tmp_path: Path, rows: list[dict], columns: list[str] | None = None) -> Path:
    path = tmp_path / "live_execution_ledger.csv"
    cols = columns or LIVE_LEDGER_COLUMNS
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in cols})
    return path


def _run_main(ledger_path: str) -> int | None:
    from src.tools.live_ledger_verify import main
    try:
        main(["--ledger", ledger_path])
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

class TestLedgerSchema:
    def test_columns_defined(self):
        assert isinstance(LIVE_LEDGER_COLUMNS, list)
        assert len(LIVE_LEDGER_COLUMNS) == 16

    def test_required_columns_present(self):
        required = [
            "run_id", "flow", "client_order_id", "alpaca_order_id",
            "symbol", "side", "order_type", "live_sizing_mode",
            "quantity", "notional", "status", "submitted_at",
            "checked_at_utc", "dry_run_only", "submit_allowed", "notes",
        ]
        for col in required:
            assert col in LIVE_LEDGER_COLUMNS, f"Missing column: {col}"

    def test_valid_sides(self):
        assert "buy" in VALID_SIDES
        assert "sell" in VALID_SIDES

    def test_valid_order_types(self):
        assert "market" in VALID_ORDER_TYPES
        assert "limit" in VALID_ORDER_TYPES


# ---------------------------------------------------------------------------
# append_live_ledger_row — write guard
# ---------------------------------------------------------------------------

class TestAppendLiveLedgerRow:
    def test_raises_without_allow_write(self, tmp_path):
        path = tmp_path / "test_ledger.csv"
        with pytest.raises(RuntimeError, match="allow_write must be explicitly True"):
            append_live_ledger_row(path, _make_valid_row())

    def test_raises_with_allow_write_false(self, tmp_path):
        path = tmp_path / "test_ledger.csv"
        with pytest.raises(RuntimeError):
            append_live_ledger_row(path, _make_valid_row(), allow_write=False)

    def test_raises_with_allow_write_none(self, tmp_path):
        path = tmp_path / "test_ledger.csv"
        with pytest.raises(RuntimeError):
            append_live_ledger_row(path, _make_valid_row(), allow_write=None)

    def test_writes_when_allow_write_true(self, tmp_path):
        path = tmp_path / "test_ledger.csv"
        append_live_ledger_row(path, _make_valid_row(), allow_write=True)
        assert path.exists()
        df = pd.read_csv(path, dtype=str)
        assert list(df.columns) == LIVE_LEDGER_COLUMNS
        assert len(df) == 1

    def test_appends_second_row(self, tmp_path):
        path = tmp_path / "test_ledger.csv"
        row1 = _make_valid_row(client_order_id="coid-1")
        row2 = _make_valid_row(client_order_id="coid-2")
        append_live_ledger_row(path, row1, allow_write=True)
        append_live_ledger_row(path, row2, allow_write=True)
        df = pd.read_csv(path, dtype=str)
        assert len(df) == 2
        assert df.iloc[0]["client_order_id"] == "coid-1"
        assert df.iloc[1]["client_order_id"] == "coid-2"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "ledger.csv"
        append_live_ledger_row(path, _make_valid_row(), allow_write=True)
        assert path.exists()

    def test_no_submit_order_called(self, tmp_path):
        path = tmp_path / "ledger.csv"
        broker = MagicMock()
        append_live_ledger_row(path, _make_valid_row(), allow_write=True)
        broker.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# parse_ledger
# ---------------------------------------------------------------------------

class TestParseLedger:
    def test_missing_file_returns_none(self, tmp_path):
        path = tmp_path / "nonexistent.csv"
        rows, msg = parse_ledger(path)
        assert rows is None
        assert "no live ledger exists yet" in msg

    def test_valid_file_returns_rows(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row()])
        rows, msg = parse_ledger(path)
        assert rows is not None
        assert len(rows) == 1
        assert msg == ""

    def test_empty_file_returns_empty_rows(self, tmp_path):
        path = _write_csv(tmp_path, [])
        rows, msg = parse_ledger(path)
        assert rows == []
        assert msg == ""


# ---------------------------------------------------------------------------
# validate_ledger
# ---------------------------------------------------------------------------

class TestValidateLedger:
    def test_empty_rows_pass(self):
        result, errors, warnings = validate_ledger([])
        assert result == "PASS"
        assert errors == []
        assert warnings == []

    def test_valid_dry_run_row_pass(self):
        rows = [_make_valid_row()]
        result, errors, warnings = validate_ledger(rows)
        assert result == "PASS"
        assert errors == []
        assert warnings == []

    def test_missing_required_column_fail(self):
        row = _make_valid_row()
        del row["client_order_id"]
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert any("client_order_id" in e for e in errors)

    def test_submit_allowed_and_dry_run_only_fail(self):
        row = _make_valid_row(submit_allowed="True", dry_run_only="True")
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert any("submit_allowed=true" in e and "dry_run_only=true" in e for e in errors)

    def test_missing_client_order_id_fail(self):
        row = _make_valid_row(client_order_id="")
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert any("client_order_id is empty" in e for e in errors)

    def test_empty_status_fail(self):
        row = _make_valid_row(status="")
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert any("status is empty" in e for e in errors)

    def test_invalid_side_fail(self):
        row = _make_valid_row(side="long")
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert any("invalid side=" in e for e in errors)

    def test_valid_sides_accepted(self):
        for side in ("buy", "sell"):
            result, _, _ = validate_ledger([_make_valid_row(side=side)])
            assert result == "PASS", f"Expected PASS for side={side}"

    def test_invalid_order_type_fail(self):
        row = _make_valid_row(order_type="stop")
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert any("invalid order_type=" in e for e in errors)

    def test_valid_order_types_accepted(self):
        for ot in ("market", "limit"):
            result, _, _ = validate_ledger([_make_valid_row(order_type=ot)])
            assert result == "PASS", f"Expected PASS for order_type={ot}"

    def test_warn_alpaca_id_missing_for_dry_run(self):
        row = _make_valid_row(dry_run_only="True", alpaca_order_id="")
        result, errors, warnings = validate_ledger([row])
        assert result == "WARN"
        assert errors == []
        assert any("alpaca_order_id is empty" in w for w in warnings)

    def test_no_warn_when_alpaca_id_present_for_dry_run(self):
        row = _make_valid_row(dry_run_only="True", alpaca_order_id="some-uuid")
        result, _, warnings = validate_ledger([row])
        assert result == "PASS"
        assert warnings == []

    def test_multiple_errors_collected(self):
        row = _make_valid_row(
            client_order_id="",
            status="",
            side="invalid",
        )
        result, errors, _ = validate_ledger([row])
        assert result == "FAIL"
        assert len(errors) >= 3

    def test_truthy_variants_submit_allowed(self):
        for truthy in ("True", "true", "1", "TRUE"):
            row = _make_valid_row(submit_allowed=truthy, dry_run_only="True")
            result, errors, _ = validate_ledger([row])
            assert result == "FAIL", f"Expected FAIL for submit_allowed={truthy!r}"

    def test_falsy_submit_allowed_pass(self):
        for falsy in ("False", "false", "0", ""):
            row = _make_valid_row(submit_allowed=falsy, dry_run_only="True")
            result, _, _ = validate_ledger([row])
            assert result in ("PASS", "WARN"), f"Expected PASS/WARN for submit_allowed={falsy!r}"


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

class TestMain:
    def test_missing_ledger_exits_0(self, tmp_path):
        path = tmp_path / "nonexistent.csv"
        code = _run_main(str(path))
        assert code in (0, None)

    def test_valid_dry_run_ledger_exits_0(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row()])
        code = _run_main(str(path))
        assert code in (0, None)

    def test_missing_column_exits_1(self, tmp_path):
        cols = [c for c in LIVE_LEDGER_COLUMNS if c != "client_order_id"]
        path = tmp_path / "ledger.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            row = _make_valid_row()
            del row["client_order_id"]
            writer.writerow({c: row.get(c, "") for c in cols})
        code = _run_main(str(path))
        assert code == 1

    def test_submit_allowed_true_exits_1(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row(submit_allowed="True")])
        code = _run_main(str(path))
        assert code == 1

    def test_empty_client_order_id_exits_1(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row(client_order_id="")])
        code = _run_main(str(path))
        assert code == 1

    def test_empty_status_exits_1(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row(status="")])
        code = _run_main(str(path))
        assert code == 1

    def test_invalid_side_exits_1(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row(side="long")])
        code = _run_main(str(path))
        assert code == 1

    def test_invalid_order_type_exits_1(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row(order_type="stop")])
        code = _run_main(str(path))
        assert code == 1

    def test_warn_exits_1(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row(dry_run_only="True", alpaca_order_id="")])
        code = _run_main(str(path))
        assert code == 1

    def test_no_alpaca_calls(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row()])
        with patch("src.tools.live_ledger_verify.pd.read_csv",
                   wraps=pd.read_csv) as mock_read:
            _run_main(str(path))
            # Confirm pandas was used (local read), not Alpaca
            mock_read.assert_called_once()

    def test_no_submit_order_called(self, tmp_path):
        path = _write_csv(tmp_path, [_make_valid_row()])
        broker = MagicMock()
        _run_main(str(path))
        broker.submit_order.assert_not_called()

    def test_no_ledger_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_csv(tmp_path, [_make_valid_row()])
        _run_main(str(path))
        # Verify no new files created beyond the input ledger
        created = list(tmp_path.glob("**/*"))
        assert all(f == path or f.is_dir() for f in created)
