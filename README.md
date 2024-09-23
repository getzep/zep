<p align="center">
  <a href="https://www.getzep.com/">
    <img src="https://raw.githubusercontent.com/getzep/zep/main/assets/zep-logo-icon-gradient-rgb.svg" width="150" alt="Zep Logo">
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
  <a href="https://pypi.org/project/zep-python"><img alt="PyPI - Downloads" src="https://img.shields.io/pypi/dw/zep-python?label=pypi%20downloads"></a>
  <a href="https://www.npmjs.com/package/@getzep/zep-js"><img alt="@getzep/zep-js" src="https://img.shields.io/npm/dw/%40getzep/zep-js?label=npm%20downloads"></a>
  <img src="https://github.com/getzep/zep/actions/workflows/build-test.yml/badge.svg" alt="build/test" />
  <img
  src="https://github.com/getzep/zep/actions/workflows/golangci-lint.yml/badge.svg"
  alt="GoLangCI Lint"
  />
</p>

<p align="center">
<a href="https://help.getzep.com/ce/quickstart">Quick Start</a> | 
<a href="https://help.getzep.com/memory">Documentation</a> | 
<a href="https://help.getzep.com">Zep Cloud Docs</a>
</p>

## What is Zep? 💬

Zep continually learns from user interactions, improving your AI agent's knowledge over time. With Zep, you can personalize user experiences and significantly improve agent accuracy.

Zep is powered by a temporal Knowledge Graph. As your user's conversation with an agent progresses, new facts are added to the graph. Zep maintains historical context, helping your agent reason with state change and offering data provenance insights.

Retrieving facts is simple and very fast, with both semantic and graph search used to ensure facts are relevant to the current conversation. Fact retrieval does not require LLM inference, with the slowest activity being embedding the search query.

Zep supports:

- Adding chat history messages.
- Ingestion of JSON and unstructured text.
- Session, user, and group-level graphs. Group graphs allow for capturing organizational knowledge.

## Simple APIs with SDKs for Python, TypeScript, and Go

Persisting chat history memory is simple and fast.

```python
result = await client.memory.add(session_id, messages=messages)
```

Zep's high-level memory API offers an optionated retrieval API, which uses BM25, semantic, and graph search to retrieve facts relevant to the current conversation. Results are reranked by distance from the user node, further improving relevance.

```python
memory = client.memory.get(session_id="session_id")
```

Lower-level APIs for search and CRUD are also available.

## Why does Zep use a temporal Knowledge Graph?

> A Knowledge Graph is a network of interconnected facts, such as “Kendra loves Adidas shoes.” Each fact is a “triplet” represented by two entities, or nodes (”Kendra”, “Adidas shoes”), and their relationship, or edge (”loves”).

Knowledge Graphs allow us to model an agent's complex world and offer a superior approach to retrieval than semantic search alone, which is commonly used in RAG. Most approaches to buiilding Knowledge Graphs don't reason well with state changes. Facts inevitablely change over time as users provide new information or business data changes.

Most graph-building tools don't reason well with state changes. Zep incorporates a temporal Knowledge Graph library, [Graphiti](https://github.com/getzep/graphiti), which we developed to address this challenge. What makes Graphiti unique is its ability to autonomously build a Knowledge Graph while handling changing relationships and maintaining historical context.

Graphiti also offers Zep the ability to ingest not just chat history, but also JSON business data and unstructured text.

## Is Zep tied to a framework such as LangChain?

Zep is framework agnostic. You may use it with LangChain, LangGraph, Chainlit, Microsoft Autogen, and more.

## What is Zep Community Edition? ⭐️

Zep Community Edition is an open source licensed distribution of Zep and is contained in this repo. Zep Community Edition is shares APIs with Zep Cloud, with [comprehensive documentation](https://help.getzep.com) available.

## What is Zep Cloud? ⚡️

[Zep Cloud](https://www.getzep.com/) is a managed service with Zep Community Edition at its core. In addition to Zep Community Edition's memory layer, Zep Cloud offers:

- **Low Latency, Scalability, High Availability:** Our cloud is designed to scale to the needs of customers with millions of DAUs and is SOC II Type 2 certified. Zep utilizes self-hosted LLMs and embedding models, offering customers very low-latency memory retrieval and graph-building.
- **Dialog Classification:** Instantly and accurately classify chat dialog. Understand user intent and emotion, segment users, and more. Route chains based on semantic context, and trigger events.
- **Structured Data Extraction:** Quickly extract business data from chat conversations using a schema you define. Understand what your Assistant should ask for next in order to complete its task.

## Why use Zep for long-term memory?

### Why not just include the entire chat history in the prompt?

With increased LLM context lengths, it may be tempting to include entire an chat history in a prompt, alongside RAG results, and other instructions. Unfortunately, we've seen poor temporal reasoning, poor recall, hallucinations, and slow and expensive inference as a result.

### Why not use Redis, Postgres, or ... to persist chat histories?

As discussed above, providing just the chat history to an LLM can often result in poor temporal reasoning.

### Zep is purpose-built for Agent and Assistant applications

Users, Sessions, and Chat Messages are first-class abstractions in Zep. This allows simple and flexible management of chat memory, including the execution of Right To Be Forgetten requests and other privacy compliance-related tasks with single-API call.

## Zep Language Support and Ecosystem

### Does Zep have Python, TypeScript, and Go support?

Yes - Zep offers Python & TypeScript/JS SDKs for easy integration with your Assistant app. We also have examples of using Zep with popular frameworks - see below.

### Can I use Zep with LangChain, LlamaIndex, Vercel AI, n8n, FlowWise, ...?

Yes - the Zep team and community contributors have built integrations with Zep, making it simple to, for example, drop Zep's memory components into a LangChain app. Please see the [Zep Documentation](https://help.getzep.com/) and your favorite framework's documentation for more.

## Zep Community Edition LLM Service Dependencies

Zep Community Edition relies on an external LLM API service to function. Any OpenAI-compatible LLM API is supported. Providers such as Anthropic can be used via a proxy such as LiteLLM. Note that you will also need to configure LiteLLM with an embedding service.

## Examples

### Create Users, Chat Sessions, and Chat Messages (Zep Python SDK)

```python
user_request = CreateUserRequest(
    user_id=user_id,
    email="user@example.com",
    first_name="Jane",
    last_name="Smith",
    metadata={"foo": "bar"},
)
new_user = client.user.add(user_request)

# create a chat session
session_id = uuid.uuid4().hex # A new session identifier
session = Session(
            session_id=session_id,
            user_id=user_id,
            metadata={"foo" : "bar"}
        )
client.memory.add_session(session)

# Add a chat message to the session
history = [
     { role: "human", content: "Who was Octavia Butler?" },
]
messages = [Message(role=m.role, content=m.content) for m in history]
memory = Memory(messages=messages)
client.memory.add_memory(session_id, memory)

# Get all sessions for user_id
sessions = client.user.getSessions(user_id)
```

### Persist Chat History with LangChain.js (Zep TypeScript SDK)

```typescript
const memory = new ZepMemory({
  sessionId,
  baseURL: zepApiURL,
  apiKey: zepApiKey,
});
const chain = new ConversationChain({ llm: model, memory });
const response = await chain.run({
  input = "What is the book's relevance to the challenges facing contemporary society?",
});
```

### Hybrid similarity search over a document collection with text input and JSONPath filters (TypeScript)

```typescript
const query = "Who was Octavia Butler?";
const searchResults = await collection.search({ text: query }, 3);

// Search for documents using both text and metadata
const metadataQuery = {
  where: { jsonpath: '$[*] ? (@.genre == "scifi")' },
};

const newSearchResults = await collection.search(
  {
    text: query,
    metadata: metadataQuery,
  },
  3
);
```

## Get Started

### Install Server

Please see the [Zep Quick Start Guide](https://docs.getzep.com/deployment/quickstart/) for important configuration information.

```bash
docker compose up
```

Looking for <a href="https://docs.getzep.com/deployment">other deployment options</a>?

### Install SDK

Please see the Zep [Develoment Guide](https://docs.getzep.com/sdk/) for important beta information and usage instructions.

```bash
pip install zep-python
```

**or**

```bash
npm i @getzep/zep-js
```

## How does Zep Community Edition differ from Zep Open Source v0.x?

Zep Open Source is an older version of Zep that did not use a Knowledge Graph to persist and recall memory.

Some additional changes:

- The Zep OSS web UI has been deprecated in favor of significantly expanded SDK support.
- The Zep CE supports broad number of LLM services and local servers that offer OpenAI-compatible APIs. Other services may be used with an LLM proxy.
- Zep CE no longer ships with a local embedding service and named entity extractor.

### Is there a migration path from Zep Open Source to Zep Community Edition?

There have been significant changes to how Zep operates and unfortunately we have not been able to devise a migration path from Zep OSS to Zep CE.

Zep OSS will remain available in our container repo but will not see future enhancements or bug fixes. The code is available in the `legacy` branch in this repo.

## Contributing

We welcome contributions. See the [`CONTRIBUTING`](CONTRIBUTING.md) file in this repo for more.
