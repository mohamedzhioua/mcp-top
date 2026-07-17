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

Example output (real run on the author's machine):

```text
$ python bin/mcp-top

=== claude-code ===

Coverage: 192 transcripts found, 192 parsed, 0 skipped; 30 in window; 1044 tool calls (0 MCP); server queries: 3 ok, 0 failed, 0 not queried

SERVER      SCOPE  TOOLS  DEF TOKENS  CALLS(window)  VERDICT
----------  -----  -----  ----------  -------------  ----------------------------
serena      user   20     ~6,182      0              prune -> save ~6,182/session
playwright  user   24     ~4,621      0              prune -> save ~4,621/session
context7    user   2      ~1,229      0              prune -> save ~1,229/session

=== codex ===

Coverage: 112 transcripts found, 112 parsed, 0 skipped; 30 in window; 1130 tool calls (5 MCP); server queries: 4 ok, 0 failed, 0 not queried

SERVER      SCOPE  TOOLS  DEF TOKENS  CALLS(window)  VERDICT
----------  -----  -----  ----------  -------------  ----------------------------
playwright  user   24     ~4,621      0              prune -> save ~4,621/session
node_repl   user   3      ~1,475      0              prune -> save ~1,475/session
context7    user   2      ~1,229      0              prune -> save ~1,229/session
serena      user   22     ~6,509      5              keep

serena called tools:
  - initial_instructions: 5

=== cursor ===

Coverage: 0 transcripts found, 0 parsed, 0 skipped; usage: unknown (no transcript adapter); server queries: 0 ok, 0 failed, 0 not queried
  usage: Cursor stores chats in undocumented SQLite; no transcript adapter -- usage unknown
  config warning: C:\Users\User\.cursor\mcp.json: exists but is empty -- no servers read

SERVER  SCOPE  TOOLS  DEF TOKENS  CALLS(window)  VERDICT
------  -----  -----  ----------  -------------  -------

Definition token counts use the chars/4 heuristic; ~ means estimate.
```

## Prune Suggestions

Add `--prune` to append a suggested-removal block. It is never applied -- `mcp-top`
is read-only and only tells you which file to edit yourself.

```text
$ python bin/mcp-top --cli claude-code --prune

Coverage: 192 transcripts found, 192 parsed, 0 skipped; 30 in window; 1045 tool calls (0 MCP); server queries: 3 ok, 0 failed, 0 not queried

SERVER      SCOPE  TOOLS  DEF TOKENS  CALLS(window)  VERDICT
----------  -----  -----  ----------  -------------  ----------------------------
serena      user   20     ~6,182      0              prune -> save ~6,182/session
playwright  user   24     ~4,621      0              prune -> save ~4,621/session
context7    user   2      ~1,229      0              prune -> save ~1,229/session

Suggested removals (never applied; mcp-top is read-only -- edit configs yourself):
  - [claude-code] serena (user) in C:\Users\User\.claude.json -> est. net saving ~6,182/session
  - [claude-code] playwright (user) in C:\Users\User\.claude.json -> est. net saving ~4,621/session
  - [claude-code] context7 (user) in C:\Users\User\.claude.json -> est. net saving ~1,229/session
  (~ marks a chars/4 estimate, rough error +/-25%)

Prune candidates -- review before removing (net saving unknown):
  none

Definition token counts use the chars/4 heuristic; ~ means estimate.
```

`--prune` splits removals into two honest tiers:

- **Suggested removals** -- a *global* (user-scope) server with a measured definition
  cost and zero recent calls. Deleting it reactivates nothing, so the estimated net
  saving equals its definition cost.
- **Prune candidates** -- everything else that scored `prune` but cannot be recommended
  cleanly: a project-scoped server (usage is not attributed per project in this release,
  so zero calls may just reflect other projects), a server whose deletion would
  *reactivate* a lower-precedence entry of the same name (net saving unknown -- it may
  even increase), or a config layer that could not be parsed. Each candidate lists its
  gross definition cost and the exact reason it needs review.

CLIs without a usage adapter (Cursor) are reported as "usage unavailable" rather than an
empty block.

## How It Works

1. Read supported CLI MCP configs from local files.
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

`--cli NAME`: CLI source to inspect: `claude-code`, `codex`, `cursor`, or `all`. Default: `all`. With `all`, only CLIs detected from local config or transcript paths are included.

`--no-query`: do not launch configured MCP servers. Default: off.

`--prune`: append a suggested-removal block (human output) or a `suggested_removals` array per CLI (`--json`). Never applied. Default: off.

`--json`: emit machine-readable JSON schema `mcp-top/v2` instead of the human table. JSON v2 groups results under `clis[]`; each CLI entry has `cli`, `window`, `coverage`, and `servers`. Rows use `usage_status: "no-data"` when transcripts exist or are expected but no usable sessions fall inside the usage window. Coverage usage counters such as `in_window`, `total_tool_calls`, and `mcp_tool_calls` are nullable when usage cannot be measured, such as a CLI without a transcript adapter. With `--prune`, each CLI entry gains an additive `suggested_removals` array (`kind`, `server`, `scope`, `source_path`, `gross_tokens`, `net_tokens`, `reactivates`, `reasons`); plain `--json` is unchanged, so the schema stays `mcp-top/v2`.

`--version`: print the installed version and exit.

Exit code `0` means the report completed. Exit code `2` means invalid usage or an unexpected internal error. Skipped transcripts are reported in coverage and are not fatal.

## Honesty Rules

Coverage is always reported at the top of every output: found, parsed, skipped, skip reasons, usage-window count, tool calls, and server query status.

Token counts are labeled. `~` means an estimate from the `chars/4` heuristic, with rough error bars around +/-25%. Exact and estimated counts are never silently mixed.

`mcp-top` is read-only. It never edits configs, uploads data, or sends telemetry.

Unknown Claude Code transcript format versions are reported and skipped. Codex transcript admission is structural because Codex ships frequently and records the CLI version in session metadata.

A zero-call verdict is `prune` only when definition cost was actually measured. With unmeasured definition cost, zero calls is `review`. When a CLI has no measurable usage, calls are unknown and the verdict is `unknown`.

`--prune` only presents a clean saving for a global (user-scope) server whose deletion reactivates nothing. Anything else that scored `prune` -- a project-scoped server, a server whose removal would reactivate a lower-precedence entry, or a CLI with an unparsed config layer -- is a review *candidate* with an explicitly unknown net saving, never a recommendation.

`enabled_tools` names that were not returned by a server's `tools/list` snapshot are surfaced in coverage (phrased as "not returned by this snapshot", not an absolute claim). A malformed `enabled_tools`/`disabled_tools` value that is not a list is warned about and applies no filter, rather than silently hiding every tool.

## Verdicts

| Verdict | Default |
| --- | --- |
| `prune` | 0 calls with measured definition cost |
| `review` | 1-3 calls, or 0 calls with unmeasured definition cost |
| `keep` | More than 3 calls |
| `unknown` | Usage is unsupported for this CLI/server |

## Supported CLIs

| CLI | Config inventory | Usage adapter |
| --- | --- | --- |
| Claude Code | Full: user, user-project, and project scopes | Full: JSONL transcript format `2.x` |
| Codex | User scope `~/.codex/config.toml`; project-layer `.codex/config.toml` is read and its servers listed as a conditional inventory -- not queried and not merged (see below) | Rollout JSONL sessions under `~/.codex/sessions/**/rollout-*.jsonl` |
| Cursor | User `~/.cursor/mcp.json` and project `.cursor/mcp.json` inventory | Not available; Cursor stores chats in undocumented per-workspace SQLite, so usage is unknown |

A Codex project-layer `.codex/config.toml` is reported but deliberately not queried or merged: Codex applies project layers only to *trusted* projects (a state `mcp-top` cannot observe), reads a cascade of files from the project root down to the working directory, and field-merges same-name tables, so its per-server precedence cannot be reproduced faithfully from the published spec. See `docs/v0.3-provenance-and-prune.md`.

Roadmap: additional CLI adapters and per-project usage attribution in upcoming changes; a Cursor transcript adapter and CI threshold mode remain deferred.

## Non-Goals

`mcp-top` is not a cost dashboard; `ccusage` exists for that. It is not an auto-pruner and never rewrites configs. It is not an MCP security scanner. There is no hosted service.

## Running Tests

```sh
bash tests/run.sh
```

## License

MIT.
