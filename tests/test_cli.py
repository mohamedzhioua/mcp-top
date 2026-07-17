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
from mcp_top.engine import CliReport, Report, ServerRow


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
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
        self.assertEqual(payload["schema"], "mcp-top/v2")
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
        self.assertIsNotNone(server["def_tokens"])
        self.assertFalse(server["def_tokens"]["exact"])
        self.assertEqual(server["calls"], 2)
        self.assertEqual(server["usage_status"], "measured")
        self.assertEqual(server["verdict"], "review")
        self.assertEqual(server["filtered_tools"], 0)

    def test_human_output_starts_with_coverage_then_table(self) -> None:
        exit_code, output = self._run()

        self.assertEqual(exit_code, 0)
        self.assertTrue(output.startswith("Coverage:"))
        self.assertIn("SERVER", output)
        self.assertIn("DEF TOKENS", output)
        self.assertLess(output.index("Coverage:"), output.index("SERVER"))

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
        self.assertIsNone(server["def_tokens"])

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
                            def_tokens=None,
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
                        usage_note="no transcript adapter for this CLI in v0.2",
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
                            def_tokens=None,
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


if __name__ == "__main__":
    unittest.main()
