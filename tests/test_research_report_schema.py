"""
tests/test_research_report_schema.py
--------------------------------------
Tests for src/research/report_schema.py (PR S6).

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
)
from src.research.metrics_validation import MetricsValidationResult
from src.research.report_schema import (
    ResearchReport,
    ResearchReportCandidate,
    ResearchReportSplit,
    ResearchReportSummary,
    build_research_report,
    research_report_to_dict,
    research_report_to_json,
)
from src.research.walk_forward import WalkForwardSplit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_FIXED_TS = "2026-06-04T00:00:00+00:00"


def _make_spec(
    symbol: str = "SPY",
    interval: str = "60m",
    group: str = "A",
) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=f"{group}_{symbol}_{interval}_trend_breakout_one_to_two_days",
        group=group,
        symbol=symbol,
        interval=interval,
        strategy_family=StrategyFamily.TREND_BREAKOUT,
        holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
    )


def _good_metrics(
    average_monthly_return: float = 0.0095,
    max_drawdown: float = -0.15,
    total_trades: int = 80,
) -> dict:
    return {
        "cagr": 0.12,
        "average_monthly_return": average_monthly_return,
        "worst_month": None,
        "sharpe": 1.2,
        "sortino": None,
        "max_drawdown": max_drawdown,
        "calmar": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.3,
        "average_trade_return": 0.005,
        "median_trade_return": None,
        "exposure_pct": 0.35,
        "trades_per_month": 4.5,
        "total_trades": total_trades,
        "average_holding_bars": 6.0,
        "stop_loss_frequency": 0.2,
        "session_end_frequency": 0.1,
        "slippage_bps": None,
    }


def _none_metrics() -> dict:
    return {k: None for k in REQUIRED_METRIC_KEYS}


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


def _make_val_result(
    result: str = "PASS",
    blocker: str | None = None,
) -> MetricsValidationResult:
    return MetricsValidationResult(
        result=result,
        blocker=blocker,
        missing_keys=(),
        invalid_keys=(),
        warnings=(),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _make_wf_result(
    split_results: tuple[CandidateEvaluationResult, ...] = (),
    validation_results: tuple[MetricsValidationResult, ...] = (),
    result: str = "PASS",
    blocker: str | None = None,
    splits_requested: int | None = None,
    splits_evaluated: int | None = None,
    candidate_id: str = "A_SPY_60m_trend_breakout_one_to_two_days",
    broker_calls_made: bool = False,
    credentials_read: bool = False,
    network_calls_made: bool = False,
    order_action_requested: bool = False,
    live_trading_allowed: bool = False,
) -> CachedWalkForwardRunResult:
    n = len(split_results)
    return CachedWalkForwardRunResult(
        result=result,
        blocker=blocker,
        candidate_id=candidate_id,
        splits_requested=splits_requested if splits_requested is not None else n,
        splits_evaluated=splits_evaluated if splits_evaluated is not None else n,
        split_results=split_results,
        validation_results=validation_results,
        broker_calls_made=broker_calls_made,
        credentials_read=credentials_read,
        network_calls_made=network_calls_made,
        order_action_requested=order_action_requested,
        live_trading_allowed=live_trading_allowed,
    )


def _make_3split_wf_result(
    metrics_list: list[dict] | None = None,
    split_results_override: tuple[CandidateEvaluationResult, ...] | None = None,
    val_results_override: tuple[MetricsValidationResult, ...] | None = None,
    result: str = "PASS",
) -> CachedWalkForwardRunResult:
    """Helper: 3-split walk-forward result, all PASS by default."""
    if metrics_list is None:
        metrics_list = [_good_metrics() for _ in range(3)]
    split_results = split_results_override or tuple(
        _make_eval_result(metrics=m) for m in metrics_list
    )
    val_results = val_results_override or tuple(_make_val_result() for _ in range(3))
    return _make_wf_result(
        split_results=split_results,
        validation_results=val_results,
        result=result,
    )


def _make_wf_split(n: int = 1) -> WalkForwardSplit:
    return WalkForwardSplit(
        split_id=f"WF{n:03d}_2020-01-01_2020-12-31_2021-01-01_2021-03-31",
        train_start="2020-01-01",
        train_end="2020-12-31",
        test_start="2021-01-01",
        test_end="2021-03-31",
    )


def _make_report(**kwargs) -> ResearchReport:
    spec = kwargs.pop("candidate", _make_spec())
    wf = kwargs.pop("walk_forward_result", _make_3split_wf_result())
    return build_research_report(
        spec,
        wf,
        generated_at_utc=_FIXED_TS,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TestResearchReportDataclasses
# ---------------------------------------------------------------------------

class TestResearchReportDataclasses:
    def test_candidate_frozen(self):
        c = ResearchReportCandidate(
            candidate_id="x", group="A", symbol="SPY",
            interval="60m", strategy_family="trend_breakout",
            holding_horizon="one_to_two_days",
        )
        with pytest.raises(Exception):
            c.symbol = "QQQ"  # type: ignore[misc]

    def test_split_frozen(self):
        s = ResearchReportSplit(
            split_id="split_001", train_start=None, train_end=None,
            test_start=None, test_end=None, result="PASS", blocker=None,
            metrics={}, metrics_validation_result="PASS",
            metrics_validation_blocker=None,
        )
        with pytest.raises(Exception):
            s.result = "BLOCKED"  # type: ignore[misc]

    def test_summary_frozen(self):
        s = ResearchReportSummary(
            result="PASS", blocker=None, candidate_id="x",
            splits_requested=1, splits_evaluated=1,
            splits_passed=1, splits_blocked=0, splits_error=0,
            validations_passed=1, validations_blocked=0,
            average_monthly_return_mean=None, average_monthly_return_min=None,
            max_drawdown_worst=None, total_trades_sum=None,
        )
        with pytest.raises(Exception):
            s.result = "BLOCKED"  # type: ignore[misc]

    def test_report_frozen(self):
        r = _make_report()
        with pytest.raises(Exception):
            r.schema_version = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestBuildResearchReport
# ---------------------------------------------------------------------------

class TestBuildResearchReport:
    def test_returns_research_report(self):
        r = _make_report()
        assert isinstance(r, ResearchReport)

    def test_schema_version(self):
        r = _make_report()
        assert r.schema_version == "S6/1.0"

    def test_candidate_id_preserved(self):
        spec = _make_spec(symbol="QQQ")
        r = build_research_report(spec, _make_3split_wf_result(), generated_at_utc=_FIXED_TS)
        assert r.candidate.candidate_id == spec.candidate_id

    def test_candidate_group_preserved(self):
        spec = _make_spec(group="B")
        r = build_research_report(spec, _make_3split_wf_result(), generated_at_utc=_FIXED_TS)
        assert r.candidate.group == "B"

    def test_candidate_symbol_preserved(self):
        spec = _make_spec(symbol="IWM")
        r = build_research_report(spec, _make_3split_wf_result(), generated_at_utc=_FIXED_TS)
        assert r.candidate.symbol == "IWM"

    def test_candidate_interval_preserved(self):
        spec = _make_spec(interval="1d")
        r = build_research_report(spec, _make_3split_wf_result(), generated_at_utc=_FIXED_TS)
        assert r.candidate.interval == "1d"

    def test_candidate_strategy_family_preserved(self):
        spec = _make_spec()
        r = build_research_report(spec, _make_3split_wf_result(), generated_at_utc=_FIXED_TS)
        assert r.candidate.strategy_family == "trend_breakout"

    def test_candidate_holding_horizon_preserved(self):
        spec = _make_spec()
        r = build_research_report(spec, _make_3split_wf_result(), generated_at_utc=_FIXED_TS)
        assert r.candidate.holding_horizon == "one_to_two_days"

    def test_generated_at_utc_injectable(self):
        r = build_research_report(_make_spec(), _make_3split_wf_result(),
                                   generated_at_utc="2025-01-01T00:00:00+00:00")
        assert r.generated_at_utc == "2025-01-01T00:00:00+00:00"

    def test_generated_at_utc_default_is_string(self):
        r = build_research_report(_make_spec(), _make_3split_wf_result())
        assert isinstance(r.generated_at_utc, str)
        assert len(r.generated_at_utc) > 0

    def test_notes_preserved(self):
        r = _make_report(notes=("custom note A", "custom note B"))
        assert "custom note A" in r.notes
        assert "custom note B" in r.notes

    def test_split_count_matches(self):
        r = _make_report()
        assert len(r.splits) == 3

    def test_split_result_preserved(self):
        sr = _make_eval_result(result="BLOCKED", blocker="fake")
        wf = _make_wf_result(
            split_results=(sr,),
            validation_results=(_make_val_result("BLOCKED"),),
            result="BLOCKED",
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.splits[0].result == "BLOCKED"

    def test_split_blocker_preserved(self):
        sr = _make_eval_result(result="BLOCKED", blocker="no cache")
        wf = _make_wf_result(
            split_results=(sr,),
            validation_results=(_make_val_result("BLOCKED"),),
            result="BLOCKED",
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.splits[0].blocker == "no cache"

    def test_split_metrics_preserved(self):
        m = _good_metrics(average_monthly_return=0.042)
        sr = _make_eval_result(metrics=m)
        wf = _make_wf_result(split_results=(sr,), validation_results=(_make_val_result(),))
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.splits[0].metrics["average_monthly_return"] == pytest.approx(0.042)

    def test_split_validation_result_preserved(self):
        sr = _make_eval_result()
        vr = _make_val_result(result="BLOCKED", blocker="invalid max_drawdown")
        wf = _make_wf_result(split_results=(sr,), validation_results=(vr,), result="BLOCKED")
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.splits[0].metrics_validation_result == "BLOCKED"

    def test_split_validation_blocker_preserved(self):
        sr = _make_eval_result()
        vr = _make_val_result(result="BLOCKED", blocker="invalid max_drawdown")
        wf = _make_wf_result(split_results=(sr,), validation_results=(vr,), result="BLOCKED")
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.splits[0].metrics_validation_blocker == "invalid max_drawdown"

    def test_splits_with_date_metadata(self):
        ws = _make_wf_split(1)
        sr = _make_eval_result()
        wf = _make_wf_result(split_results=(sr,), validation_results=(_make_val_result(),))
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS, splits=(ws,))
        assert r.splits[0].train_start == "2020-01-01"
        assert r.splits[0].train_end == "2020-12-31"
        assert r.splits[0].test_start == "2021-01-01"
        assert r.splits[0].test_end == "2021-03-31"
        assert r.splits[0].split_id == ws.split_id

    def test_splits_without_dates_are_none(self):
        r = _make_report()
        for s in r.splits:
            assert s.train_start is None
            assert s.train_end is None
            assert s.test_start is None
            assert s.test_end is None

    def test_split_id_index_based_when_no_splits_param(self):
        r = _make_report()
        assert r.splits[0].split_id == "split_001"
        assert r.splits[1].split_id == "split_002"
        assert r.splits[2].split_id == "split_003"

    def test_empty_split_results(self):
        wf = _make_wf_result(split_results=(), validation_results=(), result="BLOCKED",
                              splits_requested=0, splits_evaluated=0)
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.splits == ()


# ---------------------------------------------------------------------------
# TestSummaryAggregation
# ---------------------------------------------------------------------------

class TestSummaryAggregation:
    def _make_mixed_wf(self) -> CachedWalkForwardRunResult:
        """3 splits: PASS+PASS, PASS+BLOCKED, BLOCKED+PASS."""
        m1 = _good_metrics(average_monthly_return=0.01, max_drawdown=-0.10, total_trades=50)
        m2 = _good_metrics(average_monthly_return=0.02, max_drawdown=-0.20, total_trades=60)
        m3 = _good_metrics(average_monthly_return=0.03, max_drawdown=-0.05, total_trades=70)
        sr1 = _make_eval_result(result="PASS", metrics=m1)
        sr2 = _make_eval_result(result="PASS", metrics=m2)
        sr3 = _make_eval_result(result="BLOCKED", metrics=m3, blocker="no cache")
        vr1 = _make_val_result(result="PASS")
        vr2 = _make_val_result(result="BLOCKED", blocker="invalid")
        vr3 = _make_val_result(result="PASS")
        return _make_wf_result(
            split_results=(sr1, sr2, sr3),
            validation_results=(vr1, vr2, vr3),
            result="BLOCKED",
        )

    def test_summary_result(self):
        wf = _make_3split_wf_result()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.result == "PASS"

    def test_splits_requested(self):
        wf = _make_3split_wf_result()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.splits_requested == 3

    def test_splits_evaluated(self):
        wf = _make_3split_wf_result()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.splits_evaluated == 3

    def test_splits_passed_all_pass(self):
        wf = _make_3split_wf_result()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.splits_passed == 3

    def test_splits_blocked_count(self):
        wf = self._make_mixed_wf()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.splits_blocked == 1

    def test_splits_error_count(self):
        sr1 = _make_eval_result(result="ERROR", metrics=_none_metrics(), blocker="err")
        sr2 = _make_eval_result(result="PASS")
        wf = _make_wf_result(
            split_results=(sr1, sr2),
            validation_results=(_make_val_result("PASS"), _make_val_result("PASS")),
            result="ERROR",
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.splits_error == 1
        assert r.summary.splits_passed == 1

    def test_validations_passed_count(self):
        wf = _make_3split_wf_result()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.validations_passed == 3

    def test_validations_blocked_count(self):
        wf = self._make_mixed_wf()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.validations_blocked == 1

    def test_avg_monthly_return_mean_from_pass_valid_only(self):
        # Only sr1 (PASS+PASS) contributes; sr2 has val BLOCKED; sr3 is BLOCKED split
        wf = self._make_mixed_wf()
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_mean == pytest.approx(0.01)

    def test_avg_monthly_return_mean_multiple_valid(self):
        m1 = _good_metrics(average_monthly_return=0.01)
        m2 = _good_metrics(average_monthly_return=0.03)
        sr1 = _make_eval_result(metrics=m1)
        sr2 = _make_eval_result(metrics=m2)
        wf = _make_wf_result(
            split_results=(sr1, sr2),
            validation_results=(_make_val_result(), _make_val_result()),
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_mean == pytest.approx(0.02)

    def test_avg_monthly_return_min(self):
        m1 = _good_metrics(average_monthly_return=0.01)
        m2 = _good_metrics(average_monthly_return=0.03)
        sr1 = _make_eval_result(metrics=m1)
        sr2 = _make_eval_result(metrics=m2)
        wf = _make_wf_result(
            split_results=(sr1, sr2),
            validation_results=(_make_val_result(), _make_val_result()),
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_min == pytest.approx(0.01)

    def test_max_drawdown_worst_is_most_negative(self):
        m1 = _good_metrics(max_drawdown=-0.10)
        m2 = _good_metrics(max_drawdown=-0.25)
        m3 = _good_metrics(max_drawdown=-0.05)
        split_results = tuple(_make_eval_result(metrics=m) for m in [m1, m2, m3])
        val_results = tuple(_make_val_result() for _ in range(3))
        wf = _make_wf_result(split_results=split_results, validation_results=val_results)
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.max_drawdown_worst == pytest.approx(-0.25)

    def test_total_trades_sum(self):
        m1 = _good_metrics(total_trades=30)
        m2 = _good_metrics(total_trades=40)
        m3 = _good_metrics(total_trades=50)
        split_results = tuple(_make_eval_result(metrics=m) for m in [m1, m2, m3])
        val_results = tuple(_make_val_result() for _ in range(3))
        wf = _make_wf_result(split_results=split_results, validation_results=val_results)
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.total_trades_sum == 120

    def test_total_trades_sum_is_int(self):
        r = _make_report()
        if r.summary.total_trades_sum is not None:
            assert isinstance(r.summary.total_trades_sum, int)

    def test_no_valid_splits_all_metrics_none(self):
        sr = _make_eval_result(result="BLOCKED", metrics=_none_metrics(), blocker="b")
        vr = _make_val_result(result="BLOCKED")
        wf = _make_wf_result(split_results=(sr,), validation_results=(vr,), result="BLOCKED")
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_mean is None
        assert r.summary.average_monthly_return_min is None
        assert r.summary.max_drawdown_worst is None
        assert r.summary.total_trades_sum is None

    def test_none_metric_values_excluded_from_aggregation(self):
        # If average_monthly_return is None for all valid splits → mean is None.
        m = _good_metrics()
        m["average_monthly_return"] = None
        sr = _make_eval_result(metrics=m)
        wf = _make_wf_result(split_results=(sr,), validation_results=(_make_val_result(),))
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_mean is None

    def test_blocked_splits_excluded_from_aggregation(self):
        # A BLOCKED split with numeric metrics should NOT contribute.
        m = _good_metrics(average_monthly_return=0.99, total_trades=999)
        sr = _make_eval_result(result="BLOCKED", metrics=m, blocker="b")
        vr = _make_val_result(result="PASS")
        wf = _make_wf_result(split_results=(sr,), validation_results=(vr,), result="BLOCKED")
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_mean is None
        assert r.summary.total_trades_sum is None

    def test_val_blocked_splits_excluded_from_aggregation(self):
        # A PASS split with BLOCKED validation should NOT contribute.
        m = _good_metrics(average_monthly_return=0.99)
        sr = _make_eval_result(result="PASS", metrics=m)
        vr = _make_val_result(result="BLOCKED", blocker="invalid")
        wf = _make_wf_result(split_results=(sr,), validation_results=(vr,), result="BLOCKED")
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.average_monthly_return_mean is None


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_safety_flags_copied(self):
        r = _make_report()
        assert "broker_calls_made" in r.safety
        assert "credentials_read" in r.safety
        assert "network_calls_made" in r.safety
        assert "order_action_requested" in r.safety
        assert "live_trading_allowed" in r.safety

    def test_safety_all_false_by_default(self):
        r = _make_report()
        for flag, val in r.safety.items():
            assert val is False, f"{flag} should be False"

    def test_true_broker_calls_adds_note(self):
        wf = _make_wf_result(
            split_results=(_make_eval_result(),),
            validation_results=(_make_val_result(),),
            broker_calls_made=True,
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert any("broker_calls_made" in n for n in r.notes)

    def test_true_safety_flag_note_contains_flag_name(self):
        wf = _make_wf_result(
            split_results=(_make_eval_result(),),
            validation_results=(_make_val_result(),),
            credentials_read=True,
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        note_text = " ".join(r.notes)
        assert "credentials_read" in note_text

    def test_true_safety_flag_does_not_change_result(self):
        wf = _make_wf_result(
            split_results=(_make_eval_result(),),
            validation_results=(_make_val_result(),),
            broker_calls_made=True,
            result="PASS",
        )
        r = build_research_report(_make_spec(), wf, generated_at_utc=_FIXED_TS)
        assert r.summary.result == "PASS"

    def test_false_safety_flag_adds_no_note(self):
        r = _make_report()
        assert not any("non-false safety flag" in n for n in r.notes)


# ---------------------------------------------------------------------------
# TestResearchReportToDict
# ---------------------------------------------------------------------------

class TestResearchReportToDict:
    def test_returns_dict(self):
        d = research_report_to_dict(_make_report())
        assert isinstance(d, dict)

    def test_has_schema_version(self):
        d = research_report_to_dict(_make_report())
        assert d["schema_version"] == "S6/1.0"

    def test_has_generated_at_utc(self):
        d = research_report_to_dict(_make_report())
        assert d["generated_at_utc"] == _FIXED_TS

    def test_has_candidate_dict(self):
        d = research_report_to_dict(_make_report())
        assert isinstance(d["candidate"], dict)
        assert "symbol" in d["candidate"]

    def test_has_summary_dict(self):
        d = research_report_to_dict(_make_report())
        assert isinstance(d["summary"], dict)
        assert "result" in d["summary"]
        assert "splits_passed" in d["summary"]

    def test_has_splits_list(self):
        d = research_report_to_dict(_make_report())
        assert isinstance(d["splits"], list)
        assert len(d["splits"]) == 3

    def test_split_dict_has_metrics(self):
        d = research_report_to_dict(_make_report())
        assert isinstance(d["splits"][0]["metrics"], dict)

    def test_has_safety_dict(self):
        d = research_report_to_dict(_make_report())
        assert isinstance(d["safety"], dict)
        assert "broker_calls_made" in d["safety"]

    def test_has_notes_list(self):
        d = research_report_to_dict(_make_report(notes=("note A",)))
        assert isinstance(d["notes"], list)
        assert "note A" in d["notes"]

    def test_json_serializable(self):
        d = research_report_to_dict(_make_report())
        serialized = json.dumps(d)
        assert isinstance(serialized, str)

    def test_summary_total_trades_sum_present(self):
        d = research_report_to_dict(_make_report())
        assert "total_trades_sum" in d["summary"]


# ---------------------------------------------------------------------------
# TestResearchReportToJson
# ---------------------------------------------------------------------------

class TestResearchReportToJson:
    def test_returns_string(self):
        s = research_report_to_json(_make_report())
        assert isinstance(s, str)

    def test_is_valid_json(self):
        s = research_report_to_json(_make_report())
        parsed = json.loads(s)
        assert isinstance(parsed, dict)

    def test_deterministic_same_inputs(self):
        r = _make_report()
        j1 = research_report_to_json(r)
        j2 = research_report_to_json(r)
        assert j1 == j2

    def test_sort_keys_applied(self):
        s = research_report_to_json(_make_report())
        parsed = json.loads(s)
        top_keys = list(parsed.keys())
        assert top_keys == sorted(top_keys)

    def test_schema_version_in_json(self):
        s = research_report_to_json(_make_report())
        assert "S6/1.0" in s

    def test_candidate_in_json(self):
        s = research_report_to_json(_make_report())
        assert '"symbol"' in s

    def test_no_file_written(self, tmp_path):
        research_report_to_json(_make_report())
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.report_schema as _m
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
