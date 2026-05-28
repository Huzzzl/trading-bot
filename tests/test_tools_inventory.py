"""
tests/test_tools_inventory.py
------------------------------
Inventory tests for src/tools/.

Locks the classification from docs/tools_scripts_isolation_design.md:
  - 30 live safety / readiness gate tools (stay in src/tools/ permanently)
  -  4 manual live/paper guard tools (stay in src/tools/ permanently)
  -  6 paper diagnostic utilities (move candidates in a future PR)

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
# Tool classification — mirrors docs/tools_scripts_isolation_design.md § 2
# ---------------------------------------------------------------------------

LIVE_SAFETY_TOOLS: tuple[str, ...] = (
    "live_account_check",
    "live_broker_preflight_readonly",
    "live_credential_presence_guard",
    "live_dry_run_intents",
    "live_dry_run_review",
    "live_ledger_verify",
    "live_operator_config_override_review",
    "live_operator_release_checklist",
    "live_order_submission_approval",
    "live_post_submit_ledger_update_dry_run",
    "live_pre_submit_checklist",
    "live_pre_submit_ledger_dry_run",
    "live_readiness_gate",
    "live_readiness_history_review",
    "live_real_submit_pr_approval",
    "live_safety_status",
    "live_shadow_preflight",
    "live_shadow_review",
    "live_shadow_screen_review",
    "live_shadow_screen_symbols",
    "live_submit",
    "live_submit_blocked_review",
    "live_submit_enablement_gate",
    "live_submit_executor_check",
    "live_submit_plan_review",
    "live_trading_approval",
    "live_v2_approvals_review",
    "live_v2_executor_readiness_review",
    "live_v2_final_readiness_review",
    "live_v2_readiness_bundle",
)

MANUAL_GUARD_TOOLS: tuple[str, ...] = (
    "live_position_reconciliation_readonly",
    "live_single_manual_submit",
    "live_single_submit_approval_review",
    "manual_position_status_checker_readonly",
)

PAPER_DIAGNOSTIC_TOOLS: tuple[str, ...] = (
    "paper_ledger_import",
    "paper_ledger_verify",
    "paper_pre_submit_check",
    "paper_smoke_check",
    "paper_status",
    "replay_order_reconciliation",
)

ALL_TOOLS: tuple[str, ...] = LIVE_SAFETY_TOOLS + MANUAL_GUARD_TOOLS + PAPER_DIAGNOSTIC_TOOLS

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
# TestToolsInventory — counts and file existence
# ---------------------------------------------------------------------------


class TestToolsInventory:
    """Verify the tool classification counts match the design document."""

    def test_live_safety_tools_count(self) -> None:
        assert len(LIVE_SAFETY_TOOLS) == 30

    def test_manual_guard_tools_count(self) -> None:
        assert len(MANUAL_GUARD_TOOLS) == 4

    def test_paper_diagnostic_tools_count(self) -> None:
        assert len(PAPER_DIAGNOSTIC_TOOLS) == 6

    def test_all_tools_count(self) -> None:
        assert len(ALL_TOOLS) == 40

    def test_categories_are_mutually_exclusive(self) -> None:
        live_set = set(LIVE_SAFETY_TOOLS)
        manual_set = set(MANUAL_GUARD_TOOLS)
        paper_set = set(PAPER_DIAGNOSTIC_TOOLS)
        assert live_set.isdisjoint(manual_set), "overlap: LIVE_SAFETY ∩ MANUAL_GUARD"
        assert live_set.isdisjoint(paper_set), "overlap: LIVE_SAFETY ∩ PAPER_DIAGNOSTIC"
        assert manual_set.isdisjoint(paper_set), "overlap: MANUAL_GUARD ∩ PAPER_DIAGNOSTIC"

    def test_no_unclassified_tools_in_src_tools(self) -> None:
        actual = {
            p.stem
            for p in _TOOLS_DIR.glob("*.py")
            if p.stem != "__init__"
        }
        classified = set(ALL_TOOLS)
        unclassified = actual - classified
        assert not unclassified, f"Unclassified tools found: {sorted(unclassified)}"

    def test_no_phantom_tools_in_classification(self) -> None:
        actual = {
            p.stem
            for p in _TOOLS_DIR.glob("*.py")
            if p.stem != "__init__"
        }
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
# TestToolsTestCoverage — every tool has a test file
# ---------------------------------------------------------------------------


class TestToolsTestCoverage:
    """Every tool in src/tools/ must have a corresponding test file."""

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_has_test_file(self, name: str) -> None:
        assert _test_path(name).is_file(), (
            f"tests/test_{name}.py not found — "
            f"docs/tools_scripts_isolation_design.md § 1 states no tool has zero test coverage"
        )


# ---------------------------------------------------------------------------
# TestToolsSourceScan — static analysis of tool source files
# ---------------------------------------------------------------------------


class TestToolsSourceScan:
    """Source-level safety checks for every tool module."""

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
        This targets raw token/key strings while ignoring legitimate identifiers.
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
# TestLiveToolsHaveMain — live tools must expose a main() callable
# ---------------------------------------------------------------------------


class TestLiveToolsHaveMain:
    """Every live safety and manual-guard tool must define a main() function."""

    _GATED_TOOLS = LIVE_SAFETY_TOOLS + MANUAL_GUARD_TOOLS

    @pytest.mark.parametrize("name", _GATED_TOOLS)
    def test_live_tool_has_main_callable(self, name: str) -> None:
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
    """All tool modules must be importable (no import-time side effects)."""

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_is_importable(self, name: str) -> None:
        module_name = f"src.tools.{name}"
        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            # Optional third-party deps (e.g. alpaca-trade-api) may be absent;
            # that is acceptable as long as the import failure is an ImportError,
            # not a SyntaxError or NameError caused by bad module-level code.
            if "alpaca" in str(exc).lower() or "No module named" in str(exc):
                pytest.skip(f"Optional dependency absent: {exc}")
            raise
        assert mod is not None

    @pytest.mark.parametrize("name", ALL_TOOLS)
    def test_tool_does_not_module_level_import_src_main_build_engine(self, name: str) -> None:
        """No tool may import build_engine from src.main at module level.

        Function-level lazy imports are not flagged — they reflect a separate
        runtime concern and are out of scope for this inventory PR.
        """
        tree = _parse_tool(name)
        scanner = _ModuleLevelBuildEngineImportScanner()
        scanner.visit(tree)
        assert not scanner.findings, (
            f"src/tools/{name}.py has a module-level import of the removed "
            f"build_engine symbol from src.main: {scanner.findings}"
        )
