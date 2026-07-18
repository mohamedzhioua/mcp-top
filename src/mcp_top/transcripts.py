"""Neutral transcript data model shared by CLI adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ToolCall:
    """A tool invocation recorded in an assistant transcript."""

    raw: str
    kind: str
    server: str | None
    tool: str | None
    timestamp: str
    sidechain: bool = False
    result_bytes: int | None = None
    result_kind: str | None = None


@dataclass(frozen=True)
class RecordedResult:
    """One transcript result payload after adapter-specific extraction."""

    result_id: str | None
    byte_count: int
    kind: str


@dataclass(frozen=True)
class ResultPairing:
    """Aggregate facts from pairing recorded results back to tool calls."""

    unpaired_results: int = 0
    unsupported_results: int = 0
    unmeasurable_results: int = 0


@dataclass
class SessionResult:
    """The parsed contents and coverage status of one transcript."""

    path: str
    session_id: str | None
    status: str
    skip_reason: str | None
    versions_seen: list[str]
    tool_calls: list[ToolCall]
    first_ts: str | None
    last_ts: str | None
    bad_lines: int = 0
    duplicate_tool_use: int = 0
    project: str | None = None
    raw_cwd: str | None = None
    unpaired_results: int = 0
    unsupported_results: int = 0
    unmeasurable_results: int = 0


@dataclass
class JsonlReadResult:
    """Parsed JSONL records plus non-fatal line damage accounting."""

    records: list[dict[str, Any]]
    non_empty_lines: int
    bad_lines: int
    error: str | None = None


def read_jsonl_records(path: str) -> JsonlReadResult:
    """Read JSONL records, counting malformed non-empty lines."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeError) as err:
        return JsonlReadResult([], 0, 0, f"unreadable: {err}")

    records: list[dict[str, Any]] = []
    non_empty_lines = 0
    bad_lines = 0
    for line in lines:
        if not line.strip():
            continue
        non_empty_lines += 1
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
            bad_lines += 1
            continue
        if not isinstance(record, dict):
            bad_lines += 1
            continue
        records.append(record)

    return JsonlReadResult(records, non_empty_lines, bad_lines)


def parse_timestamp(value: str) -> datetime | None:
    """Parse a transcript timestamp as UTC, accepting Claude's Z suffix."""

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def measured_utf8_bytes(text: str) -> tuple[int, bool]:
    """Return UTF-8 byte length, or a false signal for malformed text."""

    try:
        return len(text.encode("utf-8")), True
    except UnicodeEncodeError:
        return 0, False


def pair_results(
    calls_by_id: dict[str, ToolCall],
    collision_ids: set[str],
    results: list[RecordedResult],
) -> ResultPairing:
    """Pair results after transcript parsing.

    This intentionally does not enforce causal ordering. Transcript footprint is
    informational and real-world call IDs are expected to be unique; ambiguous
    duplicate IDs and second results are counted as unpaired instead.
    """

    unpaired = 0
    unsupported = 0
    unmeasurable = 0
    for result in results:
        call = (
            calls_by_id.get(result.result_id)
            if result.result_id is not None and result.result_id not in collision_ids
            else None
        )
        if call is None or call.result_kind is not None:
            unpaired += 1
            continue
        call.result_bytes = result.byte_count
        call.result_kind = result.kind
        if result.kind == "unsupported":
            unsupported += 1
        elif result.kind == "unmeasurable":
            unmeasurable += 1
    return ResultPairing(
        unpaired_results=unpaired,
        unsupported_results=unsupported,
        unmeasurable_results=unmeasurable,
    )
