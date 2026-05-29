"""
backtest/metrics_diagnostics.py
--------------------------------
Offline diagnostic helper for Sharpe-ratio calculation inspection.

Accepts an equity curve and an interval string, recomputes the Sharpe ratio
using the same formula as compute_metrics(), and returns diagnostic flags
that explain extreme or suspicious values.

Designed to investigate the extreme daily Sharpe values (SPY 1d: -163.35,
QQQ 1d: -134.92) observed in the first cached real-data backtest run.

**No Alpaca SDK is imported.**
**No network library is imported.**
**No credentials are read.**
**No environment variable access.**
**No broker calls.**
**No execution layer imported.**
**No orders are placed.**
**No raw equity curve values are emitted in output.**
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.metrics import bars_per_year_for_interval

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# std values at or below this are treated as effectively zero.
# Machine-epsilon floating-point noise (e.g. 2.7e-20) from np.diff on a
# perfectly flat equity curve can produce a non-zero but meaningless std;
# 1e-14 is well above that noise floor and well below any real variance.
_ZERO_STD_THRESHOLD: float = 1e-14

# Per-bar std of period returns below this triggers low_variance_warning.
# Legacy threshold — catches near-constant equity curves with sub-microscopic
# variance (e.g. std ≈ 1e-8).
_LOW_VARIANCE_THRESHOLD: float = 1e-6

# Annualized volatility below this also triggers low_variance_warning.
# Real-world market volatility is typically 15–30% annualized; values below
# 0.1% (0.001) indicate the equity curve is so flat that the Sharpe ratio is
# dominated by the sign of the mean excess return, not genuine risk-adjusted
# performance.  This catches the SPY/QQQ 1d real-data cases where
# annualized_vol ≈ 0.0003 produced |Sharpe| > 100 (diagnostic PASS without
# warning under the old per-bar threshold alone).
_LOW_ANNUALIZED_VOL_THRESHOLD: float = 0.001  # 0.1 %


# ---------------------------------------------------------------------------
# diagnose_sharpe — main entry point
# ---------------------------------------------------------------------------


def diagnose_sharpe(
    equity_curve: pd.DataFrame | pd.Series,
    interval: str,
    *,
    risk_free_rate: float = 0.05,
    low_variance_threshold: float = _LOW_VARIANCE_THRESHOLD,
) -> dict[str, Any]:
    """Recompute Sharpe ratio and return diagnostic flags.

    Accepts the same equity_curve format as compute_metrics(): a DataFrame
    with an ``equity`` column, or a Series of equity values.  Uses the same
    bar-return formula (np.diff / eq[:-1]) and annualisation constant
    (bars_per_year_for_interval).

    Parameters
    ----------
    equity_curve:
        DataFrame with an ``equity`` column, or a Series of equity values.
    interval:
        Bar interval string (e.g. ``"1d"``, ``"60m"``).  Must be a valid
        key for ``bars_per_year_for_interval``.
    risk_free_rate:
        Annualised risk-free rate (default 0.05 = 5 %).
    low_variance_threshold:
        Per-bar std of period returns below this value triggers
        ``low_variance_warning`` (default 1e-6).  Note that
        ``low_variance_warning`` is also triggered when
        ``annualized_volatility < _LOW_ANNUALIZED_VOL_THRESHOLD`` (0.1 %),
        regardless of this parameter.

    Returns
    -------
    dict
        Diagnostic result.  All numeric fields are statistical aggregates;
        no raw equity values are present in the output.

        Key fields:

        ``result``
            ``"PASS"`` if diagnostics ran to completion;
            ``"BLOCKED"`` if a fatal input problem was detected.
        ``blocker``
            Human-readable reason if ``result == "BLOCKED"``, else ``None``.
        ``zero_std_detected``
            ``True`` if std of period returns is exactly zero or not finite.
            When ``True``, the Sharpe formula produces +/-inf or NaN; the
            returned ``sharpe_ratio_recomputed`` is 0.0 and ``result`` is
            ``"BLOCKED"``.
        ``low_variance_warning``
            ``True`` if either (a) per-bar std is non-zero but below
            ``low_variance_threshold``, or (b) ``annualized_volatility`` is
            below ``_LOW_ANNUALIZED_VOL_THRESHOLD`` (0.1 %).  The Sharpe is
            mathematically valid but may be inflated by near-zero variance.
            ``result`` is still ``"PASS"``.
        ``finite_values_only``
            ``True`` if all equity values are finite (no NaN or inf).
            ``False`` triggers ``result == "BLOCKED"``.
    """
    _safe_flags = {
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
    }

    # 1. Resolve interval
    try:
        bpy = bars_per_year_for_interval(interval)
    except ValueError:
        return {
            "result": "BLOCKED",
            "blocker": "invalid interval",
            "interval": None,
            "bars_per_year": None,
            "equity_points": 0,
            "return_points": 0,
            "mean_period_return": None,
            "std_period_return": None,
            "annualized_volatility": None,
            "sharpe_ratio_recomputed": None,
            "zero_std_detected": False,
            "low_variance_warning": False,
            "finite_values_only": False,
            **_safe_flags,
        }

    # 2. Extract equity Series
    if isinstance(equity_curve, pd.DataFrame):
        if "equity" not in equity_curve.columns:
            return {
                "result": "BLOCKED",
                "blocker": "equity column missing from DataFrame",
                "interval": interval,
                "bars_per_year": bpy,
                "equity_points": 0,
                "return_points": 0,
                "mean_period_return": None,
                "std_period_return": None,
                "annualized_volatility": None,
                "sharpe_ratio_recomputed": None,
                "zero_std_detected": False,
                "low_variance_warning": False,
                "finite_values_only": False,
                **_safe_flags,
            }
        eq_series: pd.Series = equity_curve["equity"]
    else:
        eq_series = equity_curve

    eq_vals = np.asarray(eq_series, dtype=float)
    equity_points = len(eq_vals)

    # 3. Check minimum length
    if equity_points < 2:
        return {
            "result": "BLOCKED",
            "blocker": "equity curve too short (need ≥ 2 points)",
            "interval": interval,
            "bars_per_year": bpy,
            "equity_points": equity_points,
            "return_points": 0,
            "mean_period_return": None,
            "std_period_return": None,
            "annualized_volatility": None,
            "sharpe_ratio_recomputed": None,
            "zero_std_detected": False,
            "low_variance_warning": False,
            "finite_values_only": False,
            **_safe_flags,
        }

    # 4. Check finite values
    finite_values_only = bool(np.all(np.isfinite(eq_vals)))
    if not finite_values_only:
        return {
            "result": "BLOCKED",
            "blocker": "non-finite values (NaN or inf) in equity curve",
            "interval": interval,
            "bars_per_year": bpy,
            "equity_points": equity_points,
            "return_points": 0,
            "mean_period_return": None,
            "std_period_return": None,
            "annualized_volatility": None,
            "sharpe_ratio_recomputed": None,
            "zero_std_detected": False,
            "low_variance_warning": False,
            "finite_values_only": False,
            **_safe_flags,
        }

    # 5. Compute bar-level period returns (same formula as compute_metrics)
    bar_rets = np.diff(eq_vals) / eq_vals[:-1]
    return_points = len(bar_rets)

    excess = bar_rets - (risk_free_rate / bpy)
    mean_excess = float(np.mean(excess))
    std_excess = float(np.std(excess, ddof=1)) if return_points > 1 else 0.0

    mean_period_return = float(np.mean(bar_rets))
    std_period_return = std_excess
    annualized_volatility = float(std_excess * math.sqrt(bpy))

    # 6. Detect zero / near-zero std.
    # Use a small threshold rather than exact equality: np.diff on a perfectly
    # flat array can produce floating-point noise (~2.7e-20) rather than 0.0.
    zero_std_detected = (std_excess <= _ZERO_STD_THRESHOLD) or not math.isfinite(std_excess)

    # low_variance_warning fires if EITHER:
    #   (a) per-bar std is below the legacy threshold (catches near-constant curves), OR
    #   (b) annualized volatility is below 0.1% (catches the SPY/QQQ daily cases where
    #       std is non-negligible per-bar but tiny after annualisation, producing |Sharpe|>100)
    low_variance_warning = not zero_std_detected and (
        std_excess < low_variance_threshold
        or annualized_volatility < _LOW_ANNUALIZED_VOL_THRESHOLD
    )

    # 7. Compute Sharpe (BLOCKED if zero std, to avoid misleading result)
    if zero_std_detected:
        return {
            "result": "BLOCKED",
            "blocker": "zero or non-finite std — Sharpe is undefined",
            "interval": interval,
            "bars_per_year": bpy,
            "equity_points": equity_points,
            "return_points": return_points,
            "mean_period_return": round(mean_period_return, 8),
            "std_period_return": 0.0,
            "annualized_volatility": 0.0,
            "sharpe_ratio_recomputed": None,
            "zero_std_detected": True,
            "low_variance_warning": False,
            "finite_values_only": True,
            **_safe_flags,
        }

    sharpe = float(mean_excess / std_excess * math.sqrt(bpy))

    return {
        "result": "PASS",
        "blocker": None,
        "interval": interval,
        "bars_per_year": bpy,
        "equity_points": equity_points,
        "return_points": return_points,
        "mean_period_return": round(mean_period_return, 8),
        "std_period_return": round(std_period_return, 8),
        "annualized_volatility": round(annualized_volatility, 8),
        "sharpe_ratio_recomputed": round(sharpe, 4),
        "zero_std_detected": False,
        "low_variance_warning": low_variance_warning,
        "finite_values_only": True,
        **_safe_flags,
    }
