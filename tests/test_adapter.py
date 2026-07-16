"""Unit tests for the Claude Code transcript adapter."""

from __future__ import annotations

import os
import json
import tempfile
import unittest

import _path  # noqa: F401

from mcp_top.adapters.claude_code import (
    find_transcripts,
    parse_session,
    session_key,
)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "transcripts")


class ClaudeCodeAdapterTests(unittest.TestCase):
    """Exercises supported, unsupported, and damaged transcript files."""

    def test_supported_session_extracts_all_tool_calls(self) -> None:
        path = os.path.join(FIXTURES, "good_session.jsonl")

        result = parse_session(path)

        counts: dict[str, int] = {}
        for call in result.tool_calls:
            counts[call.raw] = counts.get(call.raw, 0) + 1
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
            [call.raw for call in result.tool_calls if call.sidechain], ["Bash"]
        )
        github_call = result.tool_calls[0]
        self.assertEqual(github_call.kind, "mcp")
        self.assertEqual(github_call.server, "github")
        self.assertEqual(github_call.tool, "get_pr")
        bash_call = result.tool_calls[1]
        self.assertEqual(bash_call.kind, "builtin")
        self.assertIsNone(bash_call.server)
        self.assertIsNone(bash_call.tool)
        self.assertEqual(result.first_ts, "2026-06-10T10:00:00.000Z")
        self.assertEqual(result.last_ts, "2026-06-10T10:04:00.000Z")

    def test_second_supported_version_is_parsed(self) -> None:
        path = os.path.join(FIXTURES, "good_session_2.jsonl")

        result = parse_session(path)

        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.versions_seen, ["2.1.211"])
        self.assertEqual(
            [call.raw for call in result.tool_calls],
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
                os.path.join(first, "session-id", "subagents", "agent-x.jsonl"),
            ]
            for path in paths:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8"):
                    pass
            with open(os.path.join(projects, "ignored.jsonl"), "w", encoding="utf-8"):
                pass

            found = find_transcripts(home)

        self.assertEqual(found, sorted(paths))

    def test_session_key_groups_nested_transcripts_under_parent(self) -> None:
        home = os.path.join("home", "test")
        project = os.path.join(home, ".claude", "projects", "slug")

        self.assertEqual(
            session_key(os.path.join(project, "uuid.jsonl"), home),
            "slug/uuid",
        )
        self.assertEqual(
            session_key(
                os.path.join(
                    project, "uuid", "subagents", "agent-123.jsonl"
                ),
                home,
            ),
            "slug/uuid",
        )

    def test_version_policy_rejects_malformed_and_incomplete_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            malformed = self._write_records(
                temporary,
                "malformed.jsonl",
                [self._record(7)],
            )
            incomplete = self._write_records(
                temporary,
                "incomplete.jsonl",
                [self._record("2.")],
            )
            supported = self._write_records(
                temporary,
                "supported.jsonl",
                [self._record("2.1.207")],
            )

            malformed_result = parse_session(malformed)
            incomplete_result = parse_session(incomplete)
            supported_result = parse_session(supported)

        self.assertEqual(malformed_result.skip_reason, "malformed version marker")
        self.assertEqual(
            incomplete_result.skip_reason, 'unknown format version "2."'
        )
        self.assertEqual(supported_result.status, "parsed")

    def test_timestamps_use_valid_minimum_and_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_records(
                temporary,
                "out-of-order.jsonl",
                [
                    self._record("2.1.207", "2026-06-10T12:00:00Z"),
                    self._record("2.1.207", "not-a-timestamp"),
                    self._record("2.1.207", "2026-06-10T10:00:00Z"),
                    self._record("2.1.207", "2026-06-10T11:00:00Z"),
                ],
            )

            result = parse_session(path)

        self.assertEqual(result.first_ts, "2026-06-10T10:00:00Z")
        self.assertEqual(result.last_ts, "2026-06-10T12:00:00Z")

    def test_duplicate_tool_use_ids_are_counted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = self._record("2.1.207")
            record["type"] = "assistant"
            record["message"] = {
                "content": [
                    {"type": "tool_use", "id": "same", "name": "First"},
                    {"type": "tool_use", "id": "same", "name": "Second"},
                    {"type": "tool_use", "name": "NoId"},
                    {"type": "tool_use", "id": 7, "name": "NonStringId"},
                ]
            }
            path = self._write_records(temporary, "duplicates.jsonl", [record])

            result = parse_session(path)

        self.assertEqual(
            [call.raw for call in result.tool_calls],
            ["First", "NoId", "NonStringId"],
        )
        self.assertEqual(result.duplicate_tool_use, 1)

    def test_mcp_prefixed_unparseable_call_is_unattributed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = self._record("2.1.207")
            record["type"] = "assistant"
            record["message"] = {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__unattributed",
                    }
                ]
            }
            path = self._write_records(temporary, "unattributed.jsonl", [record])

            result = parse_session(path)

        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.tool_calls[0].raw, "mcp__unattributed")
        self.assertEqual(result.tool_calls[0].kind, "mcp-unattributed")
        self.assertIsNone(result.tool_calls[0].server)
        self.assertIsNone(result.tool_calls[0].tool)

    def test_configured_server_attribution_prefers_longest_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = self._record("2.1.207")
            record["type"] = "assistant"
            record["message"] = {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__a__b__complex",
                    }
                ]
            }
            path = self._write_records(temporary, "configured.jsonl", [record])

            result = parse_session(path, {"a", "a__b"})

        self.assertEqual(result.tool_calls[0].kind, "mcp")
        self.assertEqual(result.tool_calls[0].server, "a__b")
        self.assertEqual(result.tool_calls[0].tool, "complex")

    def test_pathological_json_line_is_counted_bad_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, "deep.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("[" * 2000 + "]" * 2000 + "\n")
                for _ in range(10):
                    handle.write(json.dumps(self._record("2.1.207")) + "\n")

            result = parse_session(path)

        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.bad_lines, 1)

    def _record(
        self, version: object, timestamp: str = "2026-06-10T10:00:00Z"
    ) -> dict:
        return {
            "type": "user",
            "version": version,
            "timestamp": timestamp,
            "sessionId": "session-id",
        }

    def _write_records(
        self, directory: str, name: str, records: list[dict]
    ) -> str:
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return path


if __name__ == "__main__":
    unittest.main()
