# Changelog

All notable changes to the `building-with-zep` plugin. This file is the only
release feed the plugin system offers — Claude Code, Codex, and Cursor have no
changelog plumbing, so users who want to know what a version changed read this.

The version here is the release identity shared by all three ecosystem manifests;
Claude Code also uses it for update detection. Bump it with
`python3 scripts/plugin_manifests.py set <version>` from the repo root; see
[Releasing](README.md#releasing).

## 0.2.0 — 2026-07-30

### Added

- **Cursor as a third ecosystem** ([#581]). The plugin directory now carries a
  `.cursor-plugin/plugin.json` manifest and a Cursor-shaped `mcp.json`, and the
  repo root carries a `.cursor-plugin/marketplace.json`. Install with
  `/add-plugin building-with-zep@https://github.com/getzep/zep` in a Cursor 2.5+
  Agent chat. All three runtimes load the same `skills/building-with-zep/` tree.

### Changed

- The version now lives in exactly one place per ecosystem, and CI fails on
  drift. `version` was removed from the `building-with-zep` entries in both
  `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`: each
  ecosystem resolves the release from its own `plugin.json`, so marketplace
  copies are dead weight that can mask a bump. The Claude marketplace manifest's
  own `metadata.version` was removed for the same reason — nothing consumes it,
  and it reads as the plugin's version.
- CI now requires a strictly newer semantic version whenever loaded plugin
  content changes. The release helper updates all three manifests together and
  refuses invalid versions or downgrades.
- All three manifests now link to the canonical plugin documentation, and the
  Codex manifest includes the interface metadata required by current plugin
  ingestion.

## 0.1.0 — 2026-06-15

Initial release ([#520]): the `building-with-zep` skill plus the `zep-docs` MCP
server (`https://docs-mcp.getzep.com/mcp`, remote HTTP, no API key).

Two later changes shipped **under this same `0.1.0` string**, so anyone who
installed before them never received them and had to reinstall:

- Codex as a second ecosystem, sharing one skill and MCP config ([#566]).
- Restored skill guidance and the "what goes in the skill vs. the docs"
  placement rule ([#567]).

That is the failure mode 0.2.0's tooling exists to prevent: Claude Code uses the
explicit version for update detection, so pushing plugin content without
increasing it leaves existing Claude Code installs on the old cached release.

[#520]: https://github.com/getzep/zep/pull/520
[#566]: https://github.com/getzep/zep/pull/566
[#567]: https://github.com/getzep/zep/pull/567
[#581]: https://github.com/getzep/zep/pull/581
