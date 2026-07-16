"""Unit tests for the Claude Code transcript adapter."""

from __future__ import annotations

import os
import tempfile
import unittest

import _path  # noqa: F401

from mcp_top.adapters.claude_code import find_transcripts, parse_session


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "transcripts")


class ClaudeCodeAdapterTests(unittest.TestCase):
    """Exercises supported, unsupported, and damaged transcript files."""

    def test_supported_session_extracts_all_tool_calls(self) -> None:
        path = os.path.join(FIXTURES, "good_session.jsonl")

        result = parse_session(path)

        counts: dict[str, int] = {}
        for call in result.tool_calls:
            counts[call.tool] = counts.get(call.tool, 0) + 1
        self.assertEqual(result.status, "parsed")
        self.assertIsNone(result.skip_reason)
        self.assertEqual(result.session_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(result.versions_seen, ["2.1.207"])
        self.assertEqual(result.bad_lines, 0)
        self.assertEqual(
            counts,
            {
                "mcp__github__get_pr": 2,
                "Bash": 3,
                "mcp__weather__forecast": 1,
            },
        )
        self.assertEqual(
            [call.tool for call in result.tool_calls if call.sidechain], ["Bash"]
        )
        self.assertEqual(result.first_ts, "2026-06-10T10:00:00.000Z")
        self.assertEqual(result.last_ts, "2026-06-10T10:04:00.000Z")

    def test_second_supported_version_is_parsed(self) -> None:
        path = os.path.join(FIXTURES, "good_session_2.jsonl")

        result = parse_session(path)

        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.versions_seen, ["2.1.211"])
        self.assertEqual(
            [call.tool for call in result.tool_calls],
            ["mcp__weather__forecast", "Read"],
        )

    def test_unknown_version_is_skipped_without_calls(self) -> None:
        path = os.path.join(FIXTURES, "unknown_version.jsonl")

        result = parse_session(path)

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.skip_reason, 'unknown format version "3.0.1"')
        self.assertEqual(result.versions_seen, ["3.0.1"])
        self.assertEqual(result.tool_calls, [])

    def test_mostly_unparseable_file_is_skipped(self) -> None:
        path = os.path.join(FIXTURES, "garbage.jsonl")

        result = parse_session(path)

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.skip_reason, "4 of 5 lines unparseable")
        self.assertEqual(result.bad_lines, 4)
        self.assertEqual(result.tool_calls, [])

    def test_file_without_version_marker_is_skipped(self) -> None:
        path = os.path.join(FIXTURES, "no_version.jsonl")

        result = parse_session(path)

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.skip_reason, "no version marker found")
        self.assertEqual(result.versions_seen, [])
        self.assertEqual(result.tool_calls, [])

    def test_unreadable_file_is_reported_without_raising(self) -> None:
        path = os.path.join(FIXTURES, "missing.jsonl")

        result = parse_session(path)

        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.skip_reason.startswith("unreadable: "))
        self.assertEqual(result.tool_calls, [])

    def test_find_transcripts_returns_sorted_project_jsonl_paths(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            projects = os.path.join(home, ".claude", "projects")
            first = os.path.join(projects, "a-project")
            second = os.path.join(projects, "b-project")
            os.makedirs(first)
            os.makedirs(second)
            paths = [
                os.path.join(second, "z.jsonl"),
                os.path.join(first, "a.jsonl"),
            ]
            for path in paths:
                with open(path, "w", encoding="utf-8"):
                    pass
            with open(os.path.join(projects, "ignored.jsonl"), "w", encoding="utf-8"):
                pass

            found = find_transcripts(home)

        self.assertEqual(found, sorted(paths))


if __name__ == "__main__":
    unittest.main()
