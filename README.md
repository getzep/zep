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
- **Go**: `go get github.com/getzep/zep-go/v3`

> Looking for the open-source temporal knowledge graph framework that powers Zep? See
> [Graphiti](https://github.com/getzep/graphiti).

## Contents

| Directory | Description |
|-----------|-------------|
| [`examples/`](examples/) | Example apps and snippets in Python, TypeScript, and Go |
| [`integrations/`](integrations/) | Agent-framework integration packages |
| [`ontology/`](ontology/) | Default ontology definitions |
| [`plugins/`](plugins/) | Plugins for building with Zep |
| [`benchmarks/`](benchmarks/) | Memory benchmarks (LoCoMo, LongMemEval) |
| [`zep-eval-harness/`](zep-eval-harness/) | Evaluation harness for ingestion and retrieval |
| [`legacy/`](legacy/) | Deprecated Zep Community Edition (unsupported) |

## Integrations

Framework integration packages live under [`integrations/`](integrations/), organized
framework-first then language: `integrations/<framework>/<language>/`. Each package is built,
tested, and released independently.

- **Python**: Google ADK, Microsoft Agent Framework, Microsoft AutoGen, AG2, CrewAI, LangGraph, LiveKit, Pydantic AI
- **TypeScript**: Google ADK, Mastra, Vercel AI SDK
- **Go**: Google ADK

See [`integrations/README.md`](integrations/README.md) for packages, release status, and
links, and [`integrations/CLAUDE.md`](integrations/CLAUDE.md) for conventions.

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines covering code,
documentation, bug reports, and community examples.

## Community Edition (Deprecated)

Zep Community Edition is no longer supported. Its code has been moved to the
[`legacy/`](legacy/) folder. Read more in
[Announcing a New Direction for Zep's Open Source Strategy](https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/).
