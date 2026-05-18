"""
tests/test_paper_close.py
--------------------------
Tests for the two-phase paper close/flatten flow.

All tests are fully offline — no Alpaca credentials, no real network calls,
no orders submitted or cancelled.  The existing buy-submit tests in
test_alpaca_broker_skeleton.py are unchanged.
"""

from __future__ import annotations

import json
from unittest import mock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Shared constants & helpers
# ---------------------------------------------------------------------------

# A fixed Timestamp used to make candidate IDs predictable.
_FIXED_TS         = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")
_FIXED_DATE_LABEL = "20240115"
_FIXED_CID        = f"BC-{_FIXED_DATE_LABEL}-SPY-CLOSE"


def _make_config(
    *,
    paper_close_positions_enabled: bool = True,
    paper_close_preview_only: bool = True,
    paper_selected_close_client_order_id: str | None = None,
    paper_close_quantity_override: float | None = None,
):
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
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_close_positions_enabled=paper_close_positions_enabled,
            paper_close_preview_only=paper_close_preview_only,
            paper_selected_close_client_order_id=paper_selected_close_client_order_id,
            paper_close_quantity_override=paper_close_quantity_override,
        ),
    )


def _spy_pos(qty: float = 1.0) -> dict:
    return {"symbol": "SPY", "qty": qty, "market_value": qty * 475.0,
            "avg_entry_price": 470.0, "current_price": 475.0, "unrealized_pl": qty * 5.0}


def _make_submit_return(client_order_id: str = _FIXED_CID, qty: float = 1.0, status: str = "accepted"):
    from src.execution.broker import OrderResult
    return OrderResult(
        order_id=f"ALPACA-{client_order_id}",
        symbol="SPY", side="sell", quantity=qty, status=status,
        submitted_at=_FIXED_TS, reason="paper_close",
        client_order_id=client_order_id,
        metadata={"raw_status": status, "partial_fill": False},
    )


def _pass_recon_generate(rg_self):
    """Substitute for ReportGenerator.generate_all that writes a PASS reconciliation."""
    rg_self._output_dir.mkdir(parents=True, exist_ok=True)
    (rg_self._output_dir / "order_reconciliation.json").write_text(
        json.dumps({"overall_status": "PASS"})
    )


def _run(
    cfg,
    positions: dict | None = None,
    submit_return=None,
    tmp_path=None,
) -> tuple[mock.MagicMock, mock.MagicMock]:
    """Run src.main.main() with mocked broker and engine, return (mock_submit, mock_cancel)."""
    import tempfile
    from src.execution.alpaca_broker import AlpacaBrokerAdapter

    out = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
    fake_preflight = {
        "ok": True,
        "account": {"status": "ACTIVE"},
        "positions": positions if positions is not None else {},
        "symbols": ["SPY"],
    }
    mock_submit = mock.MagicMock(return_value=submit_return)
    mock_cancel = mock.MagicMock()

    import src.main  # ensure module is loaded before patching

    with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
         mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
         mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                           return_value=fake_preflight), \
         mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
         mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel), \
         mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                    _pass_recon_generate), \
         mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
         mock.patch("src.execution.paper_ledger.append_ledger_row"), \
         mock.patch("src.main.load_config", return_value=cfg), \
         mock.patch("sys.argv", ["prog", "--output-dir", out]):
        src.main.main()

    return mock_submit, mock_cancel


# ---------------------------------------------------------------------------
# 1. Config defaults
# ---------------------------------------------------------------------------

class TestPaperCloseConfigDefaults:
    def test_default_close_positions_enabled_is_false(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig().paper_close_positions_enabled is False

    def test_default_close_preview_only_is_true(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig().paper_close_preview_only is True

    def test_default_selected_close_client_order_id_is_none(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig().paper_selected_close_client_order_id is None

    def test_default_close_quantity_override_is_none(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig().paper_close_quantity_override is None

    def test_close_positions_enabled_true(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig(paper_close_positions_enabled=True).paper_close_positions_enabled is True

    def test_close_preview_only_false(self):
        from src.config.loader import ExecutionConfig
        assert ExecutionConfig(paper_close_preview_only=False).paper_close_preview_only is False


# ---------------------------------------------------------------------------
# 2. Close path not triggered when disabled
# ---------------------------------------------------------------------------

class TestPaperClosePathNotTriggered:
    """paper_close_positions_enabled=False → buy-submit path; no close CSV written."""

    def test_disabled_does_not_write_close_candidate_csv(self, tmp_path):
        from src.config.loader import ExecutionConfig
        from src.execution.alpaca_broker import AlpacaBrokerAdapter

        cfg = _make_config(paper_close_positions_enabled=False)
        # Use paper_preview_only=True (default) so the buy path exits cleanly.
        cfg.execution.__class__  # just reference to avoid lint

        # Rebuild cfg with paper_close_positions_enabled=False and paper_preview_only=True.
        from src.config.loader import (
            AppConfig, BacktestConfig, DataConfig, LoggingConfig, RiskConfig, StrategyConfig,
        )
        cfg2 = AppConfig(
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
            logging=LoggingConfig(level="WARNING", format="%(message)s"),
            execution=ExecutionConfig(
                mode="paper",
                paper_trading_enabled=True,
                paper_close_positions_enabled=False,  # disabled
                paper_preview_only=True,
            ),
        )

        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": _spy_pos(1.0)}, "symbols": ["SPY"],
        }
        fake_engine = mock.MagicMock()
        fake_engine.run.return_value = {
            "order_intents": [], "metrics": {}, "trades": [], "equity_curve": [],
        }
        fake_engine._portfolio.positions = {}

        import src.main
        with mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch("src.main.build_engine", return_value=fake_engine), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _pass_recon_generate), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg2), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            src.main.main()

        assert not (tmp_path / "paper_close_candidate_intents.csv").exists()


# ---------------------------------------------------------------------------
# 3. Phase A — close preview
# ---------------------------------------------------------------------------

class TestPaperClosePreview:
    """Phase A: writes paper_close_candidate_intents.csv, no submit, no cancel."""

    def test_preview_writes_close_candidate_csv(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        assert (tmp_path / "paper_close_candidate_intents.csv").exists()

    def test_preview_does_not_call_submit_order(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        mock_submit, _ = _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        mock_submit.assert_not_called()

    def test_preview_does_not_call_cancel_order(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _, mock_cancel = _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        mock_cancel.assert_not_called()

    def test_preview_with_no_positions_writes_empty_csv(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert len(df) == 0

    def test_preview_with_spy_position_creates_one_candidate(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert len(df) == 1

    def test_preview_with_spy_position_candidate_symbol_is_spy(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert df["symbol"].iloc[0] == "SPY"

    def test_preview_candidate_side_is_sell(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert df["side"].iloc[0] == "sell"

    def test_preview_candidate_order_type_is_market(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert df["order_type"].iloc[0] == "market"

    def test_preview_csv_has_current_position_qty_column(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(3.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert "current_position_qty" in df.columns
        assert float(df["current_position_qty"].iloc[0]) == pytest.approx(3.0)

    def test_preview_ignores_non_spy_positions(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        positions = {
            "SPY": _spy_pos(1.0),
            "AAPL": {"symbol": "AAPL", "qty": 5.0, "market_value": 900.0,
                     "avg_entry_price": 180.0, "current_price": 180.0, "unrealized_pl": 0.0},
        }
        _run(cfg, positions=positions, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        # Only SPY candidate generated; AAPL ignored.
        assert len(df) == 1
        assert df["symbol"].iloc[0] == "SPY"

    def test_preview_with_zero_qty_spy_position_writes_empty_csv(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(0.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert len(df) == 0

    def test_preview_no_buy_candidate_generated(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert (df["side"] != "buy").all()


# ---------------------------------------------------------------------------
# 4. Phase B — close submit
# ---------------------------------------------------------------------------

class TestPaperCloseSubmit:
    """Phase B: select one candidate, validate, submit exactly one sell order."""

    def test_submit_without_selected_id_raises_before_submit_order(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=None,
        )
        with pytest.raises(RuntimeError, match="paper_selected_close_client_order_id"):
            _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)

    def test_submit_blank_selected_id_raises(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id="   ",
        )
        with pytest.raises(RuntimeError, match="paper_selected_close_client_order_id"):
            _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)

    def test_submit_selected_id_not_found_raises(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id="BC-NONEXISTENT",
        )
        with pytest.raises(RuntimeError, match="no close candidate found"):
            _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)

    def test_submit_selected_id_not_found_does_not_call_submit_order(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id="BC-NONEXISTENT",
        )
        with pytest.raises(RuntimeError):
            mock_submit, _ = _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
            mock_submit.assert_not_called()

    def test_duplicate_selected_close_id_raises(self, tmp_path):
        # Two positions that both normalise to SPY (case variants) produce two
        # candidates with the same ID; selecting that ID raises.
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        # "SPY" and "spy" both become "SPY" after .upper(); they share the same ID.
        positions = {
            "SPY": _spy_pos(1.0),
            "spy": _spy_pos(1.0),
        }
        with pytest.raises(RuntimeError, match="candidates found"):
            _run(cfg, positions=positions, tmp_path=tmp_path)

    def test_duplicate_id_does_not_call_submit_order(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        positions = {"SPY": _spy_pos(1.0), "spy": _spy_pos(1.0)}
        with pytest.raises(RuntimeError):
            _run(cfg, positions=positions, tmp_path=tmp_path)

    def test_close_submit_spy_sell_calls_submit_order_exactly_once(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        mock_submit, _ = _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        assert mock_submit.call_count == 1

    def test_close_submit_submits_sell_intent(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        mock_submit, _ = _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        intent = mock_submit.call_args[0][0]
        assert intent.side == "sell"

    def test_close_submit_writes_close_candidate_csv(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        _run(cfg, positions={"SPY": _spy_pos(1.0)},
             submit_return=_make_submit_return(), tmp_path=tmp_path)
        assert (tmp_path / "paper_close_candidate_intents.csv").exists()

    def test_close_submit_writes_close_audit_csv(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        _run(cfg, positions={"SPY": _spy_pos(1.0)},
             submit_return=_make_submit_return(), tmp_path=tmp_path)
        assert (tmp_path / "paper_close_intent_audit.csv").exists()

    def test_close_audit_csv_columns(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        _run(cfg, positions={"SPY": _spy_pos(1.0)},
             submit_return=_make_submit_return(), tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_intent_audit.csv")
        for col in ["client_order_id", "symbol", "side", "original_quantity",
                    "submitted_quantity", "override_applied", "current_position_qty"]:
            assert col in df.columns, f"Missing column: {col}"

    def test_cancel_order_is_never_called_in_submit(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        _, mock_cancel = _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        mock_cancel.assert_not_called()

    def test_cancel_order_is_never_called_in_preview(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _, mock_cancel = _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        mock_cancel.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Quantity safety constraints
# ---------------------------------------------------------------------------

class TestPaperCloseQuantitySafety:
    def test_position_qty_5_no_override_raises_qty_exceeds_max(self, tmp_path):
        """position qty=5, no override → submitted qty=5 > max 1 → raises before submit."""
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        with pytest.raises(RuntimeError, match="quantity"):
            _run(cfg, positions={"SPY": _spy_pos(5.0)}, tmp_path=tmp_path)

    def test_position_qty_5_no_override_does_not_call_submit(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        with pytest.raises(RuntimeError):
            mock_submit, _ = _run(cfg, positions={"SPY": _spy_pos(5.0)}, tmp_path=tmp_path)
            mock_submit.assert_not_called()

    def test_override_1_passes_when_position_qty_is_large(self, tmp_path):
        """position qty=5, override=1 → submitted qty=1 <= 1 and <= 5 → OK."""
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=1.0,
        )
        mock_submit, _ = _run(
            cfg,
            positions={"SPY": _spy_pos(5.0)},
            submit_return=_make_submit_return(qty=1.0),
            tmp_path=tmp_path,
        )
        assert mock_submit.call_count == 1
        intent = mock_submit.call_args[0][0]
        assert intent.quantity == pytest.approx(1.0)

    def test_override_1_fails_when_position_qty_is_less_than_1(self, tmp_path):
        """position qty=0.5, override=1 → submitted qty=1 > 0.5 (current_pos_qty) → raises."""
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=1.0,
        )
        with pytest.raises(RuntimeError, match="exceeds current position"):
            _run(cfg, positions={"SPY": _spy_pos(0.5)}, tmp_path=tmp_path)

    def test_override_greater_than_1_raises(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=2.0,
        )
        with pytest.raises(RuntimeError, match="must be <= 1"):
            _run(cfg, positions={"SPY": _spy_pos(5.0)}, tmp_path=tmp_path)

    def test_override_zero_raises(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=0.0,
        )
        with pytest.raises(RuntimeError, match="must be > 0"):
            _run(cfg, positions={"SPY": _spy_pos(5.0)}, tmp_path=tmp_path)

    def test_override_negative_raises(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=-1.0,
        )
        with pytest.raises(RuntimeError, match="must be > 0"):
            _run(cfg, positions={"SPY": _spy_pos(5.0)}, tmp_path=tmp_path)

    def test_override_applies_only_to_submitted_intent_not_candidate_csv(self, tmp_path):
        """The close candidate CSV is written before the override; its qty is the original position qty."""
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=1.0,
        )
        mock_submit, _ = _run(
            cfg,
            positions={"SPY": _spy_pos(5.0)},
            submit_return=_make_submit_return(qty=1.0),
            tmp_path=tmp_path,
        )
        # Candidate CSV has original qty (5.0).
        df_cand = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        assert float(df_cand["quantity"].iloc[0]) == pytest.approx(5.0)
        # Submitted intent has overridden qty (1.0).
        submitted_intent = mock_submit.call_args[0][0]
        assert submitted_intent.quantity == pytest.approx(1.0)

    def test_override_audit_csv_records_original_and_submitted_quantities(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
            paper_close_quantity_override=1.0,
        )
        _run(
            cfg,
            positions={"SPY": _spy_pos(5.0)},
            submit_return=_make_submit_return(qty=1.0),
            tmp_path=tmp_path,
        )
        df = pd.read_csv(tmp_path / "paper_close_intent_audit.csv")
        assert float(df["original_quantity"].iloc[0]) == pytest.approx(5.0)
        assert float(df["submitted_quantity"].iloc[0]) == pytest.approx(1.0)
        assert bool(df["override_applied"].iloc[0]) is True

    def test_no_override_audit_csv_override_applied_is_false(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(qty=1.0),
            tmp_path=tmp_path,
        )
        df = pd.read_csv(tmp_path / "paper_close_intent_audit.csv")
        assert bool(df["override_applied"].iloc[0]) is False


# ---------------------------------------------------------------------------
# 6. No-buy safety in close flow
# ---------------------------------------------------------------------------

class TestPaperCloseNoBuy:
    """The close flow must never generate or submit buy orders."""

    def test_preview_no_buy_in_candidate_csv(self, tmp_path):
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(3.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        if len(df) > 0:
            assert (df["side"] == "sell").all()

    def test_submit_side_is_sell(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        mock_submit, _ = _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        intent = mock_submit.call_args[0][0]
        assert intent.side == "sell"


# ---------------------------------------------------------------------------
# 7. Network isolation
# ---------------------------------------------------------------------------

class TestPaperCloseNoRealNetworkCalls:
    """The close path must make no real network calls."""

    def test_no_real_network_calls_in_preview(self, tmp_path, monkeypatch):
        import urllib.request
        original = urllib.request.urlopen

        def fail(*a, **kw):
            raise AssertionError("no real network calls allowed in close preview")

        monkeypatch.setattr(urllib.request, "urlopen", fail)
        try:
            cfg = _make_config(paper_close_preview_only=True)
            _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        finally:
            monkeypatch.setattr(urllib.request, "urlopen", original)

    def test_no_real_network_calls_in_submit(self, tmp_path, monkeypatch):
        import urllib.request
        original = urllib.request.urlopen

        def fail(*a, **kw):
            raise AssertionError("no real network calls allowed in close submit")

        monkeypatch.setattr(urllib.request, "urlopen", fail)
        try:
            cfg = _make_config(
                paper_close_preview_only=False,
                paper_selected_close_client_order_id=_FIXED_CID,
            )
            _run(
                cfg,
                positions={"SPY": _spy_pos(1.0)},
                submit_return=_make_submit_return(),
                tmp_path=tmp_path,
            )
        finally:
            monkeypatch.setattr(urllib.request, "urlopen", original)


# ---------------------------------------------------------------------------
# 8. Preflight always runs
# ---------------------------------------------------------------------------

class TestPaperClosePreflight:
    def test_preflight_called_in_preview(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = _make_config(paper_close_preview_only=True)
        mock_pf = mock.MagicMock(return_value={
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        })
        import src.main
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check", mock_pf), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            src.main.main()
        mock_pf.assert_called_once()

    def test_preflight_called_with_allow_existing_positions_true(self, tmp_path):
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        cfg = _make_config(paper_close_preview_only=True)
        mock_pf = mock.MagicMock(return_value={
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {}, "symbols": ["SPY"],
        })
        import src.main
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check", mock_pf), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            src.main.main()
        mock_pf.assert_called_once_with(["SPY"], allow_existing_positions=True)


# ---------------------------------------------------------------------------
# 9. Close submit audit hardening
# ---------------------------------------------------------------------------

def _make_config_with_poll(
    *,
    paper_close_positions_enabled: bool = True,
    paper_close_preview_only: bool = False,
    paper_selected_close_client_order_id: str | None = _FIXED_CID,
    paper_close_quantity_override: float | None = None,
    paper_poll_order_status: bool = False,
    paper_poll_timeout_seconds: int = 30,
    paper_poll_interval_seconds: float = 1.0,
):
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
        logging=LoggingConfig(level="WARNING", format="%(message)s"),
        execution=ExecutionConfig(
            mode="paper",
            paper_trading_enabled=True,
            paper_close_positions_enabled=paper_close_positions_enabled,
            paper_close_preview_only=paper_close_preview_only,
            paper_selected_close_client_order_id=paper_selected_close_client_order_id,
            paper_close_quantity_override=paper_close_quantity_override,
            paper_poll_order_status=paper_poll_order_status,
            paper_poll_timeout_seconds=paper_poll_timeout_seconds,
            paper_poll_interval_seconds=paper_poll_interval_seconds,
        ),
    )


def _run_extended(
    cfg,
    positions: dict | None = None,
    submit_return=None,
    tmp_path=None,
    *,
    poll_client=None,
) -> tuple[mock.MagicMock, mock.MagicMock, mock.MagicMock]:
    """Like _run() but captures append_ledger_row calls and supports a poll client.

    Returns (mock_submit, mock_cancel, mock_ledger).
    """
    import tempfile
    from src.execution.alpaca_broker import AlpacaBrokerAdapter

    out = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
    fake_preflight = {
        "ok": True,
        "account": {"status": "ACTIVE"},
        "positions": positions if positions is not None else {},
        "symbols": ["SPY"],
    }
    mock_submit = mock.MagicMock(return_value=submit_return)
    mock_cancel = mock.MagicMock()
    mock_ledger = mock.MagicMock()

    import src.main

    patches = [
        mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS),
        mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None),
        mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                          return_value=fake_preflight),
        mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit),
        mock.patch.object(AlpacaBrokerAdapter, "cancel_order", mock_cancel),
        mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                   _pass_recon_generate),
        mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"),
        mock.patch("src.execution.paper_ledger.append_ledger_row", mock_ledger),
        mock.patch("src.main.load_config", return_value=cfg),
        mock.patch("sys.argv", ["prog", "--output-dir", out]),
    ]
    if poll_client is not None:
        patches.append(
            mock.patch.object(AlpacaBrokerAdapter, "_get_client",
                              return_value=poll_client)
        )

    import contextlib
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        src.main.main()

    return mock_submit, mock_cancel, mock_ledger


class TestCloseSubmitAuditHardening:
    """Audit hardening: current_position_qty and selected_close_client_order_id
    are recorded in the close audit CSV and ledger notes."""

    def test_zero_qty_position_raises_before_submit_order(self, tmp_path):
        """position qty=0 → no close candidate generated → raises before submit_order."""
        cfg = _make_config_with_poll()
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": _spy_pos(0.0)}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError):
                src.main.main()
        mock_submit.assert_not_called()

    def test_qty_exceeds_current_position_raises_before_submit_order(self, tmp_path):
        """override=1.0 with position qty=0.5 → quantity exceeds current position → raises."""
        cfg = _make_config_with_poll(paper_close_quantity_override=1.0)
        mock_submit = mock.MagicMock()
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": _spy_pos(0.5)}, "symbols": ["SPY"],
        }
        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(RuntimeError, match="exceeds current position"):
                src.main.main()
        mock_submit.assert_not_called()

    def test_audit_csv_has_selected_close_client_order_id_column(self, tmp_path):
        cfg = _make_config_with_poll()
        _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        df = pd.read_csv(tmp_path / "paper_close_intent_audit.csv")
        assert "selected_close_client_order_id" in df.columns

    def test_audit_csv_selected_close_client_order_id_value(self, tmp_path):
        cfg = _make_config_with_poll()
        _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        df = pd.read_csv(tmp_path / "paper_close_intent_audit.csv")
        assert df["selected_close_client_order_id"].iloc[0] == _FIXED_CID

    def test_audit_csv_current_position_qty_value(self, tmp_path):
        cfg = _make_config_with_poll()
        _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        df = pd.read_csv(tmp_path / "paper_close_intent_audit.csv")
        assert float(df["current_position_qty"].iloc[0]) == pytest.approx(1.0)

    def test_ledger_notes_includes_current_position_qty(self, tmp_path):
        cfg = _make_config_with_poll()
        _, _, mock_ledger = _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        assert mock_ledger.call_count == 1
        row = mock_ledger.call_args[0][1]
        assert "current_position_qty" in row.get("notes", "")
        assert "1.0" in row.get("notes", "")

    def test_ledger_flow_is_close_submit(self, tmp_path):
        cfg = _make_config_with_poll()
        _, _, mock_ledger = _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        row = mock_ledger.call_args[0][1]
        assert row.get("flow") == "close_submit"

    def test_polling_enabled_close_submit_writes_final_status_to_ledger(self, tmp_path):
        """When polling is enabled and final status is 'filled', ledger records 'filled'."""
        cfg = _make_config_with_poll(paper_poll_order_status=True, paper_poll_timeout_seconds=5)

        poll_client = mock.MagicMock()
        filled_response = mock.MagicMock()
        filled_response.status = "filled"
        poll_client.get_order_by_id.return_value = filled_response
        poll_client.submit_order.side_effect = AssertionError("must not call")
        poll_client.cancel_order.side_effect = AssertionError("must not call")

        _, _, mock_ledger = _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(status="accepted"),
            tmp_path=tmp_path,
            poll_client=poll_client,
        )
        row = mock_ledger.call_args[0][1]
        assert row.get("status") == "filled"

    def test_submit_order_called_exactly_once(self, tmp_path):
        cfg = _make_config_with_poll()
        mock_submit, _, _ = _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        assert mock_submit.call_count == 1

    def test_cancel_order_never_called(self, tmp_path):
        cfg = _make_config_with_poll()
        _, mock_cancel, _ = _run_extended(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        mock_cancel.assert_not_called()


# ---------------------------------------------------------------------------
# 10. Close candidate ID stability
# ---------------------------------------------------------------------------

class TestCloseCandidateIdStability:
    """Close candidate IDs must be stable (date-only) so the ID chosen during
    preview is still valid when the submit run regenerates candidates seconds
    or minutes later on the same trading day."""

    def test_candidate_id_has_no_seconds_component(self, tmp_path):
        """The generated candidate ID must not contain HHMMSS — only YYYYMMDD."""
        cfg = _make_config(paper_close_preview_only=True)
        _run(cfg, positions={"SPY": _spy_pos(1.0)}, tmp_path=tmp_path)
        df = pd.read_csv(tmp_path / "paper_close_candidate_intents.csv")
        cid = df["client_order_id"].iloc[0]
        # Must match BC-YYYYMMDD-SPY-CLOSE exactly (8-digit date, no time).
        import re
        assert re.fullmatch(r"BC-\d{8}-SPY-CLOSE", cid), (
            f"candidate ID {cid!r} does not match BC-YYYYMMDD-SPY-CLOSE"
        )

    def test_preview_and_submit_generate_same_candidate_id_on_same_date(self, tmp_path):
        """Preview run and submit run on the same date produce the same candidate ID."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        # Two Timestamps on the same date but different times.
        ts_morning = pd.Timestamp("2024-01-15 09:45:00", tz="America/New_York")
        ts_later   = pd.Timestamp("2024-01-15 10:32:17", tz="America/New_York")

        preview_cfg = _make_config(paper_close_preview_only=True)
        submit_cfg  = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )

        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": _spy_pos(1.0)}, "symbols": ["SPY"],
        }

        # --- Phase A: preview (morning timestamp) ---
        preview_dir = tmp_path / "preview"
        preview_dir.mkdir()
        with mock.patch.object(pd.Timestamp, "now", return_value=ts_morning), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch("src.main.load_config", return_value=preview_cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(preview_dir)]):
            src.main.main()

        preview_id = pd.read_csv(
            preview_dir / "paper_close_candidate_intents.csv"
        )["client_order_id"].iloc[0]

        # --- Phase B: submit (later timestamp, same date) ---
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        mock_submit = mock.MagicMock(return_value=_make_submit_return(client_order_id=preview_id))
        with mock.patch.object(pd.Timestamp, "now", return_value=ts_later), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _pass_recon_generate), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=submit_cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(submit_dir)]):
            src.main.main()

        submit_id = pd.read_csv(
            submit_dir / "paper_close_candidate_intents.csv"
        )["client_order_id"].iloc[0]

        assert preview_id == submit_id, (
            f"preview ID {preview_id!r} != submit ID {submit_id!r}"
        )
        assert mock_submit.call_count == 1

    def test_preview_selected_id_accepted_by_submit_run(self, tmp_path):
        """The ID written to paper_close_candidate_intents.csv during preview
        can be used as paper_selected_close_client_order_id in the submit run."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        ts_a = pd.Timestamp("2024-01-15 09:31:00", tz="America/New_York")
        ts_b = pd.Timestamp("2024-01-15 09:45:53", tz="America/New_York")

        preview_cfg = _make_config(paper_close_preview_only=True)
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": _spy_pos(1.0)}, "symbols": ["SPY"],
        }

        preview_dir = tmp_path / "preview"
        preview_dir.mkdir()
        with mock.patch.object(pd.Timestamp, "now", return_value=ts_a), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch("src.main.load_config", return_value=preview_cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(preview_dir)]):
            src.main.main()

        selected_id = pd.read_csv(
            preview_dir / "paper_close_candidate_intents.csv"
        )["client_order_id"].iloc[0]

        # Now submit using that ID.
        submit_cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=selected_id,
        )
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        mock_submit = mock.MagicMock(
            return_value=_make_submit_return(client_order_id=selected_id)
        )
        with mock.patch.object(pd.Timestamp, "now", return_value=ts_b), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.reporting.report_generator.ReportGenerator.generate_all",
                        _pass_recon_generate), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused"), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=submit_cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(submit_dir)]):
            src.main.main()  # must not raise

        mock_submit.assert_called_once()

    def test_repeated_close_submit_blocked_by_ledger_before_submit_order(self, tmp_path):
        """A second submit with the same client_order_id is blocked by the ledger
        duplicate guard before submit_order is called."""
        from src.execution.alpaca_broker import AlpacaBrokerAdapter
        import src.main

        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        fake_preflight = {
            "ok": True, "account": {"status": "ACTIVE"},
            "positions": {"SPY": _spy_pos(1.0)}, "symbols": ["SPY"],
        }
        mock_submit = mock.MagicMock(return_value=_make_submit_return())

        def _raise_duplicate(_path, _coid):
            raise RuntimeError(
                f"client_order_id {_FIXED_CID!r} already in ledger"
            )

        with mock.patch.object(pd.Timestamp, "now", return_value=_FIXED_TS), \
             mock.patch.object(AlpacaBrokerAdapter, "__init__", return_value=None), \
             mock.patch.object(AlpacaBrokerAdapter, "preflight_check",
                               return_value=fake_preflight), \
             mock.patch.object(AlpacaBrokerAdapter, "submit_order", mock_submit), \
             mock.patch("src.execution.paper_ledger.assert_client_order_id_unused",
                        side_effect=_raise_duplicate), \
             mock.patch("src.execution.paper_ledger.append_ledger_row"), \
             mock.patch("src.main.load_config", return_value=cfg), \
             mock.patch("sys.argv", ["prog", "--output-dir", str(tmp_path)]):
            with pytest.raises(Exception, match=_FIXED_CID):
                src.main.main()

        mock_submit.assert_not_called()

    def test_submit_order_called_exactly_once_on_valid_selection(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        mock_submit, _ = _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        assert mock_submit.call_count == 1

    def test_cancel_order_never_called_on_valid_selection(self, tmp_path):
        cfg = _make_config(
            paper_close_preview_only=False,
            paper_selected_close_client_order_id=_FIXED_CID,
        )
        _, mock_cancel = _run(
            cfg,
            positions={"SPY": _spy_pos(1.0)},
            submit_return=_make_submit_return(),
            tmp_path=tmp_path,
        )
        mock_cancel.assert_not_called()
