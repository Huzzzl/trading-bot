"""
tests/test_replay_order_reconciliation.py
------------------------------------------
Tests for src.reporting.replay_reconciliation (library module).

All tests are fully offline — no Alpaca credentials, no network calls,
no order submissions or cancellations.

The former src.tools.replay_order_reconciliation CLI tool was archived in
PR R4d; its CLI tests are removed.  All reconciliation logic now lives in
src.reporting.replay_reconciliation and is tested here.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from src.reporting.replay_reconciliation import (
    _normalize_side,
    _normalize_status,
    replay,
)


# ---------------------------------------------------------------------------
# Normalisation unit tests
# ---------------------------------------------------------------------------

class TestNormalizeSide:
    def test_plain_buy(self):
        assert _normalize_side("buy") == "buy"

    def test_plain_sell(self):
        assert _normalize_side("sell") == "sell"

    def test_sdk_enum_buy(self):
        assert _normalize_side("OrderSide.BUY") == "buy"

    def test_sdk_enum_sell(self):
        assert _normalize_side("OrderSide.SELL") == "sell"

    def test_uppercase_buy(self):
        assert _normalize_side("BUY") == "buy"

    def test_strips_whitespace(self):
        assert _normalize_side("  OrderSide.BUY  ") == "buy"


class TestNormalizeStatus:
    def test_accepted(self):
        assert _normalize_status("accepted") == "accepted"

    def test_pending_new(self):
        assert _normalize_status("pending_new") == "accepted"

    def test_sdk_pending_new(self):
        assert _normalize_status("OrderStatus.PENDING_NEW") == "accepted"

    def test_sdk_filled(self):
        assert _normalize_status("OrderStatus.FILLED") == "filled"

    def test_sdk_canceled(self):
        assert _normalize_status("OrderStatus.CANCELED") == "cancelled"

    def test_sdk_rejected(self):
        assert _normalize_status("OrderStatus.REJECTED") == "rejected"

    def test_unknown_passthrough(self):
        # Unknown statuses are passed through lowercased rather than raising,
        # so the replay tool never silently swallows errors in the recon logic.
        assert _normalize_status("OrderStatus.BANANA") == "banana"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_bt000035_fixture(tmp_path: Path) -> Path:
    """Write BT-000035 artifact CSVs: intent side='buy', result side='OrderSide.BUY'."""
    intents_csv = textwrap.dedent("""\
        client_order_id,timestamp,symbol,side,quantity,order_type,reason,limit_price,stop_price,metadata_json
        BT-000035,2026-05-13 15:42:35+00:00,SPY,buy,1.0,market,entry,,,"{}"\
    """)
    results_csv = textwrap.dedent("""\
        client_order_id,order_id,symbol,side,quantity,status,submitted_at,filled_at,filled_price,reason,metadata_json
        BT-000035,1b0905cf-3cfb-4834-8479-12071099dc17,SPY,OrderSide.BUY,1.0,accepted,2026-05-13 15:42:35+00:00,,,entry,"{""raw_status"": ""OrderStatus.PENDING_NEW"", ""partial_fill"": false}"\
    """)
    (tmp_path / "order_intents.csv").write_text(intents_csv, encoding="utf-8")
    (tmp_path / "order_results.csv").write_text(results_csv, encoding="utf-8")
    return tmp_path


def _write_simple_fixture(
    tmp_path: Path,
    intent_side: str = "buy",
    result_side: str = "buy",
    result_status: str = "accepted",
    result_reason: str = "entry",
    intent_reason: str = "entry",
    client_order_id: str = "BT-TEST",
) -> Path:
    intents_csv = (
        "client_order_id,timestamp,symbol,side,quantity,order_type,reason,limit_price,stop_price,metadata_json\n"
        f'{client_order_id},2024-01-15 10:00:00-05:00,SPY,{intent_side},1.0,market,{intent_reason},,,"{{}}"\n'
    )
    results_csv = (
        "client_order_id,order_id,symbol,side,quantity,status,submitted_at,filled_at,filled_price,reason,metadata_json\n"
        f'{client_order_id},ALPACA-001,SPY,{result_side},1.0,{result_status},2024-01-15 10:00:00-05:00,,,{result_reason},"{{}}"\n'
    )
    (tmp_path / "order_intents.csv").write_text(intents_csv, encoding="utf-8")
    (tmp_path / "order_results.csv").write_text(results_csv, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# BT-000035 regression: side normalisation
# ---------------------------------------------------------------------------

class TestBT000035Regression:
    """The original BT-000035 artifact had result.side = 'OrderSide.BUY' while
    intent.side = 'buy', causing reconciliation WARN.  After the side-normalisation
    fix the replay should produce PASS with mismatch_count = 0."""

    def test_overall_status_is_pass(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["overall_status"] == "PASS"

    def test_mismatch_count_is_zero(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["mismatch_count"] == 0

    def test_intent_count_is_one(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["intent_count"] == 1

    def test_result_count_is_one(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["result_count"] == 1

    def test_accepted_count_is_one(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["accepted_count"] == 1

    def test_unmatched_count_is_zero(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["unmatched_count"] == 0

    def test_missing_ids_warn_is_false(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        recon = replay(tmp_path)
        assert recon["missing_ids_warn"] is False


# ---------------------------------------------------------------------------
# Missing file errors
# ---------------------------------------------------------------------------

class TestMissingFiles:
    def test_missing_order_intents_csv_raises(self, tmp_path):
        # Write only results CSV.
        (tmp_path / "order_results.csv").write_text(
            "client_order_id,order_id,symbol,side,quantity,status,submitted_at,"
            "filled_at,filled_price,reason,metadata_json\n"
            'BT-001,A,SPY,buy,1.0,accepted,2024-01-15,,,,{}\n',
            encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError, match="order_intents.csv"):
            replay(tmp_path)

    def test_missing_order_results_csv_raises(self, tmp_path):
        # Write only intents CSV.
        (tmp_path / "order_intents.csv").write_text(
            "client_order_id,timestamp,symbol,side,quantity,order_type,reason,"
            "limit_price,stop_price,metadata_json\n"
            'BT-001,2024-01-15,SPY,buy,1.0,market,entry,,,{}\n',
            encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError, match="order_results.csv"):
            replay(tmp_path)

    def test_missing_intents_error_message_includes_path(self, tmp_path):
        (tmp_path / "order_results.csv").write_text("", encoding="utf-8")
        with pytest.raises(FileNotFoundError) as exc_info:
            replay(tmp_path)
        assert "order_intents.csv" in str(exc_info.value)

    def test_missing_results_error_message_includes_path(self, tmp_path):
        (tmp_path / "order_intents.csv").write_text(
            "client_order_id,timestamp,symbol,side,quantity,order_type,reason,"
            "limit_price,stop_price,metadata_json\n"
            'BT-001,2024-01-15,SPY,buy,1.0,market,entry,,,{}\n',
            encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError) as exc_info:
            replay(tmp_path)
        assert "order_results.csv" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Rejected result behaviour
# ---------------------------------------------------------------------------

class TestRejectedResult:
    def test_rejected_result_sets_rejected_count(self, tmp_path):
        _write_simple_fixture(tmp_path, result_status="rejected")
        recon = replay(tmp_path)
        assert recon["rejected_count"] == 1

    def test_rejected_result_matching_fields_is_pass(self, tmp_path):
        """A rejected result whose fields match the intent is still PASS —
        rejected_count is informational only, not a WARN trigger."""
        _write_simple_fixture(tmp_path, result_status="rejected")
        recon = replay(tmp_path)
        assert recon["overall_status"] == "PASS"

    def test_rejected_result_filled_count_zero(self, tmp_path):
        _write_simple_fixture(tmp_path, result_status="rejected")
        recon = replay(tmp_path)
        assert recon["filled_count"] == 0

    def test_rejected_result_accepted_count_zero(self, tmp_path):
        _write_simple_fixture(tmp_path, result_status="rejected")
        recon = replay(tmp_path)
        assert recon["accepted_count"] == 0


# ---------------------------------------------------------------------------
# Mismatch detection
# ---------------------------------------------------------------------------

class TestMismatchDetection:
    def test_side_mismatch_produces_warn(self, tmp_path):
        """Verifies the original BT-000035 failure mode (before fix)."""
        _write_simple_fixture(tmp_path, intent_side="buy", result_side="sell")
        recon = replay(tmp_path)
        assert recon["overall_status"] == "WARN"
        assert recon["mismatch_count"] == 1

    def test_sdk_enum_side_after_normalization_produces_pass(self, tmp_path):
        """'OrderSide.BUY' normalises to 'buy' so it matches intent 'buy'."""
        _write_simple_fixture(tmp_path, intent_side="buy", result_side="OrderSide.BUY")
        recon = replay(tmp_path)
        assert recon["overall_status"] == "PASS"
        assert recon["mismatch_count"] == 0

    def test_reason_mismatch_produces_warn(self, tmp_path):
        _write_simple_fixture(tmp_path, intent_reason="entry", result_reason="exit")
        recon = replay(tmp_path)
        assert recon["overall_status"] == "WARN"
        assert recon["mismatch_count"] == 1

    def test_unmatched_ids_produces_warn(self, tmp_path):
        """Result with different client_order_id → unmatched → WARN."""
        intents_csv = (
            "client_order_id,timestamp,symbol,side,quantity,order_type,reason,"
            "limit_price,stop_price,metadata_json\n"
            'BT-001,2024-01-15,SPY,buy,1.0,market,entry,,,{}\n'
        )
        results_csv = (
            "client_order_id,order_id,symbol,side,quantity,status,submitted_at,"
            "filled_at,filled_price,reason,metadata_json\n"
            'BT-999,ALPACA-001,SPY,buy,1.0,accepted,2024-01-15,,,entry,{}\n'
        )
        (tmp_path / "order_intents.csv").write_text(intents_csv, encoding="utf-8")
        (tmp_path / "order_results.csv").write_text(results_csv, encoding="utf-8")
        recon = replay(tmp_path)
        assert recon["overall_status"] == "WARN"
        assert recon["unmatched_count"] == 2


# ---------------------------------------------------------------------------
# --write flag
# ---------------------------------------------------------------------------

class TestWriteFlag:
    def test_write_creates_replay_json(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        replay(tmp_path, write=True)
        assert (tmp_path / "order_reconciliation_replay.json").exists()

    def test_write_json_content_is_valid(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        replay(tmp_path, write=True)
        content = json.loads((tmp_path / "order_reconciliation_replay.json").read_text(encoding="utf-8"))
        assert content["overall_status"] == "PASS"

    def test_no_write_does_not_create_file(self, tmp_path):
        _write_bt000035_fixture(tmp_path)
        replay(tmp_path, write=False)
        assert not (tmp_path / "order_reconciliation_replay.json").exists()


# ---------------------------------------------------------------------------
# build_reconciliation reuse — verify ReportGenerator still works
# ---------------------------------------------------------------------------

class TestReportGeneratorDelegation:
    """Ensure the refactored _build_reconciliation still produces the same output."""

    def test_report_generator_recon_pass(self, tmp_path):
        import pandas as pd
        from src.execution.broker import OrderResult
        from src.execution.order_intent import OrderIntent
        from src.reporting.report_generator import ReportGenerator
        from src.config.loader import AppConfig, BacktestConfig, DataConfig, ExecutionConfig, LoggingConfig, RiskConfig, StrategyConfig

        cfg = AppConfig(
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
            execution=ExecutionConfig(mode="paper", paper_trading_enabled=True),
        )
        ts = pd.Timestamp("2024-01-15 10:00:00", tz="America/New_York")
        intent = OrderIntent(
            symbol="SPY", side="buy", quantity=1.0, order_type="market",
            reason="entry", timestamp=ts, client_order_id="BT-000001",
        )
        result = OrderResult(
            order_id="ALPACA-001", symbol="SPY", side="buy", quantity=1.0,
            status="accepted", submitted_at=ts, reason="entry",
            client_order_id="BT-000001",
            metadata={"raw_status": "accepted", "partial_fill": False},
        )
        rg = ReportGenerator(
            metrics={"total_return": 0.0}, trades=[], equity_curve=[],
            config=cfg, output_dir=tmp_path,
            order_intents=[intent], order_results=[result],
        )
        recon = rg._build_reconciliation()
        assert recon["overall_status"] == "PASS"
        assert recon["mismatch_count"] == 0
