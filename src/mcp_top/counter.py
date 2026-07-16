"""Count transcript tool calls inside bounded recent-usage windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mcp_top.adapters.claude_code import SessionResult, parse_timestamp


@dataclass
class UsageWindow:
    """Aggregated tool calls from parsed sessions inside a usage window."""

    sessions_considered: int
    window_sessions: int
    window_days: int
    counts: dict[str, int]
    sidechain_counts: dict[str, int]
    future_sessions: int = 0


def count_calls(
    sessions: list[SessionResult],
    keys: list[str],
    window_sessions: int = 30,
    window_days: int = 30,
    now: datetime | None = None,
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
        (max(timestamp for timestamp, _ in files), files)
        for files in grouped.values()
    ]
    future_limit = current_time + timedelta(minutes=5)
    future_sessions = sum(
        1 for timestamp, _ in dated_groups if timestamp > future_limit
    )
    cutoff = current_time - timedelta(days=window_days)
    eligible_groups = [
        (timestamp, files)
        for timestamp, files in dated_groups
        if cutoff <= timestamp <= future_limit
    ]
    eligible_groups.sort(key=lambda item: item[0], reverse=True)
    included_groups = eligible_groups[:window_sessions]

    counts: dict[str, int] = {}
    sidechain_counts: dict[str, int] = {}
    for _, files in included_groups:
        for _, session in files:
            for call in session.tool_calls:
                counts[call.tool] = counts.get(call.tool, 0) + 1
                if call.sidechain:
                    sidechain_counts[call.tool] = (
                        sidechain_counts.get(call.tool, 0) + 1
                    )

    return UsageWindow(
        sessions_considered=len(included_groups),
        window_sessions=window_sessions,
        window_days=window_days,
        counts=counts,
        sidechain_counts=sidechain_counts,
        future_sessions=future_sessions,
    )
