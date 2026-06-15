# building-with-zep (Claude Code plugin)

A Claude Code plugin for building applications that use [Zep](https://www.getzep.com) — agent memory built on temporal Context Graphs.

It bundles two things:

- **The `building-with-zep` skill** (`skills/building-with-zep/`) — what Zep is, its core concepts, the high-level vs low-level APIs, customization, and how to start simple and benchmark. Model-invoked when you work on Zep integration code.
- **The Zep documentation MCP server** (`zep-docs`) — real-time search over Zep's docs at `https://docs-mcp.getzep.com/mcp` (HTTP transport, no API key).

## Install

This repo's root (`.claude-plugin/marketplace.json`) is a plugin marketplace named `zep`.

```bash
# Add the marketplace (from this repo)
/plugin marketplace add getzep/zep

# Install the plugin
/plugin install building-with-zep@zep
```

To develop locally without the marketplace, point Claude Code at the plugin directory:

```bash
claude --plugin-dir plugins/building-with-zep
```

## Contents

```
plugins/building-with-zep/
├── .claude-plugin/
│   └── plugin.json          # manifest; declares the zep-docs MCP server
├── skills/
│   └── building-with-zep/
│       ├── SKILL.md
│       └── references/      # concepts, apis, customization, getting-started, evaluation, governance
└── README.md
```
