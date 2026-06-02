"""
execution/paper_close_runner.py
--------------------------------
Paper close/flatten execution runner extracted from src/main.py in PR R6.

Two-phase paper close/flatten flow:
  Phase A (preview): fetch current positions, generate SPY sell-market close candidates,
                     write paper_close_candidate_intents.csv, return without submitting.
  Phase B (submit): select one candidate, validate safety constraints, submit one order,
                    write audit artifacts, append ledger row, reconcile.

This module is the library target for future automated runtime/state-machine code.
No Alpaca endpoint is called when a FakeBrokerAdapter or mock broker is injected.
When the default broker path is used (no _broker injected), AlpacaBrokerAdapter is
created and preflight_check() reads credentials from env and makes network calls.
No orders are submitted in preview mode. No live trading. No live gates are modified.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.config.loader import AppConfig

logger = logging.getLogger(__name__)

_CLOSE_SYMBOL     = "SPY"
_CLOSE_SIDE       = "sell"
_CLOSE_ORDER_TYPE = "market"
_CLOSE_REASON     = "paper_close"
_CLOSE_MAX_QTY    = 1


@dataclass(frozen=True)
class PaperCloseRunResult:
    """Deterministic result of run_paper_close()."""
    result: str               # "PREVIEW_COMPLETE" | "SUBMIT_COMPLETE"
    blocker: str | None       # None (errors are raised as RuntimeError)
    mode: str                 # "preview" | "submit"
    preview_only: bool
    close_candidates_generated: int
    orders_submitted: int     # 0 in preview, 1 in submit
    ledger_rows_written: int  # 0 in preview, 1 in submit
    output_dir: str | None
    broker_calls_made: bool   # True if broker.preflight_check() was called
    credentials_read: bool    # True when default AlpacaBrokerAdapter is used (reads env creds)
    order_action_requested: bool  # True only if broker.submit_order() was called
    network_calls_made: bool      # True when default AlpacaBrokerAdapter is used (network I/O)


def run_paper_close(
    config: AppConfig,
    *,
    output_dir: Path | str,
    _broker=None,  # injectable for testing (default: AlpacaBrokerAdapter)
) -> PaperCloseRunResult:
    """Run the two-phase paper close/flatten flow.

    Phase A (config.execution.paper_close_preview_only=True, the default):
        Fetch current positions, generate SPY sell-market close candidates,
        write paper_close_candidate_intents.csv, return without submitting.

    Phase B (config.execution.paper_close_preview_only=False):
        Require paper_selected_close_client_order_id.  Select exactly that
        one candidate, apply paper_close_quantity_override if set (only 1.0
        accepted), validate all safety constraints, submit exactly one order,
        write audit artifacts, reconcile.

    Safety constraints (fail closed — raises before submit_order):
        - Symbol must be SPY.
        - Side must be sell (no buys in close flow, no shorting).
        - order_type must be market.
        - quantity must be <= 1 (or paper_close_quantity_override=1).
        - quantity must be <= current position qty (no shorting).
        - client_order_id must be non-empty.
        - Selected ID must match exactly one candidate.
        - cancel_order is never called.

    Parameters
    ----------
    config : AppConfig
    output_dir : Path or str
        Directory for output artifacts.
    _broker : broker adapter, optional
        Injected for testing.  Production path creates AlpacaBrokerAdapter.

    Returns
    -------
    PaperCloseRunResult

    Raises
    ------
    RuntimeError
        On any safety constraint violation, missing selection, reconciliation failure.
    """
    import json as _json
    import pandas as _pd
    from dataclasses import replace as _dc_replace
    from zoneinfo import ZoneInfo as _ZoneInfo
    from src.execution.paper_ledger import assert_client_order_id_unused, append_ledger_row
    from src.execution.order_intent import OrderIntent
    from src.reporting.report_generator import ReportGenerator

    cfg = config
    _EASTERN = _ZoneInfo("America/New_York")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Broker ---
    # Track whether caller injected a broker; default path reads credentials + makes network calls.
    _default_broker_path = _broker is None
    if _default_broker_path:
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        _broker = AlpacaBrokerAdapter()

    preflight = _broker.preflight_check(cfg.symbols, allow_existing_positions=True)
    logger.info(
        "Paper close preflight passed: account_status=%s symbols=%s",
        preflight["account"].get("status"),
        preflight["symbols"],
    )

    positions = preflight["positions"]
    now_ts     = _pd.Timestamp.now(tz=_EASTERN)
    # Close candidate IDs use date-only so the same ID is produced on preview
    # and submit runs within the same trading day, making selection stable.
    date_label = now_ts.strftime("%Y%m%d")

    # --- Generate close candidates (SPY only, sell market) ---
    close_candidates: list[OrderIntent] = []
    for sym, pos in positions.items():
        if sym.upper() != _CLOSE_SYMBOL:
            continue
        qty = float(pos.get("qty") or 0.0)
        if qty <= 0:
            continue
        intent = OrderIntent(
            symbol=_CLOSE_SYMBOL,
            side=_CLOSE_SIDE,
            quantity=qty,
            order_type=_CLOSE_ORDER_TYPE,
            reason=_CLOSE_REASON,
            timestamp=now_ts,
            client_order_id=f"BC-{date_label}-{_CLOSE_SYMBOL}-CLOSE",
            metadata={"current_position_qty": qty},
        )
        close_candidates.append(intent)

    # --- Always write paper_close_candidate_intents.csv ---
    _cand_cols = [
        "client_order_id", "timestamp", "symbol", "side",
        "quantity", "order_type", "reason", "current_position_qty",
    ]
    _cand_rows = [
        {
            "client_order_id":      c.client_order_id,
            "timestamp":            str(c.timestamp),
            "symbol":               c.symbol,
            "side":                 c.side,
            "quantity":             c.quantity,
            "order_type":           c.order_type,
            "reason":               c.reason,
            "current_position_qty": c.metadata.get("current_position_qty"),
        }
        for c in close_candidates
    ]
    _cand_path = output_dir / "paper_close_candidate_intents.csv"
    _pd.DataFrame(_cand_rows, columns=_cand_cols).to_csv(_cand_path, index=False)
    logger.info(
        "Paper close candidates written: %d candidate(s) -> %s",
        len(close_candidates), _cand_path,
    )

    # --- Phase A: close preview-only ---
    if cfg.execution.paper_close_preview_only:
        logger.info(
            "Paper close preview-only mode: %d candidate(s) written to %s. "
            "No order submitted. Set paper_close_preview_only=false and "
            "paper_selected_close_client_order_id=<id> to submit.",
            len(close_candidates), _cand_path,
        )
        return PaperCloseRunResult(
            result="PREVIEW_COMPLETE",
            blocker=None,
            mode="preview",
            preview_only=True,
            close_candidates_generated=len(close_candidates),
            orders_submitted=0,
            ledger_rows_written=0,
            output_dir=str(output_dir),
            broker_calls_made=True,
            credentials_read=_default_broker_path,
            order_action_requested=False,
            network_calls_made=_default_broker_path,
        )

    # --- Phase B: close submit ---
    if cfg.execution.paper_require_market_hours:
        from src.execution.paper_market_hours_guard import assert_regular_market_hours as _assert_mh_close
        _assert_mh_close()

    _selected_coid = cfg.execution.paper_selected_close_client_order_id
    if not _selected_coid or not str(_selected_coid).strip():
        raise RuntimeError(
            "Paper close (non-preview mode): "
            "execution.paper_selected_close_client_order_id must be set to a non-empty "
            "client_order_id. Run in close preview mode first to see candidates."
        )

    _matches = [c for c in close_candidates if c.client_order_id == _selected_coid]
    if len(_matches) == 0:
        raise RuntimeError(
            f"Paper close: no close candidate found with "
            f"client_order_id={_selected_coid!r}. "
            f"Available: {[c.client_order_id for c in close_candidates]}"
        )
    if len(_matches) > 1:
        raise RuntimeError(
            f"Paper close: {len(_matches)} candidates found with "
            f"client_order_id={_selected_coid!r}. Expected exactly 1."
        )

    _selected_original = _matches[0]
    selected_intent    = _selected_original
    current_pos_qty    = float(_selected_original.metadata.get("current_position_qty", 0.0))

    # Apply close quantity override (only 1.0 allowed).
    _qty_override = cfg.execution.paper_close_quantity_override
    if _qty_override is not None:
        if _qty_override <= 0:
            raise RuntimeError(
                f"execution.paper_close_quantity_override must be > 0, got {_qty_override}."
            )
        if _qty_override > 1:
            raise RuntimeError(
                f"execution.paper_close_quantity_override must be <= 1, "
                f"got {_qty_override}. Only 1.0 is supported in this implementation."
            )
        if _qty_override != 1.0:
            raise RuntimeError(
                f"execution.paper_close_quantity_override must be exactly 1.0, "
                f"got {_qty_override}. Only 1.0 is supported in this implementation."
            )
        _new_meta = dict(_selected_original.metadata)
        _new_meta["paper_close_quantity_override"] = True
        _new_meta["original_quantity"] = _selected_original.quantity
        selected_intent = _dc_replace(
            _selected_original, quantity=_qty_override, metadata=_new_meta
        )
        logger.info(
            "Paper close quantity override: client_order_id=%s "
            "original_qty=%s -> submitted_qty=%s",
            selected_intent.client_order_id,
            _selected_original.quantity,
            _qty_override,
        )

    # Safety validation (fail closed — all checks before submit_order).
    _violations: list[str] = []
    if selected_intent.symbol != _CLOSE_SYMBOL:
        _violations.append(
            f"symbol={selected_intent.symbol!r} (must be {_CLOSE_SYMBOL!r})"
        )
    if selected_intent.side != _CLOSE_SIDE:
        _violations.append(
            f"side={selected_intent.side!r} (must be {_CLOSE_SIDE!r} — no buys in close flow)"
        )
    if selected_intent.order_type != _CLOSE_ORDER_TYPE:
        _violations.append(
            f"order_type={selected_intent.order_type!r} (must be {_CLOSE_ORDER_TYPE!r})"
        )
    if selected_intent.quantity > _CLOSE_MAX_QTY:
        _violations.append(
            f"quantity={selected_intent.quantity} (must be <= {_CLOSE_MAX_QTY})"
        )
    if selected_intent.quantity > current_pos_qty:
        _violations.append(
            f"quantity={selected_intent.quantity} exceeds current position "
            f"qty={current_pos_qty} (no shorting)"
        )
    if not selected_intent.client_order_id or not str(selected_intent.client_order_id).strip():
        _violations.append("client_order_id is missing or blank")
    if _violations:
        raise RuntimeError(
            f"Paper close safety constraint violated for "
            f"{selected_intent.client_order_id!r}: " + "; ".join(_violations)
        )

    _ledger_path = Path(cfg.execution.paper_ledger_path)
    assert_client_order_id_unused(_ledger_path, selected_intent.client_order_id)

    from src.execution.paper_daily_limits import assert_within_daily_limits as _assert_daily_close
    _today_close = _pd.Timestamp.now(tz=_EASTERN).date()
    _assert_daily_close(
        ledger_path=_ledger_path,
        trading_date=_today_close,
        flow="close_submit",
        intent_quantity=selected_intent.quantity,
        max_orders=cfg.execution.paper_daily_max_orders,
        max_buy_orders=cfg.execution.paper_daily_max_buy_orders,
        max_close_orders=cfg.execution.paper_daily_max_close_orders,
        max_notional=cfg.execution.paper_daily_max_notional,
        intent_price=selected_intent.metadata.get("entry_price") if cfg.execution.paper_daily_max_notional is not None else None,
    )

    from src.execution.paper_kill_switch import assert_kill_switch_disabled as _assert_ks_close
    _assert_ks_close(cfg.execution.paper_kill_switch_enabled)

    if cfg.execution.paper_block_if_open_orders:
        from src.execution.paper_open_order_guard import assert_no_open_orders_for_symbol as _assert_no_open_close
        _assert_no_open_close(_broker._get_client, selected_intent.symbol)

    logger.info(
        "Paper close: submitting intent client_order_id=%s symbol=%s side=%s qty=%s",
        selected_intent.client_order_id,
        selected_intent.symbol,
        selected_intent.side,
        selected_intent.quantity,
    )
    order_result = _broker.submit_order(selected_intent)
    if not order_result.client_order_id:
        raise RuntimeError(
            f"Paper close: OrderResult for intent "
            f"{selected_intent.client_order_id!r} has no client_order_id. Aborting."
        )
    logger.info(
        "Paper close: order_id=%s status=%s client_order_id=%s",
        order_result.order_id,
        order_result.status,
        order_result.client_order_id,
    )

    # Optional: poll until terminal status (read-only, never cancels).
    if cfg.execution.paper_poll_order_status:
        from src.execution.paper_order_poller import poll_order_status as _poll_close
        _poll_result = _poll_close(
            client=_broker._get_client(),
            alpaca_order_id=order_result.order_id,
            client_order_id=order_result.client_order_id or selected_intent.client_order_id,
            initial_status=order_result.status,
            timeout_seconds=cfg.execution.paper_poll_timeout_seconds,
            interval_seconds=cfg.execution.paper_poll_interval_seconds,
            output_dir=output_dir,
        )
        if _poll_result["final_status"] != order_result.status:
            order_result = _dc_replace(order_result, status=_poll_result["final_status"])

    order_results: list = [order_result]
    append_ledger_row(_ledger_path, {
        "run_id":          output_dir.name,
        "flow":            "close_submit",
        "client_order_id": order_result.client_order_id,
        "alpaca_order_id": order_result.order_id,
        "symbol":          order_result.symbol,
        "side":            order_result.side,
        "quantity":        order_result.quantity,
        "status":          order_result.status,
        "submitted_at":    str(order_result.submitted_at),
        "output_dir":      str(output_dir),
        "notes":           f"close_submit current_position_qty={current_pos_qty}",
    })
    logger.info("Paper close: ledger row appended to %s", _ledger_path)

    # Write paper_close_intent_audit.csv.
    _audit_cols = [
        "client_order_id", "symbol", "side",
        "original_quantity", "submitted_quantity", "override_applied",
        "current_position_qty", "selected_close_client_order_id",
    ]
    _audit_df = _pd.DataFrame([{
        "client_order_id":                selected_intent.client_order_id,
        "symbol":                         selected_intent.symbol,
        "side":                           selected_intent.side,
        "original_quantity":              _selected_original.quantity,
        "submitted_quantity":             selected_intent.quantity,
        "override_applied":               _qty_override is not None,
        "current_position_qty":           current_pos_qty,
        "selected_close_client_order_id": _selected_coid,
    }], columns=_audit_cols)
    _audit_path = output_dir / "paper_close_intent_audit.csv"
    _audit_df.to_csv(_audit_path, index=False)
    logger.info("Paper close intent audit written to %s", _audit_path)

    # Write order_intents.csv, order_results.csv, order_reconciliation.json.
    reporter = ReportGenerator(
        metrics={},
        trades=[],
        equity_curve=_pd.DataFrame(),
        config=cfg,
        output_dir=output_dir,
        order_intents=[selected_intent],
        order_results=order_results,
    )
    reporter.generate_all()

    recon_path = output_dir / "order_reconciliation.json"
    if not recon_path.exists():
        raise RuntimeError(
            "Paper close: order_reconciliation.json was not written. "
            "This is an internal error. Aborting."
        )
    recon = _json.loads(recon_path.read_text())

    if recon.get("overall_status") not in ("PASS", "N/A"):
        raise RuntimeError(
            f"Paper close reconciliation failed: {recon.get('overall_status')}. "
            "Check order_reconciliation.json for details."
        )
    logger.info("Paper close complete. Artifacts written to %s", output_dir)

    return PaperCloseRunResult(
        result="SUBMIT_COMPLETE",
        blocker=None,
        mode="submit",
        preview_only=False,
        close_candidates_generated=len(close_candidates),
        orders_submitted=1,
        ledger_rows_written=1,
        output_dir=str(output_dir),
        broker_calls_made=True,
        credentials_read=_default_broker_path,
        order_action_requested=True,
        network_calls_made=_default_broker_path,
    )
