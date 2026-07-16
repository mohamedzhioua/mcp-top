"""Neutral transcript data model shared by CLI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ToolCall:
    """A tool invocation recorded in an assistant transcript."""

    raw: str
    kind: str
    server: str | None
    tool: str | None
    timestamp: str
    sidechain: bool = False


@dataclass
class SessionResult:
    """The parsed contents and coverage status of one transcript."""

    path: str
    session_id: str | None
    status: str
    skip_reason: str | None
    versions_seen: list[str]
    tool_calls: list[ToolCall]
    first_ts: str | None
    last_ts: str | None
    bad_lines: int = 0
    duplicate_tool_use: int = 0


def parse_timestamp(value: str) -> datetime | None:
    """Parse a transcript timestamp as UTC, accepting Claude's Z suffix."""

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
