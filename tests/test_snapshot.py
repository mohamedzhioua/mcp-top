"""Tests for redacted snapshots and semantic snapshot diffs."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import unittest

import _path  # noqa: F401

from mcp_top.config import ServerConfig
from mcp_top.counter import UsageWindow
from mcp_top.coverage import Coverage, RecordedResultsCoverage
from mcp_top.engine import (
    CliReport,
    RecordedResultFootprint,
    Report,
    ServerRow,
    build_cli_report,
)
from mcp_top.mcpclient import ServerTools
from mcp_top.snapshot import (
    SNAPSHOT_SCHEMA,
    build_snapshot,
    diff_snapshots,
    load_snapshot_file,
    render_diff_human,
    snapshot_to_dict,
)
from mcp_top.tokens import TokenCount
from mcp_top.transcripts import SessionResult


GOLDENS = os.path.join(os.path.dirname(__file__), "goldens")


class SnapshotTests(unittest.TestCase):
    def test_snapshot_schema_matches_golden(self) -> None:
        snapshot = snapshot_to_dict(
            build_snapshot(self._report(), self._generated_at())
        )

        self.assertEqual(
            json.dumps(snapshot, sort_keys=True),
            self._read_golden("snapshot_v1.json").strip(),
        )

    def test_redaction_guard_excludes_config_paths_errors_and_results(self) -> None:
        server_name = "/home/alice/.ssh/id_ed25519"
        tool_name = "sk_live_TOOL_DO_NOT_LEAK"
        server = ServerConfig(
            name=server_name,
            scope="user",
            source_path="/home/alice/.claude.json",
            transport="stdio",
            command="/home/alice/bin/srv",
            args=["--secret", "xyz"],
            env={"TOKEN": "do-not-leak"},
            url="https://user:pw@host",
            loading_regime="deferred",
            regime_evidence=[
                "/home/alice/.claude/settings.json:env.ENABLE_TOOL_SEARCH=true"
            ],
        )
        report = build_cli_report(
            "claude-code",
            [server],
            [
                ServerTools(
                    server=server_name,
                    status="error",
                    error="RAW_RESULT_CONTENT_DO_NOT_LEAK",
                    tools=[],
                )
            ],
            [
                SessionResult(
                    path="/home/alice/.claude/projects/p/session.jsonl",
                    session_id=None,
                    status="skipped",
                    skip_reason="RAW_RESULT_CONTENT_DO_NOT_LEAK",
                    versions_seen=[],
                    tool_calls=[],
                    first_ts=None,
                    last_ts=None,
                )
            ],
            UsageWindow(
                sessions_considered=1,
                window_sessions=30,
                window_days=30,
                counts={},
                sidechain_counts={},
                server_tool_counts={server_name: {tool_name: 1}},
                server_result_bytes={server_name: [128]},
                results_paired=1,
            ),
            config_warnings=["/home/alice/.claude.json: do-not-leak"],
        )

        rendered = json.dumps(
            snapshot_to_dict(build_snapshot(Report([report], "note"), self._generated_at())),
            sort_keys=True,
        )

        self.assertIn(server_name, rendered)
        self.assertIn(tool_name, rendered)
        for sentinel in (
            "do-not-leak",
            "--secret",
            "/home/alice/.claude.json",
            "/home/alice/bin/srv",
            "user:pw",
            "/home/alice/.claude/settings.json",
            "RAW_RESULT_CONTENT_DO_NOT_LEAK",
        ):
            self.assertNotIn(sentinel, rendered)

    def test_redact_identifiers_hashes_server_and_tool_names(self) -> None:
        server_name = "/home/alice/.ssh/id_ed25519"
        tool_name = "sk_live_TOOL_DO_NOT_LEAK"
        report = self._hostile_identifier_report(server_name, tool_name)
        first = snapshot_to_dict(
            build_snapshot(
                report,
                self._generated_at(),
                redact_identifiers=True,
            )
        )
        second = snapshot_to_dict(
            build_snapshot(
                report,
                self._generated_at(),
                redact_identifiers=True,
            )
        )

        rendered = json.dumps(first, sort_keys=True)
        self.assertTrue(first["identifiers_redacted"])
        self.assertNotIn(server_name, rendered)
        self.assertNotIn(tool_name, rendered)
        server_hash = "srv_" + hashlib.sha256(
            server_name.encode("utf-8")
        ).hexdigest()[:12]
        tool_hash = "tool_" + hashlib.sha256(
            tool_name.encode("utf-8")
        ).hexdigest()[:12]
        server = first["clis"][0]["servers"][0]
        self.assertEqual(server["server"], server_hash)
        self.assertEqual(list(server["called_tools"]), [tool_hash])
        self.assertEqual(first, second)

    def test_diff_reports_add_remove_change_and_unchanged(self) -> None:
        diff = diff_snapshots(
            self._loaded_snapshot(self._old_snapshot()),
            self._loaded_snapshot(self._new_snapshot()),
        )

        self.assertEqual(diff["schema"], "mcp-top-diff/v1")
        claude = next(item for item in diff["clis"] if item["cli"] == "claude-code")
        self.assertEqual(claude["added"], ["gamma"])
        self.assertEqual(claude["removed"], ["alpha"])
        self.assertEqual(claude["unchanged"], 1)
        beta = claude["changed"][0]
        self.assertEqual(beta["server"], "beta")
        self.assertEqual(beta["advertised_max_tokens"]["delta"], 5)
        self.assertTrue(beta["advertised_max_tokens"]["estimated"])
        self.assertEqual(beta["upfront_floor_tokens"]["delta"], -2)
        self.assertEqual(beta["calls"]["delta"], -3)
        self.assertEqual(beta["recorded_result_total_tokens"]["delta"], -8)
        self.assertEqual(beta["loading_regime"]["old"], "unknown")
        self.assertEqual(beta["loading_regime"]["new"], "upfront")
        self.assertEqual(beta["verdict"]["old"], "review")
        self.assertEqual(beta["verdict"]["new"], "keep")
        codex = next(item for item in diff["clis"] if item["cli"] == "codex")
        self.assertEqual(codex["removed"], ["old-only"])
        cursor = next(item for item in diff["clis"] if item["cli"] == "cursor")
        self.assertEqual(cursor["added"], ["new-only"])

        human = render_diff_human(diff)

        self.assertIn("=== claude-code ===", human)
        self.assertIn("added: gamma", human)
        self.assertIn("removed: alpha", human)
        self.assertIn("advertised_max_tokens: ~10 -> ~15 (+5, estimate)", human)
        self.assertIn("upfront_floor_tokens: 5 -> 3 (-2)", human)
        self.assertIn("calls: 4 -> 1 (-3)", human)
        self.assertIn("recorded_result_total_tokens: ~20 -> ~12 (-8, estimate)", human)
        self.assertIn("loading_regime: unknown -> upfront", human)
        self.assertIn("verdict: review -> keep", human)
        self.assertIn("unchanged: 1 server(s)", human)

    def test_diff_marks_usage_incomparable_when_window_scope_differs(self) -> None:
        old_payload = self._old_snapshot()
        new_payload = copy.deepcopy(old_payload)
        old_payload["clis"][0]["window"] = {
            "sessions": 30,
            "days": 30,
            "sessions_considered": 30,
            "project_filter_active": False,
        }
        new_payload["generated_at"] = "2026-01-02T00:00:00+00:00"
        new_payload["clis"][0]["window"] = {
            "sessions": 1,
            "days": 1,
            "sessions_considered": 1,
            "project_filter_active": True,
        }
        old_beta = old_payload["clis"][0]["servers"][1]
        new_beta = new_payload["clis"][0]["servers"][1]
        new_beta["advertised_max_tokens"]["value"] = 15
        new_beta["calls"] = 1
        new_beta["recorded_result_footprint"]["total_tokens"]["value"] = 12

        diff = diff_snapshots(
            self._loaded_snapshot(old_payload),
            self._loaded_snapshot(new_payload),
        )

        claude = next(item for item in diff["clis"] if item["cli"] == "claude-code")
        beta = next(item for item in claude["changed"] if item["server"] == "beta")
        self.assertEqual(beta["advertised_max_tokens"]["delta"], 5)
        self.assertTrue(beta["calls"]["incomparable"])
        self.assertNotIn("delta", beta["calls"])
        self.assertTrue(beta["recorded_result_total_tokens"]["incomparable"])
        self.assertNotIn("delta", beta["recorded_result_total_tokens"])
        self.assertIsNotNone(claude["window"])
        self.assertEqual(claude["sessions_considered"]["delta"], -29)

        human = render_diff_human(diff)
        self.assertIn(
            "window: 30 session(s), 30 day(s), home-wide -> "
            "1 session(s), 1 day(s), project-filtered",
            human,
        )
        self.assertIn("sessions_considered: 30 -> 1 (-29)", human)
        self.assertIn("calls: 4 -> 1 (population/scope differs)", human)
        self.assertIn(
            "recorded_result_total_tokens: ~20 -> ~12 "
            "(population/scope differs)",
            human,
        )

    def test_load_snapshot_rejects_bad_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            missing = os.path.join(tempdir, "missing.json")
            with self.assertRaisesRegex(Exception, "file not found"):
                load_snapshot_file(missing)

            invalid = os.path.join(tempdir, "invalid.json")
            with open(invalid, "w", encoding="utf-8") as handle:
                handle.write("{")
            with self.assertRaisesRegex(Exception, "invalid JSON"):
                load_snapshot_file(invalid)

            wrong = os.path.join(tempdir, "wrong.json")
            with open(wrong, "w", encoding="utf-8") as handle:
                json.dump({"schema": "wrong"}, handle)
            with self.assertRaisesRegex(Exception, SNAPSHOT_SCHEMA):
                load_snapshot_file(wrong)

    def test_load_snapshot_rejects_strict_validation_errors(self) -> None:
        cases = [
            ("missing clis", lambda payload: payload.pop("clis")),
            ("clis null", lambda payload: payload.__setitem__("clis", None)),
            (
                "wrong type",
                lambda payload: payload["clis"][0]["window"].__setitem__(
                    "sessions", "30"
                ),
            ),
            (
                "duplicate server",
                lambda payload: payload["clis"][0]["servers"].append(
                    copy.deepcopy(payload["clis"][0]["servers"][0])
                ),
            ),
            (
                "negative count",
                lambda payload: payload["clis"][0]["servers"][0][
                    "called_tools"
                ].__setitem__("bad", -1),
            ),
            (
                "bool as int",
                lambda payload: payload["clis"][0]["servers"][0][
                    "advertised_max_tokens"
                ].__setitem__("value", True),
            ),
        ]
        with tempfile.TemporaryDirectory() as tempdir:
            for name, mutate in cases:
                with self.subTest(name=name):
                    payload = copy.deepcopy(self._old_snapshot())
                    mutate(payload)
                    path = os.path.join(tempdir, f"{name}.json")
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle)
                    with self.assertRaises(Exception):
                        load_snapshot_file(path)

    def test_load_snapshot_rejects_unknown_token_keys_without_values(self) -> None:
        secret = "sk_live_DO_NOT_REPLAY"
        payload = copy.deepcopy(self._old_snapshot())
        payload["clis"][0]["servers"][0]["advertised_max_tokens"][
            "source_path"
        ] = secret
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, "crafted.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            with self.assertRaises(Exception) as raised:
                load_snapshot_file(path)

        self.assertNotIn(secret, str(raised.exception))

    def _report(self) -> Report:
        return Report(
            clis=[
                CliReport(
                    cli="codex",
                    rows=[
                        ServerRow(
                            server="beta",
                            scope="project",
                            transport="http",
                            advertised_max_tokens=None,
                            upfront_floor_tokens=None,
                            loading_regime="unknown",
                            regime_evidence=["/repo/.codex/config.toml"],
                            def_status="unsupported",
                            def_error="unsupported transport",
                            tool_count=None,
                            calls=0,
                            usage_status="measured",
                            called_tools={},
                            verdict="review",
                        ),
                        ServerRow(
                            server="alpha",
                            scope="user",
                            transport="stdio",
                            advertised_max_tokens=TokenCount(10, False),
                            upfront_floor_tokens=TokenCount(4, False),
                            loading_regime="deferred",
                            regime_evidence=["/home/test/.codex/config.toml"],
                            def_status="ok",
                            def_error=None,
                            tool_count=2,
                            calls=2,
                            usage_status="measured",
                            called_tools={"search": 2},
                            verdict="review",
                            recorded_result_footprint=RecordedResultFootprint(
                                results=2,
                                lower_bound=True,
                                basis="recorded_utf8_bytes",
                                token_estimate="bytes/4",
                                total_bytes=20,
                                total_tokens=TokenCount(5, False),
                                max_tokens=TokenCount(3, False),
                                p50_tokens=TokenCount(2, False),
                                p90_tokens=TokenCount(3, False),
                            ),
                        ),
                    ],
                    window=UsageWindow(
                        sessions_considered=2,
                        window_sessions=7,
                        window_days=14,
                        counts={},
                        sidechain_counts={},
                        project_filter="project-key",
                    ),
                    coverage=Coverage(
                        transcripts_found=3,
                        transcripts_parsed=2,
                        transcripts_skipped=[("/tmp/session.jsonl", "bad")],
                        in_window=2,
                        total_tool_calls=2,
                        mcp_tool_calls=2,
                        servers_queried_ok=1,
                        servers_query_failed=[("beta", "bad")],
                        config_warnings=["/home/test/.codex/config.toml"],
                        servers_unsupported=1,
                        recorded_results=RecordedResultsCoverage(
                            paired=1,
                            partial=1,
                            unsupported=2,
                            unmeasurable=3,
                            unpaired_results=4,
                            unpaired_calls=5,
                        ),
                    ),
                )
            ],
            generated_note="note",
        )

    def _old_snapshot(self) -> dict:
        return {
            "schema": SNAPSHOT_SCHEMA,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "identifiers_redacted": False,
            "clis": [
                {
                    "cli": "claude-code",
                    "window": self._window(),
                    "recorded_results": None,
                    "servers": [
                        self._server("alpha", 1, 1, 0, "unknown", "prune", 0),
                        self._server("beta", 10, 5, 4, "unknown", "review", 20),
                        self._server("stable", 7, 2, 1, "deferred", "review", 8),
                    ],
                },
                {
                    "cli": "codex",
                    "window": self._window(),
                    "recorded_results": None,
                    "servers": [
                        self._server("old-only", 2, 1, 0, "unknown", "prune", 0)
                    ],
                },
            ],
        }

    def _new_snapshot(self) -> dict:
        return {
            "schema": SNAPSHOT_SCHEMA,
            "generated_at": "2026-01-02T00:00:00+00:00",
            "identifiers_redacted": False,
            "clis": [
                {
                    "cli": "claude-code",
                    "window": self._window(),
                    "recorded_results": None,
                    "servers": [
                        self._server("beta", 15, 3, 1, "upfront", "keep", 12),
                        self._server("gamma", 2, 1, 0, "unknown", "prune", 0),
                        self._server("stable", 7, 2, 1, "deferred", "review", 8),
                    ],
                },
                {
                    "cli": "cursor",
                    "window": self._window(),
                    "recorded_results": None,
                    "servers": [
                        self._server("new-only", 2, 1, 0, "unknown", "review", 0)
                    ],
                },
            ],
        }

    def _server(
        self,
        name: str,
        advertised: int,
        upfront: int,
        calls: int,
        regime: str,
        verdict: str,
        result_tokens: int,
    ) -> dict:
        footprint = None
        if result_tokens:
            footprint = {
                "results": 1,
                "total_bytes": result_tokens * 4,
                "total_tokens": {"value": result_tokens, "exact": False},
                "max_tokens": {"value": result_tokens, "exact": False},
                "p50_tokens": {"value": result_tokens, "exact": False},
                "p90_tokens": {"value": result_tokens, "exact": False},
                "lower_bound": True,
                "basis": "recorded_utf8_bytes",
                "token_estimate": "bytes/4",
            }
        return {
            "server": name,
            "scope": "user",
            "transport": "stdio",
            "loading_regime": regime,
            "advertised_max_tokens": {"value": advertised, "exact": False},
            "upfront_floor_tokens": {"value": upfront, "exact": True},
            "tool_count": 1,
            "calls": calls,
            "usage_status": "measured",
            "verdict": verdict,
            "called_tools": {},
            "recorded_result_footprint": footprint,
        }

    def _window(self) -> dict:
        return {
            "sessions": 30,
            "days": 30,
            "sessions_considered": 1,
            "project_filter_active": False,
        }

    def _generated_at(self) -> datetime:
        return datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    def _loaded_snapshot(self, payload: dict):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, "snapshot.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            return load_snapshot_file(path)

    def _hostile_identifier_report(self, server_name: str, tool_name: str) -> Report:
        return Report(
            clis=[
                CliReport(
                    cli="claude-code",
                    rows=[
                        ServerRow(
                            server=server_name,
                            scope="user",
                            transport="stdio",
                            advertised_max_tokens=TokenCount(10, False),
                            upfront_floor_tokens=TokenCount(4, False),
                            loading_regime="deferred",
                            regime_evidence=["/home/alice/.claude/settings.json"],
                            def_status="ok",
                            def_error=None,
                            tool_count=1,
                            calls=1,
                            usage_status="measured",
                            called_tools={tool_name: 1},
                            verdict="review",
                        )
                    ],
                    window=UsageWindow(
                        sessions_considered=1,
                        window_sessions=30,
                        window_days=30,
                        counts={},
                        sidechain_counts={},
                        server_tool_counts={server_name: {tool_name: 1}},
                    ),
                    coverage=Coverage(
                        transcripts_found=1,
                        transcripts_parsed=1,
                        transcripts_skipped=[],
                        in_window=1,
                        total_tool_calls=1,
                        mcp_tool_calls=1,
                        servers_queried_ok=1,
                        servers_query_failed=[],
                        config_warnings=[],
                    ),
                )
            ],
            generated_note="note",
        )

    def _read_golden(self, name: str) -> str:
        with open(os.path.join(GOLDENS, name), "r", encoding="utf-8") as handle:
            return handle.read()


if __name__ == "__main__":
    unittest.main()
