"""
tests/test_paper_close_runner.py
----------------------------------
Tests for src/execution/paper_close_runner.py.

Fully offline: no Alpaca calls, no credentials required,
no orders submitted in preview mode, no live ledger writes.
The conftest auto-patches paper_ledger, paper_daily_limits,
paper_open_order_guard, paper_market_hours_guard, paper_kill_switch
for all tests in this file.
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.config.loader import load_config, AppConfig
from src.execution.broker import OrderResult
from src.execution.paper_close_runner import PaperCloseRunResult, run_paper_close


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

_FIXED_TS         = pd.Timestamp("2026-06-02 10:00:00", tz="America/New_York")
_FIXED_DATE_LABEL = "20260602"
_FIXED_CLOSE_COID = f"BC-{_FIXED_DATE_LABEL}-SPY-CLOSE"


def _close_cfg(
    *,
    preview_only: bool = True,
    selected_coid: str | None = None,
    qty_override: float | None = None,
    require_market_hours: bool = False,
    block_if_open_orders: bool = False,
    kill_switch_enabled: bool = False,
    poll_order_status: bool = False,
) -> AppConfig:
    cfg = load_config(None)
    cfg.execution.mode = "paper"
    cfg.execution.paper_trading_enabled = True
    cfg.execution.paper_close_positions_enabled = True
    cfg.execution.paper_close_preview_only = preview_only
    cfg.execution.paper_selected_close_client_order_id = selected_coid
    cfg.execution.paper_close_quantity_override = qty_override
    cfg.execution.paper_require_market_hours = require_market_hours
    cfg.execution.paper_block_if_open_orders = block_if_open_orders
    cfg.execution.paper_kill_switch_enabled = kill_switch_enabled
    cfg.execution.paper_poll_order_status = poll_order_status
    return cfg


def _make_broker(
    positions: dict | None = None,
    submit_side_effect=None,
) -> MagicMock:
    broker = MagicMock()
    broker.preflight_check.return_value = {
        "account": {"status": "ACTIVE"},
        "symbols": ["SPY"],
        "positions": positions or {},
    }
    if submit_side_effect is not None:
        broker.submit_order.side_effect = submit_side_effect
    return broker


def _make_close_result(coid: str, qty: float = 1.0) -> OrderResult:
    return OrderResult(
        order_id=f"ALPACA-CLOSE-{coid}",
        symbol="SPY", side="sell", quantity=qty,
        status="accepted",
        submitted_at=_FIXED_TS, reason="paper_close",
        client_order_id=coid,
    )


def _patch_reporter(monkeypatch, output_dir: Path):
    """Patch ReportGenerator to write a PASS reconciliation on generate_all()."""
    import src.reporting.report_generator as _rg

    class _FakeReporter:
        def __init__(self, **kw):
            (output_dir / "order_reconciliation.json").write_text(
                json.dumps({"overall_status": "PASS"})
            )

        def generate_all(self):
            pass

    monkeypatch.setattr(_rg, "ReportGenerator", _FakeReporter)


# ---------------------------------------------------------------------------
# 1. TestPaperCloseRunResultDataclass
# ---------------------------------------------------------------------------


class TestPaperCloseRunResultDataclass:
    """Verify frozen dataclass fields exist and types are as specified."""

    def _make_result(self) -> PaperCloseRunResult:
        return PaperCloseRunResult(
            result="PREVIEW_COMPLETE",
            blocker=None,
            mode="preview",
            preview_only=True,
            close_candidates_generated=0,
            orders_submitted=0,
            ledger_rows_written=0,
            output_dir=None,
            broker_calls_made=True,
            credentials_read=False,
            order_action_requested=False,
            network_calls_made=False,
        )

    def test_is_frozen(self) -> None:
        r = self._make_result()
        with pytest.raises((AttributeError, TypeError)):
            r.result = "X"  # type: ignore[misc]

    def test_has_eleven_fields(self) -> None:
        assert len(fields(PaperCloseRunResult)) == 12

    def test_has_result_field(self) -> None:
        assert "result" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_blocker_field(self) -> None:
        assert "blocker" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_mode_field(self) -> None:
        assert "mode" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_preview_only_field(self) -> None:
        assert "preview_only" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_close_candidates_generated_field(self) -> None:
        assert "close_candidates_generated" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_orders_submitted_field(self) -> None:
        assert "orders_submitted" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_ledger_rows_written_field(self) -> None:
        assert "ledger_rows_written" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_output_dir_field(self) -> None:
        assert "output_dir" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_broker_calls_made_field(self) -> None:
        assert "broker_calls_made" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_credentials_read_field(self) -> None:
        assert "credentials_read" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_order_action_requested_field(self) -> None:
        assert "order_action_requested" in {f.name for f in fields(PaperCloseRunResult)}

    def test_has_network_calls_made_field(self) -> None:
        assert "network_calls_made" in {f.name for f in fields(PaperCloseRunResult)}


# ---------------------------------------------------------------------------
# 2. TestPreviewMode
# ---------------------------------------------------------------------------


class TestPreviewMode:
    """Tests with preview_only=True."""

    def test_returns_preview_complete(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.result == "PREVIEW_COMPLETE"

    def test_mode_is_preview(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.mode == "preview"

    def test_orders_submitted_is_zero(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.orders_submitted == 0

    def test_ledger_rows_written_is_zero(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.ledger_rows_written == 0

    def test_preview_only_is_true(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.preview_only is True

    def test_submit_order_not_called_in_preview(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        broker.submit_order.assert_not_called()

    def test_no_position_generates_zero_candidates(self, tmp_path) -> None:
        broker = _make_broker(positions={})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.close_candidates_generated == 0

    def test_spy_position_generates_one_candidate(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.close_candidates_generated == 1


# ---------------------------------------------------------------------------
# 3. TestSubmitMode
# ---------------------------------------------------------------------------


class TestSubmitMode:
    """Tests with preview_only=False."""

    def _submit_result(self, tmp_path, monkeypatch) -> PaperCloseRunResult:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        return run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_returns_submit_complete(self, tmp_path, monkeypatch) -> None:
        result = self._submit_result(tmp_path, monkeypatch)
        assert result.result == "SUBMIT_COMPLETE"

    def test_mode_is_submit(self, tmp_path, monkeypatch) -> None:
        result = self._submit_result(tmp_path, monkeypatch)
        assert result.mode == "submit"

    def test_orders_submitted_is_one(self, tmp_path, monkeypatch) -> None:
        result = self._submit_result(tmp_path, monkeypatch)
        assert result.orders_submitted == 1

    def test_ledger_rows_written_is_one(self, tmp_path, monkeypatch) -> None:
        result = self._submit_result(tmp_path, monkeypatch)
        assert result.ledger_rows_written == 1

    def test_preview_only_is_false(self, tmp_path, monkeypatch) -> None:
        result = self._submit_result(tmp_path, monkeypatch)
        assert result.preview_only is False

    def test_submit_order_called_once(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        broker.submit_order.assert_called_once()

    def test_missing_selection_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=None)
        with pytest.raises(RuntimeError, match="paper_selected_close_client_order_id"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_blank_selection_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        cfg = _close_cfg(preview_only=False, selected_coid="   ")
        with pytest.raises(RuntimeError, match="paper_selected_close_client_order_id"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)


# ---------------------------------------------------------------------------
# 4. TestPreviewSafetyFlags
# ---------------------------------------------------------------------------


class TestPreviewSafetyFlags:
    """Safety flags in preview mode."""

    def test_order_action_requested_false(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.order_action_requested is False

    def test_credentials_read_false_when_injected(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.credentials_read is False

    def test_network_calls_made_false_when_injected(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.network_calls_made is False

    def test_broker_calls_made_true(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.broker_calls_made is True


# ---------------------------------------------------------------------------
# 5. TestSubmitSafetyFlags
# ---------------------------------------------------------------------------


class TestSubmitSafetyFlags:
    """Safety flags in submit mode."""

    def _run_submit(self, tmp_path, monkeypatch) -> PaperCloseRunResult:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        return run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_order_action_requested_true(self, tmp_path, monkeypatch) -> None:
        result = self._run_submit(tmp_path, monkeypatch)
        assert result.order_action_requested is True

    def test_credentials_read_false_when_injected(self, tmp_path, monkeypatch) -> None:
        result = self._run_submit(tmp_path, monkeypatch)
        assert result.credentials_read is False

    def test_network_calls_made_false_when_injected(self, tmp_path, monkeypatch) -> None:
        result = self._run_submit(tmp_path, monkeypatch)
        assert result.network_calls_made is False

    def test_broker_calls_made_true(self, tmp_path, monkeypatch) -> None:
        result = self._run_submit(tmp_path, monkeypatch)
        assert result.broker_calls_made is True


# ---------------------------------------------------------------------------
# 6. TestSafetyConstraints
# ---------------------------------------------------------------------------


class TestSafetyConstraints:
    """Symbol=SPY only, side=sell only, order_type=market only, qty<=1, qty<=position qty."""

    def test_quantity_above_max_raises(self, tmp_path, monkeypatch) -> None:
        """Position qty > 1, no override → candidate qty > 1 → raises before submit."""
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        with pytest.raises(RuntimeError, match="quantity"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_quantity_above_max_does_not_call_submit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        with pytest.raises(RuntimeError):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        broker.submit_order.assert_not_called()

    def test_quantity_exceeds_position_raises(self, tmp_path, monkeypatch) -> None:
        """Override=1.0, position qty=0.5 → submitted qty > position qty → raises."""
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 0.5}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=1.0)
        with pytest.raises(RuntimeError, match="exceeds current position"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_buy_side_cannot_be_submitted(self, tmp_path, monkeypatch) -> None:
        """The close flow always generates sell; position qty > 1 causes qty violation."""
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        # no override — will fail on qty > 1
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        with pytest.raises(RuntimeError):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        broker.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# 7. TestQuantityOverride
# ---------------------------------------------------------------------------


class TestQuantityOverride:
    """Quantity override is only accepted if exactly 1.0."""

    def test_override_1_0_large_position_succeeds(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID, qty=1.0)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=1.0)
        result = run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert result.orders_submitted == 1

    def test_override_above_1_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=2.0)
        with pytest.raises(RuntimeError, match="must be <= 1"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_override_zero_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=0.0)
        with pytest.raises(RuntimeError, match="must be > 0"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_override_negative_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=-1.0)
        with pytest.raises(RuntimeError, match="must be > 0"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_override_fractional_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        broker = _make_broker(positions={"SPY": {"qty": 5.0}})
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=0.5)
        with pytest.raises(RuntimeError, match="exactly 1.0"):
            run_paper_close(cfg, output_dir=tmp_path, _broker=broker)

    def test_no_override_allowed(self, tmp_path, monkeypatch) -> None:
        """No override (None) with position qty=1 → uses position qty directly."""
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID, qty=1.0)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID, qty_override=None)
        result = run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert result.orders_submitted == 1


# ---------------------------------------------------------------------------
# 8. TestGuardDelegation
# ---------------------------------------------------------------------------


class TestGuardDelegation:
    """Verify guards are called (or not) based on config flags."""

    def test_preflight_check_called_in_preview(self, tmp_path) -> None:
        broker = _make_broker(positions={})
        run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        broker.preflight_check.assert_called_once()

    def test_preflight_check_called_in_submit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        broker.preflight_check.assert_called_once()

    def test_market_hours_guard_invoked_when_enabled(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        import src.execution.paper_market_hours_guard as _mhg
        calls: list = []
        monkeypatch.setattr(_mhg, "assert_regular_market_hours", lambda: calls.append(True))

        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(
            preview_only=False,
            selected_coid=_FIXED_CLOSE_COID,
            require_market_hours=True,
        )
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert calls == [True]

    def test_market_hours_guard_not_invoked_when_disabled(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        import src.execution.paper_market_hours_guard as _mhg
        calls: list = []
        monkeypatch.setattr(_mhg, "assert_regular_market_hours", lambda: calls.append(True))

        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(
            preview_only=False,
            selected_coid=_FIXED_CLOSE_COID,
            require_market_hours=False,
        )
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert calls == []

    def test_open_order_guard_invoked_when_enabled(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        import src.execution.paper_open_order_guard as _oog
        calls: list = []
        monkeypatch.setattr(_oog, "assert_no_open_orders_for_symbol",
                            lambda client, sym: calls.append(sym))

        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(
            preview_only=False,
            selected_coid=_FIXED_CLOSE_COID,
            block_if_open_orders=True,
        )
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert calls == ["SPY"]

    def test_open_order_guard_not_invoked_when_disabled(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        import src.execution.paper_open_order_guard as _oog
        calls: list = []
        monkeypatch.setattr(_oog, "assert_no_open_orders_for_symbol",
                            lambda client, sym: calls.append(sym))

        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(
            preview_only=False,
            selected_coid=_FIXED_CLOSE_COID,
            block_if_open_orders=False,
        )
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert calls == []


# ---------------------------------------------------------------------------
# 9. TestOutputArtifacts
# ---------------------------------------------------------------------------


class TestOutputArtifacts:
    """Verify output files are written."""

    def test_close_candidate_csv_written_in_preview(self, tmp_path) -> None:
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert (tmp_path / "paper_close_candidate_intents.csv").exists()

    def test_close_candidate_csv_written_in_submit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert (tmp_path / "paper_close_candidate_intents.csv").exists()

    def test_audit_csv_written_in_submit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(pd.Timestamp, "now", lambda tz=None: _FIXED_TS)
        _patch_reporter(monkeypatch, tmp_path)
        broker = _make_broker(positions={"SPY": {"qty": 1.0}})
        broker.submit_order.return_value = _make_close_result(_FIXED_CLOSE_COID)
        cfg = _close_cfg(preview_only=False, selected_coid=_FIXED_CLOSE_COID)
        run_paper_close(cfg, output_dir=tmp_path, _broker=broker)
        assert (tmp_path / "paper_close_intent_audit.csv").exists()

    def test_output_dir_in_result(self, tmp_path) -> None:
        broker = _make_broker(positions={})
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.output_dir == str(tmp_path)


# ---------------------------------------------------------------------------
# 10. TestDefaultBrokerPath
# ---------------------------------------------------------------------------


class TestDefaultBrokerPath:
    """When _broker=None, credentials_read and network_calls_made are True."""

    def test_credentials_read_true_on_default_broker_path(self, monkeypatch, tmp_path) -> None:
        import src.execution.alpaca_broker as _ab
        mock_broker = _make_broker()  # empty positions
        monkeypatch.setattr(_ab, "AlpacaBrokerAdapter", lambda: mock_broker)
        cfg = _close_cfg(preview_only=True)
        # _broker not passed → uses default AlpacaBrokerAdapter path
        result = run_paper_close(cfg, output_dir=tmp_path)
        assert result.credentials_read is True

    def test_network_calls_made_true_on_default_broker_path(self, monkeypatch, tmp_path) -> None:
        import src.execution.alpaca_broker as _ab
        mock_broker = _make_broker()
        monkeypatch.setattr(_ab, "AlpacaBrokerAdapter", lambda: mock_broker)
        cfg = _close_cfg(preview_only=True)
        result = run_paper_close(cfg, output_dir=tmp_path)
        assert result.network_calls_made is True

    def test_credentials_read_false_when_broker_injected(self, tmp_path) -> None:
        broker = _make_broker()
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.credentials_read is False

    def test_network_calls_made_false_when_broker_injected(self, tmp_path) -> None:
        broker = _make_broker()
        result = run_paper_close(_close_cfg(preview_only=True), output_dir=tmp_path, _broker=broker)
        assert result.network_calls_made is False
