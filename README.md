# mcp-top

> `top` for your agent's context window. Profile what MCP tool definitions actually cost in
> tokens against how often each tool is actually called - from local transcripts already on
> disk - and get a safe prune list.

License: MIT · Dependencies: none (Python stdlib, 3.11+)

## The Problem

MCP servers accumulate. Their tool definitions can eat a third of a context window before the user types anything, and ordinary cost dashboards show spend rather than context composition. `mcp-top` shows the join that is usually missing: definition cost next to actual local usage.

## Quickstart

Clone the repository, then run:

```sh
python bin/mcp-top
```

You can also run the package module directly when `src` is on `PYTHONPATH`:

```sh
PYTHONPATH=src python -m mcp_top
```

Example output:

```text
$ python bin/mcp-top

Coverage: 148 transcripts found, 148 parsed, 0 skipped; 30 in window; 2412 tool calls (0 MCP); server queries: 3 ok, 0 failed, 0 not queried

SERVER      SCOPE  TOOLS  DEF TOKENS  CALLS(window)  VERDICT
----------  -----  -----  ----------  -------------  ----------------------------
serena      user   20     ~6,182      0              prune -> save ~6,182/session
playwright  user   24     ~4,621      0              prune -> save ~4,621/session
context7    user   2      ~1,229      0              prune -> save ~1,229/session

Definition token counts use the chars/4 heuristic; ~ means estimate.
```

## How It Works

1. Read Claude Code MCP configs across user, user-project, and project scopes.
2. Query each configured stdio server read-only with MCP `initialize` and `tools/list` to fetch its actual tool definitions.
3. Parse local session transcripts through a versioned adapter and count tool calls in a recent window, defaulting to the last 30 sessions and 30 days.
4. Join definition cost to recent usage, rank servers, and print `keep`, `review`, `prune`, or `unknown` verdicts.

Naive timestamps are treated as UTC. Subagent transcripts are included and grouped under their parent session for windowing.

Querying definitions launches the configured server commands. `mcp-top` sends read-only protocol calls and kills the processes afterward. Use `--no-query` to skip launching servers; definition weights are then unknown.

## CLI Reference

`--home PATH`: home directory to inspect. Default: `~`.

`--project PATH`: project directory to inspect for project-scoped MCP config. Default: current working directory.

`--sessions N`: maximum recent parsed sessions to include. Default: `30`.

`--days N`: maximum transcript age for the usage window. Default: `30`.

`--timeout SECONDS`: per-server stdio query timeout. Default: `20.0`.

`--cli NAME`: CLI source to inspect: `claude-code` or `all`. Default: `all`.

`--no-query`: do not launch configured MCP servers. Default: off.

`--json`: emit machine-readable JSON schema `mcp-top/v2` instead of the human table. JSON v2 groups results under `clis[]`; each CLI entry has `cli`, `window`, `coverage`, and `servers`.

`--version`: print the installed version and exit.

Exit code `0` means the report completed. Exit code `2` means invalid usage or an unexpected internal error. Skipped transcripts are reported in coverage and are not fatal.

## Honesty Rules

Coverage is always reported at the top of every output: found, parsed, skipped, skip reasons, usage-window count, tool calls, and server query status.

Token counts are labeled. `~` means an estimate from the `chars/4` heuristic, with rough error bars around +/-25%. Exact and estimated counts are never silently mixed.

`mcp-top` is read-only. It never edits configs, uploads data, or sends telemetry.

Unknown transcript format versions are reported and skipped. They are never guessed into counts that might be wrong.

A zero-call verdict is `prune` only when definition cost was actually measured. With unmeasured definition cost, zero calls is `review`. When a CLI has no measurable usage, calls are unknown and the verdict is `unknown`.

## Verdicts

| Verdict | v0.2 default |
| --- | --- |
| `prune` | 0 calls with measured definition cost |
| `review` | 1-3 calls, or 0 calls with unmeasured definition cost |
| `keep` | More than 3 calls |
| `unknown` | Usage is unsupported for this CLI/server |

## Adapter Support

Today, `mcp-top` supports Claude Code JSONL transcripts with format versions `2.x`. Unknown versions surface in the coverage block.

Roadmap: `--prune` suggestion block and additional CLI adapters in upcoming changes; CI threshold mode in v1.0.

## Non-Goals

`mcp-top` is not a cost dashboard; `ccusage` exists for that. It is not an auto-pruner and never rewrites configs. It is not an MCP security scanner. There is no hosted service.

## Running Tests

```sh
bash tests/run.sh
```

## License

MIT.
