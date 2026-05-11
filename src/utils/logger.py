"""
utils/logger.py
---------------
Centralised logging setup.  Call ``get_logger(__name__)`` in every module
to obtain a pre-configured logger that respects the root log level set by
``configure_logging()``.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO", fmt: str | None = None) -> None:
    """Configure the root logger once at application startup.

    Parameters
    ----------
    level:
        Logging level string, e.g. ``"DEBUG"``, ``"INFO"``, ``"WARNING"``.
    fmt:
        Optional format string.  Defaults to a sensible timestamped format.
    """
    fmt = fmt or "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))

    root = logging.getLogger()
    # Avoid duplicate handlers when called multiple times (e.g. in tests)
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module.
    """
    return logging.getLogger(name)
