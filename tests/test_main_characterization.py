"""
tests/test_main_characterization.py
-------------------------------------
Characterization tests for src/main.py CLI and dispatch behaviour.

These tests lock CURRENT behaviour before the PR 8 refactor.  They use
monkeypatching and mocks; no real yfinance/network/Alpaca/credentials.
No output, config, or src files are modified by any test.

Current CLI --mode choices (from parse_args):
    "backtest"      — default; runs a single backtest with full reporting
    "candidate-b"   — Candidate-B parameter overrides, then backtest
    "sweep"         — parameter grid search; writes experiments.csv
    "walk-forward"  — walk-forward validation across time windows

Modes NOT currently in --mode choices (rejected by argparse):
    "live"   — not implemented; rejected at parse time
    "paper"  — paper execution is gated via cfg.execution.mode (not --mode)

Paper gate (inside main()):
    Reached only when cfg.execution.mode == "paper".
    Raises NotImplementedError when paper_trading_enabled is False.
    Not reachable from the default config.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.config.loader import load_config
from src.main import (
    CANDIDATE_B_OVERRIDES,
    apply_candidate_b,
    parse_args,
)
import src.main as _main_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_cfg():
    """Return a fresh default AppConfig (reads local config/settings.yaml; no network)."""
    return load_config(None)


def _mock_run_backtest_result():
    """Minimal BacktestRunResult-like object used by dispatch tests."""
    from src.backtest.backtest_runner import BacktestRunResult
    return BacktestRunResult(
        metrics={
            "num_trades": 0,
            "initial_capital": 100_000.0,
            "final_equity":    100_000.0,
            "total_return_pct":       0.0,
            "annualized_return_pct":  0.0,
            "max_drawdown_pct":       0.0,
            "sharpe_ratio":           0.0,
            "win_rate_pct":           0.0,
            "avg_winning_trade":      0.0,
            "avg_losing_trade":       0.0,
            "total_commission":       0.0,
        },
        trades=[],
        equity_curve=pd.DataFrame({"equity": []}, index=pd.DatetimeIndex([])),
        order_intents=[],
        strategy_name="opening_range_breakout",
        symbols=["SPY"],
        bar_interval="5m",
    )


def _apply_common_patches(monkeypatch, tmp_path, mode: str):
    """
    Apply all side-effect patches needed to run main() in tests.
    Returns a dict of useful mock objects.
    """
    import src.backtest.backtest_runner as _runner_mod
    from src.experiments import sweep_runner as _sweep_mod
    from src.experiments import walk_forward_runner as _wf_mod

    cfg = _default_cfg()

    mock_result  = _mock_run_backtest_result()
    mock_reporter   = MagicMock()
    mock_sweeper    = MagicMock()
    mock_wf_runner  = MagicMock()

    monkeypatch.setattr(sys, "argv", ["src.main", "--mode", mode, "--output-dir", str(tmp_path)])
    monkeypatch.setattr(_main_mod, "load_config",       lambda _: cfg)
    monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
    monkeypatch.setattr(_runner_mod, "run_backtest",    lambda config, *, data_provider: mock_result)
    monkeypatch.setattr(_main_mod.BacktestEngine, "plot_equity_curve", lambda *a, **kw: None)
    monkeypatch.setattr(_main_mod, "ReportGenerator",   lambda **kw: mock_reporter)
    monkeypatch.setattr(_main_mod, "YahooDataProvider", lambda: MagicMock())
    monkeypatch.setattr(_main_mod, "CachedMarketDataProvider", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(_sweep_mod, "SweepRunner",           lambda **kw: mock_sweeper)
    monkeypatch.setattr(_wf_mod,    "WalkForwardRunner",     lambda **kw: mock_wf_runner)

    return {
        "cfg":        cfg,
        "result":     mock_result,
        "reporter":   mock_reporter,
        "sweeper":    mock_sweeper,
        "wf_runner":  mock_wf_runner,
    }


# ---------------------------------------------------------------------------
# TestMainImport
# ---------------------------------------------------------------------------


class TestMainImport:
    """Importing src.main must not trigger broker calls, env reads, or trading."""

    def test_parse_args_is_callable(self) -> None:
        assert callable(_main_mod.parse_args)

    def test_main_is_callable(self) -> None:
        assert callable(_main_mod.main)

    def test_build_engine_is_not_present(self) -> None:
        assert not hasattr(_main_mod, "build_engine")

    def test_apply_candidate_b_is_callable(self) -> None:
        assert callable(_main_mod.apply_candidate_b)

    def test_candidate_b_overrides_is_dict(self) -> None:
        assert isinstance(_main_mod.CANDIDATE_B_OVERRIDES, dict)

    def test_alpaca_broker_not_at_module_level(self) -> None:
        # AlpacaBrokerAdapter is a lazy import inside _run_paper_close and the
        # paper execution block — not a module-level name.  Importing main must
        # not bind it at top scope.
        assert not hasattr(_main_mod, "AlpacaBrokerAdapter")


# ---------------------------------------------------------------------------
# TestParseArgs
# ---------------------------------------------------------------------------


class TestParseArgs:
    """
    Characterise the current parse_args() CLI surface.

    Current --mode choices: "backtest", "candidate-b", "sweep", "walk-forward"
    """

    def _parse(self, argv: list[str], monkeypatch) -> object:
        monkeypatch.setattr(sys, "argv", ["src.main"] + argv)
        return parse_args()

    # --- defaults ---

    def test_default_mode_is_backtest(self, monkeypatch) -> None:
        args = self._parse([], monkeypatch)
        assert args.mode == "backtest"

    def test_default_output_dir(self, monkeypatch) -> None:
        args = self._parse([], monkeypatch)
        assert args.output_dir == "output"

    def test_default_config_is_none(self, monkeypatch) -> None:
        args = self._parse([], monkeypatch)
        assert args.config is None

    # --- accepted modes ---

    def test_mode_backtest_accepted(self, monkeypatch) -> None:
        args = self._parse(["--mode", "backtest"], monkeypatch)
        assert args.mode == "backtest"

    def test_mode_candidate_b_accepted(self, monkeypatch) -> None:
        args = self._parse(["--mode", "candidate-b"], monkeypatch)
        assert args.mode == "candidate-b"

    def test_mode_sweep_accepted(self, monkeypatch) -> None:
        args = self._parse(["--mode", "sweep"], monkeypatch)
        assert args.mode == "sweep"

    def test_mode_walk_forward_accepted(self, monkeypatch) -> None:
        args = self._parse(["--mode", "walk-forward"], monkeypatch)
        assert args.mode == "walk-forward"

    # --- rejected modes (not in current choices) ---

    def test_mode_live_rejected(self, monkeypatch) -> None:
        # "live" is not in the current choices list — argparse exits non-zero
        monkeypatch.setattr(sys, "argv", ["src.main", "--mode", "live"])
        with pytest.raises(SystemExit) as exc_info:
            parse_args()
        assert exc_info.value.code != 0

    def test_mode_paper_rejected(self, monkeypatch) -> None:
        # "paper" is not a --mode choice; paper path is reached via cfg.execution.mode
        monkeypatch.setattr(sys, "argv", ["src.main", "--mode", "paper"])
        with pytest.raises(SystemExit) as exc_info:
            parse_args()
        assert exc_info.value.code != 0

    def test_unknown_mode_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["src.main", "--mode", "bogus"])
        with pytest.raises(SystemExit) as exc_info:
            parse_args()
        assert exc_info.value.code != 0

    # --- optional arguments ---

    def test_custom_config_accepted(self, monkeypatch) -> None:
        args = self._parse(["--config", "path/to/custom.yaml"], monkeypatch)
        assert args.config == "path/to/custom.yaml"

    def test_custom_output_dir_accepted(self, monkeypatch) -> None:
        args = self._parse(["--output-dir", "/tmp/testout"], monkeypatch)
        assert args.output_dir == "/tmp/testout"

    def test_help_exits_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["src.main", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            parse_args()
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# TestCandidateBOverrides
# ---------------------------------------------------------------------------


class TestCandidateBOverrides:
    """Characterise the hardcoded CANDIDATE_B_OVERRIDES constants."""

    def test_symbols_is_qqq(self) -> None:
        assert CANDIDATE_B_OVERRIDES["symbols"] == ["QQQ"]

    def test_opening_range_end(self) -> None:
        assert CANDIDATE_B_OVERRIDES["opening_range_end"] == "09:45"

    def test_breakout_trigger(self) -> None:
        assert CANDIDATE_B_OVERRIDES["breakout_trigger"] == "close"

    def test_position_size_pct(self) -> None:
        assert CANDIDATE_B_OVERRIDES["position_size_pct"] == 0.50


# ---------------------------------------------------------------------------
# TestApplyCandidateB
# ---------------------------------------------------------------------------


class TestApplyCandidateB:
    """Characterise apply_candidate_b() — deep copy + override, no mutation."""

    def test_symbols_overridden(self) -> None:
        result = apply_candidate_b(_default_cfg())
        assert result.symbols == ["QQQ"]

    def test_or_end_overridden(self) -> None:
        result = apply_candidate_b(_default_cfg())
        assert result.strategy.params["opening_range_end"] == "09:45"

    def test_trigger_overridden(self) -> None:
        result = apply_candidate_b(_default_cfg())
        assert result.strategy.params["breakout_trigger"] == "close"

    def test_position_size_overridden(self) -> None:
        result = apply_candidate_b(_default_cfg())
        assert result.strategy.params["position_size_pct"] == pytest.approx(0.50)

    def test_does_not_mutate_original(self) -> None:
        cfg = _default_cfg()
        original_symbols = list(cfg.symbols)
        original_or_end  = cfg.strategy.params.get("opening_range_end")
        apply_candidate_b(cfg)
        assert cfg.symbols == original_symbols
        assert cfg.strategy.params.get("opening_range_end") == original_or_end

    def test_returns_different_object(self) -> None:
        cfg = _default_cfg()
        result = apply_candidate_b(cfg)
        assert result is not cfg


# ---------------------------------------------------------------------------
# TestMainPaperGate
# ---------------------------------------------------------------------------


class TestMainPaperGate:
    """
    The paper execution path is gated by cfg.execution.mode, not --mode.
    Default config does not reach the paper gate.
    """

    def test_default_execution_mode_is_backtest(self) -> None:
        # Paper gate not triggered; default execution.mode is "backtest"
        cfg = _default_cfg()
        assert cfg.execution.mode == "backtest"

    def test_paper_trading_disabled_by_default(self) -> None:
        cfg = _default_cfg()
        assert cfg.execution.paper_trading_enabled is False

    def test_paper_gate_raises_not_implemented_when_disabled(
        self, monkeypatch, tmp_path
    ) -> None:
        """execution.mode='paper' + paper_trading_enabled=False → NotImplementedError."""
        cfg = _default_cfg()
        cfg.execution.mode = "paper"
        # paper_trading_enabled remains False

        monkeypatch.setattr(sys, "argv", ["src.main", "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config",       lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)

        with pytest.raises(NotImplementedError):
            _main_mod.main()


# ---------------------------------------------------------------------------
# TestMainModeDispatch
# ---------------------------------------------------------------------------


class TestMainModeDispatch:
    """main() routes each --mode to the correct internal path (no network/broker)."""

    def test_backtest_mode_calls_run_backtest(self, monkeypatch, tmp_path) -> None:
        import src.backtest.backtest_runner as _runner_mod

        cfg = _default_cfg()
        calls: list = []

        def _track(config, data_provider):
            calls.append(config)
            return _mock_run_backtest_result()

        monkeypatch.setattr(sys, "argv", ["src.main", "--mode", "backtest",
                                           "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config",       lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
        monkeypatch.setattr(_runner_mod, "run_backtest",    _track)
        monkeypatch.setattr(_main_mod.BacktestEngine, "plot_equity_curve", lambda *a, **kw: None)
        monkeypatch.setattr(_main_mod, "ReportGenerator",   lambda **kw: MagicMock())
        monkeypatch.setattr(_main_mod, "YahooDataProvider", lambda: MagicMock())
        monkeypatch.setattr(_main_mod, "CachedMarketDataProvider", lambda *a, **kw: MagicMock())

        _main_mod.main()
        assert len(calls) == 1

    def test_backtest_run_config_core_fields(self, monkeypatch, tmp_path) -> None:
        import src.backtest.backtest_runner as _runner_mod

        cfg = _default_cfg()
        captured: list = []

        def _track(config, data_provider):
            captured.append(config)
            return _mock_run_backtest_result()

        monkeypatch.setattr(sys, "argv", ["src.main", "--mode", "backtest",
                                           "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config",       lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
        monkeypatch.setattr(_runner_mod, "run_backtest",    _track)
        monkeypatch.setattr(_main_mod.BacktestEngine, "plot_equity_curve", lambda *a, **kw: None)
        monkeypatch.setattr(_main_mod, "ReportGenerator",   lambda **kw: MagicMock())
        monkeypatch.setattr(_main_mod, "YahooDataProvider", lambda: MagicMock())
        monkeypatch.setattr(_main_mod, "CachedMarketDataProvider", lambda *a, **kw: MagicMock())

        _main_mod.main()
        rc = captured[0]
        assert rc.strategy_name == cfg.strategy.name
        assert rc.symbols == list(cfg.symbols)
        assert rc.start_date == cfg.backtest.start_date
        assert rc.end_date == cfg.backtest.end_date
        assert rc.bar_interval == cfg.data.bar_interval
        assert rc.initial_capital == pytest.approx(cfg.backtest.initial_capital)
        assert rc.position_size_pct == pytest.approx(
            float(cfg.strategy.params.get("position_size_pct", 0.95))
        )
        assert rc.stop_execution == str(cfg.strategy.params.get("stop_execution", "bar_close"))

    def test_candidate_b_mode_applies_overrides_before_run_backtest(
        self, monkeypatch, tmp_path
    ) -> None:
        import src.backtest.backtest_runner as _runner_mod

        cfg = _default_cfg()
        captured: list = []

        def _track(config, data_provider):
            captured.append(config)
            return _mock_run_backtest_result()

        monkeypatch.setattr(sys, "argv", ["src.main", "--mode", "candidate-b",
                                           "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config",       lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
        monkeypatch.setattr(_runner_mod, "run_backtest",    _track)
        monkeypatch.setattr(_main_mod.BacktestEngine, "plot_equity_curve", lambda *a, **kw: None)
        monkeypatch.setattr(_main_mod, "ReportGenerator",   lambda **kw: MagicMock())
        monkeypatch.setattr(_main_mod, "YahooDataProvider", lambda: MagicMock())
        monkeypatch.setattr(_main_mod, "CachedMarketDataProvider", lambda *a, **kw: MagicMock())

        _main_mod.main()
        # Candidate-B overrides must be applied before run_backtest is called
        assert captured[0].symbols == ["QQQ"]

    def test_sweep_mode_calls_sweep_runner(self, monkeypatch, tmp_path) -> None:
        mocks = _apply_common_patches(monkeypatch, tmp_path, "sweep")
        _main_mod.main()
        mocks["sweeper"].run.assert_called_once()

    def test_walk_forward_mode_calls_walk_forward_runner(
        self, monkeypatch, tmp_path
    ) -> None:
        mocks = _apply_common_patches(monkeypatch, tmp_path, "walk-forward")
        _main_mod.main()
        mocks["wf_runner"].run.assert_called_once()

    def test_backtest_mode_does_not_touch_alpaca(self, monkeypatch, tmp_path) -> None:
        """Backtest dispatch must not instantiate AlpacaBrokerAdapter."""
        alpaca_calls: list = []

        class _FakeAlpaca:
            def __init__(self, *a, **kw):
                alpaca_calls.append(True)

        mocks = _apply_common_patches(monkeypatch, tmp_path, "backtest")
        # Patch the lazy-import path inside _run_paper_close (not normally reached)
        import src.execution.alpaca_broker as _ab
        monkeypatch.setattr(_ab, "AlpacaBrokerAdapter", _FakeAlpaca)

        _main_mod.main()
        assert alpaca_calls == []


# ---------------------------------------------------------------------------
# TestSourceCharacterization
# ---------------------------------------------------------------------------

_MAIN_SRC = Path("src/main.py")


class TestSourceCharacterization:
    """
    Lock current mode names in the source file for regression detection.
    These tests will fail if someone renames or removes a mode without updating
    the characterization suite first.
    """

    def _src(self) -> str:
        return _MAIN_SRC.read_text(encoding="utf-8")

    def test_backtest_mode_string_present(self) -> None:
        assert '"backtest"' in self._src()

    def test_candidate_b_mode_string_present(self) -> None:
        assert '"candidate-b"' in self._src()

    def test_sweep_mode_string_present(self) -> None:
        assert '"sweep"' in self._src()

    def test_walk_forward_mode_string_present(self) -> None:
        assert '"walk-forward"' in self._src()

    def test_alpaca_import_is_not_at_module_top_level(self) -> None:
        # AlpacaBrokerAdapter must only be imported lazily inside functions,
        # never at the top of the file.  A top-level import would mean paper
        # execution infrastructure is loaded on every invocation.
        import re
        lines = self._src().splitlines()
        in_function = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                in_function = True
            # Only flag actual import statements, not docstring/comment mentions
            if not in_function and re.match(
                r"\s*from\s+src\.execution.*alpaca", line
            ):
                pytest.fail(
                    "alpaca_broker import appears at module top level in src/main.py"
                )
