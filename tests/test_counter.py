"""Unit tests for windowed transcript tool-call counts."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

import _path  # noqa: F401

from mcp_top.counter import _group_project, count_calls
from mcp_top.transcripts import SessionResult, ToolCall


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class CountCallsTests(unittest.TestCase):
    """Checks the session cap, date boundary, and sidechain split."""

    def test_most_recent_session_cap_wins(self) -> None:
        sessions = [
            self._session(
                "older",
                "2026-06-12T12:00:00Z",
                [self._call("OldTool", "2026-06-12T12:00:00Z")],
            ),
            self._session(
                "newest",
                "2026-06-14T12:00:00Z",
                [
                    self._call("SharedTool", "2026-06-14T12:00:00Z"),
                    self._call("SideTool", "2026-06-14T12:00:01Z", True),
                ],
            ),
            self._session(
                "middle",
                "2026-06-13T12:00:00Z",
                [self._call("SharedTool", "2026-06-13T12:00:00Z")],
            ),
        ]

        usage = count_calls(
            sessions,
            [session.path for session in sessions],
            window_sessions=2,
            window_days=30,
            now=NOW,
        )

        self.assertEqual(usage.sessions_considered, 2)
        self.assertEqual(usage.window_sessions, 2)
        self.assertEqual(usage.window_days, 30)
        self.assertEqual(usage.counts, {"SharedTool": 2, "SideTool": 1})
        self.assertEqual(usage.sidechain_counts, {"SideTool": 1})

    def test_day_cutoff_is_inclusive_and_excludes_missing_timestamps(self) -> None:
        sessions = [
            self._session(
                "boundary",
                "2026-05-16T12:00:00+00:00",
                [self._call("BoundaryTool", "2026-05-16T12:00:00+00:00", True)],
            ),
            self._session(
                "too-old",
                "2026-05-16T11:59:59Z",
                [self._call("OldTool", "2026-05-16T11:59:59Z")],
            ),
            self._session(
                "no-time",
                None,
                [self._call("NoTimeTool", "")],
            ),
            SessionResult(
                path="skipped",
                session_id="skipped",
                status="skipped",
                skip_reason="unknown format version",
                versions_seen=["3.0.1"],
                tool_calls=[self._call("SkippedTool", "2026-06-15T12:00:00Z")],
                first_ts="2026-06-15T12:00:00Z",
                last_ts="2026-06-15T12:00:00Z",
            ),
        ]

        usage = count_calls(
            sessions,
            [session.path for session in sessions],
            window_sessions=30,
            window_days=30,
            now=NOW,
        )

        self.assertEqual(usage.sessions_considered, 1)
        self.assertEqual(usage.counts, {"BoundaryTool": 1})
        self.assertEqual(usage.sidechain_counts, {"BoundaryTool": 1})

    def test_parent_and_subagent_share_one_group_and_sum_calls(self) -> None:
        sessions = [
            self._session(
                "parent",
                "2026-06-14T12:00:00Z",
                [self._call("ParentTool", "2026-06-14T12:00:00Z")],
            ),
            self._session(
                "subagent",
                "2026-06-14T13:00:00Z",
                [self._call("SubagentTool", "2026-06-14T13:00:00Z", True)],
            ),
            self._session(
                "older",
                "2026-06-13T12:00:00Z",
                [self._call("OlderTool", "2026-06-13T12:00:00Z")],
            ),
        ]

        usage = count_calls(
            sessions,
            ["slug/uuid", "slug/uuid", "slug/older"],
            window_sessions=1,
            window_days=30,
            now=NOW,
        )

        self.assertEqual(usage.sessions_considered, 1)
        self.assertEqual(usage.counts, {"ParentTool": 1, "SubagentTool": 1})
        self.assertEqual(usage.sidechain_counts, {"SubagentTool": 1})

    def test_future_dated_group_is_excluded_before_session_cap(self) -> None:
        sessions = [
            self._session(
                "future-parent",
                "2026-06-15T12:06:00Z",
                [self._call("Future", "2026-06-15T12:06:00Z")],
            ),
            self._session(
                "future-child",
                "2026-06-15T13:00:00Z",
                [self._call("FutureChild", "2026-06-15T13:00:00Z")],
            ),
            self._session(
                "current",
                "2026-06-15T12:00:00Z",
                [self._call("Current", "2026-06-15T12:00:00Z")],
            ),
        ]

        usage = count_calls(
            sessions,
            ["slug/future", "slug/future", "slug/current"],
            window_sessions=1,
            window_days=30,
            now=NOW,
        )

        self.assertEqual(usage.future_sessions, 1)
        self.assertEqual(usage.sessions_considered, 1)
        self.assertEqual(usage.counts, {"Current": 1})

    def test_mcp_attribution_is_aggregated_from_tool_calls(self) -> None:
        sessions = [
            self._session(
                "current",
                "2026-06-15T12:00:00Z",
                [
                    self._mcp_call(
                        "mcp__github__get_pr",
                        "github",
                        "get_pr",
                        "2026-06-15T12:00:00Z",
                    ),
                    ToolCall(
                        raw="mcp__broken",
                        kind="mcp-unattributed",
                        server=None,
                        tool=None,
                        timestamp="2026-06-15T12:00:01Z",
                    ),
                ],
            )
        ]

        usage = count_calls(
            sessions,
            [session.path for session in sessions],
            window_sessions=30,
            window_days=30,
            now=NOW,
        )

        self.assertEqual(usage.server_tool_counts, {"github": {"get_pr": 1}})
        self.assertEqual(usage.unattributed_mcp_calls, 1)

    def test_project_filter_uses_project_recent_groups_for_verdict_counts(self) -> None:
        sessions = [
            self._session(
                "project-a",
                "2026-06-15T12:00:00Z",
                [
                    self._mcp_call(
                        "mcp__github__search",
                        "github",
                        "search",
                        "2026-06-15T12:00:00Z",
                    )
                ],
                project="project-a",
            ),
            self._session(
                "project-b",
                "2026-06-14T12:00:00Z",
                [
                    self._mcp_call(
                        "mcp__github__search",
                        "github",
                        "search",
                        "2026-06-14T12:00:00Z",
                    )
                ],
                project="project-b",
            ),
            self._session(
                "no-project",
                "2026-06-13T12:00:00Z",
                [
                    self._mcp_call(
                        "mcp__github__search",
                        "github",
                        "search",
                        "2026-06-13T12:00:00Z",
                    )
                ],
            ),
        ]

        usage = count_calls(
            sessions,
            [session.path for session in sessions],
            window_sessions=30,
            window_days=30,
            now=NOW,
            project_filter="project-a",
        )
        unfiltered = count_calls(
            sessions,
            [session.path for session in sessions],
            window_sessions=30,
            window_days=30,
            now=NOW,
        )

        self.assertEqual(usage.project_filter, "project-a")
        self.assertEqual(usage.sessions_considered, 1)
        self.assertEqual(usage.sessions_with_project, 2)
        self.assertEqual(usage.sessions_unattributed, 1)
        self.assertEqual(usage.sessions_matching_project, 1)
        self.assertEqual(usage.counts, {"mcp__github__search": 1})
        self.assertEqual(usage.server_tool_counts, {"github": {"search": 1}})
        self.assertEqual(
            usage.server_calls_by_project,
            {"github": {"project-a": 1, "project-b": 1, "(unattributed)": 1}},
        )
        self.assertEqual(
            unfiltered.server_tool_counts,
            {"github": {"search": 3}},
        )
        self.assertEqual(
            unfiltered.server_calls_by_project,
            usage.server_calls_by_project,
        )

    def test_project_filter_applies_before_session_cap(self) -> None:
        sessions = [
            self._session(
                "other-project",
                "2026-06-15T12:00:00Z",
                [
                    self._mcp_call(
                        "mcp__github__other",
                        "github",
                        "other",
                        "2026-06-15T12:00:00Z",
                    )
                ],
                project="project-b",
            ),
            self._session(
                "current-project",
                "2026-06-14T12:00:00Z",
                [
                    self._mcp_call(
                        "mcp__github__current",
                        "github",
                        "current",
                        "2026-06-14T12:00:00Z",
                    )
                ],
                project="project-a",
            ),
        ]

        usage = count_calls(
            sessions,
            [session.path for session in sessions],
            window_sessions=1,
            window_days=30,
            now=NOW,
            project_filter="project-a",
        )

        self.assertEqual(usage.sessions_considered, 1)
        self.assertEqual(usage.sessions_matching_project, 1)
        self.assertEqual(usage.counts, {"mcp__github__current": 1})
        self.assertEqual(usage.server_tool_counts, {"github": {"current": 1}})
        self.assertEqual(
            usage.server_calls_by_project,
            {"github": {"project-b": 1}},
        )

    def test_group_project_conflict_is_unattributed(self) -> None:
        files = [
            (
                datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
                self._session("a", "2026-06-15T12:00:00Z", [], "project-a"),
            ),
            (
                datetime(2026, 6, 15, 12, 1, tzinfo=timezone.utc),
                self._session("b", "2026-06-15T12:01:00Z", [], "project-b"),
            ),
        ]

        self.assertIsNone(_group_project(files))

    def _session(
        self,
        path: str,
        last_ts: str | None,
        calls: list[ToolCall],
        project: str | None = None,
    ) -> SessionResult:
        return SessionResult(
            path=path,
            session_id=path,
            status="parsed",
            skip_reason=None,
            versions_seen=["2.1.211"],
            tool_calls=calls,
            first_ts=last_ts,
            last_ts=last_ts,
            project=project,
        )

    def _call(
        self, raw: str, timestamp: str, sidechain: bool = False
    ) -> ToolCall:
        return ToolCall(
            raw=raw,
            kind="builtin",
            server=None,
            tool=None,
            timestamp=timestamp,
            sidechain=sidechain,
        )

    def _mcp_call(
        self, raw: str, server: str, tool: str, timestamp: str
    ) -> ToolCall:
        return ToolCall(
            raw=raw,
            kind="mcp",
            server=server,
            tool=tool,
            timestamp=timestamp,
        )


if __name__ == "__main__":
    unittest.main()
