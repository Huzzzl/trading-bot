"""
Tests for strategy/signal_engine.py.

All tests are offline-only — no broker calls, no credentials, no network
access, no order execution, no config mutation, no ledger writes.
The signal engine is a pure function: same inputs always produce the same output.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from src.strategy.signal_engine import (
    Bar,
    OpenOrderState,
    PositionState,
    SignalEngineConfig,
    SignalResult,
    evaluate_signal,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = SignalEngineConfig(
    strategy_name="sma_crossover",
    symbol="SPY",
    timeframe="1h",
    min_bars_required=5,
    short_window=2,
    long_window=4,
)

_FLAT = PositionState(has_position=False)
_LONG = PositionState(has_position=True, symbol="SPY")
_NO_ORDER = OpenOrderState(has_open_order=False)
_HAS_ORDER = OpenOrderState(has_open_order=True)
_SESSION_OPEN = "open"


def _bar(close: float) -> Bar:
    return Bar(open=close, high=close, low=close, close=close, volume=1000.0)


def _bullish_bars(config: SignalEngineConfig = _DEFAULT_CONFIG) -> list[Bar]:
    """Returns bars where short SMA > long SMA (rising trend)."""
    # closes: [100, 101, 102, 103, 110]
    # short_window=2: SMA of [103, 110] = 106.5
    # long_window=4:  SMA of [101, 102, 103, 110] = 104.0
    # 106.5 > 104.0 → bullish
    return [_bar(c) for c in [100.0, 101.0, 102.0, 103.0, 110.0]]


def _bearish_bars(config: SignalEngineConfig = _DEFAULT_CONFIG) -> list[Bar]:
    """Returns bars where short SMA < long SMA (falling trend)."""
    # closes: [110, 109, 108, 107, 100]
    # short_window=2: SMA of [107, 100] = 103.5
    # long_window=4:  SMA of [109, 108, 107, 100] = 106.0
    # 103.5 < 106.0 → bearish
    return [_bar(c) for c in [110.0, 109.0, 108.0, 107.0, 100.0]]


# ---------------------------------------------------------------------------
# TestBuySignal
# ---------------------------------------------------------------------------

class TestBuySignal:
    def test_buy_on_bullish_crossover_no_position(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BUY"

    def test_buy_reason_code(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "SMA_CROSSOVER_BULLISH" in result.reason_codes

    def test_buy_has_reason_codes_list(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert isinstance(result.reason_codes, list)
        assert len(result.reason_codes) >= 1

    def test_buy_bar_count_correct(self) -> None:
        bars = _bullish_bars()
        result = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.bar_count == len(bars)

    def test_buy_symbol_in_result(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.symbol == "SPY"

    def test_buy_strategy_name_in_result(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.strategy_name == _DEFAULT_CONFIG.strategy_name


# ---------------------------------------------------------------------------
# TestSellSignal
# ---------------------------------------------------------------------------

class TestSellSignal:
    def test_sell_on_bearish_crossover_has_position(self) -> None:
        result = evaluate_signal(_bearish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "SELL"

    def test_sell_reason_code(self) -> None:
        result = evaluate_signal(_bearish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "SMA_CROSSOVER_BEARISH" in result.reason_codes

    def test_sell_bar_count_correct(self) -> None:
        bars = _bearish_bars()
        result = evaluate_signal(bars, _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.bar_count == len(bars)

    def test_sell_symbol_in_result(self) -> None:
        result = evaluate_signal(_bearish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.symbol == "SPY"


# ---------------------------------------------------------------------------
# TestHoldSignal
# ---------------------------------------------------------------------------

class TestHoldSignal:
    def test_hold_when_bullish_but_already_has_position(self) -> None:
        result = evaluate_signal(_bullish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "HOLD"

    def test_hold_reason_already_in_position(self) -> None:
        result = evaluate_signal(_bullish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "HOLD_ALREADY_IN_POSITION" in result.reason_codes

    def test_hold_when_bearish_but_flat(self) -> None:
        result = evaluate_signal(_bearish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "HOLD"

    def test_hold_reason_no_position_to_exit(self) -> None:
        result = evaluate_signal(_bearish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "HOLD_NO_POSITION_TO_EXIT" in result.reason_codes

    def test_hold_bar_count_correct(self) -> None:
        bars = _bullish_bars()
        result = evaluate_signal(bars, _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.bar_count == len(bars)


# ---------------------------------------------------------------------------
# TestBlockInsufficientBars
# ---------------------------------------------------------------------------

class TestBlockInsufficientBars:
    def test_block_zero_bars(self) -> None:
        result = evaluate_signal([], _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_reason_insufficient_bars(self) -> None:
        result = evaluate_signal([], _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "INSUFFICIENT_BARS" in result.reason_codes

    def test_block_one_bar_below_min(self) -> None:
        result = evaluate_signal([_bar(100.0)], _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_exactly_at_boundary_minus_one(self) -> None:
        bars = [_bar(100.0)] * (_DEFAULT_CONFIG.min_bars_required - 1)
        result = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_no_block_at_min_bars(self) -> None:
        # Exactly min_bars_required bars — sufficient to pass this gate
        # Use bullish pattern so result is BUY (not BLOCK for other reason)
        config = SignalEngineConfig(
            strategy_name="sma_crossover",
            symbol="SPY",
            timeframe="1h",
            min_bars_required=5,
            short_window=2,
            long_window=4,
        )
        bars = _bullish_bars(config)
        result = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal != "BLOCK" or "INSUFFICIENT_BARS" not in result.reason_codes


# ---------------------------------------------------------------------------
# TestBlockInvalidSymbol
# ---------------------------------------------------------------------------

class TestBlockInvalidSymbol:
    def _config_with_symbol(self, symbol: str) -> SignalEngineConfig:
        return SignalEngineConfig(
            strategy_name="sma_crossover",
            symbol=symbol,
            timeframe="1h",
            min_bars_required=5,
            short_window=2,
            long_window=4,
        )

    def test_block_non_spy_symbol(self) -> None:
        config = self._config_with_symbol("AAPL")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"

    def test_block_reason_invalid_symbol(self) -> None:
        config = self._config_with_symbol("AAPL")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert "INVALID_SYMBOL" in result.reason_codes

    def test_block_lowercase_spy(self) -> None:
        config = self._config_with_symbol("spy")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"

    def test_block_empty_symbol(self) -> None:
        config = self._config_with_symbol("")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"

    def test_no_block_for_spy(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "INVALID_SYMBOL" not in result.reason_codes


# ---------------------------------------------------------------------------
# TestBlockInvalidTimeframe
# ---------------------------------------------------------------------------

class TestBlockInvalidTimeframe:
    def _config_with_timeframe(self, tf: str) -> SignalEngineConfig:
        return SignalEngineConfig(
            strategy_name="sma_crossover",
            symbol="SPY",
            timeframe=tf,
            min_bars_required=5,
            short_window=2,
            long_window=4,
        )

    def test_block_5m_timeframe(self) -> None:
        config = self._config_with_timeframe("5m")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"

    def test_block_reason_invalid_timeframe(self) -> None:
        config = self._config_with_timeframe("5m")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert "INVALID_TIMEFRAME" in result.reason_codes

    def test_block_empty_timeframe(self) -> None:
        config = self._config_with_timeframe("")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"

    def test_block_tick_timeframe(self) -> None:
        config = self._config_with_timeframe("tick")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"

    def test_no_block_for_1h(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "INVALID_TIMEFRAME" not in result.reason_codes

    def test_no_block_for_1d(self) -> None:
        config = self._config_with_timeframe("1d")
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert "INVALID_TIMEFRAME" not in result.reason_codes


# ---------------------------------------------------------------------------
# TestBlockMarketSession
# ---------------------------------------------------------------------------

class TestBlockMarketSession:
    def test_block_market_closed(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "closed", _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_pre_market(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "pre_market", _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_after_hours(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "after_hours", _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_none_session(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, None, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_reason_market_not_open(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "closed", _DEFAULT_CONFIG)
        assert "MARKET_NOT_OPEN" in result.reason_codes

    def test_block_reason_market_not_open_pre_market(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "pre_market", _DEFAULT_CONFIG)
        assert "MARKET_NOT_OPEN" in result.reason_codes

    def test_block_reason_market_not_open_after_hours(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "after_hours", _DEFAULT_CONFIG)
        assert "MARKET_NOT_OPEN" in result.reason_codes

    def test_block_reason_market_not_open_none(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, None, _DEFAULT_CONFIG)
        assert "MARKET_NOT_OPEN" in result.reason_codes

    def test_no_block_when_open(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "open", _DEFAULT_CONFIG)
        assert "MARKET_NOT_OPEN" not in result.reason_codes


# ---------------------------------------------------------------------------
# TestBlockOpenOrder
# ---------------------------------------------------------------------------

class TestBlockOpenOrder:
    def test_block_when_open_order_present(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _HAS_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_block_reason_open_order_present(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _HAS_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "OPEN_ORDER_PRESENT" in result.reason_codes

    def test_block_open_order_with_position(self) -> None:
        result = evaluate_signal(_bullish_bars(), _LONG, _HAS_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"

    def test_no_block_when_no_open_order(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert "OPEN_ORDER_PRESENT" not in result.reason_codes


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output_buy(self) -> None:
        bars = _bullish_bars()
        r1 = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        r2 = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert r1.signal == r2.signal
        assert r1.reason_codes == r2.reason_codes

    def test_same_input_same_output_sell(self) -> None:
        bars = _bearish_bars()
        r1 = evaluate_signal(bars, _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        r2 = evaluate_signal(bars, _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert r1.signal == r2.signal

    def test_same_input_same_output_block(self) -> None:
        r1 = evaluate_signal([], _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        r2 = evaluate_signal([], _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert r1.signal == r2.signal
        assert r1.reason_codes == r2.reason_codes

    def test_deterministic_flag_always_true(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.deterministic is True

    def test_multiple_runs_same_result(self) -> None:
        bars = _bullish_bars()
        results = [
            evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
            for _ in range(5)
        ]
        signals = {r.signal for r in results}
        assert len(signals) == 1


# ---------------------------------------------------------------------------
# TestInputNotMutated
# ---------------------------------------------------------------------------

class TestInputNotMutated:
    def test_bars_list_not_mutated(self) -> None:
        bars = _bullish_bars()
        bars_copy = copy.deepcopy(bars)
        evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert bars == bars_copy

    def test_bars_length_unchanged(self) -> None:
        bars = _bullish_bars()
        original_len = len(bars)
        evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert len(bars) == original_len

    def test_bars_closes_unchanged(self) -> None:
        bars = _bullish_bars()
        original_closes = [b.close for b in bars]
        evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert [b.close for b in bars] == original_closes


# ---------------------------------------------------------------------------
# TestOutputFields
# ---------------------------------------------------------------------------

class TestOutputFields:
    def test_result_is_signal_result(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert isinstance(result, SignalResult)

    def test_signal_is_string(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert isinstance(result.signal, str)

    def test_signal_is_valid(self) -> None:
        for session in ["open", "closed", "pre_market", "after_hours", None]:
            result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, session, _DEFAULT_CONFIG)
            assert result.signal in {"BUY", "SELL", "HOLD", "BLOCK"}

    def test_reason_codes_is_list(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert isinstance(result.reason_codes, list)

    def test_reason_codes_non_empty(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert len(result.reason_codes) >= 1

    def test_reason_codes_are_strings(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        for rc in result.reason_codes:
            assert isinstance(rc, str)

    def test_timeframe_in_result(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.timeframe == _DEFAULT_CONFIG.timeframe

    def test_strategy_name_in_result(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.strategy_name == _DEFAULT_CONFIG.strategy_name

    def test_bar_count_in_result(self) -> None:
        bars = _bullish_bars()
        result = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.bar_count == len(bars)


# ---------------------------------------------------------------------------
# TestSafetyFields
# ---------------------------------------------------------------------------

class TestSafetyFields:
    """All safety invariant fields must hold for every signal type."""

    def _assert_safety(self, result: SignalResult) -> None:
        assert result.broker_calls_made is False
        assert result.credentials_read is False
        assert result.live_submit_enabled is False
        assert result.order_action_requested is False
        assert result.position_decision_is_recommendation_only is True
        assert result.deterministic is True

    def test_safety_fields_on_buy(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BUY"
        self._assert_safety(result)

    def test_safety_fields_on_sell(self) -> None:
        result = evaluate_signal(_bearish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "SELL"
        self._assert_safety(result)

    def test_safety_fields_on_hold_bullish(self) -> None:
        result = evaluate_signal(_bullish_bars(), _LONG, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "HOLD"
        self._assert_safety(result)

    def test_safety_fields_on_hold_bearish(self) -> None:
        result = evaluate_signal(_bearish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "HOLD"
        self._assert_safety(result)

    def test_safety_fields_on_block_insufficient_bars(self) -> None:
        result = evaluate_signal([], _FLAT, _NO_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"
        self._assert_safety(result)

    def test_safety_fields_on_block_invalid_symbol(self) -> None:
        config = SignalEngineConfig(
            strategy_name="sma_crossover", symbol="AAPL", timeframe="1h",
            min_bars_required=5, short_window=2, long_window=4,
        )
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BLOCK"
        self._assert_safety(result)

    def test_safety_fields_on_block_market_closed(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "closed", _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"
        self._assert_safety(result)

    def test_safety_fields_on_block_open_order(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _HAS_ORDER, _SESSION_OPEN, _DEFAULT_CONFIG)
        assert result.signal == "BLOCK"
        self._assert_safety(result)

    def test_broker_calls_never_true(self) -> None:
        cases = [
            (_bullish_bars(), _FLAT, _NO_ORDER, "open"),
            (_bearish_bars(), _LONG, _NO_ORDER, "open"),
            ([], _FLAT, _NO_ORDER, "open"),
            (_bullish_bars(), _FLAT, _NO_ORDER, "closed"),
            (_bullish_bars(), _FLAT, _HAS_ORDER, "open"),
        ]
        for bars, pos, orders, session in cases:
            result = evaluate_signal(bars, pos, orders, session, _DEFAULT_CONFIG)
            assert result.broker_calls_made is False

    def test_credentials_never_read(self) -> None:
        cases = [
            (_bullish_bars(), _FLAT, _NO_ORDER, "open"),
            (_bearish_bars(), _LONG, _NO_ORDER, "open"),
            ([], _FLAT, _NO_ORDER, "open"),
        ]
        for bars, pos, orders, session in cases:
            result = evaluate_signal(bars, pos, orders, session, _DEFAULT_CONFIG)
            assert result.credentials_read is False

    def test_position_decision_is_recommendation_only_always_true(self) -> None:
        cases = [
            (_bullish_bars(), _FLAT, _NO_ORDER, "open"),
            (_bearish_bars(), _LONG, _NO_ORDER, "open"),
            (_bullish_bars(), _LONG, _NO_ORDER, "open"),
            ([], _FLAT, _NO_ORDER, "open"),
        ]
        for bars, pos, orders, session in cases:
            result = evaluate_signal(bars, pos, orders, session, _DEFAULT_CONFIG)
            assert result.position_decision_is_recommendation_only is True


# ---------------------------------------------------------------------------
# TestGateOrder
# ---------------------------------------------------------------------------

class TestGateOrder:
    """Verify that gates are checked in the correct order."""

    def test_insufficient_bars_before_symbol(self) -> None:
        config = SignalEngineConfig(
            strategy_name="sma_crossover", symbol="AAPL", timeframe="1h",
            min_bars_required=5, short_window=2, long_window=4,
        )
        result = evaluate_signal([], _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert "INSUFFICIENT_BARS" in result.reason_codes

    def test_symbol_before_timeframe(self) -> None:
        config = SignalEngineConfig(
            strategy_name="sma_crossover", symbol="AAPL", timeframe="5m",
            min_bars_required=5, short_window=2, long_window=4,
        )
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert "INVALID_SYMBOL" in result.reason_codes
        assert "INVALID_TIMEFRAME" not in result.reason_codes

    def test_timeframe_before_session(self) -> None:
        config = SignalEngineConfig(
            strategy_name="sma_crossover", symbol="SPY", timeframe="5m",
            min_bars_required=5, short_window=2, long_window=4,
        )
        result = evaluate_signal(_bullish_bars(), _FLAT, _NO_ORDER, "closed", config)
        assert "INVALID_TIMEFRAME" in result.reason_codes
        assert "MARKET_NOT_OPEN" not in result.reason_codes

    def test_session_before_open_order(self) -> None:
        result = evaluate_signal(_bullish_bars(), _FLAT, _HAS_ORDER, "closed", _DEFAULT_CONFIG)
        assert "MARKET_NOT_OPEN" in result.reason_codes
        assert "OPEN_ORDER_PRESENT" not in result.reason_codes


# ---------------------------------------------------------------------------
# TestSmaLogic
# ---------------------------------------------------------------------------

class TestSmaLogic:
    """Verify SMA computation produces correct signals."""

    def test_custom_windows_buy(self) -> None:
        config = SignalEngineConfig(
            strategy_name="sma_crossover",
            symbol="SPY",
            timeframe="1h",
            min_bars_required=6,
            short_window=2,
            long_window=5,
        )
        # short SMA of last 2: (120+130)/2 = 125
        # long SMA of last 5: (100+105+110+120+130)/5 = 113
        # 125 > 113 → BUY
        bars = [_bar(c) for c in [95.0, 100.0, 105.0, 110.0, 120.0, 130.0]]
        result = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "BUY"

    def test_custom_windows_sell(self) -> None:
        config = SignalEngineConfig(
            strategy_name="sma_crossover",
            symbol="SPY",
            timeframe="1h",
            min_bars_required=6,
            short_window=2,
            long_window=5,
        )
        # short SMA of last 2: (80+70)/2 = 75
        # long SMA of last 5: (100+95+90+80+70)/5 = 87
        # 75 < 87 → SELL
        bars = [_bar(c) for c in [105.0, 100.0, 95.0, 90.0, 80.0, 70.0]]
        result = evaluate_signal(bars, _LONG, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "SELL"

    def test_equal_sma_produces_hold(self) -> None:
        # short SMA == long SMA: neither > nor <, HOLD
        config = SignalEngineConfig(
            strategy_name="sma_crossover",
            symbol="SPY",
            timeframe="1h",
            min_bars_required=4,
            short_window=2,
            long_window=4,
        )
        # all bars same close: 100.0 → both SMAs = 100.0
        bars = [_bar(100.0)] * 4
        result = evaluate_signal(bars, _FLAT, _NO_ORDER, _SESSION_OPEN, config)
        assert result.signal == "HOLD"


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

def _signal_engine_source_lines() -> list[str]:
    """Return non-comment source lines from signal_engine.py."""
    src_path = (
        Path(__file__).parent.parent / "src" / "strategy" / "signal_engine.py"
    )
    lines = []
    for line in src_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_part = line.split("#")[0]
        if code_part.strip():
            lines.append(code_part)
    return lines


class TestSourceScans:
    def test_no_alpaca_import(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            stripped = line.strip()
            assert "import alpaca" not in stripped, f"alpaca import found: {line!r}"
            assert "from alpaca" not in stripped, f"alpaca import found: {line!r}"

    def test_no_requests_import(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "import requests" not in line, f"requests import found: {line!r}"

    def test_no_httpx_import(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "import httpx" not in line, f"httpx import found: {line!r}"

    def test_no_aiohttp_import(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "import aiohttp" not in line, f"aiohttp import found: {line!r}"

    def test_no_urllib_request_import(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            stripped = line.strip()
            assert not (
                stripped.startswith("import urllib") or stripped.startswith("from urllib")
            ), f"urllib import found: {line!r}"

    def test_no_os_environ(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "os.environ" not in line, f"os.environ found: {line!r}"

    def test_no_os_import(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            stripped = line.strip()
            assert stripped != "import os", f"import os found: {line!r}"
            assert not stripped.startswith("from os "), f"from os found: {line!r}"

    def test_no_submit_order(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "submit_order(" not in line, f"submit_order( found: {line!r}"

    def test_no_cancel_order(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "cancel_order(" not in line, f"cancel_order( found: {line!r}"

    def test_no_replace_order(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "replace_order(" not in line, f"replace_order( found: {line!r}"

    def test_no_close_position(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "close_position(" not in line, f"close_position( found: {line!r}"

    def test_no_post_mutation(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert ".post(" not in line.lower(), f"POST mutation found: {line!r}"

    def test_no_patch_mutation(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert ".patch(" not in line.lower(), f"PATCH mutation found: {line!r}"

    def test_no_delete_mutation(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert ".delete(" not in line.lower(), f"DELETE mutation found: {line!r}"

    def test_no_open_for_write(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert 'open(' not in line or '"w"' not in line and "'w'" not in line, (
                f"possible file write found: {line!r}"
            )

    def test_no_write_text_call(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "write_text(" not in line, f"write_text( found: {line!r}"

    def test_no_json_dump_to_file(self) -> None:
        lines = _signal_engine_source_lines()
        for line in lines:
            assert "json.dump(" not in line, f"json.dump( found: {line!r}"
