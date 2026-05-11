"""
conftest.py
-----------
Ensures the ``trading-bot/`` root is on sys.path so that ``src.*`` imports
work when running ``pytest`` from within the ``trading-bot/`` directory.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
