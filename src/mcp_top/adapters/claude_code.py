"""Read Claude Code JSONL transcripts without modifying them."""

from __future__ import annotations

import glob
import os
import re
from typing import Any, Collection

from mcp_top.names import parse_mcp_tool_name
from mcp_top.transcripts import (
    RecordedResult,
    SessionResult,
    ToolCall,
    measured_utf8_bytes,
    pair_results,
    parse_timestamp,
    read_jsonl_records,
)


ADAPTER_NAME = "claude-code"
SUPPORTED_VERSION_PATTERN = r"2\.\d"


def parse_session(
    path: str, configured: Collection[str] | None = None
) -> SessionResult:
    """Parse one Claude Code transcript, skipping unsafe or damaged formats."""

    project = _project_slug_from_path(path)
    read_result = read_jsonl_records(path)
    if read_result.error is not None:
        return SessionResult(
            path=path,
            session_id=None,
            status="skipped",
            skip_reason=read_result.error,
            versions_seen=[],
            tool_calls=[],
            first_ts=None,
            last_ts=None,
            project=project,
        )
    records = read_result.records
    non_empty_lines = read_result.non_empty_lines
    bad_lines = read_result.bad_lines

    versions_seen: list[str] = []
    malformed_version = False
    session_id: str | None = None
    timestamps: list[tuple[datetime, str]] = []
    tool_calls: list[ToolCall] = []
    calls_by_id: dict[str, ToolCall] = {}
    collision_ids: set[str] = set()
    results: list[RecordedResult] = []
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

        if record.get("type") == "user":
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                result = _tool_result(block)
                if result is not None:
                    results.append(result)
            continue

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
                    collision_ids.add(tool_use_id)
                    duplicate_tool_use += 1
                    continue
                seen_tool_use_ids.add(tool_use_id)
            tool = block.get("name")
            if not isinstance(tool, str):
                continue
            call = _tool_call(
                tool,
                timestamp if isinstance(timestamp, str) else "",
                record.get("isSidechain") is True,
                configured,
            )
            tool_calls.append(call)
            if isinstance(tool_use_id, str):
                calls_by_id[tool_use_id] = call

    result_pairing = pair_results(calls_by_id, collision_ids, results)

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
            project=project,
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
                project=project,
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
            project=project,
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
            project=project,
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
        project=project,
        unpaired_results=result_pairing.unpaired_results,
        unsupported_results=result_pairing.unsupported_results,
        unmeasurable_results=result_pairing.unmeasurable_results,
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


def _tool_result(block: object) -> RecordedResult | None:
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return None
    result_id = block.get("tool_use_id")
    paired_id = result_id if isinstance(result_id, str) else None
    content = block.get("content")
    if isinstance(content, str):
        byte_count, measured = measured_utf8_bytes(content)
        return RecordedResult(
            paired_id,
            byte_count,
            "paired" if measured else "unmeasurable",
        )
    if isinstance(content, list):
        has_non_text = False
        total = 0
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                byte_count, measured = measured_utf8_bytes(item["text"])
                if not measured:
                    return RecordedResult(paired_id, 0, "unmeasurable")
                total += byte_count
            else:
                has_non_text = True
        if has_non_text and total == 0:
            return RecordedResult(paired_id, 0, "unsupported")
        return RecordedResult(paired_id, total, "partial" if has_non_text else "paired")
    return RecordedResult(paired_id, 0, "unsupported")


def find_transcripts(home: str) -> list[str]:
    """Return sorted Claude Code transcript paths below the supplied home."""

    pattern = os.path.join(home, ".claude", "projects", "*", "**", "*.jsonl")
    return sorted(glob.glob(pattern, recursive=True))


def _project_slug_from_path(path: str) -> str | None:
    normalized = os.path.normpath(path).replace("\\", "/")
    parts = normalized.split("/")
    compare_parts = [part.casefold() for part in parts] if os.name == "nt" else parts
    for index in range(len(compare_parts) - 2):
        if compare_parts[index] == ".claude" and compare_parts[index + 1] == "projects":
            slug = parts[index + 2]
            return slug or None
    return None


def session_key(path: str, home: str) -> str:
    """Return the parent Claude session identity for a transcript path."""

    projects = os.path.join(home, ".claude", "projects")
    relative = os.path.relpath(path, projects)
    parts = relative.replace("\\", "/").split("/")
    slug, session_component = parts[0], parts[1]
    if session_component.endswith(".jsonl"):
        session_component = session_component[: -len(".jsonl")]
    return f"{slug}/{session_component}"
