"""
tests/test_live_pre_submit_ledger_dry_run.py
----------------------------------------------
Tests for src/tools/live_pre_submit_ledger_dry_run.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no real broker calls.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_OUTPUT_FIELDS = {
    "checked_at_utc",
    "result",
    "ledger_written",
    "ledger_path",
    "client_order_id",
    "symbol",
    "side",
    "notional",
    "violations",
}

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


def _go_gate(**overrides) -> dict:
    d = {"decision": "GO", "checked_at_utc": "2026-05-21T10:00:00+00:00"}
    d.update(overrides)
    return d


def _write_gate(tmp_path: Path, data: dict | None = None) -> Path:
    p = tmp_path / "gate.json"
    p.write_text(json.dumps(data or _go_gate()), encoding="utf-8")
    return p


def _read_ledger(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _run(
    tmp_path: Path,
    *,
    gate_path: Path | None = None,
    symbol: str = "SPY",
    side: str = "buy",
    notional: float = 100.0,
    client_order_id: str = "LIVE-TEST-000001",
    ledger_path: Path | None = None,
    gate_data: dict | None = None,
) -> dict:
    from src.tools.live_pre_submit_ledger_dry_run import run_dry_run
    if gate_path is None:
        gate_path = _write_gate(tmp_path, gate_data)
    if ledger_path is None:
        ledger_path = tmp_path / "ledger.csv"
    return run_dry_run(
        gate_path=gate_path,
        symbol=symbol,
        side=side,
        notional=notional,
        client_order_id=client_order_id,
        ledger_path=ledger_path,
    )


def _run_main(
    tmp_path: Path,
    *,
    gate_path: Path | None = None,
    symbol: str = "SPY",
    side: str = "buy",
    notional: float = 100.0,
    client_order_id: str = "LIVE-TEST-000001",
    ledger_path: Path | None = None,
    gate_data: dict | None = None,
    output_name: str = "out.json",
) -> tuple[int | None, Path, Path]:
    """Returns (exit_code, output_path, ledger_path)."""
    from src.tools.live_pre_submit_ledger_dry_run import main
    if gate_path is None:
        gate_path = _write_gate(tmp_path, gate_data)
    if ledger_path is None:
        ledger_path = tmp_path / "ledger.csv"
    out = tmp_path / output_name
    argv = [
        "--enablement-gate", str(gate_path),
        "--symbol", symbol,
        "--side", side,
        "--notional", str(notional),
        "--client-order-id", client_order_id,
        "--ledger", str(ledger_path),
        "--output", str(out),
    ]
    try:
        main(argv)
        return None, out, ledger_path
    except SystemExit as exc:
        return exc.code, out, ledger_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_go_gate_valid_order_writes_ledger_row(self, tmp_path):
        result = _run(tmp_path)
        assert result["result"] == "LEDGER_DRY_RUN_WRITTEN"

    def test_go_gate_valid_order_ledger_written_true(self, tmp_path):
        result = _run(tmp_path)
        assert result["ledger_written"] is True

    def test_go_gate_valid_order_no_violations(self, tmp_path):
        result = _run(tmp_path)
        assert result["violations"] == []

    def test_go_gate_ledger_file_created(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        assert ledger.exists()

    def test_go_gate_exactly_one_row_written(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        rows = _read_ledger(ledger)
        assert len(rows) == 1

    def test_ledger_row_status_is_attempting(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["status"] == "attempting"

    def test_ledger_row_broker_order_id_blank(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["broker_order_id"] == ""

    def test_ledger_row_error_blank(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["error"] == ""

    def test_ledger_row_correct_symbol(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, symbol="SPY", ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["symbol"] == "SPY"

    def test_ledger_row_correct_side(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, side="buy", ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["side"] == "buy"

    def test_ledger_row_correct_notional(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, notional=75.5, ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert float(row["notional"]) == pytest.approx(75.5)

    def test_ledger_row_correct_client_order_id(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-TEST-XYZ", ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["client_order_id"] == "LIVE-TEST-XYZ"

    def test_ledger_row_has_timestamp(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["timestamp_utc"]

    def test_ledger_row_has_source_enablement_gate(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        row = _read_ledger(ledger)[0]
        assert row["source_enablement_gate"]

    def test_main_exits_0_on_success(self, tmp_path):
        code, _, _ = _run_main(tmp_path)
        assert code is None or code == 0

    def test_main_writes_output_json(self, tmp_path):
        _, out, _ = _run_main(tmp_path)
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["result"] == "LEDGER_DRY_RUN_WRITTEN"


# ---------------------------------------------------------------------------
# Ledger CSV schema
# ---------------------------------------------------------------------------

class TestLedgerSchema:
    def test_ledger_has_exact_columns(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, ledger_path=ledger)
        with ledger.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            assert list(reader.fieldnames) == _LEDGER_COLUMNS

    def test_ledger_columns_match_constant(self, tmp_path):
        from src.tools.live_pre_submit_ledger_dry_run import LEDGER_COLUMNS
        assert LEDGER_COLUMNS == _LEDGER_COLUMNS


# ---------------------------------------------------------------------------
# Gate failures
# ---------------------------------------------------------------------------

class TestGateFailures:
    def test_no_go_gate_blocks(self, tmp_path):
        result = _run(tmp_path, gate_data={"decision": "NO_GO"})
        assert result["result"] == "BLOCKED"

    def test_no_go_gate_ledger_written_false(self, tmp_path):
        result = _run(tmp_path, gate_data={"decision": "NO_GO"})
        assert result["ledger_written"] is False

    def test_no_go_gate_no_ledger_file(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, gate_data={"decision": "NO_GO"}, ledger_path=ledger)
        assert not ledger.exists()

    def test_no_go_gate_violation_listed(self, tmp_path):
        result = _run(tmp_path, gate_data={"decision": "NO_GO"})
        assert any("decision" in v for v in result["violations"])

    def test_missing_gate_file_blocks(self, tmp_path):
        missing = tmp_path / "no_gate.json"
        result = _run(tmp_path, gate_path=missing)
        assert result["result"] == "BLOCKED"

    def test_missing_gate_file_ledger_not_written(self, tmp_path):
        missing = tmp_path / "no_gate.json"
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, gate_path=missing, ledger_path=ledger)
        assert not ledger.exists()

    def test_missing_gate_file_writes_output_json_via_main(self, tmp_path):
        missing = tmp_path / "no_gate.json"
        code, out, _ = _run_main(tmp_path, gate_path=missing)
        assert code == 1
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["result"] == "BLOCKED"

    def test_malformed_gate_file_blocks(self, tmp_path):
        bad = tmp_path / "bad_gate.json"
        bad.write_text("not-json", encoding="utf-8")
        result = _run(tmp_path, gate_path=bad)
        assert result["result"] == "BLOCKED"

    def test_malformed_gate_file_writes_output_via_main(self, tmp_path):
        bad = tmp_path / "bad_gate.json"
        bad.write_text("{not json", encoding="utf-8")
        code, out, _ = _run_main(tmp_path, gate_path=bad)
        assert code == 1
        assert out.exists()

    def test_gate_missing_decision_field_blocks(self, tmp_path):
        result = _run(tmp_path, gate_data={"checked_at_utc": "2026-05-21"})
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Order validation failures
# ---------------------------------------------------------------------------

class TestOrderValidation:
    def test_empty_symbol_blocks(self, tmp_path):
        result = _run(tmp_path, symbol="")
        assert result["result"] == "BLOCKED"

    def test_empty_symbol_no_ledger_row(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, symbol="", ledger_path=ledger)
        assert not ledger.exists()

    def test_empty_symbol_violation_listed(self, tmp_path):
        result = _run(tmp_path, symbol="")
        assert any("symbol" in v for v in result["violations"])

    def test_side_sell_blocks(self, tmp_path):
        result = _run(tmp_path, side="sell")
        assert result["result"] == "BLOCKED"

    def test_side_sell_violation_listed(self, tmp_path):
        result = _run(tmp_path, side="sell")
        assert any("side" in v for v in result["violations"])

    def test_side_sell_no_ledger_row(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, side="sell", ledger_path=ledger)
        assert not ledger.exists()

    def test_notional_zero_blocks(self, tmp_path):
        result = _run(tmp_path, notional=0.0)
        assert result["result"] == "BLOCKED"

    def test_notional_negative_blocks(self, tmp_path):
        result = _run(tmp_path, notional=-10.0)
        assert result["result"] == "BLOCKED"

    def test_notional_zero_violation_listed(self, tmp_path):
        result = _run(tmp_path, notional=0.0)
        assert any("notional" in v for v in result["violations"])

    def test_empty_client_order_id_blocks(self, tmp_path):
        result = _run(tmp_path, client_order_id="")
        assert result["result"] == "BLOCKED"

    def test_empty_client_order_id_violation_listed(self, tmp_path):
        result = _run(tmp_path, client_order_id="")
        assert any("client_order_id" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_duplicate_client_order_id_blocks(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        result = _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_duplicate_client_order_id_no_second_row(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        rows = _read_ledger(ledger)
        assert len(rows) == 1

    def test_duplicate_client_order_id_violation_listed(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        result = _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        assert any("already present" in v for v in result["violations"])

    def test_existing_ledger_different_id_appends(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        result = _run(tmp_path, client_order_id="LIVE-002", ledger_path=ledger)
        assert result["result"] == "LEDGER_DRY_RUN_WRITTEN"

    def test_existing_ledger_different_id_two_rows(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        _run(tmp_path, client_order_id="LIVE-002", ledger_path=ledger)
        rows = _read_ledger(ledger)
        assert len(rows) == 2

    def test_existing_ledger_different_id_both_attempting(self, tmp_path):
        ledger = tmp_path / "ledger.csv"
        _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        _run(tmp_path, client_order_id="LIVE-002", ledger_path=ledger)
        rows = _read_ledger(ledger)
        assert all(r["status"] == "attempting" for r in rows)



# ---------------------------------------------------------------------------
# Ledger schema validation hardening
# ---------------------------------------------------------------------------

def _write_ledger_with_header(path: Path, columns: list[str],
                               rows: list[dict] | None = None) -> Path:
    """Write a CSV ledger with the given header and optional rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in (rows or []):
            writer.writerow({c: row.get(c, "") for c in columns})
    return path


class TestLedgerSchemaValidation:
    def test_missing_client_order_id_column_blocks(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "client_order_id"]
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", bad_cols)
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_missing_client_order_id_column_violation_listed(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "client_order_id"]
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", bad_cols)
        result = _run(tmp_path, ledger_path=ledger)
        assert any("ledger" in v for v in result["violations"])

    def test_wrong_column_order_blocks(self, tmp_path):
        shuffled = list(reversed(_LEDGER_COLUMNS))
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", shuffled)
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_wrong_column_order_violation_listed(self, tmp_path):
        shuffled = list(reversed(_LEDGER_COLUMNS))
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", shuffled)
        result = _run(tmp_path, ledger_path=ledger)
        assert any("ledger" in v for v in result["violations"])

    def test_extra_column_blocks(self, tmp_path):
        extra_cols = _LEDGER_COLUMNS + ["unexpected_column"]
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", extra_cols)
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_extra_column_violation_listed(self, tmp_path):
        extra_cols = _LEDGER_COLUMNS + ["unexpected_column"]
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", extra_cols)
        result = _run(tmp_path, ledger_path=ledger)
        assert any("ledger" in v for v in result["violations"])

    def test_schema_mismatch_does_not_append_row(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "status"]
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", bad_cols)
        row_count_before = sum(1 for _ in ledger.open(encoding="utf-8")) - 1
        _run(tmp_path, ledger_path=ledger)
        row_count_after = sum(1 for _ in ledger.open(encoding="utf-8")) - 1
        assert row_count_after == row_count_before

    def test_schema_mismatch_writes_output_json_via_main(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "broker_order_id"]
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", bad_cols)
        code, out, _ = _run_main(tmp_path, ledger_path=ledger)
        assert code == 1
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["result"] == "BLOCKED"

    def test_valid_schema_allows_append_with_different_id(self, tmp_path):
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", _LEDGER_COLUMNS,
                                            rows=[{
                                                "client_order_id": "LIVE-001",
                                                "status": "attempting",
                                            }])
        result = _run(tmp_path, client_order_id="LIVE-002", ledger_path=ledger)
        assert result["result"] == "LEDGER_DRY_RUN_WRITTEN"

    def test_valid_schema_duplicate_id_still_blocks(self, tmp_path):
        ledger = _write_ledger_with_header(tmp_path / "ledger.csv", _LEDGER_COLUMNS,
                                            rows=[{
                                                "client_order_id": "LIVE-001",
                                                "status": "attempting",
                                            }])
        result = _run(tmp_path, client_order_id="LIVE-001", ledger_path=ledger)
        assert result["result"] == "BLOCKED"
        assert any("already present" in v for v in result["violations"])


# ---------------------------------------------------------------------------
# Output JSON structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_success_has_all_required_fields(self, tmp_path):
        result = _run(tmp_path)
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_blocked_has_all_required_fields(self, tmp_path):
        result = _run(tmp_path, gate_data={"decision": "NO_GO"})
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_result_values_are_expected(self, tmp_path):
        assert _run(tmp_path)["result"] == "LEDGER_DRY_RUN_WRITTEN"
        assert _run(tmp_path, side="sell")["result"] == "BLOCKED"

    def test_violations_is_list(self, tmp_path):
        result = _run(tmp_path)
        assert isinstance(result["violations"], list)

    def test_ledger_written_is_bool(self, tmp_path):
        result = _run(tmp_path)
        assert isinstance(result["ledger_written"], bool)

    def test_checked_at_utc_is_string(self, tmp_path):
        result = _run(tmp_path)
        assert isinstance(result["checked_at_utc"], str) and result["checked_at_utc"]

    def test_written_output_json_has_all_fields(self, tmp_path):
        _, out, _ = _run_main(tmp_path)
        data = json.loads(out.read_text(encoding="utf-8"))
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in data, f"Missing field in written JSON: {field}"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_exit_0_on_ledger_written(self, tmp_path):
        code, _, _ = _run_main(tmp_path)
        assert code is None or code == 0

    def test_exit_1_on_no_go_gate(self, tmp_path):
        code, out, _ = _run_main(tmp_path, gate_data={"decision": "NO_GO"})
        assert code == 1

    def test_exit_1_on_side_sell(self, tmp_path):
        code, _, _ = _run_main(tmp_path, side="sell")
        assert code == 1

    def test_exit_1_on_missing_gate(self, tmp_path):
        missing = tmp_path / "missing.json"
        code, _, _ = _run_main(tmp_path, gate_path=missing)
        assert code == 1

    def test_exit_1_always_writes_output(self, tmp_path):
        code, out, _ = _run_main(tmp_path, gate_data={"decision": "NO_GO"})
        assert code == 1
        assert out.exists()


# ---------------------------------------------------------------------------
# Security / safety properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def _source(self) -> str:
        return (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_pre_submit_ledger_dry_run.py"
        ).read_text(encoding="utf-8")

    def test_no_alpaca_import(self):
        source = self._source()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("*"):
                continue
            assert "import alpaca" not in stripped.lower(), (
                f"Must not import Alpaca (line: {line!r})"
            )
            assert "from alpaca" not in stripped.lower(), (
                f"Must not import Alpaca (line: {line!r})"
            )

    def test_no_submit_order_call(self):
        assert "submit_order(" not in self._source()

    def test_no_cancel_order_call(self):
        assert "cancel_order(" not in self._source()

    def test_no_credentials_read(self):
        source = self._source()
        for keyword in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY", "os.environ"):
            assert keyword not in source, (
                f"Must not read credentials ({keyword!r})"
            )

    def test_no_real_broker_calls(self):
        source = self._source()
        for pattern in ("TradingClient", "submit_order(", "cancel_order(", "get_all_orders"):
            assert pattern not in source, (
                f"Must not make broker calls ({pattern!r})"
            )

    def test_module_imports_without_alpaca(self):
        import importlib
        mod = importlib.import_module("src.tools.live_pre_submit_ledger_dry_run")
        assert mod is not None

    def test_run_dry_run_does_not_touch_submit_order(self, tmp_path):
        import inspect
        from src.tools.live_pre_submit_ledger_dry_run import run_dry_run
        assert "submit_order" not in inspect.getsource(run_dry_run)
