# building-with-zep

A plugin for building applications that use [Zep](https://www.getzep.com) — agent memory built on temporal Context Graphs. This one directory is simultaneously a Claude Code plugin, an OpenAI Codex plugin, and a Cursor plugin, wrapping a single shared skill.

It bundles two things:

- **The `building-with-zep` skill** (`skills/building-with-zep/`) — the decision-and-workflow layer for building on Zep: scoping graphs, ingesting data, retrieving context, and evaluating whether Zep delivers your use case. It indexes the Zep docs rather than duplicating them. Model-invoked when you work on Zep integration code.
- **The Zep documentation MCP server** (`zep-docs`) — real-time search over Zep's docs at `https://docs-mcp.getzep.com/mcp` (remote HTTP, no API key). Claude Code and Codex read it from `.mcp.json`; Cursor reads it from `mcp.json`. See [MCP config](#mcp-config-two-files-one-server) for why there are two.

## One directory, three ecosystems

All three runtimes load the **same** `skills/building-with-zep/` tree; each reads only its own manifest and ignores the others'. There is exactly one physical copy of the skill, so no per-ecosystem duplication and no sync step for the substance of this plugin. The MCP server config is the one thing that exists twice, for the reason below.

- **Claude Code** — manifest `.claude-plugin/plugin.json`; auto-discovers `skills/` and `.mcp.json`. Listed in the marketplace at the repo root (`.claude-plugin/marketplace.json`).
- **Codex** — manifest `.codex-plugin/plugin.json` (points at `./skills/` and `./.mcp.json`). Listed in the Codex marketplace at `.agents/plugins/marketplace.json`.
- **Cursor** — manifest `.cursor-plugin/plugin.json` (points at `./skills/`); auto-discovers `mcp.json`. Listed in the Cursor marketplace at `.cursor-plugin/marketplace.json`.

### MCP config: two files, one server

`zep-docs` is declared twice, and the duplication is deliberate:

| File | Read by | Shape |
|---|---|---|
| `.mcp.json` | Claude Code, Codex | `{"type":"http","url":...}` |
| `mcp.json` | Cursor | `{"url":...}`, no `type` |

Cursor discovers `mcp.json` at the plugin root, without the leading dot, and its remote-server schema has no `type` key (`type` is documented for stdio servers only). A single shared file would therefore have to rely on Cursor tolerating `"type": "http"`, which is undocumented.

Declaring the server inline under `mcpServers` in `.cursor-plugin/plugin.json` is permitted by Cursor's schema, but no official Cursor plugin does it — every one that ships an MCP server uses a plugin-root `mcp.json`, and [`cursor/plugins@a65002e`](https://github.com/cursor/plugins/commit/a65002e3) moved the Figma plugin onto that convention. This plugin follows it.

**Both files point at `https://docs-mcp.getzep.com/mcp`. Change one and you must change the other.**

## Install

**Claude Code** — this repo's root is a marketplace named `zep`:

```bash
/plugin marketplace add getzep/zep
/plugin install building-with-zep@zep
```

Local dev: `claude --plugin-dir plugins/building-with-zep`.

**Codex** — install per the [Codex plugins docs](https://learn.chatgpt.com/docs/build-plugins) via the marketplace at `.agents/plugins/marketplace.json`.

**Cursor** — requires Cursor 2.5 or later. In an Agent chat, type the full command; it does not appear in autocomplete:

```
/add-plugin building-with-zep@https://github.com/getzep/zep
```

This resolves `building-with-zep` through `.cursor-plugin/marketplace.json` at the repo root, and installs the skill and the `zep-docs` server (from `mcp.json`) together. No Cursor Marketplace listing is involved.

Local dev: symlink the plugin directory into Cursor's local plugin folder, then run **Developer: Reload Window**.

```bash
ln -s "$PWD/plugins/building-with-zep" ~/.cursor/plugins/local/building-with-zep
```

## Contents

```
plugins/building-with-zep/
├── .claude-plugin/plugin.json   # Claude manifest
├── .codex-plugin/plugin.json    # Codex manifest (skills:"./skills/", mcpServers:"./.mcp.json")
├── .cursor-plugin/plugin.json   # Cursor manifest (skills:"./skills/")
├── .mcp.json                    # zep-docs, with type:"http" (Claude + Codex)
├── mcp.json                     # zep-docs, bare url (Cursor, auto-discovered)
├── skills/
│   └── building-with-zep/
│       ├── SKILL.md             # the one shared skill
│       └── references/          # empty initially
└── README.md
```

> MCP note: `.mcp.json` uses `{"type":"http","url":...}`. Claude requires the
> `type`; Codex uses the `url` and should ignore the `type` key. Verify the
> `zep-docs` server loads under your installed Codex CLI; if Codex rejects
> `type`, move Claude's MCP inline into `.claude-plugin/plugin.json` and keep
> `.mcp.json` as a bare-`url` Codex-only file. Cursor reads `mcp.json` instead
> and is unaffected either way.

## What goes in the skill vs. the docs

The skill is the **decision-and-workflow layer**, not a second copy of the
product docs. When deciding where a piece of content belongs, follow this rule:

> **Put stable, cross-cutting (not confined to a single doc page) philosophy,
> decision rules, and critical invariants in the skill. Use the docs for
> volatile and exhaustive facts. Add reference files only when they provide
> agent-specific value not well served by the docs — or when a self-contained,
> versioned fallback is intentionally required.**

Concretely:

- **Belongs in `SKILL.md`** — mental models, differentiators, decision rules,
  and invariants that are cross-cutting and stable over time. E.g. "Zep is not a
  chat-log store and not a vector database," "ontology defines the *shape* of the
  graph; instructions define *how to interpret* your domain."
- **Leave to the docs** (via the `zep-docs` MCP and the skill's documentation
  index) — volatile or exhaustive detail: method names, parameters, limits, plan
  availability, pricing, exact reranker names, template syntax, and the **full
  list of best practices for a given feature**. These drift, and the agent can
  retrieve them on demand. A single cross-cutting best-practice *principle* still
  belongs in the skill (e.g. "iterate, don't front-load ontology"); the
  exhaustive per-feature checklist does not.
- **Add a `references/` file only** when it provides agent-specific value the
  docs don't serve well, or when a self-contained, versioned fallback is
  deliberately required — and comes with a maintenance plan.

**Duplication is not forbidden.** Stable guidance *should* be repeated when it
must always be in context. The goal is to avoid duplicating volatile API detail
and exhaustive documentation **without a deliberate reason and a maintenance
plan.**
