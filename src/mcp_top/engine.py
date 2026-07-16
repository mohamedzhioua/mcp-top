"""Join configured servers, definitions, and transcript calls into rankings."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_top.adapters.claude_code import SessionResult
from mcp_top.config import ServerConfig
from mcp_top.counter import UsageWindow
from mcp_top.coverage import Coverage, build_coverage
from mcp_top.mcpclient import ServerTools
from mcp_top.tokens import HEURISTIC, TokenCount, estimate_tool_definition


REVIEW_THRESHOLD = 3


@dataclass
class ServerRow:
    server: str
    scope: str
    transport: str
    def_tokens: TokenCount | None
    def_status: str
    def_error: str | None
    tool_count: int | None
    calls: int
    called_tools: dict[str, int]
    verdict: str


@dataclass
class Report:
    rows: list[ServerRow]
    window: UsageWindow
    coverage: Coverage
    generated_note: str


def build_report(
    servers: list[ServerConfig],
    server_tools_list: list[ServerTools],
    sessions: list[SessionResult],
    window: UsageWindow,
    config_warnings: list[str] | None = None,
) -> Report:
    """Build the ranked report.

    A server with zero calls is ``prune`` only when definition cost was measured;
    otherwise it is ``review``. One through ``REVIEW_THRESHOLD`` (3) calls is
    ``review``, and more than 3 calls is ``keep``. Unconfigured MCP servers are
    retained so transcript usage is never silently dropped.
    """

    tools_by_server = {result.server: result for result in server_tools_list}
    calls_by_server: dict[str, dict[str, int]] = {}
    for full_name, count in window.counts.items():
        parsed = _parse_mcp_tool_name(full_name)
        if parsed is None:
            continue
        server_name, tool_name = parsed
        called_tools = calls_by_server.setdefault(server_name, {})
        called_tools[tool_name] = called_tools.get(tool_name, 0) + count

    rows: list[ServerRow] = []
    configured_names = {server.name for server in servers}
    for server in servers:
        result = tools_by_server.get(server.name)
        if result is None:
            result = ServerTools(
                server=server.name,
                status="unsupported",
                error="definitions not queried",
                tools=[],
            )
        def_tokens = _definition_tokens(result)
        called_tools = calls_by_server.get(server.name, {})
        calls = sum(called_tools.values())
        rows.append(
            ServerRow(
                server=server.name,
                scope=server.scope,
                transport=server.transport,
                def_tokens=def_tokens,
                def_status=result.status,
                def_error=result.error,
                tool_count=len(result.tools) if result.status == "ok" else None,
                calls=calls,
                called_tools=called_tools,
                verdict=_verdict(calls, result.status),
            )
        )

    for server_name, called_tools in calls_by_server.items():
        if server_name in configured_names:
            continue
        calls = sum(called_tools.values())
        rows.append(
            ServerRow(
                server=server_name,
                scope="(not configured)",
                transport="unknown",
                def_tokens=None,
                def_status="unsupported",
                def_error="server is not configured",
                tool_count=None,
                calls=calls,
                called_tools=called_tools,
                verdict=_verdict(calls, "unsupported"),
            )
        )

    severity = {"prune": 0, "review": 1, "keep": 2, "unknown": 3}
    rows.sort(
        key=lambda row: (
            severity[row.verdict],
            -(row.def_tokens.tokens if row.def_tokens is not None else 0),
            row.server,
        )
    )
    return Report(
        rows=rows,
        window=window,
        coverage=build_coverage(
            sessions, window, server_tools_list, config_warnings
        ),
        generated_note=(
            f"Definition token counts use the {HEURISTIC} heuristic; "
            "~ means estimate."
        ),
    )


def _definition_tokens(result: ServerTools) -> TokenCount | None:
    if result.status != "ok":
        return None
    estimates = [estimate_tool_definition(tool) for tool in result.tools]
    return TokenCount(
        tokens=sum(estimate.tokens for estimate in estimates),
        exact=all(estimate.exact for estimate in estimates),
    )


def _parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    prefix = "mcp__"
    if not name.startswith(prefix):
        return None
    remainder = name[len(prefix) :]
    if "__" not in remainder:
        return None
    server, tool = remainder.split("__", 1)
    if not server or not tool:
        return None
    return server, tool


def _verdict(calls: int, def_status: str) -> str:
    if calls == 0:
        return "prune" if def_status == "ok" else "review"
    if calls <= REVIEW_THRESHOLD:
        return "review"
    return "keep"
