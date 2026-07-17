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
    project_names: set[str] | None = None
    project_unreadable = False
    if project_dir is not None:
        project_names, project_unreadable = _report_project_layer(
            project_dir, user_path, warnings
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
    _apply_project_layer_caveats(servers, project_names, project_unreadable)
    return servers, warnings


def _report_project_layer(
    project_dir: str, user_path: str, warnings: list[str]
) -> tuple[set[str], bool]:
    """Report the project-layer Codex config as a conditional inventory.

    The project layer is read and its server names are listed, but it is
    deliberately not queried and not merged over the user scope. Codex applies
    project layers only to *trusted* projects -- a state mcp-top cannot observe
    from files -- reads a cascade of files from the project root down to the
    working directory, and field-merges same-name tables. Faithfully
    reproducing that precedence from the published spec is not possible, so the
    ambiguity is reported rather than guessed.

    Only ``<project_dir>/.codex/config.toml`` is inspected: it is the
    closest-wins (highest-precedence) project layer. mcp-top does not have a
    project-root concept, so it cannot safely walk the full root-to-cwd cascade
    without escaping the project into unrelated ancestor directories. A
    same-name override present only in a lower ancestor cascade file is
    therefore not detected -- a documented residual (see
    docs/v0.3-provenance-and-prune.md).

    Returns ``(names, unreadable)``: the set of project-layer server names when
    the file was read, and whether a present project file could not be read.
    Either signal downgrades a same-name user prune to a review candidate,
    because a trusted project could redefine that server.
    """

    project_path = os.path.join(project_dir, ".codex", "config.toml")
    if not os.path.exists(project_path):
        return set(), False
    # When --project points at the home directory, the "project" config file is
    # literally the user config; do not re-report it as a separate layer.
    if _same_file(project_path, user_path):
        return set(), False
    config = _read_toml_object(project_path, warnings)
    if config is None:
        # A read/parse warning was already recorded by _read_toml_object.
        warnings.append(
            f"project-layer codex config {project_path} detected but could "
            "not be read -- not queried and not merged"
        )
        return set(), True
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
    return set(names), False


def _apply_project_layer_caveats(
    servers: list[ServerConfig],
    project_names: set[str] | None,
    project_unreadable: bool,
) -> None:
    """Downgrade user servers a conditional project layer might redefine.

    A same-name project-layer entry (or an unreadable project layer that could
    contain one) means removing the user server may not save its cost in a
    trusted project, so it must never be a clean prune suggestion.
    """

    if project_unreadable:
        for server in servers:
            server.resolution_caveat = (
                "a project-layer .codex/config.toml exists but could not be "
                "read; in a trusted project it may redefine this server, so "
                "removal may not save the full cost"
            )
        return
    if not project_names:
        return
    for server in servers:
        if server.name in project_names:
            server.resolution_caveat = (
                "a project-layer .codex/config.toml also defines this server; "
                "in a trusted project Codex may override or merge it, so "
                "removal may not save the full cost"
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
