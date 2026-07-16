# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

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
