"""Discover Claude Code MCP server configuration from local files.

The module is intentionally read-only: it enumerates known Claude Code config
scopes and never writes user configuration.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any


_MISSING = object()


@dataclass
class ServerConfig:
    """A configured MCP server from the winning CLI config scope."""

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
    for server in incoming:
        existing = servers.get(server.name)
        if existing is not None:
            warnings.append(
                f"server {server.name!r} from {server.scope} overrides "
                f"{existing.scope}"
            )
        servers[server.name] = server


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
        enabled_tools=_optional_string_list(spec.get("enabled_tools")),
        disabled_tools=_optional_string_list(spec.get("disabled_tools")),
        cwd=spec.get("cwd") if isinstance(spec.get("cwd"), str) else None,
        query_timeout=_optional_number(
            spec.get("startup_timeout_sec"),
            name,
            source_path,
            warnings,
            "startup_timeout_sec",
        ),
    )


def _optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return _string_list(value)


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
