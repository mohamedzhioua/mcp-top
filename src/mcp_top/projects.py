"""Project identity helpers shared by transcript adapters."""

from __future__ import annotations

import os
import re


def normalize_project_key(path: str) -> str:
    """Normalize an absolute filesystem path for stable project comparison."""

    key = os.path.normpath(path).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        return key.casefold()
    return key


def claude_project_slug(path: str) -> str:
    """Encode an absolute project path using Claude Code's lossy slug scheme."""

    return re.sub(r"[^A-Za-z0-9]", "-", path)
