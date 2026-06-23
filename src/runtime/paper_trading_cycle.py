"""Minimal automated paper trading cycle — S51.

One end-to-end cycle:
  read clock/account/positions/open_orders
  -> build PositionState/OpenOrderState
  -> call evaluate_signal()
  -> act once (at most one Alpaca paper market order)

SPY only, long only, market orders only. No retries, no scheduling,
no print/log side effects.
"""

from __future__ import annotations

import math
from typing import Any

from src.broker.alpaca_paper_adapter import (
    AlpacaPaperAdapter,
    AlpacaPaperAdapterError,
)
from src.strategy.signal_engine import (
    OpenOrderState,
    PositionState,
    SignalEngineConfig,
    evaluate_signal,
)

_SYMBOL = "SPY"
_MAX_FRACTION_CAP = 0.25


def _is_finite_positive(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return value > 0


def _blocked(blocker: str, **extra) -> dict[str, Any]:
    base = {
        "result": "BLOCKED",
        "action": "none",
        "signal": None,
        "reason_codes": [],
        "order": None,
        "blocker": blocker,
    }
    base.update(extra)
    return base


def _error(blocker: str) -> dict[str, Any]:
    return {
        "result": "ERROR",
        "action": "error",
        "signal": None,
        "reason_codes": [],
        "order": None,
        "blocker": blocker,
    }


def _signal_dict(signal_result, action: str, order: dict | None, result: str) -> dict[str, Any]:
    return {
        "result": result,
        "action": action,
        "signal": signal_result.signal,
        "reason_codes": list(signal_result.reason_codes),
        "order": order,
        "blocker": None,
    }


def _find_spy_position(positions: list[dict]) -> dict | None:
    if not isinstance(positions, list):
        return None
    spy = [p for p in positions if isinstance(p, dict) and p.get("symbol") == _SYMBOL]
    if not spy:
        return None
    return spy[0]


def _find_spy_open_order(open_orders: list[dict]) -> bool:
    if not isinstance(open_orders, list):
        return True  # defensive: treat malformed as "open order present"
    for o in open_orders:
        if isinstance(o, dict) and o.get("symbol") == _SYMBOL:
            return True
    return False


def run_paper_trading_cycle(
    *,
    adapter: AlpacaPaperAdapter,
    bars: list,
    signal_config: SignalEngineConfig,
    max_position_fraction: float = 0.10,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(bars, list) or not bars:
        return _blocked("bars must be a non-empty list")
    latest_close = getattr(bars[-1], "close", None)
    if not _is_finite_positive(latest_close):
        return _blocked("latest bar close must be finite and > 0")
    if not _is_finite_positive(max_position_fraction) or max_position_fraction > _MAX_FRACTION_CAP:
        return _blocked("max_position_fraction must be in (0, 0.25]")

    # 1. clock
    try:
        clock = adapter.get_clock()
    except AlpacaPaperAdapterError as exc:
        return _error(f"clock read failed: {exc}")
    if not isinstance(clock, dict) or "is_open" not in clock:
        return _blocked("malformed clock response")
    is_open = bool(clock["is_open"])
    market_session = "open" if is_open else "closed"

    # 2. account
    try:
        account = adapter.get_account()
    except AlpacaPaperAdapterError as exc:
        return _error(f"account read failed: {exc}")
    if not isinstance(account, dict):
        return _blocked("malformed account response")
    status = account.get("status")
    if not isinstance(status, str) or status.upper() != "ACTIVE":
        return _blocked("account status is not ACTIVE")
    equity = account.get("equity")
    if not _is_finite_positive(equity):
        return _blocked("account equity must be finite and > 0")

    # 3. positions
    try:
        positions = adapter.get_positions()
    except AlpacaPaperAdapterError as exc:
        return _error(f"positions read failed: {exc}")
    if not isinstance(positions, list):
        return _blocked("malformed positions response")

    spy_position = _find_spy_position(positions)
    has_position = False
    held_qty: float | None = None
    if spy_position is not None:
        side = spy_position.get("side")
        qty = spy_position.get("qty")
        if not isinstance(side, str) or side.lower() != "long":
            return _blocked("SPY position side is not long")
        if not _is_finite_positive(qty):
            return _blocked("SPY position qty is malformed")
        has_position = True
        held_qty = float(qty)

    # 4. open orders
    try:
        open_orders = adapter.get_open_orders()
    except AlpacaPaperAdapterError as exc:
        return _error(f"open orders read failed: {exc}")
    if not isinstance(open_orders, list):
        return _blocked("malformed open orders response")
    has_open_order = _find_spy_open_order(open_orders)

    # 5/6. signal
    position_state = PositionState(has_position=has_position, symbol=_SYMBOL if has_position else None)
    open_order_state = OpenOrderState(has_open_order=has_open_order)
    signal_result = evaluate_signal(
        bars=bars,
        position_state=position_state,
        open_order_state=open_order_state,
        market_session=market_session,
        config=signal_config,
    )
    sig = signal_result.signal

    # 7. act
    if sig in ("BLOCK", "HOLD"):
        return _signal_dict(signal_result, action="none", order=None, result="PASS")

    if sig == "BUY":
        if not is_open:
            return _blocked("market is closed", signal=sig,
                            reason_codes=list(signal_result.reason_codes))
        if has_position:
            return _blocked("SPY position already exists", signal=sig,
                            reason_codes=list(signal_result.reason_codes))
        if has_open_order:
            return _blocked("SPY open order already present", signal=sig,
                            reason_codes=list(signal_result.reason_codes))
        max_notional = float(equity) * float(max_position_fraction)
        qty = int(math.floor(max_notional / float(latest_close)))
        if qty < 1:
            return _blocked("calculated buy qty is below 1", signal=sig,
                            reason_codes=list(signal_result.reason_codes))
        try:
            order = adapter.submit_market_order(
                _SYMBOL, qty, "buy", client_order_id=client_order_id,
            )
        except AlpacaPaperAdapterError as exc:
            return _error(f"buy submission failed: {exc}")
        return _signal_dict(signal_result, action="buy_submitted", order=order, result="PASS")

    if sig == "SELL":
        if not has_position or held_qty is None or held_qty <= 0:
            return _blocked("no SPY position to sell", signal=sig,
                            reason_codes=list(signal_result.reason_codes))
        if has_open_order:
            return _blocked("SPY open order already present", signal=sig,
                            reason_codes=list(signal_result.reason_codes))
        try:
            order = adapter.submit_market_order(
                _SYMBOL, held_qty, "sell", client_order_id=client_order_id,
            )
        except AlpacaPaperAdapterError as exc:
            return _error(f"sell submission failed: {exc}")
        return _signal_dict(signal_result, action="sell_submitted", order=order, result="PASS")

    return _blocked(f"unexpected signal: {sig}", signal=sig,
                    reason_codes=list(signal_result.reason_codes))
