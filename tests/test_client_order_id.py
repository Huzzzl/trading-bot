"""
tests/test_client_order_id.py
------------------------------
Tests for the client_order_id feature:
  - OrderIntent optional field and serialisation
  - BacktestEngine deterministic sequential IDs and reset-per-run
  - FakeBrokerAdapter copies client_order_id into OrderResult
  - CSV outputs include client_order_id column
  - Reconciliation passes even when order_results are shuffled
  - Reconciliation warns when client_order_id is missing from results
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest.mock as mock
from pathlib import Path

import pandas as pd
import pytest

from src.execution.order_intent import OrderIntent
from src.execution.broker import OrderResult
from src.execution.fake_broker import FakeBrokerAdapter
from src.reporting.report_generator import ReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_timestamp() -> pd.Timestamp:
    return pd.Timestamp("2024-01-15 09:35:00", tz="America/New_York")


def _make_intent(
    symbol: str = "SPY",
    side: str = "buy",
    quantity: float = 10.0,
    reason: str = "entry",
    client_order_id: str | None = "BT-000001",
) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type="market",
        reason=reason,
        timestamp=_make_timestamp(),
        metadata={"entry_price": 470.0},
        client_order_id=client_order_id,
    )


def _make_result(
    symbol: str = "SPY",
    side: str = "buy",
    quantity: float = 10.0,
    reason: str = "entry",
    client_order_id: str | None = "BT-000001",
) -> OrderResult:
    return OrderResult(
        order_id="FAKE-000001",
        symbol=symbol,
        side=side,
        quantity=quantity,
        status="filled",
        submitted_at=_make_timestamp(),
        reason=reason,
        filled_at=_make_timestamp(),
        filled_price=470.0,
        metadata={},
        client_order_id=client_order_id,
    )


def _minimal_app_config():
    from src.config.loader import (
        AppConfig, BacktestConfig, DataConfig, ExecutionConfig,
        LoggingConfig, RiskConfig, StrategyConfig,
    )
    return AppConfig(
        backtest=BacktestConfig(
            start_date="2024-01-15", end_date="2024-01-15",
            initial_capital=100_000, commission_per_share=0.0, slippage_per_share=0.0,
        ),
        symbols=["SPY"],
        data=DataConfig(provider="yahoo", bar_interval="5m", timezone="America/New_York"),
        strategy=StrategyConfig(name="opening_range_breakout", params={
            "opening_range_start": "09:30", "opening_range_end": "10:00",
            "force_exit_time": "15:55", "position_size_pct": 0.95, "long_only": True,
        }),
        risk=RiskConfig(),
        logging=LoggingConfig(level="DEBUG", format="%(message)s"),
        execution=ExecutionConfig(mode="backtest", dry_run_broker=False),
    )


# ---------------------------------------------------------------------------
# OrderIntent: optional field and serialisation
# ---------------------------------------------------------------------------

class TestOrderIntentClientOrderId:
    def test_accepts_client_order_id(self):
        oi = _make_intent(client_order_id="BT-000042")
        assert oi.client_order_id == "BT-000042"

    def test_client_order_id_defaults_to_none(self):
        oi = OrderIntent(
            symbol="SPY", side="buy", quantity=5.0,
            order_type="market", reason="entry",
            timestamp=_make_timestamp(),
        )
        assert oi.client_order_id is None

    def test_to_dict_includes_client_order_id(self):
        oi = _make_intent(client_order_id="BT-000001")
        d = oi.to_dict()
        assert "client_order_id" in d
        assert d["client_order_id"] == "BT-000001"

    def test_to_dict_client_order_id_none(self):
        oi = _make_intent(client_order_id=None)
        d = oi.to_dict()
        assert d["client_order_id"] is None


# ---------------------------------------------------------------------------
# OrderResult: client_order_id field
# ---------------------------------------------------------------------------

class TestOrderResultClientOrderId:
    def test_accepts_client_order_id(self):
        r = _make_result(client_order_id="BT-000007")
        assert r.client_order_id == "BT-000007"

    def test_to_dict_includes_client_order_id(self):
        r = _make_result(client_order_id="BT-000007")
        d = r.to_dict()
        assert "client_order_id" in d
        assert d["client_order_id"] == "BT-000007"


# ---------------------------------------------------------------------------
# FakeBrokerAdapter: copies client_order_id into OrderResult
# ---------------------------------------------------------------------------

class TestFakeBrokerClientOrderId:
    def test_copies_client_order_id_buy(self):
        broker = FakeBrokerAdapter()
        intent = _make_intent(client_order_id="BT-000003")
        result = broker.submit_order(intent)
        assert result.client_order_id == "BT-000003"

    def test_copies_none_client_order_id(self):
        broker = FakeBrokerAdapter()
        intent = _make_intent(client_order_id=None)
        result = broker.submit_order(intent)
        assert result.client_order_id is None

    def test_copies_client_order_id_sell(self):
        broker = FakeBrokerAdapter()
        buy = _make_intent(side="buy", client_order_id="BT-000001")
        broker.submit_order(buy)
        sell = OrderIntent(
            symbol="SPY", side="sell", quantity=10.0,
            order_type="market", reason="stop_loss",
            timestamp=_make_timestamp(),
            metadata={"exit_price": 465.0},
            client_order_id="BT-000002",
        )
        result = broker.submit_order(sell)
        assert result.client_order_id == "BT-000002"

    def test_cancel_preserves_client_order_id(self):
        # Submit a pending (non-immediately-filled) order so it can be cancelled
        broker = FakeBrokerAdapter(fill_immediately=False)
        intent = OrderIntent(
            symbol="SPY", side="buy", quantity=10.0,
            order_type="limit", reason="entry",
            timestamp=_make_timestamp(),
            limit_price=470.0,
            client_order_id="BT-000001",
        )
        result = broker.submit_order(intent)
        assert result.status == "accepted"

        cancelled = broker.cancel_order(result.order_id)
        assert cancelled is True

        _, cancelled_result = broker.get_order(result.order_id)
        assert cancelled_result.status == "cancelled"
        assert cancelled_result.client_order_id == "BT-000001"


# ---------------------------------------------------------------------------
# BacktestEngine: deterministic IDs and reset per run
# ---------------------------------------------------------------------------

class TestBacktestEngineClientOrderId:
    """Run a minimal backtest via BacktestEngine and check IDs."""

    def _run_engine(self):
        """Return (engine, results) for a tiny one-bar backtest."""
        from src.backtest.engine import BacktestEngine
        from src.data.base import BaseDataProvider
        from src.portfolio.portfolio import Portfolio
        from src.risk.risk_manager import RiskManager
        from src.strategy.base import BaseStrategy, Signal, SignalDirection

        ts = pd.Timestamp("2024-01-15 10:05:00", tz="America/New_York")

        class _MockProvider(BaseDataProvider):
            def fetch_bars(self, symbol, start, end, interval):
                return pd.DataFrame(
                    [{"open": 470, "high": 475, "low": 469, "close": 472}],
                    index=pd.DatetimeIndex([ts], tz="America/New_York"),
                )

        class _AlwaysBuyStrategy(BaseStrategy):
            def reset(self): pass
            def generate_signal(self, symbol, bars, current_bar):
                return Signal(
                    symbol=symbol,
                    direction=SignalDirection.LONG,
                    entry_price=470.0,
                    stop_loss=460.0,
                    timestamp=current_bar,
                )

        portfolio = Portfolio(initial_capital=100_000)
        risk_mgr  = RiskManager()
        strategy  = _AlwaysBuyStrategy(params={})
        provider  = _MockProvider()

        engine = BacktestEngine(
            strategy=strategy,
            data_provider=provider,
            portfolio=portfolio,
            risk_manager=risk_mgr,
            symbols=["SPY"],
            start_date="2024-01-15",
            end_date="2024-01-15",
        )
        results = engine.run()
        return engine, results

    def test_intents_have_bt_ids(self):
        _, results = self._run_engine()
        intents = results["order_intents"]
        assert len(intents) > 0
        for oi in intents:
            assert oi.client_order_id is not None
            assert oi.client_order_id.startswith("BT-")

    def test_ids_are_sequential(self):
        _, results = self._run_engine()
        intents = results["order_intents"]
        seqs = [int(oi.client_order_id.split("-")[1]) for oi in intents]
        assert seqs == list(range(1, len(seqs) + 1))

    def test_ids_reset_per_run(self):
        engine, results1 = self._run_engine()
        # Re-run: IDs must start from BT-000001 again
        from src.portfolio.portfolio import Portfolio
        from src.risk.risk_manager import RiskManager
        from src.strategy.base import BaseStrategy, Signal, SignalDirection
        from src.data.base import BaseDataProvider

        ts = pd.Timestamp("2024-01-15 10:05:00", tz="America/New_York")

        class _MockProvider(BaseDataProvider):
            def fetch_bars(self, symbol, start, end, interval):
                return pd.DataFrame(
                    [{"open": 470, "high": 475, "low": 469, "close": 472}],
                    index=pd.DatetimeIndex([ts], tz="America/New_York"),
                )

        class _AlwaysBuyStrategy(BaseStrategy):
            def reset(self): pass
            def generate_signal(self, symbol, bars, current_bar):
                return Signal(symbol=symbol, direction=SignalDirection.LONG,
                              entry_price=470.0, stop_loss=460.0,
                              timestamp=current_bar)

        from src.backtest.engine import BacktestEngine
        engine2 = BacktestEngine(
            strategy=_AlwaysBuyStrategy(params={}),
            data_provider=_MockProvider(),
            portfolio=Portfolio(initial_capital=100_000),
            risk_manager=RiskManager(),
            symbols=["SPY"],
            start_date="2024-01-15",
            end_date="2024-01-15",
        )
        results2 = engine2.run()
        ids1 = [oi.client_order_id for oi in results1["order_intents"]]
        ids2 = [oi.client_order_id for oi in results2["order_intents"]]
        assert ids1 == ids2, f"IDs should reset: {ids1} vs {ids2}"


# ---------------------------------------------------------------------------
# ReportGenerator: CSV columns include client_order_id
# ---------------------------------------------------------------------------

class TestReportGeneratorClientOrderIdCsv:
    def _gen(self, intents, results=None):
        cfg = _minimal_app_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            rg = ReportGenerator(
                metrics={},
                trades=[],
                equity_curve=pd.DataFrame(),
                config=cfg,
                output_dir=tmpdir,
                order_intents=intents,
                order_results=results,
            )
            rg.generate_all()
            intents_df = pd.read_csv(Path(tmpdir) / "order_intents.csv")
            results_df = None
            if results is not None:
                results_df = pd.read_csv(Path(tmpdir) / "order_results.csv")
        return intents_df, results_df

    def test_order_intents_csv_has_client_order_id_column(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        df, _ = self._gen(intents)
        assert "client_order_id" in df.columns

    def test_order_intents_csv_client_order_id_value(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        df, _ = self._gen(intents)
        assert df["client_order_id"].iloc[0] == "BT-000001"

    def test_order_results_csv_has_client_order_id_column(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        results = [_make_result(client_order_id="BT-000001")]
        _, rdf = self._gen(intents, results)
        assert "client_order_id" in rdf.columns

    def test_order_results_csv_client_order_id_value(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        results = [_make_result(client_order_id="BT-000001")]
        _, rdf = self._gen(intents, results)
        assert rdf["client_order_id"].iloc[0] == "BT-000001"

    def test_empty_intents_csv_still_has_column(self):
        df, _ = self._gen([])
        assert "client_order_id" in df.columns


# ---------------------------------------------------------------------------
# Reconciliation: ID-based matching (order-independent)
# ---------------------------------------------------------------------------

class TestReconciliationIdBased:
    def _reconcile(self, intents, results):
        cfg = _minimal_app_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            rg = ReportGenerator(
                metrics={},
                trades=[],
                equity_curve=pd.DataFrame(),
                config=cfg,
                output_dir=tmpdir,
                order_intents=intents,
                order_results=results,
            )
            return rg._build_reconciliation()

    def test_pass_when_ids_match_in_order(self):
        intents = [_make_intent("SPY", "buy", 10.0, "entry", "BT-000001")]
        results = [_make_result("SPY", "buy", 10.0, "entry", "BT-000001")]
        rc = self._reconcile(intents, results)
        assert rc["overall_status"] == "PASS"
        assert rc["mismatch_count"] == 0
        assert rc["unmatched_count"] == 0

    def test_pass_when_results_shuffled(self):
        intents = [
            _make_intent("SPY", "buy",  10.0, "entry",     "BT-000001"),
            _make_intent("SPY", "sell", 10.0, "stop_loss", "BT-000002"),
        ]
        results = [
            _make_result("SPY", "sell", 10.0, "stop_loss", "BT-000002"),
            _make_result("SPY", "buy",  10.0, "entry",     "BT-000001"),
        ]
        rc = self._reconcile(intents, results)
        assert rc["overall_status"] == "PASS"
        assert rc["mismatch_count"] == 0
        assert rc["unmatched_count"] == 0

    def test_warn_when_field_mismatch(self):
        intents = [_make_intent("SPY", "buy", 10.0, "entry", "BT-000001")]
        results = [_make_result("SPY", "buy", 99.0, "entry", "BT-000001")]  # qty mismatch
        rc = self._reconcile(intents, results)
        assert rc["overall_status"] == "WARN"
        assert rc["mismatch_count"] == 1

    def test_warn_when_result_missing_id(self):
        intents = [_make_intent("SPY", "buy", 10.0, "entry", "BT-000001")]
        results = [_make_result("SPY", "buy", 10.0, "entry", client_order_id=None)]
        rc = self._reconcile(intents, results)
        assert rc["overall_status"] == "WARN"
        assert rc["missing_ids_warn"] is True

    def test_missing_ids_warn_false_when_all_ids_present(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        results = [_make_result(client_order_id="BT-000001")]
        rc = self._reconcile(intents, results)
        assert rc["missing_ids_warn"] is False

    def test_unmatched_count_when_extra_result(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        results = [
            _make_result(client_order_id="BT-000001"),
            _make_result("SPY", "sell", 10.0, "stop_loss", "BT-000002"),
        ]
        rc = self._reconcile(intents, results)
        assert rc["overall_status"] == "WARN"
        assert rc["unmatched_count"] == 1

    def test_positional_fallback_when_no_ids(self):
        intents = [_make_intent(client_order_id=None)]
        results = [_make_result(client_order_id=None)]
        rc = self._reconcile(intents, results)
        assert rc["overall_status"] == "PASS"
        assert rc["missing_ids_warn"] is False

    def test_warn_when_same_count_but_different_ids(self):
        # Same total count but one ID differs — unmatched_count > 0 → WARN
        intents = [
            _make_intent("SPY", "buy",  10.0, "entry",     "BT-000001"),
            _make_intent("SPY", "sell", 10.0, "stop_loss", "BT-000002"),
        ]
        results = [
            _make_result("SPY", "buy",  10.0, "entry",     "BT-000001"),
            _make_result("SPY", "sell", 10.0, "stop_loss", "BT-999999"),  # wrong ID
        ]
        rc = self._reconcile(intents, results)
        assert rc["unmatched_count"] > 0
        assert rc["overall_status"] == "WARN"

    def test_warn_when_intent_id_missing_from_results_same_count(self):
        # Both lists have length 2 but BT-000002 has no matching result ID
        intents = [
            _make_intent("SPY", "buy",  10.0, "entry",     "BT-000001"),
            _make_intent("SPY", "sell", 10.0, "stop_loss", "BT-000002"),
        ]
        results = [
            _make_result("SPY", "buy",  10.0, "entry",     "BT-000001"),
            _make_result("SPY", "sell", 10.0, "stop_loss", "BT-000003"),
        ]
        rc = self._reconcile(intents, results)
        assert rc["unmatched_count"] > 0
        assert rc["overall_status"] == "WARN"

    def test_reconciliation_json_has_missing_ids_warn(self):
        intents = [_make_intent(client_order_id="BT-000001")]
        results = [_make_result(client_order_id=None)]
        cfg = _minimal_app_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            rg = ReportGenerator(
                metrics={},
                trades=[],
                equity_curve=pd.DataFrame(),
                config=cfg,
                output_dir=tmpdir,
                order_intents=intents,
                order_results=results,
            )
            rg.generate_all()
            with open(Path(tmpdir) / "order_reconciliation.json") as fh:
                data = json.load(fh)
        assert "missing_ids_warn" in data
        assert data["missing_ids_warn"] is True
