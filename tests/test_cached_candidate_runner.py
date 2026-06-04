"""
tests/test_cached_candidate_runner.py
---------------------------------------
Tests for src/research/cached_candidate_runner.py (PR S3).

No broker/API/credential/env access.
No live gates unfrozen. No live trading. No order action. No real data.
Cache lookup tests use tmp_path (pytest fixture) for isolation.
"""

from __future__ import annotations

import inspect

import pytest

import datetime
import pathlib

import pandas as pd

from src.research.cached_candidate_runner import (
    CachedCandidateRunResult,
    _slice_cached_df,
    run_cached_candidate_evaluation,
)
from src.research.candidate_evaluator import REQUIRED_METRIC_KEYS
from src.research.candidate_universe import (
    CandidateSpec,
    HoldingHorizon,
    StrategyFamily,
    build_initial_candidate_universe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trend_spec(
    symbol="SPY",
    interval="60m",
    group="A",
    candidate_id=None,
    enabled=True,
) -> CandidateSpec:
    if candidate_id is None:
        candidate_id = f"{group}_{symbol}_{interval}_trend_breakout_1to2d"
    return CandidateSpec(
        candidate_id=candidate_id,
        group=group,
        symbol=symbol,
        interval=interval,
        strategy_family=StrategyFamily.TREND_BREAKOUT,
        holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
        enabled=enabled,
    )


def _make_non_trend_spec(
    family=StrategyFamily.MEAN_REVERSION,
    symbol="SPY",
    interval="30m",
    group="C",
) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=f"{group}_{symbol}_{interval}_{family.value}_intraday",
        group=group,
        symbol=symbol,
        interval=interval,
        strategy_family=family,
        holding_horizon=HoldingHorizon.INTRADAY,
    )


def _fake_data_provider(symbol: str, interval: str):
    return {"symbol": symbol, "interval": interval, "rows": 500}


def _fake_backtest_runner(data, candidate) -> dict:
    return {
        "cagr": 0.12,
        "average_monthly_return": 0.01,
        "worst_month": -0.04,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.15,
        "calmar": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.3,
        "average_trade_return": 0.005,
        "median_trade_return": 0.004,
        "exposure_pct": 0.35,
        "trades_per_month": 4.5,
        "total_trades": 80,
        "average_holding_bars": 6.0,
        "stop_loss_frequency": 0.2,
        "session_end_frequency": 0.1,
        "slippage_bps": 10.0,
    }


# ---------------------------------------------------------------------------
# TestCachedCandidateRunResultDataclass
# ---------------------------------------------------------------------------

class TestCachedCandidateRunResultDataclass:
    def _make_result(self) -> CachedCandidateRunResult:
        return run_cached_candidate_evaluation(
            [_make_trend_spec()],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )

    def test_frozen(self):
        r = self._make_result()
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_fields_exist(self):
        r = self._make_result()
        assert hasattr(r, "result")
        assert hasattr(r, "blocker")
        assert hasattr(r, "candidates_requested")
        assert hasattr(r, "candidates_evaluated")
        assert hasattr(r, "candidates_passed")
        assert hasattr(r, "candidates_blocked")
        assert hasattr(r, "candidates_error")
        assert hasattr(r, "results")
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")

    def test_result_is_str(self):
        r = self._make_result()
        assert isinstance(r.result, str)

    def test_results_is_tuple(self):
        r = self._make_result()
        assert isinstance(r.results, tuple)

    def test_live_trading_allowed_false(self):
        r = self._make_result()
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestRunCachedCandidateEvaluationBasics
# ---------------------------------------------------------------------------

class TestRunCachedCandidateEvaluationBasics:
    def test_returns_cachedcandidaterunresult(self):
        r = run_cached_candidate_evaluation(
            [_make_trend_spec()],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert isinstance(r, CachedCandidateRunResult)

    def test_empty_candidates_returns_blocked(self):
        r = run_cached_candidate_evaluation([])
        assert r.result == "BLOCKED"

    def test_max_candidates_zero_returns_blocked(self):
        r = run_cached_candidate_evaluation(max_candidates=0)
        assert r.result == "BLOCKED"
        assert r.candidates_requested == 0

    def test_max_candidates_limits_results_count(self):
        universe = build_initial_candidate_universe()
        r = run_cached_candidate_evaluation(
            universe,
            max_candidates=3,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert len(r.results) == 3

    def test_max_candidates_exceeds_universe(self):
        specs = [_make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d")]
        r = run_cached_candidate_evaluation(
            specs,
            max_candidates=100,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.candidates_requested == 1

    def test_default_candidates_uses_full_universe(self, tmp_path):
        # No cache → all BLOCKED, but length matches full universe
        universe = build_initial_candidate_universe()
        r = run_cached_candidate_evaluation(cache_dir=str(tmp_path))
        assert r.candidates_requested == len(universe)

    def test_results_length_matches_candidates(self):
        specs = [_make_trend_spec(candidate_id=f"A_SPY_{i}m_trend_breakout_1to2d", interval="60m") for i in range(3)]
        # All share same candidate_id pattern but that's OK for this test
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
            _make_trend_spec(candidate_id="A_IWM_60m_trend_breakout_1to2d", symbol="IWM"),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert len(r.results) == 3


# ---------------------------------------------------------------------------
# TestStrategyFamilyWiring
# ---------------------------------------------------------------------------

class TestStrategyFamilyWiring:
    def test_trend_breakout_evaluates_with_injection(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "PASS"

    def test_mean_reversion_blocked(self):
        spec = _make_non_trend_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "BLOCKED"

    def test_momentum_continuation_blocked(self):
        spec = _make_non_trend_spec(StrategyFamily.MOMENTUM_CONTINUATION)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "BLOCKED"

    def test_gap_overnight_blocked(self):
        spec = _make_non_trend_spec(StrategyFamily.GAP_OVERNIGHT)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "BLOCKED"

    def test_blocked_mentions_strategy_family(self):
        spec = _make_non_trend_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert "mean_reversion" in (r.results[0].blocker or "")

    def test_blocked_mentions_not_yet_wired(self):
        spec = _make_non_trend_spec(StrategyFamily.GAP_OVERNIGHT)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert "not yet wired" in (r.results[0].blocker or "")

    def test_mixed_families_partial_block(self):
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_non_trend_spec(StrategyFamily.MEAN_REVERSION),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "PASS"
        assert r.results[1].result == "BLOCKED"

    def test_trend_breakout_result_has_metrics(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].metrics["total_trades"] == 80


# ---------------------------------------------------------------------------
# TestCachePreCheck
# ---------------------------------------------------------------------------

class TestCachePreCheck:
    def test_blocked_when_cache_dir_empty(self, tmp_path):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation([spec], cache_dir=str(tmp_path))
        assert r.results[0].result == "BLOCKED"

    def test_blocked_blocker_mentions_no_cache_file(self, tmp_path):
        spec = _make_trend_spec(symbol="SPY", interval="60m")
        r = run_cached_candidate_evaluation([spec], cache_dir=str(tmp_path))
        assert "no cache file found" in (r.results[0].blocker or "")

    def test_blocked_blocker_mentions_symbol(self, tmp_path):
        spec = _make_trend_spec(symbol="QQQ", interval="60m")
        r = run_cached_candidate_evaluation([spec], cache_dir=str(tmp_path))
        assert "QQQ" in (r.results[0].blocker or "")

    def test_blocked_blocker_mentions_interval(self, tmp_path):
        spec = _make_trend_spec(symbol="SPY", interval="1d")
        r = run_cached_candidate_evaluation([spec], cache_dir=str(tmp_path))
        assert "1d" in (r.results[0].blocker or "")

    def test_all_blocked_when_no_cache(self, tmp_path):
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
        ]
        r = run_cached_candidate_evaluation(specs, cache_dir=str(tmp_path))
        assert all(res.result == "BLOCKED" for res in r.results)

    def test_injection_bypasses_cache_check(self, tmp_path):
        spec = _make_trend_spec()
        # tmp_path is empty — without injection this would BLOCK.
        # With injection, cache check is skipped.
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=str(tmp_path),
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "PASS"

    def test_strategy_family_check_before_cache_check(self, tmp_path):
        spec = _make_non_trend_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_candidate_evaluation([spec], cache_dir=str(tmp_path))
        # Blocker should mention "not yet wired" not "no cache file"
        assert "not yet wired" in (r.results[0].blocker or "")


# ---------------------------------------------------------------------------
# TestInjectedProviders
# ---------------------------------------------------------------------------

class TestInjectedProviders:
    def test_pass_with_both_providers(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "PASS"

    def test_blocked_with_no_providers_and_no_cache(self, tmp_path):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation([spec], cache_dir=str(tmp_path))
        assert r.results[0].result == "BLOCKED"

    def test_error_on_data_provider_exception(self):
        def exploding_dp(symbol, interval):
            raise RuntimeError("data fetch failed")

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=exploding_dp,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].result == "ERROR"
        assert "data fetch failed" in (r.results[0].blocker or "")

    def test_error_on_backtest_runner_exception(self):
        def exploding_br(data, candidate):
            raise ValueError("runner exploded")

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=exploding_br,
        )
        assert r.results[0].result == "ERROR"

    def test_one_error_does_not_stop_others(self):
        call_count = [0]

        def counting_runner(data, candidate):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("second exploded")
            return _fake_backtest_runner(data, candidate)

        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
            _make_trend_spec(candidate_id="A_IWM_60m_trend_breakout_1to2d", symbol="IWM"),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=counting_runner,
        )
        assert len(r.results) == 3
        assert r.results[1].result == "ERROR"
        assert r.results[0].result == "PASS"
        assert r.results[2].result == "PASS"

    def test_injection_does_not_bypass_strategy_family_check(self):
        spec = _make_non_trend_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        # Strategy family check happens before injection check
        assert r.results[0].result == "BLOCKED"
        assert "not yet wired" in (r.results[0].blocker or "")

    def test_injected_candidate_id_preserved(self):
        spec = _make_trend_spec(candidate_id="MY_CUSTOM_ID")
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].candidate_id == "MY_CUSTOM_ID"

    def test_injected_metrics_from_runner(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.results[0].metrics["sharpe"] == 1.2
        assert r.results[0].metrics["total_trades"] == 80


# ---------------------------------------------------------------------------
# TestCounters
# ---------------------------------------------------------------------------

class TestCounters:
    def test_candidates_requested_with_max(self):
        universe = build_initial_candidate_universe()
        r = run_cached_candidate_evaluation(
            universe,
            max_candidates=5,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.candidates_requested == 5

    def test_candidates_requested_without_max(self):
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.candidates_requested == 2

    def test_candidates_blocked_all_non_wired(self):
        specs = [
            _make_non_trend_spec(StrategyFamily.MEAN_REVERSION),
            _make_non_trend_spec(StrategyFamily.GAP_OVERNIGHT),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.candidates_blocked == 2
        assert r.candidates_passed == 0

    def test_candidates_passed_with_injection(self):
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.candidates_passed == 2
        assert r.candidates_blocked == 0

    def test_candidates_error_on_exception(self):
        def always_explodes(data, candidate):
            raise RuntimeError("boom")

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=always_explodes,
        )
        assert r.candidates_error == 1

    def test_candidates_evaluated_equals_passed_plus_error(self):
        call_count = [0]

        def mixed_runner(data, candidate):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("error on first")
            return _fake_backtest_runner(data, candidate)

        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=mixed_runner,
        )
        assert r.candidates_evaluated == r.candidates_passed + r.candidates_error

    def test_counters_sum_to_requested(self):
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_non_trend_spec(StrategyFamily.MEAN_REVERSION),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        total = r.candidates_passed + r.candidates_blocked + r.candidates_error
        assert total == r.candidates_requested


# ---------------------------------------------------------------------------
# TestOverallResult
# ---------------------------------------------------------------------------

class TestOverallResult:
    def test_result_pass_when_any_pass(self):
        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_non_trend_spec(StrategyFamily.MEAN_REVERSION),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.result == "PASS"
        assert r.blocker is None

    def test_result_blocked_when_all_blocked(self):
        specs = [
            _make_non_trend_spec(StrategyFamily.MEAN_REVERSION),
            _make_non_trend_spec(StrategyFamily.GAP_OVERNIGHT),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.result == "BLOCKED"
        assert r.blocker is not None

    def test_result_blocked_when_empty(self):
        r = run_cached_candidate_evaluation([])
        assert r.result == "BLOCKED"

    def test_result_error_when_any_error_and_none_pass(self):
        def always_explodes(data, candidate):
            raise RuntimeError("boom")

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=always_explodes,
        )
        assert r.result == "ERROR"
        assert r.blocker is not None

    def test_result_pass_when_some_error_but_some_pass(self):
        call_count = [0]

        def mixed_runner(data, candidate):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("error on first")
            return _fake_backtest_runner(data, candidate)

        specs = [
            _make_trend_spec(candidate_id="A_SPY_60m_trend_breakout_1to2d"),
            _make_trend_spec(candidate_id="A_QQQ_60m_trend_breakout_1to2d", symbol="QQQ"),
        ]
        r = run_cached_candidate_evaluation(
            specs,
            _data_provider=_fake_data_provider,
            _backtest_runner=mixed_runner,
        )
        # n_passed > 0 → "PASS" even though one errored
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_all_flags_false_on_blocked(self):
        r = run_cached_candidate_evaluation([])
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_pass(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_error(self):
        def exploding(data, candidate):
            raise RuntimeError("boom")

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=exploding,
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.live_trading_allowed is False

    def test_individual_results_flags_false(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        for res in r.results:
            assert res.broker_calls_made is False
            assert res.credentials_read is False
            assert res.network_calls_made is False
            assert res.order_action_requested is False
            assert res.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestMetricsFromInjectedRunner
# ---------------------------------------------------------------------------

class TestMetricsFromInjectedRunner:
    def test_all_required_keys_present_on_pass(self):
        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        for k in REQUIRED_METRIC_KEYS:
            assert k in r.results[0].metrics, f"missing key {k!r}"

    def test_all_required_keys_present_on_blocked_family(self):
        spec = _make_non_trend_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        for k in REQUIRED_METRIC_KEYS:
            assert k in r.results[0].metrics, f"missing key {k!r}"

    def test_metrics_none_on_blocked(self):
        spec = _make_non_trend_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=_fake_backtest_runner,
        )
        for k in REQUIRED_METRIC_KEYS:
            assert r.results[0].metrics[k] is None

    def test_unknown_runner_keys_not_in_metrics(self):
        def runner_with_extra(data, candidate):
            m = _fake_backtest_runner(data, candidate)
            m["unknown_extra_key"] = 999
            return m

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=_fake_data_provider,
            _backtest_runner=runner_with_extra,
        )
        assert "unknown_extra_key" not in r.results[0].metrics


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.cached_candidate_runner as _m
        return inspect.getsource(_m)

    def test_no_alpaca_broker_adapter(self):
        assert "AlpacaBrokerAdapter" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_requests(self):
        assert "requests" not in self._src()

    def test_no_live_submit(self):
        assert "live_submit" not in self._src()

    def test_no_live_trading_allowed_true(self):
        assert "live_trading_allowed=True" not in self._src()

    def test_no_broker_calls_true(self):
        assert "broker_calls_made=True" not in self._src()


# ---------------------------------------------------------------------------
# Helpers for S5b tests
# ---------------------------------------------------------------------------

def _make_tz_aware_df(dates: list[str]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a tz-aware DatetimeIndex."""
    idx = pd.to_datetime(dates, utc=True).tz_convert("America/New_York")
    return pd.DataFrame(
        {
            "open": [100.0] * len(dates),
            "high": [105.0] * len(dates),
            "low": [95.0] * len(dates),
            "close": [102.0] * len(dates),
            "volume": [1000] * len(dates),
        },
        index=idx,
    )


def _write_csv(tmp_path: pathlib.Path, symbol: str, interval: str, df: pd.DataFrame) -> pathlib.Path:
    path = tmp_path / f"{symbol}_2020-01-01_{interval}.csv"
    df.to_csv(path)
    return path


# ---------------------------------------------------------------------------
# TestSliceCachedDf
# ---------------------------------------------------------------------------

class TestSliceCachedDf:
    """Direct unit tests for _slice_cached_df()."""

    def _df(self) -> pd.DataFrame:
        dates = [
            "2020-01-02", "2020-02-03", "2020-03-04",
            "2020-04-01", "2020-05-01",
        ]
        return _make_tz_aware_df(dates)

    def test_no_dates_returns_original(self):
        df = self._df()
        out, err = _slice_cached_df(df, None, None, "SPY", "1d")
        assert err is None
        assert len(out) == len(df)

    def test_start_date_only(self):
        df = self._df()
        out, err = _slice_cached_df(df, "2020-03-01", None, "SPY", "1d")
        assert err is None
        assert len(out) == 3  # 2020-03-04, 2020-04-01, 2020-05-01

    def test_start_date_only_count(self):
        df = self._df()
        # "2020-04-01" UTC → "2020-03-31" NY date; only "2020-05-01" UTC
        # (NY date 2020-04-30) satisfies >= 2020-04-01
        out, err = _slice_cached_df(df, "2020-04-01", None, "SPY", "1d")
        assert err is None
        assert len(out) == 1

    def test_end_date_only(self):
        df = self._df()
        out, err = _slice_cached_df(df, None, "2020-02-28", "SPY", "1d")
        assert err is None
        assert len(out) == 2  # 2020-01-02, 2020-02-03

    def test_start_and_end(self):
        df = self._df()
        # UTC dates shift to NY: "2020-02-03" UTC → NY 2020-02-02,
        # "2020-03-04" UTC → NY 2020-03-03, "2020-04-01" UTC → NY 2020-03-31.
        # All three NY dates fall in [2020-02-01, 2020-03-31].
        out, err = _slice_cached_df(df, "2020-02-01", "2020-03-31", "SPY", "1d")
        assert err is None
        assert len(out) == 3

    def test_inclusive_boundaries(self):
        df = self._df()
        # "2020-01-02" UTC → NY date 2020-01-01; slice on that NY date.
        out, err = _slice_cached_df(df, "2020-01-01", "2020-01-01", "SPY", "1d")
        assert err is None
        assert len(out) == 1

    def test_empty_result_no_error(self):
        df = self._df()
        out, err = _slice_cached_df(df, "2030-01-01", "2030-12-31", "SPY", "1d")
        assert err is None
        assert len(out) == 0

    def test_non_datetimeindex_returns_error(self):
        df = pd.DataFrame({"close": [1.0, 2.0]}, index=[0, 1])
        out, err = _slice_cached_df(df, "2020-01-01", None, "SPY", "1d")
        assert err is not None
        assert "DatetimeIndex" in err
        assert "SPY" in err
        assert "1d" in err

    def test_does_not_mutate_original(self):
        df = self._df()
        original_len = len(df)
        _slice_cached_df(df, "2020-04-01", "2020-12-31", "SPY", "1d")
        assert len(df) == original_len

    def test_returns_copy(self):
        df = self._df()
        out, _ = _slice_cached_df(df, "2020-01-01", "2020-12-31", "SPY", "1d")
        assert out is not df

    def test_invalid_start_date_returns_error(self):
        df = self._df()
        out, err = _slice_cached_df(df, "not-a-date", None, "SPY", "1d")
        assert err is not None
        assert "invalid date range" in err
        assert "SPY" in err
        assert "1d" in err

    def test_invalid_end_date_returns_error(self):
        df = self._df()
        out, err = _slice_cached_df(df, None, "2020-13-45", "SPY", "1d")
        assert err is not None
        assert "invalid date range" in err

    def test_invalid_date_no_exception_escapes(self):
        df = self._df()
        try:
            _slice_cached_df(df, "bad", "also-bad", "SPY", "1d")
        except Exception as exc:
            pytest.fail(f"_slice_cached_df raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# TestDateRangeSlicingIntegration
# ---------------------------------------------------------------------------

class TestDateRangeSlicingIntegration:
    """Integration tests: _start_date/_end_date flow through run_cached_candidate_evaluation()."""

    def _make_csv(self, tmp_path: pathlib.Path) -> pathlib.Path:
        dates = [
            "2020-01-02", "2020-01-03", "2020-02-03",
            "2020-03-02", "2020-04-01", "2020-05-01",
        ]
        df = _make_tz_aware_df(dates)
        return _write_csv(tmp_path, "SPY", "1d", df)

    def test_no_slice_no_date_params_uses_full_df(self, tmp_path):
        # When no _start_date/_end_date given, no slicing occurs.
        # We verify the run completes (BLOCKED on strategy/data, not on slicing).
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        r = run_cached_candidate_evaluation([spec], cache_dir=tmp_path)
        # Result is whatever the real backtest returns; slicing blocker must not appear.
        for res in r.results:
            assert "no cached rows in requested date range" not in (res.blocker or "")

    def test_date_range_outside_cache_gives_blocked(self, tmp_path):
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=tmp_path,
            _start_date="2025-01-01",
            _end_date="2025-12-31",
        )
        assert r.result == "BLOCKED"
        assert r.results[0].result == "BLOCKED"
        assert "no cached rows in requested date range" in (r.results[0].blocker or "")
        assert "SPY" in (r.results[0].blocker or "")

    def test_injection_mode_ignores_dates(self):
        """With both _data_provider and _backtest_runner, date params are ignored."""
        called = []

        def dp(symbol, iv):
            called.append("dp")
            return _fake_data_provider(symbol, iv)

        def runner(data, candidate):
            called.append("runner")
            return _fake_backtest_runner(data, candidate)

        spec = _make_trend_spec()
        r = run_cached_candidate_evaluation(
            [spec],
            _data_provider=dp,
            _backtest_runner=runner,
            _start_date="2020-01-01",
            _end_date="2020-06-30",
        )
        assert "dp" in called
        assert "runner" in called
        assert r.result == "PASS"

    def test_slicing_does_not_affect_safety_flags(self, tmp_path):
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=tmp_path,
            _start_date="2025-01-01",
            _end_date="2025-12-31",
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_date_range_with_partial_overlap(self, tmp_path):
        # Slice to a window that includes some (but not all) rows.
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        # The CSV has NY dates: 2020-01-01, 2020-01-02, 2020-02-02, 2020-03-01, 2020-03-31, 2020-04-30
        # Slice to include only NY dates >= 2020-03-01
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=tmp_path,
            _start_date="2020-03-01",
        )
        # Must not BLOCKED on "no cached rows" (there are rows in that range)
        assert "no cached rows in requested date range" not in (r.results[0].blocker or "")

    def test_invalid_start_date_gives_blocked(self, tmp_path):
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=tmp_path,
            _start_date="not-a-date",
        )
        assert r.result == "BLOCKED"
        assert r.results[0].result == "BLOCKED"
        assert "invalid date range" in (r.results[0].blocker or "")

    def test_invalid_end_date_gives_blocked(self, tmp_path):
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=tmp_path,
            _end_date="2020-99-99",
        )
        assert r.result == "BLOCKED"
        assert r.results[0].result == "BLOCKED"
        assert "invalid date range" in (r.results[0].blocker or "")

    def test_invalid_date_no_exception_escapes_integration(self, tmp_path):
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        try:
            run_cached_candidate_evaluation(
                [spec],
                cache_dir=tmp_path,
                _start_date="bad-date",
                _end_date="also-bad",
            )
        except Exception as exc:
            pytest.fail(f"run_cached_candidate_evaluation raised unexpectedly: {exc}")

    def test_invalid_date_blocked_result_has_required_metric_keys(self, tmp_path):
        self._make_csv(tmp_path)
        spec = _make_trend_spec(interval="1d")
        r = run_cached_candidate_evaluation(
            [spec],
            cache_dir=tmp_path,
            _start_date="not-a-date",
        )
        assert r.results[0].result == "BLOCKED"
        for key in REQUIRED_METRIC_KEYS:
            assert key in r.results[0].metrics
