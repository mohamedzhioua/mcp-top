"""Join configured servers, definitions, and transcript calls into rankings."""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp_top.config import ServerConfig
from mcp_top.counter import UsageWindow
from mcp_top.coverage import Coverage, build_coverage
from mcp_top.mcpclient import ServerTools
from mcp_top.tokens import (
    HEURISTIC,
    TokenCount,
    exact_count,
    estimate_text,
    estimate_tool_definition,
)
from mcp_top.transcripts import SessionResult


REVIEW_THRESHOLD = 3

# Claude documents: "Claude Code truncates tool descriptions and server
# instructions at 2KB each." Interpret 2KB as 2 * 1024 UTF-8 bytes.
# https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search
_CLAUDE_TEXT_LIMIT_BYTES = 2 * 1024


@dataclass
class ServerRow:
    server: str
    scope: str
    transport: str
    advertised_max_tokens: TokenCount | None
    upfront_floor_tokens: TokenCount | None
    loading_regime: str
    regime_evidence: list[str]
    def_status: str
    def_error: str | None
    tool_count: int | None
    calls: int | None
    usage_status: str
    called_tools: dict[str, int]
    verdict: str
    filtered_tools: int = 0


@dataclass
class Reactivation:
    """A lower-precedence server that would become active if a winner is removed."""

    server: str
    scope: str
    source_path: str


@dataclass
class PruneSuggestion:
    """A safe, serializable prune recommendation for one server.

    ``kind`` is ``"suggestion"`` only for a clean, global-scope removal. Both
    kinds carry the advertised maximum and upfront floor removed with the
    winning config entry; a ``"candidate"`` needs review because its net effect
    is unknown. This object deliberately carries no ``env``/``args`` -- it is
    derived from ``ServerConfig`` but never exposes its secrets.
    """

    kind: str
    server: str
    scope: str
    source_path: str
    removes_advertised_max_tokens: TokenCount
    removes_upfront_floor_tokens: TokenCount | None
    reactivates: Reactivation | None
    reasons: list[str]


@dataclass
class CliReport:
    cli: str
    rows: list[ServerRow]
    window: UsageWindow | None
    coverage: Coverage
    suggestions: list[PruneSuggestion] = field(default_factory=list)


@dataclass
class Report:
    clis: list[CliReport]
    generated_note: str
    note: str | None = None


@dataclass
class CliReportInput:
    """Input bundle for one CLI report."""

    cli_name: str
    servers: list[ServerConfig]
    server_tools_list: list[ServerTools]
    sessions: list[SessionResult]
    window: UsageWindow | None
    config_warnings: list[str] | None = None
    usage_note: str | None = None


def build_cli_report(
    cli_name: str,
    servers: list[ServerConfig],
    server_tools_list: list[ServerTools],
    sessions: list[SessionResult],
    window: UsageWindow | None,
    config_warnings: list[str] | None = None,
    usage_note: str | None = None,
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
    if window is None:
        usage_status = "unsupported"
    elif window.sessions_considered == 0:
        usage_status = "no-data"
    else:
        usage_status = "measured"

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
        advertised_max, upfront_floor = _context_token_range(
            result, server, cli_name
        )
        called_tools = calls_by_server.get(server.name, {})
        calls = None
        if usage_status == "measured":
            calls = sum(called_tools.values())
        rows.append(
            ServerRow(
                server=server.name,
                scope=server.scope,
                transport=server.transport,
                advertised_max_tokens=advertised_max,
                upfront_floor_tokens=upfront_floor,
                loading_regime=server.loading_regime,
                regime_evidence=list(server.regime_evidence),
                def_status=result.status,
                def_error=result.error,
                tool_count=len(result.tools) if result.status == "ok" else None,
                calls=calls,
                usage_status=usage_status,
                called_tools=called_tools,
                verdict=_verdict(calls, result.status, usage_status),
                filtered_tools=result.filtered_tools,
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
                    advertised_max_tokens=None,
                    upfront_floor_tokens=None,
                    loading_regime="unknown",
                    regime_evidence=[],
                    def_status="unsupported",
                    def_error="server is not configured",
                    tool_count=None,
                    calls=calls,
                    usage_status="measured",
                    called_tools=called_tools,
                    verdict=_verdict(calls, "unsupported", "measured"),
                    filtered_tools=0,
                )
            )

    severity = {"prune": 0, "review": 1, "keep": 2, "unknown": 3}
    rows.sort(
        key=lambda row: (
            severity.get(row.verdict, len(severity)),
            -(
                row.advertised_max_tokens.tokens
                if row.advertised_max_tokens is not None
                else 0
            ),
            row.server,
        )
    )
    suggestions = _prune_suggestions(rows, servers, config_warnings)
    return CliReport(
        cli=cli_name,
        rows=rows,
        window=window,
        coverage=build_coverage(
            sessions,
            window,
            server_tools_list,
            config_warnings,
            usage_note,
        ),
        suggestions=suggestions,
    )


def _prune_suggestions(
    rows: list[ServerRow],
    servers: list[ServerConfig],
    config_warnings: list[str] | None,
) -> list[PruneSuggestion]:
    """Classify prune-verdict rows into clean suggestions and review candidates.

    A clean ``suggestion`` requires a global (``user``) scope -- the only scope
    whose home-wide zero-call count is a valid denominator, since usage is not
    attributed per project in v0.3 -- with complete provenance and no enabled
    lower-precedence entry that would reactivate on deletion. Everything else is
    a ``candidate`` whose net effect is unknown.
    """

    config_by_name = {server.name: server for server in servers}
    # Match only the read/parse-failure warnings emitted by the config readers,
    # which always begin with these prefixes. A substring match would misfire on
    # a server literally named "could not parse".
    provenance_incomplete = any(
        warning.startswith(("could not parse", "could not read"))
        for warning in (config_warnings or [])
    )
    suggestions: list[PruneSuggestion] = []
    for row in rows:
        if row.verdict != "prune" or row.advertised_max_tokens is None:
            continue
        if row.advertised_max_tokens.tokens <= 0:
            # Zero-cost rows are not useful prune suggestions.
            continue
        cfg = config_by_name.get(row.server)
        if cfg is None:
            continue

        reasons: list[str] = []
        reactivation: Reactivation | None = None

        # An enabled immediate shadow reactivates on deletion; a disabled one
        # becomes the (uncosted) winner and blocks deeper layers, so it does
        # not reactivate anything.
        if cfg.shadowed:
            immediate = cfg.shadowed[0]
            if immediate.enabled is not False:
                reactivation = Reactivation(
                    server=immediate.name,
                    scope=immediate.scope,
                    source_path=immediate.source_path,
                )
                reasons.append(
                    f"deleting this reactivates {immediate.scope}-scope "
                    f"'{immediate.name}' from {immediate.source_path}; net "
                    "saving is unknown -- it may be lower, unchanged, or higher"
                )

        if cfg.scope != "user":
            reasons.append(
                "usage is not attributed per-project in v0.3, so 0 calls may "
                "reflect sessions from other projects; verify this server is "
                "unused in this project before removing"
            )

        if provenance_incomplete:
            reasons.append(
                "a config layer for this CLI could not be parsed, so a "
                "shadowing or shadowed entry may be missing; treat removal as "
                "unverified"
            )

        # A CLI resolver may attach its own reason a clean removal is unsafe
        # (e.g. a Codex project layer that could redefine this server in a
        # trusted project). It always forces a review candidate.
        if cfg.resolution_caveat:
            reasons.append(cfg.resolution_caveat)

        kind = "suggestion" if not reasons else "candidate"
        suggestions.append(
            PruneSuggestion(
                kind=kind,
                server=row.server,
                scope=cfg.scope,
                source_path=cfg.source_path,
                removes_advertised_max_tokens=row.advertised_max_tokens,
                removes_upfront_floor_tokens=row.upfront_floor_tokens,
                reactivates=reactivation,
                reasons=reasons,
            )
        )
    return suggestions


def build_report(
    cli_inputs: list[CliReportInput], note: str | None = None
) -> Report:
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
                item.usage_note,
            )
            for item in cli_inputs
        ],
        generated_note=(
            f"Definition token counts use the {HEURISTIC} heuristic; "
            "~ means estimate."
        ),
        note=note,
    )


def _context_token_range(
    result: ServerTools,
    server: ServerConfig,
    cli_name: str,
) -> tuple[TokenCount | None, TokenCount | None]:
    """Return one disjoint advertised-maximum/upfront-floor token range."""

    if result.status != "ok":
        return None, None

    text_limit = _CLAUDE_TEXT_LIMIT_BYTES if cli_name == "claude-code" else None
    upfront_components: list[TokenCount] = []
    deferred_components: list[TokenCount] = []
    names = [
        tool["name"]
        for tool in result.tools
        if isinstance(tool.get("name"), str)
    ]
    if names:
        upfront_components.append(estimate_text("\n".join(names)))
    if result.instructions:
        upfront_components.append(
            estimate_text(_truncate_text(result.instructions, text_limit))
        )

    for tool in result.tools:
        definition = _definition_without_name(tool, text_limit)
        estimate = estimate_tool_definition(definition)
        if server.loading_regime == "upfront" or _is_always_loaded_tool(tool):
            upfront_components.append(estimate)
        else:
            deferred_components.append(estimate)

    upfront_floor = _sum_token_counts(upfront_components)
    advertised_max = _sum_token_counts(
        [*upfront_components, *deferred_components]
    )
    _enforce_token_range(advertised_max, upfront_floor)
    return advertised_max, upfront_floor


def _definition_without_name(tool: dict, text_limit: int | None) -> dict:
    """Return the definition-only component, excluding its advertised name."""

    definition = dict(tool)
    if isinstance(tool.get("name"), str):
        definition.pop("name")
    description = definition.get("description")
    if isinstance(description, str):
        definition["description"] = _truncate_text(description, text_limit)
    return definition


def _truncate_text(text: str, byte_limit: int | None) -> str:
    if byte_limit is None:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _sum_token_counts(estimates: list[TokenCount]) -> TokenCount:
    if not estimates:
        return exact_count(0)
    return TokenCount(
        tokens=sum(estimate.tokens for estimate in estimates),
        exact=all(estimate.exact for estimate in estimates),
    )


def _enforce_token_range(
    advertised_max: TokenCount, upfront_floor: TokenCount
) -> None:
    if upfront_floor.tokens > advertised_max.tokens:
        raise AssertionError(
            "invalid token range: upfront floor exceeds advertised maximum"
        )


def _is_always_loaded_tool(tool: dict) -> bool:
    meta = tool.get("_meta")
    return (
        isinstance(meta, dict)
        and meta.get("anthropic/alwaysLoad") is True
    )


def _verdict(calls: int | None, def_status: str, usage_status: str) -> str:
    if usage_status in {"unsupported", "no-data"}:
        return "unknown"
    if calls is None:
        return "unknown"
    if calls == 0:
        return "prune" if def_status == "ok" else "review"
    if calls <= REVIEW_THRESHOLD:
        return "review"
    return "keep"
