"""Tests for the one-shot automated paper trading runner — S53.

Mock-adapter only; no real Alpaca calls, no Yahoo network.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.broker.alpaca_paper_adapter import AlpacaPaperAdapterError
from src.tools import run_automated_paper_cycle as runner


# Two fixed "now" values + matching cache fixtures:
#  * Saturday (market closed) — paired with the Friday final-bar label.
#  * Friday during market hours — paired with a bar 30 minutes before now.
_FIXED_NOW = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)  # Saturday
_FRI_FINAL_BAR = datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc)

_OPEN_NOW = datetime(2026, 6, 26, 18, 0, tzinfo=timezone.utc)  # Fri 14:00 ET
_OPEN_LATEST_BAR = datetime(2026, 6, 26, 17, 30, tzinfo=timezone.utc)


def _fixed_now():
    return _FIXED_NOW


def _open_now():
    return _OPEN_NOW


def _hourly_timestamps_ending(end: datetime, count: int) -> list[datetime]:
    return [end - timedelta(hours=i) for i in range(count - 1, -1, -1)]


def _write_friday_cache(cache_dir: Path, closes: list[float]) -> Path:
    """Write 20 hourly bars ending exactly at the Friday final-bar label."""
    ts = _hourly_timestamps_ending(_FRI_FINAL_BAR, len(closes))
    df = pd.DataFrame(
        [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in closes],
        index=pd.DatetimeIndex(ts, name="timestamp"),
    )
    path = cache_dir / "SPY_2026-01-01_2026-06-26_60m.csv"
    df.to_csv(path)
    return path


def _bullish_cache(cache_dir: Path) -> Path:
    return _write_friday_cache(cache_dir, [float(c) for c in range(100, 120)])


def _bearish_cache(cache_dir: Path) -> Path:
    return _write_friday_cache(cache_dir, [float(c) for c in range(120, 100, -1)])


def _flat_cache(cache_dir: Path) -> Path:
    return _write_friday_cache(cache_dir, [100.0] * 20)


def _write_open_cache(cache_dir: Path, closes: list[float]) -> Path:
    """Write 20 hourly bars ending at _OPEN_LATEST_BAR (suitable for the
    open-market 2h freshness window centered on _OPEN_NOW)."""
    ts = _hourly_timestamps_ending(_OPEN_LATEST_BAR, len(closes))
    df = pd.DataFrame(
        [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in closes],
        index=pd.DatetimeIndex(ts, name="timestamp"),
    )
    path = cache_dir / "SPY_open_60m.csv"
    df.to_csv(path)
    return path


def _bullish_open_cache(cache_dir: Path) -> Path:
    return _write_open_cache(cache_dir, [float(c) for c in range(100, 120)])


def _mock_adapter(
    *,
    clock_is_open: bool = False,
    positions=None, open_orders=None,
    next_open: str | None = "2026-06-29T13:30:00+00:00",
    next_close: str | None = "2026-06-29T20:00:00+00:00",
):
    a = MagicMock()
    a.get_clock.return_value = {
        "timestamp": "t", "is_open": clock_is_open,
        "next_open": next_open, "next_close": next_close,
    }
    a.get_account.return_value = {
        "status": "ACTIVE",
        "cash": 100000.0,
        "buying_power": 200000.0,
        "equity": 100000.0,
        "currency": "USD",
        "pattern_day_trader": False,
    }
    a.get_positions.return_value = positions or []
    a.get_open_orders.return_value = open_orders or []
    # S54 broker-state safety additions.
    a.get_position.return_value = None
    a.list_open_orders.return_value = [
        o for o in (open_orders or []) if isinstance(o, dict) and o.get("symbol") == "SPY"
    ]
    a.submit_market_order.return_value = {
        "id": "alpaca-ord-1",
        "client_order_id": "cid-x",
        "symbol": "SPY",
        "side": "buy",
        "status": "new",
        "qty": 1.0,
        "filled_qty": 0.0,
        "filled_avg_price": None,
    }
    # Immediate post-submit status lookup: mirror the submit response
    # so tests that don't override the status see a coherent "new"
    # order carried through to the audit log.
    a.get_order.return_value = {
        "id": "alpaca-ord-1",
        "client_order_id": "cid-x",
        "symbol": "SPY",
        "side": "buy",
        "status": "new",
        "qty": 1.0,
        "filled_qty": 0.0,
        "filled_avg_price": None,
    }
    a.get_order_by_client_order_id.return_value = None
    return a


def _good_fetch_result():
    return {
        "result": "PASS",
        "blocker": None,
        "entries": [{
            "symbol": "SPY", "interval": "60m", "status": "FETCHED",
            "rows": 20, "inferred_start": "2026-06-22", "inferred_end": "2026-06-26",
        }],
        "files_written": 1,
        "fetched_count": 1,
        "cache_hit_count": 0,
        "force_refresh": True,
        "availability_check_result": "PASS",
        "network_calls_made": True,
        "broker_calls_made": False,
        "credentials_read": False,
        "order_action_requested": False,
    }


def _failed_fetch_result():
    return {
        "result": "BLOCKED",
        "blocker": "yahoo rate limit",
        "entries": [{
            "symbol": "SPY", "interval": "60m", "status": "BLOCKED",
            "reason": "yahoo rate limit",
        }],
        "files_written": 0,
        "fetched_count": 0,
        "cache_hit_count": 0,
        "force_refresh": True,
        "availability_check_result": "BLOCKED",
        "network_calls_made": True,
        "broker_calls_made": False,
        "credentials_read": False,
        "order_action_requested": False,
    }


def _run(argv, *, adapter, fetch_result=None, audit_dir=None, cache_dir=None, now=_fixed_now):
    if fetch_result is None:
        fetch_result = _good_fetch_result()
    full = list(argv)
    if audit_dir is not None:
        full += ["--audit-dir", str(audit_dir)]
    if cache_dir is not None:
        full += ["--cache-dir", str(cache_dir)]
    stdout = io.StringIO()
    with patch.object(runner, "run_fetch", return_value=fetch_result) as fetch_mock, \
         patch.object(runner.AlpacaPaperAdapter, "from_environment", return_value=adapter), \
         patch.object(sys, "stdout", stdout):
        code = runner.main(full, now_utc_fn=now)
    return code, stdout.getvalue(), fetch_mock


def _run_open(argv, *, adapter, audit_dir=None, cache_dir=None, fetch_result=None):
    """_run with the Friday-market-hours fixed now."""
    return _run(argv, adapter=adapter, audit_dir=audit_dir,
                cache_dir=cache_dir, fetch_result=fetch_result, now=_open_now)


def _open_audit_lines(audit_dir: Path) -> list[dict]:
    return _audit_lines(audit_dir, now=_OPEN_NOW)


def _audit_lines(audit_dir: Path, now: datetime = _FIXED_NOW) -> list[dict]:
    date_iso = now.astimezone(timezone.utc).date().isoformat()
    path = audit_dir / f"{date_iso}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestFetchFailurePreventsCycle:
    def test_blocked_fetch_aborts(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        audit_dir = tmp_path / "audit"
        a = _mock_adapter()
        code, _, fetch_mock = _run(
            [], adapter=a, fetch_result=_failed_fetch_result(),
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        # Fetch was called
        assert fetch_mock.call_count == 1
        # Adapter never built, cycle never ran
        assert a.get_clock.call_count == 0
        assert a.submit_market_order.call_count == 0
        assert code == 1
        # Exactly one audit record
        lines = _audit_lines(audit_dir)
        assert len(lines) == 1
        rec = lines[0]
        assert rec["final_result"] == "BLOCKED"
        assert rec["fetch_result"] == "BLOCKED"
        assert "cache refresh" in rec["blocker"]
        assert rec["exit_code"] == 1

    def test_fetch_raises_returns_error(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        audit_dir = tmp_path / "audit"
        a = _mock_adapter()
        stdout = io.StringIO()
        with patch.object(runner, "run_fetch", side_effect=RuntimeError("net down")), \
             patch.object(runner.AlpacaPaperAdapter, "from_environment", return_value=a), \
             patch.object(sys, "stdout", stdout):
            code = runner.main(
                ["--cache-dir", str(cache_dir), "--audit-dir", str(audit_dir)],
                now_utc_fn=_fixed_now,
            )
        assert code == 2
        assert a.submit_market_order.call_count == 0
        lines = _audit_lines(audit_dir)
        assert len(lines) == 1
        assert lines[0]["final_result"] == "ERROR"
        assert lines[0]["exit_code"] == 2


class TestPassPathsZeroSubmissions:
    def test_hold_signal_no_submission(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _flat_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=False)
        code, _, _ = _run(
            [], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert a.submit_market_order.call_count == 0
        assert code == 0
        rec = _audit_lines(audit_dir)[0]
        assert rec["signal"] in ("HOLD", "BLOCK")
        assert rec["action"] == "none"
        assert rec["final_result"] == "PASS"

    def test_market_closed_no_submission(self, tmp_path):
        # Use a Saturday now → market is genuinely closed; signal engine
        # returns BLOCK from MARKET_NOT_OPEN.
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=False)
        code, _, _ = _run(
            [], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert a.submit_market_order.call_count == 0
        rec = _audit_lines(audit_dir)[0]
        assert rec["signal"] == "BLOCK"
        assert "MARKET_NOT_OPEN" in rec["reason_codes"]
        assert code == 0


class TestDryRunBuyPlan:
    def test_dry_run_no_submission(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        # Force the signal engine into the BUY path: market open clock.
        a = _mock_adapter(clock_is_open=True)
        # When market is open + bullish + no position → cycle returns
        # buy_planned in dry-run mode.
        code, _, _ = _run_open(
            ["--dry-run"], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert a.submit_market_order.call_count == 0
        assert code == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["action"] == "buy_planned"
        assert rec["order_plan"] is not None
        assert rec["order_plan"]["symbol"] == "SPY"
        # Idempotency key must NOT be set in dry-run mode.
        assert rec["idempotency_key"] is None
        assert rec["duplicate_prevented"] is False

    def test_dry_run_does_not_persist_key(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(["--dry-run"], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir)
        # Dry-run never creates a claim or submitted file.
        claims_dir = audit_dir / runner._CLAIMS_SUBDIR
        if claims_dir.exists():
            assert list(claims_dir.iterdir()) == []

    def test_default_is_dry_run(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # No --dry-run, no --submit-paper.
        code, out, _ = _run_open(
            [], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert "DRY_RUN" in out
        assert a.submit_market_order.call_count == 0
        assert code == 0


class TestPaperSubmit:
    def test_submit_paper_buy_submits_once(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert a.submit_market_order.call_count == 1
        assert code == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["action"] == "buy_submitted"
        assert rec["broker_order_id"] == "alpaca-ord-1"
        assert rec["idempotency_key"] is not None
        # Idempotency state transitioned to SUBMITTED.
        spath = runner._submitted_path(audit_dir, rec["idempotency_key"])
        assert spath.exists()
        # Claim file removed on successful finalization.
        cpath = runner._claim_path(audit_dir, rec["idempotency_key"])
        assert not cpath.exists()

    def test_submit_paper_passes_idempotency_as_client_order_id(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        kwargs = a.submit_market_order.call_args.kwargs
        # client_order_id was passed and equals the recorded idempotency key.
        rec = _open_audit_lines(audit_dir)[0]
        assert kwargs.get("client_order_id") == rec["idempotency_key"]


class TestIdempotency:
    def test_duplicate_key_blocks_second_submission(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"

        # First run — succeeds and persists the key.
        a1 = _mock_adapter(clock_is_open=True)
        code1, _, _ = _run_open(
            ["--submit-paper"], adapter=a1,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code1 == 0
        assert a1.submit_market_order.call_count == 1

        # Second run — same inputs → same idempotency key → BLOCKED.
        a2 = _mock_adapter(clock_is_open=True)
        code2, _, _ = _run_open(
            ["--submit-paper"], adapter=a2,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code2 == 1
        assert a2.submit_market_order.call_count == 0
        records = _open_audit_lines(audit_dir)
        assert len(records) == 2
        assert records[1]["duplicate_prevented"] is True
        assert records[1]["final_result"] == "BLOCKED"

    def test_failed_broker_submission_not_persisted_and_not_retried(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.submit_market_order.side_effect = AlpacaPaperAdapterError("broker rejected")
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        # Single submission attempt; no retry.
        assert a.submit_market_order.call_count == 1
        assert code == 2
        # SUBMITTED state never reached on broker failure.
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        assert key is not None
        assert not runner._submitted_path(audit_dir, key).exists()
        # "broker rejected" (no "submit_market_order failed:" prefix)
        # is classified as CONFIRMED_NOT_SUBMITTED → claim released.
        assert not runner._claim_path(audit_dir, key).exists()
        assert rec["final_result"] == "ERROR"


class TestMutuallyExclusiveFlags:
    def test_dry_run_and_submit_paper_mutually_exclusive(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        audit_dir = tmp_path / "audit"
        a = _mock_adapter()
        with pytest.raises(SystemExit):
            runner.main(
                ["--dry-run", "--submit-paper",
                 "--cache-dir", str(cache_dir),
                 "--audit-dir", str(audit_dir)],
                now_utc_fn=_fixed_now,
            )


class TestExactlyOneAuditRecordPerRun:
    @pytest.mark.parametrize("scenario", [
        "fetch_blocked", "hold", "buy_planned", "buy_submitted",
        "duplicate", "submission_failed",
    ])
    def test_one_record_per_scenario(self, tmp_path, scenario):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        audit_dir = tmp_path / "audit"
        if scenario == "fetch_blocked":
            a = _mock_adapter()
            _run([], adapter=a, fetch_result=_failed_fetch_result(),
                 audit_dir=audit_dir, cache_dir=cache_dir)
        elif scenario == "hold":
            _flat_cache(cache_dir)
            a = _mock_adapter(clock_is_open=False)
            _run([], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir)
        elif scenario == "buy_planned":
            _bullish_cache(cache_dir)
            a = _mock_adapter(clock_is_open=True)
            _run(["--dry-run"], adapter=a,
                 audit_dir=audit_dir, cache_dir=cache_dir)
        elif scenario == "buy_submitted":
            _bullish_cache(cache_dir)
            a = _mock_adapter(clock_is_open=True)
            _run(["--submit-paper"], adapter=a,
                 audit_dir=audit_dir, cache_dir=cache_dir)
        elif scenario == "duplicate":
            _bullish_cache(cache_dir)
            a1 = _mock_adapter(clock_is_open=True)
            _run(["--submit-paper"], adapter=a1,
                 audit_dir=audit_dir, cache_dir=cache_dir)
            a2 = _mock_adapter(clock_is_open=True)
            _run(["--submit-paper"], adapter=a2,
                 audit_dir=audit_dir, cache_dir=cache_dir)
            # Two runs → two records — verified below.
            lines = _audit_lines(audit_dir)
            assert len(lines) == 2
            return
        elif scenario == "submission_failed":
            _bullish_cache(cache_dir)
            a = _mock_adapter(clock_is_open=True)
            a.submit_market_order.side_effect = AlpacaPaperAdapterError("rejected")
            _run(["--submit-paper"], adapter=a,
                 audit_dir=audit_dir, cache_dir=cache_dir)
        lines = _audit_lines(audit_dir)
        assert len(lines) == 1


class TestAuditNoSecrets:
    def test_audit_log_does_not_contain_env_credentials(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "super-secret-key-do-not-leak",
            "ALPACA_SECRET_KEY": "super-secret-do-not-leak",
            "ALPACA_PAPER_BASE_URL": "https://paper-api.alpaca.markets",
        }, clear=False):
            _run_open(["--submit-paper"], adapter=a,
                 audit_dir=audit_dir, cache_dir=cache_dir)
        date_iso = _OPEN_NOW.date().isoformat()
        raw = (audit_dir / f"{date_iso}.jsonl").read_text(encoding="utf-8")
        assert "super-secret-key-do-not-leak" not in raw
        assert "super-secret-do-not-leak" not in raw

    def test_audit_record_has_no_forbidden_keys(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(["--submit-paper"], adapter=a,
             audit_dir=audit_dir, cache_dir=cache_dir)
        rec = _open_audit_lines(audit_dir)[0]
        forbidden = {
            "api_key", "secret_key", "secret", "token",
            "password", "authorization", "credentials",
            "account_number", "account_id",
            "env", "environment",
        }
        keys_lower = {k.lower() for k in rec}
        assert keys_lower & forbidden == set()


class TestAtMostOneSubmissionPerInvocation:
    def test_buy_path_calls_submit_at_most_once(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(["--submit-paper"], adapter=a,
             audit_dir=audit_dir, cache_dir=cache_dir)
        assert a.submit_market_order.call_count == 1

    def test_submission_failure_no_retry(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.submit_market_order.side_effect = AlpacaPaperAdapterError("rate limit")
        _run_open(["--submit-paper"], adapter=a,
             audit_dir=audit_dir, cache_dir=cache_dir)
        assert a.submit_market_order.call_count == 1


class TestExitCodes:
    def test_zero_on_dry_run_buy_plan(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        code, _, _ = _run_open(
            ["--dry-run"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0

    def test_zero_on_no_action_hold(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _flat_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=False)
        code, _, _ = _run(
            [], adapter=a, audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0

    def test_zero_on_paper_submit_success(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0

    def test_one_on_fetch_blocked(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        audit_dir = tmp_path / "audit"
        a = _mock_adapter()
        code, _, _ = _run(
            [], adapter=a, fetch_result=_failed_fetch_result(),
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 1

    def test_one_on_duplicate(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a1 = _mock_adapter(clock_is_open=True)
        _run(["--submit-paper"], adapter=a1,
             audit_dir=audit_dir, cache_dir=cache_dir)
        a2 = _mock_adapter(clock_is_open=True)
        code, _, _ = _run(
            ["--submit-paper"], adapter=a2,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 1

    def test_two_on_adapter_init_error(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        stdout = io.StringIO()
        with patch.object(runner, "run_fetch", return_value=_good_fetch_result()), \
             patch.object(
                 runner.AlpacaPaperAdapter, "from_environment",
                 side_effect=AlpacaPaperAdapterError("missing key"),
             ), \
             patch.object(sys, "stdout", stdout):
            code = runner.main(
                ["--cache-dir", str(cache_dir), "--audit-dir", str(audit_dir)],
                now_utc_fn=_fixed_now,
            )
        assert code == 2

    def test_two_on_submission_error(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.submit_market_order.side_effect = AlpacaPaperAdapterError("rejected")
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 2


class TestIdempotencyHardening:
    """Per-key claim/state semantics and concurrent-safety."""

    # ---- (1) two concurrent runners ----

    def test_concurrent_runners_produce_exactly_one_broker_call(self, tmp_path):
        import threading
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        # Both threads use independent adapters so we can sum the call
        # counts and prove only one broker call happens overall.
        a1 = _mock_adapter(clock_is_open=True)
        a2 = _mock_adapter(clock_is_open=True)

        # Gate both threads so they enter the claim race at the same time.
        gate = threading.Barrier(2)

        def make_factory(adapter):
            def _factory():
                gate.wait()
                return adapter
            return _factory

        codes = {}

        def run(idx, adapter):
            try:
                with patch.object(runner, "run_fetch", return_value=_good_fetch_result()), \
                     patch.object(
                         runner.AlpacaPaperAdapter,
                         "from_environment",
                         side_effect=make_factory(adapter),
                     ), \
                     patch.object(sys, "stdout", io.StringIO()):
                    codes[idx] = runner.main(
                        ["--submit-paper",
                         "--audit-dir", str(audit_dir),
                         "--cache-dir", str(cache_dir)],
                        now_utc_fn=_open_now,
                    )
            except BaseException as exc:  # noqa: BLE001
                codes[idx] = exc

        t1 = threading.Thread(target=run, args=(1, a1))
        t2 = threading.Thread(target=run, args=(2, a2))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # Exactly one broker call across both adapters.
        total_calls = a1.submit_market_order.call_count + a2.submit_market_order.call_count
        assert total_calls == 1
        # One PASS and one BLOCKED exit code (or the BLOCKED side surfaces
        # ERROR if the broker call raised in the winner — but the winner
        # in this fixture succeeds).
        result_codes = sorted([codes[1], codes[2]])
        assert 0 in result_codes
        assert 1 in result_codes
        # Both invocations wrote audit records.
        recs = _open_audit_lines(audit_dir)
        assert len(recs) == 2

    # ---- (2) unreadable idempotency state ----

    def test_unreadable_state_blocks_with_zero_submissions(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        with patch.object(
            runner, "_read_idempotency_state",
            side_effect=runner._IdempotencyError("OSError"),
        ):
            code, _, _ = _run_open(
                ["--submit-paper"], adapter=a,
                audit_dir=audit_dir, cache_dir=cache_dir,
            )
        assert code == 1
        assert a.submit_market_order.call_count == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["final_result"] == "BLOCKED"
        assert "idempotency state unreadable" in rec["blocker"]

    # ---- (3) corrupt state ----

    def test_corrupt_claim_file_blocks_with_zero_submissions(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        # Pre-compute the key and pre-create a corrupt claim file.
        latest_ts = _OPEN_LATEST_BAR
        key = runner._idempotency_key(
            symbol="SPY", interval="60m",
            latest_ts=latest_ts, signal_side="buy",
            session_date=runner._session_date(latest_ts),
        )
        cpath = runner._claim_path(audit_dir, key)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        # Bytes that fail UTF-8 decode.
        cpath.write_bytes(b"\xff\xfe\xfd corrupt")
        a = _mock_adapter(clock_is_open=True)
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        # Either treated as unreadable (corrupt) or as an existing claim;
        # either way blocks with zero submissions.
        assert code == 1
        assert a.submit_market_order.call_count == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["final_result"] == "BLOCKED"

    # ---- (4) claim creation failure ----

    def test_claim_creation_failure_blocks(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        with patch.object(
            runner, "_try_claim",
            side_effect=runner._IdempotencyError("disk full"),
        ):
            code, _, _ = _run_open(
                ["--submit-paper"], adapter=a,
                audit_dir=audit_dir, cache_dir=cache_dir,
            )
        assert code == 1
        assert a.submit_market_order.call_count == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["final_result"] == "BLOCKED"
        assert rec["duplicate_prevented"] is True
        assert "could not claim idempotency" in rec["blocker"]

    # ---- (5) confirmed broker failure releases claim ----

    def test_confirmed_broker_failure_releases_claim(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # No "submit_market_order failed:" prefix → confirmed pre-broker.
        a.submit_market_order.side_effect = AlpacaPaperAdapterError(
            "qty must be a positive number",
        )
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 2
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        assert not runner._claim_path(audit_dir, key).exists()
        assert not runner._submitted_path(audit_dir, key).exists()
        assert "claim released" in rec["blocker"]

    # ---- (6) ambiguous broker failure retains claim ----

    def test_ambiguous_broker_failure_retains_claim(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # "submit_market_order failed:" prefix → ambiguous.
        a.submit_market_order.side_effect = AlpacaPaperAdapterError(
            "submit_market_order failed: connection timeout",
        )
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 2
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        # Claim retained for manual reconciliation; never marked SUBMITTED.
        assert runner._claim_path(audit_dir, key).exists()
        assert not runner._submitted_path(audit_dir, key).exists()
        assert "manual reconciliation" in rec["blocker"]

        # Second run with the same inputs must block without calling the broker.
        a2 = _mock_adapter(clock_is_open=True)
        code2, _, _ = _run_open(
            ["--submit-paper"], adapter=a2,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code2 == 1
        assert a2.submit_market_order.call_count == 0
        rec2 = _open_audit_lines(audit_dir)[1]
        assert rec2["duplicate_prevented"] is True
        assert "claim already exists" in rec2["blocker"]

    # ---- (7) successful submission CLAIMED -> SUBMITTED ----

    def test_successful_submission_transitions_claimed_to_submitted(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        assert runner._submitted_path(audit_dir, key).exists()
        assert not runner._claim_path(audit_dir, key).exists()
        # File contents include the SUBMITTED marker.
        body = runner._submitted_path(audit_dir, key).read_text(encoding="utf-8")
        assert body.startswith("SUBMITTED\n")

    # ---- (8) failure to finalize SUBMITTED does not allow a second submission ----

    def test_finalize_failure_blocks_second_submission(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        with patch.object(runner, "_finalize_submitted", return_value=False):
            code, _, _ = _run_open(
                ["--submit-paper"], adapter=a,
                audit_dir=audit_dir, cache_dir=cache_dir,
            )
        assert code == 0  # broker accepted; final result is still PASS
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        assert "finalization failed" in rec["blocker"]
        # Claim still exists (was not transitioned). Next run must block.
        key = rec["idempotency_key"]
        assert runner._claim_path(audit_dir, key).exists()

        # Second run with identical inputs blocks before submission.
        a2 = _mock_adapter(clock_is_open=True)
        code2, _, _ = _run_open(
            ["--submit-paper"], adapter=a2,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code2 == 1
        assert a2.submit_market_order.call_count == 0
        rec2 = _open_audit_lines(audit_dir)[1]
        assert rec2["duplicate_prevented"] is True

    # ---- (9) dry-run creates no claim ----

    def test_dry_run_creates_no_claim_file(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(["--dry-run"], adapter=a,
                  audit_dir=audit_dir, cache_dir=cache_dir)
        claims_dir = audit_dir / runner._CLAIMS_SUBDIR
        if claims_dir.exists():
            assert list(claims_dir.iterdir()) == []

    # ---- (10) audit indicates duplicate/claim/persistence status safely ----

    def test_audit_indicates_persistence_failure(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        with patch.object(runner, "_finalize_submitted", return_value=False):
            _run_open(["--submit-paper"], adapter=a,
                      audit_dir=audit_dir, cache_dir=cache_dir)
        rec = _open_audit_lines(audit_dir)[0]
        # Persistence failure surfaced in the audit blocker text without
        # leaking implementation paths.
        assert "finalization failed" in rec["blocker"]
        assert "claim retained" in rec["blocker"]
        # Sensitive markers absent.
        for forbidden in ("api_key", "secret", "/home/", "C:\\Users\\"):
            assert forbidden not in rec["blocker"]

    def test_audit_indicates_duplicate_block(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        # First run succeeds.
        a1 = _mock_adapter(clock_is_open=True)
        _run_open(["--submit-paper"], adapter=a1,
                  audit_dir=audit_dir, cache_dir=cache_dir)
        # Second run blocks as duplicate.
        a2 = _mock_adapter(clock_is_open=True)
        _run_open(["--submit-paper"], adapter=a2,
                  audit_dir=audit_dir, cache_dir=cache_dir)
        rec2 = _open_audit_lines(audit_dir)[1]
        assert rec2["duplicate_prevented"] is True
        assert "already submitted" in rec2["blocker"]
        assert a2.submit_market_order.call_count == 0


class TestPreSubmitBrokerStateChecks:
    """S54: broker state is source of truth before BUY submission."""

    def _position(self, qty=5.0, side="long"):
        return {
            "symbol": "SPY", "qty": qty, "side": side,
            "avg_entry_price": 100.0, "market_value": qty * 100.0,
            "unrealized_pl": 0.0, "current_price": 100.0,
        }

    def _open_buy_order(self, order_id="pending-1"):
        return {
            "id": order_id, "client_order_id": "prev-cid",
            "symbol": "SPY", "side": "buy",
            "type": "market", "time_in_force": "day",
            "qty": 1.0, "filled_qty": 0.0, "filled_avg_price": None,
            "status": "new", "submitted_at": None, "filled_at": None,
        }

    def test_existing_position_blocks_buy(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # Broker state: SPY position exists at 5 shares.
        a.get_position.return_value = self._position(qty=5.0)
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0  # safely no-action
        assert a.submit_market_order.call_count == 0
        # Claim was never created.
        claims_dir = audit_dir / runner._CLAIMS_SUBDIR
        if claims_dir.exists():
            assert list(claims_dir.iterdir()) == []
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["action"] == "none"
        assert rec["broker_position_qty"] == 5.0
        assert "POSITION_ALREADY_EXISTS" in rec["reason_codes"]

    def test_existing_open_buy_order_blocks_buy(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # No position, but an open BUY order already exists.
        a.list_open_orders.return_value = [self._open_buy_order()]
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0
        assert a.submit_market_order.call_count == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["action"] == "none"
        assert rec["broker_open_buy_order_count"] == 1
        assert "OPEN_BUY_ORDER_ALREADY_EXISTS" in rec["reason_codes"]

    def test_no_position_and_no_open_order_allows_submit(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["action"] == "buy_submitted"
        assert rec["broker_position_qty"] == 0.0
        assert rec["broker_open_buy_order_count"] == 0

    def test_position_check_error_returns_error(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.get_position.side_effect = AlpacaPaperAdapterError("net down")
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 2
        assert a.submit_market_order.call_count == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert "pre-submit position check failed" in rec["blocker"]

    def test_non_buy_open_orders_do_not_block(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # An OPEN sell (or unrelated) order must NOT count as a duplicate BUY.
        sell_order = dict(self._open_buy_order(), side="sell")
        a.list_open_orders.return_value = [sell_order]
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["broker_open_buy_order_count"] == 0
        assert rec["action"] == "buy_submitted"


class TestPostSubmitStatusAudit:
    """S54: post-submit status lookup + reason-code mapping."""

    def _order_response(self, **overrides):
        base = {
            "id": "alpaca-ord-1",
            "client_order_id": "cid-x",
            "symbol": "SPY", "side": "buy",
            "type": "market", "time_in_force": "day",
            "qty": 1.0, "filled_qty": 0.0, "filled_avg_price": None,
            "status": "new", "submitted_at": None, "filled_at": None,
        }
        base.update(overrides)
        return base

    def test_filled_status_audited(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.get_order.return_value = self._order_response(
            status="filled", filled_qty=1.0, filled_avg_price=110.5,
        )
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 0
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["order_status"] == "filled"
        assert rec["filled_qty"] == 1.0
        assert rec["filled_avg_price"] == 110.5
        assert rec["submitted_qty"] == 1.0
        assert "ORDER_FILLED" in rec["reason_codes"]

    def test_partially_filled_status_audited(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.get_order.return_value = self._order_response(
            status="partially_filled", filled_qty=0.5, filled_avg_price=110.0,
        )
        _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["order_status"] == "partially_filled"
        assert rec["filled_qty"] == 0.5
        assert "ORDER_PARTIALLY_FILLED" in rec["reason_codes"]

    def test_rejected_status_audited(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.get_order.return_value = self._order_response(status="rejected")
        _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["order_status"] == "rejected"
        assert "ORDER_REJECTED" in rec["reason_codes"]

    def test_new_status_maps_to_order_submitted(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # Default mock returns status="new".
        _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["order_status"] == "new"
        assert "ORDER_SUBMITTED" in rec["reason_codes"]

    def test_status_lookup_failure_preserves_claim(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.get_order.side_effect = AlpacaPaperAdapterError("timeout")
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        # Submitted but status unknown → ERROR exit 2, claim retained.
        assert code == 2
        assert a.submit_market_order.call_count == 1
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        assert runner._claim_path(audit_dir, key).exists()
        assert not runner._submitted_path(audit_dir, key).exists()
        assert "ORDER_STATUS_UNKNOWN" in rec["reason_codes"]
        assert "status lookup failed" in rec["blocker"]

    def test_client_order_id_recorded_in_audit(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["client_order_id"] is not None
        assert rec["client_order_id"] == rec["idempotency_key"]


class TestSubmitTimeoutReconciliation:
    """S54: ambiguous submit failures attempt reconciliation."""

    def _order_response(self, **overrides):
        base = {
            "id": "alpaca-ord-9",
            "client_order_id": None,
            "symbol": "SPY", "side": "buy",
            "type": "market", "time_in_force": "day",
            "qty": 1.0, "filled_qty": 0.0, "filled_avg_price": None,
            "status": "new", "submitted_at": None, "filled_at": None,
        }
        base.update(overrides)
        return base

    def test_timeout_reconciles_successfully_via_client_order_id(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.submit_market_order.side_effect = AlpacaPaperAdapterError(
            "submit_market_order failed: connection timeout",
        )
        # Reconciliation lookup returns a matching order.
        a.get_order_by_client_order_id.return_value = self._order_response(
            id="reconciled-1", status="accepted",
        )
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        # Ambiguous — claim retained, ERROR exit 2, reconciliation
        # recorded the recovered order details.
        assert code == 2
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        assert runner._claim_path(audit_dir, key).exists()
        assert not runner._submitted_path(audit_dir, key).exists()
        assert rec["reconciliation_attempted"] is True
        assert rec["reconciliation_result"] == "found"
        assert rec["broker_order_id"] == "reconciled-1"
        assert rec["order_status"] == "accepted"
        assert "ORDER_SUBMITTED" in rec["reason_codes"]
        # Reconciliation was called exactly once with the idempotency key.
        assert a.get_order_by_client_order_id.call_count == 1
        args, _ = a.get_order_by_client_order_id.call_args
        assert args[0] == key

    def test_timeout_reconcile_not_found_keeps_claim(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.submit_market_order.side_effect = AlpacaPaperAdapterError(
            "submit_market_order failed: connection timeout",
        )
        a.get_order_by_client_order_id.return_value = None
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 2
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        assert runner._claim_path(audit_dir, key).exists()
        assert rec["reconciliation_attempted"] is True
        assert rec["reconciliation_result"] == "not_found"
        assert "ORDER_STATUS_UNKNOWN" in rec["reason_codes"]

    def test_timeout_reconcile_lookup_failure_keeps_claim(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        a.submit_market_order.side_effect = AlpacaPaperAdapterError(
            "submit_market_order failed: connection timeout",
        )
        a.get_order_by_client_order_id.side_effect = AlpacaPaperAdapterError(
            "reconcile network error",
        )
        code, _, _ = _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        assert code == 2
        rec = _open_audit_lines(audit_dir)[0]
        key = rec["idempotency_key"]
        # Claim retained; state never marked SUBMITTED.
        assert runner._claim_path(audit_dir, key).exists()
        assert not runner._submitted_path(audit_dir, key).exists()
        assert rec["reconciliation_attempted"] is True
        assert rec["reconciliation_result"] is not None
        assert rec["reconciliation_result"].startswith("lookup_failed:")
        assert "ORDER_STATUS_UNKNOWN" in rec["reason_codes"]

    def test_confirmed_failure_no_reconciliation(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        # No "submit_market_order failed:" marker → CONFIRMED_NOT_SUBMITTED
        # → claim released, no reconciliation attempted.
        a.submit_market_order.side_effect = AlpacaPaperAdapterError(
            "qty must be a positive number",
        )
        _run_open(
            ["--submit-paper"], adapter=a,
            audit_dir=audit_dir, cache_dir=cache_dir,
        )
        rec = _open_audit_lines(audit_dir)[0]
        assert rec["reconciliation_attempted"] is False
        assert a.get_order_by_client_order_id.call_count == 0


class TestDryRunNoSubmitOnlyBrokerQueries:
    """Dry-run does not need the pre-submit position or open-orders
    queries required only for submission."""

    def test_dry_run_does_not_call_get_position(self, tmp_path):
        cache_dir = tmp_path / "cache"; cache_dir.mkdir()
        _bullish_open_cache(cache_dir)
        audit_dir = tmp_path / "audit"
        a = _mock_adapter(clock_is_open=True)
        _run_open(["--dry-run"], adapter=a,
                  audit_dir=audit_dir, cache_dir=cache_dir)
        # get_position is a S54 submit-only precheck.
        assert a.get_position.call_count == 0
        assert a.list_open_orders.call_count == 0
        assert a.submit_market_order.call_count == 0


class TestNoLiveTrading:
    def test_module_source_has_no_live_path(self):
        import inspect
        src = inspect.getsource(runner)
        # No path that constructs a live adapter or bypasses paper=True.
        assert "paper=False" not in src
        assert "live_submit" not in src
        assert "AlpacaBrokerAdapter(" not in src or "live=" not in src
