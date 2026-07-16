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
                        in_window=0,
                        total_tool_calls=0,
                        mcp_tool_calls=0,
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
                        in_window=0,
                        total_tool_calls=0,
                        mcp_tool_calls=0,
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

    def test_human_output_explains_unavailable_definitions(self) -> None:
        exit_code, output = self._run("--no-query")

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "  github: definitions unavailable — skipped by --no-query",
            output,
        )

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
        output = io.StringIO()
        args = [
            "--home",
            self.home,
            "--project",
            self.project,
            "--days",
            "3650",
            *extra,
        ]
        with contextlib.redirect_stdout(output):
            exit_code = cli.main(args)
        return exit_code, output.getvalue()


if __name__ == "__main__":
    unittest.main()
