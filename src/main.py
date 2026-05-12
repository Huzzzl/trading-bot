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

    logger.info("Loaded config: strategy=%s  symbols=%s  mode=%s",
                cfg.strategy.name, cfg.symbols, args.mode)

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

        reporter = ReportGenerator(
            metrics=results["metrics"],
            trades=results["trades"],
            equity_curve=equity_curve,
            config=cfg,
            output_dir=output_dir,
            open_positions_count=open_positions_count,
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
