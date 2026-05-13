"""
main.py
-------
Application entry point.

Usage
-----
    # Single backtest with full reporting (default)
    python -m src.main

    # Candidate B backtest — QQQ, 09:45 OR end, close trigger, 50 % size
    python -m src.main --mode candidate-b

    # Parameter sweep — writes output/experiments.csv
    python -m src.main --mode sweep

    # Custom config file
    python -m src.main --config path/to/settings.yaml

TODO (Alpaca integration):
  Add a ``--mode live`` CLI flag that swaps out YahooDataProvider for an
  AlpacaDataProvider and Portfolio for an AlpacaBrokerAdapter.  Everything
  else (strategy, risk manager) stays identical.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

# Make sure ``src/`` is importable when running ``python src/main.py`` directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import BacktestEngine
from src.config.loader import load_config, AppConfig
from src.data.cached_provider import CachedMarketDataProvider
from src.data.yahoo_provider import YahooDataProvider
from src.portfolio.portfolio import Portfolio
from src.reporting.report_generator import ReportGenerator
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout
from src.utils.logger import configure_logging, get_logger


# ---------------------------------------------------------------------------
# Candidate B — parameter overrides from walk-forward validation
# ---------------------------------------------------------------------------

CANDIDATE_B_OVERRIDES: dict[str, Any] = {
    "symbols":           ["QQQ"],
    "opening_range_end": "09:45",
    "breakout_trigger":  "close",
    "position_size_pct": 0.50,
}


def apply_candidate_b(cfg: AppConfig) -> AppConfig:
    """Return a deep copy of *cfg* with Candidate B parameter overrides applied.

    Overrides: symbols=["QQQ"], opening_range_end="09:45",
    breakout_trigger="close", position_size_pct=0.50.
    The original *cfg* is never mutated.
    """
    cfg = copy.deepcopy(cfg)
    cfg.symbols = list(CANDIDATE_B_OVERRIDES["symbols"])
    cfg.strategy.params["opening_range_end"] = CANDIDATE_B_OVERRIDES["opening_range_end"]
    cfg.strategy.params["breakout_trigger"]  = CANDIDATE_B_OVERRIDES["breakout_trigger"]
    cfg.strategy.params["position_size_pct"] = CANDIDATE_B_OVERRIDES["position_size_pct"]
    return cfg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Intraday Trading Bot")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to settings.yaml (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Directory for output files",
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "candidate-b", "sweep", "walk-forward"],
        default="backtest",
        help=(
            "'backtest' runs a single backtest with full reporting; "
            "'candidate-b' runs Candidate B (QQQ, 09:45 OR end, close trigger, 50%% size); "
            "'sweep' runs a parameter grid search and writes experiments.csv; "
            "'walk-forward' tests candidate configs across multiple time windows"
        ),
    )
    return parser.parse_args()


def build_engine(cfg: AppConfig) -> BacktestEngine:
    """Wire all components together from *cfg* and return a ready engine.

    This factory function is the single place where concrete implementations
    are bound to interfaces.  Swapping providers or strategies requires only
    changing code here.
    """
    strategy = OpeningRangeBreakout(params=cfg.strategy.params)

    # TODO (Alpaca): replace with AlpacaDataProvider(api_key=…, secret=…)
    raw = YahooDataProvider()
    data_provider = (
        CachedMarketDataProvider(raw, cache_dir=cfg.data.cache_dir)
        if cfg.data.cache_enabled
        else raw
    )

    portfolio = Portfolio(
        initial_capital=cfg.backtest.initial_capital,
        commission_per_share=cfg.backtest.commission_per_share,
        slippage_per_share=cfg.backtest.slippage_per_share,
    )

    force_exit      = cfg.strategy.params.get("force_exit_time", "15:55")
    stop_execution  = cfg.strategy.params.get("stop_execution", "bar_close")
    risk_manager = RiskManager(
        force_exit_time=force_exit,
        max_open_positions=cfg.risk.max_open_positions,
        stop_execution=stop_execution,
        daily_loss_limit_pct=cfg.risk.daily_loss_limit_pct,
        daily_loss_action=cfg.risk.daily_loss_action,
    )

    return BacktestEngine(
        strategy=strategy,
        data_provider=data_provider,
        portfolio=portfolio,
        risk_manager=risk_manager,
        symbols=cfg.symbols,
        start_date=cfg.backtest.start_date,
        end_date=cfg.backtest.end_date,
        bar_interval=cfg.data.bar_interval,
        position_size_pct=cfg.strategy.params.get("position_size_pct", 0.95),
        stop_execution=stop_execution,
    )


def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    configure_logging(level=cfg.logging.level, fmt=cfg.logging.format)
    logger = get_logger(__name__)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Loaded config: strategy=%s  symbols=%s  cli_mode=%s  execution.mode=%s",
        cfg.strategy.name, cfg.symbols, args.mode, cfg.execution.mode,
    )
    logger.info(
        "Startup | execution.mode=%s  dry_run_broker=%s  symbols=%s  strategy=%s  output_dir=%s",
        cfg.execution.mode,
        cfg.execution.dry_run_broker,
        cfg.symbols,
        cfg.strategy.name,
        output_dir,
    )
    if cfg.execution.dry_run_broker:
        logger.warning(
            "Dry-run broker enabled: orders are submitted only to FakeBrokerAdapter."
        )

    if cfg.execution.mode == "paper":
        if not cfg.execution.paper_trading_enabled:
            raise NotImplementedError(
                "Paper trading is disabled. "
                "Set execution.paper_trading_enabled to true in config to enable preflight checks. "
                "Paper order execution is not yet wired."
            )

        # ------------------------------------------------------------------
        # Two-phase paper execution path
        #
        # Phase 1 — preview (paper_preview_only=True, the default):
        #   Run preflight + engine, write paper_candidate_intents.csv with all
        #   generated intents so the owner can review and choose one by
        #   client_order_id.  No order is submitted.
        #
        # Phase 2 — submit (paper_preview_only=False):
        #   Require paper_selected_client_order_id.  Select exactly that one
        #   intent, apply paper_order_quantity_override if set, validate all
        #   safety constraints, submit exactly that one order, write full audit
        #   artifacts, reconcile.
        #
        # Hard safety constraints (fail closed — any violation raises before
        # submit_order is called):
        #   - symbol must be "SPY"
        #   - order_type must be "market"
        #   - quantity must be <= 1 (or use paper_order_quantity_override=1)
        #   - client_order_id must be non-empty
        #   - selected intent must exist uniquely in candidate list
        # ------------------------------------------------------------------
        import json as _json
        import pandas as _pd
        from dataclasses import replace as _dc_replace
        from src.execution.alpaca_broker import AlpacaBrokerAdapter

        _PAPER_SYMBOL     = "SPY"
        _PAPER_ORDER_TYPE = "market"
        _PAPER_MAX_QTY    = 1

        broker = AlpacaBrokerAdapter()
        # allow_existing_positions=True: account safety is still enforced but an
        # existing target-symbol position does not abort preview runs.  Submit mode
        # enforces its own buy-blocking check below using preflight["positions"].
        preflight = broker.preflight_check(cfg.symbols, allow_existing_positions=True)
        logger.info(
            "Paper trading preflight passed: account_status=%s symbols=%s",
            preflight["account"].get("status"),
            preflight["symbols"],
        )

        # Generate all candidate intents via the strategy/risk pipeline.
        engine  = build_engine(cfg)
        results = engine.run()
        open_positions_count = len(engine._portfolio.positions)
        candidate_intents = results.get("order_intents", [])

        # Always write paper_candidate_intents.csv before any submission decision.
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
        _cand_path = output_dir / "paper_candidate_intents.csv"
        _pd.DataFrame(_cand_rows, columns=_cand_cols).to_csv(_cand_path, index=False)
        logger.info(
            "Paper candidate intents written: %d intent(s) → %s",
            len(candidate_intents), _cand_path,
        )

        # ---- Phase 1: preview-only ----------------------------------------
        if cfg.execution.paper_preview_only:
            logger.info(
                "Paper preview-only mode: %d candidate intent(s) written to %s. "
                "No order submitted. Set paper_preview_only=false and "
                "paper_selected_client_order_id=<id> to submit.",
                len(candidate_intents), _cand_path,
            )
            reporter = ReportGenerator(
                metrics=results["metrics"],
                trades=results["trades"],
                equity_curve=results["equity_curve"],
                config=cfg,
                output_dir=output_dir,
                open_positions_count=open_positions_count,
                order_intents=candidate_intents,
                order_results=[],
            )
            reporter.generate_all()
            return

        # ---- Phase 2: submit selected intent --------------------------------

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

        # Apply quantity override to the selected intent only.
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
                "Paper quantity override: client_order_id=%s "
                "original_qty=%s -> submitted_qty=%s",
                selected_intent.client_order_id,
                _selected_original.quantity,
                _qty_override,
            )

        # Safety validation on the selected intent (fail closed).
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

        # Position safety: block a buy if an existing position already exists for the
        # selected symbol.  Sell intents are not blocked (no automatic close logic).
        if selected_intent.side == "buy":
            _existing_pos = preflight["positions"].get(selected_intent.symbol.upper())
            if _existing_pos and (_existing_pos.get("qty") or 0) != 0:
                raise RuntimeError(
                    f"Paper safety: existing {selected_intent.symbol} position detected "
                    f"(qty={_existing_pos.get('qty')}). "
                    f"Close it in the Alpaca dashboard before submitting another buy. "
                    f"No order was submitted."
                )

        logger.info(
            "Paper execution: submitting intent client_order_id=%s symbol=%s side=%s qty=%s",
            selected_intent.client_order_id,
            selected_intent.symbol,
            selected_intent.side,
            selected_intent.quantity,
        )
        result = broker.submit_order(selected_intent)
        if not result.client_order_id:
            raise RuntimeError(
                f"Paper execution: OrderResult for intent "
                f"{selected_intent.client_order_id!r} has no client_order_id. Aborting."
            )
        order_results: list = [result]
        logger.info(
            "Paper execution: order_id=%s status=%s client_order_id=%s",
            result.order_id,
            result.status,
            result.client_order_id,
        )

        # Write paper_intent_audit.csv for the selected/submitted intent.
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

        # Reporter receives only the selected (submitted) intent so that
        # reconciliation compares exactly 1 intent vs 1 result.
        reporter = ReportGenerator(
            metrics=results["metrics"],
            trades=results["trades"],
            equity_curve=results["equity_curve"],
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
        recon = _json.loads(recon_path.read_text())

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
        logger.info("Paper execution complete. Artifacts written to %s", output_dir)
        return

    if args.mode == "candidate-b":
        cfg = apply_candidate_b(cfg)
        logger.info("Candidate B overrides applied: symbols=%s  or_end=%s  trigger=%s  size=%.2f",
                    cfg.symbols,
                    cfg.strategy.params["opening_range_end"],
                    cfg.strategy.params["breakout_trigger"],
                    cfg.strategy.params["position_size_pct"])

    if args.mode in ("backtest", "candidate-b"):
        engine = build_engine(cfg)
        results = engine.run()
        open_positions_count = len(engine._portfolio.positions)

        equity_curve = results["equity_curve"]
        chart_path   = output_dir / "equity_curve.png"
        BacktestEngine.plot_equity_curve(equity_curve, output_path=chart_path)

        order_results = None
        if cfg.execution.dry_run_broker:
            from src.execution.fake_broker import FakeBrokerAdapter
            broker = FakeBrokerAdapter(fill_immediately=True)
            order_results = [broker.submit_order(oi) for oi in results.get("order_intents", [])]
            logger.info("Dry-run broker: submitted %d intents → %d results", len(order_results), len(order_results))

        reporter = ReportGenerator(
            metrics=results["metrics"],
            trades=results["trades"],
            equity_curve=equity_curve,
            config=cfg,
            output_dir=output_dir,
            open_positions_count=open_positions_count,
            order_intents=results.get("order_intents", []),
            order_results=order_results,
        )
        reporter.generate_all()
        return

    if args.mode == "sweep":
        from src.experiments.sweep_runner import SweepRunner
        raw = YahooDataProvider()
        disk_provider = (
            CachedMarketDataProvider(raw, cache_dir=cfg.data.cache_dir)
            if cfg.data.cache_enabled
            else raw
        )
        sweeper = SweepRunner(base_config=cfg, output_dir=output_dir, data_provider=disk_provider)
        sweeper.run()
        return

    if args.mode == "walk-forward":
        from src.experiments.walk_forward_runner import WalkForwardRunner
        raw = YahooDataProvider()
        disk_provider = (
            CachedMarketDataProvider(raw, cache_dir=cfg.data.cache_dir)
            if cfg.data.cache_enabled
            else raw
        )
        runner = WalkForwardRunner(base_config=cfg, output_dir=output_dir, data_provider=disk_provider)
        runner.run()
        return


if __name__ == "__main__":
    main()
