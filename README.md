<p align="center">
  <a href="https://www.getzep.com/">
    <img src="https://raw.githubusercontent.com/getzep/zep/main/assets/zep-logo-icon-gradient-rgb.svg" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">
Zep: Long-Term Memory for ‍AI Assistants.
</h1>
<h2 align="center">Recall, understand, and extract data from chat histories. Power personalized AI experiences.</h2>
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
<a href="https://docs.getzep.com/deployment/quickstart/">Quick Start</a> | 
<a href="https://docs.getzep.com/">Documentation</a> | 
<a href="https://docs.getzep.com/sdk/langchain/">LangChain</a> and 
<a href="https://docs.getzep.com/sdk/langchain/">LlamaIndex</a> Support | 
<a href="https://discord.gg/W8Kw6bsgXQ">Discord</a><br />
<a href="https://www.getzep.com">www.getzep.com</a>
</p>

## What is Zep? 💬 
Zep is a long-term memory service for AI Assistant apps. With Zep, you can provide AI assistants with the ability to recall past conversations, no matter how distant, while also reducing hallucinations, latency, and cost. 

### How Zep works

Zep persists and recalls chat histories, and automatically generates summaries and other artifacts from these chat histories. It also embeds messages and summaries, enabling you to search Zep for relevant context from past conversations. Zep does all of this asyncronously, ensuring these operations don't impact your user's chat experience. Data is persisted to database, allowing you to scale out when growth demands. 

Zep also provides a simple, easy to use abstraction for document vector search called Document Collections. This is designed to complement Zep's core memory features, but is not designed to be a general purpose vector database.

Zep allows you to be more intentional about constructing your prompt: 
1. automatically adding a few recent messages, with the number customized for your app;
2. a summary of recent conversations prior to the messages above;
3. and/or contextually relevant summaries or messages surfaced from the entire chat session.
4. and/or relevant Business data from Zep Document Collections.

## What is Zep Cloud? ⚡️ 

[Zep Cloud](https://www.getzep.com/) is a managed service with Zep Open Source at its core. In addition to Zep Open Source's memory management features, Zep Cloud offers:
- **Fact Extraction:** Automatically build fact tables from conversations, without having to define a data schema upfront.
- **Dialog Classification:** Instantly and accurately classify chat dialog. Understand user intent and emotion, segment users, and more. Route chains based on semantic context, and trigger events. 
- **Structured Data Extraction:** Quickly extract business data from chat conversations using a schema you define. Understand what your Assistant should ask for next in order to complete its task.

## Why use Zep for long-term memory?

### Why not just include the entire chat history in the prompt? 

With increased LLM context lengths, it may be tempting to include entire an chat history in a prompt, alongside RAG results, and other instructions. Unfortunately, we've seen poor recall, hallucinations, and slow and expensive inference as a result. 

### Why not use Redis, Postgres, a Vector Database, or ... to persist chat histories?

Our goal with Zep is to elevate the layer of abstraction for memory management. We believe developer productivity is best served by infrastructure with well-designed abstractions, rather than building peristence, summarization, extraction, embedding management, and search from the ground up.

### Is Zep a vector database?

No. Zep uses embeddings and vector database capaiblities under the hood to power many of its features, but is not designed to be a general purpose vector database. 

### Zep is purpose-built for Assistant applications

Users, Sessions, and Chat Messages are first-class abstractions in Zep. This allows simple and flexible management of chat memory, including the execution of Right To Be Forgetten requests and other privacy compliance-related tasks with single-API call. 

## Zep Language Support and Ecosystem

### Does Zep have Python and TypeScript support?

Yes - Zep offers Python & TypeScript/JS SDKs for easy integration with your Assistant app. We also have examples of using Zep with popular frameworks - see below.


### Can I use Zep with LangChain, LlamaIndex, Vercel AI, n8n, FlowWise, ...?

Yes - the Zep team and community contributors have built integrations with Zep, making it simple to, for example, drop Zep's memory components into a LangChain app. Please see the [Zep Documentation](https://docs.getzep.com/) and your favorite framework's documentation for more.

## Zep Open Source LLM Service Dependencies

Zep Open Source relies on an external LLM API service to function. OpenAI, Azure OpenAI, Anthropic, and OpenAI-compatible APIs are all supported. 


## Learn more
- 🏎️ **[Quick Start Guide](https://docs.getzep.com/deployment/quickstart/)**: Docker deployment, and coding, in < 5 minutes.
- 📚 **[Zep By Example](https://docs.getzep.com/sdk/examples/)**: Learn how to use Zep by example.
- 🦙 **[Building Apps with LlamaIndex](https://docs.getzep.com/sdk/llamaindex/)**
- 🦜⛓️ **[Building Apps with LangChain](https://docs.getzep.com/sdk/langchain/)**
- 🛠️ [**Getting Started with TypeScript/JS or Python**](https://docs.getzep.com/sdk/)

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
const response = await chain.run(
    {
        input="What is the book's relevance to the challenges facing contemporary society?"
    },
);
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

### Create a LlamaIndex Index using Zep as a VectorStore (Python)
```python
from llama_index import VectorStoreIndex, SimpleDirectoryReader
from llama_index.vector_stores import ZepVectorStore
from llama_index.storage.storage_context import StorageContext

vector_store = ZepVectorStore(
    api_url=zep_api_url,
    api_key=zep_api_key,
    collection_name=collection_name
)

documents = SimpleDirectoryReader("documents/").load_data()
storage_context = StorageContext.from_defaults(vector_store=vector_store)
index = VectorStoreIndex.from_documents(
                            documents,
                            storage_context=storage_context
)
```                  

### Search by embedding (Zep Python SDK)
```python
# Search by embedding vector, rather than text query
# embedding is a list of floats
results = collection.search(
    embedding=embedding, limit=5
)
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
