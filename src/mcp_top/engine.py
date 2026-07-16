"""Join configured servers, definitions, and transcript calls into rankings."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_top.config import ServerConfig
from mcp_top.counter import UsageWindow
from mcp_top.coverage import Coverage, build_coverage
from mcp_top.mcpclient import ServerTools
from mcp_top.tokens import HEURISTIC, TokenCount, estimate_tool_definition
from mcp_top.transcripts import SessionResult


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
    calls: int | None
    usage_status: str
    called_tools: dict[str, int]
    verdict: str


@dataclass
class CliReport:
    cli: str
    rows: list[ServerRow]
    window: UsageWindow | None
    coverage: Coverage


@dataclass
class Report:
    clis: list[CliReport]
    generated_note: str


@dataclass
class CliReportInput:
    """Input bundle for one CLI report."""

    cli_name: str
    servers: list[ServerConfig]
    server_tools_list: list[ServerTools]
    sessions: list[SessionResult]
    window: UsageWindow | None
    config_warnings: list[str] | None = None


def build_cli_report(
    cli_name: str,
    servers: list[ServerConfig],
    server_tools_list: list[ServerTools],
    sessions: list[SessionResult],
    window: UsageWindow | None,
    config_warnings: list[str] | None = None,
) -> CliReport:
    """Build the ranked report for one CLI.

    Verdict rules:
    ``usage_status == "unsupported"`` is always ``unknown``.
    Zero measured calls are ``prune`` only when definition cost was measured;
    otherwise they are ``review``. One through ``REVIEW_THRESHOLD`` (3)
    measured calls are ``review``, and more than 3 measured calls is ``keep``.
    Unconfigured MCP servers are retained so transcript usage is never silently
    dropped.
    """

    tools_by_server = {result.server: result for result in server_tools_list}
    configured_names = {server.name for server in servers}
    calls_by_server = {} if window is None else window.server_tool_counts
    usage_status = "unsupported" if window is None else "measured"

    rows: list[ServerRow] = []
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
        calls = None
        if usage_status == "measured":
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
                usage_status=usage_status,
                called_tools=called_tools,
                verdict=_verdict(calls, result.status, usage_status),
            )
        )

    if window is not None:
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
                    usage_status="measured",
                    called_tools=called_tools,
                    verdict=_verdict(calls, "unsupported", "measured"),
                )
            )

    severity = {"prune": 0, "review": 1, "keep": 2, "unknown": 3}
    rows.sort(
        key=lambda row: (
            severity.get(row.verdict, len(severity)),
            -(row.def_tokens.tokens if row.def_tokens is not None else 0),
            row.server,
        )
    )
    return CliReport(
        cli=cli_name,
        rows=rows,
        window=window,
        coverage=build_coverage(
            sessions,
            window,
            server_tools_list,
            config_warnings,
        ),
    )


def build_report(cli_inputs: list[CliReportInput]) -> Report:
    """Build a report from already separated per-CLI inputs."""

    return Report(
        clis=[
            build_cli_report(
                item.cli_name,
                item.servers,
                item.server_tools_list,
                item.sessions,
                item.window,
                item.config_warnings,
            )
            for item in cli_inputs
        ],
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
        exact=bool(estimates) and all(estimate.exact for estimate in estimates),
    )


def _verdict(calls: int | None, def_status: str, usage_status: str) -> str:
    if usage_status == "unsupported":
        return "unknown"
    if calls is None:
        return "unknown"
    if calls == 0:
        return "prune" if def_status == "ok" else "review"
    if calls <= REVIEW_THRESHOLD:
        return "review"
    return "keep"
