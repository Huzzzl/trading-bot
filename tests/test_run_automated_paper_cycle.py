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
    a.submit_market_order.return_value = {
        "id": "alpaca-ord-1",
        "client_order_id": "cid-x",
        "symbol": "SPY",
        "side": "buy",
        "status": "new",
        "qty": 1.0,
    }
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
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


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
        # No idempotency file written.
        assert not (audit_dir / runner._IDEMPOTENCY_FILENAME).exists()

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
        # Idempotency key was persisted.
        keys_path = audit_dir / runner._IDEMPOTENCY_FILENAME
        assert keys_path.exists()
        assert rec["idempotency_key"] in keys_path.read_text()

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
        # Idempotency key NOT persisted on failure.
        keys_path = audit_dir / runner._IDEMPOTENCY_FILENAME
        assert not keys_path.exists()
        rec = _open_audit_lines(audit_dir)[0]
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
        raw = (audit_dir / f"{date_iso}.jsonl").read_text()
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


class TestNoLiveTrading:
    def test_module_source_has_no_live_path(self):
        import inspect
        src = inspect.getsource(runner)
        # No path that constructs a live adapter or bypasses paper=True.
        assert "paper=False" not in src
        assert "live_submit" not in src
        assert "AlpacaBrokerAdapter(" not in src or "live=" not in src
