"""
tests/test_tools_inventory.py
------------------------------
Inventory tests for src/tools/.

PR R2 replaces the old LIVE_SAFETY / MANUAL_GUARD / PAPER_DIAGNOSTIC / DATA
classification with a cleanup-aware model aligned with the PR R1 automated-bot
inventory plan:

  ACTIVE_RESEARCH_TOOLS      (3)  — cache / backtest characterisation tools
  ACTIVE_RUNTIME_CANDIDATE_TOOLS (15) — may feed future automated runtime
  ARCHIVE_MANUAL_TOOLS       (14) — manual-operator workflow; not part of
                                    final automated bot; eligible for archive
  DELETE_CANDIDATE_TOOLS     (10) — likely redundant; eligible for deletion
                                    after dependency scan
  PRESERVE_RUNTIME_SUPPORT_TOOLS (1) — may be needed by runtime; keep for now

Total: 43 tools (no files moved or deleted in this PR).

Safety scans (Alpaca, env, mutation, secret literals) still apply to ALL tools
while they remain physically in src/tools/.
Import safety still applies to ALL tools.
main() requirement applies only to ACTIVE_RESEARCH + ACTIVE_RUNTIME_CANDIDATE
+ PRESERVE_RUNTIME_SUPPORT tools.

No broker/API/credentials access.  No file moves.  No order submission.
No live trading.  No automated paper trading.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys
from typing import Iterator

import pytest

# ---------------------------------------------------------------------------
# Tool classification — mirrors docs/automated_bot_codebase_inventory_deletion_plan.md
# ---------------------------------------------------------------------------

# Offline research / characterisation pipeline — keep active.
ACTIVE_RESEARCH_TOOLS: tuple[str, ...] = (
    "cached_data_availability_check",
    "cached_real_data_backtest_check",
    "yahoo_cache_fetch",
)

# Tools that may plausibly feed future automated runtime or runtime safety.
# Not yet wired to automated pipeline; classified FREEZE_DEFERRED in PR R1.
ACTIVE_RUNTIME_CANDIDATE_TOOLS: tuple[str, ...] = (
    "live_account_check",
    "live_broker_preflight_readonly",
    "live_credential_presence_guard",
    "live_dry_run_intents",
    "live_ledger_verify",
    "live_post_submit_ledger_update_dry_run",
    "live_pre_submit_ledger_dry_run",
    "live_readiness_gate",
    "live_safety_status",
    "live_shadow_preflight",
    "live_shadow_screen_symbols",
    "live_submit",
    "live_submit_enablement_gate",
    "live_submit_executor_check",
    "live_trading_approval",
)

# Manual-operator workflow tools.  Not part of the final automated bot.
# Eligible for archive to scripts/archive/manual_live_readiness/ after
# dependency scan (PR R4).
ARCHIVE_MANUAL_TOOLS: tuple[str, ...] = (
    "live_dry_run_review",
    "live_operator_config_override_review",
    "live_operator_release_checklist",
    "live_order_submission_approval",
    "live_position_reconciliation_readonly",
    "live_pre_submit_checklist",
    "live_real_submit_pr_approval",
    "live_single_manual_submit",
    "live_single_submit_approval_review",
    "live_submit_blocked_review",
    "live_submit_plan_review",
    "manual_position_status_checker_readonly",
    "paper_smoke_check",
    "paper_status",
)

# Tools likely redundant with current codebase.  Eligible for deletion after
# dependency scan confirms no active import/test/config references (PR R4).
DELETE_CANDIDATE_TOOLS: tuple[str, ...] = (
    "live_readiness_history_review",
    "live_shadow_review",
    "live_shadow_screen_review",
    "live_v2_approvals_review",
    "live_v2_executor_readiness_review",
    "live_v2_final_readiness_review",
    "live_v2_readiness_bundle",
    "paper_ledger_import",
    "paper_pre_submit_check",
    "replay_order_reconciliation",
)

# May be needed by automated runtime; keep in place for now.
PRESERVE_RUNTIME_SUPPORT_TOOLS: tuple[str, ...] = (
    "paper_ledger_verify",
)

# Aggregate sets.
ALL_TOOLS: tuple[str, ...] = (
    ACTIVE_RESEARCH_TOOLS
    + ACTIVE_RUNTIME_CANDIDATE_TOOLS
    + ARCHIVE_MANUAL_TOOLS
    + DELETE_CANDIDATE_TOOLS
    + PRESERVE_RUNTIME_SUPPORT_TOOLS
)

# Active = still expected to serve as CLI-callable tools going forward.
ACTIVE_TOOLS: tuple[str, ...] = (
    ACTIVE_RESEARCH_TOOLS
    + ACTIVE_RUNTIME_CANDIDATE_TOOLS
    + PRESERVE_RUNTIME_SUPPORT_TOOLS
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / "src" / "tools"
_TESTS_DIR = _REPO_ROOT / "tests"


def _tool_path(name: str) -> pathlib.Path:
    return _TOOLS_DIR / f"{name}.py"


def _test_path(name: str) -> pathlib.Path:
    return _TESTS_DIR / f"test_{name}.py"


def _parse_tool(name: str) -> ast.Module:
    return ast.parse(_tool_path(name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AST visitors for source scanning
# ---------------------------------------------------------------------------


class _ScopeDepthVisitor(ast.NodeVisitor):
    """Base visitor that tracks function/class scope depth."""

    def __init__(self) -> None:
        self._depth = 0
        self.findings: list[str] = []

    def _enter(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = _enter  # type: ignore[assignment]
    visit_AsyncFunctionDef = _enter  # type: ignore[assignment]
    visit_ClassDef = _enter  # type: ignore[assignment]

    @property
    def at_module_level(self) -> bool:
        return self._depth == 0


class _ModuleLevelAlpacaImportScanner(_ScopeDepthVisitor):
    """Detects module-level ``import alpaca`` / ``from alpaca …`` statements."""

    def visit_Import(self, node: ast.Import) -> None:
        if self.at_module_level:
            for alias in node.names:
                if alias.name.startswith("alpaca"):
                    self.findings.append(f"import {alias.name} at line {node.lineno}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.at_module_level and node.module and node.module.startswith("alpaca"):
            self.findings.append(f"from {node.module} at line {node.lineno}")
        self.generic_visit(node)


class _ModuleLevelEnvScanner(_ScopeDepthVisitor):
    """Detects module-level ``os.environ`` / ``os.getenv`` reads."""

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self.at_module_level
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in ("environ", "getenv")
        ):
            self.findings.append(f"os.{node.attr} at line {node.lineno}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.at_module_level:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in ("environ", "getenv")
            ):
                self.findings.append(f"os.{func.attr}() at line {node.lineno}")
        self.generic_visit(node)


class _ModuleLevelMutationScanner(_ScopeDepthVisitor):
    """Detects module-level calls to order-mutation APIs."""

    _MUTATION_NAMES: frozenset[str] = frozenset(
        {"submit_order", "cancel_order", "replace_order", "close_order"}
    )

    def visit_Call(self, node: ast.Call) -> None:
        if self.at_module_level:
            func = node.func
            name: str | None = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in self._MUTATION_NAMES:
                self.findings.append(f"{name}() at line {node.lineno}")
        self.generic_visit(node)


class _ModuleLevelBuildEngineImportScanner(_ScopeDepthVisitor):
    """Detects module-level ``from src.main import build_engine`` statements."""

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.at_module_level and node.module == "src.main":
            imported = {alias.name for alias in node.names}
            if "build_engine" in imported:
                self.findings.append(f"from src.main import build_engine at line {node.lineno}")
        self.generic_visit(node)


def _collect_string_literals(tree: ast.Module) -> Iterator[str]:
    """Yield every string constant in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


# ---------------------------------------------------------------------------
# TestToolsInventory — counts, existence, and classification integrity
# ---------------------------------------------------------------------------


class TestToolsInventory:
    """Verify PR R2 classification counts and physical file presence."""

    def test_active_research_tools_count(self) -> None:
        assert len(ACTIVE_RESEARCH_TOOLS) == 3

    def test_active_runtime_candidate_tools_count(self) -> None:
        assert len(ACTIVE_RUNTIME_CANDIDATE_TOOLS) == 15

    def test_archive_manual_tools_count(self) -> None:
        assert len(ARCHIVE_MANUAL_TOOLS) == 14

    def test_delete_candidate_tools_count(self) -> None:
        assert len(DELETE_CANDIDATE_TOOLS) == 10

    def test_preserve_runtime_support_tools_count(self) -> None:
        assert len(PRESERVE_RUNTIME_SUPPORT_TOOLS) == 1

    def test_all_tools_count(self) -> None:
        assert len(ALL_TOOLS) == 43

    def test_active_tools_count(self) -> None:
        assert len(ACTIVE_TOOLS) == 19

    def test_groups_are_mutually_exclusive(self) -> None:
        groups = {
            "ACTIVE_RESEARCH": set(ACTIVE_RESEARCH_TOOLS),
            "ACTIVE_RUNTIME_CANDIDATE": set(ACTIVE_RUNTIME_CANDIDATE_TOOLS),
            "ARCHIVE_MANUAL": set(ARCHIVE_MANUAL_TOOLS),
            "DELETE_CANDIDATE": set(DELETE_CANDIDATE_TOOLS),
            "PRESERVE_RUNTIME_SUPPORT": set(PRESERVE_RUNTIME_SUPPORT_TOOLS),
        }
        names = list(groups.keys())
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                overlap = groups[a] & groups[b]
                assert not overlap, f"Overlap between {a} and {b}: {sorted(overlap)}"

    def test_no_unclassified_tools_in_src_tools(self) -> None:
        actual = {p.stem for p in _TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
        classified = set(ALL_TOOLS)
        unclassified = actual - classified
        assert not unclassified, f"Unclassified tools found: {sorted(unclassified)}"

    def test_no_phantom_tools_in_classification(self) -> None:
        actual = {p.stem for p in _TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
        classified = set(ALL_TOOLS)
        phantoms = classified - actual
        assert not phantoms, f"Classified tools not on disk: {sorted(phantoms)}"

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_file_exists(self, name: str) -> None:
        assert _tool_path(name).is_file(), f"src/tools/{name}.py not found"

    def test_all_tools_are_in_src_tools(self) -> None:
        missing = [n for n in ALL_TOOLS if not _tool_path(n).is_file()]
        assert not missing, f"Missing tool files: {missing}"


# ---------------------------------------------------------------------------
# TestToolsTestCoverage — every tool currently has a test file
# ---------------------------------------------------------------------------


class TestToolsTestCoverage:
    """Every tool in src/tools/ must have a corresponding test file.

    This applies to ALL tools while they remain physically in src/tools/.
    Archive/delete PRs (R3, R4) will update this requirement when files move.
    """

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_has_test_file(self, name: str) -> None:
        assert _test_path(name).is_file(), (
            f"tests/test_{name}.py not found — "
            f"all tools in src/tools/ must have test coverage"
        )


# ---------------------------------------------------------------------------
# TestToolsSourceScan — static analysis while tools remain in src/tools/
# ---------------------------------------------------------------------------


class TestToolsSourceScan:
    """Source-level safety checks for every tool module.

    These apply to ALL 43 tools while they remain in src/tools/.
    Archive/delete PRs may relax these requirements for moved/deleted files.
    """

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_no_module_level_alpaca_import(self, name: str) -> None:
        tree = _parse_tool(name)
        scanner = _ModuleLevelAlpacaImportScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"{name}.py has module-level Alpaca imports: {scanner.findings}"
        )

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_no_module_level_env_reads(self, name: str) -> None:
        tree = _parse_tool(name)
        scanner = _ModuleLevelEnvScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"{name}.py reads os.environ at module level: {scanner.findings}"
        )

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_no_module_level_mutation_calls(self, name: str) -> None:
        tree = _parse_tool(name)
        scanner = _ModuleLevelMutationScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"{name}.py calls an order-mutation API at module level: {scanner.findings}"
        )

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_no_hardcoded_secret_literals(self, name: str) -> None:
        """No string literal that looks like a hardcoded API key or secret token.

        Heuristic: long (≥32 chars), all-ASCII, no spaces, no path separators,
        no underscores (config keys / identifiers), no hyphens (CLI flags).
        """
        tree = _parse_tool(name)
        suspicious: list[str] = []
        for s in _collect_string_literals(tree):
            if (
                len(s) >= 32
                and s.isascii()
                and " " not in s
                and "/" not in s
                and "\\" not in s
                and "." not in s
                and "_" not in s
                and "-" not in s
            ):
                suspicious.append(repr(s[:40]))
        assert not suspicious, (
            f"{name}.py contains string literals that may be hardcoded secrets: {suspicious}"
        )

    def test_live_submit_enablement_not_true_by_default(self) -> None:
        """live_submit_enablement_gate must not set LIVE_SUBMIT_ENABLED = True at top level."""
        src = _tool_path("live_submit_enablement_gate").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "LIVE_SUBMIT_ENABLED" in target.id:
                        if isinstance(node.value, ast.Constant) and node.value.value is True:
                            raise AssertionError(
                                "live_submit_enablement_gate.py sets LIVE_SUBMIT_ENABLED = True "
                                "at module level — live submit must not be enabled by default"
                            )


# ---------------------------------------------------------------------------
# TestActiveToolsHaveMain — CLI-callable tools must define main()
# ---------------------------------------------------------------------------


class TestActiveToolsHaveMain:
    """Active tools (research + runtime candidates + preserve-support) must
    define a main() function for `python -m src.tools.<name>` CLI usage.

    ARCHIVE_MANUAL and DELETE_CANDIDATE tools are not required to have main()
    since they are targeted for removal and may already be dead-code paths.
    """

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_active_tool_has_main_callable(self, name: str) -> None:
        tree = _parse_tool(name)
        func_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "main" in func_names, (
            f"src/tools/{name}.py does not define a main() function — "
            f"required for `python -m src.tools.{name}` CLI surface"
        )


# ---------------------------------------------------------------------------
# TestToolsImportSafety — all tools importable without side effects
# ---------------------------------------------------------------------------


class TestToolsImportSafety:
    """All tool modules must be importable (no import-time side effects).

    Applies to ALL tools while they remain in src/tools/.
    """

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_is_importable(self, name: str) -> None:
        module_name = f"src.tools.{name}"
        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            if "alpaca" in str(exc).lower() or "No module named" in str(exc):
                pytest.skip(f"Optional dependency absent: {exc}")
            raise
        assert mod is not None

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_does_not_module_level_import_src_main_build_engine(self, name: str) -> None:
        """No tool may import build_engine from src.main at module level."""
        tree = _parse_tool(name)
        scanner = _ModuleLevelBuildEngineImportScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"src/tools/{name}.py has a module-level import of the removed "
            f"build_engine symbol from src.main: {scanner.findings}"
        )


# ---------------------------------------------------------------------------
# TestCleanupEligibility — documents archive/delete intent (PR R1 plan)
# ---------------------------------------------------------------------------


class TestCleanupEligibility:
    """These tests document archive/delete eligibility per the PR R1 plan.

    No files are moved or deleted in PR R2.  These tests lock in that
    ARCHIVE_MANUAL and DELETE_CANDIDATE tools are classified for future
    removal and must not be silently re-promoted to active status without
    updating this file.

    The actual move/delete happens in PR R4 after dependency scan.
    """

    def test_archive_manual_tools_are_classified(self) -> None:
        """All archive-manual tools are explicitly listed in ARCHIVE_MANUAL_TOOLS."""
        assert len(ARCHIVE_MANUAL_TOOLS) > 0
        for name in ARCHIVE_MANUAL_TOOLS:
            assert name not in ACTIVE_TOOLS, (
                f"{name} is in ARCHIVE_MANUAL_TOOLS but also in ACTIVE_TOOLS — "
                f"a tool cannot be both active and archive-eligible"
            )

    def test_delete_candidate_tools_are_classified(self) -> None:
        """All delete-candidate tools are explicitly listed in DELETE_CANDIDATE_TOOLS."""
        assert len(DELETE_CANDIDATE_TOOLS) > 0
        for name in DELETE_CANDIDATE_TOOLS:
            assert name not in ACTIVE_TOOLS, (
                f"{name} is in DELETE_CANDIDATE_TOOLS but also in ACTIVE_TOOLS — "
                f"a tool cannot be both active and delete-eligible"
            )

    def test_archive_manual_tools_still_in_src_tools(self) -> None:
        """ARCHIVE_MANUAL tools still exist in src/tools/ — not yet moved."""
        missing = [n for n in ARCHIVE_MANUAL_TOOLS if not _tool_path(n).is_file()]
        assert not missing, (
            f"ARCHIVE_MANUAL tools already missing from src/tools/ (moved/deleted without PR R4): "
            f"{missing}"
        )

    def test_delete_candidate_tools_still_in_src_tools(self) -> None:
        """DELETE_CANDIDATE tools still exist in src/tools/ — not yet removed."""
        missing = [n for n in DELETE_CANDIDATE_TOOLS if not _tool_path(n).is_file()]
        assert not missing, (
            f"DELETE_CANDIDATE tools already missing from src/tools/ (deleted without PR R4): "
            f"{missing}"
        )

    def test_archive_manual_count_matches_r1_plan(self) -> None:
        """14 tools classified as ARCHIVE_MANUAL per PR R1 inventory."""
        assert len(ARCHIVE_MANUAL_TOOLS) == 14

    def test_delete_candidate_count_matches_r1_plan(self) -> None:
        """10 tools classified as DELETE_CANDIDATE per PR R1 inventory."""
        assert len(DELETE_CANDIDATE_TOOLS) == 10

    def test_archive_manual_not_in_delete_candidate(self) -> None:
        overlap = set(ARCHIVE_MANUAL_TOOLS) & set(DELETE_CANDIDATE_TOOLS)
        assert not overlap, (
            f"Tools in both ARCHIVE_MANUAL and DELETE_CANDIDATE: {sorted(overlap)}"
        )

    def test_future_move_allowed_for_archive_manual(self) -> None:
        """Explicit confirmation: ARCHIVE_MANUAL tools may be moved to
        scripts/archive/manual_live_readiness/ in PR R4 after dependency scan.
        This test exists to document intent — it always passes.
        """
        # Documented intent: these tools are eligible for archive in PR R4.
        # Dependency scan required before move:
        #   grep -r "from src.tools.<name>" src/ tests/ scripts/
        assert True

    def test_future_delete_allowed_for_delete_candidates(self) -> None:
        """Explicit confirmation: DELETE_CANDIDATE tools may be deleted in PR R4
        after dependency scan confirms no active import/test/config references.
        This test exists to document intent — it always passes.
        """
        # Documented intent: these tools are eligible for deletion in PR R4.
        # Dependency scan required before deletion:
        #   grep -r "from src.tools.<name>" src/ tests/ scripts/
        #   grep -r "<name>" docs/ config/
        assert True
