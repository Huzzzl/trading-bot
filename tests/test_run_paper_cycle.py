"""Tests for the runnable Alpaca paper cycle CLI — S52.

Mock-adapter only; never makes a real Alpaca call or a network fetch.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.broker.alpaca_paper_adapter import AlpacaPaperAdapterError
from src.tools import run_paper_cycle as cli


_NEAR_FUTURE = "2099-01-01T00:00:00Z"


def _write_bullish_cache(cache_dir: Path) -> Path:
    # Rising closes 100..119 over 20 bars (bullish SMA crossover).
    rows = []
    for i, c in enumerate(range(100, 120)):
        rows.append({
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        })
    df = pd.DataFrame(rows)
    df.index = pd.RangeIndex(len(rows))
    path = cache_dir / "SPY_2026-01-01_2026-06-01_60m.csv"
    df.to_csv(path)
    return path


def _write_bearish_cache(cache_dir: Path) -> Path:
    rows = []
    for c in range(120, 100, -1):
        rows.append({
            "open": float(c), "high": float(c), "low": float(c),
            "close": float(c), "volume": 1000.0,
        })
    df = pd.DataFrame(rows)
    df.index = pd.RangeIndex(len(rows))
    path = cache_dir / "SPY_2026-01-01_2026-06-01_60m.csv"
    df.to_csv(path)
    return path


def _write_flat_cache(cache_dir: Path) -> Path:
    rows = [{
        "open": 100.0, "high": 100.0, "low": 100.0,
        "close": 100.0, "volume": 1000.0,
    } for _ in range(20)]
    df = pd.DataFrame(rows)
    df.index = pd.RangeIndex(len(rows))
    path = cache_dir / "SPY_2026-01-01_2026-06-01_60m.csv"
    df.to_csv(path)
    return path


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


def _run_cli(argv, *, adapter, cache_dir: Path):
    full_argv = list(argv) + ["--cache-dir", str(cache_dir)]
    stdout = io.StringIO()
    with patch.object(
        cli.AlpacaPaperAdapter, "from_environment", return_value=adapter,
    ), patch.object(sys, "stdout", stdout):
        code = cli.main(full_argv)
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
        _write_bullish_cache(tmp_path)
        a = _mock_adapter(clock_open=False)
        code, out = _run_cli([], adapter=a, cache_dir=tmp_path)
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


class TestArgParsing:
    def test_interval_only_60m_supported(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--interval", "1d", "--cache-dir", str(tmp_path)])
