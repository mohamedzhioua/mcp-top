"""Discover Claude Code MCP server configuration from local files.

The module is intentionally read-only: it enumerates known Claude Code config
scopes and never writes user configuration.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any


_MISSING = object()

# Relative precedence of the config scopes, low to high. Only the ordering
# matters: a higher number wins and shadows lower-numbered same-name entries.
# Claude Code layers user -> user-project -> project; Cursor user -> project;
# Codex reads user scope only (its project layer is a conditional inventory and
# is never merged into this map -- see clis/codex.py).
_SCOPE_PRECEDENCE = {"user": 0, "user-project": 1, "project": 2}


@dataclass
class ServerConfig:
    """A configured MCP server from the winning CLI config scope.

    ``shadowed`` holds the same-name lower-precedence entries this winner
    overrides, ordered highest precedence first. It exists so a prune
    simulation can tell whether deleting this winner would reactivate a
    lower-precedence server. It is never serialized: ``env`` and ``args`` can
    carry secrets.
    """

    name: str
    scope: str
    source_path: str
    transport: str
    command: str | None
    args: list[str]
    env: dict[str, str]
    url: str | None
    enabled: bool = True
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] | None = None
    cwd: str | None = None
    query_timeout: float | None = None
    precedence: int = 0
    shadowed: tuple["ServerConfig", ...] = ()
    resolution_caveat: str | None = None


def discover_servers(
    home: str, project_dir: str | None
) -> tuple[list[ServerConfig], list[str]]:
    """Return configured MCP servers and non-fatal warnings.

    Discovery reads Claude Code user, user-project, and project scopes in
    increasing precedence. Later scopes override earlier servers with the same
    name; only winning entries are returned.
    """

    warnings: list[str] = []
    servers: dict[str, ServerConfig] = {}

    user_path = os.path.join(home, ".claude.json")
    user_config = _read_json_object(user_path, warnings)
    if user_config is not None:
        _merge_servers(
            servers,
            _server_configs_from_mapping(
                user_config.get("mcpServers", _MISSING),
                "user",
                user_path,
                warnings,
            ),
            warnings,
        )

        if project_dir is not None:
            projects = user_config.get("projects")
            if isinstance(projects, dict):
                for configured_path, project_config in projects.items():
                    if not isinstance(configured_path, str):
                        continue
                    if not _same_config_path(configured_path, project_dir):
                        continue
                    if isinstance(project_config, dict):
                        _merge_servers(
                            servers,
                            _server_configs_from_mapping(
                                project_config.get("mcpServers", _MISSING),
                                "user-project",
                                user_path,
                                warnings,
                            ),
                            warnings,
                        )

    if project_dir is not None:
        project_path = os.path.join(project_dir, ".mcp.json")
        project_config = _read_json_object(project_path, warnings)
        if project_config is not None:
            _merge_servers(
                servers,
                _server_configs_from_mapping(
                    project_config.get("mcpServers", _MISSING),
                    "project",
                    project_path,
                    warnings,
                ),
                warnings,
            )

    return list(servers.values()), warnings


def _read_json_object(path: str, warnings: list[str]) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as err:
        warnings.append(f"could not parse {path}: {err}")
        return None
    except OSError as err:
        warnings.append(f"could not read {path}: {err}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: config root is not an object")
        return None
    return data


def _merge_servers(
    servers: dict[str, ServerConfig],
    incoming: list[ServerConfig],
    warnings: list[str],
) -> None:
    """Merge incoming servers, recording precedence provenance.

    A strictly higher-precedence entry wins and records the entry it shadows
    (plus that entry's own chain). An equal-precedence collision -- e.g. two
    ``projects`` keys that normalize to the same path -- keeps the later
    definition and warns, but is deliberately not recorded as a shadow, so it
    can never be mistaken for a lower-scope server that would reactivate on
    deletion.
    """

    for server in incoming:
        existing = servers.get(server.name)
        if existing is None:
            servers[server.name] = server
            continue
        if server.precedence > existing.precedence:
            warnings.append(
                f"server {server.name!r} from {server.scope} overrides "
                f"{existing.scope}"
            )
            server.shadowed = _ordered_chain((existing, *existing.shadowed))
            servers[server.name] = server
        elif server.precedence == existing.precedence:
            warnings.append(
                f"server {server.name!r} defined more than once at "
                f"{server.scope} scope -- keeping the later definition"
            )
            server.shadowed = existing.shadowed
            servers[server.name] = server
        else:
            # Out-of-order merge: the existing entry outranks the incoming one,
            # so it stays the winner and the incoming one joins its chain. The
            # chain is re-sorted so the immediate fallback is always the
            # highest-precedence shadowed entry regardless of merge order.
            existing.shadowed = _ordered_chain((*existing.shadowed, server))


def _ordered_chain(
    entries: tuple[ServerConfig, ...]
) -> tuple[ServerConfig, ...]:
    """Return a shadow chain ordered highest precedence first (stable)."""

    return tuple(
        sorted(entries, key=lambda entry: entry.precedence, reverse=True)
    )


def _server_configs_from_mapping(
    mapping: Any,
    scope: str,
    source_path: str,
    warnings: list[str],
    mapping_name: str = "mcpServers",
) -> list[ServerConfig]:
    if mapping is _MISSING:
        return []
    if not isinstance(mapping, dict):
        warnings.append(f"{source_path}: {mapping_name} is not an object")
        return []

    configs: list[ServerConfig] = []
    for name, spec in mapping.items():
        if not isinstance(spec, dict):
            warnings.append(
                f"{source_path}: server {name!r} spec is not an object"
            )
            continue
        if not isinstance(name, str):
            continue
        configs.append(
            _server_config_from_spec(name, scope, source_path, spec, warnings)
        )
    return configs


def _server_config_from_spec(
    name: str,
    scope: str,
    source_path: str,
    spec: dict[str, Any],
    warnings: list[str],
) -> ServerConfig:
    command = spec.get("command")
    url = spec.get("url")
    transport = spec.get("type")
    if not isinstance(transport, str):
        if isinstance(command, str):
            transport = "stdio"
        elif isinstance(url, str):
            transport = "http"
        else:
            transport = "unknown"

    enabled = True
    if "enabled" in spec:
        if isinstance(spec.get("enabled"), bool):
            enabled = spec["enabled"]
        else:
            warnings.append(
                f"{source_path}: server {name!r} enabled is not a boolean -- "
                "treating server as disabled"
            )
            enabled = False

    return ServerConfig(
        name=name,
        scope=scope,
        source_path=source_path,
        transport=transport if transport in {"stdio", "http", "sse"} else "unknown",
        command=command if isinstance(command, str) else None,
        args=_string_list(spec.get("args"), name, source_path, warnings, "args"),
        env=_string_dict(spec.get("env"), name, source_path, warnings),
        url=url if isinstance(url, str) else None,
        enabled=enabled,
        enabled_tools=_optional_string_list(
            spec.get("enabled_tools", _MISSING),
            name,
            source_path,
            warnings,
            "enabled_tools",
        ),
        disabled_tools=_optional_string_list(
            spec.get("disabled_tools", _MISSING),
            name,
            source_path,
            warnings,
            "disabled_tools",
        ),
        cwd=spec.get("cwd") if isinstance(spec.get("cwd"), str) else None,
        query_timeout=_optional_number(
            spec.get("startup_timeout_sec"),
            name,
            source_path,
            warnings,
            "startup_timeout_sec",
        ),
        precedence=_SCOPE_PRECEDENCE.get(scope, 0),
    )


def _optional_string_list(
    value: Any,
    server_name: str,
    source_path: str,
    warnings: list[str],
    field_name: str,
) -> list[str] | None:
    """Return a string allow/deny list, or ``None`` when absent or malformed.

    A key that is present but not a list is warned about and treated as absent
    (no filter). Returning an empty list here would silently hide every tool
    and could produce a misleading zero-cost prune row.
    """

    if value is _MISSING or value is None:
        return None
    if not isinstance(value, list):
        warnings.append(
            f"{source_path}: server {server_name!r} {field_name} is not a "
            "list -- ignored (no tool filter applied)"
        )
        return None
    return [item for item in value if isinstance(item, str)]


def _string_list(
    value: Any,
    server_name: str | None = None,
    source_path: str | None = None,
    warnings: list[str] | None = None,
    field_name: str = "value",
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        if warnings is not None and source_path is not None and server_name is not None:
            warnings.append(
                f"{source_path}: server {server_name!r} {field_name} is not "
                "a list -- ignored"
            )
        return []
    return [item for item in value if isinstance(item, str)]


def _string_dict(
    value: Any,
    server_name: str,
    source_path: str,
    warnings: list[str],
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append(
            f"{source_path}: server {server_name!r} env is not an object -- "
            "ignored"
        )
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            result[key] = item
            continue
        warnings.append(
            f"{source_path}: server {server_name!r} env key {key!r} "
            "has non-string value -- dropped"
        )
    return result


def _optional_number(
    value: Any,
    server_name: str,
    source_path: str,
    warnings: list[str],
    field_name: str,
) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not float(value) > 0
    ):
        warnings.append(
            f"{source_path}: server {server_name!r} {field_name} is not "
            "a positive finite number -- ignored"
        )
        return None
    return float(value)


def _same_config_path(left: str, right: str) -> bool:
    return _path_key(left) == _path_key(right)


def _path_key(path: str) -> str:
    normalized = os.path.normpath(path).replace("\\", "/")
    normalized = normalized.rstrip("/")
    return normalized.casefold() if os.name == "nt" else normalized
