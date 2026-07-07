"""
tests/test_live_ledger_verify.py
----------------------------------
Tests for the dry-run submit ledger validation path in
src/tools/live_ledger_verify.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no real broker calls.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_LEDGER_COLUMNS = [
    "timestamp_utc",
    "client_order_id",
    "symbol",
    "side",
    "notional",
    "status",
    "source_enablement_gate",
    "broker_order_id",
    "error",
]

_REQUIRED_OUTPUT_FIELDS = {
    "checked_at_utc",
    "result",
    "ledger_path",
    "row_count",
    "attempting_count",
    "submitted_count",
    "rejected_count",
    "exception_count",
    "duplicate_client_order_ids",
    "violations",
}


def _base_row(**overrides) -> dict:
    d = {
        "timestamp_utc":          "2026-05-21T10:00:00+00:00",
        "client_order_id":        "LIVE-TEST-000001",
        "symbol":                 "SPY",
        "side":                   "buy",
        "notional":               "100.0",
        "status":                 "submitted",
        "source_enablement_gate": "/output/gate.json",
        "broker_order_id":        "ALPACA-ORDER-123",
        "error":                  "",
    }
    d.update(overrides)
    return d


def _write_ledger(path: Path, rows: list[dict], columns: list[str] | None = None) -> Path:
    cols = columns or _LEDGER_COLUMNS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in cols})
    return path


def _ledger(tmp_path: Path, *rows: dict, columns: list[str] | None = None) -> Path:
    return _write_ledger(tmp_path / "ledger.csv", list(rows), columns)


def _run(
    tmp_path: Path,
    *,
    ledger_path: Path | None = None,
    allow_attempting: bool = False,
) -> dict:
    from src.tools.live_ledger_verify import run_verify
    if ledger_path is None:
        ledger_path = tmp_path / "ledger.csv"
    return run_verify(ledger_path, allow_attempting=allow_attempting)


def _run_main(
    tmp_path: Path,
    *,
    ledger_path: Path | None = None,
    allow_attempting: bool = False,
    output_name: str = "out.json",
) -> tuple[int | None, Path]:
    from src.tools.live_ledger_verify import main
    if ledger_path is None:
        ledger_path = tmp_path / "ledger.csv"
    out = tmp_path / output_name
    argv = ["--ledger", str(ledger_path), "--output", str(out)]
    if allow_attempting:
        argv.append("--allow-attempting")
    try:
        main(argv)
        return None, out
    except SystemExit as exc:
        return exc.code, out


# ---------------------------------------------------------------------------
# Happy-path: valid ledgers
# ---------------------------------------------------------------------------

class TestValidLedgers:
    def test_submitted_ledger_passes(self, tmp_path):
        _ledger(tmp_path, _base_row(status="submitted", broker_order_id="B-1", error=""))
        result = _run(tmp_path)
        assert result["result"] == "PASS"

    def test_rejected_ledger_passes(self, tmp_path):
        _ledger(tmp_path, _base_row(status="rejected", broker_order_id="", error="rejected by broker"))
        result = _run(tmp_path)
        assert result["result"] == "PASS"

    def test_exception_ledger_passes(self, tmp_path):
        _ledger(tmp_path, _base_row(status="exception", broker_order_id="", error="timeout"))
        result = _run(tmp_path)
        assert result["result"] == "PASS"

    def test_attempting_fails_by_default(self, tmp_path):
        _ledger(tmp_path, _base_row(status="attempting", broker_order_id="", error=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_attempting_passes_with_allow_flag(self, tmp_path):
        _ledger(tmp_path, _base_row(status="attempting", broker_order_id="", error=""))
        result = _run(tmp_path, allow_attempting=True)
        assert result["result"] == "PASS"

    def test_attempting_violation_message(self, tmp_path):
        _ledger(tmp_path, _base_row(status="attempting", broker_order_id="", error=""))
        result = _run(tmp_path)
        assert any("attempting" in v for v in result["violations"])

    def test_no_violations_on_valid_submitted(self, tmp_path):
        _ledger(tmp_path, _base_row())
        result = _run(tmp_path)
        assert result["violations"] == []


# ---------------------------------------------------------------------------
# Missing / malformed ledger
# ---------------------------------------------------------------------------

class TestLedgerRead:
    def test_missing_ledger_fails(self, tmp_path):
        result = _run(tmp_path, ledger_path=tmp_path / "nofile.csv")
        assert result["result"] == "FAIL"

    def test_missing_ledger_violation_listed(self, tmp_path):
        result = _run(tmp_path, ledger_path=tmp_path / "nofile.csv")
        assert any("ledger" in v for v in result["violations"])

    def test_missing_ledger_writes_output_via_main(self, tmp_path):
        code, out = _run_main(tmp_path, ledger_path=tmp_path / "nofile.csv")
        assert code == 1
        assert out.exists()
        assert json.loads(out.read_text(encoding="utf-8"))["result"] == "FAIL"

    def test_schema_mismatch_fails(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "broker_order_id"]
        _ledger(tmp_path, _base_row(), columns=bad_cols)
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_schema_mismatch_writes_output_via_main(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "error"]
        _ledger(tmp_path, _base_row(), columns=bad_cols)
        code, out = _run_main(tmp_path)
        assert code == 1
        assert out.exists()

    def test_schema_mismatch_violation_listed(self, tmp_path):
        bad_cols = _LEDGER_COLUMNS + ["extra"]
        _ledger(tmp_path, _base_row(), columns=bad_cols)
        result = _run(tmp_path)
        assert any("schema" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# client_order_id rules
# ---------------------------------------------------------------------------

class TestClientOrderId:
    def test_empty_client_order_id_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(client_order_id=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_empty_client_order_id_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(client_order_id=""))
        result = _run(tmp_path)
        assert any("client_order_id" in v for v in result["violations"])

    def test_duplicate_client_order_id_fails(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="LIVE-001"),
                _base_row(client_order_id="LIVE-001"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_duplicate_client_order_id_violation(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="LIVE-001"),
                _base_row(client_order_id="LIVE-001"))
        result = _run(tmp_path)
        assert any("duplicate" in v for v in result["violations"])

    def test_duplicate_ids_in_output(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="LIVE-001"),
                _base_row(client_order_id="LIVE-001"))
        result = _run(tmp_path)
        assert "LIVE-001" in result["duplicate_client_order_ids"]

    def test_unique_ids_no_duplicates_in_output(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="LIVE-001"),
                _base_row(client_order_id="LIVE-002", status="rejected",
                           broker_order_id="", error="err"))
        result = _run(tmp_path)
        assert result["duplicate_client_order_ids"] == []


# ---------------------------------------------------------------------------
# Status rules
# ---------------------------------------------------------------------------

class TestStatusRules:
    def test_invalid_status_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(status="pending"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_invalid_status_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(status="pending"))
        result = _run(tmp_path)
        assert any("status" in v for v in result["violations"])

    def test_submitted_without_broker_order_id_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(status="submitted", broker_order_id=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_submitted_without_broker_order_id_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(status="submitted", broker_order_id=""))
        result = _run(tmp_path)
        assert any("broker_order_id" in v for v in result["violations"])

    def test_submitted_with_error_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(status="submitted", broker_order_id="B-1", error="oops"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_submitted_with_error_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(status="submitted", broker_order_id="B-1", error="oops"))
        result = _run(tmp_path)
        assert any("error" in v for v in result["violations"])

    def test_rejected_without_error_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(status="rejected", broker_order_id="", error=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_rejected_without_error_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(status="rejected", broker_order_id="", error=""))
        result = _run(tmp_path)
        assert any("error" in v for v in result["violations"])

    def test_exception_without_error_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(status="exception", broker_order_id="", error=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_exception_without_error_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(status="exception", broker_order_id="", error=""))
        result = _run(tmp_path)
        assert any("error" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# Field-level rules
# ---------------------------------------------------------------------------

class TestFieldRules:
    def test_empty_source_enablement_gate_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(source_enablement_gate=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_empty_source_enablement_gate_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(source_enablement_gate=""))
        result = _run(tmp_path)
        assert any("source_enablement_gate" in v for v in result["violations"])

    def test_empty_symbol_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(symbol=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_empty_symbol_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(symbol=""))
        result = _run(tmp_path)
        assert any("symbol" in v for v in result["violations"])

    def test_side_not_buy_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(side="sell"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_side_not_buy_violation(self, tmp_path):
        _ledger(tmp_path, _base_row(side="sell"))
        result = _run(tmp_path)
        assert any("side" in v for v in result["violations"])

    def test_notional_missing_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(notional=""))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_notional_invalid_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(notional="abc"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_notional_zero_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(notional="0"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_notional_negative_fails(self, tmp_path):
        _ledger(tmp_path, _base_row(notional="-5.0"))
        result = _run(tmp_path)
        assert result["result"] == "FAIL"

    def test_notional_violation_listed(self, tmp_path):
        _ledger(tmp_path, _base_row(notional="abc"))
        result = _run(tmp_path)
        assert any("notional" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# Counts in output
# ---------------------------------------------------------------------------

class TestOutputCounts:
    def test_row_count_correct(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="L-1"),
                _base_row(client_order_id="L-2",
                           status="rejected", broker_order_id="", error="err"))
        result = _run(tmp_path)
        assert result["row_count"] == 2

    def test_submitted_count(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="L-1"),
                _base_row(client_order_id="L-2"))
        result = _run(tmp_path)
        assert result["submitted_count"] == 2

    def test_rejected_count(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="L-1",
                           status="rejected", broker_order_id="", error="err"))
        result = _run(tmp_path)
        assert result["rejected_count"] == 1

    def test_exception_count(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="L-1",
                           status="exception", broker_order_id="", error="timeout"))
        result = _run(tmp_path)
        assert result["exception_count"] == 1

    def test_attempting_count_with_flag(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="L-1",
                           status="attempting", broker_order_id="", error=""))
        result = _run(tmp_path, allow_attempting=True)
        assert result["attempting_count"] == 1

    def test_mixed_counts(self, tmp_path):
        _ledger(tmp_path,
                _base_row(client_order_id="L-1"),
                _base_row(client_order_id="L-2",
                           status="rejected", broker_order_id="", error="err"),
                _base_row(client_order_id="L-3",
                           status="exception", broker_order_id="", error="ex"))
        result = _run(tmp_path)
        assert result["submitted_count"] == 1
        assert result["rejected_count"] == 1
        assert result["exception_count"] == 1

    def test_zero_counts_on_failure(self, tmp_path):
        result = _run(tmp_path, ledger_path=tmp_path / "nofile.csv")
        assert result["row_count"] == 0
        assert result["submitted_count"] == 0


# ---------------------------------------------------------------------------
# Output JSON structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_pass_has_all_required_fields(self, tmp_path):
        _ledger(tmp_path, _base_row())
        result = _run(tmp_path)
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_fail_has_all_required_fields(self, tmp_path):
        result = _run(tmp_path, ledger_path=tmp_path / "nofile.csv")
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_violations_is_list(self, tmp_path):
        _ledger(tmp_path, _base_row())
        assert isinstance(_run(tmp_path)["violations"], list)

    def test_duplicate_client_order_ids_is_list(self, tmp_path):
        _ledger(tmp_path, _base_row())
        assert isinstance(_run(tmp_path)["duplicate_client_order_ids"], list)

    def test_written_json_has_all_fields(self, tmp_path):
        _ledger(tmp_path, _base_row())
        _, out = _run_main(tmp_path)
        data = json.loads(out.read_text(encoding="utf-8"))
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in data, f"Missing field in JSON: {field}"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_exit_0_on_pass(self, tmp_path):
        _ledger(tmp_path, _base_row())
        code, _ = _run_main(tmp_path)
        assert code is None or code == 0

    def test_exit_1_on_fail(self, tmp_path):
        code, _ = _run_main(tmp_path, ledger_path=tmp_path / "nofile.csv")
        assert code == 1

    def test_exit_1_on_attempting_without_flag(self, tmp_path):
        _ledger(tmp_path, _base_row(status="attempting", broker_order_id="", error=""))
        code, _ = _run_main(tmp_path)
        assert code == 1

    def test_exit_0_on_attempting_with_flag(self, tmp_path):
        _ledger(tmp_path, _base_row(status="attempting", broker_order_id="", error=""))
        code, _ = _run_main(tmp_path, allow_attempting=True)
        assert code is None or code == 0

    def test_always_writes_output_on_fail(self, tmp_path):
        code, out = _run_main(tmp_path, ledger_path=tmp_path / "nofile.csv")
        assert code == 1
        assert out.exists()

    def test_always_writes_output_on_pass(self, tmp_path):
        _ledger(tmp_path, _base_row())
        _, out = _run_main(tmp_path)
        assert out.exists()


# ---------------------------------------------------------------------------
# Security / safety properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def _source(self) -> str:
        return (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_ledger_verify.py"
        ).read_text(encoding="utf-8")

    def test_no_alpaca_import(self):
        for line in self._source().splitlines():
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("*"):
                continue
            assert "import alpaca" not in s.lower()
            assert "from alpaca" not in s.lower()

    def test_no_submit_order_call(self):
        assert "submit_order(" not in self._source()

    def test_no_cancel_order_call(self):
        assert "cancel_order(" not in self._source()

    def test_no_credentials_read(self):
        source = self._source()
        for kw in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY", "os.environ"):
            assert kw not in source

    def test_no_real_broker_calls(self):
        source = self._source()
        for pattern in ("TradingClient", "submit_order(", "cancel_order("):
            assert pattern not in source

    def test_module_imports_without_side_effects(self):
        import importlib
        mod = importlib.import_module("src.tools.live_ledger_verify")
        assert mod is not None

    def test_ledger_columns_imported_from_pre_submit(self):
        from src.tools.live_ledger_verify import LEDGER_COLUMNS
        from src.tools.live_pre_submit_ledger_dry_run import LEDGER_COLUMNS as PRE_COLS
        assert LEDGER_COLUMNS is PRE_COLS
