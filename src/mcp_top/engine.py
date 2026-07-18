"""Join configured servers, definitions, and transcript calls into rankings."""

from __future__ import annotations

import math
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
class RecordedResultFootprint:
    """Lower-bound stats for recorded result bytes.

    Percentiles use the nearest-rank method over per-result token estimates:
    sort values ascending and take ceil(k / 100 * n), using one-based ranks.
    """

    results: int
    lower_bound: bool
    basis: str
    token_estimate: str
    total_bytes: int
    total_tokens: TokenCount
    max_tokens: TokenCount
    p50_tokens: TokenCount
    p90_tokens: TokenCount


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
    recorded_result_footprint: RecordedResultFootprint | None = None


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
    Zero measured calls are ``prune`` only when definition cost was actually
    observed (estimated from the queried tool definitions); otherwise they
    are ``review``. One through ``REVIEW_THRESHOLD`` (3) measured calls are
    ``review``, and more than 3 measured calls is ``keep``.
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
                recorded_result_footprint=_recorded_result_footprint(
                    window, server.name
                ),
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
                    recorded_result_footprint=_recorded_result_footprint(
                        window, server_name
                    ),
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
    suggestions = _prune_suggestions(rows, servers, config_warnings, window)
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
    window: UsageWindow | None,
) -> list[PruneSuggestion]:
    """Classify prune-verdict rows into clean suggestions and review candidates.

    A clean ``suggestion`` requires complete provenance, no enabled
    lower-precedence entry that would reactivate on deletion, and no
    attribution caveat. Everything else is a ``candidate`` whose net effect is
    unknown.
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
        if cfg.reserved:
            # Claude Code never loads a reserved-name server; a removal
            # recipe for it would be phantom advice.
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

        if window is not None and window.project_filter is not None:
            calls_by_project = window.server_calls_by_project.get(
                row.server, {}
            )
            project_filter = window.project_filter
            other_project = sum(
                count
                for project_key, count in calls_by_project.items()
                if (
                    project_key != project_filter
                    and project_key != "(unattributed)"
                )
            )
            unattributed = calls_by_project.get("(unattributed)", 0)
            if cfg.scope == "user" and other_project > 0:
                reasons.append(
                    f"server has {other_project} call(s) in other project(s); "
                    "removing the user-scope entry may affect them, and "
                    "name-only usage cannot prove they used this entry rather "
                    "than a local override -- verify before removing"
                )
            if unattributed > 0:
                reasons.append(
                    f"{unattributed} call(s) could not be attributed to a "
                    "project; verify before removing"
                )
            if window.sessions_unattributed > 0:
                reasons.append(
                    f"the recent window has {window.sessions_unattributed} "
                    "session(s) with no recorded project, which may belong to "
                    "this project; cannot confirm this server is unused here "
                    "-- verify before removing"
                )
            if window.unattributed_mcp_calls > 0:
                reasons.append(
                    f"{window.unattributed_mcp_calls} MCP call(s) in the "
                    "window could not be attributed to a server and may be "
                    "this one; verify before removing"
                )
        elif cfg.scope != "user":
            reasons.append(
                "usage is counted across all projects (home-wide), not "
                "per-project; pass --project-usage to scope counts to this "
                "project before removing"
            )

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
    """Return one disjoint advertised-maximum/upfront-floor token range.

    ``advertised_max`` is the v0.3 whole-definition estimator summed over
    every tool's full JSON (as the client would eventually load it), plus
    server instructions -- the only component that never appears inside a
    tool's own JSON. ``upfront_floor`` covers exactly what an upfront-loaded
    client sees before any deferred schema loads: for a tool whose full
    definition already loads upfront (server regime ``upfront``, or a
    per-tool always-load marker), its whole-definition estimate is reused
    (it already covers the bare name, so counting the name again would
    double it); for a still-deferred tool, only its bare, client-visible
    name loads upfront. Each component is counted exactly once in each
    total, which keeps ``upfront_floor <= advertised_max`` provable rather
    than incidental.
    """

    if result.status != "ok":
        return None, None

    text_limit = _CLAUDE_TEXT_LIMIT_BYTES if cli_name == "claude-code" else None
    floor_components: list[TokenCount] = []
    max_components: list[TokenCount] = []

    if result.instructions:
        instructions_estimate = estimate_text(
            _truncate_text(result.instructions, text_limit)
        )
        floor_components.append(instructions_estimate)
        max_components.append(instructions_estimate)

    for tool in result.tools:
        raw_name = tool.get("name")
        canonical_name = (
            _canonical_tool_name(raw_name, server.name, cli_name)
            if isinstance(raw_name, str)
            else None
        )
        whole_definition = _truncated_definition(tool, canonical_name, text_limit)
        whole_estimate = estimate_tool_definition(whole_definition)
        max_components.append(whole_estimate)

        if server.loading_regime == "upfront" or _is_always_loaded_tool(tool):
            floor_components.append(whole_estimate)
        elif canonical_name is not None:
            floor_components.append(estimate_text(canonical_name))

    upfront_floor = _sum_token_counts(floor_components)
    advertised_max = _sum_token_counts(max_components)
    _enforce_token_range(advertised_max, upfront_floor)
    return advertised_max, upfront_floor


def _canonical_tool_name(tool_name: str, server_name: str, cli_name: str) -> str:
    """Return the name the client actually shows/consumes tokens for.

    Claude Code exposes MCP tools under a client-visible
    ``mcp__<server>__<tool>`` name (https://code.claude.com/docs/en/
    agent-sdk/mcp); other CLIs are not documented to rename tools, so the
    raw name is used as-is.
    """

    if cli_name == "claude-code":
        return f"mcp__{server_name}__{tool_name}"
    return tool_name


def _truncated_definition(
    tool: dict, canonical_name: str | None, text_limit: int | None
) -> dict:
    """Return the whole tool definition with its client-visible name and
    Claude's documented 2KB description truncation applied."""

    definition = dict(tool)
    if canonical_name is not None:
        definition["name"] = canonical_name
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


def _recorded_result_footprint(
    window: UsageWindow | None, server: str
) -> RecordedResultFootprint | None:
    if window is None:
        return None
    byte_counts = window.server_result_bytes.get(server, [])
    if not byte_counts:
        return None
    token_values = sorted(_estimate_recorded_bytes(value).tokens for value in byte_counts)
    return RecordedResultFootprint(
        results=len(byte_counts),
        lower_bound=True,
        basis="recorded_utf8_bytes",
        token_estimate="bytes/4",
        total_bytes=sum(byte_counts),
        total_tokens=_estimate_recorded_bytes(sum(byte_counts)),
        max_tokens=TokenCount(tokens=max(token_values), exact=False),
        p50_tokens=TokenCount(tokens=_nearest_rank(token_values, 50), exact=False),
        p90_tokens=TokenCount(tokens=_nearest_rank(token_values, 90), exact=False),
    )


def _estimate_recorded_bytes(byte_count: int) -> TokenCount:
    return TokenCount(tokens=math.ceil(byte_count / 4), exact=False)


def _nearest_rank(sorted_values: list[int], percentile: int) -> int:
    rank = math.ceil(percentile / 100 * len(sorted_values))
    return sorted_values[max(rank - 1, 0)]


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
