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

Zep enhances your AI agent's knowledge through continuous learning from user interactions, enabling personalized experiences and improved accuracy.

### How Zep works

1. Add chat messages or data artifacts to Zep during each user interaction or agent event.
2. Zep intelligently integrates new information into the user's Knowledge Graph, updating existing context as needed.
3. Retrieve relevant facts from Zep for subsequent interactions or events.

Zep's temporal Knowledge Graph maintains contextual information about facts, enabling reasoning about state changes and providing data provenance insights. Each fact includes `valid_at` and `invalid_at` dates, allowing agents to track changes in user preferences, traits, or environment.

### Zep is fast

Retrieving facts is simple and very fast. Unlike other memory solutions, Zep does not use agents to ensure facts are relevant. It precomputes facts, entity summaries, and other artifacts asynchronously. Retrieval speed primarily depends on the embedding service's performance.

### Zep supports many types of data

You can add a variety of data artifacts to Zep:
- Adding chat history messages.
- Ingestion of JSON and unstructured text. (Coming soon)


Zep supports chat session, user, and group-level graphs. Group graphs allow for capturing organizational knowledge.

## Getting Started

### Install Server

Please see the [Zep Quick Start Guide](https://help.getzep.com/ce/quickstart) for important configuration information.

```bash
./zep pull
./zep up
```
> [!NOTE]
> Make sure to set the `secret` value in the `zep.yaml` configuration file.
>
> Additionally, make sure that you expose an `OPENAI_API_KEY` environment variable either in a local .env file or by running
> ```bash
> export OPENAI_API_KEY=your_openai_api_key
> ```

### Install SDK
```bash
pip install zep-python
```

**or**

```bash
npm i @getzep/zep-js
```

## Simple APIs with SDKs for Python, TypeScript, and Go

Persisting chat history memory is simple and fast.

```python
result = await client.memory.add(session_id, messages=messages)
```

Zep's high-level memory API offers an opinionated retrieval API, which uses BM25, semantic, and graph search to retrieve facts relevant to the current conversation. Results are reranked by distance from the user node, further improving relevance.

```python
memory = client.memory.get(session_id="session_id")
```

Lower-level APIs for search and CRUD are also available.

## Why does Zep use a temporal Knowledge Graph?

> A Knowledge Graph is a network of interconnected facts, such as “Kendra loves Adidas shoes.” Each fact is a “triplet” represented by two entities, or nodes (”Kendra”, “Adidas shoes”), and their relationship, or edge (”loves”).

Knowledge Graphs allow us to model an agent's complex world and offer a superior retrieval approach than semantic search alone, which is commonly used in RAG. Most approaches to building Knowledge Graphs don't reason well with state changes. Facts inevitably change over time as users provide new information or business data changes.

<p align="center">
<img src="https://github.com/user-attachments/assets/52ecafc9-5a83-44cc-a020-04bc50904d0b" alt="Graphiti Overview" width="650">
</p>

Most graph-building tools don't reason well with state changes. Zep incorporates a temporal Knowledge Graph library, [Graphiti](https://github.com/getzep/graphiti), which we developed to address this challenge. What makes Graphiti unique is its ability to autonomously build a Knowledge Graph while handling changing relationships and maintaining historical context.

Graphiti also offers Zep the ability to ingest chat history, JSON business data, and unstructured text.

## Is Zep tied to a framework such as LangChain?

Zep is framework agnostic. It can be used with LangChain, LangGraph, Chainlit, Microsoft Autogen, and more.

## What is Zep Community Edition? ⭐️

Zep Community Edition is an open-source Zep distribution. It shares APIs with Zep Cloud and has [comprehensive documentation](https://help.getzep.com) available.

## What is Zep Cloud? ⚡️

[Zep Cloud](https://www.getzep.com/) is a managed service with Zep Community Edition at its core. In addition to Zep Community Edition's memory layer, Zep Cloud offers:

- **Low Latency, Scalability, High Availability:** Our cloud is designed to scale to the needs of customers with millions of DAUs and is SOC II Type 2 certified. Zep utilizes self-hosted LLMs and embedding models, offering customers very low-latency memory retrieval and graph-building.
- **Dialog Classification:** Instantly and accurately classify chat dialog. Understand user intent and emotion, segment users, and more. Route chains based on semantic context, and trigger events.
- **Structured Data Extraction:** Quickly extract business data from chat conversations using a schema you define. Understand what your assistant should ask for next to complete the task.

## Why use Zep for long-term memory?

### Why not just include the entire chat history in the prompt?

With increased LLM context lengths, including the entire chat history, RAG results, and other instructions in a prompt may be tempting. Unfortunately, this has resulted in poor temporal reasoning, poor recall, hallucinations, and slow and expensive inference.

### Why not use Redis, Postgres, or ... to persist chat histories?

As discussed above, providing just the chat history to an LLM can often result in poor temporal reasoning.

### Zep is purpose-built for Agent and Assistant applications

Users, Sessions, and Chat Messages are first-class abstractions in Zep. This allows simple and flexible management of chat memory, including the execution of Right To Be Forgetten requests and other privacy compliance-related tasks with single-API call.

## Zep Language Support and Ecosystem

### Does Zep have Python, TypeScript, and Go support?

Yes - Zep offers Python & TypeScript/JS SDKs for easy integration with your Assistant app. We also have examples of using Zep with popular frameworks - see below.

### Can I use Zep with LangChain, LlamaIndex, Vercel AI, n8n, FlowWise, ...?

Yes - the Zep team and community contributors have built integrations with Zep, making it simple to, for example, drop Zep's memory components into a LangChain app. Please see the [Zep Documentation](https://help.getzep.com/) and your favorite framework's documentation.

## Zep Community Edition LLM Service Dependencies

Zep Community Edition relies on an external LLM API service to function. Any OpenAI-compatible LLM API is supported. Providers such as Anthropic can be used via a proxy such as LiteLLM. You will also need to configure LiteLLM with an embedding service.

## Does Zep collect telemetry?
In order to better understand how Zep is used, we can collect telemetry data. This is optional and can be disabled by modifying the `zep.yaml` config file as below.

We do not collect any PII or any of your data, other than the `org_name` you may optionally set in the `telemetry` config. We only collect anonymized data about how Zep is used. 

```yaml
telemetry:
  disabled: false
```

## Examples

### Python SDK
```python
import uuid
from zep_python.client import AsyncZep
from zep_python.types import Message

client = AsyncZep(
    api_key=API_KEY,
    base_url=BASE_URL,
)

user_id = uuid.uuid4().hex # A new user identifier
new_user = await client.user.add(
    user_id=user_id,
    email="user@example.com",
    first_name="Jane",
    last_name="Smith",
    metadata={"foo": "bar"},
)

# create a chat session
session_id = uuid.uuid4().hex # A new session identifier
session = await client.memory.add_session(
    session_id=session_id,
    user_id=user_id,
    metadata={"foo" : "bar"}
)

# Add a memory to the session
await client.memory.add_memory(
    session_id=session_id,
    messages=[
        Message(
            role_type = "user", # One of ("system", "assistant", "user", "function", "tool")
            role = "Researcher", # Optional, a use case specific string representing the role of the user
            content = "Who was Octavia Butler?", # The message content
        )
    ],
)

# Get session memory
memory = await client.memory.get(session_id=session_id)
messages = memory.messages # List of messages in the session (quantity determined by optional lastn parameter in memory.get)
relevant_facts = memory.relevant_facts # List of facts relevant to the recent messages in the session

# Search user facts across all sessions
search_response = await client.memory.search_sessions(
    user_id=user_id,
    search_scope="facts",
    text="What science fiction books did I recently read?",
)
facts = [r.fact for r in search_response.results]
```

### TypeScript SDK
```typescript
import { v4 as uuidv4 } from 'uuid';
import { ZepClient } from '@getzep/zep-js';
import type { CreateUserRequest, CreateSessionRequest, SessionSearchQuery } from '@getzep/zep-js/api';

const client = new ZepClient({
    apiKey: API_KEY,
    baseUrl: BASE_URL,
});

// A new user identifier
const userId = uuidv4();
const userRequest: CreateUserRequest = {
    userId: userId,
    email: "user@example.com",
    firstName: "Jane",
    lastName: "Smith",
    metadata: { foo: "bar" },
};
const newUser = await client.user.add(userRequest);

// Create a chat session
const sessionId = uuidv4();
const sessionRequest: CreateSessionRequest = {
    sessionId: sessionId,
    userId: userId,
    metadata: { foo: "bar" },
};

// A new session identifier
const session = await client.memory.addSession(sessionRequest);

// Add a memory to the session
await client.memory.add(sessionId, {
    messages: [
        {
            role: "Researcher",
            roleType: "user",
            content: "Who was Octavia Butler?",
        },
    ],
});

// Get session memory
const memory = await client.memory.get(sessionId);
const messages = memory.messages; // List of messages in the session (quantity determined by optional lastN parameter in memory.get)
const relevantFacts = memory.relevantFacts; // List of facts relevant to the recent messages in the session

// Search user facts across all sessions
const searchQuery: SessionSearchQuery = {
    userId: userId,
    searchScope: "facts",
    text: "What science fiction books did I recently read?",
};
const searchResponse = await client.memory.searchSessions(searchQuery);
const facts = searchResponse.results?.map(result => result.fact);
```

## How does Zep Community Edition differ from Zep Open Source v0.x?

Zep Open Source is an older version of Zep that did not use a Knowledge Graph to persist and recall memory.

Some additional changes:

- The Zep OSS web UI has been deprecated in favor of significantly expanded SDK support.
- Zep CE no longer offers Document Collections. We suggest using one of many hosted or local vector databases.
- Zep CE supports many LLM services and local servers that offer OpenAI-compatible APIs. Other services may be used with an LLM proxy.
- Zep CE no longer ships with a local embedding service and named entity extractor.

### Is there a migration path from Zep Open Source to Zep Community Edition?

Significant changes have been made to Zep, and unfortunately, we have not been able to devise a migration path from Zep OSS to Zep CE.

Zep OSS will remain available in our container repo, but we will not see future enhancements or bug fixes. The code is available in the `legacy` branch in this repo.

## Contributing

We welcome contributions. For more, see the [`CONTRIBUTING`](CONTRIBUTING.md) file in this repo.
