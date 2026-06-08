"""
tests/test_paper_simulation.py
-------------------------------------
S19: Tests for the pure offline paper simulation skeleton.

No broker, API, credential, env, network, or order access.
No paper or live trading approval. No file I/O from the simulation.
All providers are injected fake/deterministic objects.
"""

from __future__ import annotations

import copy
import importlib
import inspect
from typing import Any

import pytest

from src.research.paper_simulation import (
    PaperSimulationResult,
    PaperSimulationStatus,
    run_paper_simulation,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _valid_config(**overrides: Any) -> dict:
    """Return a fully valid DRAFT paper config dict."""
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


def _blocked_validator(config_dict: dict):
    """Fake validator that always returns BLOCKED."""
    from src.research.paper_config_validator import (
        PaperConfigStatus,
        PaperConfigValidationResult,
    )
    return PaperConfigValidationResult(
        result="BLOCKED",
        blocker="fake block",
        candidate_id=None,
        run_id=None,
        status=PaperConfigStatus.BLOCKED_PROVENANCE,
        criteria_checked=("schema.config",),
        criteria_failed=("schema.config",),
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _safety_flag_validator(config_dict: dict):
    """Fake validator that returns PASS but with a True safety flag."""
    from src.research.paper_config_validator import (
        PaperConfigStatus,
        PaperConfigValidationResult,
    )
    return PaperConfigValidationResult(
        result="PASS",
        blocker=None,
        candidate_id="A_SPY_60m_trend_breakout_1to2d",
        run_id="20260604T000000Z_PASS",
        status=PaperConfigStatus.APPROVED_FOR_PAPER_SIMULATION_DESIGN,
        criteria_checked=tuple(),
        criteria_failed=tuple(),
        broker_calls_made=True,   # <-- safety flag set True
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _enter_then_exit_provider(bar, prior_bars, config_dict, state):
    """Signal ENTER_LONG on bar 0, EXIT on bar 1, HOLD otherwise."""
    idx = len(prior_bars)
    if idx == 0:
        return "ENTER_LONG"
    if idx == 1:
        return "EXIT"
    return "HOLD"


def _always_enter_provider(bar, prior_bars, config_dict, state):
    """Always signal ENTER_LONG."""
    return "ENTER_LONG"


def _default_run(**kwargs) -> PaperSimulationResult:
    cfg = kwargs.pop("config_dict", _valid_config())
    bars = kwargs.pop("bars", _make_bars())
    return run_paper_simulation(
        cfg,
        bars,
        start_date=kwargs.pop("start_date", "2026-06-04"),
        end_date=kwargs.pop("end_date", "2026-06-04"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TestPaperSimulationStatusEnum
# ---------------------------------------------------------------------------

class TestPaperSimulationStatusEnum:
    def test_not_run(self):
        assert PaperSimulationStatus.NOT_RUN == "NOT_RUN"

    def test_pass(self):
        assert PaperSimulationStatus.PASS == "PASS"

    def test_blocked_config(self):
        assert PaperSimulationStatus.BLOCKED_CONFIG == "BLOCKED_CONFIG"

    def test_blocked_safety(self):
        assert PaperSimulationStatus.BLOCKED_SAFETY == "BLOCKED_SAFETY"

    def test_blocked_data(self):
        assert PaperSimulationStatus.BLOCKED_DATA == "BLOCKED_DATA"

    def test_error_simulation(self):
        assert PaperSimulationStatus.ERROR_SIMULATION == "ERROR_SIMULATION"

    def test_all_six_values(self):
        values = {s.value for s in PaperSimulationStatus}
        assert values == {
            "NOT_RUN", "PASS", "BLOCKED_CONFIG",
            "BLOCKED_SAFETY", "BLOCKED_DATA", "ERROR_SIMULATION",
        }


# ---------------------------------------------------------------------------
# TestPaperSimulationResultDataclass
# ---------------------------------------------------------------------------

class TestPaperSimulationResultDataclass:
    def test_is_frozen(self):
        r = _default_run()
        with pytest.raises((AttributeError, TypeError)):
            r.result = "BLOCKED"  # type: ignore[misc]

    def test_has_all_required_fields(self):
        r = _default_run()
        for f in (
            "result", "blocker", "status", "summary",
            "trades", "equity_curve", "risk_limit_events",
            "broker_calls_made", "credentials_read", "network_calls_made",
            "order_action_requested", "live_trading_allowed",
        ):
            assert hasattr(r, f), f"missing field: {f}"

    def test_trades_is_tuple(self):
        assert isinstance(_default_run().trades, tuple)

    def test_equity_curve_is_tuple(self):
        assert isinstance(_default_run().equity_curve, tuple)

    def test_risk_limit_events_is_tuple(self):
        assert isinstance(_default_run().risk_limit_events, tuple)

    def test_status_is_enum(self):
        assert isinstance(_default_run().status, PaperSimulationStatus)

    def test_summary_is_dict(self):
        assert isinstance(_default_run().summary, dict)


# ---------------------------------------------------------------------------
# TestDefaultNoSignal
# ---------------------------------------------------------------------------

class TestDefaultNoSignal:
    def test_returns_pass(self):
        r = _default_run()
        assert r.result == "PASS"
        assert r.status == PaperSimulationStatus.PASS
        assert r.blocker is None

    def test_zero_trades(self):
        assert _default_run().trades == ()

    def test_equity_curve_populated(self):
        r = _default_run()
        assert len(r.equity_curve) >= 1

    def test_summary_bars_used(self):
        r = _default_run()
        assert r.summary["bars_used"] == 3

    def test_summary_zero_trades(self):
        assert _default_run().summary["total_simulated_trades"] == 0

    def test_all_safety_flags_false(self):
        r = _default_run()
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.network_calls_made
        assert not r.order_action_requested
        assert not r.live_trading_allowed

    def test_summary_safety_flags_false(self):
        s = _default_run().summary
        assert not s["broker_calls_made"]
        assert not s["credentials_read"]
        assert not s["network_calls_made"]
        assert not s["order_action_requested"]
        assert not s["live_trading_allowed"]


# ---------------------------------------------------------------------------
# TestConfigGate
# ---------------------------------------------------------------------------

class TestConfigGate:
    def test_blocked_validator_returns_blocked_config(self):
        r = _default_run(_config_validator=_blocked_validator)
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_CONFIG

    def test_blocked_validator_bars_not_processed(self):
        processed = []
        def counting_signal(bar, prior, cfg, state):
            processed.append(bar)
            return "HOLD"
        _default_run(
            _config_validator=_blocked_validator,
            _signal_provider=counting_signal,
        )
        assert processed == []

    def test_safety_flag_validator_returns_blocked_safety(self):
        r = _default_run(_config_validator=_safety_flag_validator)
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_SAFETY

    def test_safety_flag_validator_bars_not_processed(self):
        processed = []
        def counting_signal(bar, prior, cfg, state):
            processed.append(bar)
            return "HOLD"
        _default_run(
            _config_validator=_safety_flag_validator,
            _signal_provider=counting_signal,
        )
        assert processed == []

    def test_invalid_config_schema_blocked(self):
        r = _default_run(config_dict=_valid_config(config_schema_version="BAD"))
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_CONFIG

    def test_validator_exception_blocked_config(self):
        def raising_validator(cfg):
            raise RuntimeError("validator exploded")
        r = _default_run(_config_validator=raising_validator)
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_CONFIG

    def test_interval_mismatch_blocked_config(self):
        bars = _make_bars(interval="1d")  # all bars are 1d
        r = run_paper_simulation(
            _valid_config(interval="60m"),
            bars,
            start_date="2026-06-04",
            end_date="2026-06-04",
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_CONFIG


# ---------------------------------------------------------------------------
# TestDataGate
# ---------------------------------------------------------------------------

class TestDataGate:
    def test_empty_bars_blocked_data(self):
        r = run_paper_simulation(
            _valid_config(), [], start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_bars_not_list_blocked_data(self):
        r = run_paper_simulation(
            _valid_config(), "not-a-list",  # type: ignore[arg-type]
            start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_missing_bar_field_blocked_data(self):
        bar = _make_bar()
        del bar["close"]
        r = run_paper_simulation(
            _valid_config(), [bar], start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_non_positive_close_blocked_data(self):
        bar = _make_bar(close=0.0)
        r = run_paper_simulation(
            _valid_config(), [bar], start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_negative_close_blocked_data(self):
        bar = _make_bar(close=-5.0)
        r = run_paper_simulation(
            _valid_config(), [bar], start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_nan_close_blocked_data(self):
        import math
        bar = _make_bar(close=float("nan"))
        r = run_paper_simulation(
            _valid_config(), [bar], start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_date_range_selects_no_bars_blocked_data(self):
        bars = _make_bars(date="2026-06-04")
        r = run_paper_simulation(
            _valid_config(), bars, start_date="2026-01-01", end_date="2026-01-31"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA

    def test_bar_not_dict_blocked_data(self):
        r = run_paper_simulation(
            _valid_config(), ["not-a-dict"],  # type: ignore[list-item]
            start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r.result == "BLOCKED"
        assert r.status == PaperSimulationStatus.BLOCKED_DATA


# ---------------------------------------------------------------------------
# TestUnsupportedSignal
# ---------------------------------------------------------------------------

class TestUnsupportedSignal:
    def test_bad_signal_returns_error(self):
        def bad_signal(bar, prior, cfg, state):
            return "SELL_SHORT"
        r = _default_run(_signal_provider=bad_signal)
        assert r.result == "ERROR"
        assert r.status == PaperSimulationStatus.ERROR_SIMULATION

    def test_signal_exception_returns_error(self):
        def raising_signal(bar, prior, cfg, state):
            raise ValueError("signal exploded")
        r = _default_run(_signal_provider=raising_signal)
        assert r.result == "ERROR"
        assert r.status == PaperSimulationStatus.ERROR_SIMULATION

    def test_fill_model_exception_on_entry_returns_error(self):
        def enter_signal(bar, prior, cfg, state):
            return "ENTER_LONG"
        def bad_fill(bar, side, slippage_bps):
            raise RuntimeError("fill exploded")
        r = _default_run(
            _signal_provider=enter_signal,
            _fill_model=bad_fill,
        )
        assert r.result == "ERROR"
        assert r.status == PaperSimulationStatus.ERROR_SIMULATION

    def test_fill_model_exception_on_exit_returns_error(self):
        def bad_fill(bar, side, slippage_bps):
            if side == "exit":
                raise RuntimeError("exit fill exploded")
            return float(bar["close"])
        r = _default_run(
            _signal_provider=_enter_then_exit_provider,
            _fill_model=bad_fill,
        )
        assert r.result == "ERROR"
        assert r.status == PaperSimulationStatus.ERROR_SIMULATION


# ---------------------------------------------------------------------------
# TestSimulatedTrade
# ---------------------------------------------------------------------------

class TestSimulatedTrade:
    def test_enter_then_exit_creates_one_trade(self):
        bars = _make_bars(n=3)
        r = run_paper_simulation(
            _valid_config(),
            bars,
            start_date="2026-06-04",
            end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r.result == "PASS"
        assert len(r.trades) == 1

    def test_trade_has_required_fields(self):
        bars = _make_bars(n=3)
        r = run_paper_simulation(
            _valid_config(),
            bars,
            start_date="2026-06-04",
            end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        trade = r.trades[0]
        for field in (
            "entry_bar_index", "exit_bar_index",
            "entry_price_assumption", "exit_price_assumption",
            "simulated_shares", "simulated_pnl", "exit_reason",
        ):
            assert field in trade, f"missing trade field: {field}"

    def test_slippage_affects_pnl_deterministically(self):
        bars = [_make_bar(close=100.0), _make_bar(close=110.0), _make_bar(close=110.0)]
        r_low = run_paper_simulation(
            _valid_config(slippage_bps_assumption=0.0, commission_bps_assumption=0.0),
            bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        r_high = run_paper_simulation(
            _valid_config(slippage_bps_assumption=50.0, commission_bps_assumption=0.0),
            bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r_low.result == "PASS"
        assert r_high.result == "PASS"
        assert r_low.trades[0]["simulated_pnl"] > r_high.trades[0]["simulated_pnl"]

    def test_commission_affects_pnl_deterministically(self):
        bars = [_make_bar(close=100.0), _make_bar(close=110.0), _make_bar(close=110.0)]
        r_low = run_paper_simulation(
            _valid_config(slippage_bps_assumption=0.0, commission_bps_assumption=0.0),
            bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        r_high = run_paper_simulation(
            _valid_config(slippage_bps_assumption=0.0, commission_bps_assumption=50.0),
            bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r_low.trades[0]["simulated_pnl"] > r_high.trades[0]["simulated_pnl"]

    def test_summary_trade_count_matches(self):
        bars = _make_bars(n=3)
        r = run_paper_simulation(
            _valid_config(),
            bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r.summary["total_simulated_trades"] == len(r.trades)


# ---------------------------------------------------------------------------
# TestPositionSizing
# ---------------------------------------------------------------------------

class TestPositionSizing:
    def test_max_notional_caps_position(self):
        cfg = _valid_config(
            max_notional_per_position=500.0,
            max_position_fraction=0.10,   # at cap; notional cap 500 < fraction cap 10_000
            min_cash_buffer=0.01,         # small positive (validator requires > 0)
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        bars = [_make_bar(close=100.0), _make_bar(close=100.0)]
        r = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r.result == "PASS"
        trade = r.trades[0]
        notional = trade["simulated_shares"] * trade["entry_price_assumption"]
        # notional cap is 500; fraction cap is 0.10 * 100_000 = 10_000; notional wins
        assert notional <= 500.0 + 1e-6

    def test_max_position_fraction_caps_position(self):
        cfg = _valid_config(
            max_notional_per_position=100_000.0,  # much larger than fraction cap
            max_position_fraction=0.01,            # fraction cap: 0.01 * 100_000 = 1_000
            min_cash_buffer=0.01,                  # small positive (validator requires > 0)
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        bars = [_make_bar(close=100.0), _make_bar(close=100.0)]
        r = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r.result == "PASS"
        trade = r.trades[0]
        notional = trade["simulated_shares"] * trade["entry_price_assumption"]
        assert notional <= 100_000.0 * 0.01 + 1e-6

    def test_min_cash_buffer_caps_deployable_cash(self):
        # With min_cash_buffer=0.99, only 1% of cash is deployable.
        # deployable = 100_000 * (1 - 0.99) = 1_000, which is less than
        # the notional cap (50_000) and fraction cap (10_000).
        cfg_tight = _valid_config(
            max_notional_per_position=50_000.0,
            max_position_fraction=0.10,
            min_cash_buffer=0.99,
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        cfg_loose = _valid_config(
            max_notional_per_position=50_000.0,
            max_position_fraction=0.10,
            min_cash_buffer=0.01,
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        bars = [_make_bar(close=100.0), _make_bar(close=100.0)]
        r_tight = run_paper_simulation(
            cfg_tight, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        r_loose = run_paper_simulation(
            cfg_loose, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=_enter_then_exit_provider,
        )
        assert r_tight.result == "PASS"
        assert r_loose.result == "PASS"
        # tight buffer => smaller position => smaller absolute pnl magnitude
        tight_shares = r_tight.trades[0]["simulated_shares"]
        loose_shares = r_loose.trades[0]["simulated_shares"]
        assert tight_shares < loose_shares


# ---------------------------------------------------------------------------
# TestRiskLimitEvents
# ---------------------------------------------------------------------------

class TestRiskLimitEvents:
    def test_max_orders_per_day_creates_risk_event(self):
        cfg = _valid_config(
            max_orders_per_day=1,
            max_notional_per_position=5000.0,
            min_cash_buffer=0.01,
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        bars = [
            _make_bar(date="2026-06-04"),
            _make_bar(date="2026-06-04"),
            _make_bar(date="2026-06-04"),
        ]
        # ENTER on bar 0, EXIT on bar 1, ENTER again on bar 2 (should be blocked)
        def signal(bar, prior, cfg, state):
            idx = len(prior)
            if idx == 0:
                return "ENTER_LONG"
            if idx == 1:
                return "EXIT"
            return "ENTER_LONG"

        r = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=signal,
        )
        assert r.result == "PASS"
        limit_types = [e["limit_type"] for e in r.risk_limit_events]
        assert "max_orders_per_day" in limit_types

    def test_max_daily_loss_creates_risk_event(self):
        cfg = _valid_config(
            max_daily_loss=0.01,  # very tight — $0.01
            max_notional_per_position=5000.0,
            max_position_fraction=0.05,
            min_cash_buffer=0.01,
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        # Trade at a loss first, then try to enter again same day
        bars = [
            _make_bar(date="2026-06-04", close=100.0),
            _make_bar(date="2026-06-04", close=50.0),  # big drop on exit
            _make_bar(date="2026-06-04", close=50.0),  # try to enter again
        ]
        def signal(bar, prior, cfg, state):
            idx = len(prior)
            if idx == 0:
                return "ENTER_LONG"
            if idx == 1:
                return "EXIT"
            return "ENTER_LONG"

        r = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=signal,
        )
        assert r.result == "PASS"
        limit_types = [e["limit_type"] for e in r.risk_limit_events]
        assert "max_daily_loss" in limit_types

    def test_max_drawdown_stop_creates_risk_event(self):
        cfg = _valid_config(
            max_drawdown_stop=0.001,  # very tight — 0.1%; a 2.5% loss triggers it
            max_notional_per_position=5000.0,
            max_position_fraction=0.05,
            min_cash_buffer=0.01,
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        bars = [
            _make_bar(date="2026-06-04", close=100.0),
            _make_bar(date="2026-06-04", close=50.0),  # large exit loss
            _make_bar(date="2026-06-04", close=50.0),  # try to enter again
        ]
        def signal(bar, prior, cfg, state):
            idx = len(prior)
            if idx == 0:
                return "ENTER_LONG"
            if idx == 1:
                return "EXIT"
            return "ENTER_LONG"

        r = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=signal,
        )
        assert r.result == "PASS"
        limit_types = [e["limit_type"] for e in r.risk_limit_events]
        assert "max_drawdown_stop" in limit_types

    def test_risk_limit_events_is_tuple(self):
        assert isinstance(_default_run().risk_limit_events, tuple)

    def test_risk_event_has_required_fields(self):
        cfg = _valid_config(
            max_orders_per_day=1,
            max_notional_per_position=5000.0,
            min_cash_buffer=0.01,
            slippage_bps_assumption=0.0,
            commission_bps_assumption=0.0,
        )
        bars = [
            _make_bar(date="2026-06-04"),
            _make_bar(date="2026-06-04"),
            _make_bar(date="2026-06-04"),
        ]
        # ENTER on bar 0, EXIT on bar 1, ENTER again on bar 2 (blocked by
        # max_orders_per_day -> guaranteed risk_limit_event)
        def signal(bar, prior, cfg, state):
            idx = len(prior)
            if idx == 0:
                return "ENTER_LONG"
            if idx == 1:
                return "EXIT"
            return "ENTER_LONG"

        r = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04",
            _signal_provider=signal,
        )
        assert r.result == "PASS"
        assert len(r.risk_limit_events) >= 1
        for evt in r.risk_limit_events:
            assert "bar_index" in evt
            assert "limit_type" in evt
            assert "limit_value" in evt
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestEquityCurve
# ---------------------------------------------------------------------------

class TestEquityCurve:
    def test_equity_curve_is_deterministic(self):
        bars = _make_bars(n=3)
        r1 = _default_run(bars=bars)
        r2 = _default_run(bars=bars)
        assert r1.equity_curve == r2.equity_curve

    def test_equity_curve_record_has_required_fields(self):
        r = _default_run()
        for rec in r.equity_curve:
            assert "date" in rec
            assert "simulated_equity" in rec
            assert "daily_pnl" in rec

    def test_equity_curve_in_memory_not_file(self):
        r = _default_run()
        assert isinstance(r.equity_curve, tuple)


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def _check_all_false(self, r: PaperSimulationResult) -> None:
        assert not r.broker_calls_made
        assert not r.credentials_read
        assert not r.network_calls_made
        assert not r.order_action_requested
        assert not r.live_trading_allowed

    def test_pass_result(self):
        self._check_all_false(_default_run())

    def test_blocked_config_result(self):
        self._check_all_false(_default_run(_config_validator=_blocked_validator))

    def test_blocked_safety_result(self):
        self._check_all_false(_default_run(_config_validator=_safety_flag_validator))

    def test_blocked_data_result(self):
        r = run_paper_simulation(
            _valid_config(), [], start_date="2026-06-04", end_date="2026-06-04"
        )
        self._check_all_false(r)

    def test_error_result(self):
        def bad_signal(bar, prior, cfg, state):
            return "INVALID"
        self._check_all_false(_default_run(_signal_provider=bad_signal))


# ---------------------------------------------------------------------------
# TestPureFunctionProperties
# ---------------------------------------------------------------------------

class TestPureFunctionProperties:
    def test_does_not_mutate_config_dict(self):
        cfg = _valid_config()
        original = copy.deepcopy(cfg)
        _default_run(config_dict=cfg)
        assert cfg == original

    def test_does_not_mutate_bars(self):
        bars = _make_bars(n=3)
        original = copy.deepcopy(bars)
        _default_run(bars=bars)
        assert bars == original

    def test_same_input_same_output_pass(self):
        cfg = _valid_config()
        bars = _make_bars(n=3)
        r1 = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04"
        )
        r2 = run_paper_simulation(
            cfg, bars, start_date="2026-06-04", end_date="2026-06-04"
        )
        assert r1 == r2

    def test_same_input_same_output_blocked(self):
        r1 = _default_run(_config_validator=_blocked_validator)
        r2 = _default_run(_config_validator=_blocked_validator)
        assert r1 == r2

    def test_independent_runs_are_independent(self):
        bars_a = _make_bars(n=2)
        bars_b = _make_bars(n=5)
        ra = run_paper_simulation(
            _valid_config(), bars_a, start_date="2026-06-04", end_date="2026-06-04"
        )
        rb = run_paper_simulation(
            _valid_config(), bars_b, start_date="2026-06-04", end_date="2026-06-04"
        )
        assert ra.summary["bars_used"] != rb.summary["bars_used"]


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

_sim_mod = importlib.import_module("src.research.paper_simulation")


class TestNoForbiddenImports:
    _FORBIDDEN_IMPORT_PATTERNS = (
        "import requests",
        "import urllib",
        "import aiohttp",
        "import alpaca",
        "import socket",
        "import subprocess",
    )

    def test_no_forbidden_stdlib_imports(self):
        source = inspect.getsource(_sim_mod)
        for pat in self._FORBIDDEN_IMPORT_PATTERNS:
            assert pat not in source, f"found forbidden import: {pat}"

    def test_no_open_calls(self):
        source = inspect.getsource(_sim_mod)
        assert "open(" not in source

    def test_no_import_os(self):
        source = inspect.getsource(_sim_mod)
        assert "import os" not in source

    def test_no_pathlib_write(self):
        source = inspect.getsource(_sim_mod)
        for pat in ("read_text", "write_text", "write_bytes", "open("):
            assert pat not in source, f"found forbidden pattern: {pat}"

    def test_no_network_patterns(self):
        source = inspect.getsource(_sim_mod)
        for pat in ("requests.get", "requests.post", "urlopen", "socket.connect"):
            assert pat not in source, f"found forbidden pattern: {pat}"

    def test_no_broker_adapter_import(self):
        source = inspect.getsource(_sim_mod)
        for pat in ("AlpacaBrokerAdapter", "alpaca_trade_api", "broker.submit"):
            assert pat not in source, f"found forbidden pattern: {pat}"

    def test_no_live_submit_import(self):
        source = inspect.getsource(_sim_mod)
        assert "live_submit" not in source

    def test_no_order_action(self):
        source = inspect.getsource(_sim_mod)
        for pat in ("place_order", "submit_order"):
            assert pat not in source, f"found forbidden pattern: {pat}"
