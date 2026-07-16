"""Unit tests for Claude Code config inventory."""

import json
import os
import tempfile
import unittest

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

    def test_user_project_scope_path_matching_normalizes_case_and_slashes(self) -> None:
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
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].name, "projectish")
        self.assertEqual(servers[0].scope, "user-project")
        self.assertEqual(servers[0].transport, "sse")
        self.assertEqual(servers[0].url, "https://example.com/events")

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

    def _write_json(self, path: str, data: object) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)


if __name__ == "__main__":
    unittest.main()
