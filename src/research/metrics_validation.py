"""
research/metrics_validation.py
---------------------------------
S4: Metrics mapping validation for candidate evaluation results.

Validates that a metrics dict (as produced by CandidateEvaluationResult.metrics)
conforms to the REQUIRED_METRIC_KEYS schema and numeric conventions used by S3.

Research metric conventions (S3/S4):
  All values are stored as decimals, not raw percentages.

  Rates and fractions in [0, 1]:
    - win_rate: fraction of winning trades, e.g. 0.55 = 55%.
    - exposure_pct: fraction of bars in-market, e.g. 0.35 = 35%.
    - session_end_frequency: fraction of exits via session_end.
    - stop_loss_frequency: fraction of exits via stop_loss.

  Return and ratio metrics:
    - cagr: annualised compound return decimal, e.g. 0.12 = 12% p.a.
    - average_monthly_return: geometric monthly mean derived from CAGR.
    - average_trade_return, median_trade_return: per-trade return decimal.
    - worst_month: worst single-month return decimal (None in S3 — not yet computed).

  Drawdown — NEGATIVE decimal convention:
    - max_drawdown: maximum peak-to-trough drawdown stored as a negative decimal,
      e.g. -0.15 for a 15% drawdown.  Valid range: max_drawdown <= 0.
      This matches the sign of max_drawdown_pct from compute_metrics (always ≤ 0).

  Ratio metrics (finite or None):
    - sharpe, sortino, calmar: risk-adjusted return ratios.
    - profit_factor: gross_profit / gross_loss, non-negative (or None).
    - slippage_bps: estimated slippage in basis points (None in S3 — price-dependent).

  Count / scale metrics (non-negative or None):
    - total_trades: integer trade count.
    - trades_per_month: average trades per calendar month.
    - average_holding_bars: average trade holding period in approximate bars/hours.

Safety flags:
  broker_calls_made, credentials_read, network_calls_made,
  order_action_requested, live_trading_allowed — all always False.

This module does not import from broker adapters, live tools, environment
variables, network libraries, or execution layers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from src.research.candidate_evaluator import REQUIRED_METRIC_KEYS

# Keys that S3 always sets to None (not yet computable from basic backtest).
# A warning is emitted when these are None so reviewers can track coverage.
_ALWAYS_NONE_IN_S3: frozenset[str] = frozenset({
    "worst_month",
    "sortino",
    "median_trade_return",
    "slippage_bps",
})

# Keys that must be in [0, 1] (decimal fraction) when not None.
_FRACTION_KEYS: frozenset[str] = frozenset({
    "win_rate",
    "exposure_pct",
    "session_end_frequency",
    "stop_loss_frequency",
})

# Keys that must be finite when not None (no specific range beyond finiteness).
_FINITE_KEYS: frozenset[str] = frozenset({
    "cagr",
    "average_monthly_return",
    "worst_month",
    "sharpe",
    "sortino",
    "calmar",
    "average_trade_return",
    "median_trade_return",
    "trades_per_month",
    "average_holding_bars",
    "slippage_bps",
})


@dataclass(frozen=True)
class MetricsValidationResult:
    """Immutable result of validate_candidate_metrics().

    result is "PASS" or "BLOCKED".
    missing_keys: keys from REQUIRED_METRIC_KEYS absent from input.
    invalid_keys: keys present but with invalid type or out-of-range value.
      Each entry is a "<key>: <reason>" string.
    warnings: informational notes (do not affect result).
    All safety flags are always False.
    """
    result: str
    blocker: str | None
    missing_keys: tuple[str, ...]
    invalid_keys: tuple[str, ...]
    warnings: tuple[str, ...]
    broker_calls_made: bool      # always False
    credentials_read: bool       # always False
    network_calls_made: bool     # always False
    order_action_requested: bool # always False
    live_trading_allowed: bool   # always False


def validate_candidate_metrics(
    metrics: Mapping[str, Any],
) -> MetricsValidationResult:
    """Validate a metrics dict against REQUIRED_METRIC_KEYS and research conventions.

    Parameters
    ----------
    metrics:
        Dict as found in CandidateEvaluationResult.metrics. May contain None
        values for metrics that are not yet computable.

    Returns
    -------
    MetricsValidationResult
        result="PASS" when all required keys are present and all non-None values
        conform to their numeric conventions. result="BLOCKED" when any key is
        missing or any value is invalid.

    Notes
    -----
    Type check: every non-None value must be int or float (not bool).
    bool is excluded because isinstance(True, int) is True in Python and a bool
    value in a numeric field is almost certainly a mapping error.

    Finiteness: NaN and Inf are rejected for all numeric fields.

    Metric conventions enforced:
      max_drawdown  ≤ 0  (negative decimal; 0.0 accepted — no drawdown).
      total_trades  ≥ 0  (non-negative count).
      profit_factor ≥ 0  (non-negative ratio).
      win_rate, exposure_pct, session_end_frequency, stop_loss_frequency ∈ [0, 1].
    """
    missing: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []

    # 1. Check all required keys are present.
    for key in REQUIRED_METRIC_KEYS:
        if key not in metrics:
            missing.append(key)

    # 2. Validate each key that is present.
    for key in REQUIRED_METRIC_KEYS:
        if key not in metrics:
            continue
        val = metrics[key]

        # None is always allowed; warn for fields S3 cannot yet compute.
        if val is None:
            if key in _ALWAYS_NONE_IN_S3:
                warnings.append(f"{key} is None (not yet computable in S3)")
            continue

        # Type check: must be int or float, not bool.
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            invalid.append(f"{key}: invalid type {type(val).__name__!r}")
            continue

        fval = float(val)

        # Finiteness check for all numeric fields.
        if not math.isfinite(fval):
            invalid.append(f"{key}: non-finite value")
            continue

        # Key-specific range checks.
        if key == "max_drawdown":
            # Negative decimal convention: max_drawdown <= 0.
            if fval > 0.0:
                invalid.append(
                    f"{key}: expected <= 0 (negative decimal convention), got positive"
                )

        elif key == "total_trades":
            if fval < 0.0:
                invalid.append(f"{key}: negative trade count")

        elif key == "profit_factor":
            if fval < 0.0:
                invalid.append(f"{key}: negative profit factor")

        elif key in _FRACTION_KEYS:
            if fval < 0.0 or fval > 1.0:
                invalid.append(
                    f"{key}: value {fval!r} outside [0, 1] fraction range"
                )

        elif key in _FINITE_KEYS:
            # Already checked finite above; no additional range constraint.
            pass

    # 3. Build result.
    _safety = dict(
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )

    if missing or invalid:
        parts = []
        if missing:
            parts.append(f"{len(missing)} missing key(s)")
        if invalid:
            parts.append(f"{len(invalid)} invalid value(s)")
        return MetricsValidationResult(
            result="BLOCKED",
            blocker="metrics validation failed: " + ", ".join(parts),
            missing_keys=tuple(missing),
            invalid_keys=tuple(invalid),
            warnings=tuple(warnings),
            **_safety,
        )

    return MetricsValidationResult(
        result="PASS",
        blocker=None,
        missing_keys=(),
        invalid_keys=(),
        warnings=tuple(warnings),
        **_safety,
    )
