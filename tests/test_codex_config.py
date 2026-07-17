"""Unit tests for Codex CLI config inventory."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import _path  # noqa: F401

from mcp_top.clis.codex import discover_servers


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class CodexConfigTests(unittest.TestCase):
    def test_parses_user_scope_toml_and_codex_fields(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            codex_dir = os.path.join(home, ".codex")
            os.makedirs(codex_dir)
            config_path = os.path.join(codex_dir, "config.toml")
            shutil.copyfile(os.path.join(FIXTURES, "codex_config.toml"), config_path)

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        by_name = {server.name: server for server in servers}
        self.assertEqual(set(by_name), {"context7", "disabled_server", "remote_docs"})

        context7 = by_name["context7"]
        self.assertEqual(context7.scope, "user")
        self.assertEqual(context7.source_path, config_path)
        self.assertEqual(context7.transport, "stdio")
        self.assertEqual(context7.command, "npx")
        self.assertEqual(context7.args, ["-y", "@upstash/context7-mcp@3.2.3"])
        self.assertEqual(context7.env, {"CONTEXT7_API_KEY": "local"})
        self.assertTrue(context7.enabled)
        self.assertEqual(
            context7.enabled_tools,
            ["resolve-library-id", "get-library-docs"],
        )
        self.assertEqual(context7.disabled_tools, ["get-library-docs"])
        self.assertEqual(context7.cwd, "/tmp")
        self.assertEqual(context7.query_timeout, 7.5)

        disabled = by_name["disabled_server"]
        self.assertFalse(disabled.enabled)

        remote = by_name["remote_docs"]
        self.assertEqual(remote.transport, "http")
        self.assertEqual(remote.url, "https://example.com/mcp")

    def test_missing_and_absent_mcp_servers_are_empty_without_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(discover_servers(home, None), ([], []))

            codex_dir = os.path.join(home, ".codex")
            os.makedirs(codex_dir)
            with open(
                os.path.join(codex_dir, "config.toml"), "w", encoding="utf-8"
            ) as handle:
                handle.write("model = 'gpt-5'\n")

            servers, warnings = discover_servers(home, None)

        self.assertEqual(servers, [])
        self.assertEqual(warnings, [])

    def test_invalid_toml_warns_without_servers(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            codex_dir = os.path.join(home, ".codex")
            os.makedirs(codex_dir)
            config_path = os.path.join(codex_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("[mcp_servers.context7\n")

            servers, warnings = discover_servers(home, None)

        self.assertEqual(servers, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn(f"could not parse {config_path}:", warnings[0])

    def test_project_layer_config_warns_but_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            codex_dir = os.path.join(home, ".codex")
            os.makedirs(codex_dir)
            shutil.copyfile(
                os.path.join(FIXTURES, "codex_config.toml"),
                os.path.join(codex_dir, "config.toml"),
            )
            project = os.path.join(home, "project")
            project_codex = os.path.join(project, ".codex")
            os.makedirs(project_codex)
            with open(
                os.path.join(project_codex, "config.toml"), "w", encoding="utf-8"
            ) as handle:
                handle.write("[mcp_servers.project_only]\ncommand = 'ignored'\n")

            servers, warnings = discover_servers(home, project)

        self.assertNotIn("project_only", {server.name for server in servers})
        self.assertEqual(
            warnings,
            [
                "project-layer codex config present but not read in v0.2 "
                "(user scope only)"
            ],
        )

    def test_project_layer_warning_does_not_require_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "project")
            project_codex = os.path.join(project, ".codex")
            os.makedirs(project_codex)
            with open(
                os.path.join(project_codex, "config.toml"), "w", encoding="utf-8"
            ) as handle:
                handle.write("[mcp_servers.project_only]\ncommand = 'ignored'\n")

            servers, warnings = discover_servers(home, project)

        self.assertEqual(servers, [])
        self.assertEqual(
            warnings,
            [
                "project-layer codex config present but not read in v0.2 "
                "(user scope only)"
            ],
        )

    def test_malformed_server_fields_warn_and_use_safe_values(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            codex_dir = os.path.join(home, ".codex")
            os.makedirs(codex_dir)
            config_path = os.path.join(codex_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[mcp_servers.bad]\n"
                    "command = 'fake'\n"
                    "args = 'not-list'\n"
                    "enabled = 'false'\n"
                    "startup_timeout_sec = -1\n"
                    "[mcp_servers.bad.env]\n"
                    "KEEP = 'yes'\n"
                    "DROP = 7\n"
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


if __name__ == "__main__":
    unittest.main()
