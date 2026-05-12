"""
tests/test_candidate_b.py
--------------------------
Unit tests for candidate-b mode in src/main.py.

Verifies that apply_candidate_b() applies the correct parameter overrides,
does not mutate the original config, and that the resulting config drives
the full backtest + reporting pipeline correctly.
All tests use FakeDataProvider — no network calls, no yfinance.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.config.loader import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.data.base import BaseDataProvider
from src.main import CANDIDATE_B_OVERRIDES, apply_candidate_b
from src.portfolio.portfolio import Portfolio
from src.reporting.report_generator import ReportGenerator
from src.risk.risk_manager import RiskManager
from src.strategy.opening_range_breakout import OpeningRangeBreakout

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRADING_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def _make_day_bars(date_str: str) -> pd.DataFrame:
    session_start = pd.Timestamp(date_str, tz=_ET).replace(hour=9, minute=30)
    idx = pd.date_range(session_start, periods=78, freq="5min")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000},
        index=idx,
    )


class FakeDataProvider(BaseDataProvider):
    def fetch_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        return pd.concat([_make_day_bars(d) for d in _TRADING_DAYS])


def _make_base_config(
    symbols: list[str] | None = None,
    opening_range_end: str = "10:00",
    breakout_trigger: str = "high",
    position_size_pct: float = 0.95,
) -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-02",
            end_date="2024-01-05",
            initial_capital=100_000.0,
            commission_per_share=0.005,
            slippage_per_share=0.01,
        ),
        symbols=symbols if symbols is not None else ["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(
            name="opening_range_breakout",
            params={
                "opening_range_start": "09:30",
                "opening_range_end":   opening_range_end,
                "force_exit_time":     "15:55",
                "position_size_pct":   position_size_pct,
                "long_only":           True,
                "breakout_trigger":    breakout_trigger,
            },
        ),
        risk=RiskConfig(max_open_positions=1),
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
    )


# ===========================================================================
# CANDIDATE_B_OVERRIDES constant
# ===========================================================================

class TestCandidateBConstant:
    def test_symbols_is_qqq(self):
        assert CANDIDATE_B_OVERRIDES["symbols"] == ["QQQ"]

    def test_opening_range_end(self):
        assert CANDIDATE_B_OVERRIDES["opening_range_end"] == "09:45"

    def test_breakout_trigger(self):
        assert CANDIDATE_B_OVERRIDES["breakout_trigger"] == "close"

    def test_position_size_pct(self):
        assert abs(CANDIDATE_B_OVERRIDES["position_size_pct"] - 0.50) < 1e-9

    def test_constant_has_exactly_four_keys(self):
        assert set(CANDIDATE_B_OVERRIDES.keys()) == {
            "symbols", "opening_range_end", "breakout_trigger", "position_size_pct"
        }

    def test_entry_cutoff_time_not_in_candidate_b_overrides(self):
        # entry_cutoff_time is intentionally NOT overridden by Candidate B —
        # it inherits null from the base config so no hardcoded cutoff is applied.
        assert "entry_cutoff_time" not in CANDIDATE_B_OVERRIDES


# ===========================================================================
# apply_candidate_b — override correctness
# ===========================================================================

class TestApplyCandidateB:
    def test_symbols_overridden_to_qqq(self):
        cfg = _make_base_config(symbols=["SPY"])
        result = apply_candidate_b(cfg)
        assert result.symbols == ["QQQ"]

    def test_opening_range_end_overridden(self):
        cfg = _make_base_config(opening_range_end="10:00")
        result = apply_candidate_b(cfg)
        assert result.strategy.params["opening_range_end"] == "09:45"

    def test_breakout_trigger_overridden(self):
        cfg = _make_base_config(breakout_trigger="high")
        result = apply_candidate_b(cfg)
        assert result.strategy.params["breakout_trigger"] == "close"

    def test_position_size_overridden(self):
        cfg = _make_base_config(position_size_pct=0.95)
        result = apply_candidate_b(cfg)
        assert abs(result.strategy.params["position_size_pct"] - 0.50) < 1e-9

    def test_other_params_preserved(self):
        cfg = _make_base_config()
        result = apply_candidate_b(cfg)
        assert result.strategy.params["opening_range_start"] == "09:30"
        assert result.strategy.params["force_exit_time"]     == "15:55"
        assert result.strategy.params["long_only"]           is True

    def test_entry_cutoff_time_not_overridden_to_11_30(self):
        cfg = _make_base_config()
        cfg.strategy.params["entry_cutoff_time"] = None
        result = apply_candidate_b(cfg)
        assert result.strategy.params.get("entry_cutoff_time") is None

    def test_backtest_dates_preserved(self):
        cfg = _make_base_config()
        result = apply_candidate_b(cfg)
        assert result.backtest.start_date == cfg.backtest.start_date
        assert result.backtest.end_date   == cfg.backtest.end_date

    def test_initial_capital_preserved(self):
        cfg = _make_base_config()
        result = apply_candidate_b(cfg)
        assert result.backtest.initial_capital == cfg.backtest.initial_capital


# ===========================================================================
# apply_candidate_b — config isolation (original must not be mutated)
# ===========================================================================

class TestApplyCandidateBIsolation:
    def test_original_symbols_not_mutated(self):
        cfg = _make_base_config(symbols=["SPY"])
        apply_candidate_b(cfg)
        assert cfg.symbols == ["SPY"]

    def test_original_opening_range_end_not_mutated(self):
        cfg = _make_base_config(opening_range_end="10:00")
        apply_candidate_b(cfg)
        assert cfg.strategy.params["opening_range_end"] == "10:00"

    def test_original_breakout_trigger_not_mutated(self):
        cfg = _make_base_config(breakout_trigger="high")
        apply_candidate_b(cfg)
        assert cfg.strategy.params["breakout_trigger"] == "high"

    def test_original_position_size_not_mutated(self):
        cfg = _make_base_config(position_size_pct=0.95)
        apply_candidate_b(cfg)
        assert abs(cfg.strategy.params["position_size_pct"] - 0.95) < 1e-9

    def test_returned_symbols_is_independent_list(self):
        cfg = _make_base_config(symbols=["SPY"])
        result = apply_candidate_b(cfg)
        result.symbols.append("EXTRA")
        assert cfg.symbols == ["SPY"]


# ===========================================================================
# End-to-end: candidate-b config drives backtest + reporting
# ===========================================================================

class TestCandidateBEndToEnd:
    def _run_pipeline(self, cfg: AppConfig, output_dir: Path) -> None:
        strategy     = OpeningRangeBreakout(params=cfg.strategy.params)
        portfolio    = Portfolio(
            initial_capital=cfg.backtest.initial_capital,
            commission_per_share=cfg.backtest.commission_per_share,
            slippage_per_share=cfg.backtest.slippage_per_share,
        )
        risk_manager = RiskManager(
            force_exit_time=cfg.strategy.params.get("force_exit_time", "15:55"),
            max_open_positions=cfg.risk.max_open_positions,
        )
        engine = BacktestEngine(
            strategy=strategy,
            data_provider=FakeDataProvider(),
            portfolio=portfolio,
            risk_manager=risk_manager,
            symbols=cfg.symbols,
            start_date=cfg.backtest.start_date,
            end_date=cfg.backtest.end_date,
            bar_interval=cfg.data.bar_interval,
            position_size_pct=cfg.strategy.params.get("position_size_pct", 0.95),
        )
        results = engine.run()
        open_positions_count = len(engine._portfolio.positions)

        equity_curve = results["equity_curve"]
        BacktestEngine.plot_equity_curve(equity_curve, output_path=output_dir / "equity_curve.png")

        ReportGenerator(
            metrics=results["metrics"],
            trades=results["trades"],
            equity_curve=equity_curve,
            config=cfg,
            output_dir=output_dir,
            open_positions_count=open_positions_count,
        ).generate_all()

    def test_reporting_artifacts_created(self, tmp_path):
        cfg = apply_candidate_b(_make_base_config())
        self._run_pipeline(cfg, tmp_path)
        assert (tmp_path / "equity_curve.png").exists()
        assert (tmp_path / "metrics.json").exists()
        assert (tmp_path / "trade_log.csv").exists()
        assert (tmp_path / "daily_summary.csv").exists()
        assert (tmp_path / "backtest_report.md").exists()

    def test_backtest_report_mentions_qqq(self, tmp_path):
        cfg = apply_candidate_b(_make_base_config(symbols=["SPY"]))
        self._run_pipeline(cfg, tmp_path)
        report = (tmp_path / "backtest_report.md").read_text()
        assert "QQQ" in report

    def test_metrics_json_is_valid_json(self, tmp_path):
        cfg = apply_candidate_b(_make_base_config())
        self._run_pipeline(cfg, tmp_path)
        metrics = json.loads((tmp_path / "metrics.json").read_text())
        assert isinstance(metrics, dict)

    def test_trade_log_csv_has_expected_columns(self, tmp_path):
        cfg = apply_candidate_b(_make_base_config())
        self._run_pipeline(cfg, tmp_path)
        df = pd.read_csv(tmp_path / "trade_log.csv")
        for col in ["symbol", "entry_time", "exit_time", "pnl"]:
            assert col in df.columns
