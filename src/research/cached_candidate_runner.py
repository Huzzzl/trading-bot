"""
research/cached_candidate_runner.py
-------------------------------------
S3: Wire candidate evaluator to cached backtest path.

Evaluates CandidateSpec instances against locally cached bar data by
injecting an offline data loader and backtest runner into the candidate
evaluator. No network calls, no broker calls, no credentials, no live or
paper trading actions.

Partial wiring (S3 scope):
  StrategyFamily.TREND_BREAKOUT → "trend_following" strategy
  All other strategy families → BLOCKED("strategy family not yet wired: {family}")

Cache miss policy:
  If no matching .parquet or .csv file exists under cache_dir for a
  (symbol, interval) pair → BLOCKED("no cache file found for {symbol}/{interval}")

Safety flags:
  broker_calls_made, credentials_read, network_calls_made,
  order_action_requested, live_trading_allowed — all always False.

This module never imports from broker adapters, environment variables,
network libraries, or execution layers. No order submission. No live gates.
"""

from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.backtest.backtest_runner import BacktestRunConfig, run_backtest
from src.backtest.trade_diagnostics import trade_summary_diagnostics
from src.data.base import BaseDataProvider
from src.research.candidate_evaluator import (
    REQUIRED_METRIC_KEYS,
    CandidateEvaluationResult,
    evaluate_candidate,
)
from src.research.candidate_universe import (
    CandidateSpec,
    StrategyFamily,
    build_initial_candidate_universe,
)

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = "data/cache"

# Daily bar intervals — force_exit_time must be None for these (daily-bar guard).
_DAILY_INTERVALS: frozenset[str] = frozenset({"1d", "1day", "daily"})

# S3 partial wiring: only TREND_BREAKOUT is connected to a real strategy.
_WIRED_FAMILIES: dict[StrategyFamily, str] = {
    StrategyFamily.TREND_BREAKOUT: "trend_following",
}

# Default trend-following parameters (same as cached_real_data_backtest_check).
_TREND_PARAMS: dict[str, Any] = {
    "fast_ema_period": 10,
    "slow_ema_period": 50,
    "atr_period": 14,
    "atr_stop_mult": 2.0,
    "volatility_lookback": 50,
    "breakout_lookback": 5,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CachedCandidateRunResult:
    """Immutable aggregate result of run_cached_candidate_evaluation().

    result is "PASS" (≥1 candidate passed), "BLOCKED" (all blocked, none
    evaluated), or "ERROR" (≥1 error, none passed).
    All safety flags are always False.
    """
    result: str
    blocker: str | None
    candidates_requested: int
    candidates_evaluated: int
    candidates_passed: int
    candidates_blocked: int
    candidates_error: int
    results: tuple[CandidateEvaluationResult, ...]
    broker_calls_made: bool      # always False
    credentials_read: bool       # always False
    network_calls_made: bool     # always False
    order_action_requested: bool # always False
    live_trading_allowed: bool   # always False


# ---------------------------------------------------------------------------
# Internal data-loading helpers
# ---------------------------------------------------------------------------

class _DataFrameProvider(BaseDataProvider):
    """In-memory BaseDataProvider backed by a pre-loaded DataFrame.

    Returned by fetch_bars regardless of symbol/start/end/interval arguments.
    Never makes any network calls.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str,
    ) -> pd.DataFrame:
        return self._df.copy()


def _find_cache_files(
    cache_dir: pathlib.Path,
    symbol: str,
    interval: str,
) -> list[pathlib.Path]:
    """Return cache files matching (symbol, interval), parquet before csv."""
    safe_iv = re.sub(r"[^\w\-]", "_", interval)
    parquets = sorted(cache_dir.glob(f"{symbol}_*_{safe_iv}.parquet"))
    csvs = sorted(cache_dir.glob(f"{symbol}_*_{safe_iv}.csv"))
    return parquets + csvs


def _load_cache_file(path: pathlib.Path) -> pd.DataFrame:
    """Load a .parquet or .csv cache file and ensure numeric OHLCV columns."""
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Metrics mapping
# ---------------------------------------------------------------------------

def _map_run_result_to_metrics(
    run_result: Any,
    candidate: CandidateSpec,
) -> dict[str, Any]:
    """Map BacktestRunResult + trade diagnostics to REQUIRED_METRIC_KEYS format.

    All percentage values from compute_metrics are converted to decimal.
    Fields not computable from the basic backtest are set to None.
    """
    raw = run_result.metrics
    trades = run_result.trades
    equity = run_result.equity_curve

    n: int = int(raw.get("num_trades") or 0)
    total_bars: int | None = len(equity) if (equity is not None and not equity.empty) else None

    tdiag = trade_summary_diagnostics(trades, total_bars=total_bars)

    # Time range for per-month metrics
    months: float | None = None
    if equity is not None and not equity.empty and len(equity) >= 2:
        days = (equity.index[-1] - equity.index[0]).days
        if days > 0:
            months = days / 30.44

    # Core metrics — convert from percentage to decimal
    ann_ret_pct = raw.get("annualized_return_pct") or 0.0
    cagr = ann_ret_pct / 100.0

    max_dd_pct = raw.get("max_drawdown_pct") or 0.0
    max_dd = max_dd_pct / 100.0

    sharpe = raw.get("sharpe_ratio")

    win_rate_pct = raw.get("win_rate_pct") or 0.0
    win_rate = win_rate_pct / 100.0

    # Derived: average monthly return from CAGR
    try:
        avg_monthly: float | None = (1.0 + cagr) ** (1.0 / 12.0) - 1.0
    except Exception:
        avg_monthly = None

    # Derived: calmar ratio = cagr / |max_drawdown|
    calmar: float | None = None
    if max_dd != 0.0:
        calmar = cagr / abs(max_dd)

    # Derived: trades per month
    trades_per_month: float | None = None
    if months is not None and months > 0 and n > 0:
        trades_per_month = n / months

    # Trade diagnostics — exposure is returned as percentage, convert to decimal
    exposure_raw = tdiag.get("exposure_pct")
    exposure_pct: float | None = (exposure_raw / 100.0) if exposure_raw is not None else None

    profit_factor = tdiag.get("profit_factor")

    avg_trade_raw = tdiag.get("avg_trade_return_pct")
    avg_trade: float | None = (avg_trade_raw / 100.0) if avg_trade_raw is not None else None

    avg_holding = tdiag.get("avg_holding_bars")

    # Exit reason frequencies
    reason_counts: dict[str, int] = tdiag.get("exit_reason_counts") or {}
    session_end_freq: float | None = None
    stop_loss_freq: float | None = None
    if n > 0:
        session_end_freq = reason_counts.get("session_end", 0) / n
        stop_loss_freq = reason_counts.get("stop_loss", 0) / n

    return {
        "cagr": cagr,
        "average_monthly_return": avg_monthly,
        "worst_month": None,          # not computable from current metrics
        "sharpe": sharpe,
        "sortino": None,              # not computable from current metrics
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "average_trade_return": avg_trade,
        "median_trade_return": None,  # not computable from current metrics
        "exposure_pct": exposure_pct,
        "trades_per_month": trades_per_month,
        "total_trades": n,
        "average_holding_bars": avg_holding,
        "stop_loss_frequency": stop_loss_freq,
        "session_end_frequency": session_end_freq,
        "slippage_bps": None,         # per-share config; bps depends on price
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_blocked_result(
    candidate: CandidateSpec,
    blocker: str,
) -> CandidateEvaluationResult:
    return CandidateEvaluationResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        interval=candidate.interval,
        strategy_family=candidate.strategy_family.value,
        result="BLOCKED",
        blocker=blocker,
        metrics={k: None for k in REQUIRED_METRIC_KEYS},
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )


def _build_real_providers(
    df: pd.DataFrame,
    strategy_name: str,
    interval: str,
):
    """Return (data_provider, backtest_runner) closures backed by df."""
    force_exit_time: str | None = None if interval in _DAILY_INTERVALS else "15:55"

    def data_provider(symbol: str, iv: str) -> pd.DataFrame:
        return df.copy()

    def backtest_runner(data: pd.DataFrame, candidate: CandidateSpec) -> dict:
        if not isinstance(data, pd.DataFrame) or len(data) == 0:
            raise ValueError("data is empty or not a DataFrame")
        start_date = data.index[0].date().isoformat()
        end_date = data.index[-1].date().isoformat()
        config = BacktestRunConfig(
            strategy_name=strategy_name,
            strategy_params=dict(_TREND_PARAMS),
            symbols=[candidate.symbol],
            start_date=start_date,
            end_date=end_date,
            bar_interval=candidate.interval,
            initial_capital=100_000.0,
            commission_per_share=0.005,
            slippage_per_share=0.010,
            position_size_pct=0.95,
            stop_execution="bar_close",
            force_exit_time=force_exit_time,
        )
        provider = _DataFrameProvider(data.copy())
        run_result = run_backtest(config, data_provider=provider)
        return _map_run_result_to_metrics(run_result, candidate)

    return data_provider, backtest_runner


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cached_candidate_evaluation(
    candidates: tuple[CandidateSpec, ...] | list[CandidateSpec] | None = None,
    *,
    cache_dir: str | pathlib.Path | None = None,
    max_candidates: int | None = None,
    _data_provider=None,
    _backtest_runner=None,
) -> CachedCandidateRunResult:
    """Evaluate candidates against locally cached market data.

    Parameters
    ----------
    candidates:
        Candidates to evaluate. Defaults to build_initial_candidate_universe().
    cache_dir:
        Directory containing .parquet or .csv cache files.
        Defaults to "data/cache".
    max_candidates:
        If given, evaluate only the first max_candidates candidates.
    _data_provider:
        Test injection: callable(symbol, interval) → data.
        When both _data_provider and _backtest_runner are non-None, the cache
        lookup is skipped entirely.
    _backtest_runner:
        Test injection: callable(data, candidate) → metrics dict.
        Only active when _data_provider is also non-None.

    Returns
    -------
    CachedCandidateRunResult
        result="PASS" when ≥1 candidate passed; "BLOCKED" when none ran;
        "ERROR" when ≥1 error and none passed.
        All safety flags are always False.

    Notes
    -----
    Strategy family wiring is partial (S3 scope):
      TREND_BREAKOUT → "trend_following".
      All other families → BLOCKED immediately.

    This function never reads credentials, contacts brokers, makes network
    calls, or submits orders.
    """
    if candidates is None:
        candidates = build_initial_candidate_universe()

    all_candidates = tuple(candidates)
    if max_candidates is not None:
        all_candidates = all_candidates[:max_candidates]

    cache_path = (
        pathlib.Path(cache_dir)
        if cache_dir is not None
        else pathlib.Path(_DEFAULT_CACHE_DIR)
    )

    all_results: list[CandidateEvaluationResult] = []

    for candidate in all_candidates:
        # 1. Strategy family pre-check (always, even with injected providers)
        strategy_name = _WIRED_FAMILIES.get(candidate.strategy_family)
        if strategy_name is None:
            all_results.append(
                _make_blocked_result(
                    candidate,
                    f"strategy family not yet wired: {candidate.strategy_family.value}",
                )
            )
            continue

        # 2. Both providers injected → bypass cache, delegate to evaluate_candidate
        if _data_provider is not None and _backtest_runner is not None:
            result = evaluate_candidate(
                candidate,
                data_provider=_data_provider,
                backtest_runner=_backtest_runner,
            )
            all_results.append(result)
            continue

        # 3. Cache file lookup
        cache_files = _find_cache_files(cache_path, candidate.symbol, candidate.interval)
        if not cache_files:
            all_results.append(
                _make_blocked_result(
                    candidate,
                    f"no cache file found for {candidate.symbol}/{candidate.interval}",
                )
            )
            continue

        # 4. Load the first matching cache file
        try:
            df = _load_cache_file(cache_files[0])
        except Exception as exc:
            logger.warning(
                "cached_candidate_runner: load error %s: %s",
                cache_files[0].name,
                exc,
            )
            all_results.append(
                _make_blocked_result(candidate, f"cache file load error: {exc}")
            )
            continue

        if len(df) == 0:
            all_results.append(
                _make_blocked_result(candidate, "cache file contains no rows")
            )
            continue

        # 5. Build real data/backtest providers from the loaded DataFrame
        real_dp, real_br = _build_real_providers(df, strategy_name, candidate.interval)

        result = evaluate_candidate(
            candidate,
            data_provider=real_dp,
            backtest_runner=real_br,
        )
        all_results.append(result)

    # Aggregate counts
    results_tuple = tuple(all_results)
    n_passed = sum(1 for r in results_tuple if r.result == "PASS")
    n_blocked = sum(1 for r in results_tuple if r.result == "BLOCKED")
    n_error = sum(1 for r in results_tuple if r.result == "ERROR")
    n_evaluated = n_passed + n_error

    if n_error > 0 and n_passed == 0:
        overall = "ERROR"
        blocker: str | None = f"{n_error} candidate(s) returned ERROR"
    elif n_passed > 0:
        overall = "PASS"
        blocker = None
    else:
        overall = "BLOCKED"
        blocker = "no candidates passed evaluation"

    return CachedCandidateRunResult(
        result=overall,
        blocker=blocker,
        candidates_requested=len(all_candidates),
        candidates_evaluated=n_evaluated,
        candidates_passed=n_passed,
        candidates_blocked=n_blocked,
        candidates_error=n_error,
        results=results_tuple,
        broker_calls_made=False,
        credentials_read=False,
        network_calls_made=False,
        order_action_requested=False,
        live_trading_allowed=False,
    )
