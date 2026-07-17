"""Discover Cursor MCP server configuration from JSON inventory files."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp_top.config import (
    _MISSING,
    ServerConfig,
    _merge_servers,
    _server_configs_from_mapping,
)


def discover_servers(
    home: str, project_dir: str | None
) -> tuple[list[ServerConfig], list[str]]:
    """Return Cursor MCP servers from user and project scopes."""

    warnings: list[str] = []
    servers: dict[str, ServerConfig] = {}

    user_path = os.path.join(home, ".cursor", "mcp.json")
    user_config = _read_json_object_with_empty_warning(user_path, warnings)
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
        project_path = os.path.join(project_dir, ".cursor", "mcp.json")
        project_config = _read_json_object_with_empty_warning(
            project_path, warnings
        )
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


def _read_json_object_with_empty_warning(
    path: str, warnings: list[str]
) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeError) as err:
        warnings.append(f"could not read {path}: {err}")
        return None
    if not text.strip():
        warnings.append(f"{path}: exists but is empty -- no servers read")
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as err:
        warnings.append(f"could not parse {path}: {err}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: config root is not an object")
        return None
    return data
