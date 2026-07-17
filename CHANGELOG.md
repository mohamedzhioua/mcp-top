# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

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
