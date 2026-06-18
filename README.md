<p align="center">
  <a href="https://www.getzep.com/">
    <img src="https://github.com/user-attachments/assets/119c5682-9654-4257-8922-56b7cb8ffd73" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">Zep Cloud: Examples & Integrations</h1>

<p align="center">
  <a href="https://discord.gg/W8Kw6bsgXQ"><img
    src="https://img.shields.io/badge/Discord-%235865F2.svg?&logo=discord&logoColor=white"
    alt="Chat on Discord"
  /></a>
  <a href="https://twitter.com/intent/follow?screen_name=zep_ai" target="_new"><img alt="Twitter Follow" src="https://img.shields.io/twitter/follow/zep_ai"></a>
</p>

## About This Repository

This repository is **not** Zep's product or service. It contains **example code, framework
integrations, and tools** for building agent memory with [Zep Cloud](https://www.getzep.com/),
Zep's managed agent memory platform.

To use Zep Cloud, sign up at [www.getzep.com](https://www.getzep.com/) and read the
documentation at [help.getzep.com](https://help.getzep.com). Zep's official SDKs are:

- **Python**: `pip install zep-cloud`
- **TypeScript/JavaScript**: `npm install @getzep/zep-cloud`
- **Go**: `go get github.com/getzep/zep-go/v2`

> Looking for the open-source temporal knowledge graph framework that powers Zep? See
> [Graphiti](https://github.com/getzep/graphiti).

## Contents

| Directory | Description |
|-----------|-------------|
| [`examples/`](examples/) | Example apps and snippets in Python, TypeScript, and Go |
| [`integrations/`](integrations/) | Agent-framework integration packages (ADK, AutoGen, CrewAI, LiveKit) |
| [`ontology/`](ontology/) | Default ontology definitions |
| [`plugins/`](plugins/) | Plugins for building with Zep |
| [`benchmarks/`](benchmarks/) | Memory benchmarks (LoCoMo, LongMemEval) |
| [`zep-eval-harness/`](zep-eval-harness/) | Evaluation harness for ingestion and retrieval |
| [`legacy/`](legacy/) | Deprecated Zep Community Edition (unsupported) |

## Examples

The [`examples/`](examples/) directory holds runnable samples per language:

- **[Python](examples/python/)** — quickstart, graph, chat history, context templates, and
  framework demos (LangGraph, OpenAI Agents SDK, AutoGen, ElevenLabs)
- **[TypeScript](examples/typescript/)** — graph, memory, users, LangGraph, and graph
  visualization
- **[Go](examples/go/)** — conversations, user graph, entity types, chunking

## Integrations

Framework integration packages live under [`integrations/`](integrations/), organized
framework-first then language: `integrations/<framework>/<language>/`. Each package is built,
tested, and released independently.

- **Available**: Google ADK, Microsoft AutoGen, CrewAI, and LiveKit (Python)
- **Develop**: `cd integrations/<framework>/python && uv sync --extra dev && uv run pytest`
- See [`integrations/README.md`](integrations/README.md) for the full list and
  [`integrations/CLAUDE.md`](integrations/CLAUDE.md) for conventions.

## Development Setup

This project uses [UV](https://github.com/astral-sh/uv) for Python package management.

```bash
# Install UV (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync the workspace
uv sync
```

Requires Python 3.13+.

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines covering code,
documentation, bug reports, and community examples.

## Community Edition (Deprecated)

Zep Community Edition is no longer supported. Its code has been moved to the
[`legacy/`](legacy/) folder. Read more in
[Announcing a New Direction for Zep's Open Source Strategy](https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/).
