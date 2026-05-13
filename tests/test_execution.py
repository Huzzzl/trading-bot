"""
tests/test_execution.py
-----------------------
Tests for the paper-trading readiness guard and OrderIntent abstraction.

No network calls, no broker, no API keys.
"""

from __future__ import annotations

import pathlib
import tempfile
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.config.loader import AppConfig, ExecutionConfig, load_config
from src.execution.order_intent import OrderIntent

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Minimal YAML helper — writes a temporary settings file and loads it.
# ---------------------------------------------------------------------------

_BASE_YAML = """\
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
"""


def _load_yaml(extra: str = "") -> AppConfig:
    tmp = pathlib.Path(tempfile.mktemp(suffix=".yaml"))
    tmp.write_text(_BASE_YAML + extra)
    return load_config(tmp)


# ===========================================================================
# ExecutionConfig — loader and defaults
# ===========================================================================

class TestExecutionConfig:
    def test_default_mode_is_backtest_when_section_absent(self):
        cfg = _load_yaml()
        assert cfg.execution.mode == "backtest"

    def test_explicit_backtest_mode_accepted(self):
        cfg = _load_yaml('execution:\n  mode: "backtest"\n')
        assert cfg.execution.mode == "backtest"

    def test_paper_mode_accepted_by_loader(self):
        cfg = _load_yaml('execution:\n  mode: "paper"\n')
        assert cfg.execution.mode == "paper"

    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="execution.mode"):
            _load_yaml('execution:\n  mode: "live"\n')

    def test_execution_config_dataclass_defaults(self):
        ec = ExecutionConfig()
        assert ec.mode == "backtest"

    def test_settings_yaml_has_backtest_mode(self):
        cfg = load_config()
        assert cfg.execution.mode == "backtest"


# ===========================================================================
# Paper-trading guard in main.py
# ===========================================================================

class TestPaperTradingGuard:
    def _make_cfg(self, mode: str) -> AppConfig:
        return _load_yaml(f'execution:\n  mode: "{mode}"\n')

    def test_paper_mode_raises_not_implemented_error(self):
        from src.main import main as _main
        import sys, unittest.mock as mock

        cfg = self._make_cfg("paper")

        # Patch load_config to return a paper-mode config, capture the guard
        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--mode", "backtest"]):
            with pytest.raises(NotImplementedError, match="Paper trading is disabled"):
                _main()

    def test_paper_mode_error_message_is_clear(self):
        from src.main import main as _main
        import unittest.mock as mock

        cfg = self._make_cfg("paper")

        with mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog"]):
            with pytest.raises(NotImplementedError) as exc_info:
                _main()
        assert "paper" in str(exc_info.value).lower()

    def test_backtest_mode_does_not_raise(self):
        """Loading a backtest-mode config must not trigger the paper guard."""
        cfg = self._make_cfg("backtest")
        assert cfg.execution.mode == "backtest"
        # Guard only fires inside main(); the config itself is fine.


# ===========================================================================
# OrderIntent construction and validation
# ===========================================================================

_TS = pd.Timestamp("2024-01-02 10:05", tz=_ET)


class TestOrderIntentConstruction:
    def test_buy_market_order(self):
        oi = OrderIntent(
            symbol="SPY",
            side="buy",
            quantity=100.0,
            order_type="market",
            reason="breakout_entry",
            timestamp=_TS,
        )
        assert oi.symbol     == "SPY"
        assert oi.side       == "buy"
        assert oi.quantity   == 100.0
        assert oi.order_type == "market"
        assert oi.reason     == "breakout_entry"

    def test_sell_limit_order_with_prices(self):
        oi = OrderIntent(
            symbol="QQQ",
            side="sell",
            quantity=50.0,
            order_type="limit",
            reason="force_exit",
            timestamp=_TS,
            limit_price=420.0,
        )
        assert oi.limit_price == 420.0
        assert oi.stop_price  is None

    def test_stop_order_with_stop_price(self):
        oi = OrderIntent(
            symbol="SPY",
            side="sell",
            quantity=95.0,
            order_type="stop",
            reason="stop_loss",
            timestamp=_TS,
            stop_price=390.0,
        )
        assert oi.stop_price == 390.0

    def test_metadata_stored(self):
        oi = OrderIntent(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="market", reason="test", timestamp=_TS,
            metadata={"or_high": 410.5, "signal": "close_breakout"},
        )
        assert oi.metadata["or_high"] == 410.5

    def test_default_optional_fields_are_none(self):
        oi = OrderIntent(
            symbol="SPY", side="buy", quantity=1.0,
            order_type="market", reason="test", timestamp=_TS,
        )
        assert oi.limit_price is None
        assert oi.stop_price  is None
        assert oi.metadata    == {}


# ===========================================================================
# OrderIntent validation errors
# ===========================================================================

class TestOrderIntentValidation:
    def test_invalid_side_raises_value_error(self):
        with pytest.raises(ValueError, match="side"):
            OrderIntent(
                symbol="SPY", side="long", quantity=1.0,
                order_type="market", reason="x", timestamp=_TS,
            )

    def test_invalid_order_type_raises_value_error(self):
        with pytest.raises(ValueError, match="order_type"):
            OrderIntent(
                symbol="SPY", side="buy", quantity=1.0,
                order_type="twap", reason="x", timestamp=_TS,
            )

    def test_zero_quantity_raises_value_error(self):
        with pytest.raises(ValueError, match="quantity"):
            OrderIntent(
                symbol="SPY", side="buy", quantity=0.0,
                order_type="market", reason="x", timestamp=_TS,
            )

    def test_negative_quantity_raises_value_error(self):
        with pytest.raises(ValueError, match="quantity"):
            OrderIntent(
                symbol="SPY", side="buy", quantity=-5.0,
                order_type="market", reason="x", timestamp=_TS,
            )


# ===========================================================================
# OrderIntent serialisation
# ===========================================================================

class TestOrderIntentToDict:
    def _oi(self, **kwargs) -> OrderIntent:
        defaults = dict(
            symbol="SPY", side="buy", quantity=100.0,
            order_type="market", reason="entry", timestamp=_TS,
        )
        defaults.update(kwargs)
        return OrderIntent(**defaults)

    def test_to_dict_contains_all_required_keys(self):
        d = self._oi().to_dict()
        for key in ("symbol", "side", "quantity", "order_type",
                    "reason", "timestamp", "limit_price", "stop_price", "metadata"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_symbol_value(self):
        assert self._oi().to_dict()["symbol"] == "SPY"

    def test_to_dict_timestamp_is_string(self):
        assert isinstance(self._oi().to_dict()["timestamp"], str)

    def test_to_dict_optional_prices_are_none(self):
        d = self._oi().to_dict()
        assert d["limit_price"] is None
        assert d["stop_price"]  is None

    def test_to_dict_with_prices(self):
        oi = self._oi(order_type="limit", limit_price=400.0, stop_price=390.0)
        d  = oi.to_dict()
        assert d["limit_price"] == 400.0
        assert d["stop_price"]  == 390.0

    def test_to_dict_metadata_is_copy(self):
        meta = {"or_high": 410.0}
        oi   = self._oi(metadata=meta)
        d    = oi.to_dict()
        d["metadata"]["extra"] = "injected"
        # Original metadata in the intent is unchanged
        assert "extra" not in oi.metadata

    def test_to_dict_is_json_serialisable(self):
        import json
        oi = self._oi(metadata={"note": "test"})
        json.dumps(oi.to_dict())  # must not raise
