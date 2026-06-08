"""
tests/test_paper_architecture_invariants.py
--------------------------------------------
S23: Tests-only architecture invariant coverage for the S14-S22 offline /
paper-prep chain.

This file adds NO production behaviour. It only locks down the existing
safety architecture by inspecting already-written source files and docs and
by exercising existing pure offline functions with in-memory fixtures.

No broker/execution/runtime modules are imported. No live tools are run.
No files are created or written. No environment variables are read. No
network calls are made.

Paper trading remains not approved. Live trading remains blocked.
"""

from __future__ import annotations

import inspect
import os
from typing import Any

import pytest

import src.research.candidate_promotion as _promotion_mod
import src.research.offline_snapshot_command as _snapshot_cmd_mod
import src.research.paper_config_validator as _paper_config_mod
import src.research.paper_simulation as _paper_sim_mod
import src.research.pipeline_orchestrator as _orchestrator_mod
import src.research.report_persistence as _persistence_mod
from src.research.candidate_promotion import (
    CandidatePromotionResult,
    PromotionStatus,
    evaluate_candidate_for_promotion,
)
from src.research.paper_config_validator import (
    PaperConfigStatus,
    PaperConfigValidationResult,
    validate_paper_config,
)
from src.research.paper_simulation import (
    PaperSimulationResult,
    PaperSimulationStatus,
    run_paper_simulation,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS_DIR = os.path.join(_REPO_ROOT, "docs")


def _read_doc(name: str) -> str:
    path = os.path.join(_DOCS_DIR, name)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Core offline research modules contain no forbidden imports/patterns
# ---------------------------------------------------------------------------

_CORE_OFFLINE_MODULES = (
    _orchestrator_mod,
    _persistence_mod,
    _snapshot_cmd_mod,
    _promotion_mod,
    _paper_config_mod,
    _paper_sim_mod,
)

# These patterns must never appear as substrings anywhere in any of the core
# offline research modules -- there is no legitimate reason for any of them
# to exist in production source (unlike "os.environ"/"submit_order"/
# "place_order", which legitimately appear as string literals inside the
# paper config validator's forbidden-substring scan list).
_STRICT_FORBIDDEN_SUBSTRINGS = (
    "import requests",
    "import urllib",
    "import aiohttp",
    "import socket",
    "import subprocess",
    "import alpaca",
    "AlpacaBrokerAdapter",
    "alpaca_trade_api",
    "live_submit",
)

# These patterns describe actual imports/calls (not string-literal mentions
# inside a forbidden-substring scan list) and must never appear in any core
# offline research module.
_ACTUAL_CALL_OR_IMPORT_PATTERNS = (
    "import os",
    "os.environ[",
    "os.environ.get(",
    ".submit_order(",
    ".place_order(",
)


class TestCoreModulesHaveNoForbiddenPatterns:
    @pytest.mark.parametrize("mod", _CORE_OFFLINE_MODULES, ids=lambda m: m.__name__)
    def test_no_strict_forbidden_substrings(self, mod):
        source = inspect.getsource(mod)
        for pat in _STRICT_FORBIDDEN_SUBSTRINGS:
            assert pat not in source, (
                f"{mod.__name__} contains forbidden pattern {pat!r}"
            )

    @pytest.mark.parametrize("mod", _CORE_OFFLINE_MODULES, ids=lambda m: m.__name__)
    def test_no_actual_forbidden_calls_or_imports(self, mod):
        source = inspect.getsource(mod)
        for pat in _ACTUAL_CALL_OR_IMPORT_PATTERNS:
            assert pat not in source, (
                f"{mod.__name__} contains forbidden call/import pattern {pat!r}"
            )

    def test_paper_config_validator_forbidden_strings_are_scan_literals_only(self):
        """The paper config validator legitimately contains the string
        literals "os.environ", "submit_order", and "place_order" -- but only
        inside its _FORBIDDEN_SUBSTRINGS scan tuple, never as actual imports
        or calls."""
        source = inspect.getsource(_paper_config_mod)
        assert "import os" not in source
        assert "os.environ[" not in source
        assert "os.environ.get(" not in source
        assert ".submit_order(" not in source
        assert ".place_order(" not in source
        # The literals are present (as scan-list entries) -- confirming the
        # scan itself exists and has not been weakened or removed.
        assert '"os.environ"' in source
        assert '"submit_order"' in source
        assert '"place_order"' in source


# ---------------------------------------------------------------------------
# 2. Paper simulation remains pure offline
# ---------------------------------------------------------------------------

_SIM_VALID_CONFIG: dict[str, Any] = {
    "config_schema_version": "PC/1.0",
    "candidate_id": "A_SPY_60m_trend_breakout_1to2d",
    "run_id": "20260604T000000Z_PASS",
    "source_git_sha": "abcdef1234567890",
    "symbol": "SPY",
    "interval": "60m",
    "strategy_family": "trend_breakout",
    "holding_horizon": "one_to_two_days",
    "paper_account_label": "alpaca-paper-primary",
    "max_notional_per_position": 5000.0,
    "max_position_fraction": 0.05,
    "max_daily_loss": 500.0,
    "max_drawdown_stop": 0.10,
    "max_orders_per_day": 5,
    "min_cash_buffer": 0.20,
    "allowed_order_types": ["market", "limit"],
    "allowed_session": "regular",
    "slippage_bps_assumption": 5.0,
    "commission_bps_assumption": 2.0,
    "risk_review_notes": "Reviewed for simulation design.",
    "reviewer_name": "Alice Reviewer",
    "review_date_utc": "2026-06-04T12:00:00+00:00",
    "approval_status": "APPROVED_FOR_PAPER_SIMULATION_DESIGN",
}

_SIM_BARS = [
    {
        "timestamp": "2026-06-04T10:00:00",
        "interval": "60m",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
    },
    {
        "timestamp": "2026-06-04T11:00:00",
        "interval": "60m",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
    },
]

_FORBIDDEN_APPROVAL_KEYS = (
    "paper_trading_approved",
    "live_trading_approved",
    "approved_for_paper_trading",
    "approved_for_live_trading",
)


class TestPaperSimulationRemainsPureOffline:
    def test_run_paper_simulation_exists(self):
        assert callable(run_paper_simulation)

    def test_paper_simulation_result_has_safety_flags(self):
        for flag in (
            "broker_calls_made",
            "credentials_read",
            "network_calls_made",
            "order_action_requested",
            "live_trading_allowed",
        ):
            assert flag in PaperSimulationResult.__dataclass_fields__

    def test_default_successful_simulation_has_all_safety_flags_false(self):
        r = run_paper_simulation(
            _SIM_VALID_CONFIG, _SIM_BARS,
            start_date="2026-06-04", end_date="2026-06-04",
        )
        assert r.result == "PASS"
        assert r.status == PaperSimulationStatus.PASS
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_pass_summary_has_no_approval_keys(self):
        r = run_paper_simulation(
            _SIM_VALID_CONFIG, _SIM_BARS,
            start_date="2026-06-04", end_date="2026-06-04",
        )
        assert r.result == "PASS"
        for key in _FORBIDDEN_APPROVAL_KEYS:
            assert key not in r.summary, (
                f"simulation summary must never contain approval key {key!r}"
            )


# ---------------------------------------------------------------------------
# 3. Paper config validator remains pure offline
# ---------------------------------------------------------------------------

_VALIDATOR_VALID_CONFIG: dict[str, Any] = dict(_SIM_VALID_CONFIG)


class TestPaperConfigValidatorRemainsPureOffline:
    def test_validate_paper_config_exists(self):
        assert callable(validate_paper_config)

    def test_valid_config_returns_pass(self):
        r = validate_paper_config(_VALIDATOR_VALID_CONFIG)
        assert r.result == "PASS"
        assert r.status == PaperConfigStatus.APPROVED_FOR_PAPER_SIMULATION_DESIGN

    def test_pass_result_safety_flags_all_false(self):
        r = validate_paper_config(_VALIDATOR_VALID_CONFIG)
        assert r.result == "PASS"
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_config_with_api_key_is_blocked(self):
        cfg = dict(_VALIDATOR_VALID_CONFIG)
        cfg["api_key"] = "should-never-be-here"
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_config_with_submit_order_string_is_blocked(self):
        cfg = dict(_VALIDATOR_VALID_CONFIG)
        cfg["risk_review_notes"] = "plan to call submit_order on entry"
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE

    def test_config_with_live_trading_string_is_blocked(self):
        cfg = dict(_VALIDATOR_VALID_CONFIG)
        cfg["risk_review_notes"] = "this will enable live_trading later"
        r = validate_paper_config(cfg)
        assert r.result == "BLOCKED"
        assert r.status == PaperConfigStatus.BLOCKED_PROVENANCE


# ---------------------------------------------------------------------------
# 4. Candidate promotion remains research-only
# ---------------------------------------------------------------------------

_PROMOTION_CANDIDATE_ID = "A_SPY_60m_trend_breakout_1to2d"
_PROMOTION_RUN_ID = "20260604T000000Z_PASS"
_PROMOTION_GIT_SHA = "a" * 40

_PROMOTION_SAFE_FLAGS: dict[str, bool] = dict(
    broker_calls_made=False,
    credentials_read=False,
    network_calls_made=False,
    order_action_requested=False,
    live_trading_allowed=False,
)


def _eligible_promotion_report() -> dict[str, Any]:
    return {
        "schema_version": "S6/1.0",
        "candidate": {
            "candidate_id": _PROMOTION_CANDIDATE_ID,
            "group": "A",
            "symbol": "SPY",
            "interval": "60m",
            "strategy_family": "trend_breakout",
            "holding_horizon": "one_to_two_days",
        },
        "summary": {
            "result": "PASS",
            "blocker": None,
            "candidate_id": _PROMOTION_CANDIDATE_ID,
            "splits_requested": 4,
            "splits_evaluated": 4,
            "splits_passed": 4,
            "splits_blocked": 0,
            "splits_error": 0,
            "validations_passed": 4,
            "validations_blocked": 0,
            "average_monthly_return_mean": 0.015,
            "average_monthly_return_min": 0.005,
            "max_drawdown_worst": -0.12,
            "total_trades_sum": 80,
        },
        "splits": [],
        "safety": dict(_PROMOTION_SAFE_FLAGS),
        "notes": [],
    }


def _eligible_promotion_manifest() -> dict[str, Any]:
    return {
        "schema_version": "S9/1.0",
        "run_id": _PROMOTION_RUN_ID,
        "git_commit_sha": _PROMOTION_GIT_SHA,
        "result": "PASS",
        "safety": dict(_PROMOTION_SAFE_FLAGS),
        "files": {
            "pipeline_summary": "pipeline_summary.json",
            "reports": [f"{_PROMOTION_CANDIDATE_ID}.json"],
        },
    }


def _eligible_promotion_result() -> CandidatePromotionResult:
    return evaluate_candidate_for_promotion(
        _eligible_promotion_report(),
        manifest_dict=_eligible_promotion_manifest(),
    )


class TestCandidatePromotionRemainsResearchOnly:
    def test_evaluate_candidate_for_promotion_exists(self):
        assert callable(evaluate_candidate_for_promotion)

    def test_paper_candidate_eligible_status_exists(self):
        assert PromotionStatus.PAPER_CANDIDATE_ELIGIBLE == "PAPER_CANDIDATE_ELIGIBLE"

    def test_eligible_result_safety_flags_always_false(self):
        r = _eligible_promotion_result()
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE
        assert r.broker_calls_made is False
        assert r.credentials_read is False
        assert r.network_calls_made is False
        assert r.order_action_requested is False
        assert r.live_trading_allowed is False

    def test_eligible_result_has_no_approval_fields(self):
        r = _eligible_promotion_result()
        assert r.status is PromotionStatus.PAPER_CANDIDATE_ELIGIBLE
        for field_name in CandidatePromotionResult.__dataclass_fields__:
            assert field_name not in (
                "paper_trading_approved",
                "live_trading_approved",
                "approved_for_paper_trading",
                "approved_for_live_trading",
            )

    def test_eligible_classification_docstring_disclaims_approval(self):
        """PAPER_CANDIDATE_ELIGIBLE is documented as a research classification
        only -- not a paper or live trading approval."""
        source = inspect.getsource(_promotion_mod)
        assert "research classification only" in source
        assert "does not" in source


# ---------------------------------------------------------------------------
# 5. Docs architecture invariants
# ---------------------------------------------------------------------------

class TestPaperTradingArchitectureDesignDocInvariants:
    def test_doc_exists(self):
        path = os.path.join(_DOCS_DIR, "paper_trading_architecture_design.md")
        assert os.path.isfile(path)

    def test_contains_required_phrases(self):
        text = _read_doc("paper_trading_architecture_design.md").lower()
        assert "docs-only" in text
        assert "future-only" in text
        assert "not approved" in text
        assert "live trading remains blocked" in text
        assert "no broker" in text
        assert "no order" in text


# ---------------------------------------------------------------------------
# 6. Live readiness status remains blocked
# ---------------------------------------------------------------------------

class TestLiveReadinessStatusRemainsBlocked:
    def test_required_safety_rows_present(self):
        text = _read_doc("live_readiness_status.md")
        required_rows = (
            "`live_trading_enabled` | `false`",
            "`live_kill_switch_enabled` | `true`",
            "`live_submit_dry_run` | `true`",
            "`live_require_human_confirm` | `true`",
            "`live_trading_approved` | `false`",
            "`live_order_submission_approved` | `false`",
        )
        for row in required_rows:
            assert row in text, f"missing required safety row: {row!r}"

    def test_real_live_submit_not_implemented_not_approved(self):
        text = _read_doc("live_readiness_status.md")
        assert "Real live submit | **Not implemented. Not approved.**" in text


# ---------------------------------------------------------------------------
# 7. No production paper trading path exists
# ---------------------------------------------------------------------------

_RESEARCH_SRC_DIR = os.path.join(_REPO_ROOT, "src", "research")

_PAPER_TRADING_PATH_PATTERNS = (
    "PaperBroker",
    "PaperExecutionAdapter",
    "PaperOrderSubmitter",
    "submit_order(",
    "place_order(",
)


class TestNoProductionPaperTradingPathExists:
    def _research_source_files(self):
        for fname in sorted(os.listdir(_RESEARCH_SRC_DIR)):
            if fname.endswith(".py"):
                yield os.path.join(_RESEARCH_SRC_DIR, fname)

    def test_no_paper_trading_path_symbols_in_research_source(self):
        for path in self._research_source_files():
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
            for pat in _PAPER_TRADING_PATH_PATTERNS:
                assert pat not in source, (
                    f"{path} contains forbidden paper-trading-path pattern {pat!r}"
                )

    def test_docs_mentions_do_not_fail_source_check(self):
        """These symbol names may legitimately appear in design docs as
        named *future-only, unimplemented* concepts (S22). Their presence in
        docs must not be confused with -- and this test confirms is wholly
        separate from -- their absence from production source (checked
        above)."""
        arch_doc = _read_doc("paper_trading_architecture_design.md")
        # The architecture doc is allowed to *name* these concepts while
        # describing them as future-only and unimplemented.
        assert "future-only" in arch_doc.lower()
        # But the doc mentioning them must not coincide with their existence
        # as importable symbols anywhere in src.research.
        for path in self._research_source_files():
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
            assert "PaperExecutionAdapter" not in source
            assert "PaperOrderSubmitter" not in source


# ---------------------------------------------------------------------------
# 8. No artifact/config files added by this PR
# ---------------------------------------------------------------------------

class TestNoArtifactOrConfigFilesAdded:
    def test_no_paper_config_example_files_in_config_dir(self):
        config_dir = os.path.join(_REPO_ROOT, "config")
        if not os.path.isdir(config_dir):
            return
        for root, _dirs, files in os.walk(config_dir):
            for fname in files:
                lowered = fname.lower()
                assert "paper_config" not in lowered, (
                    f"unexpected paper config example file: "
                    f"{os.path.join(root, fname)}"
                )
                assert "paper_trading" not in lowered, (
                    f"unexpected paper trading artifact file: "
                    f"{os.path.join(root, fname)}"
                )

    def test_this_test_module_writes_nothing(self):
        """Sanity check that this module performs no file-write operations:
        no open() in write mode, no Path.write_*, no os.makedirs."""
        source = inspect.getsource(inspect.getmodule(self.test_this_test_module_writes_nothing))
        # Patterns are built via concatenation so this assertion's own source
        # text never contains the literal substring it is checking for.
        write_patterns = (
            "write" + "_text(",
            "write" + "_bytes(",
            "os." + "makedirs(",
            "os." + "mkdir(",
        )
        for pat in write_patterns:
            assert source.count(pat) == 0, f"module contains write pattern {pat!r}"
        # "open(" appears only in _read_doc (read mode "r") -- never to write.
        assert 'open(path, "r"' in source or "open(path, 'r'" in source
