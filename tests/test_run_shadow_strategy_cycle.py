"""Test entry point for src.tools.run_shadow_strategy_cycle.

The actual tests live in ``tests/test_shadow_strategy.py`` — this
module exists so the shared tools-inventory coverage check can find a
matching ``test_<tool>.py`` file for the runner.
"""

from __future__ import annotations

# Re-export every test from the shared module so pytest sees the same
# assertions here and the tools-inventory test finds this file.
from tests.test_shadow_strategy import *  # noqa: F401,F403
