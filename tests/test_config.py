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

    def test_winner_records_shadowed_lower_precedence_entry(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"shared": {"command": "user-cmd"}}},
            )
            self._write_json(
                os.path.join(project_dir, ".mcp.json"),
                {"mcpServers": {"shared": {"command": "project-cmd"}}},
            )

            servers, _ = discover_servers(home, project_dir)

        winner = {server.name: server for server in servers}["shared"]
        self.assertEqual(winner.scope, "project")
        self.assertEqual(winner.command, "project-cmd")
        self.assertGreater(winner.precedence, 0)
        # The user-scope entry is retained so a deletion can be simulated.
        self.assertEqual(len(winner.shadowed), 1)
        self.assertEqual(winner.shadowed[0].scope, "user")
        self.assertEqual(winner.shadowed[0].command, "user-cmd")
        self.assertLess(winner.shadowed[0].precedence, winner.precedence)

    def test_user_scope_winner_has_no_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"solo": {"command": "run"}}},
            )

            servers, _ = discover_servers(home, None)

        self.assertEqual(servers[0].shadowed, ())
        self.assertEqual(servers[0].precedence, 0)

    def test_three_layer_shadow_chain_uses_local_project_user_precedence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            configured_path = project_dir.replace("\\", "/")
            self._write_json(
                os.path.join(home, ".claude.json"),
                {
                    "mcpServers": {"shared": {"command": "user-cmd"}},
                    "projects": {
                        configured_path: {
                            "mcpServers": {"shared": {"command": "userproj-cmd"}}
                        }
                    },
                },
            )
            self._write_json(
                os.path.join(project_dir, ".mcp.json"),
                {"mcpServers": {"shared": {"command": "project-cmd"}}},
            )

            servers, _ = discover_servers(home, project_dir)

        winner = {server.name: server for server in servers}["shared"]
        self.assertEqual(winner.scope, "user-project")
        self.assertEqual(winner.command, "userproj-cmd")
        self.assertEqual(
            [entry.scope for entry in winner.shadowed],
            ["project", "user"],
        )
        # Precedence must be strictly decreasing down the chain.
        ranks = [winner.precedence, *[e.precedence for e in winner.shadowed]]
        self.assertEqual(ranks, [2, 1, 0])

    def test_local_always_load_value_beats_opposing_project_value(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            configured_path = project_dir.replace("\\", "/")
            self._write_json(
                os.path.join(home, ".claude.json"),
                {
                    "projects": {
                        configured_path: {
                            "mcpServers": {
                                "shared": {
                                    "command": "local-cmd",
                                    "alwaysLoad": False,
                                }
                            }
                        }
                    }
                },
            )
            self._write_json(
                os.path.join(project_dir, ".mcp.json"),
                {
                    "mcpServers": {
                        "shared": {
                            "command": "project-cmd",
                            "alwaysLoad": True,
                        }
                    }
                },
            )

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual(warnings, [])
        winner = servers[0]
        self.assertEqual(winner.scope, "user-project")
        self.assertEqual(winner.precedence, 2)
        self.assertFalse(winner.always_load)
        self.assertEqual(winner.loading_regime, "unknown")
        self.assertEqual(winner.shadowed[0].scope, "project")
        self.assertEqual(winner.shadowed[0].precedence, 1)
        self.assertTrue(winner.shadowed[0].always_load)

    def test_malformed_enabled_tools_applies_no_filter(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            user_path = os.path.join(home, ".claude.json")
            self._write_json(
                user_path,
                {
                    "mcpServers": {
                        "srv": {
                            "command": "run",
                            "enabled_tools": "not-a-list",
                        }
                    }
                },
            )

            servers, warnings = discover_servers(home, None)

        # A malformed allowlist must not become an empty allowlist that hides
        # every tool (which would produce a misleading zero-cost prune row).
        self.assertIsNone(servers[0].enabled_tools)
        self.assertTrue(
            any(
                "enabled_tools is not a list" in warning for warning in warnings
            )
        )

    def test_always_load_server_is_upfront(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            user_path = os.path.join(home, ".claude.json")
            self._write_json(
                user_path,
                {"mcpServers": {"srv": {"command": "run", "alwaysLoad": True}}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "upfront")
        self.assertIn("alwaysLoad=true", servers[0].regime_evidence[0])

    def test_settings_enable_tool_search_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"env": {"ENABLE_TOOL_SEARCH": "true"}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "deferred")
        self.assertIn(
            "env.ENABLE_TOOL_SEARCH=true",
            servers[0].regime_evidence[0],
        )

    def test_settings_disable_tool_search_is_upfront(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"env": {"ENABLE_TOOL_SEARCH": "false"}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "upfront")
        self.assertIn(
            "env.ENABLE_TOOL_SEARCH=false",
            servers[0].regime_evidence[0],
        )

    def test_settings_auto_tool_search_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"env": {"ENABLE_TOOL_SEARCH": "auto:5"}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "unknown")
        self.assertIn(
            "env.ENABLE_TOOL_SEARCH=auto:N",
            servers[0].regime_evidence[0],
        )

    def test_settings_tool_search_denial_is_upfront(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"permissions": {"deny": ["ToolSearch"]}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "upfront")
        self.assertIn(
            "permissions.deny=ToolSearch",
            servers[0].regime_evidence[0],
        )

    def test_scoped_tool_search_denial_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"permissions": {"deny": ["ToolSearch(mcp__srv__*)"]}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "unknown")
        self.assertEqual(
            servers[0].regime_evidence,
            [f"{settings_path}:permissions.deny=ToolSearch(...)"],
        )

    def test_disable_experimental_betas_overrides_enable_tool_search(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {
                    "env": {
                        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                        "ENABLE_TOOL_SEARCH": "true",
                    }
                },
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "upfront")
        self.assertTrue(
            any(
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=set" in evidence
                for evidence in servers[0].regime_evidence
            )
        )
        self.assertFalse(
            any(
                "EXPERIMENTAL_BETAS=1" in evidence
                for evidence in servers[0].regime_evidence
            )
        )

    def test_invalid_beta_override_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"env": {"CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "true"}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "unknown")
        self.assertEqual(
            servers[0].regime_evidence,
            [
                f"{settings_path}:env."
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=unrecognized"
            ],
        )

    def test_invalid_tool_search_env_is_unknown_without_leaking_value(self) -> None:
        sentinel = "sk_live_DO_NOT_LEAK"
        with tempfile.TemporaryDirectory() as home:
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            settings_path = os.path.join(home, ".claude", "settings.json")
            os.makedirs(os.path.dirname(settings_path))
            self._write_json(
                settings_path,
                {"env": {"ENABLE_TOOL_SEARCH": sentinel}},
            )

            servers, warnings = discover_servers(home, None)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "unknown")
        self.assertEqual(
            servers[0].regime_evidence,
            [f"{settings_path}:env.ENABLE_TOOL_SEARCH=unrecognized"],
        )
        self.assertNotIn(sentinel, json.dumps(servers[0].regime_evidence))

    def test_conflicting_tool_search_settings_are_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project_dir = os.path.join(home, "project")
            os.mkdir(project_dir)
            self._write_json(
                os.path.join(home, ".claude.json"),
                {"mcpServers": {"srv": {"command": "run"}}},
            )
            user_settings = os.path.join(home, ".claude", "settings.json")
            project_settings = os.path.join(
                project_dir, ".claude", "settings.json"
            )
            os.makedirs(os.path.dirname(user_settings))
            os.makedirs(os.path.dirname(project_settings))
            self._write_json(
                user_settings,
                {"env": {"ENABLE_TOOL_SEARCH": "true"}},
            )
            self._write_json(
                project_settings,
                {"env": {"ENABLE_TOOL_SEARCH": "false"}},
            )

            servers, warnings = discover_servers(home, project_dir)

        self.assertEqual(warnings, [])
        self.assertEqual(servers[0].loading_regime, "unknown")
        self.assertEqual(len(servers[0].regime_evidence), 2)

    def _write_json(self, path: str, data: object) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)


if __name__ == "__main__":
    unittest.main()
