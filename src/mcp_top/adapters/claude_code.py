"""Read Claude Code JSONL transcripts without modifying them."""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from typing import Any


ADAPTER_NAME = "claude-code"
SUPPORTED_VERSION_PREFIXES = ("2.",)


@dataclass
class ToolCall:
    """A tool invocation recorded in a Claude Code assistant message."""

    tool: str
    timestamp: str
    sidechain: bool


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


def parse_session(path: str) -> SessionResult:
    """Parse one Claude Code transcript, skipping unsafe or damaged formats."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeError) as err:
        return SessionResult(
            path=path,
            session_id=None,
            status="skipped",
            skip_reason=f"unreadable: {err}",
            versions_seen=[],
            tool_calls=[],
            first_ts=None,
            last_ts=None,
        )

    records: list[dict[str, Any]] = []
    non_empty_lines = 0
    bad_lines = 0
    for line in lines:
        if not line.strip():
            continue
        non_empty_lines += 1
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
            bad_lines += 1
            continue
        if not isinstance(record, dict):
            bad_lines += 1
            continue
        records.append(record)

    versions_seen: list[str] = []
    session_id: str | None = None
    timestamps: list[str] = []
    tool_calls: list[ToolCall] = []
    for record in records:
        version = record.get("version")
        if isinstance(version, str) and version not in versions_seen:
            versions_seen.append(version)

        record_session_id = record.get("sessionId")
        if session_id is None and isinstance(record_session_id, str):
            session_id = record_session_id

        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            timestamps.append(timestamp)

        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool = block.get("name")
            if not isinstance(tool, str):
                continue
            tool_calls.append(
                ToolCall(
                    tool=tool,
                    timestamp=timestamp if isinstance(timestamp, str) else "",
                    sidechain=record.get("isSidechain") is True,
                )
            )

    first_ts = timestamps[0] if timestamps else None
    last_ts = timestamps[-1] if timestamps else None
    for version in versions_seen:
        if not version.startswith(SUPPORTED_VERSION_PREFIXES):
            return SessionResult(
                path=path,
                session_id=session_id,
                status="skipped",
                skip_reason=f'unknown format version "{version}"',
                versions_seen=versions_seen,
                tool_calls=[],
                first_ts=first_ts,
                last_ts=last_ts,
                bad_lines=bad_lines,
            )

    if not versions_seen:
        return SessionResult(
            path=path,
            session_id=session_id,
            status="skipped",
            skip_reason="no version marker found",
            versions_seen=[],
            tool_calls=[],
            first_ts=first_ts,
            last_ts=last_ts,
            bad_lines=bad_lines,
        )

    if non_empty_lines and bad_lines / non_empty_lines > 0.1:
        return SessionResult(
            path=path,
            session_id=session_id,
            status="skipped",
            skip_reason=f"{bad_lines} of {non_empty_lines} lines unparseable",
            versions_seen=versions_seen,
            tool_calls=[],
            first_ts=first_ts,
            last_ts=last_ts,
            bad_lines=bad_lines,
        )

    return SessionResult(
        path=path,
        session_id=session_id,
        status="parsed",
        skip_reason=None,
        versions_seen=versions_seen,
        tool_calls=tool_calls,
        first_ts=first_ts,
        last_ts=last_ts,
        bad_lines=bad_lines,
    )


def find_transcripts(home: str) -> list[str]:
    """Return sorted Claude Code transcript paths below the supplied home."""

    pattern = os.path.join(home, ".claude", "projects", "*", "*.jsonl")
    return sorted(glob.glob(pattern))
