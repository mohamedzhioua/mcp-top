"""Golden tests for joining, ranking, and verdict assignment."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

import _path  # noqa: F401

from mcp_top.config import ServerConfig
from mcp_top.counter import UsageWindow, count_calls
from mcp_top.engine import build_cli_report
from mcp_top.mcpclient import ServerTools
from mcp_top.transcripts import SessionResult, ToolCall


class EngineTests(unittest.TestCase):
    def test_joins_calls_keeps_unconfigured_servers_and_ranks_rows(self) -> None:
        servers = [
            self._server("alpha_prune"),
            self._server("beta_review"),
            self._server("gamma_keep"),
            self._server("delta_error"),
            self._server("a__b"),
        ]
        definitions = [
            self._tools("alpha_prune", 3),
            self._tools("beta_review", 2),
            self._tools("gamma_keep", 4),
            ServerTools("delta_error", "error", "query failed", []),
            self._tools("a__b", 1),
        ]
        window = UsageWindow(
            sessions_considered=2,
            window_sessions=30,
            window_days=30,
            counts={
                "mcp__beta_review__lookup": 2,
                "mcp__gamma_keep__search": 6,
                "mcp__gamma_keep__fetch": 4,
                "mcp__rogue__surprise": 1,
                "mcp__a__b__complex": 2,
                "mcp__unattributed": 3,
                "Read": 7,
            },
            sidechain_counts={},
            server_tool_counts={
                "beta_review": {"lookup": 2},
                "gamma_keep": {"search": 6, "fetch": 4},
                "rogue": {"surprise": 1},
                "a__b": {"complex": 2},
            },
            unattributed_mcp_calls=3,
        )

        report = build_cli_report("claude-code", servers, definitions, [], window)

        self.assertEqual(
            [row.server for row in report.rows],
            [
                "alpha_prune",
                "beta_review",
                "a__b",
                "delta_error",
                "rogue",
                "gamma_keep",
            ],
        )
        rows = {row.server: row for row in report.rows}
        self.assertEqual(rows["alpha_prune"].verdict, "prune")
        self.assertEqual(rows["beta_review"].verdict, "review")
        self.assertEqual(rows["gamma_keep"].verdict, "keep")
        self.assertEqual(rows["delta_error"].verdict, "review")
        self.assertIsNone(rows["delta_error"].def_tokens)
        self.assertEqual(rows["delta_error"].calls, 0)
        self.assertEqual(rows["delta_error"].usage_status, "measured")
        self.assertEqual(rows["rogue"].scope, "(not configured)")
        self.assertEqual(rows["rogue"].called_tools, {"surprise": 1})
        self.assertEqual(rows["a__b"].called_tools, {"complex": 2})
        self.assertEqual(report.coverage.total_tool_calls, 25)
        self.assertEqual(report.coverage.mcp_tool_calls, 15)
        self.assertEqual(report.coverage.unattributed_mcp_calls, 3)
        self.assertEqual(report.coverage.servers_unsupported, 0)

    def test_unsupported_usage_yields_unknown_and_nullable_calls(self) -> None:
        servers = [self._server("alpha")]
        definitions = [self._tools("alpha", 1)]

        report = build_cli_report(
            "future-cli",
            servers,
            definitions,
            [],
            None,
        )

        row = report.rows[0]
        self.assertIsNone(row.calls)
        self.assertEqual(row.usage_status, "unsupported")
        self.assertEqual(row.verdict, "unknown")
        self.assertEqual(
            report.coverage.usage_note,
            "no transcript adapter for this CLI in v0.2",
        )

    def test_empty_corpus_usage_is_no_data_not_prune(self) -> None:
        report = self._empty_window_report([])

        self._assert_no_data_unknown(report)
        self.assertEqual(
            report.coverage.usage_note,
            "no transcripts found -- usage unknown",
        )

    def test_all_skipped_usage_is_no_data_not_prune(self) -> None:
        sessions = [
            SessionResult(
                path="skipped",
                session_id=None,
                status="skipped",
                skip_reason="unknown format version",
                versions_seen=[],
                tool_calls=[],
                first_ts=None,
                last_ts=None,
            )
        ]

        report = self._empty_window_report(sessions)

        self._assert_no_data_unknown(report)
        self.assertEqual(
            report.coverage.usage_note,
            "no usable sessions in the window -- usage unknown",
        )

    def test_no_timestamp_usage_is_no_data_not_prune(self) -> None:
        sessions = [
            SessionResult(
                path="no-time",
                session_id="no-time",
                status="parsed",
                skip_reason=None,
                versions_seen=["2.1.211"],
                tool_calls=[self._call("mcp__alpha__lookup")],
                first_ts=None,
                last_ts=None,
            )
        ]

        report = self._empty_window_report(sessions)

        self._assert_no_data_unknown(report)
        self.assertEqual(report.coverage.parsed_without_timestamp, 1)

    def test_future_only_usage_is_no_data_not_prune(self) -> None:
        sessions = [
            SessionResult(
                path="future",
                session_id="future",
                status="parsed",
                skip_reason=None,
                versions_seen=["2.1.211"],
                tool_calls=[
                    self._call("mcp__alpha__lookup", "2026-06-16T12:00:00Z")
                ],
                first_ts="2026-06-16T12:00:00Z",
                last_ts="2026-06-16T12:00:00Z",
            )
        ]

        report = self._empty_window_report(sessions)

        self._assert_no_data_unknown(report)
        self.assertEqual(report.coverage.future_sessions, 1)

    def _server(self, name: str) -> ServerConfig:
        return ServerConfig(
            name=name,
            scope="user",
            source_path="test",
            transport="stdio",
            command="fake",
            args=[],
            env={},
            url=None,
        )

    def _tools(self, server: str, count: int) -> ServerTools:
        return ServerTools(
            server=server,
            status="ok",
            error=None,
            tools=[
                {
                    "name": f"tool_{index}",
                    "description": "A fake tool definition",
                    "inputSchema": {"type": "object", "properties": {}},
                }
                for index in range(count)
            ],
        )

    def _empty_window_report(self, sessions: list[SessionResult]):
        window = count_calls(
            sessions,
            [session.path for session in sessions],
            now=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
        )
        return build_cli_report(
            "claude-code",
            [self._server("alpha")],
            [self._tools("alpha", 1)],
            sessions,
            window,
        )

    def _assert_no_data_unknown(self, report) -> None:
        row = report.rows[0]
        self.assertIsNone(row.calls)
        self.assertEqual(row.usage_status, "no-data")
        self.assertEqual(row.verdict, "unknown")

    def _call(
        self, raw: str, timestamp: str = "2026-06-15T12:00:00Z"
    ) -> ToolCall:
        return ToolCall(
            raw=raw,
            kind="mcp",
            server="alpha",
            tool="lookup",
            timestamp=timestamp,
        )


if __name__ == "__main__":
    unittest.main()
