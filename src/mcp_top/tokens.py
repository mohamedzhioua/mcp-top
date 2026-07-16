"""Token counting helpers for MCP tool definitions.

The default heuristic is explicitly approximate: about 4 chars/token for
English and JSON, with rough error bars of +/-25%.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass


HEURISTIC = "chars/4"


@dataclass
class TokenCount:
    """A token count tagged as exact or estimated."""

    tokens: int
    exact: bool


def estimate_text(text: str) -> TokenCount:
    """Estimate tokens as ceil(len(text) / 4), using the chars/4 heuristic."""

    return TokenCount(tokens=math.ceil(len(text) / 4), exact=False)


def estimate_tool_definition(defn: dict) -> TokenCount:
    """Estimate tokens for a compact JSON MCP tool definition."""

    serialized = json.dumps(defn, separators=(",", ":"), ensure_ascii=False)
    return estimate_text(serialized)


def exact_count(tokens: int) -> TokenCount:
    """Return an exact token count recorded by mcp-top itself."""

    return TokenCount(tokens=tokens, exact=True)


def fmt(tc: TokenCount) -> str:
    """Format exact counts plainly and estimates with a leading tilde."""

    rendered = f"{tc.tokens:,}"
    if tc.exact:
        return rendered
    return f"~{rendered}"
