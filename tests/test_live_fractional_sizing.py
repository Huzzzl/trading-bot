"""
tests/test_live_fractional_sizing.py
--------------------------------------
Tests for fractional/notional live shadow sizing support.

Covers _size_candidate and check_live_sizing for both "quantity" and
"notional" modes. Fully offline: no Alpaca calls, no credentials,
no orders submitted or cancelled, no ledger writes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_intent(
    symbol: str = "SPY",
    quantity: float = 1.0,
    entry_price: float | None = 400.0,
) -> MagicMock:
    intent = MagicMock()
    intent.symbol          = symbol
    intent.side            = "buy"
    intent.order_type      = "market"
    intent.quantity        = quantity
    intent.metadata        = {"entry_price": entry_price} if entry_price is not None else {}
    intent.client_order_id = f"BT-{symbol}-001"
    return intent


def _mock_cfg(
    live_sizing_mode:            str          = "quantity",
    live_max_quantity:           float        = 1.0,
    live_max_notional:           float | None = 500.0,
    live_quantity_override:      float | None = 1.0,
    live_order_notional_override: float | None = None,
    live_max_order_notional:     float        = 100.0,
) -> MagicMock:
    cfg = MagicMock()
    cfg.execution.live_sizing_mode             = live_sizing_mode
    cfg.execution.live_max_quantity            = live_max_quantity
    cfg.execution.live_max_notional            = live_max_notional
    cfg.execution.live_quantity_override       = live_quantity_override
    cfg.execution.live_order_notional_override = live_order_notional_override
    cfg.execution.live_max_order_notional      = live_max_order_notional
    return cfg


# ---------------------------------------------------------------------------
# 1. _size_candidate — quantity mode (backward compat)
# ---------------------------------------------------------------------------

class TestSizeCandidateQuantityMode:
    def test_pass_within_limits(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                           live_max_notional=500.0)
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "PASS"
        assert row["live_sizing_mode"] == "quantity"
        assert row["effective_quantity"] == 1.0
        assert row["estimated_notional"] == 400.0
        assert row["effective_notional"] == 400.0  # alias in qty mode

    def test_fail_quantity_exceeds_max(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(quantity=5.0, entry_price=100.0)
        cfg    = _mock_cfg(live_sizing_mode="quantity", live_max_quantity=1.0,
                           live_quantity_override=None, live_max_notional=None)
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "live_max_quantity" in row["sizing_reason"]

    def test_fail_notional_exceeds_max(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=600.0)
        cfg    = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                           live_max_notional=500.0)
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "estimated_notional" in row["sizing_reason"]

    def test_fail_closed_missing_entry_price_with_notional_cap(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=None)
        cfg    = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                           live_max_notional=500.0)
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "entry_price missing" in row["sizing_reason"]

    def test_no_notional_check_when_cap_is_none(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=None)
        cfg    = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                           live_max_notional=None)
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "PASS"

    def test_new_fields_present_in_quantity_mode(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=100.0)
        cfg    = _mock_cfg(live_sizing_mode="quantity", live_max_order_notional=200.0)
        row = _size_candidate(intent, cfg)
        assert "live_sizing_mode"       in row
        assert "effective_notional"     in row
        assert "live_max_order_notional" in row
        assert row["live_sizing_mode"] == "quantity"


# ---------------------------------------------------------------------------
# 2. _size_candidate — notional mode
# ---------------------------------------------------------------------------

class TestSizeCandidateNotionalMode:
    def test_notional_100_passes_under_500_cap(self):
        """$100 effective_notional with SPY @ $400 and $500 live_max_notional passes."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
            live_max_notional=500.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "PASS"
        assert row["live_sizing_mode"] == "notional"
        assert row["effective_notional"] == 100.0
        assert row["effective_quantity"] == pytest.approx(0.25)   # 100 / 400
        assert row["estimated_notional"] == 100.0  # backward-compat alias

    def test_notional_600_fails_under_500_cap(self):
        """$600 effective_notional exceeds live_max_notional=$500 → FAIL."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=600.0,
            live_max_order_notional=1000.0,
            live_max_notional=500.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "live_max_notional" in row["sizing_reason"]

    def test_notional_exceeds_max_order_notional_fails(self):
        """effective_notional > live_max_order_notional → FAIL."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=150.0,
            live_max_order_notional=100.0,
            live_max_notional=500.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "live_max_order_notional" in row["sizing_reason"]

    def test_missing_entry_price_fails_closed(self):
        """entry_price=None in notional mode → FAIL (fail closed)."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=None)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "fail closed" in row["sizing_reason"]
        assert row["effective_quantity"] is None

    def test_zero_entry_price_fails_closed(self):
        """entry_price=0 in notional mode → FAIL (fail closed)."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=0.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "fail closed" in row["sizing_reason"]

    def test_negative_notional_override_fails(self):
        """live_order_notional_override <= 0 → FAIL."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=-50.0,
            live_max_order_notional=200.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "live_order_notional_override" in row["sizing_reason"]

    def test_none_notional_override_fails(self):
        """live_order_notional_override=None → FAIL."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=None,
            live_max_order_notional=200.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"

    def test_effective_quantity_computed_correctly(self):
        """effective_quantity = notional / entry_price."""
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=200.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=50.0,
            live_max_order_notional=100.0,
            live_max_notional=500.0,
        )
        row = _size_candidate(intent, cfg)
        assert row["effective_quantity"] == pytest.approx(0.25)  # 50 / 200

    def test_required_fields_present(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        row = _size_candidate(intent, cfg)
        for field in ("live_sizing_mode", "original_quantity", "effective_quantity",
                      "effective_notional", "entry_price", "live_max_order_notional",
                      "live_max_notional", "sizing_status", "sizing_reason"):
            assert field in row, f"missing field: {field}"


# ---------------------------------------------------------------------------
# 3. check_live_sizing — notional mode
# ---------------------------------------------------------------------------

class TestCheckLiveSizingNotionalMode:
    def test_pass_notional_100_under_500_cap(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(entry_price=400.0)]
        cfg     = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
            live_max_notional=500.0,
        )
        result = check_live_sizing(intents, cfg)
        assert result["status"] == "PASS"
        assert result["live_sizing_mode"] == "notional"
        assert result["effective_notional"] == 100.0

    def test_fail_notional_600_under_500_cap(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(entry_price=400.0)]
        cfg     = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=600.0,
            live_max_order_notional=1000.0,
            live_max_notional=500.0,
        )
        result = check_live_sizing(intents, cfg)
        assert result["status"] == "FAIL"

    def test_result_contains_new_fields(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(entry_price=400.0)]
        cfg     = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        result = check_live_sizing(intents, cfg)
        for field in ("live_sizing_mode", "effective_notional", "live_max_order_notional"):
            assert field in result, f"missing field: {field}"


# ---------------------------------------------------------------------------
# 4. check_live_sizing — quantity mode unchanged
# ---------------------------------------------------------------------------

class TestCheckLiveSizingQuantityMode:
    def test_pass_within_limits(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(entry_price=400.0)]
        cfg     = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                            live_max_notional=500.0)
        result = check_live_sizing(intents, cfg)
        assert result["status"] == "PASS"
        assert result["live_sizing_mode"] == "quantity"

    def test_fail_notional_exceeds_max(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(entry_price=600.0)]
        cfg     = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                            live_max_notional=500.0)
        result = check_live_sizing(intents, cfg)
        assert result["status"] == "FAIL"

    def test_estimated_notional_still_present(self):
        """estimated_notional backward-compat field must still be present."""
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent(entry_price=400.0)]
        cfg     = _mock_cfg(live_sizing_mode="quantity", live_quantity_override=1.0,
                            live_max_notional=500.0)
        result = check_live_sizing(intents, cfg)
        assert "estimated_notional" in result


# ---------------------------------------------------------------------------
# 5. No submit/cancel/ledger
# ---------------------------------------------------------------------------

class TestNoSubmitCancelLedger:
    def test_size_candidate_no_side_effects(self, tmp_path):
        from src.tools.live_shadow_preflight import _size_candidate
        from unittest.mock import patch
        intent = _mock_intent(entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="notional", live_order_notional_override=100.0,
                           live_max_order_notional=200.0)
        # Verify no files created in tmp_path
        _size_candidate(intent, cfg)
        assert list(tmp_path.iterdir()) == []

    def test_check_live_sizing_no_network(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        from unittest.mock import patch
        intents = [_mock_intent(entry_price=400.0)]
        cfg     = _mock_cfg(live_sizing_mode="notional", live_order_notional_override=100.0,
                            live_max_order_notional=200.0)
        # Should not raise even without any mock patching (pure local logic)
        result = check_live_sizing(intents, cfg)
        assert "status" in result


# ---------------------------------------------------------------------------
# 6. ExecutionConfig defaults
# ---------------------------------------------------------------------------

class TestExecutionConfigDefaults:
    def test_new_fields_have_correct_defaults(self):
        from src.config.loader import ExecutionConfig
        cfg = ExecutionConfig()
        assert cfg.live_sizing_mode                   == "quantity"
        assert cfg.live_order_notional_override       is None
        assert cfg.live_max_order_notional            == 100.0
        assert cfg.live_max_daily_notional            == 200.0
        assert cfg.live_max_account_notional_fraction == 0.1

    def test_existing_fields_unchanged(self):
        from src.config.loader import ExecutionConfig
        cfg = ExecutionConfig()
        assert cfg.live_max_quantity     == 1.0
        assert cfg.live_max_notional     == 500.0
        assert cfg.live_quantity_override == 1.0


# ---------------------------------------------------------------------------
# 7. screen_symbol — notional mode integration
# ---------------------------------------------------------------------------

class TestScreenSymbolNotionalMode:
    def _account(self):
        return {
            "ok": True, "warn": False, "error": None,
            "status": "active", "trading_blocked": False, "account_blocked": False,
            "buying_power": "10000", "portfolio_value": "50000",
            "positions": [], "orders": [],
        }

    def test_notional_mode_pass(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        intents = [_mock_intent("SPY", entry_price=400.0)]
        cfg     = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
            live_max_notional=500.0,
        )
        result = screen_symbol("SPY", intents, self._account(), cfg)
        assert result["best_status"] == "PASS"

    def test_notional_mode_fail_exceeds_cap(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        intents = [_mock_intent("SPY", entry_price=400.0)]
        cfg     = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=600.0,
            live_max_order_notional=1000.0,
            live_max_notional=500.0,
        )
        result = screen_symbol("SPY", intents, self._account(), cfg)
        assert result["best_status"] == "FAIL"

    def test_notional_mode_fail_missing_price(self):
        from src.tools.live_shadow_screen_symbols import screen_symbol
        intents = [_mock_intent("SPY", entry_price=None)]
        cfg     = _mock_cfg(
            live_sizing_mode="notional",
            live_order_notional_override=100.0,
            live_max_order_notional=200.0,
        )
        result = screen_symbol("SPY", intents, self._account(), cfg)
        assert result["best_status"] == "FAIL"


# ---------------------------------------------------------------------------
# live_sizing_mode validation
# ---------------------------------------------------------------------------

class TestSizingModeValidation:
    """live_sizing_mode normalization and fail-closed on invalid values."""

    def test_mixed_case_notional_normalizes(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="Notional", live_order_notional_override=100.0,
                           live_max_order_notional=200.0)
        row = _size_candidate(intent, cfg)
        assert row["live_sizing_mode"] == "notional"
        assert row["sizing_status"] == "PASS"

    def test_whitespace_notional_normalizes(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode=" notional ", live_order_notional_override=100.0,
                           live_max_order_notional=200.0)
        row = _size_candidate(intent, cfg)
        assert row["live_sizing_mode"] == "notional"
        assert row["sizing_status"] == "PASS"

    def test_mixed_case_quantity_normalizes(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="QUANTITY")
        row = _size_candidate(intent, cfg)
        assert row["live_sizing_mode"] == "quantity"
        assert row["sizing_status"] == "PASS"

    def test_invalid_mode_fails_closed(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="foo")
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"
        assert "invalid live_sizing_mode" in row["sizing_reason"]
        assert "foo" in row["sizing_reason"]
        assert row["effective_quantity"] is None or row["effective_quantity"] == ""

    def test_invalid_mode_message_mentions_valid_options(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="typo")
        row = _size_candidate(intent, cfg)
        reason = row["sizing_reason"]
        assert "quantity" in reason
        assert "notional" in reason

    def test_invalid_mode_no_network_calls(self):
        from src.tools.live_shadow_preflight import _size_candidate
        intent = _mock_intent("SPY", entry_price=400.0)
        cfg    = _mock_cfg(live_sizing_mode="bad_mode")
        row = _size_candidate(intent, cfg)
        assert row["sizing_status"] == "FAIL"

    def test_check_live_sizing_invalid_mode_returns_fail(self):
        from src.tools.live_shadow_preflight import check_live_sizing
        intents = [_mock_intent("SPY", entry_price=400.0)]
        cfg     = _mock_cfg(live_sizing_mode="NOPE")
        result  = check_live_sizing(intents, cfg)
        assert result["status"] == "FAIL"
        assert "invalid live_sizing_mode" in result.get("detail", "")
