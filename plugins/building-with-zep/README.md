# Build with Zep

The Build with Zep plugin helps Claude Code, Codex, and Cursor build applications
with [Zep](https://www.getzep.com).

## User documentation

For installation, usage, supported agents, and an overview of what the plugin
does, see [Implement Zep with agents](https://help.getzep.com/implement-zep-with-agents).
That page is the canonical user-facing documentation for this plugin.

This README contains contributor and maintainer information that belongs beside
the plugin source.

## What this directory contains

This one directory is simultaneously a Claude Code plugin, an OpenAI Codex
plugin, and a Cursor plugin, wrapping a single shared skill.

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
├── AGENTS.md                    # scoped maintainer instructions for coding agents
├── CHANGELOG.md
└── README.md
```

Maintainer tooling lives at `scripts/plugin_manifests.py` in the repo root, not here:
this directory is copied wholesale into every user's plugin cache.

`AGENTS.md` deliberately repeats the maintainer guidance in this README so coding
agents receive it without another file read. The repository-root `AGENTS.md`
directs Codex agents launched from the workspace root to load these scoped
instructions before changing the plugin. Claude Code receives the same guidance
from the path-scoped `.claude/rules/building-with-zep.md` file at the repository
root. It lives outside the plugin because Claude's strict plugin validator rejects
a plugin-root `CLAUDE.md`: installed plugins load agent context from skills, not
from that file.

> MCP note: `.mcp.json` uses `{"type":"http","url":...}`. Claude requires the
> `type`; Codex uses the `url` and should ignore the `type` key. Verify the
> `zep-docs` server loads under your installed Codex CLI; if Codex rejects
> `type`, move Claude's MCP inline into `.claude-plugin/plugin.json` and keep
> `.mcp.json` as a bare-`url` Codex-only file. Cursor reads `mcp.json` instead
> and is unaffected either way.

## Local development

Load the plugin directly in Claude Code:

```bash
claude --plugin-dir plugins/building-with-zep
```

For Cursor, symlink the plugin directory into Cursor's local plugin folder, then
run **Developer: Reload Window**:

```bash
ln -s "$PWD/plugins/building-with-zep" ~/.cursor/plugins/local/building-with-zep
```

## Releasing

The marketplace *is* this git repository, so there is no publish step — merging to
`main` is the release. For Claude Code, the explicit `version` decides whether
users actually **receive** a new cached copy. Pushing plugin changes without
increasing it leaves Claude Code users on the old release, including users with
auto-update enabled. Two changes already shipped under the same `0.1.0` string
this way ([CHANGELOG.md](CHANGELOG.md)).

`version` lives in the three ecosystem plugin manifests, and one command bumps
all of them:

```bash
python3 scripts/plugin_manifests.py set 0.3.0
```

| File | Read by |
|---|---|
| `.claude-plugin/plugin.json` | Claude Code |
| `.codex-plugin/plugin.json` | Codex |
| `.cursor-plugin/plugin.json` | Cursor |

The root marketplace entries deliberately declare **no** `version`: each ecosystem
resolves it from its own `plugin.json`, so a second copy is dead weight that can
mask a real bump. [`test-plugins.yml`](../../.github/workflows/test-plugins.yml)
fails if one reappears, if the three managed versions drift apart, or if the two MCP
configs stop agreeing on the `zep-docs` endpoint. It also fails when a PR changes
loaded plugin content without a **semantic version increase**, except `README.md`,
`CHANGELOG.md`, and the maintainer-only `AGENTS.md`. The workflow also verifies
that `AGENTS.md` and Claude's path-scoped copy contain identical instructions.

That check is **advisory, not required** — a failure shows as a red check on the pull
request but leaves the merge button enabled. Forgetting step 1 warns you before you
merge; it does not stop you.

Full procedure:

1. `python3 scripts/plugin_manifests.py set <version>`, from the repo root
2. Add a [CHANGELOG.md](CHANGELOG.md) entry — no ecosystem has changelog plumbing,
   so this file is the only release note users get
3. `claude plugin validate plugins/building-with-zep --strict`
4. Open the PR; `test-plugins.yml` re-checks agreement and flags a missing increase

Merging step 4 is the release. There is no step 5.

Plugins are deliberately **not tagged**, unlike everything else released from this
repo. The `zep-ingest-v<version>` and `zep-<framework>-<language>-v<version>` schemes
are functional: publishing a GitHub Release for one of those tags is what fires the
PyPI upload, and CI checks the tag against the package metadata before publishing.
Plugins have no publish workflow for a tag to trigger, so one would be a marker
nothing reads. `claude plugin tag` would create `building-with-zep--v<version>`, and
nothing would consume it. If a plugin ever declares a semver-range dependency on this
one, tag the historical release commits at that point — `git tag` works retroactively
on any commit.

### Why explicit semver rather than commit SHAs

Omitting `version` everywhere makes Claude Code fall back to the git commit SHA, so
every merge reaches users with no bump to remember. Tempting, and wrong here:

- **The source is a relative path inside a monorepo.** The SHA that resolves is this
  repository's, not the plugin directory's, so commits to `ingestion/`,
  `integrations/`, or the eval harness would each register as a new plugin version —
  re-downloading the plugin and prompting `/reload-plugins` for changes that touched
  nothing the user has.
- **Only Claude Code documents the fallback.** Cursor lists `version` as optional but
  documents no SHA behavior, and Codex documents neither, so dropping it would leave a
  split release model across the three ecosystems.
- **Semver is a support handle.** "Which version are you on?" stays answerable.

The cost is a bump that must not be forgotten — which is what the script and the CI
check exist to enforce.

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
