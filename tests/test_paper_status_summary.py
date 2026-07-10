"""Tests for src.tools.paper_status_summary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.broker.alpaca_paper_adapter import AlpacaPaperAdapterError
from src.tools import paper_status_summary as pss


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_audit(audit_dir: Path, date: str, records: list[dict[str, Any]]) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"{date}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


def _fake_adapter(
    *,
    account: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    orders_by_id: dict[str, dict[str, Any]] | None = None,
    orders_by_client_id: dict[str, dict[str, Any]] | None = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.get_account.return_value = account or {
        "status": "ACTIVE",
        "cash": 100_000.0,
        "buying_power": 200_000.0,
        "equity": 105_000.0,
    }
    adapter.get_position.return_value = position
    adapter.list_open_orders.return_value = list(open_orders or [])
    orders_by_id = orders_by_id or {}
    orders_by_client_id = orders_by_client_id or {}

    def _get_order(order_id: str) -> dict[str, Any]:
        if order_id in orders_by_id:
            return orders_by_id[order_id]
        raise AlpacaPaperAdapterError("not found")

    def _get_by_client(cid: str) -> dict[str, Any] | None:
        return orders_by_client_id.get(cid)

    adapter.get_order.side_effect = _get_order
    adapter.get_order_by_client_order_id.side_effect = _get_by_client
    return adapter


_NOW = datetime(2026, 7, 9, 20, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# read_audit_records
# ---------------------------------------------------------------------------


def test_read_audit_missing_file_returns_empty(tmp_path: Path) -> None:
    assert pss.read_audit_records(tmp_path / "2026-07-09.jsonl") == []


def test_read_audit_skips_blank_and_invalid_lines(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    p.write_text(
        '{"a": 1}\n\n   \nnot-json\n{"b": 2}\n',
        encoding="utf-8",
    )
    records = pss.read_audit_records(p)
    assert records == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------------
# collect_audit_orders
# ---------------------------------------------------------------------------


def test_collect_audit_orders_dedupes_by_broker_id() -> None:
    records = [
        {
            "broker_order_id": "abc",
            "client_order_id": "cid1",
            "broker_order_status": "new",
            "action": "buy_submitted",
            "timestamp_utc": "2026-07-09T15:00:00+00:00",
        },
        {
            "broker_order_id": "abc",
            "client_order_id": "cid1",
            "broker_order_status": "pending_new",
            "action": "buy_submitted",
            "timestamp_utc": "2026-07-09T15:05:00+00:00",
        },
        {"broker_order_id": None, "client_order_id": None},
    ]
    orders = pss.collect_audit_orders(records)
    assert len(orders) == 1
    assert orders[0]["broker_order_id"] == "abc"
    assert orders[0]["audit_status"] == "pending_new"


# ---------------------------------------------------------------------------
# reconcile_orders
# ---------------------------------------------------------------------------


def test_reconcile_filled_order() -> None:
    adapter = _fake_adapter(
        orders_by_id={
            "ord-1": {
                "id": "ord-1",
                "client_order_id": "cid-1",
                "status": "filled",
                "side": "buy",
                "filled_qty": 5.0,
                "filled_avg_price": 500.0,
            }
        }
    )
    audit_orders = [{
        "broker_order_id": "ord-1",
        "client_order_id": "cid-1",
        "audit_status": "new",
    }]
    result = pss.reconcile_orders(adapter, audit_orders)
    assert len(result) == 1
    assert result[0]["classification"] == "filled"
    assert result[0]["current_status"] == "filled"
    assert result[0]["filled_qty"] == 5.0
    assert result[0]["found_at_broker"] is True


def test_reconcile_pending_order() -> None:
    adapter = _fake_adapter(
        orders_by_id={
            "ord-2": {
                "id": "ord-2",
                "status": "pending_new",
                "side": "buy",
                "filled_qty": 0.0,
                "filled_avg_price": None,
            }
        }
    )
    result = pss.reconcile_orders(
        adapter, [{"broker_order_id": "ord-2", "client_order_id": None}]
    )
    assert result[0]["classification"] == "pending"


def test_reconcile_uses_client_order_id_when_broker_id_missing() -> None:
    adapter = _fake_adapter(
        orders_by_client_id={
            "cid-9": {
                "id": "ord-9",
                "client_order_id": "cid-9",
                "status": "filled",
                "side": "sell",
            }
        }
    )
    result = pss.reconcile_orders(
        adapter, [{"broker_order_id": None, "client_order_id": "cid-9"}]
    )
    assert result[0]["current_status"] == "filled"
    adapter.get_order.assert_not_called()


def test_reconcile_missing_at_broker_captures_error() -> None:
    adapter = _fake_adapter(orders_by_id={})
    result = pss.reconcile_orders(
        adapter,
        [{"broker_order_id": "missing", "client_order_id": None}],
    )
    assert result[0]["found_at_broker"] is False
    assert result[0]["fetch_error"] is not None
    assert result[0]["classification"] == "unknown"


def test_reconcile_falls_back_to_client_order_id_on_broker_id_error() -> None:
    """When get_order(broker_id) raises but client_order_id lookup succeeds,
    the fallback result is used and the fetch is not considered failed."""
    adapter = _fake_adapter(
        orders_by_id={},  # get_order raises for any id
        orders_by_client_id={
            "cid-fallback": {
                "id": "ord-fallback",
                "client_order_id": "cid-fallback",
                "status": "filled",
                "side": "buy",
                "filled_qty": 3.0,
            }
        },
    )
    result = pss.reconcile_orders(
        adapter,
        [{"broker_order_id": "stale-id", "client_order_id": "cid-fallback"}],
    )
    assert result[0]["found_at_broker"] is True
    assert result[0]["classification"] == "filled"
    assert result[0]["fetch_error"] is None
    adapter.get_order.assert_called_once_with("stale-id")
    adapter.get_order_by_client_order_id.assert_called_once_with("cid-fallback")


def test_reconcile_falls_back_when_broker_id_lookup_returns_none() -> None:
    """A None-returning broker_order_id lookup also falls back to client id."""
    adapter = _fake_adapter(
        orders_by_client_id={
            "cid-x": {"id": "ord-x", "status": "filled", "side": "buy"}
        },
    )
    adapter.get_order.side_effect = None
    adapter.get_order.return_value = None
    result = pss.reconcile_orders(
        adapter,
        [{"broker_order_id": "ord-x", "client_order_id": "cid-x"}],
    )
    assert result[0]["found_at_broker"] is True
    assert result[0]["classification"] == "filled"


def test_reconcile_missing_when_both_lookups_fail() -> None:
    """AUDIT_ORDER_MISSING_AT_BROKER only when broker AND client lookups miss."""
    adapter = _fake_adapter(
        orders_by_id={},           # get_order raises
        orders_by_client_id={},    # get_order_by_client_order_id returns None
    )
    audit_orders = [{"broker_order_id": "gone", "client_order_id": "also-gone"}]
    result = pss.reconcile_orders(adapter, audit_orders)
    assert result[0]["found_at_broker"] is False
    assert result[0]["fetch_error"] is not None
    warns = pss.detect_warnings(
        reconciled=result,
        open_orders=[],
        audit_records=[],
        orphan_claims=[],
    )
    assert any("AUDIT_ORDER_MISSING_AT_BROKER" in w for w in warns)


# ---------------------------------------------------------------------------
# detect_warnings
# ---------------------------------------------------------------------------


def test_warn_audit_order_missing_at_broker() -> None:
    warns = pss.detect_warnings(
        reconciled=[{
            "broker_order_id": "ord-x",
            "client_order_id": None,
            "found_at_broker": False,
            "classification": "unknown",
            "side": None,
        }],
        open_orders=[],
        audit_records=[],
        orphan_claims=[],
    )
    assert any("AUDIT_ORDER_MISSING_AT_BROKER" in w for w in warns)


def test_warn_open_order_not_in_audit() -> None:
    warns = pss.detect_warnings(
        reconciled=[],
        open_orders=[{"id": "ord-open", "client_order_id": "cid-x", "symbol": "SPY"}],
        audit_records=[],
        orphan_claims=[],
    )
    assert any("OPEN_ORDER_NOT_IN_AUDIT" in w for w in warns)


def test_warn_open_order_present_in_audit_is_silent() -> None:
    warns = pss.detect_warnings(
        reconciled=[{
            "broker_order_id": "ord-open",
            "client_order_id": "cid-x",
            "found_at_broker": True,
            "classification": "pending",
            "side": "buy",
        }],
        open_orders=[{"id": "ord-open", "client_order_id": "cid-x", "symbol": "SPY"}],
        audit_records=[],
        orphan_claims=[],
    )
    assert warns == []


def test_overnight_position_does_not_warn() -> None:
    """Position held from a prior day must NOT create any warning.

    Strategy allows overnight positions. Emitting a warning here would
    fire every morning while a position is held — pure noise.
    """
    warns = pss.detect_warnings(
        reconciled=[],
        open_orders=[],
        audit_records=[],
        orphan_claims=[],
    )
    assert warns == []


def test_warn_order_status_unknown_in_audit() -> None:
    warns = pss.detect_warnings(
        reconciled=[],
        open_orders=[],
        audit_records=[{"reason_codes": ["ORDER_STATUS_UNKNOWN"]}],
        orphan_claims=[],
    )
    assert any("ORDER_STATUS_UNKNOWN_IN_AUDIT" in w for w in warns)


def test_warn_orphan_claim() -> None:
    warns = pss.detect_warnings(
        reconciled=[],
        open_orders=[],
        audit_records=[],
        orphan_claims=["abc123"],
    )
    assert any("ORPHAN_CLAIM" in w and "abc123" in w for w in warns)


# ---------------------------------------------------------------------------
# position_predates_audit
# ---------------------------------------------------------------------------


def test_position_predates_audit_true_when_no_filled_buy_today() -> None:
    assert pss.position_predates_audit(
        {"symbol": "SPY", "qty": 5.0},
        reconciled=[],
    ) is True


def test_position_predates_audit_false_when_filled_buy_today() -> None:
    assert pss.position_predates_audit(
        {"symbol": "SPY", "qty": 5.0},
        reconciled=[{"classification": "filled", "side": "buy"}],
    ) is False


def test_position_predates_audit_false_when_no_position() -> None:
    assert pss.position_predates_audit(None, reconciled=[]) is False
    assert pss.position_predates_audit(
        {"symbol": "SPY", "qty": 0.0}, reconciled=[]
    ) is False


def test_position_predates_audit_ignores_sell_fills() -> None:
    assert pss.position_predates_audit(
        {"symbol": "SPY", "qty": 5.0},
        reconciled=[{"classification": "filled", "side": "sell"}],
    ) is True


# ---------------------------------------------------------------------------
# scan_orphan_claims
# ---------------------------------------------------------------------------


def test_scan_orphan_claims_returns_only_unsubmitted(tmp_path: Path) -> None:
    claims = tmp_path / "_claims"
    claims.mkdir()
    (claims / "keyA.claim").write_text("CLAIMED\n2026-07-09T15:00\n", encoding="utf-8")
    (claims / "keyB.claim").write_text("CLAIMED\n2026-07-09T15:01\n", encoding="utf-8")
    (claims / "keyB.submitted").write_text("SUBMITTED\n", encoding="utf-8")
    orphans = pss.scan_orphan_claims(tmp_path)
    assert orphans == ["keyA"]


def test_scan_orphan_claims_missing_dir(tmp_path: Path) -> None:
    assert pss.scan_orphan_claims(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# build_summary — position summary + JSON schema
# ---------------------------------------------------------------------------


def test_build_summary_position_snapshot(tmp_path: Path) -> None:
    _write_audit(
        tmp_path, "2026-07-09",
        [{
            "timestamp_utc": "2026-07-09T15:00:00+00:00",
            "broker_order_id": "ord-1",
            "client_order_id": "cid-1",
            "broker_order_status": "new",
            "action": "buy_submitted",
            "reason_codes": ["ORDER_SUBMITTED"],
        }],
    )
    adapter = _fake_adapter(
        account={"status": "ACTIVE", "cash": 90_000.0, "buying_power": 180_000.0,
                 "equity": 105_500.0},
        position={
            "symbol": "SPY", "qty": 5.0, "side": "long",
            "avg_entry_price": 500.0, "market_value": 2600.0,
            "unrealized_pl": 100.0,
        },
        open_orders=[],
        orders_by_id={
            "ord-1": {
                "id": "ord-1", "client_order_id": "cid-1",
                "status": "filled", "side": "buy",
                "filled_qty": 5.0, "filled_avg_price": 500.0,
            }
        },
    )
    summary = pss.build_summary(
        adapter=adapter,
        audit_dir=tmp_path,
        audit_date_utc="2026-07-09",
        now_utc=_NOW,
    )
    assert summary["spy_position_qty"] == 5.0
    assert summary["spy_avg_entry_price"] == 500.0
    assert summary["spy_market_value"] == 2600.0
    assert summary["spy_unrealized_pl"] == 100.0
    assert summary["account_cash"] == 90_000.0
    assert summary["portfolio_value"] == 105_500.0
    assert summary["filled_order_count"] == 1
    assert summary["pending_order_count"] == 0
    assert summary["warnings"] == []
    assert summary["position_may_predate_audit_date"] is False


def test_build_summary_overnight_position_no_warning(tmp_path: Path) -> None:
    """SPY held from a prior day + empty audit today: informational only."""
    _write_audit(tmp_path, "2026-07-09", [])
    adapter = _fake_adapter(
        position={
            "symbol": "SPY", "qty": 5.0, "side": "long",
            "avg_entry_price": 500.0, "market_value": 2600.0,
            "unrealized_pl": 100.0,
        },
    )
    summary = pss.build_summary(
        adapter=adapter,
        audit_dir=tmp_path,
        audit_date_utc="2026-07-09",
        now_utc=_NOW,
    )
    assert summary["spy_position_qty"] == 5.0
    assert summary["position_may_predate_audit_date"] is True
    assert summary["warnings"] == []


def test_build_summary_json_schema_fields(tmp_path: Path) -> None:
    adapter = _fake_adapter()
    summary = pss.build_summary(
        adapter=adapter,
        audit_dir=tmp_path,
        audit_date_utc="2026-07-09",
        now_utc=_NOW,
    )
    required = {
        "timestamp_utc",
        "audit_date_utc",
        "audit_path",
        "symbol",
        "account_cash",
        "buying_power",
        "portfolio_value",
        "spy_position_qty",
        "spy_avg_entry_price",
        "spy_market_value",
        "spy_unrealized_pl",
        "open_orders",
        "open_order_count",
        "reconciled_orders",
        "audit_order_count",
        "filled_order_count",
        "pending_order_count",
        "rejected_order_count",
        "orphan_claim_keys",
        "warnings",
        "position_may_predate_audit_date",
    }
    assert required.issubset(summary.keys())
    assert summary["audit_order_count"] == 0
    assert summary["symbol"] == "SPY"
    assert summary["spy_position_qty"] is None
    # JSON round-trip proves the schema is JSON-safe.
    dumped = json.dumps(summary, default=str)
    assert json.loads(dumped)["audit_date_utc"] == "2026-07-09"


def test_build_summary_flags_open_order_not_in_audit(tmp_path: Path) -> None:
    _write_audit(tmp_path, "2026-07-09", [])
    adapter = _fake_adapter(
        open_orders=[{
            "id": "ord-open",
            "client_order_id": "cid-open",
            "symbol": "SPY",
            "status": "new",
            "side": "buy",
        }],
    )
    summary = pss.build_summary(
        adapter=adapter,
        audit_dir=tmp_path,
        audit_date_utc="2026-07-09",
        now_utc=_NOW,
    )
    assert summary["open_order_count"] == 1
    assert any("OPEN_ORDER_NOT_IN_AUDIT" in w for w in summary["warnings"])


def test_build_summary_flags_missing_broker_order(tmp_path: Path) -> None:
    _write_audit(
        tmp_path, "2026-07-09",
        [{
            "broker_order_id": "phantom",
            "client_order_id": "cid-phantom",
            "broker_order_status": "new",
            "action": "buy_submitted",
        }],
    )
    adapter = _fake_adapter(orders_by_id={})
    summary = pss.build_summary(
        adapter=adapter,
        audit_dir=tmp_path,
        audit_date_utc="2026-07-09",
        now_utc=_NOW,
    )
    assert summary["audit_order_count"] == 1
    assert any("AUDIT_ORDER_MISSING_AT_BROKER" in w for w in summary["warnings"])
    assert summary["reconciled_orders"][0]["found_at_broker"] is False


def test_build_summary_pending_reconciliation(tmp_path: Path) -> None:
    _write_audit(
        tmp_path, "2026-07-09",
        [{
            "broker_order_id": "ord-p",
            "client_order_id": "cid-p",
            "broker_order_status": "new",
            "action": "buy_submitted",
        }],
    )
    adapter = _fake_adapter(
        open_orders=[{"id": "ord-p", "client_order_id": "cid-p", "symbol": "SPY"}],
        orders_by_id={
            "ord-p": {
                "id": "ord-p",
                "client_order_id": "cid-p",
                "status": "accepted",
                "side": "buy",
                "filled_qty": 0.0,
            }
        },
    )
    summary = pss.build_summary(
        adapter=adapter,
        audit_dir=tmp_path,
        audit_date_utc="2026-07-09",
        now_utc=_NOW,
    )
    assert summary["pending_order_count"] == 1
    assert summary["filled_order_count"] == 0
    assert summary["warnings"] == []  # open order IS in audit and no other issues


# ---------------------------------------------------------------------------
# write_summary + CLI
# ---------------------------------------------------------------------------


def test_write_summary_creates_file(tmp_path: Path) -> None:
    summary = {"audit_date_utc": "2026-07-09", "foo": "bar"}
    path = pss.write_summary(summary, tmp_path / "out", "2026-07-09")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["foo"] == "bar"


def test_main_never_calls_submit_or_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """End-to-end: fake adapter, verify no writes/submits/cancels."""
    monkeypatch.setenv("ALPACA_API_KEY", "fake")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "fake")

    adapter = _fake_adapter(
        position={
            "symbol": "SPY", "qty": 3.0, "side": "long",
            "avg_entry_price": 400.0, "market_value": 1200.0,
            "unrealized_pl": 0.0,
        },
    )
    monkeypatch.setattr(
        pss.AlpacaPaperAdapter, "from_environment",
        classmethod(lambda cls: adapter),
    )

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    out_dir = tmp_path / "out"

    rc = pss.main([
        "--audit-dir", str(audit_dir),
        "--audit-date", "2026-07-09",
        "--output-dir", str(out_dir),
    ])
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["spy_position_qty"] == 3.0
    assert payload["audit_date_utc"] == "2026-07-09"

    assert (out_dir / "2026-07-09.json").exists()

    # Prove read-only: no submit/cancel calls were made on the adapter.
    adapter.submit_market_order.assert_not_called()
    adapter.cancel_order.assert_not_called()


def test_main_no_write_flag_skips_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "fake")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "fake")
    adapter = _fake_adapter()
    monkeypatch.setattr(
        pss.AlpacaPaperAdapter, "from_environment",
        classmethod(lambda cls: adapter),
    )

    out_dir = tmp_path / "out"
    rc = pss.main([
        "--audit-dir", str(tmp_path / "audit"),
        "--audit-date", "2026-07-09",
        "--output-dir", str(out_dir),
        "--no-write",
    ])
    assert rc == 0
    assert not out_dir.exists()
    payload = json.loads(capsys.readouterr().out)
    assert "warnings" in payload


def test_main_missing_credentials_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """With no Alpaca credentials, the CLI reports the error and exits 2."""
    # The autouse fixture in conftest already strips env vars for this file,
    # but be explicit here for clarity.
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    rc = pss.main(["--no-write"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "could not construct AlpacaPaperAdapter" in err["error"]
