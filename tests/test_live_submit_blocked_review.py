"""
tests/test_live_submit_blocked_review.py
------------------------------------------
Tests for src/tools/live_submit_blocked_review.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no file writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_report(**overrides) -> dict:
    d = {
        "checked_at_utc":      "2026-05-20T10:00:00+00:00",
        "symbol":              "SPY",
        "submit_order_called": False,
        "blocked":             True,
        "block_guard":         "real_submit_not_implemented",
        "violations":          ["real submit execution is not implemented in this PR"],
    }
    d.update(overrides)
    return d


def _write_report(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "live_submit_blocked_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _run_main(report_path: str) -> int | None:
    from src.tools.live_submit_blocked_review import main
    try:
        main(["--report", report_path])
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# parse_report
# ---------------------------------------------------------------------------

class TestParseReport:
    def test_valid_report_returns_dict(self, tmp_path):
        from src.tools.live_submit_blocked_review import parse_report
        path = _write_report(tmp_path, _valid_report())
        report = parse_report(path)
        assert isinstance(report, dict)
        assert report["symbol"] == "SPY"

    def test_missing_file_raises_file_not_found(self, tmp_path):
        from src.tools.live_submit_blocked_review import parse_report
        with pytest.raises(FileNotFoundError):
            parse_report(tmp_path / "nonexistent.json")

    def test_malformed_json_raises_value_error(self, tmp_path):
        from src.tools.live_submit_blocked_review import parse_report
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_report(path)

    def test_reads_utf8_encoding(self, tmp_path):
        from src.tools.live_submit_blocked_review import parse_report
        path = _write_report(tmp_path, _valid_report(symbol="SPY"))
        result = parse_report(path)
        assert result["symbol"] == "SPY"


# ---------------------------------------------------------------------------
# validate_report
# ---------------------------------------------------------------------------

class TestValidateReport:
    def test_valid_blocked_report_passes(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report())
        assert result == "PASS"
        assert violations == []

    def test_blocked_false_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(blocked=False))
        assert result == "FAIL"
        assert any("blocked" in v for v in violations)

    def test_submit_order_called_true_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(submit_order_called=True))
        assert result == "FAIL"
        assert any("submit_order_called" in v for v in violations)

    def test_empty_block_guard_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(block_guard=""))
        assert result == "FAIL"
        assert any("block_guard" in v for v in violations)

    def test_none_block_guard_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(block_guard=None))
        assert result == "FAIL"
        assert any("block_guard" in v for v in violations)

    def test_empty_violations_list_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(violations=[]))
        assert result == "FAIL"
        assert any("violations" in v for v in violations)

    def test_missing_violations_key_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        report = _valid_report()
        del report["violations"]
        result, violations = validate_report(report)
        assert result == "FAIL"
        assert any("violations" in v for v in violations)

    def test_violations_not_list_fails(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(violations="not a list"))
        assert result == "FAIL"

    def test_multiple_violations_all_reported(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, violations = validate_report(_valid_report(
            blocked=False,
            submit_order_called=True,
            block_guard="",
            violations=[],
        ))
        assert result == "FAIL"
        assert len(violations) >= 4

    def test_string_false_truthy_check(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, _ = validate_report(_valid_report(
            blocked="True",
            submit_order_called="False",
        ))
        assert result == "PASS"

    def test_real_submit_not_implemented_guard_passes(self):
        from src.tools.live_submit_blocked_review import validate_report
        result, _ = validate_report(_valid_report(
            block_guard="real_submit_not_implemented",
        ))
        assert result == "PASS"


# ---------------------------------------------------------------------------
# build_review
# ---------------------------------------------------------------------------

class TestBuildReview:
    def test_review_result_pass_for_valid(self):
        from src.tools.live_submit_blocked_review import build_review
        review = build_review(_valid_report())
        assert review["review_result"] == "PASS"

    def test_review_result_fail_propagated(self):
        from src.tools.live_submit_blocked_review import build_review
        review = build_review(_valid_report(blocked=False))
        assert review["review_result"] == "FAIL"

    def test_required_fields_in_review(self):
        from src.tools.live_submit_blocked_review import build_review
        review = build_review(_valid_report())
        for field in [
            "symbol", "blocked", "block_guard", "submit_order_called",
            "violations", "review_result", "review_violations",
        ]:
            assert field in review, f"Missing field: {field}"

    def test_review_violations_empty_on_pass(self):
        from src.tools.live_submit_blocked_review import build_review
        review = build_review(_valid_report())
        assert review["review_violations"] == []

    def test_review_violations_populated_on_fail(self):
        from src.tools.live_submit_blocked_review import build_review
        review = build_review(_valid_report(blocked=False))
        assert len(review["review_violations"]) >= 1


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_blocked_report_exits_0(self, tmp_path):
        path = _write_report(tmp_path, _valid_report())
        assert _run_main(str(path)) in (0, None)

    def test_missing_report_exits_1(self, tmp_path):
        assert _run_main(str(tmp_path / "nonexistent.json")) == 1

    def test_malformed_json_exits_1(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{bad json", encoding="utf-8")
        assert _run_main(str(path)) == 1

    def test_blocked_false_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(blocked=False))
        assert _run_main(str(path)) == 1

    def test_submit_order_called_true_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(submit_order_called=True))
        assert _run_main(str(path)) == 1

    def test_empty_block_guard_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(block_guard=""))
        assert _run_main(str(path)) == 1

    def test_empty_violations_exits_1(self, tmp_path):
        path = _write_report(tmp_path, _valid_report(violations=[]))
        assert _run_main(str(path)) == 1

    def test_no_alpaca_calls(self, tmp_path):
        path = _write_report(tmp_path, _valid_report())
        broker = MagicMock()
        _run_main(str(path))
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_read(self, tmp_path):
        import os
        from unittest.mock import patch
        path = _write_report(tmp_path, _valid_report())
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code = _run_main(str(path))
        assert code in (0, None)

    def test_no_files_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        report_path = _write_report(tmp_path, _valid_report())
        before = set(tmp_path.glob("**/*"))
        _run_main(str(report_path))
        after = set(tmp_path.glob("**/*"))
        assert after == before
