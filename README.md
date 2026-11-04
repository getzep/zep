<p align="center">
  <a href="https://www.getzep.com/">
    <img src="https://github.com/user-attachments/assets/119c5682-9654-4257-8922-56b7cb8ffd73" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">Zep: Examples and Integrations</h1>

<p align="center">
  <a href="https://twitter.com/intent/follow?screen_name=zep_ai" target="_new"><img alt="Twitter Follow" src="https://img.shields.io/twitter/follow/zep_ai"></a>
</p>

## About Zep

Zep is the enterprise context layer for reliable agents. Zep gives agents
governed access to company knowledge, and skills that Zep builds from agent tasks.

| Part of Zep | What it does | Docs | Examples |
| --- | --- | --- | --- |
| Context Lake | Unifies business data into governed Context Graphs for agents | [Context Lake](https://help.getzep.com/context-lake) | [Context Lake harnesses](examples/README.md#context-lake-harnesses) |
| Zep Agent Skills | Turns the tasks of your agents into skills that the agents follow | [Agent Skills](https://help.getzep.com/agent-skills) | [Agent Skills harnesses](examples/README.md#agent-skills-harnesses) |

Agents connect to Zep through MCP or the Zep SDKs. Read more at
[www.getzep.com](https://www.getzep.com/).

## About This Repository

This repository is **not** Zep's product or service. It contains **example code, agent
harnesses, framework integrations, and tools** for agents that use
[Zep Cloud](https://www.getzep.com/).

To use Zep Cloud, sign up at [www.getzep.com](https://www.getzep.com/) and read the
documentation at [help.getzep.com](https://help.getzep.com). Zep's official SDKs are:

- **Python**: `pip install zep-cloud`
- **TypeScript/JavaScript**: `npm install @getzep/zep-cloud`
- **Go**: `go get github.com/getzep/zep-go/v4`

> Looking for the open-source temporal knowledge graph framework that powers Zep? See
> [Graphiti](https://github.com/getzep/graphiti).

## Examples

[`examples/README.md`](examples/README.md) lists all of the agent harnesses and SDK
snippets.

### Context Lake

- [`agent-with-zep`](examples/python/agent-with-zep) and
  [`agent-with-zep-adk`](examples/python/agent-with-zep-adk): reference agents that
  retrieve from a Context Graph with tools, on PydanticAI and Google ADK.
- [`langgraph-agent`](examples/python/langgraph-agent),
  [`openai-agents-sdk`](examples/python/openai-agents-sdk), and
  [`typescript/eve`](examples/typescript/eve): agents with agent memory on other
  frameworks.

### Zep Agent Skills

- [`agent-skills-banking`](examples/python/agent-skills-banking): the full Agent Skills
  loop with PydanticAI.
- [`python/agent-skills-frameworks`](examples/python/agent-skills-frameworks): LangGraph,
  Google ADK, and Claude Agent SDK.
- [`typescript/agent-skills-frameworks`](examples/typescript/agent-skills-frameworks):
  Mastra, OpenAI Codex SDK, and Eve.

## Contents

| Project / directory | Description |
|---------------------|-------------|
| [`examples/`](examples/) | Agent harnesses, example apps, and snippets in Python, TypeScript, and Go |
| [`integrations/`](integrations/) | Agent-framework integration packages |
| [`ingestion/`](ingestion/) | `zep-ingest`, a bulk data ingestion pipeline (Slack, documents, email, JSON/CSV, fact triples) |
| [`ontology/`](ontology/) | Default ontology definitions |
| [Build with Zep](https://github.com/getzep/building-with-zep-plugin) | Agent plugin for Claude Code, Codex, and Cursor (separate repository) |
| [Zep Memory](https://github.com/getzep/zep-memory-plugin) | Agent plugin for Claude Desktop/Cowork and ChatGPT Work (separate repository) |
| [`benchmarks/`](benchmarks/) | Agent memory benchmarks (LoCoMo, LongMemEval) |
| [`zep-eval-harness/`](zep-eval-harness/) | Evaluation harness for ingestion and retrieval |
| [`legacy/`](legacy/) | Deprecated Zep Community Edition (unsupported) |

## Integrations

Framework integration packages live under [`integrations/`](integrations/), organized
framework-first then language: `integrations/<framework>/<language>/`. Each package is built,
tested, and released independently.

- **Python**: Google ADK, Microsoft Agent Framework, Microsoft AutoGen, AG2, CrewAI, LangGraph, LiveKit, Pydantic AI, Strands Agents
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
