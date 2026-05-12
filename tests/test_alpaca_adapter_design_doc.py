"""
tests/test_alpaca_adapter_design_doc.py
----------------------------------------
Tests for the Alpaca adapter design document and the updated paper-trading
readiness checklist.

No network calls, no API keys, no Alpaca SDK.
"""

from __future__ import annotations

import pathlib

import pytest

_REPO_ROOT   = pathlib.Path(__file__).resolve().parent.parent
_DESIGN_DOC  = _REPO_ROOT / "docs" / "alpaca_adapter_design.md"
_READINESS   = _REPO_ROOT / "docs" / "paper_trading_readiness.md"


# ---------------------------------------------------------------------------
# Design document existence and content
# ---------------------------------------------------------------------------

class TestAlpacaAdapterDesignDoc:
    def _text(self) -> str:
        return _DESIGN_DOC.read_text(encoding="utf-8")

    def test_doc_exists(self):
        assert _DESIGN_DOC.exists(), "docs/alpaca_adapter_design.md not found"

    def test_doc_is_not_empty(self):
        assert len(self._text().strip()) > 0

    def test_doc_mentions_paper_only(self):
        text = self._text().lower()
        assert "paper" in text and "only" in text

    def test_doc_states_no_live_trading(self):
        text = self._text().lower()
        assert "no live" in text or "never" in text or "live trading" in text

    def test_doc_mentions_alpaca_api_key_env_var(self):
        assert "ALPACA_API_KEY" in self._text()

    def test_doc_mentions_alpaca_secret_key_env_var(self):
        assert "ALPACA_SECRET_KEY" in self._text()

    def test_doc_mentions_alpaca_paper_base_url_env_var(self):
        assert "ALPACA_PAPER_BASE_URL" in self._text()

    def test_doc_mentions_client_order_id(self):
        assert "client_order_id" in self._text()

    def test_doc_mentions_market_hours_guard(self):
        text = self._text().lower()
        assert "market hour" in text or "regular" in text or "09:30" in text

    def test_doc_mentions_not_implemented_status(self):
        text = self._text()
        assert "NotImplementedError" in text or "not implemented" in text.lower()

    def test_doc_has_order_mapping_section(self):
        text = self._text().lower()
        assert "order mapping" in text

    def test_doc_has_orderresult_mapping_section(self):
        text = self._text().lower()
        assert "orderresult" in text or "order result" in text

    def test_doc_has_reconciliation_section(self):
        text = self._text().lower()
        assert "reconciliation" in text

    def test_doc_has_error_handling_section(self):
        text = self._text().lower()
        assert "error handling" in text or "error" in text

    def test_doc_has_testing_plan_section(self):
        text = self._text().lower()
        assert "testing" in text or "test plan" in text

    def test_doc_has_go_nogo_checklist(self):
        text = self._text().lower()
        assert "go" in text and ("no-go" in text or "no go" in text or "checklist" in text)

    def test_doc_mentions_fail_closed(self):
        text = self._text().lower()
        assert "fail closed" in text or "missing" in text

    def test_doc_mentions_no_api_calls_in_ci(self):
        text = self._text().lower()
        assert "ci" in text or "no real" in text

    def test_doc_mentions_market_orders_only_for_first_impl(self):
        text = self._text().lower()
        assert "market order" in text

    def test_doc_references_readiness_doc(self):
        text = self._text()
        assert "paper_trading_readiness" in text or "Paper Trading Readiness" in text


# ---------------------------------------------------------------------------
# Paper trading readiness doc references design doc and stays blocked
# ---------------------------------------------------------------------------

class TestReadinessDocUpdated:
    def _text(self) -> str:
        return _READINESS.read_text(encoding="utf-8")

    def test_readiness_doc_references_design_doc(self):
        text = self._text()
        assert "alpaca_adapter_design" in text or "alpaca_adapter_design.md" in text

    def test_readiness_doc_still_says_no_alpaca_implementation(self):
        text = self._text().lower()
        assert "not yet" in text or "no alpaca" in text or "not implemented" in text

    def test_readiness_doc_still_says_not_implemented_error(self):
        assert "NotImplementedError" in self._text()

    def test_readiness_doc_marks_adapter_design_only(self):
        text = self._text().lower()
        assert "design" in text and ("only" in text or "blocked" in text)
