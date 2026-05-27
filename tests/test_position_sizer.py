"""
tests/test_position_sizer.py
-----------------------------
Unit tests for src/risk/position_sizer.py.

All tests are pure/offline — no broker calls, no credentials, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.risk.position_sizer import calculate_notional, calculate_shares_by_risk


# ---------------------------------------------------------------------------
# TestCalculateSharesByRisk — basic sizing
# ---------------------------------------------------------------------------


class TestCalculateSharesByRisk:
    def test_standard_case(self) -> None:
        # equity=10000, risk_pct=1 → risk_amount=100
        # entry=100, stop=98 → per_share_risk=2
        # shares = floor(100/2) = 50
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0) == 50

    def test_fractional_floors_down(self) -> None:
        # risk_amount=100, per_share_risk=3 → 33.33 → floor=33
        assert calculate_shares_by_risk(10_000, 1.0, 103.0, 100.0) == 33

    def test_below_one_share_returns_zero(self) -> None:
        # risk_amount=10, per_share_risk=100 → 0.1 → 0
        assert calculate_shares_by_risk(1_000, 1.0, 200.0, 100.0) == 0

    def test_exact_one_share_returns_one(self) -> None:
        # risk_amount=10, per_share_risk=10 → 1.0 → floor=1
        assert calculate_shares_by_risk(1_000, 1.0, 110.0, 100.0) == 1

    def test_large_equity(self) -> None:
        # equity=1_000_000, risk_pct=2 → risk_amount=20_000
        # entry=50, stop=48 → per_share_risk=2 → 10_000
        assert calculate_shares_by_risk(1_000_000, 2.0, 50.0, 48.0) == 10_000

    def test_small_risk_pct(self) -> None:
        # equity=10_000, risk_pct=0.1 → risk_amount=10
        # entry=100, stop=99 → per_share_risk=1 → 10
        assert calculate_shares_by_risk(10_000, 0.1, 100.0, 99.0) == 10

    def test_high_risk_pct(self) -> None:
        # equity=10_000, risk_pct=10 → risk_amount=1_000
        # entry=100, stop=95 → per_share_risk=5 → 200
        assert calculate_shares_by_risk(10_000, 10.0, 100.0, 95.0) == 200

    def test_returns_int(self) -> None:
        result = calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0)
        assert isinstance(result, int)

    def test_result_never_negative(self) -> None:
        # Extremely small risk → 0, never negative
        result = calculate_shares_by_risk(1.0, 0.001, 1000.0, 999.0)
        assert result >= 0

    def test_no_max_notional_uses_risk_only(self) -> None:
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=None) == 50


# ---------------------------------------------------------------------------
# TestMaxNotional
# ---------------------------------------------------------------------------


class TestMaxNotional:
    def test_max_notional_caps_shares(self) -> None:
        # Risk gives 50 shares @ entry=100 → notional=5000.
        # max_notional=1000 → floor(1000/100)=10 shares → cap to 10.
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=1_000) == 10

    def test_max_notional_higher_than_risk_does_not_change_result(self) -> None:
        # Risk gives 50 shares; max_notional=100_000 → 1000 shares → no cap.
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=100_000) == 50

    def test_max_notional_exactly_matching(self) -> None:
        # Risk gives 50; max_notional=5000 → floor(5000/100)=50 → min(50,50)=50.
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=5_000) == 50

    def test_max_notional_below_one_share_returns_zero(self) -> None:
        # max_notional=50 → floor(50/100)=0
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=50) == 0

    def test_max_notional_none_ignored(self) -> None:
        r1 = calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0)
        r2 = calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=None)
        assert r1 == r2

    def test_max_notional_fractional_floors(self) -> None:
        # max_notional=150 → floor(150/100)=1 → min(50,1)=1
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=150) == 1


# ---------------------------------------------------------------------------
# TestValidationEquity
# ---------------------------------------------------------------------------


class TestValidationEquity:
    def test_equity_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(0.0, 1.0, 100.0, 98.0)

    def test_equity_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(-1000.0, 1.0, 100.0, 98.0)

    def test_equity_none_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(None, 1.0, 100.0, 98.0)  # type: ignore[arg-type]

    def test_equity_string_raises(self) -> None:
        secret = "ten_thousand_dollars_secret"
        with pytest.raises(ValueError) as exc_info:
            calculate_shares_by_risk(secret, 1.0, 100.0, 98.0)  # type: ignore[arg-type]
        assert secret not in str(exc_info.value)

    def test_equity_string_message_safe(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk("bad", 1.0, 100.0, 98.0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestValidationRiskPct
# ---------------------------------------------------------------------------


class TestValidationRiskPct:
    def test_risk_pct_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 0.0, 100.0, 98.0)

    def test_risk_pct_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, -1.0, 100.0, 98.0)

    def test_risk_pct_string_raises(self) -> None:
        secret = "risk_secret_value"
        with pytest.raises(ValueError) as exc_info:
            calculate_shares_by_risk(10_000, secret, 100.0, 98.0)  # type: ignore[arg-type]
        assert secret not in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestValidationEntryPrice
# ---------------------------------------------------------------------------


class TestValidationEntryPrice:
    def test_entry_price_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 0.0, 0.0)

    def test_entry_price_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, -100.0, -105.0)


# ---------------------------------------------------------------------------
# TestValidationStopPrice
# ---------------------------------------------------------------------------


class TestValidationStopPrice:
    def test_stop_price_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 100.0, 0.0)

    def test_stop_price_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 100.0, -5.0)

    def test_stop_price_equals_entry_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 100.0, 100.0)

    def test_stop_price_above_entry_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 100.0, 105.0)

    def test_stop_price_string_raises_safely(self) -> None:
        secret = "stop_secret_value"
        with pytest.raises(ValueError) as exc_info:
            calculate_shares_by_risk(10_000, 1.0, 100.0, secret)  # type: ignore[arg-type]
        assert secret not in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestValidationMaxNotional
# ---------------------------------------------------------------------------


class TestValidationMaxNotional:
    def test_max_notional_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=0.0)

    def test_max_notional_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0, max_notional=-500.0)


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        r1 = calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0)
        r2 = calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0)
        assert r1 == r2

    def test_different_inputs_different_outputs(self) -> None:
        r1 = calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0)   # 50
        r2 = calculate_shares_by_risk(10_000, 1.0, 100.0, 95.0)   # 20
        assert r1 != r2

    def test_no_state_between_calls(self) -> None:
        # Call with params that would give 50
        calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0)
        # Same call again must give same result — no accumulated state
        assert calculate_shares_by_risk(10_000, 1.0, 100.0, 98.0) == 50


# ---------------------------------------------------------------------------
# TestCalculateNotional
# ---------------------------------------------------------------------------


class TestCalculateNotional:
    def test_standard_case(self) -> None:
        assert calculate_notional(50, 100.0) == pytest.approx(5000.0)

    def test_zero_shares(self) -> None:
        assert calculate_notional(0, 100.0) == pytest.approx(0.0)

    def test_one_share(self) -> None:
        assert calculate_notional(1, 99.5) == pytest.approx(99.5)

    def test_returns_float(self) -> None:
        assert isinstance(calculate_notional(10, 50.0), float)

    def test_negative_shares_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_notional(-1, 100.0)

    def test_zero_entry_price_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_notional(10, 0.0)

    def test_negative_entry_price_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid position sizing parameters"):
            calculate_notional(10, -50.0)

    def test_string_shares_raises_safely(self) -> None:
        secret = "secret_shares_value"
        with pytest.raises(ValueError) as exc_info:
            calculate_notional(secret, 100.0)  # type: ignore[arg-type]
        assert secret not in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestSourceScans
# ---------------------------------------------------------------------------

_SOURCE = Path("src/risk/position_sizer.py")


def _read_code(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#")[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


class TestSourceScans:
    def _code(self) -> str:
        return _read_code(_SOURCE)

    def test_no_alpaca_import(self) -> None:
        code = self._code()
        assert "import alpaca" not in code
        assert "from alpaca" not in code

    def test_no_requests_import(self) -> None:
        assert "import requests" not in self._code()

    def test_no_httpx_import(self) -> None:
        assert "import httpx" not in self._code()

    def test_no_aiohttp_import(self) -> None:
        assert "import aiohttp" not in self._code()

    def test_no_urllib_request(self) -> None:
        assert "urllib.request" not in self._code()

    def test_no_os_environ(self) -> None:
        assert "os.environ" not in self._code()

    def test_no_execution_imports(self) -> None:
        code = self._code()
        assert "from src.execution" not in code
        assert "import src.execution" not in code

    def test_no_submit_order(self) -> None:
        assert "submit_order(" not in self._code()

    def test_no_cancel_order(self) -> None:
        assert "cancel_order(" not in self._code()

    def test_no_replace_order(self) -> None:
        assert "replace_order(" not in self._code()

    def test_no_close_position(self) -> None:
        assert "close_position(" not in self._code()

    def test_no_close_all_positions(self) -> None:
        assert "close_all_positions(" not in self._code()

    def test_no_post_mutation(self) -> None:
        assert ".post(" not in self._code().lower()

    def test_no_patch_mutation(self) -> None:
        assert ".patch(" not in self._code().lower()

    def test_no_delete_mutation(self) -> None:
        assert ".delete(" not in self._code().lower()

    def test_no_trade_record_write(self) -> None:
        assert "ledger" not in self._code()

    def test_no_config_mutation(self) -> None:
        assert "config" not in self._code()
