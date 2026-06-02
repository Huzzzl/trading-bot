"""
tests/test_tools_inventory.py
------------------------------
Inventory tests for src/tools/.

PR R4 implements the first real codebase cleanup after the R1/R2/R3 preparatory
PRs.  Manual-operator tools are archived or deleted; only tools with confirmed
active imports or active automated-runtime roles remain in src/tools/.

  ACTIVE_RESEARCH_TOOLS            (3)  — cache / backtest characterisation tools
  ACTIVE_RUNTIME_CANDIDATE_TOOLS   (26) — in src/tools/; imported by active code
                                          or feeds automated runtime
  PRESERVE_RUNTIME_SUPPORT_TOOLS   (1)  — runtime support; keep in place
  -----------------------------------------------------------------------
  ACTIVE_TOOLS                     (30) — union of the above; must be in src/tools/

  ARCHIVED_TOOLS                   (10) — moved to scripts/archive/manual_live_readiness/
                                          NOT importable as src.tools.<name>
  DELETED_TOOLS_R4                 (3)  — deleted from repo in PR R4

Safety scans (Alpaca, env, mutation, secret literals) apply to ACTIVE_TOOLS (30).
main() requirement applies to ACTIVE_TOOLS (30).
Import safety applies to ACTIVE_TOOLS (30).
Test-coverage requirement applies to ACTIVE_TOOLS (30).

No broker/API/credentials access.  No order submission.
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

# Tools in src/tools/ with confirmed active import chains or automated-runtime
# roles.  Original 15 (PR R2 FREEZE_DEFERRED) plus 11 reclassified after the
# PR R4 dependency scan found active imports.
ACTIVE_RUNTIME_CANDIDATE_TOOLS: tuple[str, ...] = (
    # --- Original 15 (PR R2) ---
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
    # --- Reclassified from ARCHIVE_MANUAL after PR R4 dependency scan ---
    "live_dry_run_review",        # imported by live_pre_submit_checklist
    "live_pre_submit_checklist",  # imported by live_submit
    "paper_smoke_check",          # imported by test_paper_ledger.py (immutable)
    "paper_status",               # imported by 5 active FREEZE_DEFERRED tools
    # --- Reclassified from DELETE_CANDIDATE after PR R4 dependency scan ---
    "live_shadow_review",               # imported by live_readiness_gate
    "live_shadow_screen_review",        # imported by live_readiness_gate
    "live_v2_approvals_review",         # imported by live_submit_enablement_gate
    "live_v2_executor_readiness_review",# imported by live_submit_enablement_gate
    "live_v2_final_readiness_review",   # imported by live_v2_readiness_bundle
    "live_v2_readiness_bundle",         # v2 chain; imported transitively
    "replay_order_reconciliation",      # imported by paper_status + test_paper_ledger.py
)

# May be needed by automated runtime; keep in place.
PRESERVE_RUNTIME_SUPPORT_TOOLS: tuple[str, ...] = (
    "paper_ledger_verify",
)

# ---------------------------------------------------------------------------
# Aggregate sets
# ---------------------------------------------------------------------------

# All tools currently in src/tools/ — the only classification that should
# have files on disk under src/tools/.
ACTIVE_TOOLS: tuple[str, ...] = (
    ACTIVE_RESEARCH_TOOLS
    + ACTIVE_RUNTIME_CANDIDATE_TOOLS
    + PRESERVE_RUNTIME_SUPPORT_TOOLS
)

# Historical record: manual-operator tools moved to
# scripts/archive/manual_live_readiness/ in PR R4.
# These are NOT importable as src.tools.<name>.
ARCHIVED_TOOLS: tuple[str, ...] = (
    "live_operator_config_override_review",
    "live_operator_release_checklist",
    "live_order_submission_approval",
    "live_position_reconciliation_readonly",
    "live_real_submit_pr_approval",
    "live_single_manual_submit",
    "live_single_submit_approval_review",
    "live_submit_blocked_review",
    "live_submit_plan_review",
    "manual_position_status_checker_readonly",
)

# Historical record: tools deleted from the repo in PR R4.
DELETED_TOOLS_R4: tuple[str, ...] = (
    "live_readiness_history_review",
    "paper_ledger_import",
    "paper_pre_submit_check",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / "src" / "tools"
_TESTS_DIR = _REPO_ROOT / "tests"
_ARCHIVE_DIR = _REPO_ROOT / "scripts" / "archive" / "manual_live_readiness"


def _tool_path(name: str) -> pathlib.Path:
    return _TOOLS_DIR / f"{name}.py"


def _test_path(name: str) -> pathlib.Path:
    return _TESTS_DIR / f"test_{name}.py"


def _archive_path(name: str) -> pathlib.Path:
    return _ARCHIVE_DIR / f"{name}.py"


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
    """Verify PR R4 classification counts and physical file presence."""

    def test_active_research_tools_count(self) -> None:
        assert len(ACTIVE_RESEARCH_TOOLS) == 3

    def test_active_runtime_candidate_tools_count(self) -> None:
        assert len(ACTIVE_RUNTIME_CANDIDATE_TOOLS) == 26

    def test_preserve_runtime_support_tools_count(self) -> None:
        assert len(PRESERVE_RUNTIME_SUPPORT_TOOLS) == 1

    def test_active_tools_count(self) -> None:
        assert len(ACTIVE_TOOLS) == 30

    def test_archived_tools_count(self) -> None:
        assert len(ARCHIVED_TOOLS) == 10

    def test_deleted_tools_r4_count(self) -> None:
        assert len(DELETED_TOOLS_R4) == 3

    def test_groups_are_mutually_exclusive(self) -> None:
        groups = {
            "ACTIVE_RESEARCH": set(ACTIVE_RESEARCH_TOOLS),
            "ACTIVE_RUNTIME_CANDIDATE": set(ACTIVE_RUNTIME_CANDIDATE_TOOLS),
            "PRESERVE_RUNTIME_SUPPORT": set(PRESERVE_RUNTIME_SUPPORT_TOOLS),
            "ARCHIVED": set(ARCHIVED_TOOLS),
            "DELETED_R4": set(DELETED_TOOLS_R4),
        }
        names = list(groups.keys())
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                overlap = groups[a] & groups[b]
                assert not overlap, f"Overlap between {a} and {b}: {sorted(overlap)}"

    def test_no_unclassified_tools_in_src_tools(self) -> None:
        actual = {p.stem for p in _TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
        classified = set(ACTIVE_TOOLS)
        unclassified = actual - classified
        assert not unclassified, (
            f"Unclassified tools found in src/tools/ — add to ACTIVE_TOOLS or archive/delete: "
            f"{sorted(unclassified)}"
        )

    def test_no_phantom_tools_in_active_classification(self) -> None:
        actual = {p.stem for p in _TOOLS_DIR.glob("*.py") if p.stem != "__init__"}
        classified = set(ACTIVE_TOOLS)
        phantoms = classified - actual
        assert not phantoms, (
            f"ACTIVE_TOOLS lists tools not found on disk in src/tools/: {sorted(phantoms)}"
        )

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_active_tool_file_exists(self, name: str) -> None:
        assert _tool_path(name).is_file(), f"src/tools/{name}.py not found"


# ---------------------------------------------------------------------------
# TestToolsTestCoverage — active tools must have test files
# ---------------------------------------------------------------------------


class TestToolsTestCoverage:
    """Every active tool in src/tools/ must have a corresponding test file.

    Applies to ACTIVE_TOOLS (30) only.  Archived and deleted tools no longer
    need test coverage in tests/.
    """

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_tool_has_test_file(self, name: str) -> None:
        assert _test_path(name).is_file(), (
            f"tests/test_{name}.py not found — "
            f"all active tools in src/tools/ must have test coverage"
        )


# ---------------------------------------------------------------------------
# TestToolsSourceScan — static analysis for active tools
# ---------------------------------------------------------------------------


class TestToolsSourceScan:
    """Source-level safety checks for ACTIVE_TOOLS (30) in src/tools/.

    Archived and deleted tools are no longer scanned (not in src/tools/).
    """

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_no_module_level_alpaca_import(self, name: str) -> None:
        tree = _parse_tool(name)
        scanner = _ModuleLevelAlpacaImportScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"{name}.py has module-level Alpaca imports: {scanner.findings}"
        )

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_no_module_level_env_reads(self, name: str) -> None:
        tree = _parse_tool(name)
        scanner = _ModuleLevelEnvScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"{name}.py reads os.environ at module level: {scanner.findings}"
        )

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_no_module_level_mutation_calls(self, name: str) -> None:
        tree = _parse_tool(name)
        scanner = _ModuleLevelMutationScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"{name}.py calls an order-mutation API at module level: {scanner.findings}"
        )

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
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
    """Active tools must define a main() function for CLI usage.

    Applies to ACTIVE_TOOLS (30) only.  Archived and deleted tools are not
    required to have main().
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
# TestToolsImportSafety — active tools importable without side effects
# ---------------------------------------------------------------------------


class TestToolsImportSafety:
    """Active tool modules must be importable (no import-time side effects).

    Applies to ACTIVE_TOOLS (30) only.
    """

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
    def test_tool_is_importable(self, name: str) -> None:
        module_name = f"src.tools.{name}"
        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            if "alpaca" in str(exc).lower() or "No module named" in str(exc):
                pytest.skip(f"Optional dependency absent: {exc}")
            raise
        assert mod is not None

    @pytest.mark.parametrize("name", ACTIVE_TOOLS)
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
# TestArchiveIntegrity — PR R4 archive/delete invariants
# ---------------------------------------------------------------------------


class TestArchiveIntegrity:
    """Verify the PR R4 archive and delete operations completed correctly.

    Replaces TestCleanupEligibility (which asserted pre-R4 state).
    """

    def test_archive_directory_exists(self) -> None:
        assert _ARCHIVE_DIR.is_dir(), (
            f"Archive directory missing: {_ARCHIVE_DIR} — "
            f"PR R4 should have created scripts/archive/manual_live_readiness/"
        )

    @pytest.mark.parametrize("name", ARCHIVED_TOOLS)
    def test_archived_tool_exists_in_archive_dir(self, name: str) -> None:
        assert _archive_path(name).is_file(), (
            f"{name}.py not found in scripts/archive/manual_live_readiness/ — "
            f"PR R4 should have moved it there"
        )

    @pytest.mark.parametrize("name", ARCHIVED_TOOLS)
    def test_archived_tool_not_in_src_tools(self, name: str) -> None:
        assert not _tool_path(name).is_file(), (
            f"src/tools/{name}.py still exists — PR R4 should have removed it "
            f"(moved to scripts/archive/manual_live_readiness/)"
        )

    @pytest.mark.parametrize("name", DELETED_TOOLS_R4)
    def test_deleted_tool_not_in_src_tools(self, name: str) -> None:
        assert not _tool_path(name).is_file(), (
            f"src/tools/{name}.py still exists — PR R4 should have deleted it"
        )

    @pytest.mark.parametrize("name", DELETED_TOOLS_R4)
    def test_deleted_tool_not_in_archive_dir(self, name: str) -> None:
        assert not _archive_path(name).is_file(), (
            f"{name}.py found in archive — deleted tools should not be archived, "
            f"only truly manual tools are archived"
        )

    def test_archived_tools_are_not_active(self) -> None:
        overlap = set(ARCHIVED_TOOLS) & set(ACTIVE_TOOLS)
        assert not overlap, (
            f"Tools in both ARCHIVED_TOOLS and ACTIVE_TOOLS: {sorted(overlap)}"
        )

    def test_deleted_tools_are_not_active(self) -> None:
        overlap = set(DELETED_TOOLS_R4) & set(ACTIVE_TOOLS)
        assert not overlap, (
            f"Tools in both DELETED_TOOLS_R4 and ACTIVE_TOOLS: {sorted(overlap)}"
        )

    def test_archived_tools_not_in_deleted(self) -> None:
        overlap = set(ARCHIVED_TOOLS) & set(DELETED_TOOLS_R4)
        assert not overlap, (
            f"Tools in both ARCHIVED_TOOLS and DELETED_TOOLS_R4: {sorted(overlap)}"
        )
