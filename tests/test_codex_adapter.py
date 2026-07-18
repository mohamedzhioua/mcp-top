"""Unit tests for the Codex transcript adapter."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import _path  # noqa: F401

from mcp_top.adapters.codex import find_transcripts, parse_session, session_key
from mcp_top.projects import normalize_project_key


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "codex_sessions")
GOOD = os.path.join(
    FIXTURES,
    "rollout-2026-06-10T100000Z-11111111-1111-1111-1111-111111111111.jsonl",
)


class CodexAdapterTests(unittest.TestCase):
    def test_admits_session_meta_and_extracts_attributed_calls(self) -> None:
        result = parse_session(GOOD)

        self.assertEqual(result.status, "parsed")
        self.assertIsNone(result.skip_reason)
        self.assertEqual(result.session_id, "codex-session-1")
        self.assertEqual(result.versions_seen, ["0.144.1"])
        self.assertEqual(result.duplicate_tool_use, 1)
        self.assertEqual(result.raw_cwd, "/tmp/project")
        self.assertEqual(result.project, normalize_project_key("/tmp/project"))
        self.assertEqual(result.first_ts, "2026-06-10T10:00:00Z")
        self.assertEqual(result.last_ts, "2026-06-10T10:06:20Z")
        self.assertEqual(result.unpaired_results, 2)
        self.assertEqual(result.unsupported_results, 0)
        self.assertEqual(result.unmeasurable_results, 0)
        self.assertEqual(
            [call.raw for call in result.tool_calls],
            ["get_issue", "custom_builtin", "shell_command", "mystery", "spawn_agent"],
        )

        github = result.tool_calls[0]
        self.assertEqual(github.kind, "mcp")
        self.assertEqual(github.server, "github")
        self.assertEqual(github.tool, "get_issue")
        self.assertFalse(github.sidechain)
        self.assertIsNone(github.result_bytes)
        self.assertIsNone(github.result_kind)

        self.assertEqual(result.tool_calls[1].kind, "builtin")
        self.assertEqual(result.tool_calls[2].kind, "builtin")
        # Bare "mcp__" namespace: MCP evidence with no server identity.
        self.assertEqual(result.tool_calls[3].kind, "mcp-unattributed")
        # Non-mcp namespaces (observed: "collaboration") are builtin groups.
        self.assertEqual(result.tool_calls[4].kind, "builtin")

    def test_file_without_session_meta_is_skipped(self) -> None:
        result = parse_session(
            os.path.join(
                FIXTURES,
                "rollout-2026-06-11T100000Z-22222222-2222-2222-2222-222222222222.jsonl",
            )
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.skip_reason, "no session metadata found")
        self.assertEqual(result.versions_seen, [])

    def test_session_meta_with_null_cli_version_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_records(
                temporary,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-06-10T10:00:00Z",
                        "payload": {"id": "session", "cli_version": None},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-10T10:01:00Z",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call-1",
                            "namespace": "mcp__github",
                            "name": "get_issue",
                        },
                    },
                ],
            )

            result = parse_session(path)

        self.assertEqual(result.status, "parsed")
        self.assertEqual(result.versions_seen, ["(unversioned)"])
        self.assertEqual(result.tool_calls[0].kind, "mcp")
        self.assertEqual(result.tool_calls[0].server, "github")
        self.assertEqual(result.tool_calls[0].tool, "get_issue")

    def test_invalid_name_does_not_consume_call_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_records(
                temporary,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-06-10T10:00:00Z",
                        "payload": {"id": "session", "cli_version": "0.144.1"},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-10T10:01:00Z",
                        "payload": {
                            "type": "function_call",
                            "call_id": "same",
                            "namespace": "mcp__github",
                            "name": None,
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-06-10T10:02:00Z",
                        "payload": {
                            "type": "function_call",
                            "call_id": "same",
                            "namespace": "mcp__github",
                            "name": "get_issue",
                        },
                    },
                ],
            )

            result = parse_session(path)

        self.assertEqual([call.raw for call in result.tool_calls], ["get_issue"])
        self.assertEqual(result.duplicate_tool_use, 0)

    def test_result_shapes_use_final_taxonomy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_records(
                temporary,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-06-10T10:00:00Z",
                        "payload": {"id": "session", "cli_version": "0.144.1"},
                    },
                    self._call("paired", "mcp__github", "paired"),
                    self._call("unsupported", "mcp__github", "unsupported"),
                    self._call("unmeasurable", "mcp__github", "unmeasurable"),
                    self._call("unpaired-call", "mcp__github", "unpaired_call"),
                    self._output("paired", "abc"),
                    self._output("unsupported", {"opaque": True}),
                    self._output("unmeasurable", "\ud800"),
                    self._output("missing", "orphan"),
                    self._output("paired", "second"),
                ],
            )

            result = parse_session(path)

        calls = {call.tool: call for call in result.tool_calls}
        self.assertEqual(calls["paired"].result_bytes, 3)
        self.assertEqual(calls["paired"].result_kind, "paired")
        self.assertEqual(calls["unsupported"].result_bytes, 0)
        self.assertEqual(calls["unsupported"].result_kind, "unsupported")
        self.assertEqual(calls["unmeasurable"].result_bytes, 0)
        self.assertEqual(calls["unmeasurable"].result_kind, "unmeasurable")
        self.assertIsNone(calls["unpaired_call"].result_kind)
        self.assertEqual(result.unpaired_results, 2)
        self.assertEqual(result.unsupported_results, 1)
        self.assertEqual(result.unmeasurable_results, 1)

    def test_collision_call_id_makes_result_unpaired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_records(
                temporary,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-06-10T10:00:00Z",
                        "payload": {"id": "session", "cli_version": "0.144.1"},
                    },
                    self._call("same", "mcp__github", "first"),
                    self._call("same", "mcp__github", "second"),
                    self._output("same", "ambiguous"),
                ],
            )

            result = parse_session(path)

        self.assertEqual(result.duplicate_tool_use, 1)
        self.assertEqual([call.tool for call in result.tool_calls], ["first"])
        self.assertIsNone(result.tool_calls[0].result_kind)
        self.assertEqual(result.unpaired_results, 1)

    def test_mostly_unparseable_file_is_skipped(self) -> None:
        result = parse_session(
            os.path.join(
                FIXTURES,
                "rollout-2026-06-12T100000Z-33333333-3333-3333-3333-333333333333.jsonl",
            )
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.skip_reason, "4 of 5 lines unparseable")
        self.assertEqual(result.bad_lines, 4)
        self.assertEqual(result.versions_seen, ["0.144.1"])
        self.assertEqual(result.tool_calls, [])

    def test_find_transcripts_and_session_key(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            session_dir = os.path.join(home, ".codex", "sessions", "2026", "06", "10")
            os.makedirs(session_dir)
            paths = [
                os.path.join(session_dir, "rollout-b.jsonl"),
                os.path.join(session_dir, "rollout-a.jsonl"),
            ]
            for path in paths:
                with open(path, "w", encoding="utf-8"):
                    pass
            with open(os.path.join(session_dir, "ignored.jsonl"), "w", encoding="utf-8"):
                pass

            found = find_transcripts(home)

        self.assertEqual(found, sorted(paths))
        self.assertEqual(session_key(paths[0], home), "rollout-b")

    def _write_records(self, directory: str, records: list[dict]) -> str:
        path = os.path.join(directory, "rollout-test.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return path

    def _call(self, call_id: str, namespace: str, name: str) -> dict:
        return {
            "type": "response_item",
            "timestamp": "2026-06-10T10:01:00Z",
            "payload": {
                "type": "function_call",
                "call_id": call_id,
                "namespace": namespace,
                "name": name,
            },
        }

    def _output(self, call_id: str, output: object) -> dict:
        return {
            "type": "response_item",
            "timestamp": "2026-06-10T10:02:00Z",
            "payload": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        }


if __name__ == "__main__":
    unittest.main()

