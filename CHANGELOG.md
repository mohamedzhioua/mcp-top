# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## 0.4.0 — 2026-07-18

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

### Added

- Added loading-regime evidence for `deferred`, `upfront`, and `unknown`
  regimes. Unknown remains a first-class result when local files do not prove a
  regime.
- Added remediation recipes to prune output. Clean suggestions get
  copy-pasteable commands or edit commands; candidates get structured guidance
  only. Recipes prefer disabling/filtering before deletion and keep raw config
  args/env out of JSON.
- Added PyPI packaging with `uvx mcp-top`, `pipx install mcp-top`, and
  `pip install mcp-top` as supported install paths for v0.4.0. The existing
  `python bin/mcp-top` clone-and-run path remains supported.
- Added Trusted Publishing release workflow preparation. Publishing still stays
  behind the maintainer-owned tag/release gate.
- Added Claude Code 2KB truncation modeling for tool descriptions and server
  instructions before token estimation.

### Fixed

- Fixed Claude Code scope precedence: user-project/local config now correctly
  outranks project config. This can change which same-name server is reported as
  active and which lower-precedence entry is shown as reactivating.

## [0.3.0] - 2026-07-17

### Added

- Added `--prune`: a read-only, never-applied suggested-removal block. Clean
  `suggestion`s are limited to global (user-scope) servers whose deletion
  reactivates nothing (estimated net saving = measured definition cost).
  Everything else that scores `prune` becomes a review `candidate` with an
  explicitly unknown net saving -- project scope (usage is not attributed per
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
