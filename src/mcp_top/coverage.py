"""Coverage accounting and honest human-readable coverage reporting."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from mcp_top.counter import UsageWindow
from mcp_top.mcpclient import ServerTools
from mcp_top.transcripts import SessionResult


@dataclass
class RecordedResultsCoverage:
    """Result classification counts for a measured transcript window."""

    paired: int = 0
    partial: int = 0
    unsupported: int = 0
    unmeasurable: int = 0
    unpaired_results: int = 0
    unpaired_calls: int = 0


@dataclass
class Coverage:
    """What mcp-top could and could not inspect for this report."""

    transcripts_found: int
    transcripts_parsed: int
    transcripts_skipped: list[tuple[str, str]]
    in_window: int | None
    total_tool_calls: int | None
    mcp_tool_calls: int | None
    servers_queried_ok: int
    servers_query_failed: list[tuple[str, str]]
    config_warnings: list[str]
    parsed_without_timestamp: int = 0
    bad_lines_in_parsed: int = 0
    future_sessions: int = 0
    duplicate_tool_use: int = 0
    unattributed_mcp_calls: int = 0
    servers_unsupported: int = 0
    usage_note: str | None = None
    unmatched_enabled_tools: list[tuple[str, list[str]]] = field(
        default_factory=list
    )
    project_filter_active: bool = False
    sessions_with_project: int = 0
    sessions_unattributed: int = 0
    sessions_matching_project: int = 0
    recorded_results: RecordedResultsCoverage | None = None


def build_coverage(
    sessions: list[SessionResult],
    window: UsageWindow | None,
    server_tools_list: list[ServerTools],
    config_warnings: list[str] | None = None,
    usage_note: str | None = None,
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
    total_tool_calls: int | None = None
    mcp_tool_calls: int | None = None
    unattributed_mcp_calls = 0
    in_window: int | None = None
    future_sessions = 0
    project_filter_active = False
    sessions_with_project = 0
    sessions_unattributed = 0
    sessions_matching_project = 0
    recorded_results = None
    if window is None:
        usage_note = usage_note or "no transcript adapter for this CLI"
    else:
        total_tool_calls = sum(window.counts.values())
        mcp_tool_calls = sum(
            count
            for called_tools in window.server_tool_counts.values()
            for count in called_tools.values()
        )
        unattributed_mcp_calls = window.unattributed_mcp_calls
        in_window = window.sessions_considered
        future_sessions = window.future_sessions
        project_filter_active = window.project_filter is not None
        sessions_with_project = window.sessions_with_project
        sessions_unattributed = window.sessions_unattributed
        sessions_matching_project = window.sessions_matching_project
        recorded_results = RecordedResultsCoverage(
            paired=window.results_paired,
            partial=window.results_partial,
            unsupported=window.results_unsupported,
            unmeasurable=window.results_unmeasurable,
            unpaired_results=window.results_unpaired,
            unpaired_calls=window.results_unpaired_calls,
        )
        if window.sessions_considered == 0:
            if not sessions:
                usage_note = usage_note or "no transcripts found -- usage unknown"
            else:
                usage_note = (
                    usage_note
                    or "no usable sessions in the window -- usage unknown"
                )
    return Coverage(
        transcripts_found=len(sessions),
        transcripts_parsed=sum(
            1 for session in sessions if session.status == "parsed"
        ),
        transcripts_skipped=skipped,
        in_window=in_window,
        total_tool_calls=total_tool_calls,
        mcp_tool_calls=mcp_tool_calls,
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
        future_sessions=future_sessions,
        duplicate_tool_use=sum(
            session.duplicate_tool_use
            for session in sessions
            if session.status == "parsed"
        ),
        unattributed_mcp_calls=unattributed_mcp_calls,
        servers_unsupported=sum(
            1 for result in server_tools_list if result.status == "unsupported"
        ),
        usage_note=usage_note,
        unmatched_enabled_tools=[
            (result.server, list(result.unmatched_enabled_tools))
            for result in server_tools_list
            if result.status == "ok" and result.unmatched_enabled_tools
        ],
        project_filter_active=project_filter_active,
        sessions_with_project=sessions_with_project,
        sessions_unattributed=sessions_unattributed,
        sessions_matching_project=sessions_matching_project,
        recorded_results=recorded_results,
    )


def render_coverage_text(cov: Coverage) -> str:
    """Render coverage first-line summary plus every skip, failure, and warning."""

    usage_fragment = (
        "usage: unknown (no transcript adapter)"
        if cov.in_window is None
        else (
            f"{cov.in_window} in window; "
            f"{cov.total_tool_calls} tool calls "
            f"({cov.mcp_tool_calls} MCP)"
        )
    )
    lines = [
        "Coverage: "
        f"{cov.transcripts_found} transcripts found, "
        f"{cov.transcripts_parsed} parsed, "
        f"{len(cov.transcripts_skipped)} skipped; "
        f"{usage_fragment}; "
        f"server queries: {cov.servers_queried_ok} ok, "
        f"{len(cov.servers_query_failed)} failed, "
        f"{cov.servers_unsupported} not queried"
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
    if cov.future_sessions:
        lines.append(
            f"  {cov.future_sessions} transcript group(s) had future-dated "
            "timestamps and were excluded from the window"
        )
    if cov.duplicate_tool_use:
        lines.append(
            f"  {cov.duplicate_tool_use} duplicate tool_use block(s) were "
            "excluded from call counts"
        )
    if cov.unattributed_mcp_calls:
        lines.append(
            f"  {cov.unattributed_mcp_calls} MCP-prefixed call(s) could not "
            "be attributed to a server"
        )
    if cov.recorded_results is not None and any(
        (
            cov.recorded_results.paired,
            cov.recorded_results.partial,
            cov.recorded_results.unsupported,
            cov.recorded_results.unmeasurable,
            cov.recorded_results.unpaired_results,
            cov.recorded_results.unpaired_calls,
        )
    ):
        lines.append(
            f"  recorded results: {cov.recorded_results.paired} paired, "
            f"{cov.recorded_results.partial} partial, "
            f"{cov.recorded_results.unsupported} unsupported, "
            f"{cov.recorded_results.unmeasurable} unmeasurable, "
            f"{cov.recorded_results.unpaired_results} unpaired result(s), "
            f"{cov.recorded_results.unpaired_calls} unpaired call(s) "
            "(recorded result footprint is a recorded-bytes lower bound)"
        )
    if cov.project_filter_active:
        lines.append(
            "  usage scoped to the current project: "
            f"{cov.sessions_matching_project} in-project session(s), "
            f"{cov.sessions_unattributed} unattributed session(s) excluded"
        )
    if cov.usage_note is not None:
        lines.append(f"  usage: {cov.usage_note}")
    for server, names in cov.unmatched_enabled_tools:
        lines.append(
            f"  {server}: enabled_tools names not returned by this "
            f"tools/list snapshot: {', '.join(names)}"
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
    for marker in ("/.claude/projects/", "/.codex/sessions/"):
        marker_index = normalized.casefold().find(marker)
        if marker_index >= 0:
            return "~" + normalized[marker_index:]
    return normalized
