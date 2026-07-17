"""Python runtime floor check shared by early entrypoints."""

from __future__ import annotations

import sys


MIN_VERSION = (3, 11)
MESSAGE = "mcp-top requires Python 3.11 or newer."


def check_python_version(version_info: tuple[int, ...] = sys.version_info) -> bool:
    """Return whether the supplied Python version can run mcp-top."""

    return version_info >= MIN_VERSION
