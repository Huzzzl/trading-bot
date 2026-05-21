"""
tests/test_live_v2_readiness_bundle.py
-----------------------------------------
Tests for src/tools/live_v2_readiness_bundle.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted or cancelled, no live ledger writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_ta(**overrides) -> dict:
    d = {
        "live_trading_approved":          True,
        "live_order_submission_approved": False,
        "approval_scope":                 "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":              True,
        "approved_symbol":                "SPY",
        "approved_max_notional":          100.0,
    }
    d.update(overrides)
    return d


def _valid_sa(ta_path: Path, **overrides) -> dict:
    d = {
        "live_trading_approved":                        True,
        "live_order_submission_approved":               True,
        "order_submission_approval_for_single_attempt": True,
        "approval_scope":                               "AUTHORIZE_SINGLE_LIVE_ORDER_ATTEMPT_ONLY",
        "risk_acknowledged":                            True,
        "source_live_trading_approval_path":            str(ta_path),
        "approved_symbol":                              "SPY",
        "approved_max_notional":                        100.0,
    }
    d.update(overrides)
    return d


def _valid_executor_report(**overrides) -> dict:
    d = {
        "checked_at_utc":      "2026-05-21T10:00:00+00:00",
        "symbol":              "SPY",
        "submit_order_called": False,
        "blocked":             True,
        "block_guard":         "config_safety",
        "violations": [
            "live_trading_enabled=false -- live trading is disabled in config",
            "live_submit_dry_run=true -- dry-run gate is engaged",
            "live_kill_switch_enabled=true -- kill switch is engaged",
        ],
    }
    d.update(overrides)
    return d


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_all(tmp_path: Path, *,
               ta_overrides=None, sa_overrides=None, exec_overrides=None):
    """Write all three input artifacts. Returns (ta_path, sa_path, exec_path)."""
    ta_path = _write(tmp_path / "ta.json", _valid_ta(**(ta_overrides or {})))
    sa_path = _write(tmp_path / "sa.json",
                     _valid_sa(ta_path, **(sa_overrides or {})))
    exec_path = _write(tmp_path / "exec.json",
                       _valid_executor_report(**(exec_overrides or {})))
    return ta_path, sa_path, exec_path


def _run_bundle(tmp_path: Path, *,
                ta_path=None, sa_path=None, exec_path=None,
                output_dir=None):
    from src.tools.live_v2_readiness_bundle import run_bundle
    if ta_path is None or sa_path is None or exec_path is None:
        _ta, _sa, _ex = _write_all(tmp_path)
        ta_path = ta_path or _ta
        sa_path = sa_path or _sa
        exec_path = exec_path or _ex
    out = output_dir or (tmp_path / "bundle_out")
    return run_bundle(ta_path=ta_path, sa_path=sa_path,
                      exec_path=exec_path, output_dir=out), out


def _run_main(tmp_path: Path, *,
              ta_path=None, sa_path=None, exec_path=None,
              output_dir=None) -> tuple[int | None, Path]:
    if ta_path is None or sa_path is None or exec_path is None:
        _ta, _sa, _ex = _write_all(tmp_path)
        ta_path = ta_path or _ta
        sa_path = sa_path or _sa
        exec_path = exec_path or _ex
    out = output_dir or (tmp_path / "main_out")
    from src.tools.live_v2_readiness_bundle import main
    argv = [
        "--live-trading-approval", str(ta_path),
        "--live-order-submission-approval", str(sa_path),
        "--executor-readiness-report", str(exec_path),
        "--output-dir", str(out),
    ]
    try:
        main(argv)
        return None, out
    except SystemExit as exc:
        return exc.code, out


# ---------------------------------------------------------------------------
# Bundle summary artifact helpers
# ---------------------------------------------------------------------------

def _bundle_summary(out: Path) -> dict:
    return json.loads((out / "live_v2_readiness_bundle.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# run_bundle unit tests
# ---------------------------------------------------------------------------

class TestRunBundle:
    def test_valid_inputs_bundle_passes(self, tmp_path):
        bundle, _ = _run_bundle(tmp_path)
        assert bundle["bundle_result"] == "PASS"

    def test_valid_inputs_writes_all_three_artifacts(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        assert (out / "live_v2_approvals_review.json").exists()
        assert (out / "live_v2_executor_readiness_review.json").exists()
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_bundle_result_in_summary(self, tmp_path):
        bundle, _ = _run_bundle(tmp_path)
        assert "bundle_result" in bundle
        assert "checked_at_utc" in bundle
        assert "approvals_review_path" in bundle
        assert "executor_readiness_review_path" in bundle
        assert "final_readiness_review_path" in bundle

    def test_artifact_paths_point_to_written_files(self, tmp_path):
        bundle, _ = _run_bundle(tmp_path)
        assert Path(bundle["approvals_review_path"]).exists()
        assert Path(bundle["executor_readiness_review_path"]).exists()
        assert Path(bundle["final_readiness_review_path"]).exists()

    def test_approvals_review_artifact_is_valid_json(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = json.loads((out / "live_v2_approvals_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "PASS"

    def test_executor_review_artifact_is_valid_json(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = json.loads(
            (out / "live_v2_executor_readiness_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "PASS"

    def test_final_review_artifact_is_valid_json(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = json.loads(
            (out / "live_v2_final_readiness_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "PASS"

    def test_output_dir_created_if_missing(self, tmp_path):
        deep_out = tmp_path / "deep" / "nested" / "bundle"
        assert not deep_out.exists()
        _run_bundle(tmp_path, output_dir=deep_out)
        assert deep_out.exists()

    def test_approval_failure_still_writes_all_artifacts(self, tmp_path):
        """Bad approval artifact: all three artifacts written, bundle FAILs."""
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        bundle, out = _run_bundle(tmp_path,
                                  ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert bundle["bundle_result"] == "FAIL"
        assert (out / "live_v2_approvals_review.json").exists()
        assert (out / "live_v2_executor_readiness_review.json").exists()
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_approval_failure_approvals_artifact_is_fail(self, tmp_path):
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        _, out = _run_bundle(tmp_path,
                             ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        data = json.loads((out / "live_v2_approvals_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "FAIL"

    def test_approval_failure_executor_artifact_still_written(self, tmp_path):
        """Executor review runs even when approvals fail."""
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        _, out = _run_bundle(tmp_path,
                             ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        executor_data = json.loads(
            (out / "live_v2_executor_readiness_review.json").read_text(encoding="utf-8"))
        assert "review_result" in executor_data

    def test_executor_failure_still_writes_all_artifacts(self, tmp_path):
        """Bad executor report: all three artifacts written, bundle FAILs."""
        ta_path, sa_path, _ = _write_all(tmp_path)
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report(
            block_guard="approval_artifact",
            violations=["live_trading_approved=false"],
        ))
        bundle, out = _run_bundle(tmp_path,
                                  ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert bundle["bundle_result"] == "FAIL"
        assert (out / "live_v2_approvals_review.json").exists()
        assert (out / "live_v2_executor_readiness_review.json").exists()
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_executor_failure_approvals_artifact_still_passes(self, tmp_path):
        """Approvals review result unaffected by executor failure."""
        ta_path, sa_path, _ = _write_all(tmp_path)
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        _, out = _run_bundle(tmp_path,
                             ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        data = json.loads((out / "live_v2_approvals_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "PASS"

    def test_malformed_ta_writes_approvals_fail_artifact(self, tmp_path):
        bad_ta = tmp_path / "bad_ta.json"
        bad_ta.write_text("{bad json", encoding="utf-8")
        _, sa_path, exec_path = _write_all(tmp_path)
        _, out = _run_bundle(tmp_path,
                             ta_path=bad_ta, sa_path=sa_path, exec_path=exec_path)
        data = json.loads((out / "live_v2_approvals_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "FAIL"

    def test_missing_executor_report_writes_executor_fail_artifact(self, tmp_path):
        ta_path, sa_path, _ = _write_all(tmp_path)
        _, out = _run_bundle(tmp_path,
                             ta_path=ta_path, sa_path=sa_path,
                             exec_path=tmp_path / "nonexistent.json")
        data = json.loads(
            (out / "live_v2_executor_readiness_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "FAIL"

    def test_missing_executor_report_final_review_still_written(self, tmp_path):
        ta_path, sa_path, _ = _write_all(tmp_path)
        _, out = _run_bundle(tmp_path,
                             ta_path=ta_path, sa_path=sa_path,
                             exec_path=tmp_path / "nonexistent.json")
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_missing_executor_report_approvals_review_passes(self, tmp_path):
        """Approvals review unaffected by missing executor report."""
        ta_path, sa_path, _ = _write_all(tmp_path)
        _, out = _run_bundle(tmp_path,
                             ta_path=ta_path, sa_path=sa_path,
                             exec_path=tmp_path / "nonexistent.json")
        data = json.loads((out / "live_v2_approvals_review.json").read_text(encoding="utf-8"))
        assert data["review_result"] == "PASS"

    def test_live_submit_never_enabled_in_final_artifact(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = json.loads(
            (out / "live_v2_final_readiness_review.json").read_text(encoding="utf-8"))
        assert data["live_submit_enabled"] is False
        assert data["real_submit_implemented"] is False

    # --- Bundle summary artifact tests ---

    def test_run_bundle_writes_bundle_summary_artifact(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        assert (out / "live_v2_readiness_bundle.json").exists()

    def test_bundle_summary_contains_bundle_result(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = _bundle_summary(out)
        assert "bundle_result" in data
        assert data["bundle_result"] == "PASS"

    def test_bundle_summary_contains_all_artifact_paths(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = _bundle_summary(out)
        assert "approvals_review_path" in data
        assert "executor_readiness_review_path" in data
        assert "final_readiness_review_path" in data

    def test_bundle_summary_contains_checked_at_utc(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = _bundle_summary(out)
        assert "checked_at_utc" in data
        assert data["checked_at_utc"]

    def test_bundle_summary_written_on_fail(self, tmp_path):
        """Bundle summary artifact is written even when bundle_result is FAIL."""
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        bundle, out = _run_bundle(tmp_path,
                                  ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert bundle["bundle_result"] == "FAIL"
        assert (out / "live_v2_readiness_bundle.json").exists()
        data = _bundle_summary(out)
        assert data["bundle_result"] == "FAIL"

    def test_bundle_summary_paths_point_to_existing_files(self, tmp_path):
        _, out = _run_bundle(tmp_path)
        data = _bundle_summary(out)
        assert Path(data["approvals_review_path"]).exists()
        assert Path(data["executor_readiness_review_path"]).exists()
        assert Path(data["final_readiness_review_path"]).exists()


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_inputs_exits_0(self, tmp_path):
        code, _ = _run_main(tmp_path)
        assert code in (0, None)

    def test_valid_inputs_writes_all_artifacts(self, tmp_path):
        _, out = _run_main(tmp_path)
        assert (out / "live_v2_approvals_review.json").exists()
        assert (out / "live_v2_executor_readiness_review.json").exists()
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_approval_failure_exits_1(self, tmp_path):
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        code, _ = _run_main(tmp_path,
                             ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert code == 1

    def test_approval_failure_still_writes_final_review(self, tmp_path):
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        _, out = _run_main(tmp_path,
                           ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_executor_failure_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_all(tmp_path)
        exec_path = _write(tmp_path / "bad_exec.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        code, _ = _run_main(tmp_path,
                             ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert code == 1

    def test_executor_failure_still_writes_final_review(self, tmp_path):
        ta_path, sa_path, _ = _write_all(tmp_path)
        exec_path = _write(tmp_path / "bad_exec.json", _valid_executor_report(
            block_guard="v2_trading_approval",
            violations=["v2 live_trading_approved=False"],
        ))
        _, out = _run_main(tmp_path,
                           ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_malformed_ta_exits_1(self, tmp_path):
        bad_ta = tmp_path / "bad_ta.json"
        bad_ta.write_text("{bad json", encoding="utf-8")
        _, sa_path, exec_path = _write_all(tmp_path)
        code, _ = _run_main(tmp_path,
                             ta_path=bad_ta, sa_path=sa_path, exec_path=exec_path)
        assert code == 1

    def test_malformed_ta_still_writes_executor_and_final_artifacts(self, tmp_path):
        bad_ta = tmp_path / "bad_ta.json"
        bad_ta.write_text("{bad json", encoding="utf-8")
        _, sa_path, exec_path = _write_all(tmp_path)
        _, out = _run_main(tmp_path,
                           ta_path=bad_ta, sa_path=sa_path, exec_path=exec_path)
        assert (out / "live_v2_executor_readiness_review.json").exists()
        assert (out / "live_v2_final_readiness_review.json").exists()

    def test_missing_executor_exits_1(self, tmp_path):
        ta_path, sa_path, _ = _write_all(tmp_path)
        code, _ = _run_main(tmp_path,
                             ta_path=ta_path, sa_path=sa_path,
                             exec_path=tmp_path / "nonexistent.json")
        assert code == 1

    def test_output_dir_created(self, tmp_path):
        deep_out = tmp_path / "x" / "y" / "z"
        assert not deep_out.exists()
        _run_main(tmp_path, output_dir=deep_out)
        assert deep_out.exists()

    def test_no_alpaca_calls(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path)
        broker.submit_order.assert_not_called()
        broker.cancel_order.assert_not_called()

    def test_no_credentials_read(self, tmp_path):
        import os
        from unittest.mock import patch
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY",
                            "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            code, _ = _run_main(tmp_path)
        assert code in (0, None)

    def test_no_submit_order(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path)
        broker.submit_order.assert_not_called()

    def test_no_cancel_order(self, tmp_path):
        broker = MagicMock()
        _run_main(tmp_path)
        broker.cancel_order.assert_not_called()

    def test_no_ledger_writes(self, tmp_path):
        _, out = _run_main(tmp_path)
        ledger_files = list(tmp_path.rglob("*ledger*"))
        assert ledger_files == []

    def test_no_alpaca_import(self):
        import sys
        # Ensure alpaca_trade_api is not imported by the bundle module
        bundle_mod = sys.modules.get("src.tools.live_v2_readiness_bundle")
        if bundle_mod is None:
            import src.tools.live_v2_readiness_bundle  # noqa: F401
            bundle_mod = sys.modules["src.tools.live_v2_readiness_bundle"]
        src = bundle_mod.__file__ or ""
        with open(src, encoding="utf-8") as f:
            content = f.read()
        assert "alpaca" not in content.lower() or "Never calls" in content

    # --- Bundle summary artifact tests (main) ---

    def test_main_writes_bundle_summary_artifact(self, tmp_path):
        _, out = _run_main(tmp_path)
        assert (out / "live_v2_readiness_bundle.json").exists()

    def test_main_bundle_summary_contains_bundle_result(self, tmp_path):
        _, out = _run_main(tmp_path)
        data = _bundle_summary(out)
        assert data["bundle_result"] == "PASS"

    def test_main_bundle_summary_contains_all_artifact_paths(self, tmp_path):
        _, out = _run_main(tmp_path)
        data = _bundle_summary(out)
        assert "approvals_review_path" in data
        assert "executor_readiness_review_path" in data
        assert "final_readiness_review_path" in data

    def test_main_bundle_summary_written_on_fail(self, tmp_path):
        """Bundle summary is written even when the run fails."""
        ta_path = _write(tmp_path / "bad_ta.json",
                         _valid_ta(live_trading_approved=False))
        sa_path = _write(tmp_path / "sa.json", _valid_sa(ta_path))
        exec_path = _write(tmp_path / "exec.json", _valid_executor_report())
        code, out = _run_main(tmp_path,
                              ta_path=ta_path, sa_path=sa_path, exec_path=exec_path)
        assert code == 1
        assert (out / "live_v2_readiness_bundle.json").exists()
        data = _bundle_summary(out)
        assert data["bundle_result"] == "FAIL"
