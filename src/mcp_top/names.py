"""Parse Claude Code MCP tool names into server and tool components."""

from __future__ import annotations

from typing import Collection


def parse_mcp_tool_name(
    name: str, configured: Collection[str] | None = None
) -> tuple[str, str] | None:
    """Attribute an MCP tool name, preferring the longest configured server."""

    prefix = "mcp__"
    if not name.startswith(prefix):
        return None

    if configured is not None:
        for server in sorted(configured, key=len, reverse=True):
            server_prefix = prefix + server
            if name == server_prefix:
                return server, ""
            separator_prefix = server_prefix + "__"
            if name.startswith(separator_prefix):
                return server, name[len(separator_prefix) :]

    remainder = name[len(prefix) :]
    if "__" not in remainder:
        return None
    server, tool = remainder.split("__", 1)
    if not server or not tool:
        return None
    return server, tool
