"""Command-line interface for read-only MCP definition and usage reporting."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from mcp_top import __version__
from mcp_top.adapters.claude_code import (
    find_transcripts,
    parse_session,
    session_key,
)
from mcp_top.config import ServerConfig, discover_servers
from mcp_top.counter import count_calls
from mcp_top.coverage import render_coverage_text
from mcp_top.engine import (
    CliReport,
    CliReportInput,
    Report,
    ServerRow,
    build_report,
)
from mcp_top.mcpclient import ServerTools, list_server_tools
from mcp_top.tokens import fmt


CLIS = {
    "claude-code": {
        "discover_servers": discover_servers,
        "find_transcripts": find_transcripts,
        "parse_session": parse_session,
        "session_key": session_key,
    }
}


def main(argv: list[str] | None = None) -> int:
    """Run mcp-top, returning 2 for invalid usage or an internal failure."""

    if sys.version_info < (3, 11):
        print("mcp-top requires Python 3.11 or newer.", file=sys.stderr)
        return 2

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
    parser.add_argument("--sessions", type=_positive_int, default=30)
    parser.add_argument("--days", type=_positive_int, default=30)
    parser.add_argument("--timeout", type=_positive_float, default=20.0)
    parser.add_argument(
        "--cli",
        choices=[*CLIS.keys(), "all"],
        default="all",
        help="CLI transcript/config source to inspect",
    )
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
    cli_names = list(CLIS) if args.cli == "all" else [args.cli]
    inputs: list[CliReportInput] = []
    for cli_name in cli_names:
        entry = CLIS[cli_name]
        servers, warnings = entry["discover_servers"](args.home, args.project)
        server_tools_list = [
            _load_server_tools(server, args.no_query, args.timeout)
            for server in servers
        ]
        paths = entry["find_transcripts"](args.home)
        configured = {server.name for server in servers}
        sessions = [entry["parse_session"](path, configured) for path in paths]
        window = count_calls(
            sessions,
            [entry["session_key"](path, args.home) for path in paths],
            window_sessions=args.sessions,
            window_days=args.days,
        )
        inputs.append(
            CliReportInput(
                cli_name=cli_name,
                servers=servers,
                server_tools_list=server_tools_list,
                sessions=sessions,
                window=window,
                config_warnings=warnings,
            )
        )
    return build_report(inputs)


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
            error="unsupported transport (this version queries stdio only)",
            tools=[],
        )
    return list_server_tools(server, timeout=timeout)


def _report_json(report: Report) -> dict:
    return {
        "schema": "mcp-top/v2",
        "generated_note": report.generated_note,
        "clis": [_cli_json(cli_report) for cli_report in report.clis],
    }


def _cli_json(cli_report: CliReport) -> dict:
    window = None
    if cli_report.window is not None:
        window = {
            "sessions": cli_report.window.window_sessions,
            "days": cli_report.window.window_days,
            "sessions_considered": cli_report.window.sessions_considered,
        }
    return {
        "cli": cli_report.cli,
        "window": window,
        "coverage": asdict(cli_report.coverage),
        "servers": [_row_json(row) for row in cli_report.rows],
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
        "usage_status": row.usage_status,
        "called_tools": row.called_tools,
        "verdict": row.verdict,
    }


def _render_human(report: Report) -> str:
    sections = [
        _render_cli_human(cli_report, include_header=len(report.clis) > 1)
        for cli_report in report.clis
    ]
    sections.append(report.generated_note)
    return "\n\n".join(sections)


def _render_cli_human(cli_report: CliReport, include_header: bool) -> str:
    coverage = render_coverage_text(cli_report.coverage)
    headers = (
        "SERVER",
        "SCOPE",
        "TOOLS",
        "DEF TOKENS",
        "CALLS(window)",
        "VERDICT",
    )
    body = []
    for row in cli_report.rows:
        verdict = row.verdict
        if row.verdict == "prune" and row.def_tokens is not None:
            verdict = f"prune -> save {fmt(row.def_tokens)}/session"
        body.append(
            (
                row.server,
                row.scope,
                "?" if row.tool_count is None else str(row.tool_count),
                "?" if row.def_tokens is None else fmt(row.def_tokens),
                "-" if row.calls is None else str(row.calls),
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
    for row in cli_report.rows:
        if row.def_status != "ok":
            table_lines.append(
                f"  {row.server}: definitions unavailable — "
                f"{row.def_error or 'unknown error'}"
            )

    breakdown = []
    for row in cli_report.rows:
        if row.verdict not in {"review", "keep"} or not row.called_tools:
            continue
        breakdown.append(f"{row.server} called tools:")
        for tool, count in sorted(
            row.called_tools.items(), key=lambda item: (-item[1], item[0])
        ):
            breakdown.append(f"  - {tool}: {count}")

    parts = []
    if include_header:
        parts.append(f"=== {cli_report.cli} ===")
    parts.extend([coverage, "\n".join(table_lines)])
    if breakdown:
        parts.append("\n".join(breakdown))
    return "\n\n".join(parts)


def _format_table_row(row: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(
        value.ljust(widths[index]) for index, value in enumerate(row)
    ).rstrip()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not parsed > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed
