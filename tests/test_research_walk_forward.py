"""
tests/test_research_walk_forward.py
------------------------------------
Tests for src/research/walk_forward.py (PR S4).

No broker/API/credential/env access.
No live gates unfrozen. No live trading. No order action. No real data.

Walk-forward design under test:
  - Rolling splits: train/test windows, step_months advance.
  - train_end = _add_months(train_start, train_months)   (boundary date, inclusive)
  - test_start = train_end + 1 day
  - test_end   = _add_months(test_start, test_months) - 1 day
  - Stop when test_end > end_date.
  - Split ID: WF{n:03d}_{train_start}_{train_end}_{test_start}_{test_end}
  - Train windows may overlap; test windows never overlap.
  - Month arithmetic clamps day to month-end.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from src.research.candidate_evaluator import REQUIRED_METRIC_KEYS, CandidateEvaluationResult
from src.research.candidate_universe import CandidateSpec, StrategyFamily, HoldingHorizon
from src.research.walk_forward import (
    WalkForwardResult,
    WalkForwardSplit,
    _add_months,
    build_walk_forward_splits,
    evaluate_walk_forward,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec() -> CandidateSpec:
    return CandidateSpec(
        candidate_id="SPY_60m_trend_breakout_one_to_two_days",
        symbol="SPY",
        interval="60m",
        strategy_family=StrategyFamily.TREND_BREAKOUT,
        holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
        group="A",
    )


def _pass_result(candidate: CandidateSpec) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="PASS",
        blocker=None,
        metrics={k: None for k in REQUIRED_METRIC_KEYS},
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _blocked_result(candidate: CandidateSpec, reason: str = "blocked") -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="BLOCKED",
        blocker=reason,
        metrics={k: None for k in REQUIRED_METRIC_KEYS},
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _error_result(candidate: CandidateSpec, reason: str = "error") -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="ERROR",
        blocker=reason,
        metrics={k: None for k in REQUIRED_METRIC_KEYS},
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


# ---------------------------------------------------------------------------
# TestAddMonths
# ---------------------------------------------------------------------------

class TestAddMonths:
    def test_simple_add(self):
        d = date(2020, 1, 1)
        assert _add_months(d, 3) == date(2020, 4, 1)

    def test_year_rollover(self):
        d = date(2020, 11, 1)
        assert _add_months(d, 3) == date(2021, 2, 1)

    def test_clamp_to_month_end(self):
        d = date(2020, 1, 31)
        # Jan 31 + 1 month → Feb 29 (2020 is a leap year)
        assert _add_months(d, 1) == date(2020, 2, 29)

    def test_clamp_non_leap_year(self):
        d = date(2021, 1, 31)
        # Jan 31 + 1 month → Feb 28 (2021 is not a leap year)
        assert _add_months(d, 1) == date(2021, 2, 28)

    def test_twelve_months(self):
        d = date(2020, 1, 1)
        assert _add_months(d, 12) == date(2021, 1, 1)


# ---------------------------------------------------------------------------
# TestWalkForwardSplitDataclass
# ---------------------------------------------------------------------------

class TestWalkForwardSplitDataclass:
    def test_frozen(self):
        s = WalkForwardSplit(
            split_id="WF001_2020-01-01_2021-01-01_2021-01-02_2021-04-01",
            train_start="2020-01-01",
            train_end="2021-01-01",
            test_start="2021-01-02",
            test_end="2021-04-01",
        )
        with pytest.raises(Exception):
            s.split_id = "mutated"  # type: ignore[misc]

    def test_fields_exist(self):
        s = WalkForwardSplit(
            split_id="WF001_2020-01-01_2021-01-01_2021-01-02_2021-04-01",
            train_start="2020-01-01",
            train_end="2021-01-01",
            test_start="2021-01-02",
            test_end="2021-04-01",
        )
        assert hasattr(s, "split_id")
        assert hasattr(s, "train_start")
        assert hasattr(s, "train_end")
        assert hasattr(s, "test_start")
        assert hasattr(s, "test_end")

    def test_split_id_stored(self):
        s = WalkForwardSplit(
            split_id="WF001_2020-01-01_2021-01-01_2021-01-02_2021-04-01",
            train_start="2020-01-01",
            train_end="2021-01-01",
            test_start="2021-01-02",
            test_end="2021-04-01",
        )
        assert s.split_id == "WF001_2020-01-01_2021-01-01_2021-01-02_2021-04-01"


# ---------------------------------------------------------------------------
# TestWalkForwardResultDataclass
# ---------------------------------------------------------------------------

class TestWalkForwardResultDataclass:
    def _make(self) -> WalkForwardResult:
        return WalkForwardResult(
            result="PASS",
            blocker=None,
            candidate_id="SPY_60m_trend_breakout_one_to_two_days",
            splits_requested=3,
            splits_evaluated=3,
            split_results=(),
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
        assert hasattr(r, "result")
        assert hasattr(r, "blocker")
        assert hasattr(r, "candidate_id")
        assert hasattr(r, "splits_requested")
        assert hasattr(r, "splits_evaluated")
        assert hasattr(r, "split_results")
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")

    def test_split_results_is_tuple(self):
        r = self._make()
        assert isinstance(r.split_results, tuple)

    def test_live_trading_allowed_false(self):
        r = self._make()
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestBuildWalkForwardSplitsValidation
# ---------------------------------------------------------------------------

class TestBuildWalkForwardSplitsValidation:
    def test_raises_if_train_months_zero(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=0, test_months=3, step_months=3,
            )

    def test_raises_if_test_months_zero(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=12, test_months=0, step_months=3,
            )

    def test_raises_if_step_months_zero(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=12, test_months=3, step_months=0,
            )

    def test_raises_if_end_equals_start(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2020-01-01",
                train_months=12, test_months=3, step_months=3,
            )

    def test_raises_if_end_before_start(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2021-01-01", end_date="2020-01-01",
                train_months=12, test_months=3, step_months=3,
            )

    def test_raises_invalid_start_date(self):
        with pytest.raises((ValueError, TypeError)):
            build_walk_forward_splits(
                start_date="not-a-date", end_date="2022-12-31",
                train_months=12, test_months=3, step_months=3,
            )

    def test_raises_negative_train_months(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=-1, test_months=3, step_months=3,
            )

    def test_raises_if_step_months_less_than_test_months(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=12, test_months=3, step_months=2,
            )

    def test_raises_step_one_test_three(self):
        with pytest.raises(ValueError):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=12, test_months=3, step_months=1,
            )

    def test_step_equals_test_months_does_not_raise(self):
        # step_months == test_months is valid (no gap, no overlap)
        result = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=12, test_months=3, step_months=3,
        )
        assert len(result) > 0

    def test_step_greater_than_test_months_does_not_raise(self):
        # step_months > test_months is valid (gap between test windows)
        result = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2022-12-31",
            train_months=12, test_months=3, step_months=6,
        )
        assert len(result) > 0

    def test_error_message_mentions_non_overlapping(self):
        with pytest.raises(ValueError, match="non-overlapping"):
            build_walk_forward_splits(
                start_date="2020-01-01", end_date="2022-12-31",
                train_months=12, test_months=3, step_months=2,
            )


# ---------------------------------------------------------------------------
# TestBuildWalkForwardSplitsOutput
# ---------------------------------------------------------------------------

class TestBuildWalkForwardSplitsOutput:
    """
    Reference configuration for the 3-split example:
      start_date="2020-01-01", end_date="2021-12-31"
      train_months=12, test_months=3, step_months=3

    Expected splits:
      WF001: train 2020-01-01→2021-01-01, test 2021-01-02→2021-04-01
      WF002: train 2020-04-01→2021-04-01, test 2021-04-02→2021-07-01
      WF003: train 2020-07-01→2021-07-01, test 2021-07-02→2021-10-01
      WF004 would have test_end=2022-01-01 > 2021-12-31 → breaks
    """

    def _splits(self):
        return build_walk_forward_splits(
            start_date="2020-01-01",
            end_date="2021-12-31",
            train_months=12,
            test_months=3,
            step_months=3,
        )

    def test_returns_tuple(self):
        assert isinstance(self._splits(), tuple)

    def test_three_splits(self):
        assert len(self._splits()) == 3

    def test_split1_id(self):
        s = self._splits()[0]
        assert s.split_id == "WF001_2020-01-01_2021-01-01_2021-01-02_2021-04-01"

    def test_split1_train_start(self):
        assert self._splits()[0].train_start == "2020-01-01"

    def test_split1_train_end(self):
        assert self._splits()[0].train_end == "2021-01-01"

    def test_split1_test_start(self):
        assert self._splits()[0].test_start == "2021-01-02"

    def test_split1_test_end(self):
        assert self._splits()[0].test_end == "2021-04-01"

    def test_split2_id(self):
        s = self._splits()[1]
        assert s.split_id == "WF002_2020-04-01_2021-04-01_2021-04-02_2021-07-01"

    def test_split2_train_start(self):
        assert self._splits()[1].train_start == "2020-04-01"

    def test_split3_id(self):
        s = self._splits()[2]
        assert s.split_id == "WF003_2020-07-01_2021-07-01_2021-07-02_2021-10-01"

    def test_all_splits_are_walk_forward_split(self):
        for s in self._splits():
            assert isinstance(s, WalkForwardSplit)

    def test_empty_when_no_splits_fit(self):
        result = build_walk_forward_splits(
            start_date="2020-01-01",
            end_date="2020-06-01",
            train_months=12,
            test_months=3,
            step_months=3,
        )
        assert result == ()

    def test_deterministic(self):
        s1 = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )
        s2 = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )
        assert s1 == s2

    def test_test_windows_do_not_overlap(self):
        splits = self._splits()
        for i in range(len(splits) - 1):
            current_test_end = date.fromisoformat(splits[i].test_end)
            next_test_start = date.fromisoformat(splits[i + 1].test_start)
            assert next_test_start > current_test_end

    def test_single_split_possible(self):
        result = build_walk_forward_splits(
            start_date="2020-01-01",
            end_date="2021-06-30",
            train_months=12,
            test_months=3,
            step_months=12,
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestEvaluateWalkForwardNoEvaluator
# ---------------------------------------------------------------------------

class TestEvaluateWalkForwardNoEvaluator:
    def test_blocked_when_no_evaluator(self):
        spec = _make_spec()
        splits = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )
        r = evaluate_walk_forward(spec, splits)
        assert r.result == "BLOCKED"

    def test_blocker_message_when_no_evaluator(self):
        spec = _make_spec()
        splits = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )
        r = evaluate_walk_forward(spec, splits)
        assert r.blocker is not None
        assert len(r.blocker) > 0

    def test_no_splits_evaluated_when_no_evaluator(self):
        spec = _make_spec()
        splits = build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )
        r = evaluate_walk_forward(spec, splits)
        assert r.splits_evaluated == 0

    def test_empty_splits_blocked(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, (), split_evaluator=lambda c, s: _pass_result(c))
        assert r.result == "BLOCKED"

    def test_empty_splits_blocker_message(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, (), split_evaluator=lambda c, s: _pass_result(c))
        assert r.blocker is not None


# ---------------------------------------------------------------------------
# TestEvaluateWalkForwardAggregation
# ---------------------------------------------------------------------------

class TestEvaluateWalkForwardAggregation:
    def _splits(self):
        return build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )

    def test_all_pass_gives_pass(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, self._splits(),
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert r.result == "PASS"

    def test_all_pass_blocker_none(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, self._splits(),
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert r.blocker is None

    def test_one_blocked_gives_blocked(self):
        spec = _make_spec()
        call_count = [0]

        def evaluator(c, s):
            call_count[0] += 1
            if call_count[0] == 2:
                return _blocked_result(c)
            return _pass_result(c)

        r = evaluate_walk_forward(spec, self._splits(), split_evaluator=evaluator)
        assert r.result == "BLOCKED"

    def test_one_error_gives_error(self):
        spec = _make_spec()
        call_count = [0]

        def evaluator(c, s):
            call_count[0] += 1
            if call_count[0] == 1:
                return _error_result(c)
            return _pass_result(c)

        r = evaluate_walk_forward(spec, self._splits(), split_evaluator=evaluator)
        assert r.result == "ERROR"

    def test_error_overrides_blocked(self):
        spec = _make_spec()
        results_iter = iter([
            _blocked_result(spec),
            _error_result(spec),
            _pass_result(spec),
        ])
        r = evaluate_walk_forward(spec, self._splits(),
                                  split_evaluator=lambda c, s: next(results_iter))
        assert r.result == "ERROR"

    def test_evaluate_all_policy(self):
        spec = _make_spec()
        call_count = [0]

        def evaluator(c, s):
            call_count[0] += 1
            return _blocked_result(c)

        splits = self._splits()
        evaluate_walk_forward(spec, splits, split_evaluator=evaluator)
        assert call_count[0] == len(splits)

    def test_exception_becomes_error_split(self):
        spec = _make_spec()
        call_count = [0]

        def evaluator(c, s):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated evaluator failure")
            return _pass_result(c)

        splits = self._splits()
        r = evaluate_walk_forward(spec, splits, split_evaluator=evaluator)
        assert r.result == "ERROR"
        assert call_count[0] == len(splits)  # evaluate-all: continued after exception

    def test_split_results_count_matches_splits(self):
        spec = _make_spec()
        splits = self._splits()
        r = evaluate_walk_forward(spec, splits,
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert len(r.split_results) == len(splits)

    def test_splits_requested_stored(self):
        spec = _make_spec()
        splits = self._splits()
        r = evaluate_walk_forward(spec, splits,
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert r.splits_requested == len(splits)

    def test_splits_evaluated_stored(self):
        spec = _make_spec()
        splits = self._splits()
        r = evaluate_walk_forward(spec, splits,
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert r.splits_evaluated == len(splits)

    def test_candidate_id_in_result(self):
        spec = _make_spec()
        splits = self._splits()
        r = evaluate_walk_forward(spec, splits,
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert r.candidate_id == spec.candidate_id


# ---------------------------------------------------------------------------
# TestEvaluateWalkForwardSafetyFlags
# ---------------------------------------------------------------------------

class TestEvaluateWalkForwardSafetyFlags:
    def _splits(self):
        return build_walk_forward_splits(
            start_date="2020-01-01", end_date="2021-12-31",
            train_months=12, test_months=3, step_months=3,
        )

    def test_all_flags_false_on_pass(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, self._splits(),
                                  split_evaluator=lambda c, s: _pass_result(c))
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_blocked_no_evaluator(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, self._splits())
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_error(self):
        spec = _make_spec()
        r = evaluate_walk_forward(spec, self._splits(),
                                  split_evaluator=lambda c, s: _error_result(c))
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.walk_forward as _m
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
