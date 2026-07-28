"""
tools/shadow_strategy_report.py
================================
Read-only report over the S62 shadow-strategy state.

Never mutates the state directory — it does not initialize a
manifest, state, or events file. If either is missing or corrupt the
report exits with a JSON error and status code 2. Always emits
``validation_status.promotion_eligible = false``; S62 collects
forward evidence, it does not declare a winner.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from src.tools.run_shadow_strategy_cycle import (
    ShadowError,
    _DEFAULT_STATE_DIR,
    _EVENTS_FILENAME,
    _validate_event_log_readonly,
    load_manifest_readonly,
    load_state_readonly,
)

_BARS_PER_YEAR = 6.5 * 252  # 60m bars per trading year


def _sharpe(rs: Sequence[float]) -> float:
    if len(rs) < 2:
        return 0.0
    mean = sum(rs) / len(rs)
    var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(_BARS_PER_YEAR)


def _profit_factor(trades: Sequence[dict[str, Any]]) -> float:
    wins = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
    losses = abs(sum(t["pnl"] for t in trades if t.get("pnl", 0) <= 0))
    if losses > 0:
        return wins / losses
    if wins > 0:
        return float("inf")
    return 0.0


def _average_holding_bars(trades: Sequence[dict[str, Any]]) -> float:
    """Average across COMPLETED trades only, computed from the
    ``bars_held`` value persisted on each trade record. Open positions
    are excluded from the completed-trade average."""
    if not trades:
        return 0.0
    total = 0
    n = 0
    for t in trades:
        bh = t.get("bars_held")
        if isinstance(bh, int):
            total += bh
            n += 1
    return total / n if n > 0 else 0.0


def _largest_positive_trade_share(trades: Sequence[dict[str, Any]]) -> float | None:
    positives = [t["pnl"] for t in trades if t.get("pnl", 0) > 0]
    if not positives:
        return None
    return max(positives) / sum(positives)


def _candidate_report(
    cid: str, s: dict[str, Any], initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    trades = s.get("trades", [])
    returns = [float(r["r"]) for r in s.get("returns_series", [])]
    marked_equity = float(s.get("marked_equity", initial_equity))
    realized_equity = float(s.get("realized_equity", initial_equity))
    total_return = (marked_equity - initial_equity) / initial_equity
    realized_return = (realized_equity - initial_equity) / initial_equity
    unrealized_return = (marked_equity - realized_equity) / initial_equity

    total_wins = s.get("win_count", 0)
    total_losses = s.get("loss_count", 0)
    ttrades = total_wins + total_losses
    win_rate = (total_wins / ttrades) if ttrades > 0 else 0.0

    processed = s.get("processed_forward_bar_count", 0) or 0
    exposure = (s.get("cumulative_exposure_bars", 0) / processed) if processed > 0 else 0.0

    entry_count = (
        s.get("immediate_entry_count", 0)
        + s.get("delayed_entry_count", 0)
        + s.get("inherited_bullish_state_entry_count", 0)
    )

    return {
        "candidate_id": cid,
        "first_eligible_forward_bar_utc": s.get("first_forward_bar_utc"),
        "last_processed_forward_bar_utc": s.get("last_forward_bar_utc"),
        "processed_forward_bar_count": processed,
        "bullish_crossover_count": s.get("bullish_crossover_count", 0),
        "bullish_state_bar_count": s.get("bullish_state_bar_count", 0),
        "bullish_signal_count": s.get("bullish_signal_count", 0),
        "unique_bullish_episode_count": s.get("unique_bullish_episode_count", 0),
        "inherited_bullish_episode_count":
            s.get("inherited_bullish_episode_count", 0),
        "filter_evaluation_count": s.get("filter_evaluation_count", 0),
        "filter_allowed_count": s.get("filter_allowed_count", 0),
        "filter_blocked_count": s.get("filter_blocked_count", 0),
        "blocked_on_crossover_count": s.get("blocked_on_crossover_count", 0),
        "immediate_entry_count": s.get("immediate_entry_count", 0),
        "delayed_entry_count": s.get("delayed_entry_count", 0),
        "inherited_bullish_state_entry_count":
            s.get("inherited_bullish_state_entry_count", 0),
        "episodes_without_entry_count": s.get("episodes_without_entry_count", 0),
        "entry_count": entry_count,
        "exit_count": s.get("completed_trade_count", 0),
        "completed_trade_count": s.get("completed_trade_count", 0),
        "position_open": bool(s.get("position_open", False)),
        "total_return": total_return,
        "realized_return": realized_return,
        "unrealized_return": unrealized_return,
        "max_drawdown": s.get("max_drawdown", 0.0),
        "sharpe_ratio": _sharpe(returns),
        "win_rate": win_rate,
        "profit_factor": _profit_factor(trades),
        "exposure_time": exposure,
        "average_holding_bars": _average_holding_bars(trades),
        "current_marked_equity": marked_equity,
        "largest_positive_trade_share": _largest_positive_trade_share(trades),
    }


def _comparison_row(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {
        "return_delta":            a["total_return"] - b["total_return"],
        "drawdown_delta":          a["max_drawdown"] - b["max_drawdown"],
        "completed_trade_delta":   a["completed_trade_count"] - b["completed_trade_count"],
        "exposure_delta":          a["exposure_time"] - b["exposure_time"],
        "delayed_entry_count":     a["delayed_entry_count"],
        "blocked_on_crossover_count": a["blocked_on_crossover_count"],
    }


def _validation_status() -> dict[str, Any]:
    return {
        "retrospective_candidate_selection": True,
        "forward_data_only": True,
        "automatic_promotion_allowed": False,
        "promotion_eligible": False,
        "reason": "S62 collects forward shadow evidence only",
    }


def _diagnostic_flags(
    reports: dict[str, dict[str, Any]],
    processed_utc_earliest: str | None,
    processed_utc_latest: str | None,
) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "insufficient_calendar_weeks": None,
        "weeks_observed": None,
        "candidates_with_fewer_than_10_completed_trades": [],
        "candidates_with_fewer_than_3_bullish_episodes": [],
        "candidates_with_single_trade_pnl_share_above_60_percent": [],
    }
    if processed_utc_earliest and processed_utc_latest:
        try:
            a = datetime.fromisoformat(processed_utc_earliest.replace("Z", "+00:00"))
            b = datetime.fromisoformat(processed_utc_latest.replace("Z", "+00:00"))
            weeks = (b - a).days / 7.0
            flags["insufficient_calendar_weeks"] = weeks < 8
            flags["weeks_observed"] = weeks
        except ValueError:
            pass
    for cid, r in reports.items():
        if r["completed_trade_count"] < 10:
            flags["candidates_with_fewer_than_10_completed_trades"].append(cid)
        if r["unique_bullish_episode_count"] < 3:
            flags["candidates_with_fewer_than_3_bullish_episodes"].append(cid)
        share = r.get("largest_positive_trade_share")
        if share is not None and share > 0.60:
            flags["candidates_with_single_trade_pnl_share_above_60_percent"].append(cid)
    return flags


def build_report(
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    per_candidate: dict[str, dict[str, Any]] = {}
    for cdef in manifest["candidates"]:
        cid = cdef["id"]
        cstate = state["candidates"][cid]
        per_candidate[cid] = _candidate_report(cid, cstate)

    control = per_candidate.get("paper_control_20_100_none")
    unfiltered_10_20 = per_candidate.get("research_10_20_none")

    comparisons: dict[str, dict[str, dict[str, Any]]] = {
        "vs_paper_control_20_100_none": {},
        "vs_research_10_20_none": {},
    }
    filter_deltas: dict[str, dict[str, Any]] = {}
    for cid, rep in per_candidate.items():
        if control is not None and cid != "paper_control_20_100_none":
            comparisons["vs_paper_control_20_100_none"][cid] = _comparison_row(
                rep, control,
            )
        if unfiltered_10_20 is not None and cid != "research_10_20_none":
            comparisons["vs_research_10_20_none"][cid] = _comparison_row(
                rep, unfiltered_10_20,
            )
        if (
            unfiltered_10_20 is not None
            and cid.startswith("research_10_20_")
            and cid != "research_10_20_none"
        ):
            filter_deltas[cid] = _comparison_row(rep, unfiltered_10_20)

    earliest = min(
        (r["first_eligible_forward_bar_utc"] or "")
        for r in per_candidate.values()
    ) or None
    latest = max(
        (r["last_processed_forward_bar_utc"] or "")
        for r in per_candidate.values()
    ) or None

    return {
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": manifest["candidate_manifest_sha256"],
        "forward_cutoff_utc": manifest["forward_cutoff_utc"],
        "candidates": per_candidate,
        "comparisons": comparisons,
        "filter_vs_unfiltered_10_20": filter_deltas,
        "diagnostic_flags": _diagnostic_flags(per_candidate, earliest, latest),
        "validation_status": _validation_status(),
        "research_only": True,
        "automatic_strategy_promotion_allowed": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.shadow_strategy_report",
        description=(
            "Read-only report over the S62 shadow-strategy state."
        ),
    )
    p.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR),
                   help=f"Persistent state directory (default: {_DEFAULT_STATE_DIR}).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)
    try:
        manifest = load_manifest_readonly(state_dir)
        state = load_state_readonly(state_dir, manifest)
        _validate_event_log_readonly(
            state_dir / _EVENTS_FILENAME, state, manifest,
        )
    except ShadowError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    print(json.dumps(build_report(manifest, state), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
