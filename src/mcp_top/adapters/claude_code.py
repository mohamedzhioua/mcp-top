"""Read Claude Code JSONL transcripts without modifying them."""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Collection

from mcp_top.names import parse_mcp_tool_name
from mcp_top.transcripts import SessionResult, ToolCall, parse_timestamp


ADAPTER_NAME = "claude-code"
SUPPORTED_VERSION_PATTERN = r"2\.\d"


def parse_session(
    path: str, configured: Collection[str] | None = None
) -> SessionResult:
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
        except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
            bad_lines += 1
            continue
        if not isinstance(record, dict):
            bad_lines += 1
            continue
        records.append(record)

    versions_seen: list[str] = []
    malformed_version = False
    session_id: str | None = None
    timestamps: list[tuple[datetime, str]] = []
    tool_calls: list[ToolCall] = []
    seen_tool_use_ids: set[str] = set()
    duplicate_tool_use = 0
    for record in records:
        if "version" in record:
            version = record["version"]
            if not isinstance(version, str):
                malformed_version = True
            elif version not in versions_seen:
                versions_seen.append(version)

        record_session_id = record.get("sessionId")
        if session_id is None and isinstance(record_session_id, str):
            session_id = record_session_id

        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            parsed_timestamp = parse_timestamp(timestamp)
            if parsed_timestamp is not None:
                timestamps.append((parsed_timestamp, timestamp))

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
            tool_use_id = block.get("id")
            if isinstance(tool_use_id, str):
                if tool_use_id in seen_tool_use_ids:
                    duplicate_tool_use += 1
                    continue
                seen_tool_use_ids.add(tool_use_id)
            tool = block.get("name")
            if not isinstance(tool, str):
                continue
            tool_calls.append(
                _tool_call(
                    tool,
                    timestamp if isinstance(timestamp, str) else "",
                    record.get("isSidechain") is True,
                    configured,
                )
            )

    first_ts = min(timestamps, default=(None, None), key=lambda item: item[0])[1]
    last_ts = max(timestamps, default=(None, None), key=lambda item: item[0])[1]
    if malformed_version:
        return SessionResult(
            path=path,
            session_id=session_id,
            status="skipped",
            skip_reason="malformed version marker",
            versions_seen=versions_seen,
            tool_calls=[],
            first_ts=first_ts,
            last_ts=last_ts,
            bad_lines=bad_lines,
            duplicate_tool_use=duplicate_tool_use,
        )
    for version in versions_seen:
        if re.match(SUPPORTED_VERSION_PATTERN, version) is None:
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
                duplicate_tool_use=duplicate_tool_use,
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
            duplicate_tool_use=duplicate_tool_use,
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
            duplicate_tool_use=duplicate_tool_use,
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
        duplicate_tool_use=duplicate_tool_use,
    )


def _tool_call(
    raw: str,
    timestamp: str,
    sidechain: bool,
    configured: Collection[str] | None,
) -> ToolCall:
    parsed = parse_mcp_tool_name(raw, configured)
    if parsed is not None:
        server, tool = parsed
        return ToolCall(
            raw=raw,
            kind="mcp",
            server=server,
            tool=tool,
            timestamp=timestamp,
            sidechain=sidechain,
        )
    if raw.startswith("mcp__"):
        return ToolCall(
            raw=raw,
            kind="mcp-unattributed",
            server=None,
            tool=None,
            timestamp=timestamp,
            sidechain=sidechain,
        )
    return ToolCall(
        raw=raw,
        kind="builtin",
        server=None,
        tool=None,
        timestamp=timestamp,
        sidechain=sidechain,
    )


def find_transcripts(home: str) -> list[str]:
    """Return sorted Claude Code transcript paths below the supplied home."""

    pattern = os.path.join(home, ".claude", "projects", "*", "**", "*.jsonl")
    return sorted(glob.glob(pattern, recursive=True))


def session_key(path: str, home: str) -> str:
    """Return the parent Claude session identity for a transcript path."""

    projects = os.path.join(home, ".claude", "projects")
    relative = os.path.relpath(path, projects)
    parts = relative.replace("\\", "/").split("/")
    slug, session_component = parts[0], parts[1]
    if session_component.endswith(".jsonl"):
        session_component = session_component[: -len(".jsonl")]
    return f"{slug}/{session_component}"
