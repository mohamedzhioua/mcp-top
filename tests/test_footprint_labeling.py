"""Guards for honest recorded-result footprint labels."""

from __future__ import annotations

import unittest
import json

import _path  # noqa: F401

from mcp_top import cli
from mcp_top.coverage import Coverage, RecordedResultsCoverage
from mcp_top.counter import UsageWindow
from mcp_top.engine import CliReport, RecordedResultFootprint, Report, ServerRow
from mcp_top.tokens import TokenCount


FORBIDDEN = [
    "context cost",
    "prompt token",
    "billing",
    "saving",
    "what the model saw",
]


class FootprintLabelingTests(unittest.TestCase):
    def test_footprint_surfaces_use_mandated_label_without_forbidden_terms(
        self,
    ) -> None:
        report = Report(
            clis=[
                CliReport(
                    cli="claude-code",
                    rows=[
                        ServerRow(
                            server="github",
                            scope="user",
                            transport="stdio",
                            advertised_max_tokens=TokenCount(10, False),
                            upfront_floor_tokens=TokenCount(5, False),
                            loading_regime="unknown",
                            regime_evidence=[],
                            def_status="ok",
                            def_error=None,
                            tool_count=1,
                            calls=1,
                            usage_status="measured",
                            called_tools={"get_pr": 1},
                            verdict="review",
                            recorded_result_footprint=RecordedResultFootprint(
                                results=1,
                                lower_bound=True,
                                basis="recorded_utf8_bytes",
                                token_estimate="bytes/4",
                                total_bytes=9,
                                total_tokens=TokenCount(3, False),
                                max_tokens=TokenCount(3, False),
                                p50_tokens=TokenCount(3, False),
                                p90_tokens=TokenCount(3, False),
                            ),
                        )
                    ],
                    window=UsageWindow(
                        sessions_considered=1,
                        window_sessions=30,
                        window_days=30,
                        counts={"mcp__github__get_pr": 1},
                        sidechain_counts={},
                        server_tool_counts={"github": {"get_pr": 1}},
                        server_result_bytes={"github": [9]},
                        results_paired=1,
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
                        recorded_results=RecordedResultsCoverage(paired=1),
                    ),
                )
            ],
            generated_note="Definition token counts use the chars/4 heuristic; ~ means estimate.",
        )

        human = cli._render_human(report)
        payload = cli._report_json(report)
        serialized = json.dumps(payload, sort_keys=True)
        help_text = cli._parser().format_help()

        self.assertIn("recorded result footprint", human)
        self.assertIn("recorded UTF-8 byte", human)
        self.assertIn("lower bound", human)
        self.assertIn("recorded_result_footprint", serialized)
        self.assertIn("lower_bound", serialized)
        self.assertIn("recorded_utf8_bytes", serialized)
        self.assertIn("bytes/4", serialized)
        self.assertEqual(payload["schema"], "mcp-top/v3")
        self.assertIn("~3 tok", human)
        for surface in (human, serialized, help_text):
            lowered = surface.lower()
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
