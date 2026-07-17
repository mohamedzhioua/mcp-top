"""Discover Codex CLI MCP server configuration from user-scope TOML."""

from __future__ import annotations

import os
from typing import Any

from mcp_top.config import (
    ServerConfig,
    _MISSING,
    _server_configs_from_mapping,
)


def discover_servers(
    home: str, project_dir: str | None
) -> tuple[list[ServerConfig], list[str]]:
    """Return Codex user-scope MCP servers and non-fatal warnings."""

    warnings: list[str] = []
    user_path = os.path.join(home, ".codex", "config.toml")
    if project_dir is not None:
        project_path = os.path.join(project_dir, ".codex", "config.toml")
        if os.path.exists(project_path):
            warnings.append(
                "project-layer codex config present but not read in v0.2 "
                "(user scope only)"
            )

    config = _read_toml_object(user_path, warnings)
    if config is None:
        return [], warnings

    servers = _server_configs_from_mapping(
        config.get("mcp_servers", _MISSING),
        "user",
        user_path,
        warnings,
        "mcp_servers",
    )
    return servers, warnings


def _read_toml_object(
    path: str, warnings: list[str]
) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    import tomllib

    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as err:
        warnings.append(f"could not parse {path}: {err}")
        return None
    except (OSError, UnicodeError, RecursionError) as err:
        warnings.append(f"could not read {path}: {err}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: config root is not an object")
        return None
    return data
