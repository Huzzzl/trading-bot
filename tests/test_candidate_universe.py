"""
tests/test_candidate_universe.py
----------------------------------
Tests for src/research/candidate_universe.py (PR S2).

All structures are offline, frozen, and deterministic.
No broker/API/credential/env access.
"""

from __future__ import annotations

import inspect

import pytest

from src.research.candidate_universe import (
    CandidateSpec,
    HoldingHorizon,
    StrategyFamily,
    build_initial_candidate_universe,
    filter_candidates,
)


# ---------------------------------------------------------------------------
# TestStrategyFamilyEnum
# ---------------------------------------------------------------------------

class TestStrategyFamilyEnum:
    def test_all_values_exist(self):
        values = {f.value for f in StrategyFamily}
        assert "trend_breakout" in values
        assert "mean_reversion" in values
        assert "momentum_continuation" in values
        assert "gap_overnight" in values

    def test_str_enum(self):
        assert StrategyFamily.TREND_BREAKOUT == "trend_breakout"
        assert StrategyFamily.MEAN_REVERSION == "mean_reversion"

    def test_four_families(self):
        assert len(StrategyFamily) == 4


# ---------------------------------------------------------------------------
# TestHoldingHorizonEnum
# ---------------------------------------------------------------------------

class TestHoldingHorizonEnum:
    def test_all_values_exist(self):
        values = {h.value for h in HoldingHorizon}
        assert "intraday" in values
        assert "one_day" in values
        assert "one_to_two_days" in values
        assert "three_to_five_days" in values

    def test_str_enum(self):
        assert HoldingHorizon.INTRADAY == "intraday"
        assert HoldingHorizon.THREE_TO_FIVE_DAYS == "three_to_five_days"

    def test_four_horizons(self):
        assert len(HoldingHorizon) == 4


# ---------------------------------------------------------------------------
# TestCandidateSpecDataclass
# ---------------------------------------------------------------------------

class TestCandidateSpecDataclass:
    def test_frozen(self):
        spec = CandidateSpec(
            candidate_id="test-id",
            group="A",
            symbol="SPY",
            interval="60m",
            strategy_family=StrategyFamily.TREND_BREAKOUT,
            holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
        )
        with pytest.raises(Exception):
            spec.symbol = "QQQ"  # type: ignore[misc]

    def test_enabled_default_true(self):
        spec = CandidateSpec(
            candidate_id="x",
            group="A",
            symbol="SPY",
            interval="60m",
            strategy_family=StrategyFamily.TREND_BREAKOUT,
            holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
        )
        assert spec.enabled is True

    def test_fields_accessible(self):
        spec = CandidateSpec(
            candidate_id="A_SPY_60m_trend_breakout_1to2d",
            group="A",
            symbol="SPY",
            interval="60m",
            strategy_family=StrategyFamily.TREND_BREAKOUT,
            holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
        )
        assert spec.candidate_id == "A_SPY_60m_trend_breakout_1to2d"
        assert spec.group == "A"
        assert spec.symbol == "SPY"
        assert spec.interval == "60m"
        assert spec.strategy_family == StrategyFamily.TREND_BREAKOUT
        assert spec.holding_horizon == HoldingHorizon.ONE_TO_TWO_DAYS


# ---------------------------------------------------------------------------
# TestBuildInitialCandidateUniverse
# ---------------------------------------------------------------------------

class TestBuildInitialCandidateUniverse:
    def test_returns_tuple(self):
        universe = build_initial_candidate_universe()
        assert isinstance(universe, tuple)

    def test_non_empty(self):
        assert len(build_initial_candidate_universe()) > 0

    def test_deterministic(self):
        u1 = build_initial_candidate_universe()
        u2 = build_initial_candidate_universe()
        assert u1 == u2

    def test_all_enabled(self):
        for spec in build_initial_candidate_universe():
            assert spec.enabled is True

    def test_candidate_ids_unique(self):
        universe = build_initial_candidate_universe()
        ids = [s.candidate_id for s in universe]
        assert len(ids) == len(set(ids))

    def test_all_groups_present(self):
        groups = {s.group for s in build_initial_candidate_universe()}
        for g in ("A", "B", "C", "D", "E", "F", "G"):
            assert g in groups, f"group {g!r} missing"

    def test_group_a_symbols(self):
        group_a = [s for s in build_initial_candidate_universe() if s.group == "A"]
        syms = {s.symbol for s in group_a}
        assert "SPY" in syms
        assert "QQQ" in syms
        assert "IWM" in syms

    def test_group_a_intervals(self):
        group_a = [s for s in build_initial_candidate_universe() if s.group == "A"]
        intervals = {s.interval for s in group_a}
        assert "60m" in intervals
        assert "1d" in intervals

    def test_group_b_sector_etfs(self):
        group_b = [s for s in build_initial_candidate_universe() if s.group == "B"]
        syms = {s.symbol for s in group_b}
        assert "XLK" in syms
        assert "XLF" in syms
        assert "XLE" in syms
        assert "XLY" in syms

    def test_group_c_mean_reversion(self):
        group_c = [s for s in build_initial_candidate_universe() if s.group == "C"]
        assert all(s.strategy_family == StrategyFamily.MEAN_REVERSION for s in group_c)

    def test_group_c_has_30m_and_60m(self):
        group_c = [s for s in build_initial_candidate_universe() if s.group == "C"]
        intervals = {s.interval for s in group_c}
        assert "30m" in intervals
        assert "60m" in intervals

    def test_group_d_mega_caps(self):
        group_d = [s for s in build_initial_candidate_universe() if s.group == "D"]
        syms = {s.symbol for s in group_d}
        assert "AAPL" in syms
        assert "MSFT" in syms
        assert "NVDA" in syms
        assert "AMZN" in syms

    def test_group_e_momentum(self):
        group_e = [s for s in build_initial_candidate_universe() if s.group == "E"]
        assert all(s.strategy_family == StrategyFamily.MOMENTUM_CONTINUATION for s in group_e)
        syms = {s.symbol for s in group_e}
        assert "TSLA" in syms
        assert "META" in syms
        assert "GOOGL" in syms

    def test_group_f_gap_overnight(self):
        group_f = [s for s in build_initial_candidate_universe() if s.group == "F"]
        assert all(s.strategy_family == StrategyFamily.GAP_OVERNIGHT for s in group_f)

    def test_group_g_swing_comparison(self):
        group_g = [s for s in build_initial_candidate_universe() if s.group == "G"]
        assert all(s.holding_horizon == HoldingHorizon.THREE_TO_FIVE_DAYS for s in group_g)
        assert all(s.interval == "1d" for s in group_g)

    def test_candidate_ids_contain_group(self):
        for spec in build_initial_candidate_universe():
            assert spec.candidate_id.startswith(spec.group + "_")

    def test_candidate_ids_contain_symbol(self):
        for spec in build_initial_candidate_universe():
            assert spec.symbol in spec.candidate_id

    def test_candidate_ids_contain_interval(self):
        for spec in build_initial_candidate_universe():
            assert spec.interval in spec.candidate_id

    def test_all_specs_are_candidate_spec_instances(self):
        for spec in build_initial_candidate_universe():
            assert isinstance(spec, CandidateSpec)


# ---------------------------------------------------------------------------
# TestFilterCandidates
# ---------------------------------------------------------------------------

class TestFilterCandidates:
    def setup_method(self):
        self.universe = build_initial_candidate_universe()

    def test_filter_by_group(self):
        result = filter_candidates(self.universe, group="A")
        assert all(s.group == "A" for s in result)
        assert len(result) > 0

    def test_filter_by_symbol(self):
        result = filter_candidates(self.universe, symbol="SPY")
        assert all(s.symbol == "SPY" for s in result)
        assert len(result) > 0

    def test_filter_by_interval(self):
        result = filter_candidates(self.universe, interval="60m")
        assert all(s.interval == "60m" for s in result)
        assert len(result) > 0

    def test_filter_by_strategy_family(self):
        result = filter_candidates(self.universe,
                                   strategy_family=StrategyFamily.TREND_BREAKOUT)
        assert all(s.strategy_family == StrategyFamily.TREND_BREAKOUT for s in result)

    def test_filter_by_holding_horizon(self):
        result = filter_candidates(self.universe,
                                   holding_horizon=HoldingHorizon.THREE_TO_FIVE_DAYS)
        assert all(s.holding_horizon == HoldingHorizon.THREE_TO_FIVE_DAYS for s in result)

    def test_filter_no_criteria_returns_all_enabled(self):
        result = filter_candidates(self.universe)
        enabled = [s for s in self.universe if s.enabled]
        assert len(result) == len(enabled)

    def test_filter_combined_group_and_interval(self):
        result = filter_candidates(self.universe, group="A", interval="60m")
        assert all(s.group == "A" and s.interval == "60m" for s in result)

    def test_filter_no_match_returns_empty(self):
        result = filter_candidates(self.universe, symbol="NONEXISTENT")
        assert result == ()

    def test_filter_returns_tuple(self):
        assert isinstance(filter_candidates(self.universe), tuple)

    def test_filter_excludes_disabled(self):
        disabled = CandidateSpec(
            candidate_id="Z_SPY_60m_trend_breakout_1to2d",
            group="Z",
            symbol="SPY",
            interval="60m",
            strategy_family=StrategyFamily.TREND_BREAKOUT,
            holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
            enabled=False,
        )
        mixed = self.universe + (disabled,)
        result = filter_candidates(mixed)
        assert disabled not in result

    def test_filter_disabled_false_includes_disabled(self):
        disabled = CandidateSpec(
            candidate_id="Z_SPY_60m_trend_breakout_1to2d",
            group="Z",
            symbol="SPY",
            interval="60m",
            strategy_family=StrategyFamily.TREND_BREAKOUT,
            holding_horizon=HoldingHorizon.ONE_TO_TWO_DAYS,
            enabled=False,
        )
        mixed = self.universe + (disabled,)
        result = filter_candidates(mixed, enabled_only=False)
        assert disabled in result


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.candidate_universe as _m
        return inspect.getsource(_m)

    def test_no_alpaca_import(self):
        assert "AlpacaBrokerAdapter" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_requests(self):
        assert "requests" not in self._src()

    def test_no_live_submit(self):
        assert "live_submit" not in self._src()

    def test_no_broker_calls(self):
        assert "broker_calls_made" not in self._src()
