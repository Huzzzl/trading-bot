"""
execution/paper_runner.py
--------------------------
Paper buy/submit execution runner extracted from src/main.py in PR R5.

Two-phase paper buy/submit flow:
  Phase 1 (preview): run backtest, generate candidate intents, write CSV artifacts.
  Phase 2 (submit): select one intent, validate safety constraints, submit one order,
                    write audit artifacts, append ledger row, reconcile.

This module is the library target for future automated runtime/state-machine code.
No Alpaca endpoint is called when a FakeBrokerAdapter or mock broker is injected.
No credentials are read. No orders are submitted in preview mode.
No live trading. No live gates are modified.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace as _dc_replace
from pathlib import Path
from typing import Any

from src.config.loader import AppConfig

logger = logging.getLogger(__name__)

_PAPER_SYMBOL     = "SPY"
_PAPER_ORDER_TYPE = "market"
_PAPER_MAX_QTY    = 1


@dataclass(frozen=True)
class PaperRunResult:
    """Deterministic result of run_paper_execution()."""
    result: str               # "PREVIEW_COMPLETE" | "SUBMIT_COMPLETE"
    blocker: str | None       # None (errors are raised as RuntimeError)
    mode: str                 # "preview" | "submit"
    preview_only: bool
    orders_submitted: int     # 0 in preview, 1 in submit
    intents_generated: int    # number of candidate intents from backtest
    ledger_rows_written: int  # 0 in preview, 1 in submit
    output_dir: str | None
    broker_calls_made: bool   # True if broker.preflight_check() was called
    credentials_read: bool    # always False — runner never reads credentials
    order_action_requested: bool  # True only if broker.submit_order() was called
    network_calls_made: bool      # always False — runner delegates network to broker


def run_paper_execution(
    config: AppConfig,
    *,
    output_dir: Path | str | None = None,
    _broker=None,         # injectable for testing (default: AlpacaBrokerAdapter)
    _data_provider=None,  # injectable for testing (default: CachedMarketDataProvider)
) -> PaperRunResult:
    """Run the two-phase paper buy/submit execution flow.

    Phase 1 (config.execution.paper_preview_only=True):
        Runs the strategy/risk backtest pipeline, writes paper_candidate_intents.csv,
        calls ReportGenerator, returns PREVIEW_COMPLETE. No order is submitted.

    Phase 2 (config.execution.paper_preview_only=False):
        Selects the intent matching paper_selected_client_order_id, applies all
        safety constraints (market hours, ledger, daily limits, kill switch, open
        orders), submits exactly one order, writes paper_intent_audit.csv, appends
        the ledger row, calls ReportGenerator, verifies reconciliation.

    Parameters
    ----------
    config : AppConfig
    output_dir : Path or str or None
        Directory for output artifacts.  File writes are skipped when None.
    _broker : broker adapter, optional
        Injected for testing.  Production path creates AlpacaBrokerAdapter.
    _data_provider : data provider, optional
        Injected for testing.  Production path creates CachedMarketDataProvider.

    Returns
    -------
    PaperRunResult

    Raises
    ------
    RuntimeError
        On any safety constraint violation, missing selection, reconciliation failure.
    """
    import pandas as _pd
    from zoneinfo import ZoneInfo as _ZoneInfo

    cfg = config
    _EASTERN = _ZoneInfo("America/New_York")

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    # --- Broker ---
    if _broker is None:
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        _broker = AlpacaBrokerAdapter()

    preflight = _broker.preflight_check(cfg.symbols, allow_existing_positions=True)
    logger.info(
        "Paper trading preflight passed: account_status=%s symbols=%s",
        preflight["account"].get("status"),
        preflight["symbols"],
    )

    # --- Backtest (intent generation) ---
    from src.backtest.backtest_runner import BacktestRunConfig, run_backtest as _run_backtest

    if _data_provider is None:
        from src.data.yahoo_provider import YahooDataProvider
        from src.data.cached_provider import CachedMarketDataProvider
        _raw = YahooDataProvider()
        _data_provider = (
            CachedMarketDataProvider(_raw, cache_dir=cfg.data.cache_dir)
            if cfg.data.cache_enabled
            else _raw
        )

    _run_cfg = BacktestRunConfig(
        strategy_name=cfg.strategy.name,
        strategy_params=dict(cfg.strategy.params),
        symbols=list(cfg.symbols),
        start_date=cfg.backtest.start_date,
        end_date=cfg.backtest.end_date,
        bar_interval=cfg.data.bar_interval,
        initial_capital=cfg.backtest.initial_capital,
        commission_per_share=cfg.backtest.commission_per_share,
        slippage_per_share=cfg.backtest.slippage_per_share,
        position_size_pct=float(cfg.strategy.params.get("position_size_pct", 0.95)),
        stop_execution=str(cfg.strategy.params.get("stop_execution", "bar_close")),
        force_exit_time=str(cfg.strategy.params.get("force_exit_time", "15:55")),
        max_open_positions=cfg.risk.max_open_positions,
        daily_loss_limit_pct=cfg.risk.daily_loss_limit_pct,
        daily_loss_action=cfg.risk.daily_loss_action,
    )
    results = _run_backtest(_run_cfg, data_provider=_data_provider)
    open_positions_count = 0
    candidate_intents = results.order_intents

    # Always write paper_candidate_intents.csv (before any submit decision)
    _cand_cols = [
        "client_order_id", "timestamp", "symbol", "side",
        "quantity", "order_type", "reason",
    ]
    _cand_rows = [
        {
            "client_order_id": i.client_order_id,
            "timestamp":       str(i.timestamp),
            "symbol":          i.symbol,
            "side":            i.side,
            "quantity":        i.quantity,
            "order_type":      i.order_type,
            "reason":          i.reason,
        }
        for i in candidate_intents
    ]
    _cand_path = output_dir / "paper_candidate_intents.csv" if output_dir else None
    if _cand_path is not None:
        _pd.DataFrame(_cand_rows, columns=_cand_cols).to_csv(_cand_path, index=False)
    logger.info(
        "Paper candidate intents written: %d intent(s)%s",
        len(candidate_intents),
        f" → {_cand_path}" if _cand_path else "",
    )

    # ---- Phase 1: preview-only ------------------------------------------------
    if cfg.execution.paper_preview_only:
        logger.info(
            "Paper preview-only mode: %d candidate intent(s) written%s. "
            "No order submitted. Set paper_preview_only=false and "
            "paper_selected_client_order_id=<id> to submit.",
            len(candidate_intents),
            f" to {_cand_path}" if _cand_path else "",
        )
        if output_dir is not None:
            from src.reporting.report_generator import ReportGenerator
            reporter = ReportGenerator(
                metrics=results.metrics,
                trades=results.trades,
                equity_curve=results.equity_curve,
                config=cfg,
                output_dir=output_dir,
                open_positions_count=open_positions_count,
                order_intents=candidate_intents,
                order_results=[],
            )
            reporter.generate_all()
        return PaperRunResult(
            result="PREVIEW_COMPLETE",
            blocker=None,
            mode="preview",
            preview_only=True,
            orders_submitted=0,
            intents_generated=len(candidate_intents),
            ledger_rows_written=0,
            output_dir=str(output_dir) if output_dir else None,
            broker_calls_made=True,
            credentials_read=False,
            order_action_requested=False,
            network_calls_made=False,
        )

    # ---- Phase 2: submit selected intent ----------------------------------------
    if cfg.execution.paper_require_market_hours:
        from src.execution.paper_market_hours_guard import assert_regular_market_hours
        assert_regular_market_hours()

    _selected_coid = cfg.execution.paper_selected_client_order_id
    if not _selected_coid or not str(_selected_coid).strip():
        raise RuntimeError(
            "Paper execution (non-preview mode): "
            "execution.paper_selected_client_order_id must be set to a non-empty "
            "client_order_id. Run in preview mode first to see candidates."
        )

    _matches = [i for i in candidate_intents if i.client_order_id == _selected_coid]
    if len(_matches) == 0:
        raise RuntimeError(
            f"Paper execution: no intent found with "
            f"client_order_id={_selected_coid!r}. "
            f"Available: {[i.client_order_id for i in candidate_intents]}"
        )
    if len(_matches) > 1:
        raise RuntimeError(
            f"Paper execution: {len(_matches)} intents found with "
            f"client_order_id={_selected_coid!r}. Expected exactly 1."
        )

    _selected_original = _matches[0]
    selected_intent    = _selected_original

    # Apply quantity override to the selected intent only
    _qty_override = cfg.execution.paper_order_quantity_override
    if _qty_override is not None:
        if _qty_override <= 0:
            raise RuntimeError(
                f"execution.paper_order_quantity_override must be > 0, "
                f"got {_qty_override}."
            )
        if _qty_override > 1:
            raise RuntimeError(
                f"execution.paper_order_quantity_override must be <= 1, "
                f"got {_qty_override}. Only 1.0 is supported in this implementation."
            )
        if _qty_override != 1.0:
            raise RuntimeError(
                f"execution.paper_order_quantity_override must be exactly 1.0, "
                f"got {_qty_override}. Only 1.0 is supported in this implementation."
            )
        _new_meta = dict(_selected_original.metadata)
        _new_meta["paper_quantity_override"] = True
        _new_meta["original_quantity"]       = _selected_original.quantity
        selected_intent = _dc_replace(
            _selected_original, quantity=_qty_override, metadata=_new_meta
        )
        logger.info(
            "Paper quantity override: client_order_id=%s original_qty=%s -> submitted_qty=%s",
            selected_intent.client_order_id,
            _selected_original.quantity,
            _qty_override,
        )

    # Safety validation on the selected intent (fail closed — all checks before submit)
    _violations: list[str] = []
    if selected_intent.symbol != _PAPER_SYMBOL:
        _violations.append(
            f"symbol={selected_intent.symbol!r} (must be {_PAPER_SYMBOL!r})"
        )
    if selected_intent.order_type != _PAPER_ORDER_TYPE:
        _violations.append(
            f"order_type={selected_intent.order_type!r} (must be {_PAPER_ORDER_TYPE!r})"
        )
    if selected_intent.quantity > _PAPER_MAX_QTY:
        _violations.append(
            f"quantity={selected_intent.quantity} (must be <= {_PAPER_MAX_QTY})"
        )
    if not selected_intent.client_order_id or not str(selected_intent.client_order_id).strip():
        _violations.append("client_order_id is missing or blank")
    if _violations:
        raise RuntimeError(
            f"Paper safety constraint violated for selected intent "
            f"{selected_intent.client_order_id!r}: " + "; ".join(_violations)
        )

    # Position safety: block a buy if an existing position already exists
    if selected_intent.side == "buy":
        _existing_pos = preflight["positions"].get(selected_intent.symbol.upper())
        if _existing_pos and (_existing_pos.get("qty") or 0) != 0:
            raise RuntimeError(
                f"Paper safety: existing {selected_intent.symbol} position detected "
                f"(qty={_existing_pos.get('qty')}). "
                f"Close it in the Alpaca dashboard before submitting another buy. "
                f"No order was submitted."
            )

    from src.execution.paper_ledger import assert_client_order_id_unused, append_ledger_row
    _ledger_path = Path(cfg.execution.paper_ledger_path)
    assert_client_order_id_unused(_ledger_path, selected_intent.client_order_id)

    from src.execution.paper_daily_limits import assert_within_daily_limits
    _today = _pd.Timestamp.now(tz=_EASTERN).date()
    assert_within_daily_limits(
        ledger_path=_ledger_path,
        trading_date=_today,
        flow="buy_submit",
        intent_quantity=selected_intent.quantity,
        max_orders=cfg.execution.paper_daily_max_orders,
        max_buy_orders=cfg.execution.paper_daily_max_buy_orders,
        max_close_orders=cfg.execution.paper_daily_max_close_orders,
        max_notional=cfg.execution.paper_daily_max_notional,
        intent_price=(
            selected_intent.metadata.get("entry_price")
            if cfg.execution.paper_daily_max_notional is not None
            else None
        ),
    )

    from src.execution.paper_kill_switch import assert_kill_switch_disabled
    assert_kill_switch_disabled(cfg.execution.paper_kill_switch_enabled)

    if cfg.execution.paper_block_if_open_orders:
        from src.execution.paper_open_order_guard import assert_no_open_orders_for_symbol
        assert_no_open_orders_for_symbol(_broker._get_client, selected_intent.symbol)

    logger.info(
        "Paper execution: submitting intent client_order_id=%s symbol=%s side=%s qty=%s",
        selected_intent.client_order_id,
        selected_intent.symbol,
        selected_intent.side,
        selected_intent.quantity,
    )
    order_result = _broker.submit_order(selected_intent)
    if not order_result.client_order_id:
        raise RuntimeError(
            f"Paper execution: OrderResult for intent "
            f"{selected_intent.client_order_id!r} has no client_order_id. Aborting."
        )
    logger.info(
        "Paper execution: order_id=%s status=%s client_order_id=%s",
        order_result.order_id,
        order_result.status,
        order_result.client_order_id,
    )

    # Optional poll (read-only, never cancels)
    if cfg.execution.paper_poll_order_status:
        from src.execution.paper_order_poller import poll_order_status
        _poll_result = poll_order_status(
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
        "run_id":          output_dir.name if output_dir else "unknown",
        "flow":            "buy_submit",
        "client_order_id": order_result.client_order_id,
        "alpaca_order_id": order_result.order_id,
        "symbol":          order_result.symbol,
        "side":            order_result.side,
        "quantity":        order_result.quantity,
        "status":          order_result.status,
        "submitted_at":    str(order_result.submitted_at),
        "output_dir":      str(output_dir) if output_dir else "",
        "notes":           "",
    })
    logger.info("Paper execution: ledger row appended to %s", _ledger_path)

    # Write paper_intent_audit.csv for the selected/submitted intent
    if output_dir is not None:
        _audit_cols = [
            "client_order_id", "symbol", "side",
            "original_quantity", "submitted_quantity", "override_applied",
        ]
        _audit_df = _pd.DataFrame([{
            "client_order_id":    selected_intent.client_order_id,
            "symbol":             selected_intent.symbol,
            "side":               selected_intent.side,
            "original_quantity":  _selected_original.quantity,
            "submitted_quantity": selected_intent.quantity,
            "override_applied":   _qty_override is not None,
        }], columns=_audit_cols)
        _audit_path = output_dir / "paper_intent_audit.csv"
        _audit_df.to_csv(_audit_path, index=False)
        logger.info("Paper intent audit written to %s", _audit_path)

    if output_dir is not None:
        from src.reporting.report_generator import ReportGenerator
        reporter = ReportGenerator(
            metrics=results.metrics,
            trades=results.trades,
            equity_curve=results.equity_curve,
            config=cfg,
            output_dir=output_dir,
            open_positions_count=open_positions_count,
            order_intents=[selected_intent],
            order_results=order_results,
        )
        reporter.generate_all()

        recon_path = output_dir / "order_reconciliation.json"
        if not recon_path.exists():
            raise RuntimeError(
                "Paper execution: order_reconciliation.json was not written. "
                "This is an internal error. Aborting."
            )
        recon = json.loads(recon_path.read_text())

        if len(order_results) != 1:
            raise RuntimeError(
                f"Paper execution: expected 1 result, got {len(order_results)}. "
                "Check order_reconciliation.json for details."
            )

        if recon.get("overall_status") not in ("PASS", "N/A"):
            raise RuntimeError(
                f"Paper execution reconciliation failed: {recon.get('overall_status')}. "
                "Check order_reconciliation.json for details."
            )

    logger.info(
        "Paper execution complete.%s",
        f" Artifacts written to {output_dir}" if output_dir else "",
    )

    return PaperRunResult(
        result="SUBMIT_COMPLETE",
        blocker=None,
        mode="submit",
        preview_only=False,
        orders_submitted=1,
        intents_generated=len(candidate_intents),
        ledger_rows_written=1,
        output_dir=str(output_dir) if output_dir else None,
        broker_calls_made=True,
        credentials_read=False,
        order_action_requested=True,
        network_calls_made=False,
    )
