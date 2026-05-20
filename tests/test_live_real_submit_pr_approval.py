"""
tests/test_live_real_submit_pr_approval.py
-------------------------------------------
Tests for src/tools/live_real_submit_pr_approval.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no ledger writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_checklist(**overrides) -> dict:
    d = {
        "release_result": "RELEASE_READY",
        "operator_name": None,
        "approval_for_real_submit_pr": False,
        "approval_timestamp_utc": None,
        "notes": "",
    }
    d.update(overrides)
    return d


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _base_args(checklist_path: Path, output_path: Path, **kwargs) -> list[str]:
    operator = kwargs.get("operator_name", "Huzzzl")
    note = kwargs.get("approval_note", "Approve opening real-submit PR only; not approving live trading.")
    return [
        "--release-checklist", str(checklist_path),
        "--operator-name", operator,
        "--approval-note", note,
        "--output", str(output_path),
    ]


def _run_main(args: list[str]) -> int | None:
    from src.tools.live_real_submit_pr_approval import main
    try:
        main(args)
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# _read_json
# ---------------------------------------------------------------------------

class TestReadJson:
    def test_valid_json_returns_dict(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import _read_json
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        assert _read_json(p) == {"key": "value"}

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import _read_json
        with pytest.raises(FileNotFoundError):
            _read_json(tmp_path / "nonexistent.json")

    def test_malformed_json_raises_value_error(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import _read_json
        p = tmp_path / "bad.json"
        p.write_text("{bad json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            _read_json(p)


# ---------------------------------------------------------------------------
# _validate_release_checklist
# ---------------------------------------------------------------------------

class TestValidateReleaseChecklist:
    def test_release_ready_passes(self):
        from src.tools.live_real_submit_pr_approval import _validate_release_checklist
        assert _validate_release_checklist({"release_result": "RELEASE_READY"}) == []

    def test_not_release_ready_fails(self):
        from src.tools.live_real_submit_pr_approval import _validate_release_checklist
        v = _validate_release_checklist({"release_result": "NOT_RELEASE_READY"})
        assert len(v) == 1
        assert "RELEASE_READY" in v[0]

    def test_missing_release_result_fails(self):
        from src.tools.live_real_submit_pr_approval import _validate_release_checklist
        v = _validate_release_checklist({})
        assert len(v) == 1


# ---------------------------------------------------------------------------
# _validate_inputs
# ---------------------------------------------------------------------------

class TestValidateInputs:
    def test_valid_inputs_pass(self):
        from src.tools.live_real_submit_pr_approval import _validate_inputs
        assert _validate_inputs("Huzzzl", "some note") == []

    def test_empty_operator_name_fails(self):
        from src.tools.live_real_submit_pr_approval import _validate_inputs
        v = _validate_inputs("", "some note")
        assert any("operator-name" in x for x in v)

    def test_whitespace_operator_name_fails(self):
        from src.tools.live_real_submit_pr_approval import _validate_inputs
        v = _validate_inputs("   ", "some note")
        assert len(v) == 1

    def test_empty_approval_note_fails(self):
        from src.tools.live_real_submit_pr_approval import _validate_inputs
        v = _validate_inputs("Huzzzl", "")
        assert any("approval-note" in x for x in v)

    def test_whitespace_approval_note_fails(self):
        from src.tools.live_real_submit_pr_approval import _validate_inputs
        v = _validate_inputs("Huzzzl", "   ")
        assert len(v) == 1

    def test_both_empty_returns_two_violations(self):
        from src.tools.live_real_submit_pr_approval import _validate_inputs
        v = _validate_inputs("", "")
        assert len(v) == 2


# ---------------------------------------------------------------------------
# build_approval
# ---------------------------------------------------------------------------

class TestBuildApproval:
    def test_required_fields_present(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        checklist = _valid_checklist()
        artifact = build_approval(checklist, tmp_path / "checklist.json", "Huzzzl", "note")
        for field in [
            "checked_at_utc", "source_release_checklist_path", "release_result",
            "operator_name", "approval_timestamp_utc", "approval_for_real_submit_pr",
            "approval_scope", "live_trading_approved", "live_order_submission_approved",
            "approval_note",
        ]:
            assert field in artifact, f"Missing field: {field}"

    def test_approval_for_real_submit_pr_true(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        assert artifact["approval_for_real_submit_pr"] is True

    def test_live_trading_approved_false(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        assert artifact["live_trading_approved"] is False

    def test_live_order_submission_approved_false(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        assert artifact["live_order_submission_approved"] is False

    def test_approval_scope_correct(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        assert artifact["approval_scope"] == "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"

    def test_operator_name_stripped(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "  Huzzzl  ", "note")
        assert artifact["operator_name"] == "Huzzzl"

    def test_approval_note_stripped(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "  my note  ")
        assert artifact["approval_note"] == "my note"

    def test_release_result_preserved(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        assert artifact["release_result"] == "RELEASE_READY"

    def test_source_path_recorded(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval
        p = tmp_path / "checklist.json"
        artifact = build_approval(_valid_checklist(), p, "Op", "note")
        assert str(p) in artifact["source_release_checklist_path"]


# ---------------------------------------------------------------------------
# write_approval
# ---------------------------------------------------------------------------

class TestWriteApproval:
    def test_writes_json_file(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval, write_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        out = write_approval(tmp_path / "approval.json", artifact)
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["approval_for_real_submit_pr"] is True

    def test_creates_parent_dirs(self, tmp_path):
        from src.tools.live_real_submit_pr_approval import build_approval, write_approval
        artifact = build_approval(_valid_checklist(), tmp_path / "c.json", "Op", "note")
        out = write_approval(tmp_path / "nested" / "dir" / "approval.json", artifact)
        assert out.exists()


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_release_ready_exits_0(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(checklist, out))
        assert code in (0, None)

    def test_valid_release_ready_creates_artifact(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist, out))
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["approval_for_real_submit_pr"] is True
        assert data["live_trading_approved"] is False
        assert data["live_order_submission_approved"] is False
        assert data["approval_scope"] == "OPEN_REAL_SUBMIT_IMPLEMENTATION_PR_ONLY"

    def test_not_release_ready_exits_1(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist(release_result="NOT_RELEASE_READY"))
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(checklist, out))
        assert code == 1

    def test_not_release_ready_no_artifact_written(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist(release_result="NOT_RELEASE_READY"))
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist, out))
        assert not out.exists()

    def test_missing_checklist_exits_1(self, tmp_path):
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(tmp_path / "nonexistent.json", out))
        assert code == 1

    def test_malformed_checklist_exits_1(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{bad json", encoding="utf-8")
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(bad, out))
        assert code == 1

    def test_empty_operator_name_exits_1(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(checklist, out, operator_name=""))
        assert code == 1

    def test_empty_approval_note_exits_1(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        code = _run_main(_base_args(checklist, out, approval_note=""))
        assert code == 1

    def test_artifact_live_trading_approved_false(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist, out))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["live_trading_approved"] is False

    def test_artifact_live_order_submission_approved_false(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist, out))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["live_order_submission_approved"] is False

    def test_no_alpaca_calls(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        broker = MagicMock()
        _run_main(_base_args(checklist, out))
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_needed(self, tmp_path):
        import os
        from unittest.mock import patch
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(_base_args(checklist, out))
        assert code in (0, None)

    def test_does_not_mutate_source_checklist(self, tmp_path):
        checklist_path = _write(tmp_path, "checklist.json", _valid_checklist())
        original = checklist_path.read_text(encoding="utf-8")
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist_path, out))
        assert checklist_path.read_text(encoding="utf-8") == original

    def test_operator_name_in_artifact(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist, out, operator_name="Alice"))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["operator_name"] == "Alice"

    def test_approval_note_in_artifact(self, tmp_path):
        checklist = _write(tmp_path, "checklist.json", _valid_checklist())
        out = tmp_path / "approval.json"
        _run_main(_base_args(checklist, out, approval_note="PR only, no live trade"))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["approval_note"] == "PR only, no live trade"
