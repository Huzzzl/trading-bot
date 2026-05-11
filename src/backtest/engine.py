"""
backtest/engine.py
------------------
Bar-by-bar backtest engine.

Design principles
-----------------
* **No look-ahead bias**: each call to ``strategy.generate_signal`` receives
  only bars up to *and including* the current bar.
* **Modular**: the engine is unaware of strategy internals; it only calls the
  ``BaseStrategy`` interface.
* **Single pass**: we iterate over a merged, time-sorted multi-symbol bar
  index once, which keeps memory usage low.

TODO (Alpaca integration):
  Replace the inner ``_process_bar`` loop with a live WebSocket feed
  from Alpaca's Market Data API.  The portfolio / risk / strategy logic
  remains unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")   # headless — no display required
import matplotlib.pyplot as plt
import pandas as pd
from zoneinfo import ZoneInfo

from src.backtest.metrics import compute_metrics, format_metrics
from src.backtest.trade import Trade
from src.data.base import BaseDataProvider
from src.portfolio.portfolio import Portfolio
from src.risk.risk_manager import RiskManager
from src.strategy.base import BaseStrategy, Signal, SignalDirection
from src.utils.logger import get_logger

logger = get_logger(__name__)

_EASTERN = ZoneInfo("America/New_York")


class BacktestEngine:
    """Drive a bar-by-bar backtest for one or more symbols.

    Parameters
    ----------
    strategy:
        A fully-instantiated strategy object.
    data_provider:
        Source of OHLCV bars (e.g. :class:`~src.data.yahoo_provider.YahooDataProvider`).
    portfolio:
        Portfolio to trade against.
    risk_manager:
        Pre-trade and per-bar risk checks.
    symbols:
        List of tickers to trade.
    start_date:
        Backtest start (``"YYYY-MM-DD"``).
    end_date:
        Backtest end inclusive (``"YYYY-MM-DD"``).
    bar_interval:
        Bar width string accepted by the data provider (e.g. ``"5m"``).
    position_size_pct:
        Fraction of available cash used for each entry.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        data_provider: BaseDataProvider,
        portfolio: Portfolio,
        risk_manager: RiskManager,
        symbols: list[str],
        start_date: str,
        end_date: str,
        bar_interval: str = "5m",
        position_size_pct: float = 0.95,
    ) -> None:
        self._strategy          = strategy
        self._data_provider     = data_provider
        self._portfolio         = portfolio
        self._risk_manager      = risk_manager
        self._symbols           = symbols
        self._start_date        = start_date
        self._end_date          = end_date
        self._bar_interval      = bar_interval
        self._position_size_pct = position_size_pct

        # Loaded bar data: {symbol: DataFrame}
        self._bars: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Execute the full backtest.

        Returns
        -------
        dict
            Contains ``"metrics"``, ``"trades"`` (list of :class:`Trade`),
            and ``"equity_curve"`` (DataFrame).
        """
        logger.info("=== Backtest starting ===")
        logger.info("Symbols: %s  |  %s → %s  |  interval=%s",
                    self._symbols, self._start_date, self._end_date, self._bar_interval)

        # 1. Load data
        self._load_data()

        # 2. Build unified sorted timeline
        all_timestamps = self._build_timeline()

        if all_timestamps.empty:
            logger.error("No bar data loaded — aborting.")
            return {"metrics": {}, "trades": [], "equity_curve": pd.DataFrame()}

        logger.info("Total bars in timeline: %d", len(all_timestamps))

        # 3. Iterate bar by bar
        for ts in all_timestamps:
            self._process_bar(ts)

        # 4. Force-close any remaining open positions at last bar
        self._close_all_open_positions(all_timestamps[-1])

        # 5. Compute metrics
        equity_curve = self._portfolio.equity_curve
        metrics      = compute_metrics(
            trades=self._portfolio.trades,
            equity_curve=equity_curve,
            initial_capital=self._portfolio.initial_capital,
        )

        logger.info("\n%s", format_metrics(metrics))
        logger.info("=== Backtest complete ===")

        return {
            "metrics":      metrics,
            "trades":       self._portfolio.trades,
            "equity_curve": equity_curve,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_data(self) -> None:
        """Fetch bars from the data provider for all symbols."""
        for symbol in self._symbols:
            df = self._data_provider.fetch_bars(
                symbol=symbol,
                start=self._start_date,
                end=self._end_date,
                interval=self._bar_interval,
            )
            self._bars[symbol] = df

    def _build_timeline(self) -> pd.DatetimeIndex:
        """Merge all per-symbol bar timestamps into one sorted index."""
        indices = [df.index for df in self._bars.values() if not df.empty]
        if not indices:
            return pd.DatetimeIndex([])
        merged = indices[0]
        for idx in indices[1:]:
            merged = merged.union(idx)
        return merged.sort_values()

    def _process_bar(self, ts: pd.Timestamp) -> None:
        """Process a single timestamp across all symbols."""
        bar_data: dict[str, dict[str, float]] = {}

        for symbol in self._symbols:
            df = self._bars.get(symbol)
            if df is None or ts not in df.index:
                continue
            row = df.loc[ts]
            bar_data[symbol] = {
                "open":  float(row["open"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "close": float(row["close"]),
            }

        # ---- Risk: check exits BEFORE generating new signals ----------
        # This prevents entering and exiting in the same bar erroneously.
        exit_orders = self._risk_manager.check_exits(
            portfolio=self._portfolio,
            current_bar=ts,
            bar_data=bar_data,
        )
        for symbol, exit_price, reason in exit_orders:
            self._portfolio.close_position(symbol, exit_price, ts, reason)

        # ---- Strategy: generate signals for each symbol ---------------
        for symbol in self._symbols:
            df = self._bars.get(symbol)
            if df is None or ts not in df.index:
                continue

            # Pass only bars up to and INCLUDING current bar — no look-ahead
            bars_so_far = df.loc[df.index <= ts]

            signal: Signal | None = self._strategy.generate_signal(
                symbol=symbol,
                bars=bars_so_far,
                current_bar=ts,
            )

            if signal is None:
                continue

            if signal.direction == SignalDirection.LONG:
                approved = self._risk_manager.approve_entry(
                    signal=signal,
                    portfolio=self._portfolio,
                    current_bar=ts,
                )
                if not approved:
                    continue

                pos = self._portfolio.open_long(
                    symbol=signal.symbol,
                    entry_price=signal.entry_price,
                    timestamp=ts,
                    position_size_pct=self._position_size_pct,
                    stop_loss=signal.stop_loss,
                    meta=signal.meta,
                )
                if pos is not None:
                    bar_et  = ts.astimezone(_EASTERN)
                    date_str = bar_et.date().isoformat()
                    self._risk_manager.record_trade_taken(symbol, date_str)

        # ---- Portfolio: record equity snapshot -------------------------
        current_prices = {sym: d["close"] for sym, d in bar_data.items()}
        self._portfolio.record_equity(ts, current_prices)

    def _close_all_open_positions(self, ts: pd.Timestamp) -> None:
        """Force-close any positions still open at the final bar."""
        for symbol in list(self._portfolio.positions.keys()):
            df = self._bars.get(symbol)
            if df is not None and not df.empty and ts in df.index:
                price = float(df.loc[ts, "close"])
            else:
                price = self._portfolio.positions[symbol].entry_price
            self._portfolio.close_position(symbol, price, ts, "end_of_backtest")

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def plot_equity_curve(
        equity_curve: pd.DataFrame,
        output_path: str | Path | None = None,
    ) -> None:
        """Plot the equity curve and optionally save to *output_path*.

        Parameters
        ----------
        equity_curve:
            DataFrame returned by :attr:`Portfolio.equity_curve`.
        output_path:
            If given, save the figure to this path instead of displaying it.
        """
        if equity_curve.empty:
            logger.warning("Equity curve is empty — nothing to plot.")
            return

        fig, ax = plt.subplots(figsize=(14, 5))
        ax.plot(equity_curve.index, equity_curve["equity"], linewidth=1.2, color="#2563eb")
        ax.set_title("Equity Curve", fontsize=14, fontweight="bold")
        ax.set_xlabel("Date")
        ax.set_ylabel("Portfolio Equity ($)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        if output_path:
            fig.savefig(output_path, dpi=150)
            logger.info("Equity curve saved to %s", output_path)
        else:
            plt.show()

        plt.close(fig)

    @staticmethod
    def trade_log(trades: list[Trade]) -> pd.DataFrame:
        """Convert the trade list to a tidy DataFrame for inspection."""
        if not trades:
            return pd.DataFrame()
        return pd.DataFrame([t.to_dict() for t in trades]).set_index("entry_time")
