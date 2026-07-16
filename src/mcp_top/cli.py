"""Command-line interface for read-only MCP definition and usage reporting."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from mcp_top import __version__
from mcp_top.adapters.claude_code import find_transcripts, parse_session
from mcp_top.config import ServerConfig, discover_servers
from mcp_top.counter import count_calls
from mcp_top.coverage import render_coverage_text
from mcp_top.engine import Report, ServerRow, build_report
from mcp_top.mcpclient import ServerTools, list_server_tools
from mcp_top.tokens import fmt


def main(argv: list[str] | None = None) -> int:
    """Run mcp-top, returning 2 only for an unexpected internal failure."""

    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = _build(args)
        if args.json:
            print(json.dumps(_report_json(report), sort_keys=True))
        else:
            print(_render_human(report))
        return 0
    except Exception as err:
        print(f"mcp-top: internal error: {err}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-top",
        description="Rank configured MCP servers by definition cost and recent usage.",
    )
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("--sessions", type=int, default=30)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--no-query",
        action="store_true",
        help="do not launch configured MCP servers",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def _build(args: argparse.Namespace) -> Report:
    servers, warnings = discover_servers(args.home, args.project)
    server_tools_list = [
        _load_server_tools(server, args.no_query, args.timeout)
        for server in servers
    ]
    paths = find_transcripts(args.home)
    sessions = [parse_session(path) for path in paths]
    window = count_calls(
        sessions,
        window_sessions=args.sessions,
        window_days=args.days,
    )
    return build_report(
        servers,
        server_tools_list,
        sessions,
        window,
        config_warnings=warnings,
    )


def _load_server_tools(
    server: ServerConfig, no_query: bool, timeout: float
) -> ServerTools:
    if no_query:
        return ServerTools(
            server=server.name,
            status="unsupported",
            error="skipped by --no-query",
            tools=[],
        )
    if server.transport != "stdio":
        return ServerTools(
            server=server.name,
            status="unsupported",
            error="unsupported transport (v0.1 queries stdio only)",
            tools=[],
        )
    return list_server_tools(server, timeout=timeout)


def _report_json(report: Report) -> dict:
    return {
        "schema": "mcp-top/v1",
        "generated_note": report.generated_note,
        "window": {
            "sessions": report.window.window_sessions,
            "days": report.window.window_days,
            "sessions_considered": report.window.sessions_considered,
        },
        "coverage": asdict(report.coverage),
        "servers": [_row_json(row) for row in report.rows],
    }


def _row_json(row: ServerRow) -> dict:
    def_tokens = None
    if row.def_tokens is not None:
        def_tokens = {
            "value": row.def_tokens.tokens,
            "exact": row.def_tokens.exact,
        }
    return {
        "server": row.server,
        "scope": row.scope,
        "transport": row.transport,
        "def_tokens": def_tokens,
        "def_status": row.def_status,
        "def_error": row.def_error,
        "tool_count": row.tool_count,
        "calls": row.calls,
        "called_tools": row.called_tools,
        "verdict": row.verdict,
    }


def _render_human(report: Report) -> str:
    coverage = render_coverage_text(report.coverage)
    headers = (
        "SERVER",
        "SCOPE",
        "TOOLS",
        "DEF TOKENS",
        "CALLS(window)",
        "VERDICT",
    )
    body = []
    for row in report.rows:
        verdict = row.verdict
        if row.verdict == "prune" and row.def_tokens is not None:
            verdict = f"prune -> save {fmt(row.def_tokens)}/session"
        body.append(
            (
                row.server,
                row.scope,
                "?" if row.tool_count is None else str(row.tool_count),
                "?" if row.def_tokens is None else fmt(row.def_tokens),
                str(row.calls),
                verdict,
            )
        )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in body))
        if body
        else len(headers[index])
        for index in range(len(headers))
    ]
    table_lines = [_format_table_row(headers, widths)]
    table_lines.append("  ".join("-" * width for width in widths))
    table_lines.extend(_format_table_row(row, widths) for row in body)

    breakdown = []
    for row in report.rows:
        if row.verdict not in {"review", "keep"} or not row.called_tools:
            continue
        breakdown.append(f"{row.server} called tools:")
        for tool, count in sorted(
            row.called_tools.items(), key=lambda item: (-item[1], item[0])
        ):
            breakdown.append(f"  - {tool}: {count}")

    parts = [coverage, "\n".join(table_lines)]
    if breakdown:
        parts.append("\n".join(breakdown))
    parts.append(report.generated_note)
    return "\n\n".join(parts)


def _format_table_row(row: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(
        value.ljust(widths[index]) for index, value in enumerate(row)
    ).rstrip()
