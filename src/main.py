"""
main.py
-------
Application entry point.

Usage
-----
    # from the trading-bot/ directory
    python -m src.main

    # or with a custom config file
    python -m src.main --config path/to/settings.yaml

TODO (Alpaca integration):
  Add a ``--mode live`` CLI flag that swaps out YahooDataProvider for an
  AlpacaDataProvider and Portfolio for an AlpacaBrokerAdapter.  Everything
  else (strategy, risk manager) stays identical.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make sure ``src/`` is importable when running ``python src/main.py`` directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import BacktestEngine
from src.config.loader import load_config, AppConfig
from src.data.yahoo_provider import YahooDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout
from src.utils.logger import configure_logging, get_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Intraday Trading Bot — Backtest Mode")
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
        help="Directory for equity curve chart and trade log CSV",
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
    data_provider = YahooDataProvider()

    portfolio = Portfolio(
        initial_capital=cfg.backtest.initial_capital,
        commission_per_share=cfg.backtest.commission_per_share,
        slippage_per_share=cfg.backtest.slippage_per_share,
    )

    force_exit = cfg.strategy.params.get("force_exit_time", "15:55")
    risk_manager = RiskManager(force_exit_time=force_exit)

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
    )


def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    configure_logging(level=cfg.logging.level, fmt=cfg.logging.format)
    logger = get_logger(__name__)

    logger.info("Loaded config: strategy=%s  symbols=%s", cfg.strategy.name, cfg.symbols)

    engine = build_engine(cfg)
    results = engine.run()

    # ---- Save outputs ------------------------------------------------
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Equity curve chart
    equity_curve = results["equity_curve"]
    chart_path   = output_dir / "equity_curve.png"
    BacktestEngine.plot_equity_curve(equity_curve, output_path=chart_path)

    # Trade log CSV
    trade_log = BacktestEngine.trade_log(results["trades"])
    if not trade_log.empty:
        csv_path = output_dir / "trade_log.csv"
        trade_log.to_csv(csv_path)
        logger.info("Trade log saved to %s", csv_path)
    else:
        logger.info("No trades executed — trade log not written.")


if __name__ == "__main__":
    main()
