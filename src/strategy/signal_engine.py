"""
strategy/signal_engine.py
--------------------------
Offline-only deterministic strategy signal engine — Phase A.

Pure function implementation of the SMA-crossover signal strategy.
This module is the first step toward automated strategy execution and
implements only the signal-generation layer.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls of any kind.**
**No order is submitted, sold, cancelled, replaced, or closed.**
**No live ledger is written.**
**No config is mutated.**
**No live or paper trading is implemented.**
**No scheduler is implemented.**
**BUY or SELL from this module does not execute anything.**
**All signals are recommendations only — risk gate and executor are not implemented here.**
**This module does not approve automated trading.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_VALID_TIMEFRAMES: frozenset[str] = frozenset({"1h", "1d"})
_REQUIRED_SYMBOL: str = "SPY"

# Reason codes
_RC_INSUFFICIENT_BARS: str = "INSUFFICIENT_BARS"
_RC_INVALID_SYMBOL: str = "INVALID_SYMBOL"
_RC_INVALID_TIMEFRAME: str = "INVALID_TIMEFRAME"
_RC_MARKET_NOT_OPEN: str = "MARKET_NOT_OPEN"
_RC_OPEN_ORDER_PRESENT: str = "OPEN_ORDER_PRESENT"
_RC_SMA_CROSSOVER_BULLISH: str = "SMA_CROSSOVER_BULLISH"
_RC_SMA_CROSSOVER_BEARISH: str = "SMA_CROSSOVER_BEARISH"
_RC_HOLD_ALREADY_IN_POSITION: str = "HOLD_ALREADY_IN_POSITION"
_RC_HOLD_NO_POSITION_TO_EXIT: str = "HOLD_NO_POSITION_TO_EXIT"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar. Entry price and cost basis are excluded."""

    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class PositionState:
    """Current position state. Entry price is intentionally absent."""

    has_position: bool
    symbol: str | None = None


@dataclass(frozen=True)
class OpenOrderState:
    """Current open order state."""

    has_open_order: bool


@dataclass(frozen=True)
class SignalEngineConfig:
    """Configuration for the signal engine evaluation."""

    strategy_name: str
    symbol: str
    timeframe: str
    min_bars_required: int
    short_window: int
    long_window: int


@dataclass
class SignalResult:
    """
    Output of evaluate_signal().

    Signal is a recommendation only — it does not execute anything.
    BUY/SELL/HOLD/BLOCK must pass through the risk gate and executor
    before any broker action can occur. Neither the risk gate nor the
    executor is implemented in this module.
    """

    signal: str
    reason_codes: list[str]
    strategy_name: str
    timeframe: str
    symbol: str
    bar_count: int
    deterministic: bool = True
    broker_calls_made: bool = False
    credentials_read: bool = False
    live_submit_enabled: bool = False
    order_action_requested: bool = False
    position_decision_is_recommendation_only: bool = True


def _sma(values: list[float], window: int) -> float:
    """Compute simple moving average of the last *window* values."""
    return sum(values[-window:]) / window


def evaluate_signal(
    bars: list[Bar],
    position_state: PositionState,
    open_order_state: OpenOrderState,
    market_session: str | None,
    config: SignalEngineConfig,
) -> SignalResult:
    """Evaluate bar data and return a deterministic signal.

    Pure function: same inputs always produce the same output.
    No broker calls, no credential reads, no order execution.

    Parameters
    ----------
    bars:
        Historical OHLCV bars in chronological order; most recent bar last.
        Must not be mutated by this function.
    position_state:
        Boolean position presence only. Entry price excluded.
    open_order_state:
        Boolean open order presence.
    market_session:
        Allowlisted: ``"open"``, ``"closed"``, ``"pre_market"``,
        ``"after_hours"``, or ``None``.
    config:
        Strategy configuration including windows and symbol constraint.

    Returns
    -------
    SignalResult
        Signal is one of ``"BUY"``, ``"SELL"``, ``"HOLD"``, ``"BLOCK"``.
        Result is a recommendation only — no execution occurs here.
    """
    bar_count = len(bars)

    def _block(reason_code: str) -> SignalResult:
        return SignalResult(
            signal="BLOCK",
            reason_codes=[reason_code],
            strategy_name=config.strategy_name,
            timeframe=config.timeframe,
            symbol=config.symbol,
            bar_count=bar_count,
        )

    def _result(signal: str, reason_code: str) -> SignalResult:
        return SignalResult(
            signal=signal,
            reason_codes=[reason_code],
            strategy_name=config.strategy_name,
            timeframe=config.timeframe,
            symbol=config.symbol,
            bar_count=bar_count,
        )

    # Gate 1 — insufficient bars
    if bar_count < config.min_bars_required:
        return _block(_RC_INSUFFICIENT_BARS)

    # Gate 2 — symbol validation (SPY only)
    if config.symbol != _REQUIRED_SYMBOL:
        return _block(_RC_INVALID_SYMBOL)

    # Gate 3 — timeframe validation
    if config.timeframe not in _VALID_TIMEFRAMES:
        return _block(_RC_INVALID_TIMEFRAME)

    # Gate 4 — market session (trading only when market is open)
    if market_session != "open":
        return _block(_RC_MARKET_NOT_OPEN)

    # Gate 5 — open order ambiguity blocks new signals
    if open_order_state.has_open_order:
        return _block(_RC_OPEN_ORDER_PRESENT)

    # Compute SMAs from bar close prices; bars list is not mutated
    closes = [bar.close for bar in bars]
    short_sma = _sma(closes, config.short_window)
    long_sma = _sma(closes, config.long_window)

    # BUY: short SMA crossed above long SMA, no existing position
    if short_sma > long_sma and not position_state.has_position:
        return _result("BUY", _RC_SMA_CROSSOVER_BULLISH)

    # SELL: short SMA crossed below long SMA, position is open
    if short_sma < long_sma and position_state.has_position:
        return _result("SELL", _RC_SMA_CROSSOVER_BEARISH)

    # HOLD: bullish but already in position, or bearish but no position to exit
    if short_sma > long_sma:
        return _result("HOLD", _RC_HOLD_ALREADY_IN_POSITION)

    return _result("HOLD", _RC_HOLD_NO_POSITION_TO_EXIT)
