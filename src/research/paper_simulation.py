"""
research/paper_simulation.py
-------------------------------------
S19: Pure offline paper simulation skeleton.

Simulates already-loaded bars in memory using an already-loaded, already-
validated paper config dict and injected deterministic providers.

This is NOT a paper trading runner. No file I/O, no network, no environment
variables, no broker/API calls, no credential access, no order action, no
paper/live trading approval.

Simulation PASS is an offline research finding only.
It does not approve paper or live trading.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from src.research.paper_config_validator import validate_paper_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STARTING_EQUITY: float = 100_000.0

_REQUIRED_BAR_FIELDS: tuple[str, ...] = (
    "timestamp",
    "interval",
    "open",
    "high",
    "low",
    "close",
)

_VALID_SIGNALS: frozenset[str] = frozenset({"ENTER_LONG", "EXIT", "HOLD"})

_SAFETY_FLAGS: dict[str, bool] = {
    "broker_calls_made": False,
    "credentials_read": False,
    "network_calls_made": False,
    "order_action_requested": False,
    "live_trading_allowed": False,
}

# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------


class PaperSimulationStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    BLOCKED_CONFIG = "BLOCKED_CONFIG"
    BLOCKED_SAFETY = "BLOCKED_SAFETY"
    BLOCKED_DATA = "BLOCKED_DATA"
    ERROR_SIMULATION = "ERROR_SIMULATION"


@dataclass(frozen=True)
class PaperSimulationResult:
    """Immutable result of run_paper_simulation().

    All safety flags are always False — the simulation makes no broker,
    credential, network, order, or live trading calls.
    Simulation PASS is an offline research finding only; it does not approve
    paper or live trading.
    """
    result: str
    blocker: str | None
    status: PaperSimulationStatus
    summary: dict
    trades: tuple
    equity_curve: tuple
    risk_limit_events: tuple
    broker_calls_made: bool
    credentials_read: bool
    network_calls_made: bool
    order_action_requested: bool
    live_trading_allowed: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blocked(
    status: PaperSimulationStatus,
    blocker: str,
    summary: dict | None = None,
) -> PaperSimulationResult:
    return PaperSimulationResult(
        result="BLOCKED",
        blocker=blocker,
        status=status,
        summary=summary or {},
        trades=(),
        equity_curve=(),
        risk_limit_events=(),
        **_SAFETY_FLAGS,
    )


def _error(blocker: str) -> PaperSimulationResult:
    return PaperSimulationResult(
        result="ERROR",
        blocker=blocker,
        status=PaperSimulationStatus.ERROR_SIMULATION,
        summary={},
        trades=(),
        equity_curve=(),
        risk_limit_events=(),
        **_SAFETY_FLAGS,
    )


def _finite_positive(v: Any) -> bool:
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f > 0.0
    except (TypeError, ValueError):
        return False


def _default_signal_provider(
    bar: dict,
    prior_bars: list[dict],
    config_dict: dict,
    state: dict,
) -> str:
    return "HOLD"


def _default_fill_model(
    bar: dict,
    side: str,
    slippage_bps: float,
) -> float:
    close = float(bar["close"])
    slip = slippage_bps / 10_000.0
    if side == "entry":
        return close * (1.0 + slip)
    return close * (1.0 - slip)


def _parse_date_prefix(ts: Any) -> str:
    """Extract YYYY-MM-DD prefix from a timestamp string."""
    s = str(ts)
    return s[:10] if len(s) >= 10 else s


def _filter_bars(
    bars: list[dict],
    start_date: str,
    end_date: str,
    interval: str,
) -> tuple[list[dict], str | None]:
    """Return bars within [start_date, end_date] matching interval; or blocker."""
    filtered = []
    for b in bars:
        ts = b.get("timestamp")
        bar_interval = b.get("interval")
        if bar_interval != interval:
            continue
        date_prefix = _parse_date_prefix(ts)
        if start_date <= date_prefix <= end_date:
            filtered.append(b)
    if not filtered:
        return [], f"no bars in range [{start_date}, {end_date}] with interval {interval!r}"
    return filtered, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_paper_simulation(
    config_dict: dict,
    bars: list[dict],
    *,
    start_date: str,
    end_date: str,
    _signal_provider: Callable | None = None,
    _fill_model: Callable | None = None,
    _config_validator: Callable | None = None,
) -> PaperSimulationResult:
    """Run a pure offline paper simulation.

    Parameters
    ----------
    config_dict:
        Already-loaded plain dict from a paper config artifact (PC/1.0).
        Must pass validate_paper_config() before bars are processed.
    bars:
        Pre-loaded list of bar dicts. No network fetch occurs here.
    start_date:
        ISO-8601 date string (YYYY-MM-DD) for simulation window start.
    end_date:
        ISO-8601 date string (YYYY-MM-DD) for simulation window end.
    _signal_provider:
        Injectable deterministic signal callable. Receives (bar, prior_bars,
        config_dict, state) and returns one of "ENTER_LONG", "EXIT", "HOLD".
        Defaults to a provider that always returns "HOLD" (no trades).
    _fill_model:
        Injectable fill price callable. Receives (bar, side, slippage_bps)
        and returns a fill price. Defaults to close ± slippage.
    _config_validator:
        Injectable config validator. Defaults to validate_paper_config from
        src.research.paper_config_validator.

    Returns
    -------
    PaperSimulationResult
        All safety flags are always False. Simulation PASS is an offline
        research finding only; it does not approve paper or live trading.

    Notes
    -----
    Pure function: same inputs -> same output.
    No file I/O, no network, no environment variables, no broker/API calls,
    no credential access, no order action, no paper/live trading approval.
    """
    validator = _config_validator if _config_validator is not None else validate_paper_config
    signal_fn = _signal_provider if _signal_provider is not None else _default_signal_provider
    fill_fn = _fill_model if _fill_model is not None else _default_fill_model

    # -----------------------------------------------------------------------
    # 1. Config gate
    # -----------------------------------------------------------------------
    try:
        val_result = validator(config_dict)
    except Exception as exc:  # noqa: BLE001
        return _blocked(
            PaperSimulationStatus.BLOCKED_CONFIG,
            f"config validator raised: {type(exc).__name__}",
        )

    safety_flag_names = (
        "broker_calls_made",
        "credentials_read",
        "network_calls_made",
        "order_action_requested",
        "live_trading_allowed",
    )
    if any(getattr(val_result, f, False) for f in safety_flag_names):
        return _blocked(
            PaperSimulationStatus.BLOCKED_SAFETY,
            "config validator returned a True safety flag",
        )

    if val_result.result != "PASS":
        return _blocked(
            PaperSimulationStatus.BLOCKED_CONFIG,
            f"config validation did not pass: {val_result.blocker}",
        )

    candidate_id: str = config_dict.get("candidate_id", "")
    run_id: str = config_dict.get("run_id", "")
    interval: str = config_dict.get("interval", "")
    slippage_bps: float = float(config_dict.get("slippage_bps_assumption", 0.0))
    commission_bps: float = float(config_dict.get("commission_bps_assumption", 0.0))
    max_notional: float = float(config_dict.get("max_notional_per_position", 0.0))
    max_fraction: float = float(config_dict.get("max_position_fraction", 0.0))
    max_daily_loss: float = float(config_dict.get("max_daily_loss", 0.0))
    max_drawdown_stop: float = float(config_dict.get("max_drawdown_stop", 0.0))
    max_orders_per_day: int = int(config_dict.get("max_orders_per_day", 0))
    min_cash_buffer: float = float(config_dict.get("min_cash_buffer", 0.0))

    # -----------------------------------------------------------------------
    # 2. Data gate — validate bars structure
    # -----------------------------------------------------------------------
    if not isinstance(bars, list) or len(bars) == 0:
        return _blocked(PaperSimulationStatus.BLOCKED_DATA, "bars must be a non-empty list")

    for i, bar in enumerate(bars):
        if not isinstance(bar, dict):
            return _blocked(
                PaperSimulationStatus.BLOCKED_DATA,
                f"bar at index {i} is not a dict",
            )
        for req_field in _REQUIRED_BAR_FIELDS:
            if req_field not in bar:
                return _blocked(
                    PaperSimulationStatus.BLOCKED_DATA,
                    f"bar at index {i} missing required field: {req_field!r}",
                )
        for price_field in ("open", "high", "low", "close"):
            if not _finite_positive(bar.get(price_field)):
                return _blocked(
                    PaperSimulationStatus.BLOCKED_DATA,
                    f"bar at index {i} has invalid {price_field!r}: {bar.get(price_field)!r}",
                )

    # -----------------------------------------------------------------------
    # 3. Filter bars by date range and interval
    # -----------------------------------------------------------------------
    # Check for any interval mismatches in the full bar list (any bar with
    # wrong interval signals a configuration/data mismatch)
    interval_mismatches = [b for b in bars if b.get("interval") != interval]
    if len(interval_mismatches) == len(bars):
        # All bars have wrong interval
        return _blocked(
            PaperSimulationStatus.BLOCKED_CONFIG,
            f"all bars have interval {bars[0].get('interval')!r}; "
            f"config requires {interval!r}",
        )

    sim_bars, filter_err = _filter_bars(bars, start_date, end_date, interval)
    if filter_err:
        return _blocked(PaperSimulationStatus.BLOCKED_DATA, filter_err)

    # -----------------------------------------------------------------------
    # 4. Run simulation
    # -----------------------------------------------------------------------
    equity = _STARTING_EQUITY
    peak_equity = _STARTING_EQUITY
    cash = _STARTING_EQUITY

    trades: list[dict] = []
    equity_curve: list[dict] = []
    risk_events: list[dict] = []

    # Position state
    in_position = False
    entry_bar_index: int = 0
    entry_price: float = 0.0
    shares: float = 0.0
    entry_notional: float = 0.0
    entry_commission: float = 0.0

    # Per-day tracking
    current_day: str = ""
    day_orders: int = 0
    day_start_equity: float = equity
    day_pnl: float = 0.0

    for bar_index, bar in enumerate(sim_bars):
        bar_date = _parse_date_prefix(bar.get("timestamp", ""))

        # Day boundary
        if bar_date != current_day:
            if current_day:
                equity_curve.append({
                    "date": current_day,
                    "simulated_equity": equity,
                    "daily_pnl": day_pnl,
                })
            current_day = bar_date
            day_orders = 0
            day_start_equity = equity
            day_pnl = 0.0

        prior_bars = sim_bars[:bar_index]
        state = {
            "in_position": in_position,
            "equity": equity,
            "cash": cash,
            "peak_equity": peak_equity,
        }

        # Get signal
        try:
            signal = signal_fn(bar, prior_bars, config_dict, state)
        except Exception as exc:  # noqa: BLE001
            return _error(f"signal provider raised: {type(exc).__name__}")

        if signal not in _VALID_SIGNALS:
            return _error(f"signal provider returned unsupported signal: {signal!r}")

        # --- Handle EXIT ---
        if signal == "EXIT" and in_position:
            try:
                exit_price = fill_fn(bar, "exit", slippage_bps)
            except Exception as exc:  # noqa: BLE001
                return _error(f"fill model raised on exit: {type(exc).__name__}")

            if not _finite_positive(exit_price):
                return _error(f"fill model returned invalid exit price: {exit_price!r}")

            exit_notional = shares * exit_price
            exit_commission = exit_notional * commission_bps / 10_000.0
            pnl = (exit_notional - exit_commission) - (entry_notional + entry_commission)

            trades.append({
                "entry_bar_index": entry_bar_index,
                "exit_bar_index": bar_index,
                "entry_price_assumption": entry_price,
                "exit_price_assumption": exit_price,
                "simulated_shares": shares,
                "simulated_pnl": pnl,
                "exit_reason": "signal_exit",
            })

            cash += exit_notional - exit_commission
            equity = cash
            peak_equity = max(peak_equity, equity)
            day_pnl += pnl
            in_position = False
            shares = 0.0
            entry_price = 0.0
            entry_notional = 0.0
            entry_commission = 0.0

        # --- Handle ENTER_LONG ---
        elif signal == "ENTER_LONG" and not in_position:
            # Risk checks before entry
            drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            daily_loss = day_start_equity - equity

            if drawdown >= max_drawdown_stop:
                risk_events.append({
                    "bar_index": bar_index,
                    "limit_type": "max_drawdown_stop",
                    "limit_value": max_drawdown_stop,
                })
            elif daily_loss >= max_daily_loss:
                risk_events.append({
                    "bar_index": bar_index,
                    "limit_type": "max_daily_loss",
                    "limit_value": max_daily_loss,
                })
            elif day_orders >= max_orders_per_day:
                risk_events.append({
                    "bar_index": bar_index,
                    "limit_type": "max_orders_per_day",
                    "limit_value": float(max_orders_per_day),
                })
            else:
                # Compute position size
                deployable = cash * (1.0 - min_cash_buffer)
                notional = min(deployable, max_notional, equity * max_fraction)
                if notional <= 0.0:
                    risk_events.append({
                        "bar_index": bar_index,
                        "limit_type": "min_cash_buffer",
                        "limit_value": min_cash_buffer,
                    })
                else:
                    try:
                        fill_price = fill_fn(bar, "entry", slippage_bps)
                    except Exception as exc:  # noqa: BLE001
                        return _error(f"fill model raised on entry: {type(exc).__name__}")

                    if not _finite_positive(fill_price):
                        return _error(
                            f"fill model returned invalid entry price: {fill_price!r}"
                        )

                    calc_shares = notional / fill_price
                    commission = notional * commission_bps / 10_000.0
                    cost = notional + commission

                    if cost > cash:
                        risk_events.append({
                            "bar_index": bar_index,
                            "limit_type": "min_cash_buffer",
                            "limit_value": min_cash_buffer,
                        })
                    else:
                        in_position = True
                        entry_bar_index = bar_index
                        entry_price = fill_price
                        shares = calc_shares
                        entry_notional = notional
                        entry_commission = commission
                        cash -= cost
                        day_orders += 1

    # End: close open position at last bar
    if in_position and sim_bars:
        last_bar = sim_bars[-1]
        try:
            exit_price = fill_fn(last_bar, "exit", slippage_bps)
        except Exception as exc:  # noqa: BLE001
            return _error(f"fill model raised on final exit: {type(exc).__name__}")

        if not _finite_positive(exit_price):
            return _error(f"fill model returned invalid final exit price: {exit_price!r}")

        exit_notional = shares * exit_price
        exit_commission = exit_notional * commission_bps / 10_000.0
        pnl = (exit_notional - exit_commission) - (entry_notional + entry_commission)

        trades.append({
            "entry_bar_index": entry_bar_index,
            "exit_bar_index": len(sim_bars) - 1,
            "entry_price_assumption": entry_price,
            "exit_price_assumption": exit_price,
            "simulated_shares": shares,
            "simulated_pnl": pnl,
            "exit_reason": "end_of_simulation",
        })

        cash += exit_notional - exit_commission
        equity = cash
        day_pnl += pnl
        in_position = False

    # Final day equity record
    if current_day:
        equity_curve.append({
            "date": current_day,
            "simulated_equity": equity,
            "daily_pnl": day_pnl,
        })

    # -----------------------------------------------------------------------
    # 5. Compute summary metrics
    # -----------------------------------------------------------------------
    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    win_rate_pct: float | None = None

    if sim_bars:
        total_return_pct = (equity - _STARTING_EQUITY) / _STARTING_EQUITY * 100.0

    if equity_curve:
        running_peak = _STARTING_EQUITY
        worst_dd = 0.0
        running_eq = _STARTING_EQUITY
        for record in equity_curve:
            running_eq = record["simulated_equity"]
            running_peak = max(running_peak, running_eq)
            dd = (running_peak - running_eq) / running_peak if running_peak > 0 else 0.0
            worst_dd = max(worst_dd, dd)
        max_drawdown_pct = -worst_dd * 100.0

    if trades:
        winners = sum(1 for t in trades if t["simulated_pnl"] > 0)
        win_rate_pct = winners / len(trades) * 100.0

    summary: dict = {
        "simulation_result": PaperSimulationStatus.PASS.value,
        "blocker": None,
        "candidate_id": candidate_id,
        "run_id": run_id,
        "start_date": start_date,
        "end_date": end_date,
        "bars_used": len(sim_bars),
        "total_simulated_trades": len(trades),
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "win_rate_pct": win_rate_pct,
        "risk_limit_events": len(risk_events),
        "broker_calls_made": False,
        "credentials_read": False,
        "network_calls_made": False,
        "order_action_requested": False,
        "live_trading_allowed": False,
    }

    return PaperSimulationResult(
        result="PASS",
        blocker=None,
        status=PaperSimulationStatus.PASS,
        summary=summary,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        risk_limit_events=tuple(risk_events),
        **_SAFETY_FLAGS,
    )
