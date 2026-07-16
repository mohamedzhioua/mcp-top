"""Unit tests for MCP tool-name attribution."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from mcp_top.names import parse_mcp_tool_name


class ParseMcpToolNameTests(unittest.TestCase):
    def test_longest_configured_server_name_wins(self) -> None:
        self.assertEqual(
            parse_mcp_tool_name("mcp__a__b__c", {"a", "a__b"}),
            ("a__b", "c"),
        )

    def test_fallback_splits_first_separator(self) -> None:
        self.assertEqual(
            parse_mcp_tool_name("mcp__a__b__c"),
            ("a", "b__c"),
        )

    def test_mcp_prefix_without_tool_is_unattributable(self) -> None:
        self.assertIsNone(parse_mcp_tool_name("mcp__x"))


if __name__ == "__main__":
    unittest.main()
