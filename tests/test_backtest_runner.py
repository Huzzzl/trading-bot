"""
tests/test_backtest_runner.py
------------------------------
Unit tests for src/backtest/backtest_runner.py.

All tests are pure/offline — no broker calls, no credentials, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from src.backtest.backtest_runner import (
    BacktestRunConfig,
    BacktestRunResult,
    run_backtest,
)
from src.data.base import BaseDataProvider

_EASTERN = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Deterministic fake data provider — no network, no yfinance, no credentials
# ---------------------------------------------------------------------------

_TREND_STRATEGY_PARAMS: dict[str, Any] = {
    "symbol": "SPY",
    "timeframe": "1d",
    "fast_ema_period": 5,
    "slow_ema_period": 10,
    "atr_period": 5,
    "volatility_lookback": 15,
    "breakout_lookback": 5,
}

# TrendFollowing min_required = max(slow_ema, atr+vol-1, breakout+1)
# = max(10, 5+15-1, 5+1) = 19.  We provide 30 daily bars to be safe.
_N_BARS = 30


def _make_bars(n: int = _N_BARS, start: str = "2024-01-02") -> pd.DataFrame:
    """Return a strictly-uptrending synthetic OHLCV bar DataFrame."""
    dates = pd.bdate_range(start, periods=n, tz=_EASTERN)
    base = np.linspace(100.0, 130.0, n)
    df = pd.DataFrame(
        {
            "open":   base * 0.998,
            "high":   base * 1.010,
            "low":    base * 0.990,
            "close":  base,
            "volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )
    return df


class _FakeProvider(BaseDataProvider):
    """Deterministic provider that returns pre-built bars. No network."""

    def __init__(self, bars: pd.DataFrame | None = None) -> None:
        self._bars = bars if bars is not None else _make_bars()

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        return self._bars.copy()


class _EmptyProvider(BaseDataProvider):
    """Provider that always returns an empty DataFrame."""

    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


_DEFAULT_CONFIG = BacktestRunConfig(
    strategy_name="trend_following",
    strategy_params=_TREND_STRATEGY_PARAMS,
    symbols=["SPY"],
    start_date="2024-01-02",
    end_date="2024-02-15",
    bar_interval="1d",
    initial_capital=100_000.0,
    position_size_pct=0.95,
    stop_execution="bar_close",
)


# ---------------------------------------------------------------------------
# TestBacktestRunConfig
# ---------------------------------------------------------------------------


class TestBacktestRunConfig:
    def test_frozen(self) -> None:
        cfg = _DEFAULT_CONFIG
        with pytest.raises((AttributeError, TypeError)):
            cfg.strategy_name = "orb"  # type: ignore[misc]

    def test_default_bar_interval(self) -> None:
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params={},
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
        )
        assert cfg.bar_interval == "5m"

    def test_default_initial_capital(self) -> None:
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params={},
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
        )
        assert cfg.initial_capital == 100_000.0

    def test_default_position_size_pct(self) -> None:
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params={},
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
        )
        assert cfg.position_size_pct == 0.95

    def test_default_stop_execution(self) -> None:
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params={},
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
        )
        assert cfg.stop_execution == "bar_close"

    def test_custom_values_stored(self) -> None:
        cfg = BacktestRunConfig(
            strategy_name="orb",
            strategy_params={"k": 1},
            symbols=["AAPL", "MSFT"],
            start_date="2023-01-01",
            end_date="2023-12-31",
            bar_interval="1h",
            initial_capital=50_000.0,
            position_size_pct=0.5,
            stop_execution="stop_price",
        )
        assert cfg.strategy_name == "orb"
        assert cfg.strategy_params == {"k": 1}
        assert cfg.symbols == ["AAPL", "MSFT"]
        assert cfg.bar_interval == "1h"
        assert cfg.initial_capital == 50_000.0
        assert cfg.position_size_pct == 0.5
        assert cfg.stop_execution == "stop_price"


# ---------------------------------------------------------------------------
# TestBacktestRunResult
# ---------------------------------------------------------------------------


class TestBacktestRunResult:
    def _make_result(self) -> BacktestRunResult:
        return run_backtest(_DEFAULT_CONFIG, data_provider=_FakeProvider())

    def test_broker_calls_made_is_false(self) -> None:
        r = self._make_result()
        assert r.broker_calls_made is False

    def test_credentials_read_is_false(self) -> None:
        r = self._make_result()
        assert r.credentials_read is False

    def test_live_submit_enabled_is_false(self) -> None:
        r = self._make_result()
        assert r.live_submit_enabled is False

    def test_order_action_requested_is_false(self) -> None:
        r = self._make_result()
        assert r.order_action_requested is False

    def test_paper_trading_enabled_is_false(self) -> None:
        r = self._make_result()
        assert r.paper_trading_enabled is False

    def test_recommendation_only_is_true(self) -> None:
        r = self._make_result()
        assert r.recommendation_only is True

    def test_result_is_frozen(self) -> None:
        r = self._make_result()
        with pytest.raises((AttributeError, TypeError)):
            r.broker_calls_made = True  # type: ignore[misc]

    def test_strategy_name_echoed(self) -> None:
        r = self._make_result()
        assert r.strategy_name == "trend_following"

    def test_symbols_echoed(self) -> None:
        r = self._make_result()
        assert r.symbols == ["SPY"]

    def test_bar_interval_echoed(self) -> None:
        r = self._make_result()
        assert r.bar_interval == "1d"

    def test_metrics_has_required_keys(self) -> None:
        r = self._make_result()
        required = {
            "initial_capital", "final_equity", "total_return_pct",
            "annualized_return_pct", "max_drawdown_pct", "sharpe_ratio",
            "num_trades", "win_rate_pct", "avg_winning_trade",
            "avg_losing_trade", "total_commission",
        }
        assert required <= set(r.metrics.keys())

    def test_trades_is_list(self) -> None:
        r = self._make_result()
        assert isinstance(r.trades, list)

    def test_equity_curve_is_dataframe(self) -> None:
        r = self._make_result()
        assert isinstance(r.equity_curve, pd.DataFrame)

    def test_order_intents_is_list(self) -> None:
        r = self._make_result()
        assert isinstance(r.order_intents, list)

    def test_initial_capital_preserved_in_metrics(self) -> None:
        r = self._make_result()
        assert r.metrics["initial_capital"] == pytest.approx(100_000.0)


# ---------------------------------------------------------------------------
# TestRunBacktestValidation
# ---------------------------------------------------------------------------


class TestRunBacktestValidation:
    def _run(self, **overrides: Any) -> BacktestRunResult:
        fields = {
            "strategy_name": "trend_following",
            "strategy_params": _TREND_STRATEGY_PARAMS,
            "symbols": ["SPY"],
            "start_date": "2024-01-02",
            "end_date": "2024-02-15",
            "bar_interval": "1d",
            "initial_capital": 100_000.0,
            "position_size_pct": 0.95,
            "stop_execution": "bar_close",
        }
        fields.update(overrides)
        cfg = BacktestRunConfig(**fields)
        return run_backtest(cfg, data_provider=_FakeProvider())

    def test_invalid_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(bar_interval="3h")

    def test_zero_initial_capital_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(initial_capital=0.0)

    def test_negative_initial_capital_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(initial_capital=-1.0)

    def test_zero_position_size_pct_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(position_size_pct=0.0)

    def test_position_size_pct_over_one_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(position_size_pct=1.01)

    def test_invalid_stop_execution_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(stop_execution="market_open")

    def test_empty_symbols_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(symbols=[])

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown strategy name"):
            self._run(strategy_name="nonexistent_strategy")

    def test_invalid_strategy_params_raises(self) -> None:
        bad_params = dict(_TREND_STRATEGY_PARAMS)
        bad_params["fast_ema_period"] = 999
        bad_params["slow_ema_period"] = 1
        with pytest.raises(ValueError, match="invalid strategy parameters"):
            self._run(strategy_params=bad_params)

    def test_raw_strategy_name_not_echoed(self) -> None:
        secret = "secret_strategy_xyzzy"
        cfg = BacktestRunConfig(
            strategy_name=secret,
            strategy_params={},
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
        )
        with pytest.raises(ValueError) as exc_info:
            run_backtest(cfg, data_provider=_FakeProvider())
        assert secret not in str(exc_info.value)

    def test_raw_interval_not_echoed(self) -> None:
        secret = "secret_interval_xyzzy"
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_TREND_STRATEGY_PARAMS,
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
            bar_interval=secret,
        )
        with pytest.raises(ValueError) as exc_info:
            run_backtest(cfg, data_provider=_FakeProvider())
        assert secret not in str(exc_info.value)

    def test_position_size_pct_of_one_is_valid(self) -> None:
        result = self._run(position_size_pct=1.0)
        assert isinstance(result, BacktestRunResult)

    def test_stop_price_mode_is_valid(self) -> None:
        result = self._run(stop_execution="stop_price")
        assert isinstance(result, BacktestRunResult)

    def test_orb_alias_is_valid(self) -> None:
        result = self._run(strategy_name="orb", strategy_params={})
        assert isinstance(result, BacktestRunResult)

    def test_opening_range_breakout_name_is_valid(self) -> None:
        result = self._run(
            strategy_name="opening_range_breakout", strategy_params={}
        )
        assert isinstance(result, BacktestRunResult)

    def test_inf_initial_capital_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(initial_capital=float("inf"))

    def test_neg_inf_initial_capital_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(initial_capital=float("-inf"))

    def test_nan_initial_capital_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(initial_capital=float("nan"))

    def test_lowercase_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(symbols=["spy"])

    def test_space_in_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid backtest run config"):
            self._run(symbols=["BAD SYMBOL"])

    def test_secret_symbol_not_echoed(self) -> None:
        secret = "SECRET_SYMBOL_VALUE"
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_TREND_STRATEGY_PARAMS,
            symbols=[secret],
            start_date="2024-01-02",
            end_date="2024-02-15",
            bar_interval="1d",
        )
        with pytest.raises(ValueError) as exc_info:
            run_backtest(cfg, data_provider=_FakeProvider())
        assert secret not in str(exc_info.value)

    def test_dot_symbol_valid(self) -> None:
        result = self._run(symbols=["BRK.B"])
        assert isinstance(result, BacktestRunResult)

    def test_dash_symbol_valid(self) -> None:
        result = self._run(symbols=["BRK-B"])
        assert isinstance(result, BacktestRunResult)


# ---------------------------------------------------------------------------
# TestRunBacktestBehaviour
# ---------------------------------------------------------------------------


class TestRunBacktestBehaviour:
    def _default_result(self) -> BacktestRunResult:
        return run_backtest(_DEFAULT_CONFIG, data_provider=_FakeProvider())

    def test_deterministic_repeated_calls(self) -> None:
        r1 = run_backtest(_DEFAULT_CONFIG, data_provider=_FakeProvider())
        r2 = run_backtest(_DEFAULT_CONFIG, data_provider=_FakeProvider())
        assert r1.metrics["total_return_pct"] == pytest.approx(
            r2.metrics["total_return_pct"]
        )
        assert r1.metrics["sharpe_ratio"] == pytest.approx(
            r2.metrics["sharpe_ratio"]
        )

    def test_empty_data_returns_result(self) -> None:
        result = run_backtest(_DEFAULT_CONFIG, data_provider=_EmptyProvider())
        assert isinstance(result, BacktestRunResult)
        assert len(result.trades) == 0

    def test_metrics_initial_capital_matches_config(self) -> None:
        r = self._default_result()
        assert r.metrics["initial_capital"] == pytest.approx(
            _DEFAULT_CONFIG.initial_capital
        )

    def test_sharpe_uses_bar_interval(self) -> None:
        cfg_1d = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_TREND_STRATEGY_PARAMS,
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
            bar_interval="1d",
        )
        cfg_5m = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_TREND_STRATEGY_PARAMS,
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
            bar_interval="5m",
        )
        provider = _FakeProvider()
        r_1d = run_backtest(cfg_1d, data_provider=provider)
        r_5m = run_backtest(cfg_5m, data_provider=provider)
        assert r_1d.metrics["sharpe_ratio"] != pytest.approx(
            r_5m.metrics["sharpe_ratio"]
        )

    def test_config_not_mutated(self) -> None:
        cfg = BacktestRunConfig(
            strategy_name="trend_following",
            strategy_params=_TREND_STRATEGY_PARAMS.copy(),
            symbols=["SPY"],
            start_date="2024-01-02",
            end_date="2024-02-15",
            bar_interval="1d",
        )
        orig_params = dict(cfg.strategy_params)
        orig_symbols = list(cfg.symbols)
        run_backtest(cfg, data_provider=_FakeProvider())
        assert cfg.strategy_params == orig_params
        assert cfg.symbols == orig_symbols

    def test_result_symbols_is_independent_copy(self) -> None:
        r = self._default_result()
        r.symbols.append("EXTRA")  # type: ignore[attr-defined]
        r2 = self._default_result()
        assert r2.symbols == ["SPY"]

    def test_no_broker_flags_ever_true(self) -> None:
        r = self._default_result()
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.live_submit_enabled
        assert not r.order_action_requested
        assert not r.paper_trading_enabled
        assert r.recommendation_only


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

_SOURCE = Path("src/backtest/backtest_runner.py")


def _read_code(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#")[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


class TestSourceScans:
    def _code(self) -> str:
        return _read_code(_SOURCE)

    def test_no_alpaca_import(self) -> None:
        code = self._code()
        assert "import alpaca" not in code
        assert "from alpaca" not in code

    def test_no_requests_import(self) -> None:
        assert "import requests" not in self._code()

    def test_no_httpx_import(self) -> None:
        assert "import httpx" not in self._code()

    def test_no_aiohttp_import(self) -> None:
        assert "import aiohttp" not in self._code()

    def test_no_urllib_request(self) -> None:
        assert "urllib.request" not in self._code()

    def test_no_os_environ(self) -> None:
        assert "os.environ" not in self._code()

    def test_no_execution_imports(self) -> None:
        code = self._code()
        assert "from src.execution" not in code or "order_intent" in code

    def test_no_submit_order(self) -> None:
        assert "submit_order(" not in self._code()

    def test_no_cancel_order(self) -> None:
        assert "cancel_order(" not in self._code()

    def test_no_replace_order(self) -> None:
        assert "replace_order(" not in self._code()

    def test_no_close_position(self) -> None:
        assert "close_position(" not in self._code()

    def test_no_close_all_positions(self) -> None:
        assert "close_all_positions(" not in self._code()

    def test_no_post_mutation(self) -> None:
        assert ".post(" not in self._code().lower()

    def test_no_patch_mutation(self) -> None:
        assert ".patch(" not in self._code().lower()

    def test_no_delete_mutation(self) -> None:
        assert ".delete(" not in self._code().lower()

    def test_no_trade_record_write(self) -> None:
        assert "ledger" not in self._code()
