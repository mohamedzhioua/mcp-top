"""Unit tests for windowed transcript tool-call counts."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

import _path  # noqa: F401

from mcp_top.adapters.claude_code import SessionResult, ToolCall
from mcp_top.counter import count_calls


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class CountCallsTests(unittest.TestCase):
    """Checks the session cap, date boundary, and sidechain split."""

    def test_most_recent_session_cap_wins(self) -> None:
        sessions = [
            self._session(
                "older",
                "2026-06-12T12:00:00Z",
                [ToolCall("OldTool", "2026-06-12T12:00:00Z", False)],
            ),
            self._session(
                "newest",
                "2026-06-14T12:00:00Z",
                [
                    ToolCall("SharedTool", "2026-06-14T12:00:00Z", False),
                    ToolCall("SideTool", "2026-06-14T12:00:01Z", True),
                ],
            ),
            self._session(
                "middle",
                "2026-06-13T12:00:00Z",
                [ToolCall("SharedTool", "2026-06-13T12:00:00Z", False)],
            ),
        ]

        usage = count_calls(sessions, window_sessions=2, window_days=30, now=NOW)

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
                [ToolCall("BoundaryTool", "2026-05-16T12:00:00+00:00", True)],
            ),
            self._session(
                "too-old",
                "2026-05-16T11:59:59Z",
                [ToolCall("OldTool", "2026-05-16T11:59:59Z", False)],
            ),
            self._session(
                "no-time",
                None,
                [ToolCall("NoTimeTool", "", False)],
            ),
            SessionResult(
                path="skipped",
                session_id="skipped",
                status="skipped",
                skip_reason="unknown format version",
                versions_seen=["3.0.1"],
                tool_calls=[ToolCall("SkippedTool", "2026-06-15T12:00:00Z", False)],
                first_ts="2026-06-15T12:00:00Z",
                last_ts="2026-06-15T12:00:00Z",
            ),
        ]

        usage = count_calls(sessions, window_sessions=30, window_days=30, now=NOW)

        self.assertEqual(usage.sessions_considered, 1)
        self.assertEqual(usage.counts, {"BoundaryTool": 1})
        self.assertEqual(usage.sidechain_counts, {"BoundaryTool": 1})

    def _session(
        self, path: str, last_ts: str | None, calls: list[ToolCall]
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
        )


if __name__ == "__main__":
    unittest.main()
