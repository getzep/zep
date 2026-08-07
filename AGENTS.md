# Repository instructions

## Zep plugin marketplace

This repository hosts the shared Zep marketplace catalogs:

- `.claude-plugin/marketplace.json` — Claude (remote `github` sources)
- `.agents/plugins/marketplace.json` — ChatGPT Work / Codex (remote `url` sources)
- `.cursor-plugin/marketplace.json` — Cursor catalog placeholder only

Plugin source lives in separate public repositories:

- `getzep/building-with-zep-plugin` — Claude Code, Codex, and Cursor
- `getzep/zep-memory-plugin` — Claude Desktop Chat / Cowork and ChatGPT Work

Claude and ChatGPT Work marketplace entries must use remote GitHub / git URL
sources. Do not copy plugin packages back into this repository and do not add
git submodules. Keep marketplace entries free of `version`; each plugin
repository owns its manifests and releases.

Cursor marketplace catalogs only resolve plugins that live in the same
repository as the marketplace file. For Cursor, import or install
`getzep/building-with-zep-plugin` directly (that repo carries its own
`.cursor-plugin/marketplace.json`).

Merge / publish order: land default-branch content in
`building-with-zep-plugin` and `zep-memory-plugin` before merging marketplace
source cutovers in this repository, so catalogs never point at empty remotes.
