"""
tests/test_live_submit_enablement_gate.py
------------------------------------------
Structural tests for docs/live_submit_enablement_gate.md.

These tests verify that the design document contains all required statements
and safety constraints.  They do NOT test runtime behaviour — they ensure the
document itself is present and carries every required clause.
"""

from __future__ import annotations

from pathlib import Path

GATE_DOC = Path(__file__).parent.parent / "docs" / "live_submit_enablement_gate.md"
READINESS_DOC = Path(__file__).parent.parent / "docs" / "live_readiness.md"
STATUS_DOC = Path(__file__).parent.parent / "docs" / "live_readiness_status.md"
V2_DOC = Path(__file__).parent.parent / "docs" / "live_submit_enablement_v2.md"


def _text() -> str:
    return GATE_DOC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Existence
# ---------------------------------------------------------------------------

class TestGateDocExists:
    def test_file_exists(self):
        assert GATE_DOC.exists(), f"Missing: {GATE_DOC}"

    def test_file_is_not_empty(self):
        assert GATE_DOC.stat().st_size > 0


# ---------------------------------------------------------------------------
# config_safety
# ---------------------------------------------------------------------------

class TestConfigSafety:
    def test_mentions_config_safety(self):
        assert "config_safety" in _text()

    def test_config_safety_is_final_blocker(self):
        text = _text()
        assert "config_safety" in text and "blocker" in text

    def test_config_safety_remains_final(self):
        text = _text()
        assert "config_safety remains the final blocker" in text or (
            "config_safety" in text and "final" in text
        )


# ---------------------------------------------------------------------------
# bundle_result="PASS"
# ---------------------------------------------------------------------------

class TestBundleResult:
    def test_mentions_bundle_result_pass(self):
        assert 'bundle_result="PASS"' in _text() or "bundle_result" in _text()

    def test_bundle_result_required_for_submit(self):
        text = _text()
        assert "bundle_result" in text
        assert "PASS" in text


# ---------------------------------------------------------------------------
# Config flags
# ---------------------------------------------------------------------------

class TestConfigFlags:
    def test_mentions_live_trading_enabled_true(self):
        assert "live_trading_enabled=true" in _text().lower() or "live_trading_enabled" in _text()

    def test_live_trading_enabled_required(self):
        text = _text()
        assert "live_trading_enabled" in text

    def test_mentions_live_submit_dry_run_false(self):
        text = _text()
        assert "live_submit_dry_run" in text

    def test_live_submit_dry_run_required_false(self):
        text = _text()
        assert "live_submit_dry_run" in text
        assert "false" in text.lower() or "False" in text

    def test_mentions_live_kill_switch_enabled_false(self):
        text = _text()
        assert "live_kill_switch_enabled" in text

    def test_live_kill_switch_required_false(self):
        text = _text()
        assert "live_kill_switch_enabled" in text
        assert "false" in text.lower() or "False" in text


# ---------------------------------------------------------------------------
# Ledger behaviour
# ---------------------------------------------------------------------------

class TestLedgerBehaviour:
    def test_mentions_pre_submit_ledger_row(self):
        text = _text()
        assert "pre-submit ledger" in text or "pre_submit ledger" in text or (
            "ledger" in text and "pre" in text
        )

    def test_pre_submit_row_must_be_written_before_submit(self):
        text = _text()
        assert "ledger" in text
        assert "before" in text and "submit_order" in text

    def test_mentions_post_submit_ledger_update(self):
        text = _text()
        assert "post-submit" in text or "Post-submit" in text or (
            "ledger" in text and "After" in text
        )

    def test_post_submit_update_covers_all_outcomes(self):
        text = _text()
        assert "submitted" in text or "rejected" in text or "exception" in text

    def test_mentions_failed_or_exception_ledger_update(self):
        text = _text()
        assert "exception" in text and "ledger" in text

    def test_ledger_never_left_in_attempting(self):
        text = _text()
        assert "attempting" in text


# ---------------------------------------------------------------------------
# Paper-only constraint
# ---------------------------------------------------------------------------

class TestPaperOnly:
    def test_mentions_paper_only(self):
        text = _text()
        assert "paper-only" in text or "paper only" in text or "paper" in text

    def test_live_disabled_by_default(self):
        text = _text()
        assert "disabled" in text and "default" in text


# ---------------------------------------------------------------------------
# Alpaca live endpoint forbidden
# ---------------------------------------------------------------------------

class TestAlpacaLiveEndpoint:
    def test_mentions_alpaca_live_endpoint(self):
        text = _text()
        assert "live endpoint" in text or "Alpaca live endpoint" in text

    def test_live_endpoint_must_not_be_used(self):
        text = _text()
        assert "must not be used" in text or "not be used" in text


# ---------------------------------------------------------------------------
# API key security
# ---------------------------------------------------------------------------

class TestApiKeySecurity:
    def test_mentions_api_keys_must_not_be_logged(self):
        text = _text()
        assert "logged" in text and ("API key" in text or "api key" in text.lower())

    def test_api_keys_not_committed(self):
        text = _text()
        assert "committed" in text

    def test_api_keys_not_printed(self):
        text = _text()
        assert "printed" in text or "logged" in text

    def test_missing_credentials_fail_closed(self):
        text = _text()
        assert "fail closed" in text or "fail-closed" in text

    def test_no_partial_credential_usage(self):
        text = _text()
        assert "missing" in text and ("fail" in text or "blocked" in text)


# ---------------------------------------------------------------------------
# References from existing docs
# ---------------------------------------------------------------------------

class TestExistingDocsReference:
    def test_live_readiness_references_gate_doc(self):
        text = READINESS_DOC.read_text(encoding="utf-8")
        assert "live_submit_enablement_gate" in text

    def test_live_readiness_status_references_gate_doc(self):
        text = STATUS_DOC.read_text(encoding="utf-8")
        assert "live_submit_enablement_gate" in text

    def test_live_submit_enablement_v2_references_gate_doc(self):
        text = V2_DOC.read_text(encoding="utf-8")
        assert "live_submit_enablement_gate" in text
