# Prune Case Study: Before/After Snapshot

This case study is reproducible from the checkout:

```sh
python scripts/examples/prune_case_study.py
```

The script creates a fixed temporary home at `C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study`, deletes it at the start of each run, writes a small Claude config with two stdio MCP servers, and points both servers at `tests/fixtures/fake_mcp_server.py`. It also writes two Claude transcript fixtures under the temp `~/.claude/projects/<slug>/` directory.

`used-server` appears in the transcript tool calls. `unused-server` is configured and queried, but has zero calls.

The script runs this checkout through `PYTHONPATH=src python -m mcp_top`; the displayed commands use the installed-console spelling, `mcp-top`. Snapshot `generated_at` timestamps vary on each run.

## Captured Run

```text
Case root: C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study
The script runs the current checkout as: PYTHONPATH=src python -m mcp_top

$ mcp-top --home C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\home --project C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\workspace\demo-project --days 3650 --timeout 5 --cli claude-code --json snapshot --out C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\outputs\before.json
$ mcp-top --home C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\home --project C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\workspace\demo-project --days 3650 --timeout 5 --cli claude-code --prune
Coverage: 2 transcripts found, 2 parsed, 0 skipped; 2 in window; 3 tool calls (2 MCP); server queries: 2 ok, 0 failed, 0 not queried
  recorded results: 2 paired, 0 partial, 0 unsupported, 0 unmeasurable, 0 unpaired result(s), 0 unpaired call(s) (recorded result footprint is a recorded-bytes lower bound)

SERVER         SCOPE  TOOLS  TOKEN RANGE  REGIME   CALLS(window)  VERDICT
-------------  -----  -----  -----------  -------  -------------  ------------------------------------------------------------
unused-server  user   2      >=~23..~77   unknown  0              prune -> removes advertised up to ~77 / upfront at least ~23
used-server    user   2      >=~22..~76   unknown  2              review
  used-server recorded result footprint: 37 recorded UTF-8 byte(s) (lower bound) -> ~10 tok (estimate) across 2 result(s) (max ~5, p90 ~5)

used-server called tools:
  - first_tool: 1
  - second_tool: 1

Suggested removals (never applied; mcp-top is read-only -- edit configs yourself):
  - [claude-code] unused-server (user) in C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\home\.claude.json -> removes advertised up to ~77 / upfront at least ~23
      command: claude mcp remove --scope user unused-server
  (~ marks a chars/4 estimate, rough error +/-25%)

Prune candidates -- review before removing (actual removal impact unknown):
  none

Definition token counts use the chars/4 heuristic; ~ means estimate.

Removed unused-server from the temporary Claude config.

$ mcp-top --home C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\home --project C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\workspace\demo-project --days 3650 --timeout 5 --cli claude-code --json snapshot --out C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\outputs\after.json
$ mcp-top diff C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\outputs\before.json C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\outputs\after.json
Snapshot diff
old generated_at: 2026-07-18T14:58:11.150732+00:00
new generated_at: 2026-07-18T14:58:11.565212+00:00

=== claude-code ===
  removed: unused-server
  unchanged: 1 server(s)

Advertised surface summary
  before advertised_max_tokens total: ~153
  after advertised_max_tokens total: ~76
  delta: -77
  removed server: unused-server

Captured files: C:\Users\User\AppData\Local\Temp\mcp-top-prune-case-study\outputs
```

## What Changed

Before pruning, the snapshot has two servers:

```json
{"server": "unused-server", "advertised_max_tokens": {"exact": false, "value": 77}, "calls": 0, "verdict": "prune"}
{"server": "used-server", "advertised_max_tokens": {"exact": false, "value": 76}, "calls": 2, "verdict": "review"}
```

After removing `unused-server` from the temporary config, the diff reports it as removed and `used-server` is unchanged. The aggregate advertised maximum in the two snapshots drops from `~153` to `~76`, a `-77` change matching the removed server's advertised maximum.

The recorded-result line is separate: `37 recorded UTF-8 byte(s) (lower bound) -> ~10 tok`. That is only a lower bound on bytes recorded in transcripts. It is not a measure of model context, prompt input, invoices, or removal impact.

Usage in this run is home-wide because `--project-usage` is not passed. If you add `--project-usage`, Claude attribution is still best-effort because Claude transcripts provide the project slug rather than a recorded cwd; unattributed sessions are reported and never guessed.
