"""
tests/test_candidate_evaluator.py
-----------------------------------
Tests for src/research/candidate_evaluator.py (PR S2).

No broker/API/credential/env access.
No live gates unfrozen. No live trading. No order action. No real data.
"""

from __future__ import annotations

import inspect

import pytest

from src.research.candidate_evaluator import (
    REQUIRED_METRIC_KEYS,
    CandidateEvaluationResult,
    CandidateGateResult,
    apply_candidate_acceptance_gates,
    evaluate_candidate,
    evaluate_candidates,
)
from src.research.candidate_universe import (
    CandidateSpec,
    HoldingHorizon,
    StrategyFamily,
    build_initial_candidate_universe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    candidate_id="test_SPY_60m_trend_1to2d",
    group="A",
    symbol="SPY",
    interval="60m",
    family=StrategyFamily.TREND_BREAKOUT,
    horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
    enabled=True,
) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=candidate_id,
        group=group,
        symbol=symbol,
        interval=interval,
        strategy_family=family,
        holding_horizon=horizon,
        enabled=enabled,
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
# TestCandidateEvaluationResultDataclass
# ---------------------------------------------------------------------------

class TestCandidateEvaluationResultDataclass:
    def test_frozen(self):
        r = evaluate_candidate(_make_spec())
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_fields_exist(self):
        r = evaluate_candidate(_make_spec())
        assert hasattr(r, "candidate_id")
        assert hasattr(r, "symbol")
        assert hasattr(r, "interval")
        assert hasattr(r, "strategy_family")
        assert hasattr(r, "result")
        assert hasattr(r, "blocker")
        assert hasattr(r, "metrics")
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")

    def test_result_is_string(self):
        r = evaluate_candidate(_make_spec())
        assert isinstance(r.result, str)

    def test_metrics_is_dict(self):
        r = evaluate_candidate(_make_spec())
        assert isinstance(r.metrics, dict)


# ---------------------------------------------------------------------------
# TestCandidateGateResultDataclass
# ---------------------------------------------------------------------------

class TestCandidateGateResultDataclass:
    def test_frozen(self):
        r = CandidateGateResult(result="PASS", blocker=None, passed=True)
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_fields(self):
        r = CandidateGateResult(result="BLOCKED", blocker="reason", passed=False)
        assert r.result == "BLOCKED"
        assert r.blocker == "reason"
        assert r.passed is False


# ---------------------------------------------------------------------------
# TestEvaluateCandidateDefault
# ---------------------------------------------------------------------------

class TestEvaluateCandidateDefault:
    def test_blocked_when_no_providers(self):
        r = evaluate_candidate(_make_spec())
        assert r.result == "BLOCKED"

    def test_blocker_mentions_provider(self):
        r = evaluate_candidate(_make_spec())
        assert "not wired" in r.blocker

    def test_metrics_all_none_when_blocked(self):
        r = evaluate_candidate(_make_spec())
        for k in REQUIRED_METRIC_KEYS:
            assert r.metrics[k] is None

    def test_blocked_when_only_data_provider_given(self):
        r = evaluate_candidate(_make_spec(), data_provider=_fake_data_provider)
        assert r.result == "BLOCKED"

    def test_blocked_when_only_backtest_runner_given(self):
        r = evaluate_candidate(_make_spec(), backtest_runner=_fake_backtest_runner)
        assert r.result == "BLOCKED"

    def test_blocked_when_candidate_disabled(self):
        spec = _make_spec(enabled=False)
        r = evaluate_candidate(
            spec,
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.result == "BLOCKED"
        assert "disabled" in r.blocker


# ---------------------------------------------------------------------------
# TestEvaluateCandidateWithFake
# ---------------------------------------------------------------------------

class TestEvaluateCandidateWithFake:
    def test_pass_with_both_providers(self):
        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.result == "PASS"

    def test_no_blocker_on_pass(self):
        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.blocker is None

    def test_metrics_populated_from_fake_runner(self):
        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.metrics["total_trades"] == 80
        assert r.metrics["average_monthly_return"] == 0.01
        assert r.metrics["sharpe"] == 1.2

    def test_candidate_id_preserved(self):
        spec = _make_spec(candidate_id="MY_TEST_ID")
        r = evaluate_candidate(
            spec,
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.candidate_id == "MY_TEST_ID"

    def test_symbol_preserved(self):
        r = evaluate_candidate(
            _make_spec(symbol="QQQ"),
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.symbol == "QQQ"

    def test_error_on_provider_exception(self):
        def exploding_provider(sym, iv):
            raise RuntimeError("data fetch failed")

        r = evaluate_candidate(
            _make_spec(),
            data_provider=exploding_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.result == "ERROR"
        assert "data fetch failed" in r.blocker

    def test_error_on_runner_exception(self):
        def exploding_runner(data, candidate):
            raise ValueError("runner failed")

        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=exploding_runner,
        )
        assert r.result == "ERROR"


# ---------------------------------------------------------------------------
# TestEvaluateCandidates
# ---------------------------------------------------------------------------

class TestEvaluateCandidates:
    def test_returns_tuple(self):
        universe = build_initial_candidate_universe()[:3]
        results = evaluate_candidates(universe)
        assert isinstance(results, tuple)

    def test_length_matches_input(self):
        universe = build_initial_candidate_universe()[:5]
        results = evaluate_candidates(universe)
        assert len(results) == 5

    def test_order_preserved(self):
        universe = build_initial_candidate_universe()[:4]
        results = evaluate_candidates(universe)
        for spec, result in zip(universe, results):
            assert result.candidate_id == spec.candidate_id

    def test_all_blocked_without_providers(self):
        universe = build_initial_candidate_universe()[:3]
        results = evaluate_candidates(universe)
        assert all(r.result == "BLOCKED" for r in results)

    def test_pass_with_providers(self):
        universe = build_initial_candidate_universe()[:3]
        results = evaluate_candidates(
            universe,
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert all(r.result == "PASS" for r in results)

    def test_one_error_does_not_stop_others(self):
        call_count = [0]

        def counting_runner(data, candidate):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("second exploded")
            return _fake_backtest_runner(data, candidate)

        universe = build_initial_candidate_universe()[:4]
        results = evaluate_candidates(
            universe,
            data_provider=_fake_data_provider,
            backtest_runner=counting_runner,
        )
        assert len(results) == 4
        assert results[1].result == "ERROR"
        assert results[0].result == "PASS"
        assert results[2].result == "PASS"


# ---------------------------------------------------------------------------
# TestRequiredMetricKeys
# ---------------------------------------------------------------------------

class TestRequiredMetricKeys:
    def test_required_keys_non_empty(self):
        assert len(REQUIRED_METRIC_KEYS) > 0

    def test_all_required_keys_present_on_blocked(self):
        r = evaluate_candidate(_make_spec())
        for k in REQUIRED_METRIC_KEYS:
            assert k in r.metrics, f"missing key {k!r}"

    def test_all_required_keys_present_on_pass(self):
        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        for k in REQUIRED_METRIC_KEYS:
            assert k in r.metrics, f"missing key {k!r}"

    def test_expected_keys_in_required(self):
        required = set(REQUIRED_METRIC_KEYS)
        for k in (
            "cagr", "average_monthly_return", "sharpe", "max_drawdown",
            "win_rate", "profit_factor", "total_trades", "exposure_pct",
            "average_holding_bars", "session_end_frequency",
        ):
            assert k in required, f"expected key {k!r} missing from REQUIRED_METRIC_KEYS"

    def test_unknown_runner_keys_not_in_metrics(self):
        def runner_with_extra(data, candidate):
            m = _fake_backtest_runner(data, candidate)
            m["unknown_extra_key"] = 999
            return m

        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=runner_with_extra,
        )
        assert "unknown_extra_key" not in r.metrics


# ---------------------------------------------------------------------------
# TestAcceptanceGates
# ---------------------------------------------------------------------------

class TestAcceptanceGates:
    def _good_metrics(self) -> dict:
        return {
            "total_trades": 50,
            "max_drawdown": -0.15,
            "average_monthly_return": 0.01,
            "session_end_frequency": 0.1,
            "exposure_pct": 0.35,
        }

    def test_passes_good_metrics(self):
        r = apply_candidate_acceptance_gates(self._good_metrics())
        assert r.result == "PASS"
        assert r.passed is True
        assert r.blocker is None

    def test_blocks_none_total_trades(self):
        m = self._good_metrics()
        m["total_trades"] = None
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"
        assert r.passed is False

    def test_blocks_low_total_trades(self):
        m = self._good_metrics()
        m["total_trades"] = 10
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"
        assert r.passed is False

    def test_blocks_exactly_29_trades(self):
        m = self._good_metrics()
        m["total_trades"] = 29
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"

    def test_passes_exactly_30_trades(self):
        m = self._good_metrics()
        m["total_trades"] = 30
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "PASS"

    def test_blocks_none_max_drawdown(self):
        m = self._good_metrics()
        m["max_drawdown"] = None
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"

    def test_blocks_none_average_monthly_return(self):
        m = self._good_metrics()
        m["average_monthly_return"] = None
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"

    def test_blocks_high_session_end_frequency(self):
        m = self._good_metrics()
        m["session_end_frequency"] = 0.97
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"
        assert "session_end" in r.blocker

    def test_passes_session_end_frequency_at_threshold(self):
        m = self._good_metrics()
        m["session_end_frequency"] = 0.95
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "PASS"

    def test_blocks_low_vol_artifact(self):
        m = self._good_metrics()
        m["exposure_pct"] = 0.001
        m["average_monthly_return"] = 0.0001
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "BLOCKED"
        assert "low-volatility" in r.blocker

    def test_passes_low_exposure_with_meaningful_return(self):
        m = self._good_metrics()
        m["exposure_pct"] = 0.001
        m["average_monthly_return"] = 0.02
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "PASS"

    def test_missing_session_end_frequency_does_not_block(self):
        m = self._good_metrics()
        del m["session_end_frequency"]
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "PASS"

    def test_missing_exposure_pct_does_not_trigger_artifact_gate(self):
        m = self._good_metrics()
        del m["exposure_pct"]
        r = apply_candidate_acceptance_gates(m)
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_all_flags_false_on_blocked(self):
        r = evaluate_candidate(_make_spec())
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_pass(self):
        r = evaluate_candidate(
            _make_spec(),
            data_provider=_fake_data_provider,
            backtest_runner=_fake_backtest_runner,
        )
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_error(self):
        def exploding(sym, iv):
            raise RuntimeError("boom")

        r = evaluate_candidate(_make_spec(), data_provider=exploding,
                               backtest_runner=_fake_backtest_runner)
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.candidate_evaluator as _m
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

    def test_no_optimize_import(self):
        src = self._src()
        assert "scipy.optimize" not in src
        assert "optuna" not in src
        assert "hyperopt" not in src
