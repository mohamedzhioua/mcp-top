"""Unit and CLI integration tests for Cursor inventory support."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

import _path  # noqa: F401

from mcp_top import cli
from mcp_top.clis.cursor import discover_servers


class CursorTests(unittest.TestCase):
    def test_user_and_project_configs_merge_with_project_override(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "project")
            os.makedirs(os.path.join(home, ".cursor"))
            os.makedirs(os.path.join(project, ".cursor"))
            user_path = os.path.join(home, ".cursor", "mcp.json")
            project_path = os.path.join(project, ".cursor", "mcp.json")
            self._write_json(
                user_path,
                {
                    "mcpServers": {
                        "shared": {"command": "old"},
                        "user_only": {"url": "https://example.com/mcp"},
                    }
                },
            )
            self._write_json(
                project_path,
                {"mcpServers": {"shared": {"command": "new", "args": ["serve"]}}},
            )

            servers, warnings = discover_servers(home, project)

        by_name = {server.name: server for server in servers}
        self.assertEqual(by_name["shared"].scope, "project")
        self.assertEqual(by_name["shared"].command, "new")
        self.assertEqual(by_name["shared"].args, ["serve"])
        self.assertEqual(by_name["user_only"].transport, "http")
        self.assertTrue(any("shared" in warning for warning in warnings))
        self.assertTrue(any("overrides" in warning for warning in warnings))

    def test_empty_file_warns(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".cursor"))
            path = os.path.join(home, ".cursor", "mcp.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("  \n")

            servers, warnings = discover_servers(home, None)

        self.assertEqual(servers, [])
        self.assertEqual(warnings, [f"{path}: exists but is empty -- no servers read"])

    def test_cli_reports_cursor_usage_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "project")
            os.makedirs(os.path.join(home, ".cursor"))
            os.makedirs(project)
            self._write_json(
                os.path.join(home, ".cursor", "mcp.json"),
                {"mcpServers": {"docs": {"url": "https://example.com/mcp"}}},
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = cli.main(
                    [
                        "--home",
                        home,
                        "--project",
                        project,
                        "--cli",
                        "cursor",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["clis"][0]["cli"], "cursor")
        coverage = payload["clis"][0]["coverage"]
        self.assertEqual(
            coverage["usage_note"],
            "Cursor stores chats in undocumented SQLite; no transcript "
            "adapter in v0.2 -- usage unknown",
        )
        row = payload["clis"][0]["servers"][0]
        self.assertEqual(row["usage_status"], "unsupported")
        self.assertIsNone(row["calls"])
        self.assertEqual(row["verdict"], "unknown")

    def _write_json(self, path: str, data: object) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)


if __name__ == "__main__":
    unittest.main()

