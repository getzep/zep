<p align="center">
  <a href="https://squidfunk.github.io/mkdocs-material/">
    <img src="https://github.com/getzep/zep/blob/main/assets/zep-bot-square-200x200.png?raw=true" width="150" alt="Zep Logo">
  </a>
</p>

<h1 align="center">
Zep: A long-term memory store for LLM applications
</h1>

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
<a href="https://docs.getzep.com/sdk/langchain/">LangChain Support</a> | 
<a href="https://discord.gg/W8Kw6bsgXQ">Discord</a><br />
<a href="https://www.getzep.com">www.getzep.com</a>
</p>
<h2 align="center">Easily add relevant documents, chat history memory & rich user data to your LLM app's prompts.</h2>

## ⭐️ Core Features
### 💬 Designed for building conversational LLM applications
- Understands chat messages, roles, and user metadata, not just texts and embeddings. 
- Zep Memory and VectorStore implementations are shipped with your favorite frameworks: LangChain, LangChain.js, LlamaIndex, and more.

### 🔎 Vector Database with Hybrid Search
- Populate your prompts with relevant documents and chat history.
- Rich metadata and JSONPath query filters offer a powerful hybrid search over texts.

### 🔋 Batteries Included Embedding & Enrichment
- Automatically embed texts and messages using state-of-the-art opeb source models, OpenAI, or bring your own vectors. 
- Enrichment of chat histories with summaries, named entities, token counts. Use these as search filters.
- Associate your own metadata with sessions, documents & chat histories.

### ⚡️ Fast, low-latency APIs and stateless deployments
- Zep’s local embedding models and async enrichment ensure a snappy user experience. 
- Storing documents and history in Zep and not in memory enables stateless deployment.

### 🛠️ Python & TypeScript/JS SDKs, Edge Deployment
- Python & TypeScript/JS SDKs for easy integration with your LLM app.
- TypeScript/JS SDK supports edge deployment.

## Learn more
- 🏎️ **[Quick Start Guide](https://docs.getzep.com/deployment/quickstart/)**: Docker or cloud deployment, and coding, in < 5 minutes.
- 🦙 **[Building Apps with LlamaIndex](https://docs.getzep.com/sdk/llamaindex/)**
- 🦜⛓️ **[Building Apps with LangChain](https://docs.getzep.com/sdk/langchain/)**
- 🛠️ [**Getting Started with TypeScript/JS or Python**](https://docs.getzep.com/sdk/)
- 🔑 **[Key Concepts](https://docs.getzep.com/sdk/concepts/)**

## Examples

### Hybrid similarity search with text input and JSONPath filters (TypeScript)
```typescript
const query = "The celestial motions are nothing but a continual";
const searchResults = await collection.search({ text: query }, 3);

// Search for documents using both text and metadata
const metadataQuery = {
    where: { jsonpath: '$[*] ? (@.bar == "qux")' },
};

const newSearchResults = await collection.search(
    {
        text: query,
        metadata: metadataQuery,
    },
    3
);
```

### Search search by embedding (Python)
```python
# Search by embedding vector, rather than text query
# embedding is a list of floats
results = collection.search(
    embedding=embedding, limit=5
)
```

### Persist Chat History to Zep (Python)
```python
session_id = "2a2a2a" 

history = [
     { role: "human", content: "Who was Octavia Butler?" },
     {
        role: "ai",
        content:
           "Octavia Estelle Butler (June 22, 1947 – February 24, 2006) was an American" +
           " science fiction author.",
     },
     {
        role: "human",
        content: "Which books of hers were made into movies?",
        metadata={"foo": "bar"},
     }
]


 messages = [Message(role=m.role, content=m.content) for m in history]
 memory = Memory(messages=messages)
 result = await client.aadd_memory(session_id, memory)
 ```

### Persist Chat History with LangChain.js (TypeScript)
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
````


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
