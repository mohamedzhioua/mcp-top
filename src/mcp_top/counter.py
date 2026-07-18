"""Count transcript tool calls inside bounded recent-usage windows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from mcp_top.transcripts import SessionResult, parse_timestamp


@dataclass
class UsageWindow:
    """Aggregated tool calls from parsed sessions inside a usage window."""

    sessions_considered: int
    window_sessions: int
    window_days: int
    counts: dict[str, int]
    sidechain_counts: dict[str, int]
    server_tool_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    unattributed_mcp_calls: int = 0
    future_sessions: int = 0
    project_filter: str | None = None
    sessions_with_project: int = 0
    sessions_unattributed: int = 0
    sessions_matching_project: int = 0
    server_calls_by_project: dict[str, dict[str, int]] = field(
        default_factory=dict
    )


def count_calls(
    sessions: list[SessionResult],
    keys: list[str],
    window_sessions: int = 30,
    window_days: int = 30,
    now: datetime | None = None,
    project_filter: str | None = None,
) -> UsageWindow:
    """Count calls from the most recent parsed sessions within the day window."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    if len(sessions) != len(keys):
        raise ValueError("sessions and keys must have the same length")

    grouped: dict[str, list[tuple[datetime, SessionResult]]] = {}
    for session, key in zip(sessions, keys):
        if session.status != "parsed" or session.last_ts is None:
            continue
        timestamp = parse_timestamp(session.last_ts)
        if timestamp is not None:
            grouped.setdefault(key, []).append((timestamp, session))

    dated_groups = [
        (max(timestamp for timestamp, _ in files), files, _group_project(files))
        for files in grouped.values()
    ]
    future_limit = current_time + timedelta(minutes=5)
    future_sessions = sum(
        1 for timestamp, _, _ in dated_groups if timestamp > future_limit
    )
    cutoff = current_time - timedelta(days=window_days)
    eligible_groups = [
        (timestamp, files, group_project)
        for timestamp, files, group_project in dated_groups
        if cutoff <= timestamp <= future_limit
    ]
    eligible_groups.sort(key=lambda item: item[0], reverse=True)
    home_included = eligible_groups[:window_sessions]
    project_included = (
        [
            group
            for group in eligible_groups
            if group[2] == project_filter
        ][:window_sessions]
        if project_filter is not None
        else []
    )
    verdict_groups = (
        home_included if project_filter is None else project_included
    )

    counts: dict[str, int] = {}
    sidechain_counts: dict[str, int] = {}
    server_tool_counts: dict[str, dict[str, int]] = {}
    server_calls_by_project: dict[str, dict[str, int]] = {}
    unattributed_mcp_calls = 0
    sessions_with_project = 0
    sessions_unattributed = 0
    for _, files, group_project in home_included:
        project_bucket = group_project or "(unattributed)"
        if group_project is None:
            sessions_unattributed += 1
        else:
            sessions_with_project += 1
        for _, session in files:
            for call in session.tool_calls:
                if (
                    call.kind == "mcp"
                    and call.server is not None
                    and call.tool is not None
                ):
                    project_counts = server_calls_by_project.setdefault(
                        call.server, {}
                    )
                    project_counts[project_bucket] = (
                        project_counts.get(project_bucket, 0) + 1
                    )

    for _, files, _ in verdict_groups:
        for _, session in files:
            for call in session.tool_calls:
                counts[call.raw] = counts.get(call.raw, 0) + 1
                if call.sidechain:
                    sidechain_counts[call.raw] = (
                        sidechain_counts.get(call.raw, 0) + 1
                    )
                if (
                    call.kind == "mcp"
                    and call.server is not None
                    and call.tool is not None
                ):
                    called_tools = server_tool_counts.setdefault(
                        call.server, {}
                    )
                    called_tools[call.tool] = (
                        called_tools.get(call.tool, 0) + 1
                    )
                elif call.kind == "mcp-unattributed":
                    unattributed_mcp_calls += 1

    return UsageWindow(
        sessions_considered=len(verdict_groups),
        window_sessions=window_sessions,
        window_days=window_days,
        counts=counts,
        sidechain_counts=sidechain_counts,
        server_tool_counts=server_tool_counts,
        unattributed_mcp_calls=unattributed_mcp_calls,
        future_sessions=future_sessions,
        project_filter=project_filter,
        sessions_with_project=sessions_with_project,
        sessions_unattributed=sessions_unattributed,
        sessions_matching_project=len(project_included),
        server_calls_by_project=server_calls_by_project,
    )


def _group_project(files: list[tuple[datetime, SessionResult]]) -> str | None:
    projects = {
        session.project
        for _, session in files
        if session.project is not None
    }
    if len(projects) == 1:
        return next(iter(projects))
    return None
