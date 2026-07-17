"""Neutral transcript data model shared by CLI adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


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


@dataclass
class JsonlReadResult:
    """Parsed JSONL records plus non-fatal line damage accounting."""

    records: list[dict[str, Any]]
    non_empty_lines: int
    bad_lines: int
    error: str | None = None


def read_jsonl_records(path: str) -> JsonlReadResult:
    """Read JSONL records, counting malformed non-empty lines."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeError) as err:
        return JsonlReadResult([], 0, 0, f"unreadable: {err}")

    records: list[dict[str, Any]] = []
    non_empty_lines = 0
    bad_lines = 0
    for line in lines:
        if not line.strip():
            continue
        non_empty_lines += 1
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
            bad_lines += 1
            continue
        if not isinstance(record, dict):
            bad_lines += 1
            continue
        records.append(record)

    return JsonlReadResult(records, non_empty_lines, bad_lines)


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
