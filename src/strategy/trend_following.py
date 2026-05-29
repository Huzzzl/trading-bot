"""
strategy/trend_following.py
----------------------------
TrendFollowing strategy — long-only, EMA trend filter + prior-bar breakout.

Pure, deterministic, offline-only. No look-ahead.

Entry: bullish EMA trend (classify_trend) AND close > prior rolling-high breakout.
Exit:  bearish trend OR close < fast EMA.

Stop metadata (ATR-based) is included in Signal.meta and Signal.stop_loss
for the risk manager / backtest engine to act on. No broker calls are made.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
**No order is submitted, cancelled, replaced, or closed.**
**No trade records are written.**
**No config is mutated.**
"""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from src.analysis.trend import classify_trend
from src.indicators.trend import rolling_high
from src.strategy.base import BaseStrategy, Signal, SignalDirection

_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-/]{1,10}$")
_VALID_TIMEFRAMES = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}
)

_STRATEGY_NAME = "trend_following"

# Fixed reason strings — no raw values echoed.
_REASON_INSUFFICIENT_BARS       = "INSUFFICIENT_BARS"
_REASON_INVALID_TREND_STATE     = "INVALID_TREND_STATE"
_REASON_TREND_NOT_BULLISH       = "TREND_NOT_BULLISH"
_REASON_BREAKOUT_NOT_CONFIRMED  = "BREAKOUT_NOT_CONFIRMED"
_REASON_ENTRY_CUTOFF_PASSED     = "ENTRY_CUTOFF_PASSED"
_REASON_BUY                     = "BUY_BULLISH_BREAKOUT"
_REASON_HOLD_NO_POSITION        = "HOLD_NO_POSITION"
_REASON_SELL_BEARISH            = "SELL_BEARISH_TREND"
_REASON_SELL_BELOW_FAST_EMA     = "SELL_CLOSE_BELOW_FAST_EMA"


class TrendFollowing(BaseStrategy):
    """Long-only trend-following strategy.

    Supported constructor params (all optional; defaults shown):
    -----------------------------------------------------------
    symbol              str         "SPY"
    timeframe           str         "1h"       passed to classify_trend
    fast_ema_period     int         20
    slow_ema_period     int         50
    atr_period          int         14
    volatility_lookback int         50
    breakout_lookback   int         5
    atr_stop_mult       float       2.0
    risk_per_trade_pct  float       1.0
    long_only           bool        True       MVP: must remain True
    entry_cutoff_time   str|None    None       "HH:MM" Eastern to stop new entries
    force_exit_time     str|None    None       informational; handled by engine

    Entry conditions (all must hold):
        1. len(bars) >= min_required_bars
        2. classify_trend(bars).trend == "bullish"
        3. latest close > prior rolling-high over breakout_lookback bars
           (current bar is excluded from the reference — no look-ahead)

    Exit conditions (checked before entry each bar):
        - trend == "bearish"   →  EXIT signal
        - close < fast_ema     →  EXIT signal

    Signals are recommendations only.  No broker calls are made.
    """

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        safe: dict[str, Any] = dict(params) if params is not None else {}
        super().__init__(safe)

        try:
            self._symbol: str          = str(safe.get("symbol", "SPY"))
            self._timeframe: str       = str(safe.get("timeframe", "1h"))
            self._fast_ema_period: int = int(safe.get("fast_ema_period", 20))
            self._slow_ema_period: int = int(safe.get("slow_ema_period", 50))
            self._atr_period: int      = int(safe.get("atr_period", 14))
            self._volatility_lookback: int = int(safe.get("volatility_lookback", 50))
            self._breakout_lookback: int   = int(safe.get("breakout_lookback", 5))
            self._atr_stop_mult: float     = float(safe.get("atr_stop_mult", 2.0))
            self._risk_per_trade_pct: float = float(safe.get("risk_per_trade_pct", 1.0))
            self._long_only: bool = bool(safe.get("long_only", True))
            self._entry_cutoff_time: str | None = (
                str(safe["entry_cutoff_time"])
                if safe.get("entry_cutoff_time") is not None
                else None
            )
            self._force_exit_time: str | None = (
                str(safe["force_exit_time"])
                if safe.get("force_exit_time") is not None
                else None
            )
        except (TypeError, ValueError):
            raise ValueError("invalid strategy parameters")

        self._validate_params()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_params(self) -> None:
        if not _VALID_SYMBOL_RE.match(self._symbol):
            raise ValueError("invalid strategy parameters")
        if self._timeframe not in _VALID_TIMEFRAMES:
            raise ValueError("invalid strategy parameters")
        if self._fast_ema_period < 1:
            raise ValueError("invalid strategy parameters")
        if self._slow_ema_period < 1:
            raise ValueError("invalid strategy parameters")
        if self._fast_ema_period >= self._slow_ema_period:
            raise ValueError("invalid strategy parameters")
        if self._atr_period < 1:
            raise ValueError("invalid strategy parameters")
        if self._volatility_lookback < 1:
            raise ValueError("invalid strategy parameters")
        if self._breakout_lookback < 1:
            raise ValueError("invalid strategy parameters")
        if self._atr_stop_mult <= 0:
            raise ValueError("invalid strategy parameters")
        if self._risk_per_trade_pct <= 0:
            raise ValueError("invalid strategy parameters")
        if not self._long_only:
            raise ValueError("invalid strategy parameters")

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def generate_signal(
        self,
        symbol: str,
        bars: pd.DataFrame,
        current_bar: pd.Timestamp,
    ) -> Signal | None:
        """Evaluate the current bar and return a Signal or None.

        Parameters
        ----------
        symbol:
            Ticker being evaluated.  Passed through to classify_trend.
        bars:
            All bars up to and including *current_bar*.  Not mutated.
        current_bar:
            Timestamp of the bar being processed (last row in *bars*).

        Returns
        -------
        Signal with direction=LONG  — buy entry confirmed.
        Signal with direction=EXIT  — exit / close position recommended.
        None                        — hold; no action this bar.
        """
        # --- Guard: sufficient bars ------------------------------------
        min_required = max(
            self._slow_ema_period,
            self._atr_period + self._volatility_lookback - 1,
            self._breakout_lookback + 1,
        )
        if len(bars) < min_required:
            return None

        # --- Trend classification (read-only copy inside classify_trend) -
        trend_state = classify_trend(
            bars,
            symbol=symbol,
            timeframe=self._timeframe,
            fast_ema_period=self._fast_ema_period,
            slow_ema_period=self._slow_ema_period,
            atr_period=self._atr_period,
            volatility_lookback=self._volatility_lookback,
        )

        if trend_state.trend == "unknown":
            return None

        close = float(bars["close"].iloc[-1])
        fast_ema = trend_state.fast_ema
        atr_val = trend_state.atr

        # --- Breakout reference (prior bars only; current excluded) ----
        breakout_ref_series = rolling_high(
            bars["high"], self._breakout_lookback, exclude_current=True
        )
        breakout_high = float(breakout_ref_series.iloc[-1])

        # ATR-based stop (metadata only; execution left to engine)
        atr_stop_price = (
            close - self._atr_stop_mult * atr_val
            if not math.isnan(atr_val)
            else float("nan")
        )

        def _meta(reason: str) -> dict[str, Any]:
            return {
                "strategy_name": _STRATEGY_NAME,
                "symbol": symbol,
                "trend": trend_state.trend,
                "trend_strength": trend_state.strength,
                "volatility_regime": trend_state.volatility_regime,
                "fast_ema": fast_ema,
                "slow_ema": trend_state.slow_ema,
                "atr": atr_val,
                "breakout_high": breakout_high,
                "atr_stop_price": atr_stop_price,
                "atr_stop_mult": self._atr_stop_mult,
                "breakout_lookback": self._breakout_lookback,
                "reason": reason,
                "deterministic": True,
                "broker_calls_made": False,
                "credentials_read": False,
                "order_action_requested": False,
                "recommendation_only": True,
            }

        # --- Exit conditions (always evaluated — not gated by cutoff) --
        if trend_state.trend == "bearish":
            return Signal(
                direction=SignalDirection.EXIT,
                symbol=symbol,
                entry_price=close,
                stop_loss=None,
                timestamp=current_bar,
                meta=_meta(_REASON_SELL_BEARISH),
            )

        if not math.isnan(fast_ema) and close < fast_ema:
            return Signal(
                direction=SignalDirection.EXIT,
                symbol=symbol,
                entry_price=close,
                stop_loss=None,
                timestamp=current_bar,
                meta=_meta(_REASON_SELL_BELOW_FAST_EMA),
            )

        # --- Entry cutoff: blocks new LONG entries only ----------------
        if self._entry_cutoff_time is not None:
            try:
                from zoneinfo import ZoneInfo
                _eastern = ZoneInfo("America/New_York")
                bar_hhmm = current_bar.astimezone(_eastern).strftime("%H:%M")
                if bar_hhmm > self._entry_cutoff_time:
                    return None
            except Exception:
                pass  # if tz conversion fails, do not block

        # --- Entry condition: bullish trend + prior-bar breakout -------
        if trend_state.trend != "bullish":
            return None

        if math.isnan(breakout_high) or close <= breakout_high:
            return None

        stop = atr_stop_price if not math.isnan(atr_stop_price) else None
        return Signal(
            direction=SignalDirection.LONG,
            symbol=symbol,
            entry_price=close,
            stop_loss=stop,
            timestamp=current_bar,
            meta=_meta(_REASON_BUY),
        )
