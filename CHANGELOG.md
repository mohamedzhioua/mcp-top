# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## [0.3.0] - 2026-07-17

### Added

- Added `--prune`: a read-only, never-applied suggested-removal block. Clean
  `suggestion`s are limited to global (user-scope) servers whose deletion
  reactivates nothing (estimated net saving = measured definition cost).
  Everything else that scores `prune` becomes a review `candidate` with an
  explicitly unknown net saving -- project scope (usage is not attributed per
  project), a deletion that would reactivate a lower-precedence entry, or an
  unparsed config layer. `--json --prune` adds an additive `suggested_removals`
  array per CLI; plain `--json` is unchanged and the schema stays `mcp-top/v2`.
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
