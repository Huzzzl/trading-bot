"""
tests/test_paper_config_validation.py
--------------------------------------
Tests for validate_paper_config() — config-level safety validation.

All tests are purely in-process; no Alpaca credentials, no network calls,
no order submission or cancellation.
"""

from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from src.config.loader import ExecutionConfig, validate_paper_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exec(**kwargs) -> ExecutionConfig:
    """Build an ExecutionConfig for paper mode with paper_trading_enabled=True."""
    defaults = dict(
        mode="paper",
        paper_trading_enabled=True,
    )
    defaults.update(kwargs)
    return ExecutionConfig(**defaults)


_BASE_YAML = textwrap.dedent("""\
    backtest:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
      initial_capital: 100000
      commission_per_share: 0.005
      slippage_per_share: 0.01
    symbols: [SPY]
    data:
      provider: yahoo
      bar_interval: "5m"
      timezone: "America/New_York"
    strategy:
      name: opening_range_breakout
      params: {}
    risk: {}
    logging:
      level: WARNING
      format: "%(message)s"
""")


def _load_yaml(execution_yaml: str = "") -> object:
    """Write a temp YAML and run load_config() to exercise end-to-end validation."""
    from src.config.loader import load_config
    tmp = Path(tempfile.mktemp(suffix=".yaml"))
    tmp.write_text(_BASE_YAML + execution_yaml, encoding="utf-8")
    try:
        return load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 1. validate_paper_config unit tests
# ---------------------------------------------------------------------------

class TestValidatePaperConfigDefaults:
    def test_default_execution_config_passes(self):
        validate_paper_config(ExecutionConfig())  # mode=backtest, all defaults

    def test_backtest_mode_passes(self):
        validate_paper_config(ExecutionConfig(mode="backtest"))

    def test_paper_mode_disabled_passes(self):
        validate_paper_config(ExecutionConfig(mode="paper", paper_trading_enabled=False))

    def test_paper_preview_default_passes(self):
        """paper_preview_only=True (default) with paper mode enabled — always valid."""
        validate_paper_config(_exec(paper_preview_only=True))

    def test_paper_close_preview_default_passes(self):
        """paper_close_preview_only=True (default) with close enabled — always valid."""
        validate_paper_config(_exec(
            paper_close_positions_enabled=True,
            paper_close_preview_only=True,
        ))


class TestValidatePaperConfigBuyPath:
    def test_buy_preview_is_valid(self):
        validate_paper_config(_exec(paper_preview_only=True))

    def test_buy_submit_with_selected_id_is_valid(self):
        validate_paper_config(_exec(
            paper_preview_only=False,
            paper_selected_client_order_id="BT-000001",
        ))

    def test_buy_submit_without_selected_id_raises(self):
        with pytest.raises(ValueError, match="paper_selected_client_order_id"):
            validate_paper_config(_exec(
                paper_preview_only=False,
                paper_selected_client_order_id=None,
            ))

    def test_buy_submit_blank_selected_id_raises(self):
        with pytest.raises(ValueError, match="paper_selected_client_order_id"):
            validate_paper_config(_exec(
                paper_preview_only=False,
                paper_selected_client_order_id="   ",
            ))

    def test_buy_submit_empty_string_selected_id_raises(self):
        with pytest.raises(ValueError, match="paper_selected_client_order_id"):
            validate_paper_config(_exec(
                paper_preview_only=False,
                paper_selected_client_order_id="",
            ))


class TestValidatePaperConfigClosePath:
    def test_close_preview_is_valid(self):
        validate_paper_config(_exec(
            paper_close_positions_enabled=True,
            paper_close_preview_only=True,
        ))

    def test_close_submit_with_selected_id_is_valid(self):
        validate_paper_config(_exec(
            paper_close_positions_enabled=True,
            paper_close_preview_only=False,
            paper_selected_close_client_order_id="BC-20240115100000-SPY",
        ))

    def test_close_submit_without_selected_id_raises(self):
        with pytest.raises(ValueError, match="paper_selected_close_client_order_id"):
            validate_paper_config(_exec(
                paper_close_positions_enabled=True,
                paper_close_preview_only=False,
                paper_selected_close_client_order_id=None,
            ))

    def test_close_submit_blank_selected_id_raises(self):
        with pytest.raises(ValueError, match="paper_selected_close_client_order_id"):
            validate_paper_config(_exec(
                paper_close_positions_enabled=True,
                paper_close_preview_only=False,
                paper_selected_close_client_order_id="   ",
            ))


class TestValidatePaperConfigConflict:
    def test_close_enabled_and_buy_submit_active_raises(self):
        """paper_close_positions_enabled=true + paper_preview_only=false → conflict."""
        with pytest.raises(ValueError, match="mutually exclusive"):
            validate_paper_config(_exec(
                paper_close_positions_enabled=True,
                paper_preview_only=False,
                paper_selected_client_order_id="BT-000001",  # selected ID is irrelevant
            ))

    def test_close_enabled_and_buy_preview_is_fine(self):
        """paper_close_positions_enabled=true + paper_preview_only=true → no conflict."""
        validate_paper_config(_exec(
            paper_close_positions_enabled=True,
            paper_preview_only=True,
        ))

    def test_close_disabled_and_buy_submit_is_fine(self):
        """paper_close_positions_enabled=false + paper_preview_only=false → buy submit."""
        validate_paper_config(_exec(
            paper_close_positions_enabled=False,
            paper_preview_only=False,
            paper_selected_client_order_id="BT-000001",
        ))


class TestValidatePaperConfigQuantityOverrides:
    def test_override_1_is_valid(self):
        validate_paper_config(_exec(paper_order_quantity_override=1.0))

    def test_override_none_is_valid(self):
        validate_paper_config(_exec(paper_order_quantity_override=None))

    def test_override_greater_than_1_raises(self):
        with pytest.raises(ValueError, match="paper_order_quantity_override"):
            validate_paper_config(_exec(paper_order_quantity_override=2.0))

    def test_override_zero_raises(self):
        with pytest.raises(ValueError, match="paper_order_quantity_override"):
            validate_paper_config(_exec(paper_order_quantity_override=0.0))

    def test_override_negative_raises(self):
        with pytest.raises(ValueError, match="paper_order_quantity_override"):
            validate_paper_config(_exec(paper_order_quantity_override=-1.0))

    def test_override_fractional_raises(self):
        with pytest.raises(ValueError, match="paper_order_quantity_override"):
            validate_paper_config(_exec(paper_order_quantity_override=0.5))

    def test_close_override_1_is_valid(self):
        validate_paper_config(_exec(paper_close_quantity_override=1.0))

    def test_close_override_none_is_valid(self):
        validate_paper_config(_exec(paper_close_quantity_override=None))

    def test_close_override_greater_than_1_raises(self):
        with pytest.raises(ValueError, match="paper_close_quantity_override"):
            validate_paper_config(_exec(paper_close_quantity_override=2.0))

    def test_close_override_zero_raises(self):
        with pytest.raises(ValueError, match="paper_close_quantity_override"):
            validate_paper_config(_exec(paper_close_quantity_override=0.0))

    def test_close_override_fractional_raises(self):
        with pytest.raises(ValueError, match="paper_close_quantity_override"):
            validate_paper_config(_exec(paper_close_quantity_override=0.5))


# ---------------------------------------------------------------------------
# 2. End-to-end via load_config() — validates YAML → validation → error
# ---------------------------------------------------------------------------

class TestLoadConfigWithValidation:
    def test_default_config_loads(self):
        """No execution section → defaults → valid."""
        cfg = _load_yaml()
        assert cfg.execution.mode == "backtest"

    def test_backtest_mode_loads(self):
        cfg = _load_yaml('execution:\n  mode: "backtest"\n')
        assert cfg.execution.mode == "backtest"

    def test_paper_preview_config_loads(self):
        cfg = _load_yaml(textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: true
        """))
        assert cfg.execution.mode == "paper"
        assert cfg.execution.paper_preview_only is True

    def test_paper_submit_without_selected_id_raises_via_load_config(self):
        with pytest.raises(ValueError, match="paper_selected_client_order_id"):
            _load_yaml(textwrap.dedent("""\
                execution:
                  mode: paper
                  paper_trading_enabled: true
                  paper_preview_only: false
            """))

    def test_paper_submit_with_selected_id_loads(self):
        cfg = _load_yaml(textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_preview_only: false
              paper_selected_client_order_id: "BT-000001"
              paper_order_quantity_override: 1
        """))
        assert cfg.execution.paper_selected_client_order_id == "BT-000001"

    def test_close_preview_config_loads(self):
        cfg = _load_yaml(textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_close_positions_enabled: true
              paper_close_preview_only: true
        """))
        assert cfg.execution.paper_close_positions_enabled is True

    def test_close_submit_without_selected_id_raises_via_load_config(self):
        with pytest.raises(ValueError, match="paper_selected_close_client_order_id"):
            _load_yaml(textwrap.dedent("""\
                execution:
                  mode: paper
                  paper_trading_enabled: true
                  paper_close_positions_enabled: true
                  paper_close_preview_only: false
            """))

    def test_close_submit_with_selected_id_loads(self):
        cfg = _load_yaml(textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: true
              paper_close_positions_enabled: true
              paper_close_preview_only: false
              paper_selected_close_client_order_id: "BC-20240115100000-SPY"
              paper_close_quantity_override: 1
        """))
        assert cfg.execution.paper_selected_close_client_order_id == "BC-20240115100000-SPY"

    def test_close_and_buy_submit_conflict_raises_via_load_config(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            _load_yaml(textwrap.dedent("""\
                execution:
                  mode: paper
                  paper_trading_enabled: true
                  paper_close_positions_enabled: true
                  paper_preview_only: false
                  paper_selected_client_order_id: "BT-000001"
            """))

    def test_override_not_1_raises_via_load_config(self):
        with pytest.raises(ValueError, match="paper_order_quantity_override"):
            _load_yaml(textwrap.dedent("""\
                execution:
                  mode: paper
                  paper_trading_enabled: true
                  paper_order_quantity_override: 5
            """))

    def test_close_override_not_1_raises_via_load_config(self):
        with pytest.raises(ValueError, match="paper_close_quantity_override"):
            _load_yaml(textwrap.dedent("""\
                execution:
                  mode: paper
                  paper_trading_enabled: true
                  paper_close_quantity_override: 2
            """))

    def test_paper_mode_disabled_passes_even_with_invalid_overrides(self):
        """paper_trading_enabled=false → validation skipped regardless of other fields."""
        # paper_preview_only=false without selected_id would normally fail, but since
        # paper_trading_enabled=false the validation returns early.
        cfg = _load_yaml(textwrap.dedent("""\
            execution:
              mode: paper
              paper_trading_enabled: false
              paper_preview_only: false
        """))
        assert cfg.execution.paper_trading_enabled is False

    def test_backtest_mode_unaffected_by_any_paper_field(self):
        """backtest mode bypasses all paper validation."""
        cfg = _load_yaml(textwrap.dedent("""\
            execution:
              mode: backtest
              paper_preview_only: false
        """))
        assert cfg.execution.mode == "backtest"
