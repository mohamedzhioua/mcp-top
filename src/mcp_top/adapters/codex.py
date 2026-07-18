"""Read Codex CLI rollout JSONL transcripts without modifying them."""

from __future__ import annotations

import glob
import os
from typing import Any, Collection

from mcp_top.projects import normalize_project_key
from mcp_top.transcripts import (
    RecordedResult,
    SessionResult,
    ToolCall,
    measured_utf8_bytes,
    pair_results,
    parse_timestamp,
    read_jsonl_records,
)


ADAPTER_NAME = "codex"


def find_transcripts(home: str) -> list[str]:
    """Return sorted Codex rollout transcript paths below the supplied home."""

    pattern = os.path.join(
        home, ".codex", "sessions", "**", "rollout-*.jsonl"
    )
    return sorted(glob.glob(pattern, recursive=True))


def session_key(path: str, home: str) -> str:
    """Return the rollout file identity for a Codex session."""

    filename = os.path.basename(path)
    if filename.endswith(".jsonl"):
        return filename[: -len(".jsonl")]
    return filename


def parse_session(
    path: str, configured: Collection[str] | None = None
) -> SessionResult:
    """Parse one Codex rollout transcript using structural admission."""

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
        )

    records = read_result.records
    timestamps: list[tuple[Any, str]] = []
    versions_seen: list[str] = []
    saw_session_meta = False
    session_id: str | None = None
    raw_cwd: str | None = None
    project: str | None = None
    tool_calls: list[ToolCall] = []
    calls_by_id: dict[str, ToolCall] = {}
    collision_ids: set[str] = set()
    results: list[RecordedResult] = []
    seen_call_ids: set[str] = set()
    duplicate_tool_use = 0

    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            parsed_timestamp = parse_timestamp(timestamp)
            if parsed_timestamp is not None:
                timestamps.append((parsed_timestamp, timestamp))

        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue

        if record.get("type") == "session_meta":
            saw_session_meta = True
            version = payload.get("cli_version")
            if isinstance(version, str) and version not in versions_seen:
                versions_seen.append(version)
            elif not isinstance(version, str) and "(unversioned)" not in versions_seen:
                versions_seen.append("(unversioned)")
            record_session_id = payload.get("id")
            if session_id is None and isinstance(record_session_id, str):
                session_id = record_session_id
            cwd = payload.get("cwd")
            if raw_cwd is None and isinstance(cwd, str) and cwd != "":
                raw_cwd = cwd
                project = normalize_project_key(cwd)
            continue

        if record.get("type") != "response_item":
            continue
        if payload.get("type") == "function_call_output":
            call_id = payload.get("call_id")
            output = payload.get("output")
            paired_id = call_id if isinstance(call_id, str) else None
            if isinstance(output, str):
                byte_count, measured = measured_utf8_bytes(output)
                results.append(
                    RecordedResult(
                        paired_id,
                        byte_count,
                        "paired" if measured else "unmeasurable",
                    )
                )
            else:
                results.append(RecordedResult(paired_id, 0, "unsupported"))
            continue
        if payload.get("type") not in {"function_call", "custom_tool_call"}:
            continue

        call_id = payload.get("call_id")
        if isinstance(call_id, str):
            if call_id in seen_call_ids:
                collision_ids.add(call_id)
                duplicate_tool_use += 1
                continue

        name = payload.get("name")
        if not isinstance(name, str):
            continue
        if isinstance(call_id, str):
            seen_call_ids.add(call_id)
        namespace = payload.get("namespace")
        call = _tool_call(
            name,
            namespace if isinstance(namespace, str) else None,
            timestamp if isinstance(timestamp, str) else "",
        )
        tool_calls.append(call)
        if isinstance(call_id, str):
            calls_by_id[call_id] = call

    result_pairing = pair_results(calls_by_id, collision_ids, results)

    first_ts = min(timestamps, default=(None, None), key=lambda item: item[0])[1]
    last_ts = max(timestamps, default=(None, None), key=lambda item: item[0])[1]

    if not saw_session_meta:
        return SessionResult(
            path=path,
            session_id=session_id,
            status="skipped",
            skip_reason="no session metadata found",
            versions_seen=[],
            tool_calls=[],
            first_ts=first_ts,
            last_ts=last_ts,
            bad_lines=read_result.bad_lines,
            duplicate_tool_use=duplicate_tool_use,
            project=project,
            raw_cwd=raw_cwd,
        )

    if (
        read_result.non_empty_lines
        and read_result.bad_lines / read_result.non_empty_lines > 0.1
    ):
        return SessionResult(
            path=path,
            session_id=session_id,
            status="skipped",
            skip_reason=(
                f"{read_result.bad_lines} of "
                f"{read_result.non_empty_lines} lines unparseable"
            ),
            versions_seen=versions_seen,
            tool_calls=[],
            first_ts=first_ts,
            last_ts=last_ts,
            bad_lines=read_result.bad_lines,
            duplicate_tool_use=duplicate_tool_use,
            project=project,
            raw_cwd=raw_cwd,
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
        bad_lines=read_result.bad_lines,
        duplicate_tool_use=duplicate_tool_use,
        project=project,
        raw_cwd=raw_cwd,
        unpaired_results=result_pairing.unpaired_results,
        unsupported_results=result_pairing.unsupported_results,
        unmeasurable_results=result_pairing.unmeasurable_results,
    )


def _tool_call(raw: str, namespace: str | None, timestamp: str) -> ToolCall:
    if namespace is None:
        return ToolCall(
            raw=raw,
            kind="builtin",
            server=None,
            tool=None,
            timestamp=timestamp,
            sidechain=False,
        )

    prefix = "mcp__"
    if namespace.startswith(prefix) and namespace != prefix:
        return ToolCall(
            raw=raw,
            kind="mcp",
            server=namespace[len(prefix) :],
            tool=raw,
            timestamp=timestamp,
            sidechain=False,
        )

    if namespace == prefix:
        # MCP marker with an empty server segment: evidence without identity.
        return ToolCall(
            raw=raw,
            kind="mcp-unattributed",
            server=None,
            tool=None,
            timestamp=timestamp,
            sidechain=False,
        )

    # Codex uses non-mcp namespaces for builtin tool groups (observed:
    # "collaboration" for spawn_agent/send_message/...). Not MCP.
    return ToolCall(
        raw=raw,
        kind="builtin",
        server=None,
        tool=None,
        timestamp=timestamp,
        sidechain=False,
    )


