"""Command-line interface for read-only MCP definition and usage reporting."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from mcp_top import __version__
from mcp_top.adapters import claude_code, codex as codex_adapter
from mcp_top.clis import codex as codex_config
from mcp_top.clis import cursor as cursor_config
from mcp_top.config import ServerConfig, discover_servers
from mcp_top.counter import count_calls
from mcp_top.coverage import render_coverage_text
from mcp_top.engine import (
    CliReport,
    CliReportInput,
    PruneSuggestion,
    Report,
    ServerRow,
    build_report,
)
from mcp_top.mcpclient import ServerTools, list_server_tools
from mcp_top.tokens import TokenCount, fmt


CLIS = {
    "claude-code": {
        "discover_servers": discover_servers,
        "find_transcripts": claude_code.find_transcripts,
        "parse_session": claude_code.parse_session,
        "session_key": claude_code.session_key,
        "detected": lambda home, project: (
            os.path.exists(os.path.join(home, ".claude.json"))
            or os.path.exists(os.path.join(home, ".claude", "projects"))
            or (
                project is not None
                and os.path.exists(os.path.join(project, ".mcp.json"))
            )
        ),
    },
    "codex": {
        "discover_servers": codex_config.discover_servers,
        "find_transcripts": codex_adapter.find_transcripts,
        "parse_session": codex_adapter.parse_session,
        "session_key": codex_adapter.session_key,
        "detected": lambda home, project: (
            os.path.exists(os.path.join(home, ".codex", "config.toml"))
            or os.path.exists(os.path.join(home, ".codex", "sessions"))
            or (
                project is not None
                and os.path.exists(
                    os.path.join(project, ".codex", "config.toml")
                )
            )
        ),
    },
    "cursor": {
        "discover_servers": cursor_config.discover_servers,
        "find_transcripts": None,
        "parse_session": None,
        "session_key": None,
        "usage_note": (
            "Cursor stores chats in undocumented SQLite; no transcript "
            "adapter in v0.2 -- usage unknown"
        ),
        "detected": lambda home, project: (
            os.path.exists(os.path.join(home, ".cursor", "mcp.json"))
            or (
                project is not None
                and os.path.exists(
                    os.path.join(project, ".cursor", "mcp.json")
                )
            )
        ),
    },
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
            print(
                json.dumps(
                    _report_json(report, include_prune=args.prune),
                    sort_keys=True,
                )
            )
        else:
            print(_render_human(report, include_prune=args.prune))
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
        "--prune",
        action="store_true",
        help=(
            "append suggested removals and review candidates; never applied "
            "(mcp-top is read-only)"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def _build(args: argparse.Namespace) -> Report:
    if args.cli == "all":
        cli_names = [
            name
            for name, entry in CLIS.items()
            if entry["detected"](args.home, args.project)
        ]
    else:
        cli_names = [args.cli]
    note = None
    if args.cli == "all" and not cli_names:
        note = f"no supported CLIs detected under {args.home}"
    inputs: list[CliReportInput] = []
    for cli_name in cli_names:
        entry = CLIS[cli_name]
        servers, warnings = entry["discover_servers"](args.home, args.project)
        server_tools_list = [
            _load_server_tools(server, args.no_query, args.timeout)
            for server in servers
        ]
        find_transcripts_func = entry["find_transcripts"]
        parse_session_func = entry["parse_session"]
        session_key_func = entry["session_key"]
        if (
            find_transcripts_func is None
            or parse_session_func is None
            or session_key_func is None
        ):
            sessions = []
            window = None
        else:
            paths = find_transcripts_func(args.home)
            configured = {server.name for server in servers}
            sessions = [
                parse_session_func(path, configured) for path in paths
            ]
            window = count_calls(
                sessions,
                [session_key_func(path, args.home) for path in paths],
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
                usage_note=entry.get("usage_note"),
            )
        )
    return build_report(inputs, note=note)


def _load_server_tools(
    server: ServerConfig, no_query: bool, timeout: float
) -> ServerTools:
    if server.enabled is False:
        return ServerTools(
            server=server.name,
            status="unsupported",
            error="disabled in config -- not queried",
            tools=[],
        )
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
    return list_server_tools(server, timeout=server.query_timeout or timeout)


def _report_json(report: Report, include_prune: bool = False) -> dict:
    payload = {
        "schema": "mcp-top/v2",
        "generated_note": report.generated_note,
        "clis": [
            _cli_json(cli_report, include_prune) for cli_report in report.clis
        ],
    }
    if report.note is not None:
        payload["note"] = report.note
    return payload


def _cli_json(cli_report: CliReport, include_prune: bool = False) -> dict:
    window = None
    if cli_report.window is not None:
        window = {
            "sessions": cli_report.window.window_sessions,
            "days": cli_report.window.window_days,
            "sessions_considered": cli_report.window.sessions_considered,
        }
    entry = {
        "cli": cli_report.cli,
        "window": window,
        "coverage": asdict(cli_report.coverage),
        "servers": [_row_json(row) for row in cli_report.rows],
    }
    if include_prune:
        # Additive field: appears only with --prune, so plain --json stays
        # byte-for-byte identical and the schema remains mcp-top/v2.
        entry["suggested_removals"] = [
            _suggestion_json(item) for item in cli_report.suggestions
        ]
    return entry


def _suggestion_json(item: PruneSuggestion) -> dict:
    reactivates = None
    if item.reactivates is not None:
        reactivates = {
            "server": item.reactivates.server,
            "scope": item.reactivates.scope,
            "source_path": item.reactivates.source_path,
        }
    return {
        "kind": item.kind,
        "server": item.server,
        "scope": item.scope,
        "source_path": item.source_path,
        "gross_tokens": {"value": item.gross_tokens, "exact": item.gross_exact},
        "net_tokens": item.net_tokens,
        "reactivates": reactivates,
        "reasons": item.reasons,
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
        "filtered_tools": row.filtered_tools,
    }


def _render_human(report: Report, include_prune: bool = False) -> str:
    sections = []
    if report.note is not None:
        sections.append(report.note)
    sections.extend([
        _render_cli_human(cli_report, include_header=len(report.clis) > 1)
        for cli_report in report.clis
    ])
    if include_prune:
        sections.append(_render_prune_block(report))
    sections.append(report.generated_note)
    return "\n\n".join(sections)


def _render_cli_human(cli_report: CliReport, include_header: bool) -> str:
    coverage = render_coverage_text(cli_report.coverage)
    suggestion_kind = {item.server: item.kind for item in cli_report.suggestions}
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
        if row.verdict == "prune":
            # Provenance-aware: only a clean, global-scope removal earns a
            # savings figure; everything else is a review candidate and never
            # asserts a number without its caveats (shown under --prune).
            kind = suggestion_kind.get(row.server)
            if kind == "suggestion" and row.def_tokens is not None:
                verdict = f"prune -> save {fmt(row.def_tokens)}/session"
            elif kind == "candidate":
                verdict = "prune candidate"
        if row.filtered_tools:
            verdict = (
                f"{verdict} "
                f"({row.filtered_tools} tool(s) hidden by config filters)"
            )
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
                f"  {row.server}: definitions unavailable -- "
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


def _render_prune_block(report: Report) -> str:
    """Render the two-tier --prune section: clean suggestions, then candidates.

    Never applied. Suggestions carry an estimated net saving; candidates carry
    only a gross figure plus the specific reasons they need review. CLIs with
    no usage adapter are reported explicitly rather than shown as an empty set.
    """

    lines = [
        "Suggested removals (never applied; mcp-top is read-only -- edit "
        "configs yourself):"
    ]
    suggestions = [
        (cli.cli, item)
        for cli in report.clis
        for item in cli.suggestions
        if item.kind == "suggestion"
    ]
    if suggestions:
        for cli_name, item in suggestions:
            saving = _fmt_tokens(item.net_tokens, item.gross_exact)
            lines.append(
                f"  - [{cli_name}] {_ascii(item.server)} ({item.scope}) in "
                f"{_ascii(item.source_path)} -> est. net saving "
                f"{saving}/session"
            )
        lines.append("  (~ marks a chars/4 estimate, rough error +/-25%)")
    else:
        lines.append("  none")

    lines.append("")
    lines.append("Prune candidates -- review before removing (net saving unknown):")
    candidates = [
        (cli.cli, item)
        for cli in report.clis
        for item in cli.suggestions
        if item.kind == "candidate"
    ]
    unavailable = [cli.cli for cli in report.clis if cli.window is None]
    if not candidates and not unavailable:
        lines.append("  none")
    for cli_name, item in candidates:
        gross = _fmt_tokens(item.gross_tokens, item.gross_exact)
        lines.append(
            f"  - [{cli_name}] {_ascii(item.server)} ({item.scope}) in "
            f"{_ascii(item.source_path)} -> removes {gross}/session gross, "
            "net unknown"
        )
        for reason in item.reasons:
            lines.append(f"      * {_ascii(reason)}")
    for cli_name in unavailable:
        lines.append(
            f"  - [{cli_name}] usage unavailable (no transcript adapter) -- "
            "no prune analysis"
        )
    return "\n".join(lines)


def _fmt_tokens(value: int | None, exact: bool) -> str:
    if value is None:
        return "unknown"
    return fmt(TokenCount(tokens=value, exact=exact))


def _ascii(text: str) -> str:
    """Force plain ASCII for terminals with narrow codepages (e.g. cp1252)."""

    return "".join(
        char if 32 <= ord(char) < 127 else "?" for char in text
    )


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
