"""Coverage accounting and honest human-readable coverage reporting."""

from __future__ import annotations

import os
from dataclasses import dataclass

from mcp_top.adapters.claude_code import SessionResult
from mcp_top.counter import UsageWindow
from mcp_top.mcpclient import ServerTools


@dataclass
class Coverage:
    """What mcp-top could and could not inspect for this report."""

    transcripts_found: int
    transcripts_parsed: int
    transcripts_skipped: list[tuple[str, str]]
    in_window: int
    total_tool_calls: int
    mcp_tool_calls: int
    servers_queried_ok: int
    servers_query_failed: list[tuple[str, str]]
    config_warnings: list[str]
    parsed_without_timestamp: int = 0
    bad_lines_in_parsed: int = 0


def build_coverage(
    sessions: list[SessionResult],
    window: UsageWindow,
    server_tools_list: list[ServerTools],
    config_warnings: list[str] | None = None,
) -> Coverage:
    """Build coverage facts from parsed sessions and server query results."""

    skipped = [
        (session.path, session.skip_reason or "unspecified reason")
        for session in sessions
        if session.status != "parsed"
    ]
    query_failures = [
        (result.server, result.error or "unknown error")
        for result in server_tools_list
        if result.status == "error"
    ]
    return Coverage(
        transcripts_found=len(sessions),
        transcripts_parsed=sum(
            1 for session in sessions if session.status == "parsed"
        ),
        transcripts_skipped=skipped,
        in_window=window.sessions_considered,
        total_tool_calls=sum(window.counts.values()),
        mcp_tool_calls=sum(
            count
            for tool, count in window.counts.items()
            if tool.startswith("mcp__")
        ),
        servers_queried_ok=sum(
            1 for result in server_tools_list if result.status == "ok"
        ),
        servers_query_failed=query_failures,
        config_warnings=list(config_warnings or []),
        parsed_without_timestamp=sum(
            1
            for session in sessions
            if session.status == "parsed" and session.last_ts is None
        ),
        bad_lines_in_parsed=sum(
            session.bad_lines
            for session in sessions
            if session.status == "parsed"
        ),
    )


def render_coverage_text(cov: Coverage) -> str:
    """Render coverage first-line summary plus every skip, failure, and warning."""

    lines = [
        "Coverage: "
        f"{cov.transcripts_found} transcripts found, "
        f"{cov.transcripts_parsed} parsed, "
        f"{len(cov.transcripts_skipped)} skipped; "
        f"{cov.in_window} in window; "
        f"{cov.total_tool_calls} tool calls "
        f"({cov.mcp_tool_calls} MCP); "
        f"server queries: {cov.servers_queried_ok} ok, "
        f"{len(cov.servers_query_failed)} failed"
    ]
    if cov.parsed_without_timestamp:
        lines.append(
            f"  {cov.parsed_without_timestamp} parsed transcript(s) had no "
            "usable timestamp and were excluded from the window"
        )
    if cov.bad_lines_in_parsed:
        lines.append(
            f"  {cov.bad_lines_in_parsed} line(s) were unparseable across "
            "parsed transcripts and are not counted"
        )
    for path, reason in cov.transcripts_skipped:
        lines.append(f"  skipped {_shorten_transcript_path(path)}: {reason}")
    for server, error in cov.servers_query_failed:
        lines.append(f"  server query failed {server}: {error}")
    for warning in cov.config_warnings:
        lines.append(f"  config warning: {warning}")
    return "\n".join(lines)


def _shorten_transcript_path(path: str) -> str:
    normalized = os.path.normpath(path).replace("\\", "/")
    marker = "/.claude/projects/"
    marker_index = normalized.casefold().find(marker)
    if marker_index >= 0:
        return "~" + normalized[marker_index:]
    return normalized
