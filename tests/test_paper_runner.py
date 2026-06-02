"""
tests/test_paper_runner.py
---------------------------
Tests for src/execution/paper_runner.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted in preview mode, no live ledger writes.
The conftest auto-patches paper_ledger, paper_daily_limits,
paper_open_order_guard, paper_market_hours_guard, paper_kill_switch
for all tests in this file.
"""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

from src.config.loader import load_config, AppConfig
from src.execution.broker import OrderResult
from src.execution.order_intent import OrderIntent
from src.execution.paper_runner import PaperRunResult, run_paper_execution


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _default_cfg() -> AppConfig:
    """Return a fresh default AppConfig."""
    return load_config(None)


def _paper_cfg(
    *,
    preview_only: bool = True,
    selected_coid: str | None = None,
    qty_override: float | None = None,
    require_market_hours: bool = False,
    block_if_open_orders: bool = False,
    kill_switch_enabled: bool = False,
    daily_max_notional: float | None = None,
) -> AppConfig:
    """Return a paper-mode AppConfig suitable for testing."""
    cfg = _default_cfg()
    cfg.execution.mode = "paper"
    cfg.execution.paper_trading_enabled = True
    cfg.execution.paper_preview_only = preview_only
    cfg.execution.paper_selected_client_order_id = selected_coid
    cfg.execution.paper_order_quantity_override = qty_override
    cfg.execution.paper_require_market_hours = require_market_hours
    cfg.execution.paper_block_if_open_orders = block_if_open_orders
    cfg.execution.paper_kill_switch_enabled = kill_switch_enabled
    cfg.execution.paper_daily_max_notional = daily_max_notional
    cfg.execution.paper_poll_order_status = False
    return cfg


def _make_intent(
    symbol: str = "SPY",
    side: str = "buy",
    quantity: float = 1.0,
    order_type: str = "market",
    coid: str = "BC-TEST-001",
    reason: str = "test_reason",
) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        reason=reason,
        timestamp=pd.Timestamp("2025-01-02 10:00:00", tz="America/New_York"),
        client_order_id=coid,
        metadata={"entry_price": 450.0},
    )


def _make_order_result(intent: OrderIntent) -> OrderResult:
    return OrderResult(
        order_id="FAKE-000001",
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        status="accepted",
        submitted_at=intent.timestamp,
        reason=intent.reason,
        client_order_id=intent.client_order_id,
    )


def _make_backtest_result(intents: list[OrderIntent] | None = None):
    """Minimal BacktestRunResult-like object."""
    from src.backtest.backtest_runner import BacktestRunResult
    return BacktestRunResult(
        metrics={
            "num_trades": 0,
            "initial_capital": 100_000.0,
            "final_equity": 100_000.0,
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate_pct": 0.0,
            "avg_winning_trade": 0.0,
            "avg_losing_trade": 0.0,
            "total_commission": 0.0,
        },
        trades=[],
        equity_curve=pd.DataFrame({"equity": []}, index=pd.DatetimeIndex([])),
        order_intents=intents or [],
        strategy_name="opening_range_breakout",
        symbols=["SPY"],
        bar_interval="5m",
    )


def _make_mock_broker(
    positions: dict | None = None,
    submit_result: OrderResult | None = None,
) -> MagicMock:
    """Build a mock broker with preflight_check and submit_order."""
    broker = MagicMock()
    broker.preflight_check.return_value = {
        "account": {"status": "ACTIVE"},
        "symbols": ["SPY"],
        "positions": positions or {},
    }
    if submit_result is not None:
        broker.submit_order.return_value = submit_result
    return broker


def _make_mock_report_generator(output_dir: Path) -> MagicMock:
    """Return a mock ReportGenerator that writes reconciliation JSON on generate_all()."""
    mock_reporter = MagicMock()

    def _write_recon(**kw):
        recon_path = output_dir / "order_reconciliation.json"
        recon_path.write_text(json.dumps({"overall_status": "PASS"}))

    mock_reporter.generate_all.side_effect = _write_recon
    return mock_reporter


# ---------------------------------------------------------------------------
# 1. TestPaperRunResultDataclass
# ---------------------------------------------------------------------------


class TestPaperRunResultDataclass:
    """Verify frozen dataclass fields exist and types are as specified."""

    def test_is_frozen_dataclass(self) -> None:
        result = PaperRunResult(
            result="PREVIEW_COMPLETE",
            blocker=None,
            mode="preview",
            preview_only=True,
            orders_submitted=0,
            intents_generated=0,
            ledger_rows_written=0,
            output_dir=None,
            broker_calls_made=True,
            credentials_read=False,
            order_action_requested=False,
            network_calls_made=False,
        )
        # Normal attribute assignment must raise FrozenInstanceError (subclass of AttributeError)
        with pytest.raises((AttributeError, TypeError)):
            result.result = "X"  # type: ignore[misc]

    def test_has_result_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "result" in field_names

    def test_has_blocker_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "blocker" in field_names

    def test_has_mode_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "mode" in field_names

    def test_has_preview_only_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "preview_only" in field_names

    def test_has_orders_submitted_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "orders_submitted" in field_names

    def test_has_intents_generated_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "intents_generated" in field_names

    def test_has_ledger_rows_written_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "ledger_rows_written" in field_names

    def test_has_output_dir_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "output_dir" in field_names

    def test_has_broker_calls_made_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "broker_calls_made" in field_names

    def test_has_credentials_read_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "credentials_read" in field_names

    def test_has_order_action_requested_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "order_action_requested" in field_names

    def test_has_network_calls_made_field(self) -> None:
        field_names = {f.name for f in fields(PaperRunResult)}
        assert "network_calls_made" in field_names

    def test_result_field_accepts_preview_complete(self) -> None:
        r = PaperRunResult(
            result="PREVIEW_COMPLETE",
            blocker=None,
            mode="preview",
            preview_only=True,
            orders_submitted=0,
            intents_generated=2,
            ledger_rows_written=0,
            output_dir="/tmp",
            broker_calls_made=True,
            credentials_read=False,
            order_action_requested=False,
            network_calls_made=False,
        )
        assert r.result == "PREVIEW_COMPLETE"

    def test_result_field_accepts_submit_complete(self) -> None:
        r = PaperRunResult(
            result="SUBMIT_COMPLETE",
            blocker=None,
            mode="submit",
            preview_only=False,
            orders_submitted=1,
            intents_generated=2,
            ledger_rows_written=1,
            output_dir="/tmp",
            broker_calls_made=True,
            credentials_read=False,
            order_action_requested=True,
            network_calls_made=False,
        )
        assert r.result == "SUBMIT_COMPLETE"


# ---------------------------------------------------------------------------
# 2. TestPreviewMode
# ---------------------------------------------------------------------------


class TestPreviewMode:
    """Preview path does not submit, writes candidate CSV, returns correct PaperRunResult."""

    def test_preview_returns_preview_complete(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent()
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        assert result.result == "PREVIEW_COMPLETE"

    def test_preview_does_not_call_submit_order(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent()
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_preview_writes_candidate_csv(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-TEST-001")
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        cand_csv = tmp_path / "paper_candidate_intents.csv"
        assert cand_csv.exists()
        df = pd.read_csv(cand_csv)
        assert len(df) == 1
        assert df.iloc[0]["client_order_id"] == "BC-TEST-001"

    def test_preview_intents_generated_matches_count(self, monkeypatch, tmp_path) -> None:
        intents = [_make_intent(coid=f"BC-{i}") for i in range(3)]
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result(intents),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        assert result.intents_generated == 3

    def test_preview_mode_field_is_preview(self, monkeypatch, tmp_path) -> None:
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        assert result.mode == "preview"

    def test_preview_calls_preflight_check(self, monkeypatch, tmp_path) -> None:
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.preflight_check.assert_called_once()

    def test_preview_output_dir_in_result(self, monkeypatch, tmp_path) -> None:
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        assert result.output_dir == str(tmp_path)


# ---------------------------------------------------------------------------
# 3. TestSubmitMode
# ---------------------------------------------------------------------------


class TestSubmitMode:
    """Submit path calls submit_order once, writes audit CSV, appends ledger row."""

    def _setup_submit(self, monkeypatch, tmp_path, intent=None, extra_positions=None):
        if intent is None:
            intent = _make_intent(coid="BC-SUBMIT-001")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result, positions=extra_positions or {})
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        return cfg, broker, intent, order_result

    def test_submit_returns_submit_complete(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        assert result.result == "SUBMIT_COMPLETE"

    def test_submit_calls_submit_order_once(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        broker.submit_order.assert_called_once()

    def test_submit_writes_audit_csv(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        audit_csv = tmp_path / "paper_intent_audit.csv"
        assert audit_csv.exists()
        df = pd.read_csv(audit_csv)
        assert len(df) == 1
        assert df.iloc[0]["client_order_id"] == intent.client_order_id

    def test_submit_writes_candidate_csv(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        cand_csv = tmp_path / "paper_candidate_intents.csv"
        assert cand_csv.exists()

    def test_submit_appends_ledger_row(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        append_mock = MagicMock()

        with patch("src.execution.paper_ledger.append_ledger_row", append_mock):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        append_mock.assert_called_once()

    def test_submit_orders_submitted_is_1(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        assert result.orders_submitted == 1

    def test_submit_ledger_rows_written_is_1(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        assert result.ledger_rows_written == 1

    def test_submit_mode_field_is_submit(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        assert result.mode == "submit"

    def test_submit_intents_generated_matches(self, monkeypatch, tmp_path) -> None:
        cfg, broker, intent, _ = self._setup_submit(monkeypatch, tmp_path)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        assert result.intents_generated == 1


def _make_mock_reporter_with_recon(output_dir: Path) -> MagicMock:
    """Return a mock ReportGenerator that writes reconciliation JSON on generate_all()."""
    mock_reporter = MagicMock()

    def _write_recon(**kw):
        recon_path = output_dir / "order_reconciliation.json"
        recon_path.write_text(json.dumps({"overall_status": "PASS"}))

    mock_reporter.generate_all.side_effect = _write_recon
    return mock_reporter


# ---------------------------------------------------------------------------
# 4. TestPreviewSafetyFlags
# ---------------------------------------------------------------------------


class TestPreviewSafetyFlags:
    """Preview result has correct safety flag values."""

    def _run_preview(self, monkeypatch, tmp_path) -> PaperRunResult:
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        return run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

    def test_order_action_requested_is_false(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.order_action_requested is False

    def test_orders_submitted_is_zero(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.orders_submitted == 0

    def test_preview_only_is_true(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.preview_only is True

    def test_mode_is_preview(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.mode == "preview"

    def test_credentials_read_is_false(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.credentials_read is False

    def test_broker_calls_made_is_true(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.broker_calls_made is True

    def test_network_calls_made_is_false(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.network_calls_made is False

    def test_ledger_rows_written_is_zero(self, monkeypatch, tmp_path) -> None:
        result = self._run_preview(monkeypatch, tmp_path)
        assert result.ledger_rows_written == 0


# ---------------------------------------------------------------------------
# 5. TestSubmitSafetyFlags
# ---------------------------------------------------------------------------


class TestSubmitSafetyFlags:
    """Submit result has correct safety flag values."""

    def _run_submit(self, monkeypatch, tmp_path) -> PaperRunResult:
        intent = _make_intent(coid="BC-SUBMIT-SAFE-001")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        return run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

    def test_order_action_requested_is_true(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.order_action_requested is True

    def test_orders_submitted_is_1(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.orders_submitted == 1

    def test_preview_only_is_false(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.preview_only is False

    def test_mode_is_submit(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.mode == "submit"

    def test_credentials_read_is_false(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.credentials_read is False

    def test_broker_calls_made_is_true(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.broker_calls_made is True

    def test_network_calls_made_is_false(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.network_calls_made is False

    def test_ledger_rows_written_is_1(self, monkeypatch, tmp_path) -> None:
        result = self._run_submit(monkeypatch, tmp_path)
        assert result.ledger_rows_written == 1


# ---------------------------------------------------------------------------
# 6. TestSafetyConstraints
# ---------------------------------------------------------------------------


class TestSafetyConstraints:
    """Safety constraint violations raise RuntimeError before submit_order."""

    def _make_submit_cfg_and_broker(self, intent: OrderIntent, tmp_path: Path):
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        return cfg, broker

    def test_wrong_symbol_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(symbol="QQQ", coid="BC-BAD-SYM")
        cfg, broker = self._make_submit_cfg_and_broker(intent, tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        with pytest.raises(RuntimeError, match="symbol"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_wrong_order_type_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(order_type="limit", coid="BC-BAD-TYPE")
        cfg, broker = self._make_submit_cfg_and_broker(intent, tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        with pytest.raises(RuntimeError, match="order_type"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_qty_greater_than_1_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(quantity=2.0, coid="BC-BAD-QTY")
        cfg, broker = self._make_submit_cfg_and_broker(intent, tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        with pytest.raises(RuntimeError, match="quantity"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_missing_coid_in_submit_mode_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-EXISTING")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        # Config with empty selected COID
        cfg = _paper_cfg(preview_only=False, selected_coid=None)

        with pytest.raises(RuntimeError, match="paper_selected_client_order_id"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_blank_coid_in_submit_mode_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-EXISTING")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(preview_only=False, selected_coid="   ")

        with pytest.raises(RuntimeError, match="paper_selected_client_order_id"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_coid_not_found_in_candidates_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-REAL-001")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(preview_only=False, selected_coid="BC-NONEXISTENT-999")

        with pytest.raises(RuntimeError, match="no intent found"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_coid_not_found_error_lists_available(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-REAL-001")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(preview_only=False, selected_coid="BC-NONEXISTENT-999")

        with pytest.raises(RuntimeError, match="BC-REAL-001"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())


# ---------------------------------------------------------------------------
# 7. TestPositionSafety
# ---------------------------------------------------------------------------


class TestPositionSafety:
    """Existing position blocks buy; sell is not blocked."""

    def test_existing_spy_position_blocks_buy(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(side="buy", coid="BC-BUY-BLOCKED")
        # Existing SPY position with qty=1
        broker = _make_mock_broker(
            positions={"SPY": {"qty": 1, "symbol": "SPY"}},
            submit_result=_make_order_result(intent),
        )

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)

        with pytest.raises(RuntimeError, match="existing.*position"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_no_position_allows_buy(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(side="buy", coid="BC-BUY-OK")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(positions={}, submit_result=order_result)
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_called_once()
        assert result.orders_submitted == 1

    def test_sell_not_blocked_by_existing_position(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(side="sell", coid="BC-SELL-OK")
        order_result = _make_order_result(intent)
        # Position exists — sell should not be blocked
        broker = _make_mock_broker(
            positions={"SPY": {"qty": 1, "symbol": "SPY"}},
            submit_result=order_result,
        )
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_called_once()
        assert result.orders_submitted == 1

    def test_existing_position_qty_zero_allows_buy(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(side="buy", coid="BC-BUY-QTY0")
        order_result = _make_order_result(intent)
        # Position with qty=0 (flat) — buy should be allowed
        broker = _make_mock_broker(
            positions={"SPY": {"qty": 0, "symbol": "SPY"}},
            submit_result=order_result,
        )
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_called_once()


# ---------------------------------------------------------------------------
# 8. TestQuantityOverride
# ---------------------------------------------------------------------------


class TestQuantityOverride:
    """Quantity override validation."""

    def _run_with_override(self, monkeypatch, tmp_path, qty_override) -> None:
        intent = _make_intent(coid="BC-OVERRIDE-001")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            qty_override=qty_override,
        )
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

    def test_valid_override_1_0_accepted(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-OVERRIDE-1-OK")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            qty_override=1.0,
        )
        result = run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())
        assert result.orders_submitted == 1

    def test_override_greater_than_1_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-OVERRIDE-2-BAD")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            qty_override=2.0,
        )

        with pytest.raises(RuntimeError, match="<= 1"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_override_less_than_or_equal_to_0_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-OVERRIDE-NEG")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            qty_override=-0.5,
        )

        with pytest.raises(RuntimeError, match="> 0"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_override_not_exactly_1_raises(self, monkeypatch, tmp_path) -> None:
        """0.5 is <= 1 but != 1.0 — must raise."""
        intent = _make_intent(coid="BC-OVERRIDE-HALF")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            qty_override=0.5,
        )

        with pytest.raises(RuntimeError, match="exactly 1.0"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_override_zero_raises(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-OVERRIDE-ZERO")
        broker = _make_mock_broker()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            qty_override=0.0,
        )

        with pytest.raises(RuntimeError, match="> 0"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# 9. TestGuardDelegation
# ---------------------------------------------------------------------------


class TestGuardDelegation:
    """Each guard can be made to raise via monkeypatching; errors propagate."""

    def _make_valid_submit_cfg_and_broker(self, intent: OrderIntent, tmp_path: Path):
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        cfg = _paper_cfg(
            preview_only=False,
            selected_coid=intent.client_order_id,
            require_market_hours=True,
            block_if_open_orders=True,
            kill_switch_enabled=False,
        )
        return cfg, broker

    def test_kill_switch_raises_propagates(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-KS-TEST")
        cfg, broker = self._make_valid_submit_cfg_and_broker(intent, tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        def _ks_raise(enabled):
            raise RuntimeError("Kill switch is active")

        monkeypatch.setattr(
            "src.execution.paper_kill_switch.assert_kill_switch_disabled",
            _ks_raise,
        )

        with pytest.raises(RuntimeError, match="Kill switch"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_market_hours_guard_raises_propagates(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-MH-TEST")
        cfg, broker = self._make_valid_submit_cfg_and_broker(intent, tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        def _mh_raise():
            raise RuntimeError("Outside market hours")

        monkeypatch.setattr(
            "src.execution.paper_market_hours_guard.assert_regular_market_hours",
            _mh_raise,
        )

        with pytest.raises(RuntimeError, match="market hours"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_daily_limits_guard_raises_propagates(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-DL-TEST")
        cfg, broker = self._make_valid_submit_cfg_and_broker(intent, tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        def _dl_raise(**kw):
            raise RuntimeError("Daily limit exceeded")

        monkeypatch.setattr(
            "src.execution.paper_daily_limits.assert_within_daily_limits",
            _dl_raise,
        )

        with pytest.raises(RuntimeError, match="Daily limit"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()

    def test_open_orders_guard_raises_propagates(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-OO-TEST")
        cfg, broker = self._make_valid_submit_cfg_and_broker(intent, tmp_path)
        cfg.execution.paper_block_if_open_orders = True

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        def _oo_raise(client, symbol):
            raise RuntimeError("Open orders detected")

        monkeypatch.setattr(
            "src.execution.paper_open_order_guard.assert_no_open_orders_for_symbol",
            _oo_raise,
        )

        with pytest.raises(RuntimeError, match="Open orders"):
            run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        broker.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# 10. TestOutputArtifacts
# ---------------------------------------------------------------------------


class TestOutputArtifacts:
    """Output artifact writes behave correctly in preview and submit modes."""

    def test_submit_writes_paper_intent_audit_csv(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-AUDIT-001")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        audit_csv = tmp_path / "paper_intent_audit.csv"
        assert audit_csv.exists()
        df = pd.read_csv(audit_csv)
        assert "client_order_id" in df.columns
        assert "symbol" in df.columns
        assert "side" in df.columns
        assert "override_applied" in df.columns

    def test_preview_writes_paper_candidate_intents_csv(self, monkeypatch, tmp_path) -> None:
        intents = [_make_intent(coid=f"BC-CAND-{i}") for i in range(2)]
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result(intents),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        cand_csv = tmp_path / "paper_candidate_intents.csv"
        assert cand_csv.exists()
        df = pd.read_csv(cand_csv)
        assert len(df) == 2

    def test_submit_with_no_output_dir_skips_file_writes(self, monkeypatch) -> None:
        """output_dir=None: no file writes, but still appends ledger and returns SUBMIT_COMPLETE."""
        intent = _make_intent(coid="BC-NO-DIR-001")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        append_mock = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )

        with patch("src.execution.paper_ledger.append_ledger_row", append_mock):
            cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
            result = run_paper_execution(
                cfg, output_dir=None, _broker=broker, _data_provider=MagicMock()
            )

        assert result.result == "SUBMIT_COMPLETE"
        append_mock.assert_called_once()
        assert result.output_dir is None

    def test_preview_candidate_csv_has_correct_columns(self, monkeypatch, tmp_path) -> None:
        intent = _make_intent(coid="BC-COL-TEST")
        broker = _make_mock_broker()
        mock_reporter = MagicMock()

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=True)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        df = pd.read_csv(tmp_path / "paper_candidate_intents.csv")
        expected_cols = {"client_order_id", "timestamp", "symbol", "side", "quantity", "order_type", "reason"}
        assert expected_cols.issubset(set(df.columns))

    def test_submit_also_writes_candidate_csv(self, monkeypatch, tmp_path) -> None:
        """Candidate CSV must be written even in submit mode (before submit decision)."""
        intent = _make_intent(coid="BC-CAND-SUBMIT")
        order_result = _make_order_result(intent)
        broker = _make_mock_broker(submit_result=order_result)
        mock_reporter = _make_mock_reporter_with_recon(tmp_path)

        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: mock_reporter,
        )

        cfg = _paper_cfg(preview_only=False, selected_coid=intent.client_order_id)
        run_paper_execution(cfg, output_dir=tmp_path, _broker=broker, _data_provider=MagicMock())

        assert (tmp_path / "paper_candidate_intents.csv").exists()


# ---------------------------------------------------------------------------
# 11. TestMainDelegation
# ---------------------------------------------------------------------------


class TestMainDelegation:
    """main() with paper mode delegates to run_paper_execution."""

    def test_paper_preview_delegates_to_run_paper_execution(self, monkeypatch, tmp_path) -> None:
        import src.main as _main_mod
        import src.execution.paper_runner as _runner_mod

        cfg = _default_cfg()
        cfg.execution.mode = "paper"
        cfg.execution.paper_trading_enabled = True
        cfg.execution.paper_preview_only = True
        cfg.execution.paper_close_positions_enabled = False

        calls = []

        def _fake_run(c, *, output_dir=None, **kw):
            calls.append(True)
            return PaperRunResult(
                result="PREVIEW_COMPLETE",
                blocker=None,
                mode="preview",
                preview_only=True,
                orders_submitted=0,
                intents_generated=0,
                ledger_rows_written=0,
                output_dir=str(output_dir) if output_dir else None,
                broker_calls_made=True,
                credentials_read=False,
                order_action_requested=False,
                network_calls_made=False,
            )

        monkeypatch.setattr(sys, "argv", ["src.main", "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config", lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
        monkeypatch.setattr(_runner_mod, "run_paper_execution", _fake_run)

        _main_mod.main()
        assert len(calls) == 1

    def test_paper_submit_delegates_to_run_paper_execution(self, monkeypatch, tmp_path) -> None:
        import src.main as _main_mod
        import src.execution.paper_runner as _runner_mod

        cfg = _default_cfg()
        cfg.execution.mode = "paper"
        cfg.execution.paper_trading_enabled = True
        cfg.execution.paper_preview_only = False
        cfg.execution.paper_selected_client_order_id = "BC-TEST-999"
        cfg.execution.paper_close_positions_enabled = False

        calls = []

        def _fake_run(c, *, output_dir=None, **kw):
            calls.append(True)
            return PaperRunResult(
                result="SUBMIT_COMPLETE",
                blocker=None,
                mode="submit",
                preview_only=False,
                orders_submitted=1,
                intents_generated=1,
                ledger_rows_written=1,
                output_dir=str(output_dir) if output_dir else None,
                broker_calls_made=True,
                credentials_read=False,
                order_action_requested=True,
                network_calls_made=False,
            )

        monkeypatch.setattr(sys, "argv", ["src.main", "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config", lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
        monkeypatch.setattr(_runner_mod, "run_paper_execution", _fake_run)

        _main_mod.main()
        assert len(calls) == 1

    def test_paper_close_path_does_not_call_run_paper_execution(self, monkeypatch, tmp_path) -> None:
        """When paper_close_positions_enabled=True, run_paper_execution must NOT be called."""
        import src.main as _main_mod
        import src.execution.paper_runner as _runner_mod
        import src.execution.paper_close_runner as _close_runner_mod
        from src.execution.paper_close_runner import PaperCloseRunResult

        cfg = _default_cfg()
        cfg.execution.mode = "paper"
        cfg.execution.paper_trading_enabled = True
        cfg.execution.paper_close_positions_enabled = True
        cfg.execution.paper_close_preview_only = True

        runner_calls = []

        def _fake_run(c, *, output_dir=None, **kw):
            runner_calls.append(True)
            return PaperRunResult(
                result="PREVIEW_COMPLETE",
                blocker=None,
                mode="preview",
                preview_only=True,
                orders_submitted=0,
                intents_generated=0,
                ledger_rows_written=0,
                output_dir=None,
                broker_calls_made=True,
                credentials_read=False,
                order_action_requested=False,
                network_calls_made=False,
            )

        close_calls = []

        def _fake_close(config, *, output_dir=None, **kw):
            close_calls.append(True)
            return PaperCloseRunResult(
                result="PREVIEW_COMPLETE",
                blocker=None,
                mode="preview",
                preview_only=True,
                close_candidates_generated=0,
                orders_submitted=0,
                ledger_rows_written=0,
                output_dir=str(output_dir) if output_dir else None,
                broker_calls_made=True,
                credentials_read=False,
                order_action_requested=False,
                network_calls_made=False,
            )

        monkeypatch.setattr(sys, "argv", ["src.main", "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config", lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)
        monkeypatch.setattr(_runner_mod, "run_paper_execution", _fake_run)
        monkeypatch.setattr(_close_runner_mod, "run_paper_close", _fake_close)

        _main_mod.main()
        assert runner_calls == []  # run_paper_execution must NOT be called
        assert close_calls == [True]  # run_paper_close IS called

    def test_paper_gate_not_enabled_raises_not_implemented(self, monkeypatch, tmp_path) -> None:
        import src.main as _main_mod

        cfg = _default_cfg()
        cfg.execution.mode = "paper"
        cfg.execution.paper_trading_enabled = False  # gate disabled

        monkeypatch.setattr(sys, "argv", ["src.main", "--output-dir", str(tmp_path)])
        monkeypatch.setattr(_main_mod, "load_config", lambda _: cfg)
        monkeypatch.setattr(_main_mod, "configure_logging", lambda **kw: None)

        with pytest.raises(NotImplementedError):
            _main_mod.main()


# ---------------------------------------------------------------------------
# 12. TestDefaultBrokerPath
# ---------------------------------------------------------------------------


class TestDefaultBrokerPath:
    """When _broker=None, credentials_read and network_calls_made are True."""

    def _run_preview_default_broker(self, monkeypatch, tmp_path) -> PaperRunResult:
        import src.execution.alpaca_broker as _ab

        intent = _make_intent(coid="BC-DEFAULT-BROKER-001")
        mock_broker = _make_mock_broker()

        monkeypatch.setattr(_ab, "AlpacaBrokerAdapter", lambda: mock_broker)
        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: MagicMock(),
        )

        cfg = _paper_cfg(preview_only=True)
        # _broker not passed — uses default AlpacaBrokerAdapter path
        return run_paper_execution(cfg, output_dir=None, _data_provider=MagicMock())

    def test_credentials_read_true_on_default_broker_path_preview(
        self, monkeypatch, tmp_path
    ) -> None:
        result = self._run_preview_default_broker(monkeypatch, tmp_path)
        assert result.credentials_read is True

    def test_network_calls_made_true_on_default_broker_path_preview(
        self, monkeypatch, tmp_path
    ) -> None:
        result = self._run_preview_default_broker(monkeypatch, tmp_path)
        assert result.network_calls_made is True

    def test_credentials_read_false_when_broker_injected(self, monkeypatch) -> None:
        intent = _make_intent(coid="BC-INJECTED-001")
        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: MagicMock(),
        )
        cfg = _paper_cfg(preview_only=True)
        broker = _make_mock_broker()
        result = run_paper_execution(
            cfg, output_dir=None, _broker=broker, _data_provider=MagicMock()
        )
        assert result.credentials_read is False

    def test_network_calls_made_false_when_broker_injected(self, monkeypatch) -> None:
        intent = _make_intent(coid="BC-INJECTED-002")
        monkeypatch.setattr(
            "src.backtest.backtest_runner.run_backtest",
            lambda config, *, data_provider: _make_backtest_result([intent]),
        )
        monkeypatch.setattr(
            "src.reporting.report_generator.ReportGenerator",
            lambda **kw: MagicMock(),
        )
        cfg = _paper_cfg(preview_only=True)
        broker = _make_mock_broker()
        result = run_paper_execution(
            cfg, output_dir=None, _broker=broker, _data_provider=MagicMock()
        )
        assert result.network_calls_made is False
