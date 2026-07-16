"""Count transcript tool calls inside bounded recent-usage windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mcp_top.adapters.claude_code import SessionResult


@dataclass
class UsageWindow:
    """Aggregated tool calls from parsed sessions inside a usage window."""

    sessions_considered: int
    window_sessions: int
    window_days: int
    counts: dict[str, int]
    sidechain_counts: dict[str, int]


def count_calls(
    sessions: list[SessionResult],
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

    dated_sessions: list[tuple[datetime, SessionResult]] = []
    for session in sessions:
        if session.status != "parsed" or session.last_ts is None:
            continue
        timestamp = _parse_timestamp(session.last_ts)
        if timestamp is not None:
            dated_sessions.append((timestamp, session))

    dated_sessions.sort(key=lambda item: item[0], reverse=True)
    capped_sessions = dated_sessions[: max(0, window_sessions)]
    cutoff = current_time - timedelta(days=window_days)
    included = [
        session
        for timestamp, session in capped_sessions
        if cutoff <= timestamp <= current_time
    ]

    counts: dict[str, int] = {}
    sidechain_counts: dict[str, int] = {}
    for session in included:
        for call in session.tool_calls:
            counts[call.tool] = counts.get(call.tool, 0) + 1
            if call.sidechain:
                sidechain_counts[call.tool] = sidechain_counts.get(call.tool, 0) + 1

    return UsageWindow(
        sessions_considered=len(included),
        window_sessions=window_sessions,
        window_days=window_days,
        counts=counts,
        sidechain_counts=sidechain_counts,
    )


def _parse_timestamp(value: str) -> datetime | None:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
