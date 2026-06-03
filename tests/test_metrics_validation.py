"""
tests/test_metrics_validation.py
----------------------------------
Tests for src/research/metrics_validation.py (PR S4).

No broker/API/credential/env access.
No live gates unfrozen. No live trading. No order action. No real data.

Research metric conventions tested:
  - max_drawdown stored as NEGATIVE decimal (<= 0); positive values are invalid.
  - exposure_pct, win_rate, session_end_frequency, stop_loss_frequency in [0, 1].
  - total_trades >= 0; profit_factor >= 0.
  - All other numeric fields must be finite when not None.
  - bool values rejected (isinstance(True, int) is True in Python).
"""

from __future__ import annotations

import inspect
import math

import pytest

from src.research.candidate_evaluator import REQUIRED_METRIC_KEYS
from src.research.metrics_validation import (
    MetricsValidationResult,
    validate_candidate_metrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _good_metrics() -> dict:
    """A valid metrics dict with all REQUIRED_METRIC_KEYS and sensible values."""
    return {
        "cagr": 0.12,
        "average_monthly_return": 0.0095,
        "worst_month": None,
        "sharpe": 1.2,
        "sortino": None,
        "max_drawdown": -0.15,
        "calmar": 0.8,
        "win_rate": 0.55,
        "profit_factor": 1.3,
        "average_trade_return": 0.005,
        "median_trade_return": None,
        "exposure_pct": 0.35,
        "trades_per_month": 4.5,
        "total_trades": 80,
        "average_holding_bars": 6.0,
        "stop_loss_frequency": 0.2,
        "session_end_frequency": 0.1,
        "slippage_bps": None,
    }


def _all_none_metrics() -> dict:
    """All required keys present, all values None."""
    return {k: None for k in REQUIRED_METRIC_KEYS}


# ---------------------------------------------------------------------------
# TestMetricsValidationResultDataclass
# ---------------------------------------------------------------------------

class TestMetricsValidationResultDataclass:
    def test_frozen(self):
        r = validate_candidate_metrics(_good_metrics())
        with pytest.raises(Exception):
            r.result = "mutated"  # type: ignore[misc]

    def test_fields_exist(self):
        r = validate_candidate_metrics(_good_metrics())
        assert hasattr(r, "result")
        assert hasattr(r, "blocker")
        assert hasattr(r, "missing_keys")
        assert hasattr(r, "invalid_keys")
        assert hasattr(r, "warnings")
        assert hasattr(r, "broker_calls_made")
        assert hasattr(r, "credentials_read")
        assert hasattr(r, "network_calls_made")
        assert hasattr(r, "order_action_requested")
        assert hasattr(r, "live_trading_allowed")

    def test_result_is_str(self):
        r = validate_candidate_metrics(_good_metrics())
        assert isinstance(r.result, str)

    def test_missing_keys_is_tuple(self):
        r = validate_candidate_metrics(_good_metrics())
        assert isinstance(r.missing_keys, tuple)

    def test_invalid_keys_is_tuple(self):
        r = validate_candidate_metrics(_good_metrics())
        assert isinstance(r.invalid_keys, tuple)

    def test_warnings_is_tuple(self):
        r = validate_candidate_metrics(_good_metrics())
        assert isinstance(r.warnings, tuple)

    def test_live_trading_allowed_false(self):
        r = validate_candidate_metrics(_good_metrics())
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestValidateCandidateMetricsPass
# ---------------------------------------------------------------------------

class TestValidateCandidateMetricsPass:
    def test_pass_with_good_metrics(self):
        r = validate_candidate_metrics(_good_metrics())
        assert r.result == "PASS"
        assert r.blocker is None

    def test_pass_with_all_none_metrics(self):
        r = validate_candidate_metrics(_all_none_metrics())
        assert r.result == "PASS"

    def test_pass_with_negative_max_drawdown(self):
        m = _good_metrics()
        m["max_drawdown"] = -0.25
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_max_drawdown_zero(self):
        m = _good_metrics()
        m["max_drawdown"] = 0.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_total_trades_zero(self):
        m = _good_metrics()
        m["total_trades"] = 0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_all_zero_frequencies(self):
        m = _good_metrics()
        m["session_end_frequency"] = 0.0
        m["stop_loss_frequency"] = 0.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_exposure_at_boundary_zero(self):
        m = _good_metrics()
        m["exposure_pct"] = 0.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_exposure_at_boundary_one(self):
        m = _good_metrics()
        m["exposure_pct"] = 1.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_win_rate_at_boundary_one(self):
        m = _good_metrics()
        m["win_rate"] = 1.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_pass_profit_factor_zero(self):
        m = _good_metrics()
        m["profit_factor"] = 0.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_missing_keys_empty_on_pass(self):
        r = validate_candidate_metrics(_good_metrics())
        assert r.missing_keys == ()

    def test_invalid_keys_empty_on_pass(self):
        r = validate_candidate_metrics(_good_metrics())
        assert r.invalid_keys == ()


# ---------------------------------------------------------------------------
# TestMissingKeys
# ---------------------------------------------------------------------------

class TestMissingKeys:
    def test_missing_single_key_blocks(self):
        m = _good_metrics()
        del m["sharpe"]
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_missing_key_appears_in_missing_keys(self):
        m = _good_metrics()
        del m["cagr"]
        r = validate_candidate_metrics(m)
        assert "cagr" in r.missing_keys

    def test_missing_multiple_keys_all_in_tuple(self):
        m = _good_metrics()
        del m["sharpe"]
        del m["win_rate"]
        r = validate_candidate_metrics(m)
        assert "sharpe" in r.missing_keys
        assert "win_rate" in r.missing_keys
        assert len(r.missing_keys) == 2

    def test_empty_dict_blocks(self):
        r = validate_candidate_metrics({})
        assert r.result == "BLOCKED"
        assert len(r.missing_keys) == len(REQUIRED_METRIC_KEYS)

    def test_missing_blocker_mentions_missing(self):
        m = _good_metrics()
        del m["total_trades"]
        r = validate_candidate_metrics(m)
        assert "missing" in (r.blocker or "").lower()

    def test_missing_total_trades_blocks(self):
        m = _good_metrics()
        del m["total_trades"]
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"


# ---------------------------------------------------------------------------
# TestInvalidTypes
# ---------------------------------------------------------------------------

class TestInvalidTypes:
    def test_invalid_type_string_blocks(self):
        m = _good_metrics()
        m["sharpe"] = "high"
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"
        assert any("sharpe" in s for s in r.invalid_keys)

    def test_invalid_type_list_blocks(self):
        m = _good_metrics()
        m["cagr"] = [0.12]
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_invalid_type_bool_blocks(self):
        m = _good_metrics()
        m["total_trades"] = True  # bool is a subclass of int but not a valid metric
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_invalid_non_finite_inf_blocks(self):
        m = _good_metrics()
        m["sharpe"] = math.inf
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_invalid_non_finite_neg_inf_blocks(self):
        m = _good_metrics()
        m["cagr"] = -math.inf
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_invalid_non_finite_nan_blocks(self):
        m = _good_metrics()
        m["max_drawdown"] = math.nan
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_invalid_key_reported_in_tuple(self):
        m = _good_metrics()
        m["win_rate"] = "unknown"
        r = validate_candidate_metrics(m)
        assert any("win_rate" in s for s in r.invalid_keys)


# ---------------------------------------------------------------------------
# TestMaxDrawdownConvention
# ---------------------------------------------------------------------------

class TestMaxDrawdownConvention:
    def test_positive_max_drawdown_blocked(self):
        m = _good_metrics()
        m["max_drawdown"] = 0.15  # positive: wrong convention
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_positive_max_drawdown_in_invalid_keys(self):
        m = _good_metrics()
        m["max_drawdown"] = 0.15
        r = validate_candidate_metrics(m)
        assert any("max_drawdown" in s for s in r.invalid_keys)

    def test_negative_max_drawdown_passes(self):
        m = _good_metrics()
        m["max_drawdown"] = -0.40
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_zero_max_drawdown_passes(self):
        m = _good_metrics()
        m["max_drawdown"] = 0.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"

    def test_max_drawdown_none_passes(self):
        m = _good_metrics()
        m["max_drawdown"] = None
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestRangeChecks
# ---------------------------------------------------------------------------

class TestRangeChecks:
    def test_total_trades_negative_blocked(self):
        m = _good_metrics()
        m["total_trades"] = -1
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_exposure_pct_above_one_blocked(self):
        m = _good_metrics()
        m["exposure_pct"] = 1.01
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_exposure_pct_below_zero_blocked(self):
        m = _good_metrics()
        m["exposure_pct"] = -0.01
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_win_rate_above_one_blocked(self):
        m = _good_metrics()
        m["win_rate"] = 1.001
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_win_rate_below_zero_blocked(self):
        m = _good_metrics()
        m["win_rate"] = -0.1
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_session_end_frequency_above_one_blocked(self):
        m = _good_metrics()
        m["session_end_frequency"] = 1.1
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_stop_loss_frequency_below_zero_blocked(self):
        m = _good_metrics()
        m["stop_loss_frequency"] = -0.05
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_profit_factor_negative_blocked(self):
        m = _good_metrics()
        m["profit_factor"] = -0.5
        r = validate_candidate_metrics(m)
        assert r.result == "BLOCKED"

    def test_profit_factor_zero_passes(self):
        m = _good_metrics()
        m["profit_factor"] = 0.0
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"


# ---------------------------------------------------------------------------
# TestWarnings
# ---------------------------------------------------------------------------

class TestWarnings:
    def test_warnings_for_always_none_s3_fields(self):
        m = _all_none_metrics()
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"
        # worst_month, sortino, median_trade_return, slippage_bps are always None in S3
        warning_text = " ".join(r.warnings)
        assert "worst_month" in warning_text
        assert "sortino" in warning_text
        assert "median_trade_return" in warning_text
        assert "slippage_bps" in warning_text

    def test_no_warning_when_field_has_value(self):
        m = _good_metrics()
        m["sortino"] = 1.5
        r = validate_candidate_metrics(m)
        assert not any("sortino" in w for w in r.warnings)

    def test_warnings_do_not_cause_blocked(self):
        m = _all_none_metrics()
        r = validate_candidate_metrics(m)
        assert r.result == "PASS"
        # warnings are informational only


# ---------------------------------------------------------------------------
# TestSafetyFlags
# ---------------------------------------------------------------------------

class TestSafetyFlags:
    def test_all_flags_false_on_pass(self):
        r = validate_candidate_metrics(_good_metrics())
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_all_flags_false_on_blocked(self):
        r = validate_candidate_metrics({})
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False


# ---------------------------------------------------------------------------
# TestNoForbiddenImports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def _src(self) -> str:
        import src.research.metrics_validation as _m
        return inspect.getsource(_m)

    def test_no_alpaca_broker_adapter(self):
        assert "AlpacaBrokerAdapter" not in self._src()

    def test_no_os_environ(self):
        assert "os.environ" not in self._src()

    def test_no_requests(self):
        assert "requests" not in self._src()

    def test_no_live_submit(self):
        assert "live_submit" not in self._src()

    def test_no_live_trading_allowed_true(self):
        assert "live_trading_allowed=True" not in self._src()

    def test_no_broker_calls_true(self):
        assert "broker_calls_made=True" not in self._src()
