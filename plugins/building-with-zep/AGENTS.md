# Build with Zep plugin maintainer instructions

These instructions apply to every file under `plugins/building-with-zep/`.

The plugin helps Claude Code, Codex, and Cursor build applications with Zep. For
installation, usage, supported agents, and an overview, use the canonical
[Implement Zep with agents](https://help.getzep.com/implement-zep-with-agents)
documentation.

The maintainer guidance is deliberately repeated in
`plugins/building-with-zep/AGENTS.md`, `plugins/building-with-zep/README.md`, and
`.claude/rules/building-with-zep.md` so agents receive it automatically. When
changing guidance that appears in these files, update all of them and keep them
consistent.

## Package architecture

This one directory is simultaneously a Claude Code plugin, a Codex plugin, and a
Cursor plugin. All three runtimes load the same
`skills/building-with-zep/` tree.

- Do not create ecosystem-specific copies of the skill.
- Keep the plugin name `building-with-zep` in all three manifests.
- Keep the three manifest versions identical:
  - `.claude-plugin/plugin.json`
  - `.codex-plugin/plugin.json`
  - `.cursor-plugin/plugin.json`
- Keep the root marketplace entries free of `version`. Each ecosystem resolves
  the version from its own plugin manifest.
- Preserve `.codex-plugin/plugin.json`; it is required for the Codex package.
- Do not add `CLAUDE.md` at this plugin root. Claude's strict plugin validator
  rejects it because installed plugins load context from skills, not that file.
  Keep Claude's maintainer guidance in the repository's path-scoped rule.

The ecosystem-specific files are:

- Claude Code: `.claude-plugin/plugin.json`, `.mcp.json`, and the root
  `.claude-plugin/marketplace.json`.
- Codex: `.codex-plugin/plugin.json`, `.mcp.json`, and the root
  `.agents/plugins/marketplace.json`.
- Cursor: `.cursor-plugin/plugin.json`, `mcp.json`, and the root
  `.cursor-plugin/marketplace.json`.

## MCP configuration

The `zep-docs` server is intentionally declared twice:

- `.mcp.json` is read by Claude Code and Codex. Its server entry contains
  `{"type":"http","url":...}`.
- `mcp.json` is read by Cursor. Its server entry contains `{"url":...}` without
  a `type` field.

Both files must point to `https://docs-mcp.getzep.com/mcp`. Whenever one endpoint
changes, update the other in the same change. Do not consolidate the files unless
all three ecosystems document a shared compatible schema.

## Releasing

Merging the plugin change to `main` is the release; there is no separate publish
or tag step.

For loaded plugin content changes:

1. From the repository root, bump all three manifests together:

   ```bash
   python3 scripts/plugin_manifests.py set <version>
   ```

2. Add an entry to `CHANGELOG.md`.
3. Validate the Claude package:

   ```bash
   claude plugin validate plugins/building-with-zep --strict
   ```

4. Run the repository plugin-manifest check and confirm the `test-plugins.yml`
   workflow passes in the pull request.

Changes limited to the maintainer-only `README.md`, `CHANGELOG.md`, or
`plugins/building-with-zep/AGENTS.md` do not require a version bump. All other
files under this plugin are treated as loaded plugin content by the CI check. If
multiple plugin changes are being prepared in the same unreleased branch, one
semantic version increase covering that release is sufficient.

Do not add a plugin git tag. Nothing consumes one. The repository's package tags
trigger package-publishing workflows, but this plugin has no tag-based publish
workflow.

## What goes in the skill versus the docs

The skill is the decision-and-workflow layer, not a second copy of the product
documentation.

- Put stable, cross-cutting philosophy, decision rules, mental models, and
  critical invariants in `skills/building-with-zep/SKILL.md`.
- Leave volatile or exhaustive details to the Zep documentation accessed through
  `zep-docs`. This includes method names, parameters, limits, pricing, plan
  availability, exact reranker names, template syntax, and full per-feature
  checklists.
- Add a file under `skills/building-with-zep/references/` only when it provides
  agent-specific value the docs do not serve well, or when a self-contained,
  versioned fallback is deliberately required and has a maintenance plan.
- Stable guidance may be duplicated when it must always be in context. Do not
  duplicate volatile API details without a deliberate reason and maintenance
  plan.

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
