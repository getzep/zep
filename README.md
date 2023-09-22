<p align="center">
  <a href="https://squidfunk.github.io/mkdocs-material/">
    <img src="https://github.com/getzep/zep/blob/main/assets/zep-bot-square-200x200.png?raw=true" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">
Zep: Fast, scalable building blocks for LLM apps
</h1>
<h2 align="center">Chat history memory, embedding, vector search, data enrichment, and more.</h2>
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

## What is Zep?
Zep is an open source platform for productionizing LLM apps. Zep summarizes, embeds, and enriches chat histories and documents asynchronously, ensuring these operations don't impact your user's chat experience. Data is persisted to database, allowing you to scale out when growth demands. As drop-in replacements for popular LangChain components, you can get to production in minutes without rewriting code.

[![Zep Demo Video](https://img.youtube.com/vi/d6ryNEvMXno/maxresdefault.jpg)](https://vimeo.com/865785086?share=copy)


## ⭐️ Core Features
### 💬 Designed for building conversational LLM applications
- Manage users, sessions, chat messages, chat roles, and more, not just texts and embeddings.
- Build autopilots, agents, Q&A over docs apps, chatbots, and more. 

### ⚡️ Fast, scalable, low-latency APIs and stateless deployments
- Zep’s local embedding models and async enrichment ensure a snappy user experience. 
- Storing documents and history in Zep and not in memory enables stateless deployment. 

### 🛠️ Use as drop-in replacements for LangChain or LlamaIndex components, or with a frameworkless app.
- Zep Memory and VectorStore implementations are shipped with LangChain, LangChain.js, and LlamaIndex.
- Python & TypeScript/JS SDKs for easy integration with your LLM app.
- TypeScript/JS SDK supports edge deployment.

### 🔎 Vector Database with Hybrid Search
- Populate your prompts with relevant documents and chat history.
- Rich metadata and JSONPath query filters offer a powerful hybrid search over texts.

### 🔋 Batteries Included Embedding & Enrichment
- Automatically embed texts and messages using state-of-the-art open source models, OpenAI, or bring your own vectors. 
- Enrichment of chat histories with summaries, named entities, token counts. Use these as search filters.
- Associate your own metadata with sessions, documents & chat histories.


## Learn more
- 🏎️ **[Quick Start Guide](https://docs.getzep.com/deployment/quickstart/)**: Docker or cloud deployment, and coding, in < 5 minutes.
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
