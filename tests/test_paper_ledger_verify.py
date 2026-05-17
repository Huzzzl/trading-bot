"""
tests/test_paper_ledger_verify.py
-----------------------------------
Offline tests for src/tools/paper_ledger_verify.py.

All Alpaca network calls are mocked.  No credentials are required.
submit_order and cancel_order are asserted never called.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_ledger(path: Path, rows: list[dict]) -> None:
    from src.execution.paper_ledger import append_ledger_row
    path.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        append_ledger_row(path, row)


def _ledger_row(
    client_order_id: str = "BT-000035",
    alpaca_order_id: str = "alp-aaa-111",
    status: str = "filled",
    flow: str = "buy_submit",
    side: str = "buy",
) -> dict:
    return {
        "run_id":          "run1",
        "flow":            flow,
        "client_order_id": client_order_id,
        "alpaca_order_id": alpaca_order_id,
        "symbol":          "SPY",
        "side":            side,
        "quantity":        "1.0",
        "status":          status,
        "submitted_at":    "2024-01-15 10:00:00-05:00",
        "output_dir":      "/output/run1",
        "notes":           "",
    }


def _fake_order_response(
    status: str = "filled",
    symbol: str = "SPY",
    side: str = "buy",
    qty: str = "1.0",
) -> MagicMock:
    m = MagicMock()
    m.status  = status
    m.symbol  = symbol
    m.side    = side
    m.qty     = qty
    return m


def _mock_client(order_response: MagicMock | None = None) -> MagicMock:
    client = MagicMock()
    if order_response is not None:
        client.get_order_by_id.return_value = order_response
    # Safety: submit_order and cancel_order must never be called
    client.submit_order.side_effect  = AssertionError("submit_order must not be called")
    client.cancel_order.side_effect  = AssertionError("cancel_order must not be called")
    return client


# ---------------------------------------------------------------------------
# _resolve_credentials
# ---------------------------------------------------------------------------

class TestResolveCredentials:
    def test_raises_when_key_missing(self, monkeypatch):
        from src.tools.paper_ledger_verify import _resolve_credentials
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
        with pytest.raises(RuntimeError, match="credentials"):
            _resolve_credentials()

    def test_raises_when_secret_missing(self, monkeypatch):
        from src.tools.paper_ledger_verify import _resolve_credentials
        monkeypatch.setenv("ALPACA_API_KEY", "key")
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="credentials"):
            _resolve_credentials()

    def test_raises_when_both_missing(self, monkeypatch):
        from src.tools.paper_ledger_verify import _resolve_credentials
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            _resolve_credentials()

    def test_returns_tuple_when_both_present(self, monkeypatch):
        from src.tools.paper_ledger_verify import _resolve_credentials
        monkeypatch.setenv("ALPACA_API_KEY", "mykey")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "mysecret")
        key, secret = _resolve_credentials()
        assert key == "mykey"
        assert secret == "mysecret"


# ---------------------------------------------------------------------------
# _normalize_alpaca_status
# ---------------------------------------------------------------------------

class TestNormalizeAlpacaStatus:
    @pytest.mark.parametrize("raw,expected", [
        ("filled",           "filled"),
        ("FILLED",           "filled"),
        ("partially_filled", "filled"),
        ("new",              "accepted"),
        ("pending_new",      "accepted"),
        ("canceled",         "cancelled"),
        ("cancelled",        "cancelled"),
        ("rejected",         "rejected"),
        ("OrderStatus.FILLED", "filled"),
        ("OrderStatus.CANCELED", "cancelled"),
    ])
    def test_known_statuses(self, raw, expected):
        from src.tools.paper_ledger_verify import _normalize_alpaca_status
        assert _normalize_alpaca_status(raw) == expected

    def test_unknown_status_passthrough(self):
        from src.tools.paper_ledger_verify import _normalize_alpaca_status
        assert _normalize_alpaca_status("some_future_status") == "some_future_status"


# ---------------------------------------------------------------------------
# _lookup_order
# ---------------------------------------------------------------------------

class TestLookupOrder:
    def test_returns_status_on_success(self):
        from src.tools.paper_ledger_verify import _lookup_order
        client = _mock_client(_fake_order_response(status="filled"))
        result = _lookup_order(client, "alp-aaa-111")
        assert result["status"] == "filled"
        assert result["error"] == ""

    def test_normalises_enum_status(self):
        from src.tools.paper_ledger_verify import _lookup_order
        client = _mock_client(_fake_order_response(status="OrderStatus.FILLED"))
        result = _lookup_order(client, "alp-aaa-111")
        assert result["status"] == "filled"

    def test_returns_error_string_on_exception(self):
        from src.tools.paper_ledger_verify import _lookup_order
        client = MagicMock()
        client.get_order_by_id.side_effect = Exception("404 not found")
        result = _lookup_order(client, "alp-bad-999")
        assert result["status"] == ""
        assert "404" in result["error"]

    def test_submit_order_never_called(self):
        from src.tools.paper_ledger_verify import _lookup_order
        client = _mock_client(_fake_order_response())
        _lookup_order(client, "alp-aaa-111")
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self):
        from src.tools.paper_ledger_verify import _lookup_order
        client = _mock_client(_fake_order_response())
        _lookup_order(client, "alp-aaa-111")
        client.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# verify_ledger
# ---------------------------------------------------------------------------

class TestVerifyLedger:
    def test_returns_one_row_per_ledger_entry(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        client = _mock_client(_fake_order_response(status="filled"))
        rows = verify_ledger(ledger, client)
        assert len(rows) == 1

    def test_status_match_true_when_equal(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(status="filled")])
        client = _mock_client(_fake_order_response(status="filled"))
        rows = verify_ledger(ledger, client)
        assert rows[0]["status_match"] is True

    def test_status_match_false_when_different(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(status="accepted")])
        client = _mock_client(_fake_order_response(status="filled"))
        rows = verify_ledger(ledger, client)
        assert rows[0]["status_match"] is False

    def test_error_recorded_when_lookup_fails(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        client = MagicMock()
        client.get_order_by_id.side_effect = Exception("network error")
        rows = verify_ledger(ledger, client)
        assert rows[0]["error"] != ""
        assert rows[0]["status_match"] is False

    def test_continues_after_single_lookup_error(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [
            _ledger_row("BT-001", "alp-001"),
            _ledger_row("BT-002", "alp-002"),
        ])
        client = MagicMock()
        client.get_order_by_id.side_effect = [
            Exception("lookup failed"),
            _fake_order_response(status="filled"),
        ]
        rows = verify_ledger(ledger, client)
        assert len(rows) == 2
        assert rows[0]["error"] != ""
        assert rows[1]["error"] == ""

    def test_row_without_alpaca_order_id_skipped(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(alpaca_order_id="")])
        client = _mock_client()
        rows = verify_ledger(ledger, client)
        assert rows[0]["error"] == "no alpaca_order_id"
        client.get_order_by_id.assert_not_called()

    def test_verification_row_has_all_columns(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger, _VERIFY_COLUMNS
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        client = _mock_client(_fake_order_response())
        rows = verify_ledger(ledger, client)
        for col in _VERIFY_COLUMNS:
            assert col in rows[0], f"missing column: {col}"

    def test_submit_order_never_called(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        client = _mock_client(_fake_order_response())
        verify_ledger(ledger, client)
        client.submit_order.assert_not_called()

    def test_cancel_order_never_called(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row()])
        client = _mock_client(_fake_order_response())
        verify_ledger(ledger, client)
        client.cancel_order.assert_not_called()

    def test_multiple_rows_all_verified(self, tmp_path):
        from src.tools.paper_ledger_verify import verify_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [
            _ledger_row("BT-001", "alp-001", status="filled"),
            _ledger_row("BT-002", "alp-002", status="accepted"),
        ])
        client = MagicMock()
        client.submit_order.side_effect  = AssertionError("must not call")
        client.cancel_order.side_effect  = AssertionError("must not call")
        client.get_order_by_id.side_effect = [
            _fake_order_response(status="filled"),
            _fake_order_response(status="accepted"),
        ]
        rows = verify_ledger(ledger, client)
        assert rows[0]["status_match"] is True
        assert rows[1]["status_match"] is True


# ---------------------------------------------------------------------------
# write_verification_csv
# ---------------------------------------------------------------------------

class TestWriteVerificationCsv:
    def test_creates_csv_with_correct_columns(self, tmp_path):
        from src.tools.paper_ledger_verify import write_verification_csv, _VERIFY_COLUMNS
        rows = [{col: "x" for col in _VERIFY_COLUMNS}]
        out = tmp_path / "verify.csv"
        write_verification_csv(rows, out)
        df = pd.read_csv(out, dtype=str)
        assert list(df.columns) == _VERIFY_COLUMNS

    def test_creates_parent_dirs(self, tmp_path):
        from src.tools.paper_ledger_verify import write_verification_csv, _VERIFY_COLUMNS
        rows = [{col: "" for col in _VERIFY_COLUMNS}]
        out = tmp_path / "sub" / "verify.csv"
        write_verification_csv(rows, out)
        assert out.exists()

    def test_written_with_utf8(self, tmp_path):
        from src.tools.paper_ledger_verify import write_verification_csv, _VERIFY_COLUMNS
        rows = [{**{col: "" for col in _VERIFY_COLUMNS}, "symbol": "SPÝ"}]
        out = tmp_path / "verify.csv"
        write_verification_csv(rows, out)
        content = out.read_text(encoding="utf-8")
        assert "SPÝ" in content


# ---------------------------------------------------------------------------
# update_ledger_status
# ---------------------------------------------------------------------------

class TestUpdateLedgerStatus:
    def test_updates_status_for_matched_row(self, tmp_path):
        from src.tools.paper_ledger_verify import update_ledger_status
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(status="accepted")])
        verify_rows = [{
            "client_order_id": "BT-000035",
            "alpaca_status":   "filled",
            "error":           "",
        }]
        n = update_ledger_status(ledger, verify_rows)
        assert n == 1
        df = load_ledger(ledger)
        assert df.iloc[0]["status"] == "filled"

    def test_skips_rows_with_error(self, tmp_path):
        from src.tools.paper_ledger_verify import update_ledger_status
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(status="accepted")])
        verify_rows = [{
            "client_order_id": "BT-000035",
            "alpaca_status":   "filled",
            "error":           "lookup failed",
        }]
        n = update_ledger_status(ledger, verify_rows)
        assert n == 0
        df = load_ledger(ledger)
        assert df.iloc[0]["status"] == "accepted"

    def test_skips_rows_with_empty_alpaca_status(self, tmp_path):
        from src.tools.paper_ledger_verify import update_ledger_status
        from src.execution.paper_ledger import load_ledger
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(status="accepted")])
        verify_rows = [{
            "client_order_id": "BT-000035",
            "alpaca_status":   "",
            "error":           "",
        }]
        n = update_ledger_status(ledger, verify_rows)
        assert n == 0

    def test_returns_zero_when_ledger_empty(self, tmp_path):
        from src.tools.paper_ledger_verify import update_ledger_status
        from src.execution.paper_ledger import _LEDGER_COLUMNS
        ledger = tmp_path / "ledger.csv"
        ledger.write_text(",".join(_LEDGER_COLUMNS) + "\n", encoding="utf-8")
        n = update_ledger_status(ledger, [{"client_order_id": "BT-001", "alpaca_status": "filled", "error": ""}])
        assert n == 0


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

class TestMain:
    def _setup(self, tmp_path: Path, status: str = "filled"):
        ledger = tmp_path / "ledger.csv"
        _write_ledger(ledger, [_ledger_row(status=status)])
        return ledger

    def _run_main(self, tmp_path: Path, ledger: Path, extra_args: list[str] | None = None):
        from src.tools.paper_ledger_verify import main
        output = tmp_path / "verify.csv"
        args = ["--ledger", str(ledger), "--output", str(output)] + (extra_args or [])

        fake_client = _mock_client(_fake_order_response(status="filled"))
        with (
            patch("src.tools.paper_ledger_verify._resolve_credentials", return_value=("k", "s")),
            patch("src.tools.paper_ledger_verify._make_client", return_value=fake_client),
        ):
            main(args)

        return output

    def test_writes_verification_csv(self, tmp_path):
        ledger = self._setup(tmp_path)
        out = self._run_main(tmp_path, ledger)
        assert out.exists()

    def test_verification_csv_has_correct_columns(self, tmp_path):
        from src.tools.paper_ledger_verify import _VERIFY_COLUMNS
        ledger = self._setup(tmp_path)
        out = self._run_main(tmp_path, ledger)
        df = pd.read_csv(out, dtype=str, encoding="utf-8")
        assert list(df.columns) == _VERIFY_COLUMNS

    def test_missing_credentials_exits_1(self, tmp_path, monkeypatch):
        from src.tools.paper_ledger_verify import main
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        ledger = self._setup(tmp_path)
        output = tmp_path / "verify.csv"
        with pytest.raises(SystemExit) as exc:
            main(["--ledger", str(ledger), "--output", str(output)])
        assert exc.value.code == 1

    def test_missing_ledger_exits_1(self, tmp_path):
        from src.tools.paper_ledger_verify import main
        output = tmp_path / "verify.csv"
        with (
            patch("src.tools.paper_ledger_verify._resolve_credentials", return_value=("k", "s")),
            pytest.raises(SystemExit) as exc,
        ):
            main(["--ledger", str(tmp_path / "nonexistent.csv"), "--output", str(output)])
        assert exc.value.code == 1

    def test_status_mismatch_exits_1(self, tmp_path):
        from src.tools.paper_ledger_verify import main
        ledger = self._setup(tmp_path, status="accepted")  # ledger says accepted
        output = tmp_path / "verify.csv"
        fake_client = _mock_client(_fake_order_response(status="filled"))  # Alpaca says filled
        with (
            patch("src.tools.paper_ledger_verify._resolve_credentials", return_value=("k", "s")),
            patch("src.tools.paper_ledger_verify._make_client", return_value=fake_client),
            pytest.raises(SystemExit) as exc,
        ):
            main(["--ledger", str(ledger), "--output", str(output)])
        assert exc.value.code == 1

    def test_all_match_exits_0(self, tmp_path):
        ledger = self._setup(tmp_path, status="filled")
        out = self._run_main(tmp_path, ledger)
        df = pd.read_csv(out, dtype=str, encoding="utf-8")
        assert df.iloc[0]["status_match"] == "True"

    def test_update_ledger_status_flag_updates_ledger(self, tmp_path):
        from src.tools.paper_ledger_verify import main
        from src.execution.paper_ledger import load_ledger
        ledger = self._setup(tmp_path, status="accepted")
        output = tmp_path / "verify.csv"
        fake_client = _mock_client(_fake_order_response(status="filled"))
        with (
            patch("src.tools.paper_ledger_verify._resolve_credentials", return_value=("k", "s")),
            patch("src.tools.paper_ledger_verify._make_client", return_value=fake_client),
        ):
            # Exits 1 because mismatch, but ledger should still be updated
            try:
                main(["--ledger", str(ledger), "--output", str(output), "--update-ledger-status"])
            except SystemExit:
                pass
        df = load_ledger(ledger)
        assert df.iloc[0]["status"] == "filled"

    def test_submit_order_never_called_via_main(self, tmp_path):
        ledger = self._setup(tmp_path)
        from src.tools.paper_ledger_verify import main
        output = tmp_path / "verify.csv"
        fake_client = _mock_client(_fake_order_response())
        with (
            patch("src.tools.paper_ledger_verify._resolve_credentials", return_value=("k", "s")),
            patch("src.tools.paper_ledger_verify._make_client", return_value=fake_client),
        ):
            main(["--ledger", str(ledger), "--output", str(output)])
        fake_client.submit_order.assert_not_called()

    def test_cancel_order_never_called_via_main(self, tmp_path):
        ledger = self._setup(tmp_path)
        from src.tools.paper_ledger_verify import main
        output = tmp_path / "verify.csv"
        fake_client = _mock_client(_fake_order_response())
        with (
            patch("src.tools.paper_ledger_verify._resolve_credentials", return_value=("k", "s")),
            patch("src.tools.paper_ledger_verify._make_client", return_value=fake_client),
        ):
            main(["--ledger", str(ledger), "--output", str(output)])
        fake_client.cancel_order.assert_not_called()
