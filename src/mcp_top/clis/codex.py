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
        _report_project_layer(project_dir, user_path, warnings)

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


def _report_project_layer(
    project_dir: str, user_path: str, warnings: list[str]
) -> None:
    """Report a project-layer Codex config as a conditional inventory.

    The project layer is read and its server names are listed, but it is
    deliberately not queried and not merged over the user scope. Codex applies
    project layers only to *trusted* projects -- a state mcp-top cannot observe
    from files -- reads a cascade of files from the project root down to the
    working directory, and field-merges same-name tables. Faithfully
    reproducing that precedence from the published spec is not possible, so the
    ambiguity is reported rather than guessed. See
    docs/v0.3-provenance-and-prune.md.
    """

    project_path = os.path.join(project_dir, ".codex", "config.toml")
    if not os.path.exists(project_path):
        return
    # When --project points at the home directory, the "project" config file is
    # literally the user config; do not re-report it as a separate layer.
    if _same_file(project_path, user_path):
        return
    config = _read_toml_object(project_path, warnings)
    if config is None:
        # A read/parse warning was already recorded by _read_toml_object.
        warnings.append(
            f"project-layer codex config {project_path} detected but could "
            "not be read -- not queried and not merged"
        )
        return
    mapping = config.get("mcp_servers")
    names = (
        sorted(str(name) for name in mapping if isinstance(name, str))
        if isinstance(mapping, dict)
        else []
    )
    listed = f": {', '.join(names)}" if names else " (no mcp_servers table)"
    warnings.append(
        f"project-layer codex config detected at {project_path}"
        f"{listed} -- not queried and not merged. Codex applies project "
        "layers only to trusted projects (unobservable here) and layer merge "
        "semantics are undocumented, so these are shown as a conditional "
        "inventory only."
    )


def _same_file(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )


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
