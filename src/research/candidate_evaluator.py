"""
research/candidate_evaluator.py
---------------------------------
Offline strategy candidate evaluator skeleton (PR S2).

Evaluates a CandidateSpec against injected data and backtest providers.
All computation is offline and deterministic. No broker, credential, env,
or network access. No parameter optimization. No file I/O.

Default behavior (fail-closed):
  No data_provider or backtest_runner injected → returns BLOCKED with
  blocker "offline data provider/backtest runner not wired".

With injected providers:
  Calls data_provider(symbol, interval) → DataFrame-like,
  calls backtest_runner(data, candidate) → metrics dict.
  Returns PASS with aggregate metrics supplied by the backtest runner.

Safety flags:
  broker_calls_made, credentials_read, network_calls_made,
  order_action_requested, live_trading_allowed — all always False.

This module does not import from src.tools live manual gates, broker adapters,
or environment variables. No network I/O. No file I/O. No order submission.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.research.candidate_universe import CandidateSpec

logger = logging.getLogger(__name__)

_NO_PROVIDER_BLOCKER = "offline data provider/backtest runner not wired"

# Required metric keys — all must be present in CandidateEvaluationResult.metrics,
# with None as the value when data is unavailable.
REQUIRED_METRIC_KEYS: tuple[str, ...] = (
    "cagr",
    "average_monthly_return",
    "worst_month",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "win_rate",
    "profit_factor",
    "average_trade_return",
    "median_trade_return",
    "exposure_pct",
    "trades_per_month",
    "total_trades",
    "average_holding_bars",
    "stop_loss_frequency",
    "session_end_frequency",
    "slippage_bps",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateEvaluationResult:
    """Deterministic result of a single candidate evaluation.

    result is "PASS", "BLOCKED", or "ERROR".
    metrics contains all REQUIRED_METRIC_KEYS; values may be None.
    All safety flags are always False.
    """
    candidate_id: str
    symbol: str
    interval: str
    strategy_family: str
    result: str
    blocker: str | None
    metrics: dict[str, float | int | str | None]
    broker_calls_made: bool     # always False
    credentials_read: bool      # always False
    network_calls_made: bool    # always False
    order_action_requested: bool  # always False
    live_trading_allowed: bool  # always False


@dataclass(frozen=True)
class CandidateGateResult:
    """Result of apply_candidate_acceptance_gates()."""
    result: str
    blocker: str | None
    passed: bool


# ---------------------------------------------------------------------------
# Acceptance gate helper
# ---------------------------------------------------------------------------

def apply_candidate_acceptance_gates(
    metrics: Mapping[str, Any],
) -> CandidateGateResult:
    """Apply conservative acceptance gates to a metrics mapping.

    Gates (all must pass for PASS):
      1. total_trades must be ≥ 30 and not None.
      2. max_drawdown must not be None.
      3. average_monthly_return must not be None.
      4. session_end_frequency must not exceed 0.95 (session_end artifact).
      5. Low-volatility Sharpe artifact guard: if exposure_pct < 0.01 and
         average_monthly_return is not None and abs(average_monthly_return) < 0.001,
         block (data looks degenerate).

    This marks evaluation quality only; it does not approve paper or live trading.
    """
    total_trades = metrics.get("total_trades")
    if total_trades is None or total_trades < 30:
        return CandidateGateResult(
            result="BLOCKED",
            blocker=f"total_trades {total_trades!r} below minimum 30",
            passed=False,
        )

    max_dd = metrics.get("max_drawdown")
    if max_dd is None:
        return CandidateGateResult(
            result="BLOCKED",
            blocker="max_drawdown is None",
            passed=False,
        )

    avg_monthly = metrics.get("average_monthly_return")
    if avg_monthly is None:
        return CandidateGateResult(
            result="BLOCKED",
            blocker="average_monthly_return is None",
            passed=False,
        )

    session_end_freq = metrics.get("session_end_frequency")
    if session_end_freq is not None and session_end_freq > 0.95:
        return CandidateGateResult(
            result="BLOCKED",
            blocker=f"session_end_frequency {session_end_freq} > 0.95 (session_end artifact suspected)",
            passed=False,
        )

    exposure = metrics.get("exposure_pct")
    if (
        exposure is not None
        and exposure < 0.01
        and abs(avg_monthly) < 0.001
    ):
        return CandidateGateResult(
            result="BLOCKED",
            blocker="low-volatility Sharpe artifact suspected: exposure_pct < 0.01 with near-zero monthly return",
            passed=False,
        )

    return CandidateGateResult(result="PASS", blocker=None, passed=True)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def _empty_metrics() -> dict[str, float | int | str | None]:
    return {k: None for k in REQUIRED_METRIC_KEYS}


def _merge_metrics(
    base: dict[str, Any],
    supplied: Mapping[str, Any],
) -> dict[str, float | int | str | None]:
    """Merge supplied metrics over the base empty dict, keeping only known keys."""
    result = dict(base)
    for k in REQUIRED_METRIC_KEYS:
        if k in supplied:
            result[k] = supplied[k]
    return result


def evaluate_candidate(
    candidate: CandidateSpec,
    *,
    data_provider=None,
    backtest_runner=None,
) -> CandidateEvaluationResult:
    """Evaluate a single CandidateSpec offline.

    Parameters
    ----------
    candidate : CandidateSpec
        The candidate to evaluate.
    data_provider : callable, optional
        Called as data_provider(symbol, interval) → data.
        If None, returns BLOCKED.
    backtest_runner : callable, optional
        Called as backtest_runner(data, candidate) → Mapping of metric values.
        If None, returns BLOCKED.

    Returns
    -------
    CandidateEvaluationResult
        result="PASS" if evaluation succeeded; "BLOCKED" if providers missing
        or candidate disabled; "ERROR" if an exception occurred.
    """
    _base = {
        "candidate_id": candidate.candidate_id,
        "symbol": candidate.symbol,
        "interval": candidate.interval,
        "strategy_family": candidate.strategy_family.value,
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
        "live_trading_allowed": False,
    }

    if not candidate.enabled:
        return CandidateEvaluationResult(
            **_base,
            result="BLOCKED",
            blocker="candidate is disabled",
            metrics=_empty_metrics(),
        )

    if data_provider is None or backtest_runner is None:
        return CandidateEvaluationResult(
            **_base,
            result="BLOCKED",
            blocker=_NO_PROVIDER_BLOCKER,
            metrics=_empty_metrics(),
        )

    try:
        data = data_provider(candidate.symbol, candidate.interval)
        raw_metrics = backtest_runner(data, candidate)
        metrics = _merge_metrics(_empty_metrics(), raw_metrics)
    except Exception as exc:
        logger.error("evaluate_candidate error: %s %s", candidate.candidate_id, exc)
        return CandidateEvaluationResult(
            **_base,
            result="ERROR",
            blocker=str(exc),
            metrics=_empty_metrics(),
        )

    return CandidateEvaluationResult(
        **_base,
        result="PASS",
        blocker=None,
        metrics=metrics,
    )


def evaluate_candidates(
    candidates: tuple[CandidateSpec, ...] | list[CandidateSpec],
    *,
    data_provider=None,
    backtest_runner=None,
) -> tuple[CandidateEvaluationResult, ...]:
    """Evaluate each candidate in order and return results as an immutable tuple.

    Order is preserved. Each candidate is evaluated independently; one
    failure does not prevent others from being evaluated.
    """
    return tuple(
        evaluate_candidate(c, data_provider=data_provider, backtest_runner=backtest_runner)
        for c in candidates
    )
