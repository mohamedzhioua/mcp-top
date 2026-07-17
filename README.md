# mcp-top

> mcp-top profiles your local MCP context footprint: what servers advertise, what may load into context, and which tools your agents actually use.

License: MIT · Dependencies: none (Python stdlib, 3.11+)

## Install

The PyPI package is available as of v0.4.0:

```sh
uvx mcp-top
```

```sh
pipx install mcp-top
```

```sh
pip install mcp-top
```

Clone-and-run still works:

```sh
python bin/mcp-top
```

You can also run the package module directly when `src` is on `PYTHONPATH`:

```sh
PYTHONPATH=src python -m mcp_top
```

## The Problem

MCP servers accumulate. Their advertised tools can be large, but modern clients do not all load those definitions the same way. Ordinary cost dashboards show spend rather than context composition. `mcp-top` shows the join that is usually missing: advertised MCP footprint, possible upfront context footprint, and actual local usage.

## The Tool Search Era

Default 2026 Claude Code uses Tool Search, so fixed definition-tax numbers are the wrong model: tool names and server instructions may load upfront while full tool schemas load only when needed. Cursor has Dynamic Context Discovery outside a stable local contract. Codex currently has no documented MCP definition-deferral surface, so `mcp-top` treats Codex servers as upfront unless local evidence says otherwise.

v0.4 reports a range instead of a fixed tax:

- `advertised_max_tokens`: estimated maximum if every advertised tool definition is loaded.
- `upfront_floor_tokens`: estimated lower bound that may load upfront, including tool names, server instructions, and always-loaded definitions when observable.
- `loading_regime`: `deferred`, `upfront`, or `unknown`.
- `regime_evidence`: the local file/key or documented client rule that produced the regime.

`unknown` is a valid answer. If the local files do not prove a loading regime, `mcp-top` says so rather than guessing from client versions or provider defaults. Token counts remain chars/4 estimates (`~`), and Claude Code's documented 2KB truncation of tool descriptions and server instructions is modeled before counting.

The v0.4 measurement semantics are beta. If your client or config shape produces surprising ranges or regimes, compatibility fixtures are welcome before the v0.5 attribution work hardens the behavior.

## Quickstart

Example output from this machine, captured with `python bin/mcp-top --timeout 5`:

```text
=== claude-code ===

Coverage: 196 transcripts found, 196 parsed, 0 skipped; 30 in window; 1397 tool calls (0 MCP); server queries: 0 ok, 3 failed, 0 not queried
  server query failed context7: timeout after 5 seconds
  server query failed playwright: timeout after 5 seconds
  server query failed serena: server closed stdout before response id 1

SERVER      SCOPE  TOOLS  TOKEN RANGE  REGIME   CALLS(window)  VERDICT
----------  -----  -----  -----------  -------  -------------  -------
context7    user   ?      ?            unknown  0              review
playwright  user   ?      ?            unknown  0              review
serena      user   ?      ?            unknown  0              review
  context7: definitions unavailable -- timeout after 5 seconds
  playwright: definitions unavailable -- timeout after 5 seconds
  serena: definitions unavailable -- server closed stdout before response id 1

=== codex ===

Coverage: 140 transcripts found, 140 parsed, 0 skipped; 30 in window; 968 tool calls (4 MCP); server queries: 1 ok, 3 failed, 0 not queried
  server query failed context7: timeout after 5 seconds
  server query failed playwright: timeout after 5 seconds
  server query failed serena: server closed stdout before response id 1

SERVER      SCOPE  TOOLS  TOKEN RANGE  REGIME   CALLS(window)  VERDICT
----------  -----  -----  -----------  -------  -------------  ------------------------------------------------------------------
node_repl   user   3      ~1,750       upfront  0              prune -> removes advertised up to ~1,750 / upfront at least ~1,750
context7    user   ?      ?            upfront  0              review
playwright  user   ?      ?            upfront  0              review
serena      user   ?      ?            upfront  4              keep
  context7: definitions unavailable -- timeout after 5 seconds
  playwright: definitions unavailable -- timeout after 5 seconds
  serena: definitions unavailable -- server closed stdout before response id 1

serena called tools:
  - initial_instructions: 4

=== cursor ===

Coverage: 0 transcripts found, 0 parsed, 0 skipped; usage: unknown (no transcript adapter); server queries: 0 ok, 0 failed, 0 not queried
  usage: Cursor stores chats in undocumented SQLite; no transcript adapter -- usage unknown
  config warning: C:\Users\User\.cursor\mcp.json: exists but is empty -- no servers read

SERVER  SCOPE  TOOLS  TOKEN RANGE  REGIME  CALLS(window)  VERDICT
------  -----  -----  -----------  ------  -------------  -------

Definition token counts use the chars/4 heuristic; ~ means estimate.
```

## Prune Suggestions

Add `--prune` to append a suggested-removal block. It is never applied: `mcp-top` is read-only and only tells you which file or command to review yourself.

Captured with `python bin/mcp-top --timeout 5 --prune`:

```text
Suggested removals (never applied; mcp-top is read-only -- edit configs yourself):
  - [codex] node_repl (user) in C:\Users\User\.codex\config.toml -> removes advertised up to ~1,750 / upfront at least ~1,750
      command: python -c 'import sys;exec('"'"'import re\nimport sys\nfrom pathlib import Path\n\np = Path(sys.argv[1])\nh = sys.argv[2]\nlines = p.read_text(encoding="utf-8").splitlines()\nout = []\ninside = False\nwrote = False\nfor line in lines:\n    s = line.strip()\n    if s == h:\n        inside = True\n    elif inside and re.match(r"\\s*\\[", line):\n        if not wrote:\n            out.append("enabled = false")\n            wrote = True\n        inside = False\n    if inside and re.match(r"\\s*enabled\\s*=", line):\n        if not wrote:\n            out.append("enabled = false")\n            wrote = True\n        continue\n    out.append(line)\nif inside and not wrote:\n    out.append("enabled = false")\n    wrote = True\nif not wrote:\n    raise SystemExit(f"table not found: {h}")\np.write_text("\\n".join(out) + "\\n", encoding="utf-8")\n'"'"')' 'C:\Users\User\.codex\config.toml' '[mcp_servers.node_repl]'
  (~ marks a chars/4 estimate, rough error +/-25%)

Prune candidates -- review before removing (actual saving unknown):
  - [cursor] usage unavailable (no transcript adapter) -- no prune analysis

Definition token counts use the chars/4 heuristic; ~ means estimate.
```

`--prune` splits removals into two honest tiers:

- **Suggested removals** -- a global (user-scope) server with a measured advertised maximum, a measured upfront floor, zero recent calls, and no lower-precedence server that would reactivate. For Claude Code, clean suggestions get a `claude mcp remove --scope ...` command. For Codex, clean suggestions get a copy-pasteable `enabled = false` edit command. The tool still never edits configs.
- **Prune candidates** -- everything else that scored `prune` but cannot be recommended cleanly: a project-scoped server, a server whose removal would reactivate a lower-precedence entry of the same name, an unparsed config layer, or unsupported usage. Candidates get structured edit guidance only, not commands.

CLIs without a usage adapter, including Cursor, are reported as "usage unavailable" rather than an empty block.

## JSON

`--json` emits machine-readable JSON schema `mcp-top/v3`. Captured with `python bin/mcp-top --timeout 5 --json | head -c 1800`:

```json
{"clis": [{"cli": "claude-code", "coverage": {"bad_lines_in_parsed": 0, "config_warnings": [], "duplicate_tool_use": 0, "future_sessions": 0, "in_window": 30, "mcp_tool_calls": 0, "parsed_without_timestamp": 0, "servers_queried_ok": 0, "servers_query_failed": [["context7", "timeout after 5 seconds"], ["playwright", "timeout after 5 seconds"], ["serena", "server closed stdout before response id 1"]], "servers_unsupported": 0, "total_tool_calls": 1397, "transcripts_found": 196, "transcripts_parsed": 196, "transcripts_skipped": [], "unattributed_mcp_calls": 0, "unmatched_enabled_tools": [], "usage_note": null}, "servers": [{"advertised_max_tokens": null, "called_tools": {}, "calls": 0, "def_error": "timeout after 5 seconds", "def_status": "error", "filtered_tools": 0, "loading_regime": "unknown", "regime_evidence": [], "scope": "user", "server": "context7", "tool_count": null, "transport": "stdio", "upfront_floor_tokens": null, "usage_status": "measured", "verdict": "review"}, {"advertised_max_tokens": null, "called_tools": {}, "calls": 0, "def_error": "timeout after 5 seconds", "def_status": "error", "filtered_tools": 0, "loading_regime": "unknown", "regime_evidence": [], "scope": "user", "server": "playwright", "tool_count": null, "transport": "stdio", "upfront_floor_tokens": null, "usage_status": "measured", "verdict": "review"}, {"advertised_max_tokens": null, "called_tools": {}, "calls": 0, "def_error": "server closed stdout before response id 1", "def_status": "error", "filtered_tools": 0, "loading_regime": "unknown", "regime_evidence": [], "scope": "user", "server": "serena", "tool_count": null, "transport": "stdio", "upfront_floor_tokens": null, "usage_status": "measured", "verdict": "review"}], "window": {"days": 30, "sessions": 30, "sessions_considered": 30}}, {"
```

JSON v3 is a hard break from v2. Rows use `advertised_max_tokens`, `upfront_floor_tokens`, `loading_regime`, and `regime_evidence`. With `--prune`, each CLI entry gains a `suggested_removals` array with `removes_advertised_max_tokens`, `removes_upfront_floor_tokens`, `recipe`, `reactivates`, and `reasons`. The raw `ServerConfig` is never serialized, because config args and env can contain secrets.

## How It Works

1. Read supported CLI MCP configs from local files.
2. Query each configured stdio server read-only with MCP `initialize` and `tools/list` to fetch its actual tool inventory.
3. Model the server's advertised maximum and upfront floor using locally observable loading-regime evidence.
4. Parse local session transcripts through a versioned adapter and count tool calls in a recent window, defaulting to the last 30 sessions and 30 days.
5. Join context footprint to recent usage, rank servers, and print `keep`, `review`, `prune`, or `unknown` verdicts.

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

`--json`: emit machine-readable JSON schema `mcp-top/v3` instead of the human table.

`--version`: print the installed version and exit.

Exit code `0` means the report completed. Exit code `2` means invalid usage or an unexpected internal error. Skipped transcripts are reported in coverage and are not fatal.

## Honesty Rules

Coverage is always reported at the top of every output: found, parsed, skipped, skip reasons, usage-window count, tool calls, and server query status.

Token counts are labeled. `~` means an estimate from the `chars/4` heuristic, with rough error bars around +/-25%. Exact and estimated counts are never silently mixed.

Ranges obey `upfront_floor_tokens <= advertised_max_tokens`. If definitions cannot be measured, the range is `?` in human output and `null` in JSON.

Loading regimes carry evidence. Evidence is canonicalized to file/key/category style and never includes raw env values, command args, or secrets.

`mcp-top` is read-only. It never edits configs, uploads data, or sends telemetry.

Unknown Claude Code transcript format versions are reported and skipped. Codex transcript admission is structural because Codex ships frequently and records the CLI version in session metadata.

A zero-call verdict is `prune` only when definition cost was actually measured. With unmeasured definition cost, zero calls is `review`. When a CLI has no measurable usage, calls are unknown and the verdict is `unknown`.

`--prune` only presents a clean removal for a global (user-scope) server whose deletion reactivates nothing. Anything else that scored `prune` -- a project-scoped server, a server whose removal would reactivate a lower-precedence entry, or a CLI with an unparsed config layer -- is a review candidate with explicitly unknown actual savings, never a recommendation.

`enabled_tools` names that were not returned by a server's `tools/list` snapshot are surfaced in coverage (phrased as "not returned by this snapshot", not an absolute claim). A malformed `enabled_tools`/`disabled_tools` value that is not a list is warned about and applies no filter, rather than silently hiding every tool.

## Verdicts

| Verdict | Default |
| --- | --- |
| `prune` | 0 calls with measured advertised maximum |
| `review` | 1-3 calls, or 0 calls with unmeasured definition cost |
| `keep` | More than 3 calls |
| `unknown` | Usage is unsupported for this CLI/server |

## Supported CLIs

| CLI | Config inventory | Usage adapter |
| --- | --- | --- |
| Claude Code | Full: user, user-project/local, and project scopes. User-project/local outranks project. | Full: JSONL transcript format `2.x` |
| Codex | User scope `~/.codex/config.toml`; project-layer `.codex/config.toml` is read and its servers listed as a conditional inventory -- not queried and not merged (see below) | Rollout JSONL sessions under `~/.codex/sessions/**/rollout-*.jsonl` |
| Cursor | User `~/.cursor/mcp.json` and project `.cursor/mcp.json` inventory, plus a pointer to Cursor's native context panel for actual usage/context inspection | Not available; Cursor stores chats in undocumented per-workspace SQLite, so usage is unknown |

A Codex project-layer `.codex/config.toml` is reported but deliberately not queried or merged: Codex applies project layers only to *trusted* projects (a state `mcp-top` cannot observe), reads a cascade of files from the project root down to the working directory, and field-merges same-name tables, so its per-server precedence cannot be reproduced faithfully from the published spec. See `docs/v0.3-provenance-and-prune.md`.

## Roadmap

v0.5: per-project attribution, recorded-result footprint, and snapshots/diff.

v1.0: Gemini inventory, opt-in Streamable HTTP, offline CI thresholds, and schema freeze.

## Non-Goals

`mcp-top` is not a cost dashboard; `ccusage` exists for that. It is not an auto-pruner and never rewrites configs. It is not an MCP security scanner. There is no hosted service.

## Running Tests

```sh
bash tests/run.sh
```

## License

MIT.
