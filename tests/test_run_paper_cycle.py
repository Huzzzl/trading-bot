"""Tests for the runnable Alpaca paper cycle CLI — S52.

Mock-adapter only; never makes a real Alpaca call or a network fetch.
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
from src.tools import run_paper_cycle as cli


# Fixed "now" used by all tests so cache fixtures stay deterministic.
_FIXED_NOW = datetime(2026, 6, 23, 14, 30, tzinfo=timezone.utc)


def _fixed_now():
    return _FIXED_NOW


def _timestamps_ending(end: datetime, count: int) -> list[datetime]:
    """Hourly timestamps ending at *end* (latest bar is exactly *end*)."""
    return [end - timedelta(hours=i) for i in range(count - 1, -1, -1)]


def _write_cache_csv(cache_dir: Path, closes: list[float], *, end: datetime | None = None) -> Path:
    if end is None:
        # Latest bar is 1 hour before fixed now — well within freshness threshold.
        end = _FIXED_NOW - timedelta(hours=1)
    ts = _timestamps_ending(end, len(closes))
    rows = []
    for c in closes:
        rows.append({
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        })
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(ts, name="timestamp"))
    path = cache_dir / "SPY_2026-01-01_2026-06-01_60m.csv"
    df.to_csv(path)
    return path


def _write_bullish_cache(cache_dir: Path) -> Path:
    return _write_cache_csv(cache_dir, [float(c) for c in range(100, 120)])


def _write_bearish_cache(cache_dir: Path) -> Path:
    return _write_cache_csv(cache_dir, [float(c) for c in range(120, 100, -1)])


def _write_flat_cache(cache_dir: Path) -> Path:
    return _write_cache_csv(cache_dir, [100.0] * 20)


def _mock_adapter(*, clock_open=True, positions=None, open_orders=None):
    a = MagicMock()
    a.get_clock.return_value = {
        "timestamp": "t",
        "is_open": clock_open,
        "next_open": None,
        "next_close": None,
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
        "id": "ord-x",
        "client_order_id": "cid-x",
        "symbol": "SPY",
        "side": "buy",
        "status": "new",
        "qty": 1.0,
    }
    return a


def _run_cli(argv, *, adapter, cache_dir: Path, now_utc_fn=_fixed_now):
    full_argv = list(argv) + ["--cache-dir", str(cache_dir)]
    stdout = io.StringIO()
    with patch.object(
        cli.AlpacaPaperAdapter, "from_environment", return_value=adapter,
    ), patch.object(sys, "stdout", stdout):
        code = cli.main(full_argv, now_utc_fn=now_utc_fn)
    return code, stdout.getvalue()


def _parse_result(out: str) -> dict:
    # Output contains "mode: ..." then a JSON line
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    json_line = lines[-1]
    return json.loads(json_line)


class TestDryRunDefault:
    def test_no_submit_default(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert a.submit_market_order.call_count == 0
        assert "DRY_RUN" in out
        result = _parse_result(out)
        assert result["action"] == "buy_planned"
        assert code == 0

    def test_explicit_submit_paper_allows_one_submission(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        assert a.submit_market_order.call_count == 1
        assert "PAPER_SUBMIT" in out
        result = _parse_result(out)
        assert result["action"] == "buy_submitted"
        assert code == 0


class TestBuyDryRun:
    def test_buy_planned_returns_correct_qty(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        # equity=100000 * 0.10 / latest_close(119) = 84
        assert result["action"] == "buy_planned"
        assert result["order_plan"]["symbol"] == "SPY"
        assert result["order_plan"]["side"] == "buy"
        assert result["order_plan"]["type"] == "market"
        assert result["order_plan"]["qty"] == 84
        assert code == 0

    def test_smaller_fraction_yields_smaller_qty(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli(
            ["--max-position-fraction", "0.05"], adapter=a, cache_dir=tmp_path,
        )
        result = _parse_result(out)
        # equity=100000 * 0.05 / 119 = 42
        assert result["order_plan"]["qty"] == 42
        assert code == 0


class TestSellDryRun:
    def test_sell_planned_uses_full_held_qty(self, tmp_path):
        _write_bearish_cache(tmp_path)
        a = _mock_adapter(positions=[{
            "symbol": "SPY", "qty": 13, "side": "long",
            "avg_entry_price": 100.0, "market_value": 1430.0,
            "unrealized_pl": 0.0, "current_price": 110.0,
        }])
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["action"] == "sell_planned"
        assert result["order_plan"]["side"] == "sell"
        assert result["order_plan"]["qty"] == 13.0
        assert a.submit_market_order.call_count == 0
        assert code == 0


class TestHoldOrBlockNoPlan:
    def test_hold_no_plan(self, tmp_path):
        _write_flat_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["action"] == "none"
        assert result["order_plan"] is None
        assert result["order"] is None
        assert a.submit_market_order.call_count == 0
        assert code == 0

    def test_block_market_closed_no_plan(self, tmp_path):
        # Saturday now with Friday-final-bar cache → freshness passes
        # (Friday is the most recent completed NYSE session); cycle's
        # signal engine then returns BLOCK with MARKET_NOT_OPEN.
        sat_now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        fri_end = datetime(2026, 6, 26, 19, 0, tzinfo=timezone.utc)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=fri_end)
        a = _mock_adapter(clock_open=False)
        code, out = _run_cli(
            [], adapter=a, cache_dir=tmp_path,
            now_utc_fn=lambda: sat_now,
        )
        result = _parse_result(out)
        assert result["action"] == "none"
        assert result["order_plan"] is None
        assert result["signal"] == "BLOCK"
        assert "MARKET_NOT_OPEN" in result["reason_codes"]
        assert code == 0


class TestInvalidOrMissingCache:
    def test_missing_cache_dir_blocks(self, tmp_path):
        # Use a non-existent subdirectory
        missing = tmp_path / "missing"
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=missing)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "no cached bars" in result["blocker"]
        assert code == 1
        assert a.submit_market_order.call_count == 0

    def test_empty_cache_dir_blocks(self, tmp_path):
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert code == 1
        assert a.submit_market_order.call_count == 0

    def test_cache_file_missing_columns_blocks(self, tmp_path):
        df = pd.DataFrame({"foo": [1, 2, 3]})
        (tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv").write_text(
            df.to_csv(),
        )
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "missing required columns" in result["blocker"]
        assert code == 1


class TestMissingCredentialsReturnError:
    def test_missing_credentials_returns_error_cleanly(self, tmp_path):
        _write_bullish_cache(tmp_path)

        def _factory_raises():
            raise AlpacaPaperAdapterError(
                "missing ALPACA_API_KEY or ALPACA_SECRET_KEY in environment"
            )

        stdout = io.StringIO()
        with patch.object(
            cli.AlpacaPaperAdapter, "from_environment", side_effect=_factory_raises,
        ), patch.object(sys, "stdout", stdout):
            code = cli.main(["--cache-dir", str(tmp_path)])
        out = stdout.getvalue()
        result = _parse_result(out)
        assert result["result"] == "ERROR"
        assert code == 2
        assert "adapter init failed" in result["blocker"]


class TestJsonOutputNoCredentials:
    def test_output_dict_has_no_credential_keys(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        _, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        forbidden = {
            "api_key", "secret_key", "secret", "token",
            "password", "authorization", "credentials",
        }
        keys_lower = {k.lower() for k in result.keys()}
        assert keys_lower & forbidden == set()

    def test_output_text_has_no_credential_strings(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "real-key-do-not-print",
            "ALPACA_SECRET_KEY": "real-secret-do-not-print",
        }, clear=False):
            _, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert "real-key-do-not-print" not in out
        assert "real-secret-do-not-print" not in out
        assert "ALPACA_API_KEY=" not in out
        assert "ALPACA_SECRET_KEY=" not in out


class TestExitCodes:
    def test_exit_zero_on_pass(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, _ = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert code == 0

    def test_exit_one_on_blocked(self, tmp_path):
        a = _mock_adapter()
        code, _ = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert code == 1

    def test_exit_two_on_error(self, tmp_path):
        _write_bullish_cache(tmp_path)
        stdout = io.StringIO()
        with patch.object(
            cli.AlpacaPaperAdapter, "from_environment",
            side_effect=AlpacaPaperAdapterError("missing key"),
        ), patch.object(sys, "stdout", stdout):
            code = cli.main(["--cache-dir", str(tmp_path)])
        assert code == 2

    def test_exit_two_on_account_exception(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        a.get_account.side_effect = AlpacaPaperAdapterError("net down")
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "ERROR"
        assert code == 2


class TestCachedDataOnly:
    def test_no_yahoo_or_network_fetch(self):
        import inspect
        src = inspect.getsource(cli)
        for forbidden in [
            "yfinance", "yahoo", "requests", "urllib", "aiohttp",
            "socket", "YahooDataProvider", "fetch_yahoo",
        ]:
            assert forbidden not in src, f"forbidden pattern '{forbidden}'"

    def test_no_logging_or_print_credentials(self):
        import inspect
        src = inspect.getsource(cli)
        assert "logging" not in src
        assert "log" + "ger" not in src


class TestExactlyOneSubmitInPaperMode:
    def test_paper_submit_buys_exactly_once(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        assert a.submit_market_order.call_count == 1

    def test_paper_submit_sells_exactly_once(self, tmp_path):
        _write_bearish_cache(tmp_path)
        a = _mock_adapter(positions=[{
            "symbol": "SPY", "qty": 4, "side": "long",
            "avg_entry_price": 100.0, "market_value": 440.0,
            "unrealized_pl": 0.0, "current_price": 110.0,
        }])
        a.submit_market_order.return_value = {
            "id": "s1", "symbol": "SPY", "side": "sell",
            "qty": 4.0, "status": "new",
        }
        _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        assert a.submit_market_order.call_count == 1


class TestNoSubmitInEveryDryRunCase:
    @pytest.mark.parametrize("scenario", ["bullish", "bearish", "flat", "closed"])
    def test_dry_run_never_submits(self, tmp_path, scenario):
        if scenario == "bullish":
            _write_bullish_cache(tmp_path)
            positions = []
        elif scenario == "bearish":
            _write_bearish_cache(tmp_path)
            positions = [{
                "symbol": "SPY", "qty": 5, "side": "long",
                "avg_entry_price": 100.0, "market_value": 550.0,
                "unrealized_pl": 0.0, "current_price": 110.0,
            }]
        elif scenario == "flat":
            _write_flat_cache(tmp_path)
            positions = []
        else:
            _write_bullish_cache(tmp_path)
            positions = []
        a = _mock_adapter(
            clock_open=False if scenario == "closed" else True,
            positions=positions,
        )
        _run_cli([], adapter=a, cache_dir=tmp_path)
        assert a.submit_market_order.call_count == 0


class TestStaleCacheBlocks:
    def test_stale_cache_blocks_dry_run(self, tmp_path):
        # Latest bar is 10 hours old vs 4-hour threshold.
        stale_end = _FIXED_NOW - timedelta(hours=10)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=stale_end)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "stale" in result["blocker"]
        assert code == 1
        assert a.submit_market_order.call_count == 0
        # No bar prices in the blocker text.
        for raw_price in ("100.0", "119.0", "100.00", "119.00"):
            assert raw_price not in result["blocker"]

    def test_stale_cache_blocks_paper_submit(self, tmp_path):
        stale_end = _FIXED_NOW - timedelta(hours=10)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=stale_end)
        a = _mock_adapter()
        code, out = _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "stale" in result["blocker"]
        assert a.submit_market_order.call_count == 0
        assert code == 1

    def test_future_latest_bar_blocks(self, tmp_path):
        future_end = _FIXED_NOW + timedelta(hours=2)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=future_end)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "future" in result["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_recent_cache_passes_dry_run(self, tmp_path):
        # Default _FIXED_NOW - 1h latest bar — well within threshold.
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["action"] == "buy_planned"
        assert code == 0
        assert a.submit_market_order.call_count == 0

    def test_recent_cache_plus_submit_paper_allows_one_submission(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, out = _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["action"] == "buy_submitted"
        assert code == 0
        assert a.submit_market_order.call_count == 1

    def test_stale_blocker_does_not_print_credentials(self, tmp_path):
        stale_end = _FIXED_NOW - timedelta(hours=24)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=stale_end)
        a = _mock_adapter()
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "secret-key-do-not-leak",
            "ALPACA_SECRET_KEY": "secret-do-not-leak",
        }, clear=False):
            _, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert "secret-key-do-not-leak" not in out
        assert "secret-do-not-leak" not in out


class TestMalformedRowsBlock:
    def _write_malformed_csv(self, cache_dir: Path, rows: list[dict],
                              *, end: datetime | None = None) -> Path:
        if end is None:
            end = _FIXED_NOW - timedelta(hours=1)
        ts = _timestamps_ending(end, len(rows))
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(ts, name="timestamp"))
        path = cache_dir / "SPY_2026-01-01_2026-06-01_60m.csv"
        df.to_csv(path)
        return path

    def test_malformed_final_row_blocks(self, tmp_path):
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 119)]
        rows.append({  # bogus final row
            "open": "not-a-number", "high": "x", "low": "y",
            "close": "z", "volume": "w",
        })
        self._write_malformed_csv(tmp_path, rows)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "malformed" in result["blocker"]
        assert code == 1
        assert a.submit_market_order.call_count == 0

    def test_malformed_middle_row_blocks(self, tmp_path):
        rows = []
        for i, c in enumerate(range(100, 120)):
            if i == 10:
                rows.append({
                    "open": "garbage", "high": "x", "low": "y",
                    "close": "z", "volume": "w",
                })
            else:
                rows.append({
                    "open": float(c), "high": float(c), "low": float(c),
                    "close": float(c), "volume": 1000.0,
                })
        self._write_malformed_csv(tmp_path, rows)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "malformed" in result["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_non_finite_value_blocks(self, tmp_path):
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 119)]
        rows.append({
            "open": 119.0, "high": float("inf"), "low": 119.0,
            "close": 119.0, "volume": 1000.0,
        })
        self._write_malformed_csv(tmp_path, rows)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "non-finite" in result["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_malformed_row_blocks_paper_submit_too(self, tmp_path):
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 119)]
        rows.append({
            "open": "x", "high": "x", "low": "x", "close": "x", "volume": "x",
        })
        self._write_malformed_csv(tmp_path, rows)
        a = _mock_adapter()
        code, out = _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0


class TestTimestampValidation:
    def test_unsorted_timestamps_block(self, tmp_path):
        end = _FIXED_NOW - timedelta(hours=1)
        ts = _timestamps_ending(end, 20)
        # Swap two adjacent entries to break monotonicity.
        ts[5], ts[6] = ts[6], ts[5]
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 120)]
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(ts, name="timestamp"))
        path = tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv"
        df.to_csv(path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "sorted" in result["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_duplicate_timestamps_block(self, tmp_path):
        end = _FIXED_NOW - timedelta(hours=1)
        ts = _timestamps_ending(end, 20)
        ts[10] = ts[11]  # introduce a duplicate
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 120)]
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(ts, name="timestamp"))
        path = tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv"
        df.to_csv(path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "duplicate" in result["blocker"]
        assert a.submit_market_order.call_count == 0

    def test_unparseable_timestamps_block(self, tmp_path):
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 120)]
        df = pd.DataFrame(rows, index=pd.Index(
            ["not-a-date"] * 20, name="timestamp",
        ))
        path = tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv"
        df.to_csv(path)
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        # Either "invalid" or "duplicate" path is acceptable here as long
        # as it blocks and never submits.
        assert a.submit_market_order.call_count == 0


class TestCacheSelectionByLatestTimestamp:
    """The CLI must pick the file with the greatest latest-bar timestamp,
    independent of extension or filename order."""

    def _write_with_filename(
        self, cache_dir: Path, closes: list[float], *, end: datetime, filename: str,
    ) -> Path:
        ts = _timestamps_ending(end, len(closes))
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in closes]
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(ts, name="timestamp"))
        path = cache_dir / filename
        df.to_csv(path)
        return path

    def test_picks_latest_csv_over_older_csv(self, tmp_path):
        # Older alphabetically-later name; newer alphabetically-earlier name.
        # File-naming order would pick the OLDER one; greatest-timestamp
        # selection must pick the NEWER one.
        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_with_filename(
            tmp_path, [float(c) for c in range(100, 120)],
            end=old_end, filename="SPY_2026-01-01_2026-06-01_60m.csv",
        )
        self._write_with_filename(
            tmp_path, [float(c) for c in range(200, 220)],
            end=new_end, filename="SPY_2025-01-01_2025-06-01_60m.csv",
        )
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        # Closes 200..219 → latest_close=219; equity 100000 * 0.10 / 219 = 45.
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45
        assert code == 0

    def test_picks_latest_regardless_of_extension(self, tmp_path):
        # Build a recent CSV alongside an older parquet (if pyarrow
        # available); otherwise build two CSVs.
        try:
            import pyarrow  # noqa: F401
            have_parquet = True
        except ImportError:
            have_parquet = False

        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        # Newer CSV
        self._write_with_filename(
            tmp_path, [float(c) for c in range(200, 220)],
            end=new_end, filename="SPY_2026-01-01_2026-06-01_60m.csv",
        )
        # Older file: parquet if available, otherwise another CSV.
        if have_parquet:
            ts = _timestamps_ending(old_end, 20)
            df = pd.DataFrame(
                [{
                    "open": float(c), "high": float(c), "low": float(c),
                    "close": float(c), "volume": 1000.0,
                } for c in range(100, 120)],
                index=pd.DatetimeIndex(ts, name="timestamp"),
            )
            df.to_parquet(tmp_path / "SPY_2024-01-01_2024-06-01_60m.parquet")
        else:
            self._write_with_filename(
                tmp_path, [float(c) for c in range(100, 120)],
                end=old_end, filename="SPY_2024-01-01_2024-06-01_60m.csv",
            )

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        # Must have picked the newer file (closes 200..219, qty 45).
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45

    def test_single_file_still_picked(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        code, _ = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert code == 0

    def test_invalid_file_falls_back_so_loader_reports_clear_error(self, tmp_path):
        # An unreadable file plus no other candidates → loader still runs
        # on the only candidate and emits a precise blocker.
        garbage = tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv"
        garbage.write_text("not,a,real,csv\n")
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert code == 1

    def test_picks_valid_over_invalid_when_both_present(self, tmp_path):
        # Invalid CSV with the higher alphabetical name; valid CSV with
        # the lower alphabetical name. Old behavior (last alphabetical)
        # would pick the invalid one. New behavior peeks each file's
        # latest timestamp and picks the valid one.
        garbage = tmp_path / "SPY_zzzz_60m.csv"
        garbage.write_text("garbage,nonsense\n1,2\n")
        self._write_with_filename(
            tmp_path, [float(c) for c in range(200, 220)],
            end=_FIXED_NOW - timedelta(hours=1),
            filename="SPY_aaaa_60m.csv",
        )
        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45


class TestCandidateFullValidityScoring:
    """A candidate must be fully valid (timestamps AND OHLCV) to win the
    latest-bar selection — otherwise an older fully valid file is
    chosen instead."""

    def _write_valid(self, path: Path, closes: list[float], *, end: datetime) -> Path:
        ts = _timestamps_ending(end, len(closes))
        df = pd.DataFrame(
            [{
                "open": float(c), "high": float(c), "low": float(c),
                "close": float(c), "volume": 1000.0,
            } for c in closes],
            index=pd.DatetimeIndex(ts, name="timestamp"),
        )
        df.to_csv(path)
        return path

    def _write_valid_parquet(self, path: Path, closes: list[float], *, end: datetime) -> Path:
        ts = _timestamps_ending(end, len(closes))
        df = pd.DataFrame(
            [{
                "open": float(c), "high": float(c), "low": float(c),
                "close": float(c), "volume": 1000.0,
            } for c in closes],
            index=pd.DatetimeIndex(ts, name="timestamp"),
        )
        df.to_parquet(path)
        return path

    def test_newer_with_malformed_final_row_loses_to_older_valid(self, tmp_path):
        # Older valid file: closes 200..219 (latest_close 219) at end-3h
        # Newer "candidate": timestamp-valid but malformed final OHLCV row
        # at end-1h. Old scoring would have picked the newer one and
        # blocked. New scoring picks the older fully valid file.
        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_valid(
            tmp_path / "SPY_2025-01-01_2025-06-01_60m.csv",
            [float(c) for c in range(200, 220)],
            end=old_end,
        )
        new_ts = _timestamps_ending(new_end, 20)
        rows = [{
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        } for c in range(100, 119)]
        rows.append({
            "open": "nan", "high": "x", "low": "y",
            "close": "z", "volume": "w",
        })
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(new_ts, name="timestamp"))
        df.to_csv(tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv")

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        # Older valid file's qty: equity 100000 * 0.10 / 219 = 45
        assert result["order_plan"]["qty"] == 45
        assert code == 0

    def test_newer_missing_column_loses_to_older_valid(self, tmp_path):
        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_valid(
            tmp_path / "SPY_2025-01-01_2025-06-01_60m.csv",
            [float(c) for c in range(200, 220)],
            end=old_end,
        )
        # Newer candidate is missing the 'volume' column.
        new_ts = _timestamps_ending(new_end, 20)
        df = pd.DataFrame(
            [{
                "open": float(c), "high": float(c), "low": float(c),
                "close": float(c),
            } for c in range(100, 120)],
            index=pd.DatetimeIndex(new_ts, name="timestamp"),
        )
        df.to_csv(tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv")

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45

    def test_newer_unsorted_timestamps_lose_to_older_valid(self, tmp_path):
        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_valid(
            tmp_path / "SPY_2025-01-01_2025-06-01_60m.csv",
            [float(c) for c in range(200, 220)],
            end=old_end,
        )
        new_ts = _timestamps_ending(new_end, 20)
        new_ts[5], new_ts[6] = new_ts[6], new_ts[5]  # break monotonicity
        df = pd.DataFrame(
            [{
                "open": float(c), "high": float(c), "low": float(c),
                "close": float(c), "volume": 1000.0,
            } for c in range(100, 120)],
            index=pd.DatetimeIndex(new_ts, name="timestamp"),
        )
        df.to_csv(tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv")

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45

    def test_newer_duplicate_timestamps_lose_to_older_valid(self, tmp_path):
        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_valid(
            tmp_path / "SPY_2025-01-01_2025-06-01_60m.csv",
            [float(c) for c in range(200, 220)],
            end=old_end,
        )
        new_ts = _timestamps_ending(new_end, 20)
        new_ts[10] = new_ts[11]  # duplicate
        df = pd.DataFrame(
            [{
                "open": float(c), "high": float(c), "low": float(c),
                "close": float(c), "volume": 1000.0,
            } for c in range(100, 120)],
            index=pd.DatetimeIndex(new_ts, name="timestamp"),
        )
        df.to_csv(tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv")

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45

    def test_all_timestamp_valid_but_ohlcv_invalid_candidates_block(self, tmp_path):
        # Two candidates, both with valid tz-aware timestamps but
        # malformed OHLCV. With no fully valid candidate, the CLI
        # must block (not silently succeed).
        end_a = _FIXED_NOW - timedelta(hours=3)
        end_b = _FIXED_NOW - timedelta(hours=1)
        for end, name in [(end_a, "a"), (end_b, "b")]:
            ts = _timestamps_ending(end, 20)
            rows = [{
                "open": "x", "high": "x", "low": "x",
                "close": "x", "volume": "x",
            } for _ in range(20)]
            df = pd.DataFrame(rows, index=pd.DatetimeIndex(ts, name="timestamp"))
            df.to_csv(tmp_path / f"SPY_{name}_60m.csv")

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "malformed" in result["blocker"]
        assert code == 1
        assert a.submit_market_order.call_count == 0

    def test_newest_fully_valid_still_selected_across_csv_and_parquet(self, tmp_path):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            pytest.skip("pyarrow not available")

        # Older valid CSV; newer valid parquet → parquet must win
        # because it has the greater latest-bar timestamp.
        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_valid(
            tmp_path / "SPY_2025-01-01_2025-06-01_60m.csv",
            [float(c) for c in range(100, 120)],
            end=old_end,
        )
        self._write_valid_parquet(
            tmp_path / "SPY_2026-01-01_2026-06-01_60m.parquet",
            [float(c) for c in range(200, 220)],
            end=new_end,
        )

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        # Parquet's closes 200..219 win → qty 45.
        assert result["order_plan"]["qty"] == 45

    def test_newest_valid_csv_beats_older_valid_parquet(self, tmp_path):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            pytest.skip("pyarrow not available")

        old_end = _FIXED_NOW - timedelta(hours=1, minutes=30)
        new_end = _FIXED_NOW - timedelta(hours=1)
        self._write_valid_parquet(
            tmp_path / "SPY_2025-01-01_2025-06-01_60m.parquet",
            [float(c) for c in range(100, 120)],
            end=old_end,
        )
        self._write_valid(
            tmp_path / "SPY_2026-01-01_2026-06-01_60m.csv",
            [float(c) for c in range(200, 220)],
            end=new_end,
        )

        a = _mock_adapter()
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["order_plan"]["qty"] == 45


class TestSessionAwareFreshness:
    """validate_bar_freshness covers open / closed / weekend / holiday cases."""

    def _clock(self, **overrides):
        defaults = {
            "timestamp": "t",
            "is_open": True,
            "next_open": None,
            "next_close": None,
        }
        defaults.update(overrides)
        return defaults

    # ------- direct helper tests -------

    def test_open_market_recent_bar_passes(self):
        latest = _FIXED_NOW - timedelta(hours=1)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=_FIXED_NOW, clock=self._clock(is_open=True),
        ) is None

    def test_open_market_older_than_two_hours_blocks(self):
        latest = _FIXED_NOW - timedelta(hours=3)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=_FIXED_NOW, clock=self._clock(is_open=True),
        )
        assert blocker is not None
        assert "stale" in blocker
        assert "3h 0m" in blocker
        assert "2h 0m" in blocker

    def test_immediately_after_close_final_session_bar_passes(self):
        # _FIXED_NOW = 2026-06-23 14:30 UTC  ≈ Tue 10:30 ET
        # Pretend it's now 21:30 UTC (17:30 ET, just after 16:00 close).
        # Latest bar: 19:00 UTC (15:00 ET) — 2.5h old. Market closed.
        now = datetime(2026, 6, 23, 21, 30, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 23, 19, 0, tzinfo=timezone.utc)
        clock = self._clock(
            is_open=False,
            next_open="2026-06-24T13:30:00+00:00",   # next morning 9:30 ET
            next_close="2026-06-24T20:00:00+00:00",
        )
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=clock,
        ) is None

    def test_overnight_final_session_bar_passes(self):
        # Wed 2026-06-24 04:00 UTC ≈ Wed 00:00 ET overnight.
        # Latest bar: Tue 19:00 UTC (15:00 ET, last bar of Tue session) — 9h old.
        now = datetime(2026, 6, 24, 4, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 23, 19, 0, tzinfo=timezone.utc)
        clock = self._clock(
            is_open=False,
            next_open="2026-06-24T13:30:00+00:00",
        )
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=clock,
        ) is None

    def test_saturday_with_friday_final_bar_passes(self):
        # Saturday 2026-06-27 14:00 UTC; market closed; next_open Mon 06-29 13:30 UTC.
        # Latest bar: Fri 2026-06-26 19:00 UTC — about 43h old.
        now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 19, 0, tzinfo=timezone.utc)
        clock = self._clock(
            is_open=False,
            next_open="2026-06-29T13:30:00+00:00",
        )
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=clock,
        ) is None

    def test_monday_premarket_with_friday_final_bar_passes(self):
        # Monday 2026-06-29 12:00 UTC (08:00 ET premarket); next_open at 13:30 UTC.
        # Latest bar: Fri 2026-06-26 19:00 UTC ≈ 65h old. Should pass.
        now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 19, 0, tzinfo=timezone.utc)
        clock = self._clock(
            is_open=False,
            next_open="2026-06-29T13:30:00+00:00",
        )
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=clock,
        ) is None

    def test_holiday_previous_session_bar_passes(self):
        # Long weekend: Friday holiday, market reopens Tuesday morning.
        # Now: Mon 12:00 UTC; next_open Tue 13:30 UTC; latest bar Thu 19:00 UTC.
        # Latest bar is ~89h before next_open — within 96h window.
        now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 7, 2, 19, 0, tzinfo=timezone.utc)
        clock = self._clock(
            is_open=False,
            next_open="2026-07-07T13:30:00+00:00",
        )
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=clock,
        ) is None

    def test_bar_older_than_most_recent_completed_session_blocks(self):
        # Stale weekly+ gap: latest bar from 2 weeks ago.
        # Monday premarket: most recent completed session is the previous Friday.
        now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 15, 19, 0, tzinfo=timezone.utc)
        clock = self._clock(is_open=False)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=clock,
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_future_bar_blocks(self):
        latest = _FIXED_NOW + timedelta(hours=1)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=_FIXED_NOW, clock=self._clock(is_open=True),
        )
        assert blocker is not None
        assert "future" in blocker

    @pytest.mark.parametrize("bad", ["true", 0, 1, None, object(), "false"])
    def test_malformed_is_open_blocks(self, bad):
        blocker = cli.validate_bar_freshness(
            latest_ts=_FIXED_NOW - timedelta(hours=1),
            now=_FIXED_NOW,
            clock={"is_open": bad, "next_open": None, "next_close": None},
        )
        assert blocker is not None
        assert "is_open" in blocker

    def test_malformed_next_open_blocks(self):
        blocker = cli.validate_bar_freshness(
            latest_ts=_FIXED_NOW - timedelta(hours=10),
            now=_FIXED_NOW,
            clock={"is_open": False, "next_open": "not-a-date", "next_close": None},
        )
        assert blocker is not None
        assert "next_open" in blocker

    def test_malformed_next_close_blocks(self):
        blocker = cli.validate_bar_freshness(
            latest_ts=_FIXED_NOW - timedelta(hours=1),
            now=_FIXED_NOW,
            clock={
                "is_open": True,
                "next_open": "2026-06-29T13:30:00+00:00",
                "next_close": "still-not-a-date",
            },
        )
        assert blocker is not None
        assert "next_close" in blocker

    def test_naive_next_open_blocks(self):
        # Timezone-naive next_open should also block.
        blocker = cli.validate_bar_freshness(
            latest_ts=_FIXED_NOW - timedelta(hours=10),
            now=_FIXED_NOW,
            clock={
                "is_open": False,
                "next_open": "2026-06-29T13:30:00",  # no offset
                "next_close": None,
            },
        )
        assert blocker is not None
        assert "next_open" in blocker

    def test_blocker_age_includes_hours_and_minutes(self):
        latest = _FIXED_NOW - timedelta(hours=4, minutes=12)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=_FIXED_NOW, clock=self._clock(is_open=True),
        )
        assert blocker is not None
        assert "4h 12m" in blocker

    # ------- end-to-end CLI tests through main() -------

    def test_cli_open_market_recent_bar_passes_buy_plan(self, tmp_path):
        _write_bullish_cache(tmp_path)  # latest 1h before _FIXED_NOW
        a = _mock_adapter()  # is_open=True
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "PASS"
        assert result["action"] == "buy_planned"
        assert a.submit_market_order.call_count == 0
        assert code == 0

    def test_cli_open_market_three_hour_old_bar_blocks(self, tmp_path):
        # Bar 3 hours old, market open → stale (over 2h).
        old_end = _FIXED_NOW - timedelta(hours=3)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=old_end)
        a = _mock_adapter()  # is_open=True
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert "stale" in result["blocker"]
        assert "while market is open" in result["blocker"]
        assert "3h 0m" in result["blocker"]
        assert a.submit_market_order.call_count == 0
        assert code == 1

    def test_cli_closed_market_weekend_bar_passes(self, tmp_path):
        # Use a fixed "now" of Saturday with a Friday-final cache.
        sat_now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        fri_end = datetime(2026, 6, 26, 19, 0, tzinfo=timezone.utc)
        # 20 hourly bars ending Fri 19:00 UTC.
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=fri_end)
        a = _mock_adapter(clock_open=False)
        # Override the mock clock to include next_open.
        a.get_clock.return_value = {
            "timestamp": "t", "is_open": False,
            "next_open": "2026-06-29T13:30:00+00:00",
            "next_close": "2026-06-29T20:00:00+00:00",
        }
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path,
                             now_utc_fn=lambda: sat_now)
        result = _parse_result(out)
        # Freshness passes; cycle then sees is_open=False → signal BLOCK
        # with MARKET_NOT_OPEN. result=PASS, action=none, signal=BLOCK.
        assert result["result"] == "PASS"
        assert result["action"] == "none"
        assert result["signal"] == "BLOCK"
        assert "MARKET_NOT_OPEN" in result["reason_codes"]
        assert a.submit_market_order.call_count == 0
        assert code == 0

    def test_cli_clock_exception_returns_error(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        a.get_clock.side_effect = AlpacaPaperAdapterError("net down")
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "ERROR"
        assert "clock read failed" in result["blocker"]
        assert a.submit_market_order.call_count == 0
        assert code == 2

    def test_cli_dry_run_and_submit_use_same_freshness_check(self, tmp_path):
        old_end = _FIXED_NOW - timedelta(hours=3)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=old_end)
        a = _mock_adapter()  # is_open=True
        # Dry-run blocks.
        code_dry, out_dry = _run_cli([], adapter=a, cache_dir=tmp_path)
        # Paper-submit blocks identically.
        a2 = _mock_adapter()
        code_sub, out_sub = _run_cli(["--submit-paper"], adapter=a2, cache_dir=tmp_path)
        for code, out in [(code_dry, out_dry), (code_sub, out_sub)]:
            result = _parse_result(out)
            assert result["result"] == "BLOCKED"
            assert "stale" in result["blocker"]
            assert code == 1
        assert a.submit_market_order.call_count == 0
        assert a2.submit_market_order.call_count == 0

    def test_cli_blocker_contains_hours_and_minutes(self, tmp_path):
        old_end = _FIXED_NOW - timedelta(hours=4, minutes=12)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=old_end)
        a = _mock_adapter()
        _, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert "4h 12m" in result["blocker"]

    def test_cli_no_credentials_in_freshness_blocker(self, tmp_path):
        old_end = _FIXED_NOW - timedelta(hours=8)
        _write_cache_csv(tmp_path, [float(c) for c in range(100, 120)], end=old_end)
        a = _mock_adapter()
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "no-leak-key",
            "ALPACA_SECRET_KEY": "no-leak-secret",
        }, clear=False):
            _, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        assert "no-leak-key" not in out
        assert "no-leak-secret" not in out


class TestNYSECalendarFreshness:
    """Exchange-calendar-aware closed-market freshness via NYSE."""

    def _closed_clock(self):
        return {
            "timestamp": "t", "is_open": False,
            "next_open": None, "next_close": None,
        }

    def test_wednesday_after_close_with_wednesday_final_bar_passes(self):
        # 2026-06-24 is Wednesday. NYSE session: 13:30 → 20:00 UTC.
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_wednesday_after_close_with_tuesday_final_bar_blocks(self):
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 23, 19, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_tuesday_premarket_with_monday_final_bar_passes(self):
        # 2026-06-30 is Tuesday. Premarket 12:00 UTC = 08:00 ET.
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 29, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_tuesday_premarket_with_previous_friday_bar_blocks(self):
        # Monday 2026-06-29 was a regular session, so on Tuesday premarket
        # the most recent completed session is Monday — not the prior Friday.
        now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_saturday_with_friday_final_bar_passes_calendar(self):
        # Saturday 2026-06-27; most recent session is Friday 2026-06-26.
        now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_monday_premarket_with_friday_final_bar_passes_calendar(self):
        # Monday 2026-06-29 premarket 12:00 UTC; most recent completed
        # session = Friday 2026-06-26 (Monday hasn't completed yet).
        now = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_holiday_uses_actual_prior_nyse_session(self):
        # 2026-07-03 (Friday) is the observed July 4 holiday — NOT a
        # trading day. Now: Saturday morning. Most recent completed
        # session = Thursday 2026-07-02 (not Friday). Thursday bar passes.
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 7, 2, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_holiday_friday_bar_blocks_because_friday_not_a_session(self):
        # Even though Friday 2026-07-03 is calendar-Friday, NYSE was closed
        # that day. A "Friday bar" is malformed/missing data → blocks.
        now = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 7, 3, 19, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_long_holiday_weekend_passes_only_immediately_previous_session(self):
        # July 3 holiday + weekend → Monday morning's most recent
        # completed session is Thursday 2026-07-02. Thursday bar passes.
        now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 7, 2, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_long_holiday_weekend_blocks_earlier_session_bar(self):
        # The Wednesday before the long weekend is NOT the most recent
        # completed session. Even though Wednesday's bar (2026-07-01) is
        # well under 120 wall-clock hours before next_open (Mon 2026-07-06
        # = ~118h), it belongs to a skipped session and must block.
        now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 7, 1, 19, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_bar_within_120h_but_from_skipped_session_blocks(self):
        # Tuesday after close. Friday 2026-06-19 bar is ~96h before Friday's
        # close, well under 120h. But the most recent completed session is
        # Monday 2026-06-22. Friday's bar is from an earlier session.
        now = datetime(2026, 6, 23, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 19, 19, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_calendar_unavailable_returns_blocked(self):
        # Simulate a missing exchange-calendar dependency.
        from unittest.mock import patch
        now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc)
        with patch.object(
            cli, "_most_recent_completed_nyse_session", return_value=None,
        ):
            blocker = cli.validate_bar_freshness(
                latest_ts=latest, now=now, clock=self._closed_clock(),
            )
        assert blocker is not None
        assert "NYSE" in blocker


class TestSingleClockSnapshotInCLI:
    """The CLI must read the broker clock exactly once per cycle and share
    the same snapshot with both the freshness check and the cycle."""

    def test_cli_calls_get_clock_exactly_once(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        _run_cli([], adapter=a, cache_dir=tmp_path)
        assert a.get_clock.call_count == 1

    def test_cli_calls_get_clock_once_in_paper_submit_mode(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        assert a.get_clock.call_count == 1

    def test_cli_calls_get_clock_once_on_hold_signal(self, tmp_path):
        _write_flat_cache(tmp_path)
        a = _mock_adapter()
        _run_cli([], adapter=a, cache_dir=tmp_path)
        assert a.get_clock.call_count == 1

    def test_second_clock_value_never_consumed(self, tmp_path):
        # If the cycle were to call get_clock a second time it would
        # receive clock2 (with is_open=False) and produce a BLOCK signal
        # from MARKET_NOT_OPEN. Because the CLI shares its single
        # snapshot, the cycle must see the open-market clock1 and
        # produce buy_planned.
        _write_bullish_cache(tmp_path)
        clock1 = {
            "timestamp": "t1", "is_open": True,
            "next_open": None, "next_close": None,
        }
        clock2 = {
            "timestamp": "t2", "is_open": False,
            "next_open": "2026-06-24T13:30:00+00:00",
            "next_close": "2026-06-24T20:00:00+00:00",
        }
        a = _mock_adapter()
        a.get_clock.side_effect = [clock1, clock2]
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert a.get_clock.call_count == 1
        assert result["result"] == "PASS"
        assert result["action"] == "buy_planned"
        assert result["signal"] == "BUY"

    def test_no_submission_when_clock_validation_fails(self, tmp_path):
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        # Return a malformed clock (is_open not exactly bool).
        a.get_clock.return_value = {
            "timestamp": "t", "is_open": "true",
            "next_open": None, "next_close": None,
        }
        code, out = _run_cli(["--submit-paper"], adapter=a, cache_dir=tmp_path)
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0
        assert code == 1


class TestFinalBarFreshnessRequirement:
    """Closed-market freshness must require the FINAL session 60m bar,
    not any bar inside the session window."""

    def _closed_clock(self):
        return {
            "timestamp": "t", "is_open": False,
            "next_open": None, "next_close": None,
        }

    # ---- Wednesday after close (regular session 13:30–20:00 UTC) ----
    # Expected final-bar label: 19:30 UTC (Yahoo start-of-bar).
    # Accepted window: [19:00, 20:00] UTC.

    def test_after_close_final_session_bar_passes(self):
        # 2026-06-24 is a regular Wednesday session.
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_after_close_morning_bar_same_session_blocks(self):
        # Morning bar (label 13:30 UTC) is the session's FIRST bar,
        # not the final bar. Must block even though it's in the right
        # session.
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 13, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_after_close_midday_bar_same_session_blocks(self):
        # Midday bar (label 16:30 UTC = noon ET) — same session, but
        # not the final bar.
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 16, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_after_close_label_at_close_passes_via_tolerance(self):
        # End-of-bar labeling: label = session_close = 20:00 UTC. Should
        # be accepted by the ±30m tolerance around 19:30.
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 20, 0, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_after_close_label_top_of_hour_passes_via_tolerance(self):
        # Top-of-hour labeling: label = 19:00 UTC. Accepted boundary.
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 19, 0, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    # ---- Weekend ----

    def test_weekend_friday_final_bar_passes(self):
        now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)  # Saturday
        latest = datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_weekend_friday_morning_bar_blocks(self):
        now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 13, 30, tzinfo=timezone.utc)  # Fri 9:30 ET
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_weekend_friday_midday_bar_blocks(self):
        now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 26, 16, 30, tzinfo=timezone.utc)  # Fri noon ET
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    # ---- Early-close sessions ----
    # 2026-11-27 (Black Friday) and 2026-12-24 (Christmas Eve) are
    # NYSE early-close days: 14:30 → 18:00 UTC (4 hours after DST).
    # Expected final 60m bar label: 17:30 UTC. Accepted window:
    # [17:00, 18:00] UTC.

    def test_early_close_session_final_bar_passes(self):
        # 2026-11-27 Black Friday early-close session.
        now = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc)
        assert cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        ) is None

    def test_early_close_session_earlier_bar_blocks(self):
        # Same early-close session, but a midday bar (label 15:30 UTC ≈
        # 10:30 ET) — well outside the final-bar window.
        now = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 11, 27, 15, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    def test_early_close_session_regular_session_final_bar_blocks(self):
        # The 19:30 UTC label that would pass for a regular session must
        # NOT pass for an early-close session that ends at 18:00 UTC
        # (19:30 is well after that session's close).
        now = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 11, 27, 19, 30, tzinfo=timezone.utc)
        # Note: 19:30 is also in the FUTURE relative to now=19:00, so it
        # would block on the future-bar check too. Adjust now slightly.
        now = datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now, clock=self._closed_clock(),
        )
        assert blocker is not None
        assert "is not the final 60m bar" in blocker

    # ---- Unsupported intervals ----

    @pytest.mark.parametrize("bad_interval", ["1d", "30m", "15m", "5m", "1m", "", "60min", None])
    def test_unsupported_interval_blocks(self, bad_interval):
        now = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)
        latest = datetime(2026, 6, 24, 19, 30, tzinfo=timezone.utc)
        blocker = cli.validate_bar_freshness(
            latest_ts=latest, now=now,
            clock=self._closed_clock(),
            interval=bad_interval,
        )
        assert blocker is not None
        assert "does not support interval" in blocker

    # ---- CLI still calls get_clock exactly once ----

    def test_cli_get_clock_call_count_remains_one(self, tmp_path):
        # Fresh open-market bullish cache → freshness passes via 2h
        # open-market window; cycle proceeds with the same snapshot.
        _write_bullish_cache(tmp_path)
        a = _mock_adapter()
        _run_cli([], adapter=a, cache_dir=tmp_path)
        assert a.get_clock.call_count == 1

    # ---- Zero submissions on every invalid freshness case ----

    @pytest.mark.parametrize("scenario", [
        "morning_bar_blocks",
        "midday_bar_blocks",
        "skipped_session_blocks",
        "unsupported_interval_blocks",
    ])
    def test_no_submission_on_invalid_freshness(self, scenario, tmp_path):
        # Saturday is a closed-market scenario in which a Friday final
        # bar would pass. We deliberately set the cache to something
        # invalid for each scenario and confirm zero submissions.
        sat_now = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)
        if scenario == "morning_bar_blocks":
            end = datetime(2026, 6, 26, 13, 30, tzinfo=timezone.utc)
        elif scenario == "midday_bar_blocks":
            end = datetime(2026, 6, 26, 16, 30, tzinfo=timezone.utc)
        elif scenario == "skipped_session_blocks":
            # Thu final bar (skipped Friday — most recent is Friday).
            end = datetime(2026, 6, 25, 19, 30, tzinfo=timezone.utc)
        elif scenario == "unsupported_interval_blocks":
            # The CLI restricts --interval to 60m via argparse choices,
            # so directly invoke the helper instead with bad interval.
            blocker = cli.validate_bar_freshness(
                latest_ts=datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc),
                now=sat_now,
                clock=self._closed_clock(),
                interval="1d",
            )
            assert blocker is not None
            return
        else:
            pytest.fail(f"unknown scenario {scenario}")
        _write_cache_csv(
            tmp_path, [float(c) for c in range(100, 120)], end=end,
        )
        a = _mock_adapter(clock_open=False)
        code, out = _run_cli(
            ["--submit-paper"], adapter=a, cache_dir=tmp_path,
            now_utc_fn=lambda: sat_now,
        )
        result = _parse_result(out)
        assert result["result"] == "BLOCKED"
        assert a.submit_market_order.call_count == 0
        assert a.get_clock.call_count == 1
        assert code == 1


class TestArgParsing:
    def test_interval_only_60m_supported(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--interval", "1d", "--cache-dir", str(tmp_path)])
