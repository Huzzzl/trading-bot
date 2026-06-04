"""
tests/test_report_snapshot_runner.py
---------------------------------------
Tests for src/research/report_snapshot_runner.py (PR S7).

No broker/API/credential/env access.
No live gates unfrozen. No live trading. No order action. No real data.
No file writes. All walk-forward results are constructed from fake data.
"""

from __future__ import annotations

import inspect
import json

import pytest

from src.research.cached_walk_forward_runner import CachedWalkForwardRunResult
from src.research.candidate_evaluator import REQUIRED_METRIC_KEYS, CandidateEvaluationResult
from src.research.candidate_universe import (
    CandidateSpec,
    HoldingHorizon,
    StrategyFamily,
    build_initial_candidate_universe,
)
from src.research.metrics_validation import MetricsValidationResult
from src.research.report_schema import ResearchReport
from src.research.report_snapshot_runner import (
    ReportSnapshotRunResult,
    run_offline_report_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SNAPSHOT_KWARGS = dict(
    start_date="2020-01-01",
    end_date="2021-12-31",
    train_months=12,
    test_months=3,
    step_months=3,
)

_FIXED_TS = "2026-06-04T00:00:00+00:00"


def _make_spec(symbol: str = "SPY", group: str = "A", interval: str = "60m") -> CandidateSpec:
    return CandidateSpec(
        candidate_id=f"{group}_{symbol}_{interval}_trend_breakout_one_to_two_days",
        group=group,
        symbol=symbol,
        interval=interval,
        strategy_family=StrategyFamily.TREND_BREAKOUT,
        holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
    )


def _none_metrics() -> dict:
    return {k: None for k in REQUIRED_METRIC_KEYS}


def _good_metrics() -> dict:
    m = _none_metrics()
    m.update({
        "cagr": 0.12,
        "average_monthly_return": 0.0095,
        "max_drawdown": -0.15,
        "total_trades": 80,
        "win_rate": 0.55,
    })
    return m


def _make_eval_result(
    result: str = "PASS",
    metrics: dict | None = None,
    blocker: str | None = None,
    candidate_id: str = "A_SPY_60m_trend_breakout_one_to_two_days",
) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate_id,
        symbol="SPY",
        interval="60m",
        strategy_family="trend_breakout",
        result=result,
        blocker=blocker,
        metrics=metrics if metrics is not None else _good_metrics(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_val_result(result: str = "PASS") -> MetricsValidationResult:
    return MetricsValidationResult(
        result=result,
        blocker=None if result == "PASS" else "fake",
        missing_keys=(),
        invalid_keys=(),
        warnings=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_pass_wf(
    candidate_id: str = "A_SPY_60m_trend_breakout_one_to_two_days",
    broker_calls_made: bool = False,
) -> CachedWalkForwardRunResult:
    sr = _make_eval_result(candidate_id=candidate_id)
    vr = _make_val_result("PASS")
    return CachedWalkForwardRunResult(
        result="PASS",
        blocker=None,
        candidate_id=candidate_id,
        splits_requested=1,
        splits_evaluated=1,
        split_results=(sr,),
        validation_results=(vr,),
        broker_calls_made=broker_calls_made,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_blocked_wf(
    candidate_id: str = "A_SPY_60m_trend_breakout_one_to_two_days",
    blocker: str = "no cache file found",
) -> CachedWalkForwardRunResult:
    sr = _make_eval_result(result="BLOCKED", metrics=_none_metrics(), blocker=blocker,
                           candidate_id=candidate_id)
    vr = _make_val_result("BLOCKED")
    return CachedWalkForwardRunResult(
        result="BLOCKED",
        blocker=blocker,
        candidate_id=candidate_id,
        splits_requested=1,
        splits_evaluated=0,
        split_results=(sr,),
        validation_results=(vr,),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_error_wf(
    candidate_id: str = "A_SPY_60m_trend_breakout_one_to_two_days",
) -> CachedWalkForwardRunResult:
    return CachedWalkForwardRunResult(
        result="ERROR",
        blocker="fake error",
        candidate_id=candidate_id,
        splits_requested=1,
        splits_evaluated=0,
        split_results=(),
        validation_results=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _fake_runner(result: str = "PASS", broker_calls_made: bool = False):
    """Return a runner callable that returns a fixed result for all candidates."""
    def runner(candidate: CandidateSpec, **kwargs) -> CachedWalkForwardRunResult:
        cid = candidate.candidate_id
        if result == "PASS":
            return _make_pass_wf(cid, broker_calls_made=broker_calls_made)
        elif result == "BLOCKED":
            return _make_blocked_wf(cid)
        else:
            return _make_error_wf(cid)
    return runner


def _run_single(
    spec: CandidateSpec | None = None,
    result: str = "PASS",
    include_json: bool = False,
    generated_at_utc: str | None = _FIXED_TS,
    **extra,
) -> ReportSnapshotRunResult:
    """Helper: run snapshot for one candidate with a fake runner."""
    spec = spec or _make_spec()
    return run_offline_report_snapshot(
        [spec],
        **_SNAPSHOT_KWARGS,
        include_json=include_json,
        generated_at_utc=generated_at_utc,
        _walk_forward_runner=_fake_runner(result),
        **extra,
    )


# ---------------------------------------------------------------------------
# TestReportSnapshotRunResultDataclass
# ---------------------------------------------------------------------------

class TestReportSnapshotRunResultDataclass:
    def test_frozen(self):
        r = _run_single()
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_reports_is_tuple(self):
        r = _run_single()
        assert isinstance(r.reports, tuple)

    def test_json_reports_is_tuple(self):
        r = _run_single(include_json=True)
        assert isinstance(r.json_reports, tuple)

    def test_safety_fields_present(self):
        r = _run_single()
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")


# ---------------------------------------------------------------------------
# TestRunOfflineReportSnapshot
# ---------------------------------------------------------------------------

class TestRunOfflineReportSnapshot:
    def test_returns_correct_type(self):
        r = _run_single()
        assert isinstance(r, ReportSnapshotRunResult)

    def test_candidates_requested_count(self):
        specs = [_make_spec("SPY"), _make_spec("QQQ"), _make_spec("IWM")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert r.candidates_requested == 3

    def test_default_candidates_use_universe(self):
        universe = build_initial_candidate_universe()
        r = run_offline_report_snapshot(
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("BLOCKED"),
        )
        assert r.candidates_requested == len(universe)

    def test_max_candidates_limits(self):
        r = run_offline_report_snapshot(
            **_SNAPSHOT_KWARGS,
            max_candidates=2,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert r.candidates_requested == 2
        assert r.reports_created == 2

    def test_runner_called_once_per_candidate(self):
        calls: list[str] = []

        def counting_runner(candidate: CandidateSpec, **kwargs):
            calls.append(candidate.candidate_id)
            return _make_pass_wf(candidate.candidate_id)

        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=counting_runner,
        )
        assert calls == [specs[0].candidate_id, specs[1].candidate_id]

    def test_reports_count_matches_candidates(self):
        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert len(r.reports) == 2
        assert r.reports_created == 2

    def test_reports_preserve_candidate_order(self):
        specs = [_make_spec("SPY"), _make_spec("QQQ"), _make_spec("IWM")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert r.reports[0].candidate.symbol == "SPY"
        assert r.reports[1].candidate.symbol == "QQQ"
        assert r.reports[2].candidate.symbol == "IWM"

    def test_include_json_false_empty_json_reports(self):
        r = _run_single(include_json=False)
        assert r.json_reports == ()

    def test_include_json_true_yields_strings(self):
        r = _run_single(include_json=True)
        assert len(r.json_reports) == 1
        assert isinstance(r.json_reports[0], str)

    def test_include_json_true_count_matches_reports(self):
        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            include_json=True,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert len(r.json_reports) == len(r.reports)

    def test_include_json_true_is_valid_json(self):
        r = _run_single(include_json=True)
        parsed = json.loads(r.json_reports[0])
        assert isinstance(parsed, dict)

    def test_generated_at_utc_propagated_to_reports(self):
        r = run_offline_report_snapshot(
            [_make_spec()],
            **_SNAPSHOT_KWARGS,
            generated_at_utc="2025-01-01T12:00:00+00:00",
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert r.reports[0].generated_at_utc == "2025-01-01T12:00:00+00:00"

    def test_generated_at_utc_same_for_all_reports(self):
        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        assert all(rep.generated_at_utc == _FIXED_TS for rep in r.reports)

    def test_pass_if_at_least_one_pass_no_error(self):
        r = _run_single(result="PASS")
        assert r.result == "PASS"
        assert r.blocker is None

    def test_pass_if_mix_of_pass_and_blocked(self):
        results_iter = iter(["PASS", "BLOCKED"])

        def mixed_runner(candidate, **kwargs):
            res = next(results_iter, "BLOCKED")
            if res == "PASS":
                return _make_pass_wf(candidate.candidate_id)
            return _make_blocked_wf(candidate.candidate_id)

        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=mixed_runner,
        )
        assert r.result == "PASS"

    def test_blocked_if_all_blocked(self):
        r = _run_single(result="BLOCKED")
        assert r.result == "BLOCKED"

    def test_error_if_any_error(self):
        r = _run_single(result="ERROR")
        assert r.result == "ERROR"
        assert "ERROR" in (r.blocker or "")

    def test_error_overrides_pass(self):
        results_iter = iter(["PASS", "ERROR"])

        def mixed_runner(candidate, **kwargs):
            res = next(results_iter, "PASS")
            if res == "PASS":
                return _make_pass_wf(candidate.candidate_id)
            return _make_error_wf(candidate.candidate_id)

        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=mixed_runner,
        )
        assert r.result == "ERROR"

    def test_empty_candidates_tuple_gives_blocked(self):
        r = run_offline_report_snapshot(
            [],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
        )
        assert r.result == "BLOCKED"
        assert r.candidates_requested == 0
        assert r.reports == ()

    def test_max_candidates_zero_gives_blocked(self):
        r = run_offline_report_snapshot(
            **_SNAPSHOT_KWARGS,
            max_candidates=0,
            generated_at_utc=_FIXED_TS,
        )
        assert r.result == "BLOCKED"
        assert r.candidates_requested == 0

    def test_runner_exception_gives_error_result(self):
        def raising_runner(candidate, **kwargs):
            raise RuntimeError("fake runner failure")

        r = run_offline_report_snapshot(
            [_make_spec()],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=raising_runner,
        )
        assert r.result == "ERROR"

    def test_runner_exception_still_creates_report(self):
        def raising_runner(candidate, **kwargs):
            raise ValueError("oops")

        r = run_offline_report_snapshot(
            [_make_spec()],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=raising_runner,
        )
        assert r.reports_created == 1
        assert len(r.reports) == 1
        assert r.reports[0].summary.result == "ERROR"

    def test_runner_exception_partial_run_continues(self):
        """One failing candidate does not stop evaluation of subsequent candidates."""
        call_count = [0]

        def partial_runner(candidate, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first candidate fails")
            return _make_pass_wf(candidate.candidate_id)

        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=partial_runner,
        )
        assert r.reports_created == 2
        assert r.reports[0].summary.result == "ERROR"
        assert r.reports[1].summary.result == "PASS"
        assert r.result == "ERROR"  # ERROR because ≥1 ERROR, even though one PASS

    def test_deterministic_repeated_run(self):
        kwargs = dict(
            **_SNAPSHOT_KWARGS,
            include_json=True,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        r1 = run_offline_report_snapshot([_make_spec()], **kwargs)
        r2 = run_offline_report_snapshot([_make_spec()], **kwargs)
        assert r1.result == r2.result
        assert r1.json_reports == r2.json_reports

    def test_schema_version_in_all_reports(self):
        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        for report in r.reports:
            assert report.schema_version == "S6/1.0"

    def test_candidate_metadata_in_reports(self):
        spec = _make_spec("IWM", group="B", interval="1d")
        r = run_offline_report_snapshot(
            [spec],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS"),
        )
        c = r.reports[0].candidate
        assert c.symbol == "IWM"
        assert c.group == "B"
        assert c.interval == "1d"


# ---------------------------------------------------------------------------
# TestSafetyFlagAggregation
# ---------------------------------------------------------------------------

class TestSafetyFlagAggregation:
    def test_all_false_by_default(self):
        r = _run_single()
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_true_broker_calls_propagates_to_top_level(self):
        r = run_offline_report_snapshot(
            [_make_spec()],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS", broker_calls_made=True),
        )
        assert r.broker_calls_made is True

    def test_true_flag_note_in_individual_report(self):
        r = run_offline_report_snapshot(
            [_make_spec()],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS", broker_calls_made=True),
        )
        notes_text = " ".join(r.reports[0].notes)
        assert "broker_calls_made" in notes_text

    def test_true_flag_does_not_change_result(self):
        r = run_offline_report_snapshot(
            [_make_spec()],
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=_fake_runner("PASS", broker_calls_made=True),
        )
        assert r.result == "PASS"

    def test_no_false_safety_note_when_all_clean(self):
        r = _run_single()
        assert not any("non-false safety flag" in n for n in r.reports[0].notes)

    def test_safety_or_across_multiple_candidates(self):
        call_count = [0]

        def mixed_runner(candidate, **kwargs):
            call_count[0] += 1
            flag = call_count[0] == 2  # second candidate has broker_calls_made=True
            return _make_pass_wf(candidate.candidate_id, broker_calls_made=flag)

        specs = [_make_spec("SPY"), _make_spec("QQQ")]
        r = run_offline_report_snapshot(
            specs,
            **_SNAPSHOT_KWARGS,
            generated_at_utc=_FIXED_TS,
            _walk_forward_runner=mixed_runner,
        )
        assert r.broker_calls_made is True

    def test_empty_candidates_safety_all_false(self):
        r = run_offline_report_snapshot([], **_SNAPSHOT_KWARGS, generated_at_utc=_FIXED_TS)
        assert r.broker_calls_made is False
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.report_snapshot_runner as _m
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

    def test_no_optimize(self):
        assert "optimize" not in self._src()

    def test_no_open_file(self):
        assert "open(" not in self._src()

    def test_no_write_file(self):
        assert ".write(" not in self._src()
