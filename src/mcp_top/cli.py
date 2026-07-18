"""Command-line interface for read-only MCP definition and usage reporting."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from dataclasses import asdict
from datetime import datetime, timezone

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
from mcp_top.projects import claude_project_slug, normalize_project_key
from mcp_top.snapshot import (
    SnapshotLoadError,
    build_snapshot,
    diff_snapshots,
    load_snapshot_file,
    render_diff_human,
    snapshot_to_dict,
)
from mcp_top.tokens import TokenCount, fmt


CLIS = {
    "claude-code": {
        "discover_servers": discover_servers,
        "find_transcripts": claude_code.find_transcripts,
        "parse_session": claude_code.parse_session,
        "session_key": claude_code.session_key,
        # Claude transcripts expose only a lossy directory slug, so project
        # matching is best-effort lexical matching rather than path identity.
        "project_key": lambda project: claude_project_slug(
            os.path.normpath(os.path.abspath(project))
        ),
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
        "project_key": lambda project: normalize_project_key(
            os.path.abspath(project)
        ),
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
        "project_key": lambda project: None,
        "usage_note": (
            "Cursor stores chats in undocumented SQLite; no transcript "
            "adapter -- usage unknown"
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


_TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def main(argv: list[str] | None = None) -> int:
    """Run mcp-top, returning 2 for invalid usage or an internal failure."""

    if sys.version_info < (3, 11):
        print("mcp-top requires Python 3.11 or newer.", file=sys.stderr)
        return 2

    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "diff":
            old_snapshot = load_snapshot_file(args.old)
            new_snapshot = load_snapshot_file(args.new)
            diff = diff_snapshots(old_snapshot, new_snapshot)
            if args.json:
                print(json.dumps(diff, sort_keys=True))
            else:
                print(render_diff_human(diff))
            return 0
        report = _build(args)
        if args.command == "snapshot":
            snapshot = build_snapshot(
                report,
                datetime.now(timezone.utc),
                default_sessions=args.sessions,
                default_days=args.days,
                redact_identifiers=args.redact_identifiers,
            )
            rendered = json.dumps(snapshot_to_dict(snapshot), sort_keys=True)
            if args.out is None:
                print(rendered)
            else:
                try:
                    with open(args.out, "w", encoding="utf-8") as handle:
                        handle.write(rendered)
                        handle.write("\n")
                except OSError as err:
                    print(
                        "mcp-top snapshot: cannot write output: "
                        f"{err.strerror or err}",
                        file=sys.stderr,
                    )
                    return 2
            return 0
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
    except SnapshotLoadError as err:
        print(f"mcp-top diff: {err}", file=sys.stderr)
        return 2
    except Exception as err:
        print(f"mcp-top: internal error: {err}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_arguments(common, default_values=True)
    subcommand_common = argparse.ArgumentParser(add_help=False)
    _add_common_arguments(subcommand_common, default_values=False)
    parser = argparse.ArgumentParser(
        prog="mcp-top",
        description="Rank configured MCP servers by definition cost and recent usage.",
        parents=[common],
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")
    snapshot = subparsers.add_parser(
        "snapshot",
        parents=[subcommand_common],
        help="emit a redacted aggregate snapshot",
    )
    snapshot.add_argument(
        "--out",
        metavar="FILE",
        help=(
            "write the snapshot to FILE instead of stdout; overwrites FILE "
            "if it exists"
        ),
    )
    snapshot.add_argument(
        "--redact-identifiers",
        action="store_true",
        help="pseudonymize server and called-tool names in the snapshot",
    )
    diff = subparsers.add_parser(
        "diff",
        parents=[subcommand_common],
        help="compare two redacted aggregate snapshots",
    )
    diff.add_argument("old")
    diff.add_argument("new")
    return parser


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_values: bool,
) -> None:
    default = None if default_values else argparse.SUPPRESS
    parser.add_argument(
        "--home",
        default=os.path.expanduser("~") if default_values else default,
    )
    parser.add_argument(
        "--project",
        default=os.getcwd() if default_values else default,
    )
    parser.add_argument(
        "--sessions",
        type=_positive_int,
        default=30 if default_values else default,
    )
    parser.add_argument(
        "--days",
        type=_positive_int,
        default=30 if default_values else default,
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=20.0 if default_values else default,
    )
    parser.add_argument(
        "--cli",
        choices=[*CLIS.keys(), "all"],
        default="all" if default_values else default,
        help="CLI transcript/config source to inspect",
    )
    parser.add_argument(
        "--no-query",
        action="store_true",
        default=False if default_values else default,
        help="do not launch configured MCP servers",
    )
    parser.add_argument(
        "--query-project",
        action="store_true",
        default=False if default_values else default,
        help=(
            "also launch project-scope MCP servers (Claude project "
            ".mcp.json, Cursor project .cursor/mcp.json); off by default "
            "because a project's config is untrusted input (e.g. a cloned "
            "repo) and Claude Code itself gates project servers behind "
            "workspace trust -- pass this only in trusted repos"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if default_values else default,
        help="emit JSON",
    )
    parser.add_argument(
        "--project-usage",
        action="store_true",
        default=False if default_values else default,
        help=(
            "restrict usage counts to the current --project (default counts "
            "usage across all projects); Codex uses the recorded cwd exactly, "
            "while Claude attribution is best-effort slug-based; uncertain or "
            "absent attribution is reported, not guessed."
        ),
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        default=False if default_values else default,
        help=(
            "append suggested removals and review candidates; never applied "
            "(mcp-top is read-only)"
        ),
    )


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
            _load_server_tools(
                server, args.no_query, args.timeout, args.query_project
            )
            for server in servers
        ]
        find_transcripts_func = entry["find_transcripts"]
        parse_session_func = entry["parse_session"]
        session_key_func = entry["session_key"]
        project_key_func = entry.get("project_key")
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
            project_filter = (
                project_key_func(args.project)
                if args.project_usage and project_key_func is not None
                else None
            )
            window = count_calls(
                sessions,
                [session_key_func(path, args.home) for path in paths],
                window_sessions=args.sessions,
                window_days=args.days,
                project_filter=project_filter,
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
    server: ServerConfig,
    no_query: bool,
    timeout: float,
    query_project: bool = False,
) -> ServerTools:
    if server.reserved:
        return ServerTools(
            server=server.name,
            status="unsupported",
            error=(
                "reserved Claude Code server name -- Claude Code skips it "
                "at load time"
            ),
            tools=[],
        )
    if server.scope == "project" and not query_project:
        return ServerTools(
            server=server.name,
            status="unsupported",
            error=(
                "project scope not queried by default; pass "
                "--query-project in trusted repos"
            ),
            tools=[],
        )
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
        "schema": "mcp-top/v3",
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
        entry["suggested_removals"] = [
            _suggestion_json(cli_report.cli, item)
            for item in cli_report.suggestions
        ]
    return entry


def _suggestion_json(cli_name: str, item: PruneSuggestion) -> dict:
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
        "removes_advertised_max_tokens": _token_json(
            item.removes_advertised_max_tokens
        ),
        "removes_upfront_floor_tokens": _token_json(
            item.removes_upfront_floor_tokens
        ),
        "reactivates": reactivates,
        "reasons": item.reasons,
        "recipe": _recipe_json(_remediation_recipe(cli_name, item)),
    }


def _row_json(row: ServerRow) -> dict:
    return {
        "server": row.server,
        "scope": row.scope,
        "transport": row.transport,
        "advertised_max_tokens": _token_json(row.advertised_max_tokens),
        "upfront_floor_tokens": _token_json(row.upfront_floor_tokens),
        "loading_regime": row.loading_regime,
        "regime_evidence": row.regime_evidence,
        "def_status": row.def_status,
        "def_error": row.def_error,
        "tool_count": row.tool_count,
        "calls": row.calls,
        "usage_status": row.usage_status,
        "called_tools": row.called_tools,
        "verdict": row.verdict,
        "filtered_tools": row.filtered_tools,
        "recorded_result_footprint": _footprint_json(
            row.recorded_result_footprint
        ),
    }


def _token_json(tokens: TokenCount | None) -> dict | None:
    if tokens is None:
        return None
    return {"value": tokens.tokens, "exact": tokens.exact}


def _footprint_json(footprint: object) -> dict | None:
    if footprint is None:
        return None
    return {
        "results": footprint.results,
        "lower_bound": footprint.lower_bound,
        "basis": footprint.basis,
        "token_estimate": footprint.token_estimate,
        "total_bytes": footprint.total_bytes,
        "total_tokens": _token_json(footprint.total_tokens),
        "max_tokens": _token_json(footprint.max_tokens),
        "p50_tokens": _token_json(footprint.p50_tokens),
        "p90_tokens": _token_json(footprint.p90_tokens),
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
        "TOKEN RANGE",
        "REGIME",
        "CALLS(window)",
        "VERDICT",
    )
    body = []
    for row in cli_report.rows:
        verdict = row.verdict
        if row.verdict == "prune":
            kind = suggestion_kind.get(row.server)
            if kind == "suggestion" and row.advertised_max_tokens is not None:
                verdict = (
                    "prune -> "
                    + _fmt_removal_range(
                        row.advertised_max_tokens,
                        row.upfront_floor_tokens,
                    )
                )
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
                _fmt_token_range(row),
                row.loading_regime,
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
    for row in cli_report.rows:
        if row.recorded_result_footprint is None:
            continue
        footprint = row.recorded_result_footprint
        table_lines.append(
            f"  {_ascii(row.server)} recorded result footprint: "
            f"{footprint.total_bytes} recorded UTF-8 byte(s) (lower bound) -> "
            f"{fmt(footprint.total_tokens)} tok (estimate) across "
            f"{footprint.results} result(s) "
            f"(max {fmt(footprint.max_tokens)}, "
            f"p90 {fmt(footprint.p90_tokens)})"
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

    Never applied. Suggestions and candidates carry an advertised-maximum and
    upfront-floor removal range; candidates also explain why their actual
    removal impact is unknown. CLIs with no usage adapter are reported
    explicitly rather than shown as an empty set.
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
            removal_range = _fmt_removal_range(
                item.removes_advertised_max_tokens,
                item.removes_upfront_floor_tokens,
            )
            lines.append(
                f"  - [{cli_name}] {_ascii(item.server)} ({item.scope}) in "
                f"{_ascii(item.source_path)} -> "
                f"{removal_range}"
            )
            recipe = _remediation_recipe(cli_name, item)
            if recipe["kind"] == "command":
                lines.append(
                    f"      command: {_ascii(_shell_join(recipe['argv']))}"
                )
            else:
                lines.append(
                    f"      edit: {_ascii(recipe['change'])}"
                )
        lines.append("  (~ marks a chars/4 estimate, rough error +/-25%)")
    else:
        lines.append("  none")

    lines.append("")
    lines.append(
        "Prune candidates -- review before removing "
        "(actual removal impact unknown):"
    )
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
        removal_range = _fmt_removal_range(
            item.removes_advertised_max_tokens,
            item.removes_upfront_floor_tokens,
        )
        lines.append(
            f"  - [{cli_name}] {_ascii(item.server)} ({item.scope}) in "
            f"{_ascii(item.source_path)} -> "
            f"{removal_range}, "
            "actual removal impact unknown"
        )
        for reason in item.reasons:
            lines.append(f"      * {_ascii(reason)}")
        recipe = _remediation_recipe(cli_name, item)
        lines.append(f"      edit: {_ascii(recipe['change'])}")
    for cli_name in unavailable:
        lines.append(
            f"  - [{cli_name}] usage unavailable (no transcript adapter) -- "
            "no prune analysis"
        )
    return "\n".join(lines)


def _remediation_recipe(
    cli_name: str, item: PruneSuggestion
) -> dict[str, object]:
    """Return a recipe for a prune suggestion or candidate.

    Only Claude Code's clean suggestions get an executable command
    (``claude mcp remove``). Codex clean suggestions get precise structured
    guidance -- the exact file, ``[mcp_servers.<name>]`` table, and the
    exact ``enabled = false`` line to add -- but never a command: a
    text-manipulation edit of a live TOML file can corrupt multiline
    strings or drop comments, so mcp-top never emits one. Everything else
    (all candidates, and every Cursor recipe) is guidance-only too.
    """

    if item.kind == "suggestion" and cli_name == "claude-code":
        claude_scope = _claude_cli_scope(item.scope)
        return {
            "kind": "command",
            "source_path": item.source_path,
            "scope": item.scope,
            "argv": [
                "claude",
                "mcp",
                "remove",
                "--scope",
                claude_scope,
                item.server,
            ],
            "change": (
                f"Remove {claude_scope}-scope Claude Code MCP server "
                f"{item.server!r}; source config {item.source_path} "
                f"({item.scope} scope)."
            ),
        }

    return _guidance_recipe(cli_name, item)


def _guidance_recipe(cli_name: str, item: PruneSuggestion) -> dict[str, object]:
    if cli_name == "codex":
        table = _codex_table_header(item.server)
        change = (
            f"Edit {item.source_path} ({item.scope} scope): set "
            f"enabled = false under {table}. For tool-level pruning, add "
            "the unused tool name to disabled_tools in that same table."
        )
    elif cli_name == "cursor":
        change = (
            f"Edit {item.source_path} ({item.scope} scope): review "
            f"mcpServers.{item.server!r} and disable or remove it in that "
            "exact mcp.json file only after checking the candidate reasons."
        )
    elif cli_name == "claude-code":
        change = (
            f"Review {item.source_path} ({item.scope} scope) before changing "
            f"Claude Code MCP server {item.server!r}; candidates intentionally "
            "do not include removal commands."
        )
    else:
        change = (
            f"Review {item.source_path} ({item.scope} scope) before changing "
            f"MCP server {item.server!r}; candidates intentionally do not "
            "include commands."
        )
    return {
        "kind": "guidance",
        "source_path": item.source_path,
        "scope": item.scope,
        "change": change,
    }


def _recipe_json(recipe: dict[str, object]) -> dict[str, object]:
    payload = {
        "kind": recipe["kind"],
        "source_path": recipe["source_path"],
        "scope": recipe["scope"],
        "change": recipe["change"],
    }
    if recipe["kind"] == "command":
        payload["argv"] = recipe["argv"]
    return payload


def _claude_cli_scope(scope: str) -> str:
    return {"user-project": "local", "project": "project", "user": "user"}.get(
        scope,
        scope,
    )


def _codex_table_header(server_name: str) -> str:
    return f"[mcp_servers.{_toml_key(server_name)}]"


def _toml_key(value: str) -> str:
    if _TOML_BARE_KEY.fullmatch(value):
        return value
    return json.dumps(value, ensure_ascii=True)


def _shell_join(argv: object) -> str:
    if not isinstance(argv, list):
        return ""
    return " ".join(shlex.quote(str(part)) for part in argv)


def _fmt_token_range(row: ServerRow) -> str:
    if row.advertised_max_tokens is None:
        return "?"
    if (
        row.upfront_floor_tokens is not None
        and row.upfront_floor_tokens.tokens == row.advertised_max_tokens.tokens
        and row.upfront_floor_tokens.exact == row.advertised_max_tokens.exact
    ):
        return fmt(row.advertised_max_tokens)
    floor = (
        "?"
        if row.upfront_floor_tokens is None
        else fmt(row.upfront_floor_tokens)
    )
    return f">={floor}..{fmt(row.advertised_max_tokens)}"


def _fmt_removal_range(
    advertised: TokenCount,
    upfront: TokenCount | None,
) -> str:
    floor = "unknown" if upfront is None else fmt(upfront)
    return (
        f"removes advertised up to {fmt(advertised)} / "
        f"upfront at least {floor}"
    )


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
