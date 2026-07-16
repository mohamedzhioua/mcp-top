"""Unit tests for token estimate formatting."""

import json
import math
import unittest

import _path  # noqa: F401

from mcp_top.tokens import (
    HEURISTIC,
    TokenCount,
    estimate_text,
    estimate_tool_definition,
    exact_count,
    fmt,
)


class TokenTests(unittest.TestCase):
    """Checks exact and estimated token count behavior."""

    def test_estimate_text_known_answers(self) -> None:
        self.assertEqual(HEURISTIC, "chars/4")
        self.assertEqual(estimate_text("12345678"), TokenCount(tokens=2, exact=False))
        self.assertEqual(fmt(estimate_text("12345678")), "~2")
        self.assertEqual(estimate_text(""), TokenCount(tokens=0, exact=False))

    def test_exact_count_formats_without_estimate_marker(self) -> None:
        self.assertEqual(exact_count(1234), TokenCount(tokens=1234, exact=True))
        self.assertEqual(fmt(exact_count(1234)), "1,234")

    def test_estimate_tool_definition_uses_compact_json_length(self) -> None:
        tool_definition = {
            "name": "list_files",
            "description": "List files",
            "inputSchema": {"type": "object", "properties": {}},
        }
        serialized = json.dumps(
            tool_definition, separators=(",", ":"), ensure_ascii=False
        )
        expected = math.ceil(len(serialized) / 4)

        count = estimate_tool_definition(tool_definition)

        self.assertEqual(count, TokenCount(tokens=expected, exact=False))
        self.assertEqual(fmt(count), f"~{expected:,}")


if __name__ == "__main__":
    unittest.main()
