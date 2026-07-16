"""Discover Claude Code MCP server configuration from local files.

The module is intentionally read-only: it enumerates known Claude Code config
scopes and never writes user configuration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ServerConfig:
    """A configured MCP server from the winning Claude Code config scope."""

    name: str
    scope: str
    source_path: str
    transport: str
    command: str | None
    args: list[str]
    env: dict[str, str]
    url: str | None


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
                user_config.get("mcpServers"), "user", user_path
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
                                project_config.get("mcpServers"),
                                "user-project",
                                user_path,
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
                    project_config.get("mcpServers"), "project", project_path
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
    except (json.JSONDecodeError, UnicodeError) as err:
        warnings.append(f"could not parse {path}: {err}")
        return None
    except OSError as err:
        warnings.append(f"could not read {path}: {err}")
        return None
    if not isinstance(data, dict):
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
    mapping: Any, scope: str, source_path: str
) -> list[ServerConfig]:
    if not isinstance(mapping, dict):
        return []

    configs: list[ServerConfig] = []
    for name, spec in mapping.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        configs.append(_server_config_from_spec(name, scope, source_path, spec))
    return configs


def _server_config_from_spec(
    name: str, scope: str, source_path: str, spec: dict[str, Any]
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

    return ServerConfig(
        name=name,
        scope=scope,
        source_path=source_path,
        transport=transport if transport in {"stdio", "http", "sse"} else "unknown",
        command=command if isinstance(command, str) else None,
        args=_string_list(spec.get("args")),
        env=_string_dict(spec.get("env")),
        url=url if isinstance(url, str) else None,
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _same_config_path(left: str, right: str) -> bool:
    return _path_key(left) == _path_key(right)


def _path_key(path: str) -> str:
    normalized = os.path.normpath(path).replace("\\", "/")
    return normalized.rstrip("/").casefold()
