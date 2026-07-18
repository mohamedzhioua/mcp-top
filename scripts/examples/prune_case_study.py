#!/usr/bin/env python3
"""Build and run the reproducible mcp-top prune case study.

The script uses a fixed directory under the OS temp directory and deletes it at
the start of each run. It invokes the current checkout via ``python -m mcp_top``
with ``PYTHONPATH=src`` so readers do not need an installed console script.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


CASE_ROOT = Path(tempfile.gettempdir()) / "mcp-top-prune-case-study"


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    src = repo / "src"
    fake_server = repo / "tests" / "fixtures" / "fake_mcp_server.py"
    home = CASE_ROOT / "home"
    project = CASE_ROOT / "workspace" / "demo-project"
    output_dir = CASE_ROOT / "outputs"
    before = output_dir / "before.json"
    after = output_dir / "after.json"

    if CASE_ROOT.exists():
        shutil.rmtree(CASE_ROOT)
    output_dir.mkdir(parents=True)
    project.mkdir(parents=True)
    build_home(home, project, fake_server, src)

    common = [
        "--home",
        str(home),
        "--project",
        str(project),
        "--days",
        "3650",
        "--timeout",
        "5",
        "--cli",
        "claude-code",
    ]

    print(f"Case root: {CASE_ROOT}")
    print("The script runs the current checkout as: PYTHONPATH=src python -m mcp_top")
    print()

    run_mcp_top(repo, src, [*common, "--json", "snapshot", "--out", str(before)])
    report = run_mcp_top(repo, src, [*common, "--prune"], capture=True)
    write_output(output_dir / "report.txt", report)
    print(report, end="" if report.endswith("\n") else "\n")

    prune_unused_server(home)
    print("\nRemoved unused-server from the temporary Claude config.")
    print()

    run_mcp_top(repo, src, [*common, "--json", "snapshot", "--out", str(after)])
    diff = run_mcp_top(repo, src, ["diff", str(before), str(after)], capture=True)
    write_output(output_dir / "diff.txt", diff)
    print(diff, end="" if diff.endswith("\n") else "\n")

    summary = advertised_surface_summary(before, after)
    write_output(output_dir / "advertised-surface.txt", summary)
    print()
    print(summary, end="" if summary.endswith("\n") else "\n")
    print(f"\nCaptured files: {output_dir}")
    return 0


def build_home(home: Path, project: Path, fake_server: Path, src: Path) -> None:
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    config = {
        "mcpServers": {
            "used-server": {
                "command": sys.executable,
                "args": [str(fake_server), "serve"],
            },
            "unused-server": {
                "command": sys.executable,
                "args": [str(fake_server), "serve"],
            },
        }
    }
    (home / ".claude.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    sys.path.insert(0, str(src))
    from mcp_top.projects import claude_project_slug

    slug = claude_project_slug(os.path.normpath(os.path.abspath(project)))
    transcript_dir = claude_dir / "projects" / slug
    transcript_dir.mkdir(parents=True)
    write_jsonl(
        transcript_dir / "used-session.jsonl",
        [
            {
                "type": "user",
                "version": "2.1.211",
                "timestamp": "2026-07-17T10:00:00Z",
                "sessionId": "case-used",
                "message": {"role": "user", "content": "Use one MCP server."},
            },
            {
                "type": "assistant",
                "version": "2.1.211",
                "timestamp": "2026-07-17T10:01:00Z",
                "sessionId": "case-used",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_used_1",
                            "name": "mcp__used-server__first_tool",
                            "input": {},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_used_2",
                            "name": "mcp__used-server__second_tool",
                            "input": {"value": "demo"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "version": "2.1.211",
                "timestamp": "2026-07-17T10:01:05Z",
                "sessionId": "case-used",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_used_1",
                            "content": "alpha result bytes",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_used_2",
                            "content": "second result bytes",
                        },
                    ],
                },
            },
        ],
    )
    write_jsonl(
        transcript_dir / "builtin-session.jsonl",
        [
            {
                "type": "user",
                "version": "2.1.211",
                "timestamp": "2026-07-16T09:00:00Z",
                "sessionId": "case-builtin",
                "message": {"role": "user", "content": "No MCP call here."},
            },
            {
                "type": "assistant",
                "version": "2.1.211",
                "timestamp": "2026-07-16T09:01:00Z",
                "sessionId": "case-builtin",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_builtin_1",
                            "name": "Bash",
                            "input": {"command": "pwd"},
                        }
                    ],
                },
            },
        ],
    )


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def run_mcp_top(
    repo: Path, src: Path, args: list[str], *, capture: bool = False
) -> str:
    display = "mcp-top " + " ".join(shell_token(arg) for arg in args)
    print(f"$ {display}")
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(src) if not existing else os.pathsep.join([str(src), existing])
    completed = subprocess.run(
        [sys.executable, "-m", "mcp_top", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if capture:
        return completed.stdout
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    return completed.stdout


def prune_unused_server(home: Path) -> None:
    config_path = home / ".claude.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcpServers"].pop("unused-server")
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def advertised_surface_summary(before: Path, after: Path) -> str:
    before_payload = json.loads(before.read_text(encoding="utf-8"))
    after_payload = json.loads(after.read_text(encoding="utf-8"))
    before_total = advertised_total(before_payload)
    after_total = advertised_total(after_payload)
    delta = after_total - before_total
    return (
        "Advertised surface summary\n"
        f"  before advertised_max_tokens total: ~{before_total}\n"
        f"  after advertised_max_tokens total: ~{after_total}\n"
        f"  delta: {delta}\n"
        "  removed server: unused-server\n"
    )


def advertised_total(payload: dict) -> int:
    total = 0
    for cli in payload["clis"]:
        for server in cli["servers"]:
            token = server["advertised_max_tokens"]
            if token is not None:
                total += token["value"]
    return total


def write_output(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def shell_token(value: str) -> str:
    if value == "":
        return "''"
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+-=.,/:\\")
    if all(char in safe for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
