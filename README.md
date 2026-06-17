<p align="center">
  <a href="https://www.getzep.com/">
    <img src="https://github.com/user-attachments/assets/119c5682-9654-4257-8922-56b7cb8ffd73" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">
Zep: Agent Memory at Enterprise Scale
</h1>

<h2 align="center">Examples, Integrations, & More</h2>

<br />

<p align="center">
  <a href="https://discord.gg/W8Kw6bsgXQ"><img
    src="https://img.shields.io/badge/Discord-%235865F2.svg?&logo=discord&logoColor=white"
    alt="Chat on Discord"
  /></a>
  <a href="https://twitter.com/intent/follow?screen_name=zep_ai" target="_new"><img alt="Twitter Follow" src="https://img.shields.io/twitter/follow/zep_ai"></a>
</p>

## What is Zep? 💬

Zep is an agent memory platform. It captures memory of your users, your business, and the work your agents do, assembles it into token-efficient context, and serves it back in under 200ms—managed, governed, and served at scale.

Zep builds a temporal knowledge graph from chat history, business data, and user interactions. When new information contradicts an existing fact, Zep marks the old fact invalid and records the new one, so agents can ask "what is true now" or "what was true on this date" and get the right answer to either. Every query is filtered by access policies, retention rules, and audit logging at the entity level.

### Three types of memory

- **User memory**: preferences, behaviors, and history
- **Business memory**: organizational data, customer records, and domain facts
- **Work memory**: the tasks, episodes, and interactions your agents perform

### How Zep works

1. **Ingest**: Send chat messages, business data, and events to Zep as they occur.
2. **Construct**: Zep builds a Context Graph connecting entities, facts, and relationships, and tracks how they change over time.
3. **Retrieve**: Get pre-assembled, token-efficient context in under 200ms, ready for your LLM.

At the center is the **Context Lake**: millions of Context Graphs managed as one governed system, served by Zep's Context Graph Engine.

## Getting Started

### Sign up for Zep Cloud

Visit [www.getzep.com](https://www.getzep.com/) to sign up for Zep Cloud, our managed agent memory service with sub-200ms retrieval, SOC 2 Type II compliance, and a HIPAA BAA. Deploy as a managed Cloud service, Cloud with your own encryption keys (BYOK), or in your own VPC (BYOC). Add agent memory in a few lines of code.

### Find Zep SDKs

Zep offers comprehensive SDKs for multiple languages:

- **Python**: `pip install zep-cloud`
- **TypeScript/JavaScript**: `npm install @getzep/zep-cloud`
- **Go**: `go get github.com/getzep/zep-go/v2`

### Get Help

- **Documentation**: [help.getzep.com](https://help.getzep.com)
- **Discord Community**: [Join our Discord](https://discord.gg/W8Kw6bsgXQ)
- **Support**: Visit our help website for comprehensive guides and tutorials

## About This Repository

**Note**: This repository is currently a work in progress.

This repository contains examples, integrations, and tools for building agent memory with Zep. Explore the example applications to see how Zep adds memory to agents built with Google ADK, Microsoft AutoGen, CrewAI, LiveKit, and other frameworks.

### Repository Structure

The repository includes:

- Example applications demonstrating agent memory with Zep
- Integration packages for popular agent frameworks
- Code samples for different use cases
- Development tools and utilities

## Development Setup

This project uses [UV](https://github.com/astral-sh/uv) for Python package management with workspace features.

### Prerequisites

- Python 3.13+
- UV package manager

### Getting Started

1. **Install UV** (if not already installed):
   ```bash
   # On macOS and Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # On Windows
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. **Sync the workspace**:
   ```bash
   uv sync
   ```

3. **Activate the virtual environment**:
   ```bash
   # On Unix/macOS
   source .venv/bin/activate
   
   # On Windows
   .venv\Scripts\activate
   ```

### Integrations

Framework integration packages live under [`integrations/`](integrations/), organized
framework-first then language: `integrations/<framework>/<language>/`. Each package is
built, tested, and released independently.

- **Available**: Google ADK, Microsoft AutoGen, CrewAI, and LiveKit (Python).
- **Develop**: `cd integrations/<framework>/python && uv sync --extra dev && uv run pytest`
- See [`integrations/README.md`](integrations/README.md) for the full list and
  [`integrations/CLAUDE.md`](integrations/CLAUDE.md) for structure and conventions.

## Contributing

We welcome contributions to help improve Zep and its ecosystem. Please see the [CONTRIBUTING.md](CONTRIBUTING.md) file for guidelines on how to contribute, including:

- Code contributions
- Documentation improvements
- Bug reports and feature requests
- Community examples and integrations

## Graphiti: The Knowledge Graph Framework

Zep is powered by [Graphiti](https://github.com/getzep/graphiti), an open-source temporal knowledge graph framework.

Graphiti builds and maintains knowledge graphs while reasoning about state changes over time. Each fact includes `valid_at` and `invalid_at` dates, so agents can see how relationships, preferences, and facts have evolved.

Visit the [Graphiti repository](https://github.com/getzep/graphiti) to learn more about the temporal knowledge graph framework that powers Zep's agent memory.


## Community Edition (Legacy)

**Note**: Zep Community Edition is no longer supported and has been deprecated. The Community Edition code has been moved to the `legacy/` folder in this repository.

For current Zep development, we recommend using Zep Cloud or exploring the example projects in this repository.

Read more about this change in our announcement: [Announcing a New Direction for Zep's Open Source Strategy](https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/)
