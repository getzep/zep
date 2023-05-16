[![Build/Test](https://github.com/getzep/zep/actions/workflows/build-test.yml/badge.svg)](https://github.com/getzep/zep/actions/workflows/build-test.yml) [![Docker](https://github.com/getzep/zep/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/getzep/zep/actions/workflows/docker-publish.yml) [![golangci-lint](https://github.com/getzep/zep/actions/workflows/golangci-lint.yml/badge.svg)](https://github.com/getzep/zep/actions/workflows/golangci-lint.yml)
[![License: Apache](https://img.shields.io/badge/License-Apache-blue.svg)](https://img.shields.io/github/license/getzep/zep)

# Zep: A long-term memory store for LLM applications
Zep stores, summarizes, embeds, indexes, and enriches LLM app / chatbot histories, and exposes them via simple, low-latency APIs. Zep allows developers to focus on developing their AI apps, rather than on building memory persistence, search, and enrichment infrastructure.

Zep's Extractor model is easily extensible, with a simple, clean interface available to build new enrichment functionality, such as summarizers, entity extractors, embedders, and more.

**Key Features:**
- **Long-term memory persistence**, with access to historical messages irrespective of your summarization strategy.
- **Auto-summarization** of memory messages based on a configurable message window. A series of summaries are stored, providing flexibility for future summarization strategies.
- **Vector search** over memories, with messages automatically embedded on creation. 
- **Auto-token counting** of memories and summaries, allowing finer-grained control over prompt assembly.
- **Python** and **JavaScript** SDKs.

Coming (very) soon:
- Langchain `memory` and `retriever` support.
- Support for other conversational and agentic AI frameworks.

## Quick Start
Read the docs: [https://getzep.github.io](https://getzep.github.io/)

1. Clone this repo
```bash
git clone https://github.com/getzep/zep.git
```
2. Add your OpenAI API key to a `.env` file in the root of the repo:
```bash
ZEP_OPENAI_API_KEY=<your key here>
```
3. Start the Zep server:
```bash
docker-compose up
```
This will start a Zep server on port 8000, and a Postgres database on port 5432.

4. Access Zep via the Python or Javascript SDKs:

**Python**
```python
async with ZepClient(base_url) as client:
    role = "user"
    content = "who was the first man to go to space?"
    message = Message(role=role, content=content)
    memory = Memory()
    memory.messages = [message]
    # Add a memory
    result = await client.aadd_memory(session_id, memory)
```
See [zep-python](https://github.com/getzep/zep-python) for installation and use docs.

**Javascript**
```Javascript
 // Add memory
 const role = "user";
 const content = "I'm looking to plan a trip to Iceland. Can you help me?"
 const message = new Message({ role, content });
 const memory = new Memory();
 memory.messages = [message];
 const result = await client.addMemoryAsync(session_id, memory);
...
```

## Zep Documentation
Server installation and SDK usage documentation is available here: [https://getzep.github.io](https://getzep.github.io/)

## Acknowledgements
h/t to the [Motorhead](https://github.com/getmetal/motorhead) and [Langchain](https://github.com/hwchase17/langchain) projects for inspiration.
