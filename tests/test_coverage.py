"""Tests for complete, path-shortened coverage rendering."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from mcp_top.coverage import Coverage, render_coverage_text


class CoverageTests(unittest.TestCase):
    def test_render_lists_every_skipped_path_reason_and_failed_query(self) -> None:
        coverage = Coverage(
            transcripts_found=3,
            transcripts_parsed=1,
            transcripts_skipped=[
                (
                    "/home/test/.claude/projects/my-project/unknown.jsonl",
                    'unknown format version "3.0"',
                ),
                (
                    "/home/test/.claude/projects/my-project/damaged.jsonl",
                    "2 of 3 lines unparseable",
                ),
            ],
            in_window=1,
            total_tool_calls=4,
            mcp_tool_calls=2,
            servers_queried_ok=1,
            servers_query_failed=[("broken", "timeout")],
            config_warnings=["invalid project config"],
            future_sessions=1,
            duplicate_tool_use=2,
            unattributed_mcp_calls=3,
            servers_unsupported=4,
            usage_note="no transcript adapter for this CLI",
        )

        rendered = render_coverage_text(coverage)

        self.assertTrue(rendered.startswith("Coverage:"))
        self.assertIn(
            "~/.claude/projects/my-project/unknown.jsonl: "
            'unknown format version "3.0"',
            rendered,
        )
        self.assertIn(
            "~/.claude/projects/my-project/damaged.jsonl: "
            "2 of 3 lines unparseable",
            rendered,
        )
        self.assertIn("server query failed broken: timeout", rendered)
        self.assertIn("config warning: invalid project config", rendered)
        self.assertIn("server queries: 1 ok, 1 failed, 4 not queried", rendered)
        self.assertIn(
            "1 transcript group(s) had future-dated timestamps and were "
            "excluded from the window",
            rendered,
        )
        self.assertIn(
            "2 duplicate tool_use block(s) were excluded from call counts",
            rendered,
        )
        self.assertIn(
            "3 MCP-prefixed call(s) could not be attributed to a server",
            rendered,
        )
        self.assertIn(
            "usage: no transcript adapter for this CLI",
            rendered,
        )

    def test_render_surfaces_partial_parse_and_windowing_gaps(self) -> None:
        coverage = Coverage(
            transcripts_found=2,
            transcripts_parsed=2,
            transcripts_skipped=[],
            in_window=1,
            total_tool_calls=4,
            mcp_tool_calls=2,
            servers_queried_ok=1,
            servers_query_failed=[],
            config_warnings=[],
            parsed_without_timestamp=1,
            bad_lines_in_parsed=3,
        )

        rendered = render_coverage_text(coverage)

        self.assertIn(
            "1 parsed transcript(s) had no usable timestamp and were "
            "excluded from the window",
            rendered,
        )
        self.assertIn(
            "3 line(s) were unparseable across parsed transcripts and are "
            "not counted",
            rendered,
        )

    def test_unknown_adapter_usage_renders_without_zero_counts(self) -> None:
        coverage = Coverage(
            transcripts_found=0,
            transcripts_parsed=0,
            transcripts_skipped=[],
            in_window=None,
            total_tool_calls=None,
            mcp_tool_calls=None,
            servers_queried_ok=0,
            servers_query_failed=[],
            config_warnings=[],
        )

        rendered = render_coverage_text(coverage)

        self.assertIn("usage: unknown (no transcript adapter)", rendered)
        self.assertNotIn("0 in window; 0 tool calls (0 MCP)", rendered)

    def test_shorten_codex_session_paths(self) -> None:
        coverage = Coverage(
            transcripts_found=1,
            transcripts_parsed=0,
            transcripts_skipped=[
                (
                    "/home/test/.codex/sessions/2026/06/rollout-a.jsonl",
                    "no session metadata found",
                )
            ],
            in_window=0,
            total_tool_calls=0,
            mcp_tool_calls=0,
            servers_queried_ok=0,
            servers_query_failed=[],
            config_warnings=[],
        )

        rendered = render_coverage_text(coverage)

        self.assertIn(
            "~/.codex/sessions/2026/06/rollout-a.jsonl: "
            "no session metadata found",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
