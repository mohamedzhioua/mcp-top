"""Redacted aggregate snapshots and semantic diffs.

Snapshots omit config env/args/command/url values, filesystem paths, error
strings, regime evidence, prune recipes, and recorded result content. Server
names and tool names are retained as identifiers unless the snapshot command is
run with ``--redact-identifiers``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mcp_top.engine import CliReport, RecordedResultFootprint, Report, ServerRow
from mcp_top.tokens import TokenCount


SNAPSHOT_SCHEMA = "mcp-top-snapshot/v1"
DIFF_SCHEMA = "mcp-top-diff/v1"


@dataclass(frozen=True)
class SnapshotToken:
    value: int
    exact: bool


@dataclass(frozen=True)
class SnapshotRecordedResults:
    servers_queried_ok: int
    servers_unsupported: int
    paired: int
    partial: int
    unsupported: int
    unmeasurable: int
    unpaired_results: int
    unpaired_calls: int


@dataclass(frozen=True)
class SnapshotWindow:
    sessions: int
    days: int
    sessions_considered: int | None
    project_filter_active: bool


@dataclass(frozen=True)
class SnapshotFootprint:
    results: int
    total_bytes: int
    total_tokens: SnapshotToken
    max_tokens: SnapshotToken
    p50_tokens: SnapshotToken
    p90_tokens: SnapshotToken
    lower_bound: bool
    basis: str
    token_estimate: str


@dataclass(frozen=True)
class SnapshotServer:
    server: str
    scope: str
    transport: str
    loading_regime: str
    advertised_max_tokens: SnapshotToken | None
    upfront_floor_tokens: SnapshotToken | None
    tool_count: int | None
    calls: int | None
    usage_status: str
    verdict: str
    called_tools: dict[str, int]
    recorded_result_footprint: SnapshotFootprint | None


@dataclass(frozen=True)
class SnapshotCli:
    cli: str
    window: SnapshotWindow
    recorded_results: SnapshotRecordedResults | None
    servers: list[SnapshotServer]


@dataclass(frozen=True)
class Snapshot:
    schema: str
    generated_at: str
    identifiers_redacted: bool
    clis: list[SnapshotCli]


class SnapshotLoadError(Exception):
    """A user-facing diff input error."""


def build_snapshot(
    report: Report,
    generated_at: datetime,
    *,
    default_sessions: int = 0,
    default_days: int = 0,
    redact_identifiers: bool = False,
) -> Snapshot:
    """Build a redacted snapshot from already-safe report objects."""

    timestamp = generated_at.astimezone(timezone.utc).isoformat()
    return Snapshot(
        schema=SNAPSHOT_SCHEMA,
        generated_at=timestamp,
        identifiers_redacted=redact_identifiers,
        clis=[
            _snapshot_cli(
                cli_report,
                default_sessions,
                default_days,
                redact_identifiers,
            )
            for cli_report in sorted(report.clis, key=lambda item: item.cli)
        ],
    )


def snapshot_to_dict(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "schema": snapshot.schema,
        "generated_at": snapshot.generated_at,
        "identifiers_redacted": snapshot.identifiers_redacted,
        "clis": [
            {
                "cli": cli.cli,
                "window": {
                    "sessions": cli.window.sessions,
                    "days": cli.window.days,
                    "sessions_considered": cli.window.sessions_considered,
                    "project_filter_active": cli.window.project_filter_active,
                },
                "recorded_results": _recorded_results_dict(
                    cli.recorded_results
                ),
                "servers": [
                    _snapshot_server_dict(server) for server in cli.servers
                ],
            }
            for cli in snapshot.clis
        ],
    }


def load_snapshot_file(path: str) -> Snapshot:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as err:
        raise SnapshotLoadError(f"{path}: file not found") from err
    except json.JSONDecodeError as err:
        raise SnapshotLoadError(f"{path}: invalid JSON") from err
    except UnicodeDecodeError as err:
        raise SnapshotLoadError(f"{path}: invalid UTF-8") from err
    except RecursionError as err:
        raise SnapshotLoadError(f"{path}: invalid JSON") from err
    except OSError as err:
        raise SnapshotLoadError(f"{path}: {err.strerror or err}") from err
    if not isinstance(payload, dict):
        raise SnapshotLoadError(f"{path}: snapshot must be a JSON object")
    return _load_snapshot_payload(payload, path)


def diff_snapshots(old: Snapshot, new: Snapshot) -> dict[str, Any]:
    old_clis = _clis_by_name(old)
    new_clis = _clis_by_name(new)
    cli_names = sorted(set(old_clis) | set(new_clis))
    return {
        "schema": DIFF_SCHEMA,
        "old_generated_at": old.generated_at,
        "new_generated_at": new.generated_at,
        "clis": [
            _diff_cli(name, old_clis.get(name), new_clis.get(name))
            for name in cli_names
        ],
    }


def render_diff_human(diff: dict[str, Any]) -> str:
    lines = [
        "Snapshot diff",
        f"old generated_at: {_ascii(str(diff.get('old_generated_at')))}",
        f"new generated_at: {_ascii(str(diff.get('new_generated_at')))}",
    ]
    for cli_diff in diff["clis"]:
        lines.append("")
        lines.append(f"=== {_ascii(cli_diff['cli'])} ===")
        if cli_diff["added"]:
            lines.append("  added: " + ", ".join(map(_ascii, cli_diff["added"])))
        if cli_diff["removed"]:
            lines.append(
                "  removed: " + ", ".join(map(_ascii, cli_diff["removed"]))
            )
        if cli_diff.get("window") is not None:
            lines.append(
                f"  window: {_ascii(_format_window_delta(cli_diff['window']))}"
            )
        if cli_diff.get("sessions_considered") is not None:
            lines.append(
                "  sessions_considered: "
                + _ascii(_format_delta(cli_diff["sessions_considered"]))
            )
        if cli_diff["changed"]:
            lines.append("  changed:")
            for changed in cli_diff["changed"]:
                lines.append(f"    - {_ascii(changed['server'])}")
                for field, delta in changed.items():
                    if field == "server":
                        continue
                    lines.append(
                        f"      {field}: {_ascii(_format_delta(delta))}"
                    )
        lines.append(f"  unchanged: {cli_diff['unchanged']} server(s)")
    return "\n".join(lines)


def _snapshot_cli(
    cli_report: CliReport,
    default_sessions: int,
    default_days: int,
    redact_identifiers: bool,
) -> SnapshotCli:
    window = cli_report.window
    if window is None:
        snapshot_window = SnapshotWindow(
            sessions=default_sessions,
            days=default_days,
            sessions_considered=None,
            project_filter_active=cli_report.coverage.project_filter_active,
        )
    else:
        snapshot_window = SnapshotWindow(
            sessions=window.window_sessions,
            days=window.window_days,
            sessions_considered=window.sessions_considered,
            project_filter_active=window.project_filter is not None,
        )
    recorded_results = None
    if cli_report.coverage.recorded_results is not None:
        coverage = cli_report.coverage.recorded_results
        recorded_results = SnapshotRecordedResults(
            servers_queried_ok=cli_report.coverage.servers_queried_ok,
            servers_unsupported=cli_report.coverage.servers_unsupported,
            paired=coverage.paired,
            partial=coverage.partial,
            unsupported=coverage.unsupported,
            unmeasurable=coverage.unmeasurable,
            unpaired_results=coverage.unpaired_results,
            unpaired_calls=coverage.unpaired_calls,
        )
    return SnapshotCli(
        cli=cli_report.cli,
        window=snapshot_window,
        recorded_results=recorded_results,
        servers=[
            _snapshot_server(row, redact_identifiers)
            for row in sorted(cli_report.rows, key=lambda item: item.server)
        ],
    )


def _snapshot_server(
    row: ServerRow,
    redact_identifiers: bool,
) -> SnapshotServer:
    called_tools: dict[str, int] = {}
    for tool, count in sorted(row.called_tools.items()):
        name = _identifier(tool, "tool", redact_identifiers)
        called_tools[name] = called_tools.get(name, 0) + count
    return SnapshotServer(
        server=_identifier(row.server, "srv", redact_identifiers),
        scope=row.scope,
        transport=row.transport,
        loading_regime=row.loading_regime,
        advertised_max_tokens=_snapshot_token(row.advertised_max_tokens),
        upfront_floor_tokens=_snapshot_token(row.upfront_floor_tokens),
        tool_count=row.tool_count,
        calls=row.calls,
        usage_status=row.usage_status,
        verdict=row.verdict,
        called_tools=dict(sorted(called_tools.items())),
        recorded_result_footprint=_snapshot_footprint(
            row.recorded_result_footprint
        ),
    )


def _snapshot_token(tokens: TokenCount | None) -> SnapshotToken | None:
    if tokens is None:
        return None
    return SnapshotToken(value=tokens.tokens, exact=tokens.exact)


def _snapshot_footprint(
    footprint: RecordedResultFootprint | None,
) -> SnapshotFootprint | None:
    if footprint is None:
        return None
    return SnapshotFootprint(
        results=footprint.results,
        total_bytes=footprint.total_bytes,
        total_tokens=_snapshot_token(footprint.total_tokens),
        max_tokens=_snapshot_token(footprint.max_tokens),
        p50_tokens=_snapshot_token(footprint.p50_tokens),
        p90_tokens=_snapshot_token(footprint.p90_tokens),
        lower_bound=footprint.lower_bound,
        basis=footprint.basis,
        token_estimate=footprint.token_estimate,
    )


def _recorded_results_dict(
    recorded_results: SnapshotRecordedResults | None,
) -> dict[str, int] | None:
    if recorded_results is None:
        return None
    return {
        "servers_queried_ok": recorded_results.servers_queried_ok,
        "servers_unsupported": recorded_results.servers_unsupported,
        "paired": recorded_results.paired,
        "partial": recorded_results.partial,
        "unsupported": recorded_results.unsupported,
        "unmeasurable": recorded_results.unmeasurable,
        "unpaired_results": recorded_results.unpaired_results,
        "unpaired_calls": recorded_results.unpaired_calls,
    }


def _snapshot_server_dict(server: SnapshotServer) -> dict[str, Any]:
    return {
        "server": server.server,
        "scope": server.scope,
        "transport": server.transport,
        "loading_regime": server.loading_regime,
        "advertised_max_tokens": _token_dict(server.advertised_max_tokens),
        "upfront_floor_tokens": _token_dict(server.upfront_floor_tokens),
        "tool_count": server.tool_count,
        "calls": server.calls,
        "usage_status": server.usage_status,
        "verdict": server.verdict,
        "called_tools": server.called_tools,
        "recorded_result_footprint": _footprint_dict(
            server.recorded_result_footprint
        ),
    }


def _token_dict(token: SnapshotToken | None) -> dict[str, Any] | None:
    if token is None:
        return None
    return {"value": token.value, "exact": token.exact}


def _footprint_dict(
    footprint: SnapshotFootprint | None,
) -> dict[str, Any] | None:
    if footprint is None:
        return None
    return {
        "results": footprint.results,
        "total_bytes": footprint.total_bytes,
        "total_tokens": _token_dict(footprint.total_tokens),
        "max_tokens": _token_dict(footprint.max_tokens),
        "p50_tokens": _token_dict(footprint.p50_tokens),
        "p90_tokens": _token_dict(footprint.p90_tokens),
        "lower_bound": footprint.lower_bound,
        "basis": footprint.basis,
        "token_estimate": footprint.token_estimate,
    }


def _load_snapshot_payload(payload: dict[str, Any], path: str) -> Snapshot:
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise SnapshotLoadError(
            f"{path}: schema must be {SNAPSHOT_SCHEMA}"
        )
    _require_keys(
        payload,
        path,
        "snapshot",
        {"schema", "generated_at", "identifiers_redacted", "clis"},
    )
    return Snapshot(
        schema=SNAPSHOT_SCHEMA,
        generated_at=_require_nonempty_str(
            payload["generated_at"], path, "generated_at"
        ),
        identifiers_redacted=_require_bool(
            payload["identifiers_redacted"], path, "identifiers_redacted"
        ),
        clis=_load_clis(payload["clis"], path),
    )


def _load_clis(value: Any, path: str) -> list[SnapshotCli]:
    if not isinstance(value, list):
        _invalid(path, "clis", "must be a list")
    clis = [
        _load_cli(item, path, f"clis[{index}]")
        for index, item in enumerate(value)
    ]
    names: set[str] = set()
    for cli in clis:
        if cli.cli in names:
            _invalid(path, "clis", "has duplicate cli name")
        names.add(cli.cli)
    return clis


def _load_cli(value: Any, path: str, field: str) -> SnapshotCli:
    _require_keys(
        value, path, field, {"cli", "window", "recorded_results", "servers"}
    )
    cli = SnapshotCli(
        cli=_require_str(value["cli"], path, f"{field}.cli"),
        window=_load_window(value["window"], path, f"{field}.window"),
        recorded_results=_load_recorded_results(
            value["recorded_results"], path, f"{field}.recorded_results"
        ),
        servers=_load_servers(value["servers"], path, f"{field}.servers"),
    )
    return cli


def _load_window(value: Any, path: str, field: str) -> SnapshotWindow:
    _require_keys(
        value,
        path,
        field,
        {"sessions", "days", "sessions_considered", "project_filter_active"},
    )
    return SnapshotWindow(
        sessions=_require_int(value["sessions"], path, f"{field}.sessions", minimum=1),
        days=_require_int(value["days"], path, f"{field}.days", minimum=1),
        sessions_considered=_require_nullable_int(
            value["sessions_considered"],
            path,
            f"{field}.sessions_considered",
            minimum=0,
        ),
        project_filter_active=_require_bool(
            value["project_filter_active"], path, f"{field}.project_filter_active"
        ),
    )


def _load_recorded_results(
    value: Any,
    path: str,
    field: str,
) -> SnapshotRecordedResults | None:
    if value is None:
        return None
    keys = {
        "servers_queried_ok",
        "servers_unsupported",
        "paired",
        "partial",
        "unsupported",
        "unmeasurable",
        "unpaired_results",
        "unpaired_calls",
    }
    _require_keys(value, path, field, keys)
    return SnapshotRecordedResults(
        servers_queried_ok=_require_int(
            value["servers_queried_ok"],
            path,
            f"{field}.servers_queried_ok",
            minimum=0,
        ),
        servers_unsupported=_require_int(
            value["servers_unsupported"],
            path,
            f"{field}.servers_unsupported",
            minimum=0,
        ),
        paired=_require_int(value["paired"], path, f"{field}.paired", minimum=0),
        partial=_require_int(value["partial"], path, f"{field}.partial", minimum=0),
        unsupported=_require_int(
            value["unsupported"], path, f"{field}.unsupported", minimum=0
        ),
        unmeasurable=_require_int(
            value["unmeasurable"], path, f"{field}.unmeasurable", minimum=0
        ),
        unpaired_results=_require_int(
            value["unpaired_results"],
            path,
            f"{field}.unpaired_results",
            minimum=0,
        ),
        unpaired_calls=_require_int(
            value["unpaired_calls"], path, f"{field}.unpaired_calls", minimum=0
        ),
    )


def _load_servers(value: Any, path: str, field: str) -> list[SnapshotServer]:
    if not isinstance(value, list):
        _invalid(path, field, "must be a list")
    servers = [
        _load_server(item, path, f"{field}[{index}]")
        for index, item in enumerate(value)
    ]
    names: set[str] = set()
    for server in servers:
        if server.server in names:
            _invalid(path, field, "has duplicate server name")
        names.add(server.server)
    return servers


def _load_server(value: Any, path: str, field: str) -> SnapshotServer:
    _require_keys(
        value,
        path,
        field,
        {
            "server",
            "scope",
            "transport",
            "loading_regime",
            "advertised_max_tokens",
            "upfront_floor_tokens",
            "tool_count",
            "calls",
            "usage_status",
            "verdict",
            "called_tools",
            "recorded_result_footprint",
        },
    )
    return SnapshotServer(
        server=_require_str(value["server"], path, f"{field}.server"),
        scope=_require_str(value["scope"], path, f"{field}.scope"),
        transport=_require_str(value["transport"], path, f"{field}.transport"),
        loading_regime=_require_str(
            value["loading_regime"], path, f"{field}.loading_regime"
        ),
        advertised_max_tokens=_load_token(
            value["advertised_max_tokens"], path, f"{field}.advertised_max_tokens"
        ),
        upfront_floor_tokens=_load_token(
            value["upfront_floor_tokens"], path, f"{field}.upfront_floor_tokens"
        ),
        tool_count=_require_nullable_int(
            value["tool_count"], path, f"{field}.tool_count", minimum=0
        ),
        calls=_require_nullable_int(value["calls"], path, f"{field}.calls", minimum=0),
        usage_status=_require_str(
            value["usage_status"], path, f"{field}.usage_status"
        ),
        verdict=_require_str(value["verdict"], path, f"{field}.verdict"),
        called_tools=_load_called_tools(
            value["called_tools"], path, f"{field}.called_tools"
        ),
        recorded_result_footprint=_load_footprint(
            value["recorded_result_footprint"],
            path,
            f"{field}.recorded_result_footprint",
        ),
    )


def _load_token(value: Any, path: str, field: str) -> SnapshotToken | None:
    if value is None:
        return None
    _require_keys(value, path, field, {"value", "exact"})
    return SnapshotToken(
        value=_require_int(value["value"], path, f"{field}.value", minimum=0),
        exact=_require_bool(value["exact"], path, f"{field}.exact"),
    )


def _load_called_tools(value: Any, path: str, field: str) -> dict[str, int]:
    if not isinstance(value, dict):
        _invalid(path, field, "must be an object")
    called_tools: dict[str, int] = {}
    for tool, count in value.items():
        if not isinstance(tool, str):
            _invalid(path, field, "keys must be strings")
        called_tools[tool] = _require_int(count, path, f"{field}.*", minimum=0)
    return called_tools


def _load_footprint(value: Any, path: str, field: str) -> SnapshotFootprint | None:
    if value is None:
        return None
    _require_keys(
        value,
        path,
        field,
        {
            "results",
            "total_bytes",
            "total_tokens",
            "max_tokens",
            "p50_tokens",
            "p90_tokens",
            "lower_bound",
            "basis",
            "token_estimate",
        },
    )
    total_tokens = _load_token(value["total_tokens"], path, f"{field}.total_tokens")
    max_tokens = _load_token(value["max_tokens"], path, f"{field}.max_tokens")
    p50_tokens = _load_token(value["p50_tokens"], path, f"{field}.p50_tokens")
    p90_tokens = _load_token(value["p90_tokens"], path, f"{field}.p90_tokens")
    if (
        total_tokens is None
        or max_tokens is None
        or p50_tokens is None
        or p90_tokens is None
    ):
        _invalid(path, field, "token fields must be objects")
    return SnapshotFootprint(
        results=_require_int(value["results"], path, f"{field}.results", minimum=0),
        total_bytes=_require_int(
            value["total_bytes"], path, f"{field}.total_bytes", minimum=0
        ),
        total_tokens=total_tokens,
        max_tokens=max_tokens,
        p50_tokens=p50_tokens,
        p90_tokens=p90_tokens,
        lower_bound=_require_bool(value["lower_bound"], path, f"{field}.lower_bound"),
        basis=_require_str(value["basis"], path, f"{field}.basis"),
        token_estimate=_require_str(
            value["token_estimate"], path, f"{field}.token_estimate"
        ),
    )


def _require_keys(
    value: Any,
    path: str,
    field: str,
    expected: set[str],
) -> None:
    if not isinstance(value, dict):
        _invalid(path, field, "must be an object")
    actual = set(value)
    missing = sorted(expected - actual)
    if missing:
        _invalid(path, f"{field}.{missing[0]}", "is required")
    unknown = sorted(actual - expected)
    if unknown:
        _invalid(path, f"{field}.{unknown[0]}", "is not allowed")


def _require_nonempty_str(value: Any, path: str, field: str) -> str:
    text = _require_str(value, path, field)
    if text == "":
        _invalid(path, field, "must be nonempty")
    return text


def _require_str(value: Any, path: str, field: str) -> str:
    if not isinstance(value, str):
        _invalid(path, field, "must be a string")
    return value


def _require_bool(value: Any, path: str, field: str) -> bool:
    if type(value) is not bool:
        _invalid(path, field, "must be a boolean")
    return value


def _require_nullable_int(
    value: Any,
    path: str,
    field: str,
    *,
    minimum: int,
) -> int | None:
    if value is None:
        return None
    return _require_int(value, path, field, minimum=minimum)


def _require_int(value: Any, path: str, field: str, *, minimum: int) -> int:
    if type(value) is not int:
        _invalid(path, field, "must be an integer")
    if value < minimum:
        _invalid(path, field, "is out of range")
    return value


def _invalid(path: str, field: str, reason: str) -> None:
    raise SnapshotLoadError(f"{path}: {field} {reason}")


def _identifier(name: str, prefix: str, redact: bool) -> str:
    if not redact:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _clis_by_name(snapshot: Snapshot) -> dict[str, SnapshotCli]:
    return {cli.cli: cli for cli in snapshot.clis}


def _servers_by_name(cli: SnapshotCli | None) -> dict[str, SnapshotServer]:
    if cli is None:
        return {}
    return {server.server: server for server in cli.servers}


def _diff_cli(
    cli_name: str,
    old_cli: SnapshotCli | None,
    new_cli: SnapshotCli | None,
) -> dict[str, Any]:
    old_servers = _servers_by_name(old_cli)
    new_servers = _servers_by_name(new_cli)
    old_names = set(old_servers)
    new_names = set(new_servers)
    surviving = sorted(old_names & new_names)
    changed = []
    unchanged = 0
    population_differs = _population_differs(old_cli, new_cli)
    for server_name in surviving:
        server_delta = _diff_server(
            server_name,
            old_servers[server_name],
            new_servers[server_name],
            population_differs,
        )
        if len(server_delta) == 1:
            unchanged += 1
        else:
            changed.append(server_delta)
    return {
        "cli": cli_name,
        "added": sorted(new_names - old_names),
        "removed": sorted(old_names - new_names),
        "window": _window_delta(old_cli, new_cli),
        "sessions_considered": _sessions_considered_delta(old_cli, new_cli),
        "changed": changed,
        "unchanged": unchanged,
    }


def _diff_server(
    server_name: str,
    old_server: SnapshotServer,
    new_server: SnapshotServer,
    population_differs: bool,
) -> dict[str, Any]:
    changed: dict[str, Any] = {"server": server_name}
    for field in ("advertised_max_tokens", "upfront_floor_tokens"):
        delta = _token_delta(getattr(old_server, field), getattr(new_server, field))
        if delta is not None:
            changed[field] = delta
    if population_differs:
        calls_delta = _incomparable_delta(old_server.calls, new_server.calls)
    else:
        calls_delta = _int_delta(old_server.calls, new_server.calls)
    if calls_delta is not None:
        changed["calls"] = calls_delta
    if population_differs:
        result_delta = _incomparable_delta(
            _token_dict(_footprint_total_tokens(old_server)),
            _token_dict(_footprint_total_tokens(new_server)),
        )
    else:
        result_delta = _token_delta(
            _footprint_total_tokens(old_server),
            _footprint_total_tokens(new_server),
        )
    if result_delta is not None:
        changed["recorded_result_total_tokens"] = result_delta
    for field in ("loading_regime", "verdict"):
        delta = _value_delta(getattr(old_server, field), getattr(new_server, field))
        if delta is not None:
            changed[field] = delta
    return changed


def _population_differs(
    old_cli: SnapshotCli | None,
    new_cli: SnapshotCli | None,
) -> bool:
    if old_cli is None or new_cli is None:
        return False
    old = old_cli.window
    new = new_cli.window
    if old.sessions != new.sessions or old.days != new.days:
        return True
    if old.project_filter_active != new.project_filter_active:
        return True
    return old.project_filter_active and new.project_filter_active


def _window_delta(
    old_cli: SnapshotCli | None,
    new_cli: SnapshotCli | None,
) -> dict[str, Any] | None:
    if old_cli is None or new_cli is None:
        return None
    old = old_cli.window
    new = new_cli.window
    old_value = _window_settings_dict(old)
    new_value = _window_settings_dict(new)
    if old_value == new_value:
        return None
    return {"old": old_value, "new": new_value}


def _window_settings_dict(window: SnapshotWindow) -> dict[str, Any]:
    return {
        "sessions": window.sessions,
        "days": window.days,
        "project_filter_active": window.project_filter_active,
    }


def _sessions_considered_delta(
    old_cli: SnapshotCli | None,
    new_cli: SnapshotCli | None,
) -> dict[str, Any] | None:
    if old_cli is None or new_cli is None:
        return None
    return _int_delta(
        old_cli.window.sessions_considered,
        new_cli.window.sessions_considered,
    )


def _footprint_total_tokens(server: SnapshotServer) -> SnapshotToken | None:
    if server.recorded_result_footprint is None:
        return None
    return server.recorded_result_footprint.total_tokens


def _token_delta(
    old: SnapshotToken | None,
    new: SnapshotToken | None,
) -> dict[str, Any] | None:
    if old == new:
        return None
    old_value = None if old is None else old.value
    new_value = None if new is None else new.value
    delta = None
    if old_value is not None and new_value is not None:
        delta = new_value - old_value
    return {
        "old": _token_dict(old),
        "new": _token_dict(new),
        "delta": delta,
        "estimated": (old is not None and not old.exact)
        or (new is not None and not new.exact),
    }


def _incomparable_delta(old: Any, new: Any) -> dict[str, Any] | None:
    if old == new:
        return None
    return {
        "old": old,
        "new": new,
        "incomparable": True,
        "reason": "population/scope differs",
    }


def _int_delta(old: Any, new: Any) -> dict[str, Any] | None:
    if old == new:
        return None
    delta = new - old if isinstance(old, int) and isinstance(new, int) else None
    return {"old": old, "new": new, "delta": delta}


def _value_delta(old: Any, new: Any) -> dict[str, Any] | None:
    if old == new:
        return None
    return {"old": old, "new": new}


def _format_delta(delta: dict[str, Any]) -> str:
    old = delta.get("old")
    new = delta.get("new")
    if delta.get("incomparable"):
        return (
            f"{_format_delta_value(old)} -> {_format_delta_value(new)} "
            f"({_ascii(str(delta.get('reason', 'incomparable')))})"
        )
    if isinstance(old, dict) or isinstance(new, dict):
        suffix = ""
        if delta.get("delta") is not None:
            suffix = f" ({_signed(delta['delta'])}"
            if delta.get("estimated"):
                suffix += ", estimate"
            suffix += ")"
        return f"{_format_token(old)} -> {_format_token(new)}{suffix}"
    if "delta" in delta:
        suffix = ""
        if delta.get("delta") is not None:
            suffix = f" ({_signed(delta['delta'])})"
        return f"{old} -> {new}{suffix}"
    return f"{old} -> {new}"


def _format_window_delta(delta: dict[str, Any]) -> str:
    return f"{_format_window(delta['old'])} -> {_format_window(delta['new'])}"


def _format_window(window: dict[str, Any]) -> str:
    scope = "project-filtered" if window["project_filter_active"] else "home-wide"
    return f"{window['sessions']} session(s), {window['days']} day(s), {scope}"


def _format_delta_value(value: Any) -> str:
    if isinstance(value, dict) or value is None:
        return _format_token(value)
    return str(value)


def _format_token(token: Any) -> str:
    if token is None:
        return "null"
    if not isinstance(token, dict):
        return str(token)
    value = token.get("value")
    prefix = "" if token.get("exact") else "~"
    return f"{prefix}{value}"


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _ascii(text: str) -> str:
    return "".join(char if 32 <= ord(char) < 127 else "?" for char in text)
