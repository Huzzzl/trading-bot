"""
tests/test_paper_simulation_integration.py
-------------------------------------------
S20: Integration-style tests for the offline S17 -> S19 chain:

  validate_paper_config(config_dict) -> run_paper_simulation(config_dict, bars, ...)

These tests exercise the real validate_paper_config() (not a fake) for the
majority of cases, and confirm that the simulation respects the validator's
verdict end to end.

No broker, API, credential, env, network, or order access.
No paper or live trading approval. No file I/O. No real config files.
No cached data. All bars are in-memory plain dicts.

Simulation PASS is an offline research finding only — it does not approve
paper or live trading. Paper and live trading remain not enabled.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import pytest

from src.research.paper_config_validator import validate_paper_config
from src.research.paper_simulation import (
    PaperSimulationResult,
    PaperSimulationStatus,
    run_paper_simulation,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _valid_config(**overrides: Any) -> dict:
    """Return a fully valid PC/1.0 config dict that should pass S17."""
    base = {
        "config_schema_version": "PC/1.0",
        "candidate_id": "A_SPY_60m_trend_breakout_1to2d",
        "run_id": "20260604T000000Z_PASS",
        "source_git_sha": "abcdef1234567890",
        "symbol": "SPY",
        "interval": "60m",
        "strategy_family": "trend_breakout",
        "holding_horizon": "one_to_two_days",
        "paper_account_label": "alpaca-paper-primary",
        "max_notional_per_position": 5000.0,
        "max_position_fraction": 0.05,
        "max_daily_loss": 500.0,
        "max_drawdown_stop": 0.10,
        "max_orders_per_day": 5,
        "min_cash_buffer": 0.20,
        "allowed_order_types": ["market", "limit"],
        "allowed_session": "regular",
        "slippage_bps_assumption": 5.0,
        "commission_bps_assumption": 2.0,
        "risk_review_notes": "Reviewed for simulation design.",
        "reviewer_name": "Alice Reviewer",
        "review_date_utc": "2026-06-04T12:00:00+00:00",
        "approval_status": "APPROVED_FOR_PAPER_SIMULATION_DESIGN",
    }
    base.update(overrides)
    return base


def _make_bar(
    date: str = "2026-06-04",
    interval: str = "60m",
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
) -> dict:
    return {
        "timestamp": f"{date}T10:00:00",
        "interval": interval,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _make_bars(n: int = 3, date: str = "2026-06-04", interval: str = "60m") -> list[dict]:
    return [_make_bar(date=date, interval=interval) for _ in range(n)]


def _enter_then_exit_provider(bar, prior_bars, config_dict, state):
    """Signal ENTER_LONG on bar 0, EXIT on bar 1, HOLD otherwise."""
    idx = len(prior_bars)
    if idx == 0:
        return "ENTER_LONG"
    if idx == 1:
        return "EXIT"
    return "HOLD"


def _run_chain(config_dict: dict, bars: list[dict], **kwargs) -> tuple[Any, PaperSimulationResult]:
    """Run the real S17 validator then the S19 simulation; return both results."""
    validation_result = validate_paper_config(config_dict)
    sim_result = run_paper_simulation(
        config_dict,
        bars,
        start_date=kwargs.pop("start_date", "2026-06-04"),
        end_date=kwargs.pop("end_date", "2026-06-04"),
        **kwargs,
    )
    return validation_result, sim_result


def _assert_all_safety_flags_false(obj: Any) -> None:
    for flag in (
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    ):
        assert getattr(obj, flag) is False, f"{flag} must be False, got {getattr(obj, flag)!r}"


# ---------------------------------------------------------------------------
# 1. Valid config passes S17 AND S19
# ---------------------------------------------------------------------------

class TestValidConfigChain:
    def test_valid_config_passes_validator_and_simulation(self):
        cfg = _valid_config()
        bars = _make_bars()
        validation_result, sim_result = _run_chain(cfg, bars)
        assert validation_result.result == "PASS"
        assert sim_result.result == "PASS"
        assert sim_result.status == PaperSimulationStatus.PASS
        assert sim_result.blocker is None


# ---------------------------------------------------------------------------
# 2. Invalid schema -> BLOCKED_CONFIG
# ---------------------------------------------------------------------------

class TestInvalidSchemaChain:
    def test_invalid_schema_version_blocks_simulation(self):
        cfg = _valid_config(config_schema_version="PC/0.1")
        bars = _make_bars()
        validation_result, sim_result = _run_chain(cfg, bars)
        assert validation_result.result == "BLOCKED"
        assert sim_result.result == "BLOCKED"
        assert sim_result.status == PaperSimulationStatus.BLOCKED_CONFIG
        assert sim_result.blocker is not None


# ---------------------------------------------------------------------------
# 3. Forbidden credential-like field -> BLOCKED_CONFIG
# ---------------------------------------------------------------------------

class TestCredentialScanChain:
    def test_credential_like_field_blocks_simulation(self):
        cfg = _valid_config()
        cfg["api_key"] = "should-never-be-here"
        bars = _make_bars()
        validation_result, sim_result = _run_chain(cfg, bars)
        assert validation_result.result == "BLOCKED"
        assert sim_result.result == "BLOCKED"
        assert sim_result.status == PaperSimulationStatus.BLOCKED_CONFIG
        assert sim_result.blocker is not None


# ---------------------------------------------------------------------------
# 4. Invalid risk limit -> BLOCKED_CONFIG
# ---------------------------------------------------------------------------

class TestInvalidRiskLimitChain:
    def test_invalid_risk_limit_blocks_simulation(self):
        # max_position_fraction above the validator's allowed cap (0.10)
        cfg = _valid_config(max_position_fraction=0.99)
        bars = _make_bars()
        validation_result, sim_result = _run_chain(cfg, bars)
        assert validation_result.result == "BLOCKED"
        assert sim_result.result == "BLOCKED"
        assert sim_result.status == PaperSimulationStatus.BLOCKED_CONFIG
        assert sim_result.blocker is not None


# ---------------------------------------------------------------------------
# 5. Valid config but empty bars -> BLOCKED_DATA
# ---------------------------------------------------------------------------

class TestEmptyBarsChain:
    def test_valid_config_empty_bars_blocked_data(self):
        cfg = _valid_config()
        validation_result, sim_result = _run_chain(cfg, [])
        assert validation_result.result == "PASS"
        assert sim_result.result == "BLOCKED"
        assert sim_result.status == PaperSimulationStatus.BLOCKED_DATA
        assert sim_result.blocker is not None


# ---------------------------------------------------------------------------
# 6. Valid config but interval mismatch -> BLOCKED_CONFIG
# ---------------------------------------------------------------------------

class TestIntervalMismatchChain:
    def test_interval_mismatch_blocks_simulation(self):
        cfg = _valid_config(interval="60m")
        bars = _make_bars(interval="5m")
        validation_result, sim_result = _run_chain(cfg, bars)
        assert validation_result.result == "PASS"
        assert sim_result.result == "BLOCKED"
        assert sim_result.status == PaperSimulationStatus.BLOCKED_CONFIG
        assert sim_result.blocker is not None


# ---------------------------------------------------------------------------
# 7. Deterministic ENTER_LONG -> EXIT provider produces exactly one trade
# ---------------------------------------------------------------------------

class TestSingleTradeChain:
    def test_enter_then_exit_produces_exactly_one_trade(self):
        cfg = _valid_config()
        bars = _make_bars(n=3)
        validation_result, sim_result = _run_chain(
            cfg, bars, _signal_provider=_enter_then_exit_provider,
        )
        assert validation_result.result == "PASS"
        assert sim_result.result == "PASS"
        assert len(sim_result.trades) == 1
        trade = sim_result.trades[0]
        assert trade["entry_bar_index"] == 0
        assert trade["exit_bar_index"] == 1
        assert trade["exit_reason"] == "signal_exit"


# ---------------------------------------------------------------------------
# 8. Summary preserves candidate_id and run_id from config
# ---------------------------------------------------------------------------

class TestProvenancePreservedChain:
    def test_summary_preserves_candidate_id_and_run_id(self):
        cfg = _valid_config(
            candidate_id="A_QQQ_60m_trend_breakout_1to2d",
            run_id="20260605T000000Z_PASS",
        )
        bars = _make_bars()
        validation_result, sim_result = _run_chain(cfg, bars)
        assert validation_result.result == "PASS"
        assert sim_result.result == "PASS"
        assert sim_result.summary["candidate_id"] == "A_QQQ_60m_trend_breakout_1to2d"
        assert sim_result.summary["run_id"] == "20260605T000000Z_PASS"


# ---------------------------------------------------------------------------
# 9. Safety flags all False on PASS / BLOCKED / ERROR paths
# ---------------------------------------------------------------------------

class TestSafetyFlagsAcrossPaths:
    def test_safety_flags_false_on_pass(self):
        cfg = _valid_config()
        bars = _make_bars()
        _, sim_result = _run_chain(cfg, bars)
        assert sim_result.result == "PASS"
        _assert_all_safety_flags_false(sim_result)

    def test_safety_flags_false_on_blocked_config(self):
        cfg = _valid_config(config_schema_version="PC/0.1")
        bars = _make_bars()
        _, sim_result = _run_chain(cfg, bars)
        assert sim_result.result == "BLOCKED"
        _assert_all_safety_flags_false(sim_result)

    def test_safety_flags_false_on_blocked_data(self):
        cfg = _valid_config()
        _, sim_result = _run_chain(cfg, [])
        assert sim_result.result == "BLOCKED"
        _assert_all_safety_flags_false(sim_result)

    def test_safety_flags_false_on_error(self):
        cfg = _valid_config()
        bars = _make_bars()

        def bad_signal(bar, prior, cfg_, state):
            return "NOT_A_REAL_SIGNAL"

        _, sim_result = _run_chain(cfg, bars, _signal_provider=bad_signal)
        assert sim_result.result == "ERROR"
        assert sim_result.status == PaperSimulationStatus.ERROR_SIMULATION
        _assert_all_safety_flags_false(sim_result)


# ---------------------------------------------------------------------------
# 10. Simulation PASS is not treated as paper/live approval anywhere
# ---------------------------------------------------------------------------

class TestPassIsNotApproval:
    def test_pass_does_not_grant_live_trading(self):
        cfg = _valid_config()
        bars = _make_bars()
        _, sim_result = _run_chain(cfg, bars)
        assert sim_result.result == "PASS"
        assert sim_result.live_trading_allowed is False
        assert sim_result.order_action_requested is False
        assert sim_result.summary["live_trading_allowed"] is False
        assert sim_result.summary["order_action_requested"] is False

    def test_no_approval_field_is_present_or_true(self):
        cfg = _valid_config()
        bars = _make_bars()
        _, sim_result = _run_chain(cfg, bars)
        assert sim_result.result == "PASS"
        forbidden_keys = (
            "paper_trading_approved",
            "live_trading_approved",
            "trading_approved",
            "approved_for_paper_trading",
            "approved_for_live_trading",
        )
        for key in forbidden_keys:
            assert key not in sim_result.summary


# ---------------------------------------------------------------------------
# 11. At least 5 tests above use the real validate_paper_config (not a fake)
# ---------------------------------------------------------------------------

class TestUsesRealValidator:
    """All tests in this module call _run_chain(), which always invokes the
    real validate_paper_config() imported from src.research.paper_config_validator
    (no _config_validator override is ever passed). This class explicitly
    documents and re-asserts that wiring for at least five scenarios."""

    @pytest.mark.parametrize(
        "cfg_factory, bars_factory",
        [
            (lambda: _valid_config(), lambda: _make_bars()),
            (lambda: _valid_config(config_schema_version="PC/0.1"), lambda: _make_bars()),
            (lambda: _valid_config(max_position_fraction=0.99), lambda: _make_bars()),
            (lambda: _valid_config(), lambda: []),
            (lambda: _valid_config(interval="60m"), lambda: _make_bars(interval="5m")),
        ],
    )
    def test_real_validator_drives_each_scenario(self, cfg_factory, bars_factory):
        cfg = cfg_factory()
        bars = bars_factory()
        # Calls the real validator directly (no fake/injected validator).
        validation_result = validate_paper_config(cfg)
        sim_result = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
        )
        # The simulation's config gate must agree with the real validator:
        # a non-PASS validation always yields BLOCKED_CONFIG with the
        # validator's verdict propagated as the blocker. A PASS validation
        # lets the simulation proceed past the config gate (its eventual
        # status then depends on the bars supplied, e.g. BLOCKED_DATA or
        # BLOCKED_CONFIG for an interval mismatch — but never BLOCKED_SAFETY,
        # since the real validator reported no True safety flags).
        if validation_result.result == "PASS":
            assert sim_result.status != PaperSimulationStatus.BLOCKED_SAFETY
        else:
            assert sim_result.status == PaperSimulationStatus.BLOCKED_CONFIG
            assert sim_result.blocker is not None


# ---------------------------------------------------------------------------
# 12. No mutation of config or bars in the integration path
# ---------------------------------------------------------------------------

class TestNoMutation:
    def test_config_and_bars_not_mutated(self):
        cfg = _valid_config()
        bars = _make_bars(n=4)
        cfg_before = copy.deepcopy(cfg)
        bars_before = copy.deepcopy(bars)

        validation_result = validate_paper_config(cfg)
        sim_result = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )

        assert cfg == cfg_before
        assert bars == bars_before
        assert validation_result.result == "PASS"
        assert sim_result.result == "PASS"


# ---------------------------------------------------------------------------
# 13. Same input -> same output (pure / deterministic)
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        cfg = _valid_config()
        bars = _make_bars(n=4)

        validation_1 = validate_paper_config(copy.deepcopy(cfg))
        sim_1 = run_paper_simulation(
            copy.deepcopy(cfg), copy.deepcopy(bars),
            start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )

        validation_2 = validate_paper_config(copy.deepcopy(cfg))
        sim_2 = run_paper_simulation(
            copy.deepcopy(cfg), copy.deepcopy(bars),
            start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )

        assert validation_1.result == validation_2.result
        assert validation_1.status == validation_2.status
        assert sim_1.result == sim_2.result
        assert sim_1.summary == sim_2.summary
        assert sim_1.trades == sim_2.trades
        assert sim_1.equity_curve == sim_2.equity_curve
        assert sim_1.risk_limit_events == sim_2.risk_limit_events


# ---------------------------------------------------------------------------
# 14. No generated files/artifacts exist in repo paths after running the chain
# ---------------------------------------------------------------------------

class TestNoArtifactsWritten:
    def _snapshot_paths(self) -> set[str]:
        snapshot: set[str] = set()
        for rel in ("output", "data/cache"):
            abs_dir = os.path.join(_REPO_ROOT, rel)
            if os.path.isdir(abs_dir):
                for root, _dirs, files in os.walk(abs_dir):
                    for fname in files:
                        snapshot.add(os.path.join(root, fname))
        return snapshot

    def test_no_files_written_by_chain(self):
        before = self._snapshot_paths()

        cfg = _valid_config()
        bars = _make_bars(n=5)
        validation_result, sim_result = _run_chain(
            cfg, bars, _signal_provider=_enter_then_exit_provider,
        )
        assert validation_result.result == "PASS"
        assert sim_result.result == "PASS"

        after = self._snapshot_paths()
        assert after == before, f"unexpected files created: {after - before}"
