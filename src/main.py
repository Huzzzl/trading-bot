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

Disabled modes (not accepted by ``--mode``):
    ``--mode live`` and ``--mode paper`` are not valid CLI options.
    Paper execution is gated via ``execution.mode = paper`` in config (fail-closed).
    Live trading is not enabled. See docs/live_readiness_status.md.
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
from src.reporting.report_generator import ReportGenerator
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
                "Paper trading is disabled (execution.paper_trading_enabled is false). "
                "Set execution.paper_trading_enabled to true in config to reach the paper execution gate."
            )

        # ------------------------------------------------------------------
        # Close/flatten path — mutually exclusive with buy-submit path.
        # Only active when paper_close_positions_enabled=True.
        # ------------------------------------------------------------------
        if cfg.execution.paper_close_positions_enabled:
            from src.execution.paper_close_runner import run_paper_close
            run_paper_close(cfg, output_dir=output_dir)
            return

        from src.execution.paper_runner import run_paper_execution
        run_paper_execution(cfg, output_dir=output_dir)
        return

    if args.mode == "candidate-b":
        cfg = apply_candidate_b(cfg)
        logger.info("Candidate B overrides applied: symbols=%s  or_end=%s  trigger=%s  size=%.2f",
                    cfg.symbols,
                    cfg.strategy.params["opening_range_end"],
                    cfg.strategy.params["breakout_trigger"],
                    cfg.strategy.params["position_size_pct"])

    if args.mode in ("backtest", "candidate-b"):
        from src.backtest.backtest_runner import BacktestRunConfig, run_backtest

        run_config = BacktestRunConfig(
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
        raw = YahooDataProvider()
        data_provider = (
            CachedMarketDataProvider(raw, cache_dir=cfg.data.cache_dir)
            if cfg.data.cache_enabled
            else raw
        )
        result = run_backtest(run_config, data_provider=data_provider)

        equity_curve = result.equity_curve
        chart_path   = output_dir / "equity_curve.png"
        BacktestEngine.plot_equity_curve(equity_curve, output_path=chart_path)

        order_results = None
        if cfg.execution.dry_run_broker:
            from src.execution.fake_broker import FakeBrokerAdapter
            broker = FakeBrokerAdapter(fill_immediately=True)
            order_results = [broker.submit_order(oi) for oi in result.order_intents]
            logger.info("Dry-run broker: submitted %d intents → %d results", len(order_results), len(order_results))

        reporter = ReportGenerator(
            metrics=result.metrics,
            trades=result.trades,
            equity_curve=equity_curve,
            config=cfg,
            output_dir=output_dir,
            open_positions_count=0,
            order_intents=result.order_intents,
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
