"""
tests/test_live_post_submit_ledger_update_dry_run.py
------------------------------------------------------
Tests for src/tools/live_post_submit_ledger_update_dry_run.py.

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
    "ledger_updated",
    "ledger_path",
    "client_order_id",
    "outcome",
    "broker_order_id",
    "error",
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

_CLIENT_ID = "LIVE-TEST-000001"
_BROKER_ID = "ALPACA-ORDER-123"
_ERROR_MSG = "order rejected by broker"


def _write_ledger(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_LEDGER_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in _LEDGER_COLUMNS})
    return path


def _attempting_row(**overrides) -> dict:
    d = {
        "timestamp_utc":          "2026-05-21T10:00:00+00:00",
        "client_order_id":        _CLIENT_ID,
        "symbol":                 "SPY",
        "side":                   "buy",
        "notional":               "100.0",
        "status":                 "attempting",
        "source_enablement_gate": "/output/gate.json",
        "broker_order_id":        "",
        "error":                  "",
    }
    d.update(overrides)
    return d


def _ledger_with_row(tmp_path: Path, **row_overrides) -> Path:
    return _write_ledger(tmp_path / "ledger.csv", [_attempting_row(**row_overrides)])


def _read_ledger(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _run(
    tmp_path: Path,
    *,
    ledger_path: Path | None = None,
    client_order_id: str = _CLIENT_ID,
    outcome: str = "submitted",
    broker_order_id: str = _BROKER_ID,
    error: str = "",
    setup_ledger: bool = True,
) -> dict:
    from src.tools.live_post_submit_ledger_update_dry_run import run_update
    if ledger_path is None and setup_ledger:
        ledger_path = _ledger_with_row(tmp_path)
    elif ledger_path is None:
        ledger_path = tmp_path / "ledger.csv"
    return run_update(
        ledger_path=ledger_path,
        client_order_id=client_order_id,
        outcome=outcome,
        broker_order_id=broker_order_id,
        error=error,
    )


def _run_main(
    tmp_path: Path,
    *,
    ledger_path: Path | None = None,
    client_order_id: str = _CLIENT_ID,
    outcome: str = "submitted",
    broker_order_id: str = _BROKER_ID,
    error: str = "",
    setup_ledger: bool = True,
    output_name: str = "out.json",
) -> tuple[int | None, Path, Path]:
    """Returns (exit_code, output_path, ledger_path)."""
    from src.tools.live_post_submit_ledger_update_dry_run import main
    if ledger_path is None and setup_ledger:
        ledger_path = _ledger_with_row(tmp_path)
    elif ledger_path is None:
        ledger_path = tmp_path / "ledger.csv"
    out = tmp_path / output_name
    argv = [
        "--ledger", str(ledger_path),
        "--client-order-id", client_order_id,
        "--outcome", outcome,
        "--broker-order-id", broker_order_id,
        "--error", error,
        "--output", str(out),
    ]
    try:
        main(argv)
        return None, out, ledger_path
    except SystemExit as exc:
        return exc.code, out, ledger_path


# ---------------------------------------------------------------------------
# Happy path: all three outcomes
# ---------------------------------------------------------------------------

class TestOutcomes:
    def test_submitted_updates_row(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert result["result"] == "LEDGER_DRY_RUN_UPDATED"

    def test_submitted_sets_status(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        assert _read_ledger(ledger)[0]["status"] == "submitted"

    def test_submitted_sets_broker_order_id(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        assert _read_ledger(ledger)[0]["broker_order_id"] == _BROKER_ID

    def test_submitted_clears_error(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        assert _read_ledger(ledger)[0]["error"] == ""

    def test_rejected_updates_row(self, tmp_path):
        result = _run(tmp_path, outcome="rejected", broker_order_id="", error=_ERROR_MSG)
        assert result["result"] == "LEDGER_DRY_RUN_UPDATED"

    def test_rejected_sets_status(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="rejected", broker_order_id="", error=_ERROR_MSG)
        assert _read_ledger(ledger)[0]["status"] == "rejected"

    def test_rejected_sets_error(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="rejected", broker_order_id="", error=_ERROR_MSG)
        assert _read_ledger(ledger)[0]["error"] == _ERROR_MSG

    def test_exception_updates_row(self, tmp_path):
        result = _run(tmp_path, outcome="exception", broker_order_id="", error="timeout")
        assert result["result"] == "LEDGER_DRY_RUN_UPDATED"

    def test_exception_sets_status(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="exception", broker_order_id="", error="timeout")
        assert _read_ledger(ledger)[0]["status"] == "exception"

    def test_exception_sets_error(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="exception", broker_order_id="", error="timeout")
        assert _read_ledger(ledger)[0]["error"] == "timeout"

    def test_update_result_is_updated(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert result["ledger_updated"] is True

    def test_update_no_violations(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert result["violations"] == []


# ---------------------------------------------------------------------------
# Row and schema preservation
# ---------------------------------------------------------------------------

class TestPreservation:
    def test_update_preserves_row_count_single(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        assert len(_read_ledger(ledger)) == 1

    def test_update_preserves_row_count_multiple(self, tmp_path):
        ledger = _write_ledger(tmp_path / "ledger.csv", [
            _attempting_row(client_order_id="LIVE-001"),
            _attempting_row(client_order_id="LIVE-002"),
        ])
        _run(tmp_path, ledger_path=ledger, client_order_id="LIVE-001",
             outcome="submitted", broker_order_id=_BROKER_ID)
        assert len(_read_ledger(ledger)) == 2

    def test_update_preserves_exact_columns(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        with ledger.open(encoding="utf-8", newline="") as fh:
            assert list(csv.DictReader(fh).fieldnames) == _LEDGER_COLUMNS

    def test_update_preserves_unrelated_row(self, tmp_path):
        ledger = _write_ledger(tmp_path / "ledger.csv", [
            _attempting_row(client_order_id="LIVE-001"),
            _attempting_row(client_order_id="LIVE-002"),
        ])
        _run(tmp_path, ledger_path=ledger, client_order_id="LIVE-001",
             outcome="submitted", broker_order_id=_BROKER_ID)
        rows = _read_ledger(ledger)
        other = next(r for r in rows if r["client_order_id"] == "LIVE-002")
        assert other["status"] == "attempting"
        assert other["broker_order_id"] == ""

    def test_update_preserves_other_fields_of_updated_row(self, tmp_path):
        ledger = _ledger_with_row(tmp_path, symbol="AAPL", notional="50.0")
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        row = _read_ledger(ledger)[0]
        assert row["symbol"] == "AAPL"
        assert row["notional"] == "50.0"

    def test_does_not_append_new_row(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id=_BROKER_ID)
        assert len(_read_ledger(ledger)) == 1


# ---------------------------------------------------------------------------
# Outcome-specific requirement failures
# ---------------------------------------------------------------------------

class TestOutcomeRequirements:
    def test_submitted_without_broker_order_id_blocks(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id="")
        assert result["result"] == "BLOCKED"

    def test_submitted_without_broker_order_id_violation(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id="")
        assert any("broker_order_id" in v for v in result["violations"])

    def test_submitted_without_broker_order_id_no_ledger_change(self, tmp_path):
        ledger = _ledger_with_row(tmp_path)
        _run(tmp_path, ledger_path=ledger, outcome="submitted", broker_order_id="")
        assert _read_ledger(ledger)[0]["status"] == "attempting"

    def test_rejected_without_error_blocks(self, tmp_path):
        result = _run(tmp_path, outcome="rejected", broker_order_id="", error="")
        assert result["result"] == "BLOCKED"

    def test_rejected_without_error_violation(self, tmp_path):
        result = _run(tmp_path, outcome="rejected", broker_order_id="", error="")
        assert any("error" in v for v in result["violations"])

    def test_exception_without_error_blocks(self, tmp_path):
        result = _run(tmp_path, outcome="exception", broker_order_id="", error="")
        assert result["result"] == "BLOCKED"

    def test_exception_without_error_violation(self, tmp_path):
        result = _run(tmp_path, outcome="exception", broker_order_id="", error="")
        assert any("error" in v for v in result["violations"])

    def test_invalid_outcome_blocks(self, tmp_path):
        # bypass argparse choices by calling run_update directly
        from src.tools.live_post_submit_ledger_update_dry_run import run_update
        ledger = _ledger_with_row(tmp_path)
        result = run_update(
            ledger_path=ledger,
            client_order_id=_CLIENT_ID,
            outcome="cancelled",
            broker_order_id="",
            error="",
        )
        assert result["result"] == "BLOCKED"

    def test_invalid_outcome_violation(self, tmp_path):
        from src.tools.live_post_submit_ledger_update_dry_run import run_update
        ledger = _ledger_with_row(tmp_path)
        result = run_update(
            ledger_path=ledger,
            client_order_id=_CLIENT_ID,
            outcome="cancelled",
            broker_order_id="",
            error="",
        )
        assert any("outcome" in v for v in result["violations"])

    def test_main_invalid_outcome_exits_1(self, tmp_path):
        """main() with an invalid --outcome must exit 1, not crash via argparse."""
        code, _, _ = _run_main(tmp_path, outcome="cancelled", broker_order_id="")
        assert code == 1

    def test_main_invalid_outcome_writes_output_json(self, tmp_path):
        _, out, _ = _run_main(tmp_path, outcome="cancelled", broker_order_id="")
        assert out.exists()

    def test_main_invalid_outcome_result_is_blocked(self, tmp_path):
        _, out, _ = _run_main(tmp_path, outcome="cancelled", broker_order_id="")
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"

    def test_main_invalid_outcome_violations_mention_outcome(self, tmp_path):
        _, out, _ = _run_main(tmp_path, outcome="cancelled", broker_order_id="")
        data = json.loads(out.read_text())
        assert any("outcome" in v for v in data["violations"])


# ---------------------------------------------------------------------------
# Ledger-level failures
# ---------------------------------------------------------------------------

class TestLedgerFailures:
    def test_missing_ledger_blocks(self, tmp_path):
        result = _run(tmp_path, setup_ledger=False)
        assert result["result"] == "BLOCKED"

    def test_missing_ledger_violation(self, tmp_path):
        result = _run(tmp_path, setup_ledger=False)
        assert any("ledger" in v for v in result["violations"])

    def test_missing_ledger_writes_output_json_via_main(self, tmp_path):
        code, out, _ = _run_main(tmp_path, setup_ledger=False)
        assert code == 1
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["result"] == "BLOCKED"

    def test_schema_mismatch_blocks(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "broker_order_id"]
        ledger = tmp_path / "ledger.csv"
        with ledger.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=bad_cols)
            w.writeheader()
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_schema_mismatch_writes_output_json_via_main(self, tmp_path):
        bad_cols = [c for c in _LEDGER_COLUMNS if c != "error"]
        ledger = tmp_path / "ledger.csv"
        with ledger.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=bad_cols)
            w.writeheader()
        code, out, _ = _run_main(tmp_path, ledger_path=ledger)
        assert code == 1
        assert out.exists()

    def test_no_matching_client_order_id_blocks(self, tmp_path):
        result = _run(tmp_path, client_order_id="DOES-NOT-EXIST")
        assert result["result"] == "BLOCKED"

    def test_no_matching_client_order_id_violation(self, tmp_path):
        result = _run(tmp_path, client_order_id="DOES-NOT-EXIST")
        assert any("not found" in v for v in result["violations"])

    def test_duplicate_client_order_id_blocks(self, tmp_path):
        ledger = _write_ledger(tmp_path / "ledger.csv", [
            _attempting_row(client_order_id=_CLIENT_ID),
            _attempting_row(client_order_id=_CLIENT_ID),
        ])
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_duplicate_client_order_id_violation(self, tmp_path):
        ledger = _write_ledger(tmp_path / "ledger.csv", [
            _attempting_row(client_order_id=_CLIENT_ID),
            _attempting_row(client_order_id=_CLIENT_ID),
        ])
        result = _run(tmp_path, ledger_path=ledger)
        assert any("2 rows" in v for v in result["violations"])

    def test_status_submitted_blocks(self, tmp_path):
        ledger = _ledger_with_row(tmp_path, status="submitted",
                                  broker_order_id=_BROKER_ID)
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_status_rejected_blocks(self, tmp_path):
        ledger = _ledger_with_row(tmp_path, status="rejected",
                                  error="some error")
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_status_exception_blocks(self, tmp_path):
        ledger = _ledger_with_row(tmp_path, status="exception",
                                  error="some error")
        result = _run(tmp_path, ledger_path=ledger)
        assert result["result"] == "BLOCKED"

    def test_non_attempting_status_violation(self, tmp_path):
        ledger = _ledger_with_row(tmp_path, status="submitted",
                                  broker_order_id=_BROKER_ID)
        result = _run(tmp_path, ledger_path=ledger)
        assert any("attempting" in v for v in result["violations"])

    def test_non_attempting_status_no_ledger_change(self, tmp_path):
        ledger = _ledger_with_row(tmp_path, status="submitted",
                                  broker_order_id=_BROKER_ID)
        _run(tmp_path, ledger_path=ledger)
        assert _read_ledger(ledger)[0]["status"] == "submitted"

    def test_empty_client_order_id_blocks(self, tmp_path):
        from src.tools.live_post_submit_ledger_update_dry_run import run_update
        ledger = _ledger_with_row(tmp_path)
        result = run_update(
            ledger_path=ledger,
            client_order_id="",
            outcome="submitted",
            broker_order_id=_BROKER_ID,
            error="",
        )
        assert result["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Output JSON structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_success_has_all_required_fields(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_blocked_has_all_required_fields(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id="")
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in result, f"Missing field: {field}"

    def test_violations_is_list(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert isinstance(result["violations"], list)

    def test_ledger_updated_is_bool(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert isinstance(result["ledger_updated"], bool)

    def test_checked_at_utc_is_string(self, tmp_path):
        result = _run(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert isinstance(result["checked_at_utc"], str) and result["checked_at_utc"]

    def test_written_json_has_all_fields(self, tmp_path):
        _, out, _ = _run_main(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        data = json.loads(out.read_text())
        for field in _REQUIRED_OUTPUT_FIELDS:
            assert field in data, f"Missing field in JSON: {field}"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    def test_exit_0_on_update(self, tmp_path):
        code, _, _ = _run_main(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert code is None or code == 0

    def test_exit_1_on_blocked_no_broker_id(self, tmp_path):
        code, _, _ = _run_main(tmp_path, outcome="submitted", broker_order_id="")
        assert code == 1

    def test_exit_1_on_missing_ledger(self, tmp_path):
        code, _, _ = _run_main(tmp_path, setup_ledger=False)
        assert code == 1

    def test_exit_1_always_writes_output(self, tmp_path):
        code, out, _ = _run_main(tmp_path, outcome="submitted", broker_order_id="")
        assert code == 1
        assert out.exists()

    def test_exit_0_writes_output(self, tmp_path):
        code, out, _ = _run_main(tmp_path, outcome="submitted", broker_order_id=_BROKER_ID)
        assert (code is None or code == 0) and out.exists()


# ---------------------------------------------------------------------------
# Security / safety properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def _source(self) -> str:
        return (
            Path(__file__).parent.parent
            / "src" / "tools" / "live_post_submit_ledger_update_dry_run.py"
        ).read_text(encoding="utf-8")

    def test_no_alpaca_import(self):
        for line in self._source().splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("*"):
                continue
            assert "import alpaca" not in stripped.lower()
            assert "from alpaca" not in stripped.lower()

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
        for pattern in ("TradingClient", "submit_order(", "cancel_order(", "get_all_orders"):
            assert pattern not in source

    def test_module_imports_without_alpaca(self):
        import importlib
        mod = importlib.import_module("src.tools.live_post_submit_ledger_update_dry_run")
        assert mod is not None

    def test_run_update_does_not_touch_submit_order(self, tmp_path):
        import inspect
        from src.tools.live_post_submit_ledger_update_dry_run import run_update
        assert "submit_order" not in inspect.getsource(run_update)

    def test_reuses_ledger_columns_from_pre_submit(self):
        from src.tools.live_post_submit_ledger_update_dry_run import LEDGER_COLUMNS
        from src.tools.live_pre_submit_ledger_dry_run import LEDGER_COLUMNS as PRE_COLS
        assert LEDGER_COLUMNS is PRE_COLS
