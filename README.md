<p align="center">
  <a href="https://www.getzep.com/">
    <img src="https://github.com/user-attachments/assets/119c5682-9654-4257-8922-56b7cb8ffd73" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">
Zep: The Memory Foundation For Your AI Stack
</h1>

<h2 align="center">Build AI agents that continually learn. Power personalized experiences.</h2>

<br />

<p align="center">
  <a href="https://discord.gg/W8Kw6bsgXQ"><img
    src="https://dcbadge.vercel.app/api/server/W8Kw6bsgXQ?style=flat"
    alt="Chat on Discord"
  /></a>
  <a href="https://twitter.com/intent/follow?screen_name=zep_ai" target="_new"><img alt="Twitter Follow" src="https://img.shields.io/twitter/follow/zep_ai"></a>
</p>

## What is Zep? 💬

Zep is a memory platform for AI agents that learns from user interactions and business data. It builds a temporal knowledge graph to provide AI assistants with personalized, accurate, and up-to-date information, enhancing user experiences through continuous learning.

### How Zep works

1. Add chat messages or data artifacts to Zep during each user interaction or agent event
2. Zep intelligently integrates new information into the user's Knowledge Graph, updating existing context as needed
3. Retrieve relevant facts from Zep for subsequent interactions or events

Zep's temporal Knowledge Graph maintains contextual information about facts, enabling reasoning about state changes and providing data provenance insights.

## Getting Started

### Sign up for Zep Cloud

Visit [www.getzep.com](https://www.getzep.com/) to sign up for Zep Cloud, our managed service that offers low latency, scalability, and high availability with additional features like dialog classification and structured data extraction.

### Find Zep SDKs

Zep offers comprehensive SDKs for multiple languages:

- **Python**: `pip install zep-python`
- **TypeScript/JavaScript**: `npm i @getzep/zep-js`
- **Go**: Available on GitHub

### Get Help

- **Documentation**: [help.getzep.com](https://help.getzep.com)
- **Discord Community**: [Join our Discord](https://discord.gg/W8Kw6bsgXQ)
- **Support**: Visit our help website for comprehensive guides and tutorials

## About This Repository

**Note**: This repository is currently a work in progress.

This repository contains example projects, code samples, and other components to help you get started with Zep. Explore the examples to see how Zep integrates with popular frameworks like LangChain, LlamaIndex, and others.

### Repository Structure

The repository includes:

- Example applications demonstrating Zep integration
- Code samples for different use cases
- Additional tools and utilities
- Legacy code (see Community Edition section below)

## Contributing

We welcome contributions to help improve Zep and its ecosystem. Please see the [CONTRIBUTING.md](CONTRIBUTING.md) file for guidelines on how to contribute, including:

- Code contributions
- Documentation improvements
- Bug reports and feature requests
- Community examples and integrations

## Graphiti: The Knowledge Graph Framework

Zep is powered by [Graphiti](https://github.com/getzep/graphiti), an open-source temporal knowledge graph framework that we developed to handle changing relationships and maintain historical context.

What makes Graphiti unique is its ability to autonomously build a knowledge graph while reasoning about state changes over time. Each fact includes `valid_at` and `invalid_at` dates, allowing agents to track changes in user preferences, traits, or environment.

Visit the [Graphiti repository](https://github.com/getzep/graphiti) to learn more about the knowledge graph framework that powers Zep's memory capabilities.

## Framework Integration

Zep is framework agnostic and works seamlessly with:

- LangChain
- LangGraph
- LlamaIndex
- Chainlit
- Microsoft Autogen
- And many more

## Community Edition (Legacy)

**Note**: Zep Community Edition is no longer supported and has been deprecated. The Community Edition code has been moved to the `legacy/` folder in this repository.

For current Zep development, we recommend using Zep Cloud or exploring the example projects in this repository.

Read more about this change in our announcement: [Announcing a New Direction for Zep's Open Source Strategy](https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/)
