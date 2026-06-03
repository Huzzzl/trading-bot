"""
research/candidate_universe.py
--------------------------------
Offline strategy candidate universe builder (PR S2).

Defines the structured (symbol × interval × strategy family × holding horizon)
candidate matrix from the S1 design. No I/O, no broker access, no credentials,
no network access. All structures are frozen and deterministic.

Initial matrix follows S1 groups A–G as defined in
docs/strategy_candidate_universe_design.md. The universe is a research input
only; it does not approve any trade, paper execution, or live execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class StrategyFamily(str, Enum):
    TREND_BREAKOUT          = "trend_breakout"
    MEAN_REVERSION          = "mean_reversion"
    MOMENTUM_CONTINUATION   = "momentum_continuation"
    GAP_OVERNIGHT           = "gap_overnight"


class HoldingHorizon(str, Enum):
    INTRADAY          = "intraday"
    ONE_DAY           = "one_day"
    ONE_TO_TWO_DAYS   = "one_to_two_days"
    THREE_TO_FIVE_DAYS = "three_to_five_days"


@dataclass(frozen=True)
class CandidateSpec:
    """Immutable specification for a single evaluation candidate.

    candidate_id is a deterministic human-readable key composed from
    group, symbol, interval, strategy family, and holding horizon.
    """
    candidate_id: str
    group: str
    symbol: str
    interval: str
    strategy_family: StrategyFamily
    holding_horizon: HoldingHorizon
    enabled: bool = True


def _spec(
    group: str,
    symbol: str,
    interval: str,
    family: StrategyFamily,
    horizon: HoldingHorizon,
) -> CandidateSpec:
    horizon_short = {
        HoldingHorizon.INTRADAY:           "intraday",
        HoldingHorizon.ONE_DAY:            "1d",
        HoldingHorizon.ONE_TO_TWO_DAYS:    "1to2d",
        HoldingHorizon.THREE_TO_FIVE_DAYS: "3to5d",
    }[horizon]
    cid = f"{group}_{symbol}_{interval}_{family.value}_{horizon_short}"
    return CandidateSpec(
        candidate_id=cid,
        group=group,
        symbol=symbol,
        interval=interval,
        strategy_family=family,
        holding_horizon=horizon,
        enabled=True,
    )


def build_initial_candidate_universe() -> tuple[CandidateSpec, ...]:
    """Return the full initial candidate matrix as a deterministic immutable tuple.

    Groups follow S1 design:
      A — Broad ETF trend/breakout 60m + 1d
      B — Sector ETF trend/breakout 60m
      C — Broad ETF mean reversion 30m + 60m
      D — Mega-cap momentum 60m
      E — High-volume momentum 30m + 60m
      F — Gap/overnight SPY+QQQ 60m + 1d
      G — Swing comparison SPY+QQQ 1d (3–5 days)
    """
    specs: list[CandidateSpec] = []

    # Group A: broad ETFs × trend_breakout × 60m + 1d → one_to_two_days
    for sym in ("SPY", "QQQ", "IWM"):
        for iv in ("60m", "1d"):
            specs.append(_spec("A", sym, iv, StrategyFamily.TREND_BREAKOUT,
                               HoldingHorizon.ONE_TO_TWO_DAYS))

    # Group B: sector ETFs × trend_breakout × 60m → one_to_two_days
    for sym in ("XLK", "XLF", "XLE", "XLY"):
        specs.append(_spec("B", sym, "60m", StrategyFamily.TREND_BREAKOUT,
                           HoldingHorizon.ONE_TO_TWO_DAYS))

    # Group C: broad ETFs × mean_reversion × 30m (intraday) + 60m (one_day)
    for sym in ("SPY", "QQQ", "IWM"):
        specs.append(_spec("C", sym, "30m", StrategyFamily.MEAN_REVERSION,
                           HoldingHorizon.INTRADAY))
        specs.append(_spec("C", sym, "60m", StrategyFamily.MEAN_REVERSION,
                           HoldingHorizon.ONE_DAY))

    # Group D: mega-cap momentum × 60m → one_to_two_days
    for sym in ("AAPL", "MSFT", "NVDA", "AMZN"):
        specs.append(_spec("D", sym, "60m", StrategyFamily.MOMENTUM_CONTINUATION,
                           HoldingHorizon.ONE_TO_TWO_DAYS))

    # Group E: high-volume momentum × 30m (intraday) + 60m (one_day)
    for sym in ("TSLA", "META", "GOOGL"):
        specs.append(_spec("E", sym, "30m", StrategyFamily.MOMENTUM_CONTINUATION,
                           HoldingHorizon.INTRADAY))
        specs.append(_spec("E", sym, "60m", StrategyFamily.MOMENTUM_CONTINUATION,
                           HoldingHorizon.ONE_DAY))

    # Group F: gap/overnight SPY + QQQ × 60m + 1d → one_day
    for sym in ("SPY", "QQQ"):
        for iv in ("60m", "1d"):
            specs.append(_spec("F", sym, iv, StrategyFamily.GAP_OVERNIGHT,
                               HoldingHorizon.ONE_DAY))

    # Group G: swing comparison SPY + QQQ × 1d → three_to_five_days
    for sym in ("SPY", "QQQ"):
        specs.append(_spec("G", sym, "1d", StrategyFamily.TREND_BREAKOUT,
                           HoldingHorizon.THREE_TO_FIVE_DAYS))

    return tuple(specs)


def filter_candidates(
    candidates: Iterable[CandidateSpec],
    *,
    group: str | None = None,
    symbol: str | None = None,
    interval: str | None = None,
    strategy_family: StrategyFamily | None = None,
    holding_horizon: HoldingHorizon | None = None,
    enabled_only: bool = True,
) -> tuple[CandidateSpec, ...]:
    """Return a filtered subset of candidates matching all supplied criteria.

    All filters are AND-combined. Unspecified filters are not applied.
    enabled_only=True (default) excludes disabled candidates.
    """
    result = []
    for c in candidates:
        if enabled_only and not c.enabled:
            continue
        if group is not None and c.group != group:
            continue
        if symbol is not None and c.symbol != symbol:
            continue
        if interval is not None and c.interval != interval:
            continue
        if strategy_family is not None and c.strategy_family != strategy_family:
            continue
        if holding_horizon is not None and c.holding_horizon != holding_horizon:
            continue
        result.append(c)
    return tuple(result)
