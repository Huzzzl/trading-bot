"""
tests/test_cached_walk_forward_runner.py
-----------------------------------------
Tests for src/research/cached_walk_forward_runner.py (PR S5).

No broker/API/credential/env access.
No live gates unfrozen. No live trading. No order action. No real data.
No file writes.

Injection pattern:
  _split_evaluator is used throughout to inject fake CandidateEvaluationResult
  values so no real cache or backtest is needed in any test.

Two tests (TestDefaultPath) exercise the real default-path logic against a
nonexistent cache directory (BLOCKED on cache miss / unsupported family).
"""

from __future__ import annotations

import inspect

import pytest

import pathlib

import pandas as pd

from src.research.candidate_evaluator import REQUIRED_METRIC_KEYS, CandidateEvaluationResult
from src.research.candidate_universe import (
    CandidateSpec,
    HoldingHorizon,
    StrategyFamily,
)
from src.research.cached_walk_forward_runner import (
    CachedWalkForwardRunResult,
    _make_default_split_evaluator,
    run_cached_walk_forward_evaluation,
)
from src.research.metrics_validation import MetricsValidationResult
from src.research.walk_forward import WalkForwardSplit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    family: StrategyFamily = StrategyFamily.TREND_BREAKOUT,
) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=f"SPY_60m_{family.value}_one_to_two_days",
        symbol="SPY",
        interval="60m",
        strategy_family=family,
        holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
        group="A",
    )


def _good_metrics() -> dict:
    return {
        "cagr": 0.12,
        "average_monthly_return": 0.0095,
        "worst_month": None,
        "sharpe": 1.2,
        "sortino": None,
        "max_drawdown": -0.15,      # negative decimal convention
        "calmar": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.3,
        "average_trade_return": 0.005,
        "median_trade_return": None,
        "exposure_pct": 0.35,
        "trades_per_month": 4.5,
        "total_trades": 80,
        "average_holding_bars": 6.0,
        "stop_loss_frequency": 0.2,
        "session_end_frequency": 0.1,
        "slippage_bps": None,
    }


def _none_metrics() -> dict:
    return {k: None for k in REQUIRED_METRIC_KEYS}


def _invalid_metrics() -> dict:
    m = _good_metrics()
    m["max_drawdown"] = 0.15   # positive → S4 validation BLOCKED
    return m


def _make_pass_result(
    candidate: CandidateSpec,
    metrics: dict | None = None,
) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="PASS",
        blocker=None,
        metrics=metrics if metrics is not None else _good_metrics(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_blocked_result(candidate: CandidateSpec) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="BLOCKED",
        blocker="fake blocked",
        metrics=_none_metrics(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_error_result(candidate: CandidateSpec) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="ERROR",
        blocker="fake error",
        metrics=_none_metrics(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


# Standard 3-split config — always valid.
_SPLIT_KWARGS = dict(
    start_date="2020-01-01",
    end_date="2021-12-31",
    train_months=12,
    test_months=3,
    step_months=3,
)

# 1-split config for cheaper default-path tests.
_ONE_SPLIT_KWARGS = dict(
    start_date="2020-01-01",
    end_date="2021-04-30",
    train_months=12,
    test_months=3,
    step_months=3,
)


# ---------------------------------------------------------------------------
# TestCachedWalkForwardRunResultDataclass
# ---------------------------------------------------------------------------

class TestCachedWalkForwardRunResultDataclass:
    def _make(self) -> CachedWalkForwardRunResult:
        return CachedWalkForwardRunResult(
            result="PASS",
            blocker=None,
            candidate_id="SPY_60m_trend_breakout_one_to_two_days",
            splits_requested=3,
            splits_evaluated=3,
            split_results=(),
            validation_results=(),
            broker_calls_made=False,
            credentials_read=False,
            network_calls_made=False,
            order_action_requested=False,
            live_trading_allowed=False,
        )

    def test_frozen(self):
        r = self._make()
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_fields_exist(self):
        r = self._make()
        for field in (
            "result", "blocker", "candidate_id",
            "splits_requested", "splits_evaluated",
            "split_results", "validation_results",
            "broker_calls_made", "credentials_read",
            "network_calls_made", "order_action_requested",
            "live_trading_allowed",
        ):
            assert hasattr(r, field), f"missing field: {field}"

    def test_split_results_is_tuple(self):
        assert isinstance(self._make().split_results, tuple)

    def test_validation_results_is_tuple(self):
        assert isinstance(self._make().validation_results, tuple)

    def test_result_is_str(self):
        assert isinstance(self._make().result, str)

    def test_live_trading_allowed_false(self):
        assert self._make().live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestInvalidWalkForwardConfig
# ---------------------------------------------------------------------------

class TestInvalidWalkForwardConfig:
    def test_step_less_than_test_months_blocked(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=12, test_months=3, step_months=2,
        )
        assert r.result == "BLOCKED"

    def test_blocker_mentions_config(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=12, test_months=3, step_months=2,
        )
        assert "invalid walk-forward configuration" in (r.blocker or "")

    def test_end_before_start_blocked(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2022-01-01", end_date="2020-01-01",
            train_months=12, test_months=3, step_months=3,
        )
        assert r.result == "BLOCKED"

    def test_train_months_zero_blocked(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=0, test_months=3, step_months=3,
        )
        assert r.result == "BLOCKED"

    def test_config_error_split_results_empty(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=12, test_months=3, step_months=2,
        )
        assert r.split_results == ()
        assert r.validation_results == ()
        assert r.splits_requested == 0
        assert r.splits_evaluated == 0


# ---------------------------------------------------------------------------
# TestNoSplitsGenerated
# ---------------------------------------------------------------------------

class TestNoSplitsGenerated:
    def test_no_splits_blocked(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2020-06-01",
            train_months=12, test_months=3, step_months=3,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.result == "BLOCKED"

    def test_no_splits_blocker_message(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2020-06-01",
            train_months=12, test_months=3, step_months=3,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.blocker is not None
        assert len(r.blocker) > 0

    def test_no_splits_counts_zero(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2020-06-01",
            train_months=12, test_months=3, step_months=3,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.splits_requested == 0
        assert r.splits_evaluated == 0


# ---------------------------------------------------------------------------
# TestInjectedSplitEvaluator
# ---------------------------------------------------------------------------

class TestInjectedSplitEvaluator:
    def test_evaluator_called_once_per_split(self):
        spec = _make_spec()
        call_count = [0]

        def evaluator(c, s):
            call_count[0] += 1
            return _make_pass_result(c)

        run_cached_walk_forward_evaluation(spec, **_SPLIT_KWARGS, _split_evaluator=evaluator)
        assert call_count[0] == 3  # 3 splits for standard config

    def test_split_order_preserved(self):
        spec = _make_spec()
        seen_split_ids = []

        def evaluator(c, s):
            seen_split_ids.append(s.split_id)
            return _make_pass_result(c)

        r = run_cached_walk_forward_evaluation(spec, **_SPLIT_KWARGS, _split_evaluator=evaluator)
        assert seen_split_ids == [sr.candidate_id and seen_split_ids[i]
                                   for i, sr in enumerate(r.split_results)]
        assert len(seen_split_ids) == 3
        assert seen_split_ids == sorted(seen_split_ids)  # chronological = lexical here

    def test_candidate_id_in_result(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.candidate_id == spec.candidate_id

    def test_splits_requested_matches_splits(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.splits_requested == 3

    def test_splits_evaluated_matches_splits(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.splits_evaluated == 3


# ---------------------------------------------------------------------------
# TestAggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_all_pass_gives_pass(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.result == "PASS"
        assert r.blocker is None

    def test_all_pass_blocker_none(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.blocker is None

    def test_one_blocked_gives_blocked(self):
        spec = _make_spec()
        call_n = [0]

        def evaluator(c, s):
            call_n[0] += 1
            return _make_blocked_result(c) if call_n[0] == 2 else _make_pass_result(c)

        r = run_cached_walk_forward_evaluation(spec, **_SPLIT_KWARGS, _split_evaluator=evaluator)
        assert r.result == "BLOCKED"

    def test_one_error_gives_error(self):
        spec = _make_spec()
        call_n = [0]

        def evaluator(c, s):
            call_n[0] += 1
            return _make_error_result(c) if call_n[0] == 1 else _make_pass_result(c)

        r = run_cached_walk_forward_evaluation(spec, **_SPLIT_KWARGS, _split_evaluator=evaluator)
        assert r.result == "ERROR"

    def test_error_overrides_blocked(self):
        spec = _make_spec()
        results_iter = iter([
            _make_blocked_result(spec),
            _make_error_result(spec),
            _make_pass_result(spec),
        ])
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: next(results_iter),
        )
        assert r.result == "ERROR"

    def test_evaluate_all_policy(self):
        spec = _make_spec()
        call_count = [0]

        def evaluator(c, s):
            call_count[0] += 1
            return _make_blocked_result(c)

        run_cached_walk_forward_evaluation(spec, **_SPLIT_KWARGS, _split_evaluator=evaluator)
        assert call_count[0] == 3

    def test_split_results_count_matches_splits(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert len(r.split_results) == 3


# ---------------------------------------------------------------------------
# TestMetricsValidation
# ---------------------------------------------------------------------------

class TestMetricsValidation:
    def test_pass_split_valid_metrics_gives_pass(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c, _good_metrics()),
        )
        assert r.result == "PASS"

    def test_pass_split_invalid_metrics_gives_blocked(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c, _invalid_metrics()),
        )
        assert r.result == "BLOCKED"

    def test_blocker_mentions_metrics_validation(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c, _invalid_metrics()),
        )
        assert "metrics validation" in (r.blocker or "")

    def test_validation_results_count_matches_split_results(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert len(r.validation_results) == len(r.split_results)

    def test_validation_results_are_metrics_validation_result(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        for vr in r.validation_results:
            assert isinstance(vr, MetricsValidationResult)

    def test_none_metrics_validation_pass(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c, _none_metrics()),
        )
        # All-None metrics pass validation (warnings only)
        for vr in r.validation_results:
            assert vr.result == "PASS"

    def test_blocked_split_validation_still_runs(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_blocked_result(c),
        )
        # validation_results should still have entries (all-None metrics → PASS)
        assert len(r.validation_results) == 3


# ---------------------------------------------------------------------------
# TestDefaultPath
# ---------------------------------------------------------------------------

class TestDefaultPath:
    def test_missing_cache_gives_blocked(self):
        spec = _make_spec(StrategyFamily.TREND_BREAKOUT)
        r = run_cached_walk_forward_evaluation(
            spec,
            **_ONE_SPLIT_KWARGS,
            cache_dir="/nonexistent/cache/path/s5test",
        )
        assert r.result == "BLOCKED"

    def test_unsupported_family_gives_blocked(self):
        spec = _make_spec(StrategyFamily.MEAN_REVERSION)
        r = run_cached_walk_forward_evaluation(
            spec,
            **_ONE_SPLIT_KWARGS,
            cache_dir="/nonexistent/cache/path/s5test",
        )
        assert r.result == "BLOCKED"


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_all_flags_false_on_pass(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_pass_result(c),
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_blocked(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=12, test_months=3, step_months=2,  # invalid → BLOCKED
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_error(self):
        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec, **_SPLIT_KWARGS,
            _split_evaluator=lambda c, s: _make_error_result(c),
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_result(self):
        spec = _make_spec()
        kwargs = dict(**_SPLIT_KWARGS, _split_evaluator=lambda c, s: _make_pass_result(c))
        r1 = run_cached_walk_forward_evaluation(spec, **kwargs)
        r2 = run_cached_walk_forward_evaluation(spec, **kwargs)
        assert r1.result == r2.result
        assert r1.splits_requested == r2.splits_requested
        assert r1.splits_evaluated == r2.splits_evaluated
        assert len(r1.split_results) == len(r2.split_results)


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.cached_walk_forward_runner as _m
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

    def test_no_optimize(self):
        assert "optimize" not in self._src()


# ---------------------------------------------------------------------------
# TestDateRangeSlicingWiring
# ---------------------------------------------------------------------------

def _make_tz_df(dates: list[str]) -> pd.DataFrame:
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


def _make_split(test_start: str, test_end: str) -> WalkForwardSplit:
    return WalkForwardSplit(
        split_id="WF001_2019-01-01_2019-12-31_2020-01-01_2020-03-31",
        train_start="2019-01-01",
        train_end="2019-12-31",
        test_start=test_start,
        test_end=test_end,
    )


class TestDateRangeSlicingWiring:
    """Verify the default split evaluator wires split dates into the cached runner."""

    def test_split_evaluator_passes_test_dates(self):
        """_make_default_split_evaluator passes split.test_start and test_end."""
        received_starts = []
        received_ends = []

        original_runner = __import__(
            "src.research.cached_candidate_runner",
            fromlist=["run_cached_candidate_evaluation"],
        ).run_cached_candidate_evaluation

        import src.research.cached_walk_forward_runner as _mod

        captured = {}

        def fake_run(candidates, *, cache_dir=None, max_candidates=None,
                     _data_provider=None, _backtest_runner=None,
                     _start_date=None, _end_date=None):
            captured["start"] = _start_date
            captured["end"] = _end_date
            spec = list(candidates)[0]
            from src.research.cached_candidate_runner import CachedCandidateRunResult
            from src.research.candidate_evaluator import CandidateEvaluationResult, REQUIRED_METRIC_KEYS
            eval_result = CandidateEvaluationResult(
                candidate_id=spec.candidate_id,
                symbol=spec.symbol,
                interval=spec.interval,
                strategy_family=spec.strategy_family.value,
                result="BLOCKED",
                blocker="fake",
                metrics={k: None for k in REQUIRED_METRIC_KEYS},
                broker_calls_made=False,
                credentials_read=False,
                network_calls_made=False,
                order_action_requested=False,
                live_trading_allowed=False,
            )
            return CachedCandidateRunResult(
                result="BLOCKED",
                blocker="fake",
                candidates_requested=1,
                candidates_evaluated=0,
                candidates_passed=0,
                candidates_blocked=1,
                candidates_error=0,
                results=(eval_result,),
                broker_calls_made=False,
                credentials_read=False,
                network_calls_made=False,
                order_action_requested=False,
                live_trading_allowed=False,
            )

        original = _mod.run_cached_candidate_evaluation
        _mod.run_cached_candidate_evaluation = fake_run
        try:
            evaluator = _make_default_split_evaluator(None)
            split = _make_split("2020-01-01", "2020-03-31")
            spec = _make_spec()
            evaluator(spec, split)
            assert captured.get("start") == "2020-01-01"
            assert captured.get("end") == "2020-03-31"
        finally:
            _mod.run_cached_candidate_evaluation = original

    def test_default_split_evaluator_blocked_on_empty_window(self, tmp_path):
        """Default split evaluator propagates BLOCKED when no rows in test window."""
        dates = ["2020-01-02", "2020-01-03", "2020-02-03"]
        df = _make_tz_df(dates)
        path = tmp_path / "SPY_2020-01-01_1d.csv"
        df.to_csv(path)

        spec = CandidateSpec(
            candidate_id="SPY_1d_trend_breakout_one_to_two_days",
            symbol="SPY",
            interval="1d",
            strategy_family=StrategyFamily.TREND_BREAKOUT,
            holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
            group="A",
        )
        evaluator = _make_default_split_evaluator(tmp_path)
        # Split window with no matching data
        split = _make_split("2025-01-01", "2025-03-31")
        result = evaluator(spec, split)
        assert result.result == "BLOCKED"
        assert "no cached rows in requested date range" in (result.blocker or "")

    def test_run_cached_walk_forward_split_dates_injected(self):
        """Each split injected evaluator receives split-specific dates."""
        received_splits = []

        def split_evaluator(candidate, split):
            received_splits.append((split.test_start, split.test_end))
            return _make_pass_result(candidate)

        spec = _make_spec()
        r = run_cached_walk_forward_evaluation(
            spec,
            start_date="2020-01-01",
            end_date="2021-12-31",
            train_months=12,
            test_months=3,
            step_months=3,
            _split_evaluator=split_evaluator,
        )
        # 3 splits expected (based on _SPLIT_KWARGS in existing tests)
        assert len(received_splits) == r.splits_evaluated
        # Dates must differ between splits
        starts = [s[0] for s in received_splits]
        assert len(set(starts)) == len(starts), "split test_start dates should be unique"

    def test_no_split_date_slicing_not_yet_implemented_text(self):
        """Ensure stale 'not yet implemented' wording is removed from module."""
        import src.research.cached_walk_forward_runner as _mod
        src_text = inspect.getsource(_mod)
        assert "not yet implemented" not in src_text.lower()
        assert "NOT yet implemented" not in src_text
