"""End-to-end tests for JSON, human, and no-query CLI modes."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401

from mcp_top import cli
from mcp_top.coverage import Coverage
from mcp_top.counter import UsageWindow
from mcp_top.engine import CliReport, PruneSuggestion, Report, ServerRow
from mcp_top.tokens import TokenCount


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
GOLDENS = os.path.join(os.path.dirname(__file__), "goldens")
FAKE_SERVER = os.path.join(FIXTURES, "fake_mcp_server.py")
TRANSCRIPTS = os.path.join(FIXTURES, "transcripts")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = self.temporary.name
        self.project = os.path.join(self.home, "project")
        os.makedirs(self.project)
        transcript_dir = os.path.join(
            self.home, ".claude", "projects", "project-slug"
        )
        os.makedirs(transcript_dir)
        for fixture in ("good_session.jsonl", "unknown_version.jsonl"):
            shutil.copyfile(
                os.path.join(TRANSCRIPTS, fixture),
                os.path.join(transcript_dir, fixture),
            )
        with open(
            os.path.join(self.home, ".claude.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(
                {
                    "mcpServers": {
                        "github": {
                            "command": sys.executable,
                            "args": [FAKE_SERVER, "serve"],
                        }
                    }
                },
                handle,
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_json_reports_skips_estimated_definitions_and_verdict(self) -> None:
        exit_code, output = self._run("--json")

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["schema"], "mcp-top/v3")
        self.assertEqual(len(payload["clis"]), 1)
        cli_payload = payload["clis"][0]
        self.assertEqual(cli_payload["cli"], "claude-code")
        self.assertEqual(cli_payload["coverage"]["transcripts_found"], 2)
        self.assertEqual(cli_payload["coverage"]["transcripts_parsed"], 1)
        self.assertEqual(len(cli_payload["coverage"]["transcripts_skipped"]), 1)
        self.assertIn(
            "unknown format version",
            cli_payload["coverage"]["transcripts_skipped"][0][1],
        )
        server = next(
            row for row in cli_payload["servers"] if row["server"] == "github"
        )
        self.assertNotIn("def_tokens", server)
        self.assertIsNotNone(server["advertised_max_tokens"])
        self.assertFalse(server["advertised_max_tokens"]["exact"])
        self.assertIsNotNone(server["upfront_floor_tokens"])
        self.assertEqual(server["loading_regime"], "unknown")
        self.assertEqual(server["regime_evidence"], [])
        self.assertEqual(server["calls"], 2)
        self.assertEqual(server["usage_status"], "measured")
        self.assertEqual(server["verdict"], "review")
        self.assertEqual(server["filtered_tools"], 0)

    def test_human_output_starts_with_coverage_then_table(self) -> None:
        exit_code, output = self._run()

        self.assertEqual(exit_code, 0)
        self.assertTrue(output.startswith("Coverage:"))
        self.assertIn("SERVER", output)
        self.assertIn("TOKEN RANGE", output)
        self.assertIn("REGIME", output)
        self.assertLess(output.index("Coverage:"), output.index("SERVER"))

    def test_human_v04_full_output_matches_golden(self) -> None:
        output = io.StringIO()
        with mock.patch.object(cli, "_build", return_value=self._golden_report()):
            with contextlib.redirect_stdout(output):
                exit_code = cli.main(["--prune"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), self._read_golden("cli_human_v04.txt"))

    def test_json_v3_full_output_matches_golden(self) -> None:
        output = io.StringIO()
        with mock.patch.object(cli, "_build", return_value=self._golden_report()):
            with contextlib.redirect_stdout(output):
                exit_code = cli.main(["--json", "--prune"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), self._read_golden("cli_json_v3.json"))

    def test_no_query_is_unsupported_but_successful(self) -> None:
        exit_code, output = self._run("--json", "--no-query")

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        server = next(
            row
            for row in payload["clis"][0]["servers"]
            if row["server"] == "github"
        )
        self.assertEqual(server["def_status"], "unsupported")
        self.assertEqual(server["def_error"], "skipped by --no-query")
        self.assertIsNone(server["advertised_max_tokens"])
        self.assertIsNone(server["upfront_floor_tokens"])

    def test_json_emits_nullable_calls_and_usage_status(self) -> None:
        report = Report(
            clis=[
                CliReport(
                    cli="future-cli",
                    rows=[
                        ServerRow(
                            server="alpha",
                            scope="user",
                            transport="stdio",
                            advertised_max_tokens=None,
                            upfront_floor_tokens=None,
                            loading_regime="unknown",
                            regime_evidence=[],
                            def_status="unsupported",
                            def_error="not implemented",
                            tool_count=None,
                            calls=None,
                            usage_status="unsupported",
                            called_tools={},
                            verdict="unknown",
                        )
                    ],
                    window=None,
                    coverage=Coverage(
                        transcripts_found=0,
                        transcripts_parsed=0,
                        transcripts_skipped=[],
                        in_window=None,
                        total_tool_calls=None,
                        mcp_tool_calls=None,
                        servers_queried_ok=0,
                        servers_query_failed=[],
                        config_warnings=[],
                        usage_note="no transcript adapter for this CLI",
                    ),
                )
            ],
            generated_note="note",
        )

        payload = cli._report_json(report)

        server = payload["clis"][0]["servers"][0]
        self.assertIsNone(payload["clis"][0]["window"])
        self.assertIsNone(server["calls"])
        self.assertEqual(server["usage_status"], "unsupported")
        self.assertEqual(server["verdict"], "unknown")
        self.assertIsNone(payload["clis"][0]["coverage"]["in_window"])
        self.assertIsNone(payload["clis"][0]["coverage"]["total_tool_calls"])
        self.assertIsNone(payload["clis"][0]["coverage"]["mcp_tool_calls"])

    def test_human_output_renders_unknown_usage_with_dash(self) -> None:
        report = Report(
            clis=[
                CliReport(
                    cli="future-cli",
                    rows=[
                        ServerRow(
                            server="alpha",
                            scope="user",
                            transport="stdio",
                            advertised_max_tokens=None,
                            upfront_floor_tokens=None,
                            loading_regime="unknown",
                            regime_evidence=[],
                            def_status="unsupported",
                            def_error="not implemented",
                            tool_count=None,
                            calls=None,
                            usage_status="unsupported",
                            called_tools={},
                            verdict="unknown",
                        )
                    ],
                    window=None,
                    coverage=Coverage(
                        transcripts_found=0,
                        transcripts_parsed=0,
                        transcripts_skipped=[],
                        in_window=None,
                        total_tool_calls=None,
                        mcp_tool_calls=None,
                        servers_queried_ok=0,
                        servers_query_failed=[],
                        config_warnings=[],
                    ),
                )
            ],
            generated_note="note",
        )

        rendered = cli._render_human(report)

        self.assertIn("CALLS(window)", rendered)
        self.assertIn("alpha", rendered)
        self.assertIn("-              unknown", rendered)
        self.assertIn("usage: unknown (no transcript adapter)", rendered)

    def test_human_output_explains_unavailable_definitions(self) -> None:
        exit_code, output = self._run("--no-query")

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "  github: definitions unavailable -- skipped by --no-query",
            output,
        )

    def test_all_autodetects_claude_and_codex_with_independent_sections(self) -> None:
        self._install_codex_fixture()

        human_exit, human = self._run("--no-query")
        json_exit, json_output = self._run("--json", "--no-query")

        self.assertEqual(human_exit, 0)
        self.assertIn("=== claude-code ===", human)
        self.assertIn("=== codex ===", human)
        self.assertEqual(json_exit, 0)
        payload = json.loads(json_output)
        self.assertEqual(
            [cli_payload["cli"] for cli_payload in payload["clis"]],
            ["claude-code", "codex"],
        )
        by_cli = {cli_payload["cli"]: cli_payload for cli_payload in payload["clis"]}
        self.assertEqual(by_cli["claude-code"]["coverage"]["transcripts_found"], 2)
        self.assertEqual(by_cli["codex"]["coverage"]["transcripts_found"], 3)
        self.assertNotEqual(
            by_cli["claude-code"]["coverage"],
            by_cli["codex"]["coverage"],
        )

    def test_all_detects_claude_project_mcp_json_without_home_config(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "project")
            os.makedirs(project)
            with open(os.path.join(project, ".mcp.json"), "w", encoding="utf-8") as handle:
                json.dump({"mcpServers": {"docs": {"command": "fake"}}}, handle)

            exit_code, output = self._run_with_home_project(
                home,
                project,
                "--json",
                "--no-query",
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual([entry["cli"] for entry in payload["clis"]], ["claude-code"])

    def test_all_detects_codex_project_config_without_home_config(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "project")
            project_codex = os.path.join(project, ".codex")
            os.makedirs(project_codex)
            with open(
                os.path.join(project_codex, "config.toml"), "w", encoding="utf-8"
            ) as handle:
                handle.write("[mcp_servers.project_only]\ncommand = 'ignored'\n")

            exit_code, output = self._run_with_home_project(
                home,
                project,
                "--json",
                "--no-query",
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual([entry["cli"] for entry in payload["clis"]], ["codex"])
        warnings = payload["clis"][0]["coverage"]["config_warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("project-layer codex config", warnings[0])
        self.assertIn("project_only", warnings[0])
        self.assertIn("not queried and not merged", warnings[0])

    def test_all_without_detection_reports_note_and_empty_clis(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, "project")
            os.makedirs(project)

            human_exit, human = self._run_with_home_project(home, project)
            json_exit, json_output = self._run_with_home_project(
                home,
                project,
                "--json",
            )

        note = f"no supported CLIs detected under {home}"
        self.assertEqual(human_exit, 0)
        self.assertIn(note, human)
        self.assertEqual(json_exit, 0)
        payload = json.loads(json_output)
        self.assertEqual(payload["clis"], [])
        self.assertEqual(payload["note"], note)

    def test_zero_sessions_is_argparse_usage_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["--sessions", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("usage: mcp-top", stderr.getvalue())

    def test_days_and_timeout_reject_nonpositive_values(self) -> None:
        for option, value in (("--days", "0"), ("--timeout", "0")):
            with self.subTest(option=option):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main([option, value])
                self.assertEqual(raised.exception.code, 2)

    def test_python_version_floor_returns_2(self) -> None:
        stderr = io.StringIO()

        with mock.patch.object(cli.sys, "version_info", (3, 10, 0)):
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.main(["--version"])

        self.assertEqual(exit_code, 2)
        self.assertIn("Python 3.11 or newer", stderr.getvalue())

    def test_prune_json_is_additive_and_leaks_no_secrets(self) -> None:
        self._set_servers(
            {
                "github": {
                    "command": sys.executable,
                    "args": [FAKE_SERVER, "serve"],
                },
                "unused": {
                    "command": sys.executable,
                    "args": [FAKE_SERVER, "serve"],
                    "env": {"SECRET_TOKEN": "do-not-leak"},
                },
            }
        )

        _, plain = self._run("--json")
        plain_cli = json.loads(plain)["clis"][0]
        self.assertNotIn("suggested_removals", plain_cli)

        _, pruned = self._run("--json", "--prune")
        payload = json.loads(pruned)
        self.assertEqual(payload["schema"], "mcp-top/v3")
        cli_entry = payload["clis"][0]
        self.assertIn("suggested_removals", cli_entry)
        unused = next(
            item
            for item in cli_entry["suggested_removals"]
            if item["server"] == "unused"
        )
        self.assertEqual(unused["kind"], "suggestion")
        self.assertGreater(unused["removes_advertised_max_tokens"]["value"], 0)
        self.assertIsNotNone(unused["removes_upfront_floor_tokens"])
        self.assertIsNone(unused["reactivates"])
        # No env/args/command ever surface in a suggestion.
        self.assertEqual(
            set(unused),
            {
                "kind",
                "server",
                "scope",
                "source_path",
                "removes_advertised_max_tokens",
                "removes_upfront_floor_tokens",
                "reactivates",
                "reasons",
                "recipe",
            },
        )
        recipe = unused["recipe"]
        self.assertEqual(recipe["kind"], "command")
        self.assertEqual(
            recipe["argv"],
            ["claude", "mcp", "remove", "--scope", "user", "unused"],
        )
        self.assertEqual(
            recipe["source_path"], os.path.join(self.home, ".claude.json")
        )
        self.assertEqual(recipe["scope"], "user")
        self.assertNotIn("do-not-leak", pruned)

    def test_prune_human_flags_reactivation_candidate(self) -> None:
        self._set_servers(
            {
                "shared": {
                    "command": sys.executable,
                    "args": [FAKE_SERVER, "serve"],
                }
            },
            project_servers={
                "shared": {
                    "command": sys.executable,
                    "args": [FAKE_SERVER, "serve"],
                }
            },
        )

        _, output = self._run("--prune")

        self.assertIn("Prune candidates", output)
        self.assertIn("shared", output)
        self.assertIn("reactivates", output)
        # The table verdict must not assert a savings number for a candidate.
        self.assertIn("prune candidate", output)
        self.assertNotIn("prune -> save", output)
        self.assertIn("actual saving unknown", output)
        self.assertIn("edit:", output)

    def test_invalid_env_secret_appears_in_no_output_mode(self) -> None:
        sentinel = "sk_live_DO_NOT_LEAK"
        settings_path = os.path.join(self.home, ".claude", "settings.json")
        with open(settings_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "env": {
                        "ENABLE_TOOL_SEARCH": sentinel,
                        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": sentinel,
                    }
                },
                handle,
            )

        outputs = []
        for options in (
            ("--no-query",),
            ("--json", "--no-query"),
            ("--prune", "--no-query"),
            ("--json", "--prune", "--no-query"),
        ):
            exit_code, output = self._run(*options)
            self.assertEqual(exit_code, 0)
            outputs.append(output)

        for output in outputs:
            self.assertNotIn(sentinel, output)
        json_payload = json.loads(outputs[1])
        evidence = json_payload["clis"][0]["servers"][0]["regime_evidence"]
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(item.endswith("=unrecognized") for item in evidence))

    def test_prune_reports_cursor_usage_unavailable(self) -> None:
        cursor_dir = os.path.join(self.home, ".cursor")
        os.makedirs(cursor_dir)
        with open(
            os.path.join(cursor_dir, "mcp.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump({"mcpServers": {"c1": {"command": "x"}}}, handle)

        _, output = self._run("--prune", "--no-query")

        self.assertIn("usage unavailable", output)
        self.assertIn("cursor", output)

    def test_prune_human_shell_quotes_hostile_clean_command(self) -> None:
        suggestion = self._suggestion("bad 'name; rm -rf /")
        report = Report(
            clis=[
                CliReport(
                    cli="claude-code",
                    rows=[],
                    window=self._usage_window(),
                    coverage=self._coverage(),
                    suggestions=[suggestion],
                )
            ],
            generated_note="note",
        )

        rendered = cli._render_prune_block(report)

        self.assertIn(
            "claude mcp remove --scope user 'bad '\"'\"'name; rm -rf /'",
            rendered,
        )

    def test_candidate_recipe_is_guidance_without_argv(self) -> None:
        candidate = self._suggestion(
            "needs-review",
            kind="candidate",
            reasons=["reactivates another scope"],
        )

        payload = cli._suggestion_json("claude-code", candidate)

        self.assertEqual(payload["recipe"]["kind"], "guidance")
        self.assertNotIn("argv", payload["recipe"])
        self.assertIn("source_path", payload["recipe"])
        self.assertIn("scope", payload["recipe"])

    def test_codex_recipe_targets_exact_toml_table(self) -> None:
        suggestion = self._suggestion(
            'space "quote"; semi',
            source_path="/home/me/.codex/config.toml",
        )

        recipe = cli._remediation_recipe("codex", suggestion)

        self.assertEqual(recipe["kind"], "command")
        self.assertEqual(recipe["source_path"], "/home/me/.codex/config.toml")
        self.assertEqual(recipe["scope"], "user")
        self.assertEqual(recipe["argv"][0:2], ["python", "-c"])
        self.assertNotIn("\n", recipe["argv"][2])
        self.assertEqual(
            recipe["argv"][-1],
            '[mcp_servers."space \\"quote\\"; semi"]',
        )
        self.assertIn("enabled = false", recipe["change"])

    def test_cursor_recipe_names_exact_mcp_json_for_guidance(self) -> None:
        candidate = self._suggestion(
            "cursor-docs",
            kind="candidate",
            source_path="/repo/.cursor/mcp.json",
            scope="project",
            reasons=["usage unavailable"],
        )

        recipe = cli._remediation_recipe("cursor", candidate)

        self.assertEqual(recipe["kind"], "guidance")
        self.assertNotIn("argv", recipe)
        self.assertIn("/repo/.cursor/mcp.json", recipe["change"])
        self.assertIn("project scope", recipe["change"])

    def _set_servers(self, servers: dict, project_servers: dict | None = None) -> None:
        with open(
            os.path.join(self.home, ".claude.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump({"mcpServers": servers}, handle)
        if project_servers is not None:
            with open(
                os.path.join(self.project, ".mcp.json"), "w", encoding="utf-8"
            ) as handle:
                json.dump({"mcpServers": project_servers}, handle)

    def _run(self, *extra: str) -> tuple[int, str]:
        return self._run_with_home_project(self.home, self.project, *extra)

    def _run_with_home_project(
        self, home: str, project: str, *extra: str
    ) -> tuple[int, str]:
        output = io.StringIO()
        args = [
            "--home",
            home,
            "--project",
            project,
            "--days",
            "3650",
            *extra,
        ]
        with contextlib.redirect_stdout(output):
            exit_code = cli.main(args)
        return exit_code, output.getvalue()

    def _install_codex_fixture(self) -> None:
        codex_dir = os.path.join(self.home, ".codex")
        os.makedirs(codex_dir)
        shutil.copyfile(
            os.path.join(FIXTURES, "codex_config.toml"),
            os.path.join(codex_dir, "config.toml"),
        )
        session_dir = os.path.join(codex_dir, "sessions", "2026", "06", "10")
        os.makedirs(session_dir)
        source_dir = os.path.join(FIXTURES, "codex_sessions")
        for filename in os.listdir(source_dir):
            shutil.copyfile(
                os.path.join(source_dir, filename),
                os.path.join(session_dir, filename),
            )

    def _suggestion(
        self,
        server: str,
        *,
        kind: str = "suggestion",
        source_path: str = "~/.claude.json",
        scope: str = "user",
        reasons: list[str] | None = None,
    ) -> PruneSuggestion:
        return PruneSuggestion(
            kind=kind,
            server=server,
            scope=scope,
            source_path=source_path,
            removes_advertised_max_tokens=TokenCount(10, False),
            removes_upfront_floor_tokens=TokenCount(5, False),
            reactivates=None,
            reasons=[] if reasons is None else reasons,
        )

    def _golden_report(self) -> Report:
        coverage = Coverage(
            transcripts_found=2,
            transcripts_parsed=2,
            transcripts_skipped=[],
            in_window=2,
            total_tool_calls=4,
            mcp_tool_calls=4,
            servers_queried_ok=2,
            servers_query_failed=[],
            config_warnings=[],
        )
        rows = [
            ServerRow(
                server="alpha",
                scope="user",
                transport="stdio",
                advertised_max_tokens=TokenCount(20, False),
                upfront_floor_tokens=TokenCount(8, False),
                loading_regime="deferred",
                regime_evidence=[
                    "/home/test/.claude/settings.json:env.ENABLE_TOOL_SEARCH=true"
                ],
                def_status="ok",
                def_error=None,
                tool_count=2,
                calls=0,
                usage_status="measured",
                called_tools={},
                verdict="prune",
            ),
            ServerRow(
                server="beta",
                scope="project",
                transport="http",
                advertised_max_tokens=TokenCount(12, False),
                upfront_floor_tokens=TokenCount(12, False),
                loading_regime="upfront",
                regime_evidence=[
                    "/home/test/.claude/settings.json:env."
                    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=set"
                ],
                def_status="ok",
                def_error=None,
                tool_count=1,
                calls=0,
                usage_status="measured",
                called_tools={},
                verdict="prune",
            ),
        ]
        suggestions = [
            PruneSuggestion(
                kind="suggestion",
                server="alpha",
                scope="user",
                source_path="/home/test/.claude.json",
                removes_advertised_max_tokens=TokenCount(20, False),
                removes_upfront_floor_tokens=TokenCount(8, False),
                reactivates=None,
                reasons=[],
            ),
            PruneSuggestion(
                kind="candidate",
                server="beta",
                scope="project",
                source_path="/repo/.mcp.json",
                removes_advertised_max_tokens=TokenCount(12, False),
                removes_upfront_floor_tokens=TokenCount(12, False),
                reactivates=None,
                reasons=["usage is not attributed per-project; verify before removing"],
            ),
        ]
        return Report(
            clis=[
                CliReport(
                    cli="claude-code",
                    rows=rows,
                    window=self._usage_window(sessions_considered=2),
                    coverage=coverage,
                    suggestions=suggestions,
                )
            ],
            generated_note=(
                "Definition token counts use the chars/4 heuristic; ~ means estimate."
            ),
        )

    def _read_golden(self, name: str) -> str:
        with open(os.path.join(GOLDENS, name), "r", encoding="utf-8") as handle:
            return handle.read()

    def _usage_window(self, sessions_considered: int = 1) -> UsageWindow:
        return UsageWindow(
            sessions_considered=sessions_considered,
            window_sessions=30,
            window_days=30,
            counts={},
            sidechain_counts={},
            server_tool_counts={},
            unattributed_mcp_calls=0,
        )

    def _coverage(self) -> Coverage:
        return Coverage(
            transcripts_found=0,
            transcripts_parsed=0,
            transcripts_skipped=[],
            in_window=None,
            total_tool_calls=None,
            mcp_tool_calls=None,
            servers_queried_ok=0,
            servers_query_failed=[],
            config_warnings=[],
        )


if __name__ == "__main__":
    unittest.main()
