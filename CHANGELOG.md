# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## 0.5.0 - 2026-07-18

### Added

- Added `--project-usage` for opt-in project-scoped usage windows. The default
  remains home-wide.
- Added recorded-result footprint as a lower-bound measurement of recorded
  UTF-8 result bytes, including taxonomy and basis fields for paired, partial,
  unsupported, unmeasurable, unpaired-result, and unpaired-call cases.
- Added `snapshot` and `diff` subcommands with schemas `mcp-top-snapshot/v1`
  and `mcp-top-diff/v1`, plus `snapshot --redact-identifiers`.
- Added Claude loading-regime evidence for the Vertex default-off signal and
  `ENABLE_TOOL_SEARCH=auto` / `auto:N` threshold mode.
- Added a reproducible before/after prune case study under
  `docs/examples/prune-case-study.md` with a runnable script in
  `scripts/examples/prune_case_study.py`.

### Changed

- Kept JSON report schema `mcp-top/v3` and added fields without a breaking
  schema change: coverage attribution, `coverage.recorded_results`, and
  per-server `recorded_result_footprint`.
- Made prune reasons attribution-aware, including empty in-project windows,
  unattributed sessions, and home-wide versus project-scoped usage.
- Resolved Claude loading regimes through a documented precedence ladder
  instead of broad inference.
- Updated README and package metadata for the v0.5 launch.

### Fixed

- Fixed `ENABLE_TOOL_SEARCH` so it overrides a non-first-party
  `ANTHROPIC_BASE_URL`; v0.4 could misclassify that combination as unknown.
- Fixed option handling before subcommands so flags such as `--no-query` are
  honored when placed before `snapshot` or `diff`.

### Security

- Snapshots are allowlist-built and never carry config secrets, config paths,
  result content, raw command arguments, env values, URLs, errors, regime
  evidence, or prune recipes.
- The diff loader strictly validates untrusted snapshot files and reports
  value-free errors without replaying hostile embedded values.

## 0.4.0 - 2026-07-18

### Changed

- Replaced fixed per-run definition-tax claims with Tool-Search-era measurement
  ranges. Reports now distinguish what a server advertises from what may load
  upfront by showing `advertised_max_tokens`, `upfront_floor_tokens`,
  `loading_regime`, and `regime_evidence`.
- Marked the new measurement semantics as beta. The v0.4 release is a
  soft-launch for compatibility fixtures from real Claude Code, Codex, and
  Cursor configs before broader promotion in v0.5.
- Changed JSON output to breaking schema `mcp-top/v3`. There is no v2
  compatibility flag because v2's point-estimate semantics are misleading for
  deferred-loading clients.

#### JSON v2 -> v3 migration table

| Old v2 field | New v3 field / why removed |
| --- | --- |
| `def_tokens` | Replace with `advertised_max_tokens`, `upfront_floor_tokens`, `loading_regime`, and `regime_evidence`. A single definition-token value can overstate upfront context on deferred-loading clients. |
| `gross_tokens` | Replace with `removes_advertised_max_tokens`. Removal impact is now expressed as the advertised maximum removed, not a fixed gross saving. |
| `net_tokens` | Replace with `removes_upfront_floor_tokens`. Net per-run savings are no longer asserted; v3 reports the upfront floor removed and keeps candidate actual savings unknown when precedence or attribution makes the result ambiguous. |
| (none in v2) | Added `recipe` object to each `suggested_removals` entry: `{kind, source_path, scope, change}`, plus `argv` when `kind` is `"command"`. There is no v2 equivalent to migrate from. |
| Type change | Every v2 integer-or-`null` token field (`def_tokens`, `gross_tokens`, `net_tokens`) is replaced by a v3 token object `{"value": <int>, "exact": <bool>}` or `null` - not a bare integer. Callers must read `.value`, not compare the field directly. |

### Added

- Added loading-regime evidence for `deferred`, `upfront`, and `unknown`
  regimes. Unknown remains a first-class result when local files do not prove a
  regime.
- Added remediation recipes to prune output. Claude Code clean suggestions get
  a copy-pasteable command; every other recipe (Codex, Cursor, and all
  candidates) gets structured guidance only. Recipes prefer disabling/
  filtering before deletion and keep raw config args/env out of JSON.
- Added PyPI packaging with `uvx mcp-top`, `pipx install mcp-top`, and
  `pip install mcp-top` as supported install paths for v0.4.0. The existing
  `python bin/mcp-top` clone-and-run path remains supported. Sdist contents
  (tests, goldens, `bin/`, `docs/`) are now pinned via `MANIFEST.in`; the
  wheel ships only the `mcp_top` package.
- Added Trusted Publishing release workflow preparation, now gated on tag/
  package version equality, tagged-commit reachability from `origin/main`,
  and a green test run before build/publish.
- Added Claude Code 2KB truncation modeling for tool descriptions and server
  instructions before token estimation.
- Added `--query-project` to opt into launching project-scope MCP servers.

### Security

- Project-scope MCP configs (Claude project `.mcp.json`, Cursor project
  `.cursor/mcp.json`) are now inventory-only by default: they are listed but
  never launched, because a project's config ships with the repository and
  is untrusted input. Pass `--query-project` to also query them in a trusted
  repository. Coverage/output explains why the weight is unknown otherwise.
- Server-controlled JSON-RPC protocol errors are now reduced at the client
  boundary to a stable category plus an optional numeric code (e.g.
  `initialize failed (code -32603)`); the raw `error.message`/`error.data`
  fields are never retained in any output mode.

### Fixed

- Fixed Claude Code scope precedence: user-project/local config now correctly
  outranks project config. This can change which same-name server is reported as
  active and which lower-precedence entry is shown as reactivating.
- Fixed Codex's loading regime: it is now `unknown` rather than a hardcoded
  `upfront`, since Codex has its own upstream tool-search/deferred-exposure
  mechanism whose effective state is not observable from local config.
- Fixed Claude Code settings resolution to follow documented precedence
  (managed > local > project > user) instead of accumulating signals across
  files; an unreadable higher-precedence layer now forces `unknown` instead
  of being silently skipped.
- Fixed the range contract: `advertised_max_tokens` is now the whole-
  definition estimator sum (as originally specified) plus instructions, and
  `upfront_floor_tokens` always uses the client-visible `mcp__<server>__<tool>`
  name for Claude Code. Zero tools with instructions no longer attributes any
  weight to "advertised tool definitions".
- Fixed the emitted Codex remediation command: mcp-top no longer generates an
  executable TOML-editing command for Codex (it could corrupt multiline
  strings/comments); Codex clean suggestions now get the same structured,
  command-free guidance as every other non-Claude-Code recipe.
- Fixed reserved Claude Code server names (`workspace`, `claude-in-chrome`,
  `computer-use`, `Claude Preview`, `Claude Browser`) to be treated as
  unsupported and non-prunable, matching Claude Code's own load-time skip.
- Fixed the Windows wheel-smoke CI job, which broke on a bash glob over a
  backslash `$GITHUB_WORKSPACE` path; the wheel is now resolved via
  `pathlib`.

## [0.3.0] - 2026-07-17

### Added

- Added `--prune`: a read-only, never-applied suggested-removal block. Clean
  `suggestion`s are limited to global (user-scope) servers whose deletion
  reactivates nothing (estimated net saving = measured definition cost).
  Everything else that scores `prune` becomes a review `candidate` with an
  explicitly unknown net saving - project scope (usage is not attributed per
  project), a deletion that would reactivate a lower-precedence entry, or an
  unparsed config layer, or (for Codex) a same-name project-layer entry that a
  trusted project could redefine. `--json --prune` adds an additive
  `suggested_removals` array per CLI. The schema stays `mcp-top/v2`: every
  addition is a backward-compatible field (`coverage.unmatched_enabled_tools`,
  and `suggested_removals` only under `--prune`); no existing field is renamed,
  retyped, or removed.
- Added a config provenance model: `ServerConfig` now records the
  lower-precedence entries a winner shadows, so a prune deletion can be
  simulated for reactivation.
- Codex project-layer `.codex/config.toml` is now read and its servers listed
  as a conditional inventory in coverage (not queried, not merged), because
  Codex applies project layers only to trusted projects and its layer merge
  semantics are undocumented.
- `enabled_tools` allowlist names not returned by a server's `tools/list`
  snapshot are surfaced in coverage.

### Changed

- The human table verdict is now provenance-aware: `prune -> save ~N/session`
  is shown only for clean global-scope suggestions; other prune rows read
  `prune candidate` so no savings figure is asserted without its caveats.

### Fixed

- A malformed `enabled_tools`/`disabled_tools` value (present but not a list)
  now warns and applies no filter, instead of silently hiding every tool and
  producing a misleading zero-cost prune row.

## [0.2.0] - 2026-07-17

### Changed

- Raised the Python floor to 3.11+.
- Refactored reports around a per-CLI architecture for Claude Code, Codex, and Cursor inventory.
- Made usage nullable with explicit `usage_status` values, including `no-data` when no usable sessions are available.
- Changed JSON output to breaking schema `mcp-top/v2` with nested per-CLI results.
- Added the `unknown` verdict for unsupported usage.

### Added

- Added Codex CLI support for user-scope `~/.codex/config.toml` inventory and rollout JSONL usage under `~/.codex/sessions`.
- Added Cursor MCP config inventory for user and project scopes, with usage marked unknown because v0.2 has no Cursor transcript adapter.

## [0.1.0] - 2026-07-16

### Added

- Initial release.
- Config inventory across Claude Code user, user-project, and project scopes.
- Read-only stdio `tools/list` client with process-tree cleanup.
- Versioned Claude Code transcript adapter for `2.x` with skip-and-report semantics, including subagent transcripts grouped under their parent session.
- Windowed tool-call counter.
- Join and rank engine with call attribution by configured server name and `keep`, `review`, and `prune` verdicts.
- Mandatory coverage block.
- Human table output and `--json` output.
- Zero dependencies beyond the Python standard library.
