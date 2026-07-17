# building-with-zep

A plugin for building applications that use [Zep](https://www.getzep.com) — agent memory built on temporal Context Graphs. This one directory is **both** a Claude Code plugin and an OpenAI Codex plugin, wrapping a single shared skill.

It bundles two things:

- **The `building-with-zep` skill** (`skills/building-with-zep/`) — the decision-and-workflow layer for building on Zep: scoping graphs, ingesting data, retrieving context, and evaluating whether Zep delivers your use case. It indexes the Zep docs rather than duplicating them. Model-invoked when you work on Zep integration code.
- **The Zep documentation MCP server** (`zep-docs`) — real-time search over Zep's docs at `https://docs-mcp.getzep.com/mcp` (remote HTTP, no API key), declared once in `.mcp.json`.

## One directory, two ecosystems

Both runtimes load the **same** `skills/building-with-zep/` tree and the **same** `.mcp.json`; each reads only its own manifest and ignores the other's. There is exactly one physical copy of the skill — no per-ecosystem duplication and no sync step.

- **Claude Code** — manifest `.claude-plugin/plugin.json`; auto-discovers `skills/` and `.mcp.json`. Listed in the marketplace at the repo root (`.claude-plugin/marketplace.json`).
- **Codex** — manifest `.codex-plugin/plugin.json` (points at `./skills/` and `./.mcp.json`). Listed in the Codex marketplace at `.agents/plugins/marketplace.json`.

## Install

**Claude Code** — this repo's root is a marketplace named `zep`:

```bash
/plugin marketplace add getzep/zep
/plugin install building-with-zep@zep
```

Local dev: `claude --plugin-dir plugins/building-with-zep`.

**Codex** — install per the [Codex plugins docs](https://learn.chatgpt.com/docs/build-plugins) via the marketplace at `.agents/plugins/marketplace.json`.

## Contents

```
plugins/building-with-zep/
├── .claude-plugin/plugin.json   # Claude manifest
├── .codex-plugin/plugin.json    # Codex manifest (skills:"./skills/", mcpServers:"./.mcp.json")
├── .mcp.json                    # zep-docs remote HTTP MCP (shared by both)
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
> `.mcp.json` as a bare-`url` Codex-only file.

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
