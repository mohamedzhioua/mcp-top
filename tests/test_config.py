"""Unit tests for Claude Code config inventory."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401

from mcp_top.config import discover_servers


class DiscoverServersTests(unittest.TestCase):
    """Exercises the supported Claude Code config scopes."""

    def test_user_scope_only(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            user_path = os.path.join(home, ".claude.json")
            self._write_json(
                user_path,
                {
                    "mcpServers": {
                        "context7": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@upstash/context7-mcp@3.2.3"],
                            "env": {"API_KEY": "local"},
                        }
                    }
                },
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(len(servers), 1)
        server = servers[0]
        self.assertEqual(server.name, "context7")
        self.assertEqual(server.scope, "user")
        self.assertEqual(server.source_path, user_path)
        self.assertEqual(server.transport, "stdio")
        self.assertEqual(server.command, "npx")
        self.assertEqual(server.args, ["-y", "@upstash/context7-mcp@3.2.3"])
        self.assertEqual(server.env, {"API_KEY": "local"})
        self.assertIsNone(server.url)

    def test_project_scope_overrides_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            self._write_json(
                os.path.join(home, ".claude.json"),
                {
                    "mcpServers": {
                        "shared": {"command": "old"},
                        "user-only": {"url": "https://example.com/mcp"},
                    }
                },
            )
            self._write_json(
                os.path.join(project_dir, ".mcp.json"),
                {"mcpServers": {"shared": {"command": "new", "args": ["serve"]}}},
            )

            servers, warnings = discover_servers(home, project_dir)

        by_name = {server.name: server for server in servers}
        self.assertEqual(by_name["shared"].scope, "project")
        self.assertEqual(by_name["shared"].command, "new")
        self.assertEqual(by_name["shared"].args, ["serve"])
        self.assertEqual(by_name["user-only"].transport, "http")
        self.assertTrue(any("shared" in warning for warning in warnings))
        self.assertTrue(any("overrides" in warning for warning in warnings))

    def test_user_project_scope_path_matching_depends_on_platform_case_rules(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "CaseProject")
            os.mkdir(project_dir)
            configured_path = project_dir.replace("\\", "/").swapcase()
            self._write_json(
                os.path.join(home, ".claude.json"),
                {
                    "projects": {
                        configured_path: {
                            "mcpServers": {
                                "projectish": {
                                    "type": "sse",
                                    "url": "https://example.com/events",
                                }
                            }
                        }
                    }
                },
            )

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual(warnings, [])
        if os.name == "nt":
            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0].name, "projectish")
            self.assertEqual(servers[0].scope, "user-project")
        else:
            self.assertEqual(servers, [])

    def test_user_project_scope_path_matching_normalizes_slashes(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            if os.name == "nt":
                configured_path = project_dir.replace("\\", "/")
            else:
                configured_path = project_dir.replace("/", "\\")
            self._write_json(
                os.path.join(home, ".claude.json"),
                {
                    "projects": {
                        configured_path: {
                            "mcpServers": {"projectish": {"command": "run"}}
                        }
                    }
                },
            )

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual(warnings, [])
        self.assertEqual([server.name for server in servers], ["projectish"])

    def test_non_object_config_shapes_emit_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(os.path.join(home, ".claude.json"), [])
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            project_path = os.path.join(project_dir, ".mcp.json")
            self._write_json(
                project_path,
                {"mcpServers": {"bad-server": "not-an-object"}},
            )

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual(servers, [])
        self.assertIn(
            f"{os.path.join(home, '.claude.json')}: config root is not an object",
            warnings,
        )
        self.assertIn(
            f"{project_path}: server 'bad-server' spec is not an object",
            warnings,
        )

    def test_mcp_servers_non_object_and_recursion_error_emit_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            user_path = os.path.join(home, ".claude.json")
            self._write_json(user_path, {"mcpServers": []})

            servers, warnings = discover_servers(home, None)

            self.assertEqual(servers, [])
            self.assertEqual(
                warnings, [f"{user_path}: mcpServers is not an object"]
            )

            with mock.patch(
                "mcp_top.config.json.load", side_effect=RecursionError("deep")
            ):
                servers, warnings = discover_servers(home, None)

        self.assertEqual(servers, [])
        self.assertEqual(warnings, [f"could not parse {user_path}: deep"])

    def test_malformed_project_config_warns_and_keeps_user_scope(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"user-server": {"command": "python"}}},
            )
            project_path = os.path.join(project_dir, ".mcp.json")
            with open(project_path, "w", encoding="utf-8") as handle:
                handle.write("{not json")

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual([server.name for server in servers], ["user-server"])
        self.assertEqual(servers[0].scope, "user")
        self.assertEqual(len(warnings), 1)
        self.assertIn(f"could not parse {project_path}:", warnings[0])

    def test_missing_files_are_empty_without_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual(servers, [])
        self.assertEqual(warnings, [])

    def test_malformed_server_fields_warn_and_use_safe_values(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            user_path = os.path.join(home, ".claude.json")
            self._write_json(
                user_path,
                {
                    "mcpServers": {
                        "bad": {
                            "command": "fake",
                            "args": "not-list",
                            "env": {"KEEP": "yes", "DROP": 7},
                            "startup_timeout_sec": float("inf"),
                            "enabled": "false",
                        }
                    }
                },
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(len(servers), 1)
        server = servers[0]
        self.assertEqual(server.args, [])
        self.assertEqual(server.env, {"KEEP": "yes"})
        self.assertIsNone(server.query_timeout)
        self.assertFalse(server.enabled)
        self.assertTrue(any("args is not a list" in warning for warning in warnings))
        self.assertTrue(any("env key 'DROP'" in warning for warning in warnings))
        self.assertTrue(
            any(
                "startup_timeout_sec is not a positive finite number" in warning
                for warning in warnings
            )
        )
        self.assertTrue(
            any("enabled is not a boolean" in warning for warning in warnings)
        )

    def _write_json(self, path: str, data: object) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)


if __name__ == "__main__":
    unittest.main()
