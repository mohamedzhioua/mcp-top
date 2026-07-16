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


if __name__ == "__main__":
    unittest.main()
