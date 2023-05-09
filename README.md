# Zep: A long-term memory store for conversational AI applications
Zep stores, summarizes, embeds, indexes, and enriches conversational AI chat histories, and exposes them via simple, low-latency APIs. Zep allows developers to focus on developing their AI apps, rather than on building memory persistence, search, and enrichment infrastructure.

Zep's Extractor model is easily extensible, with a simple, clean interface available to build new enrichment functionality, such as summarizers, entity extractors, embedders, and more.

**Key Features:**
- **Long-term memory persistence**, with access to historical messages irrespective of your summarization strategy.
- **Auto-summarization** of memory messages based on a configurable message window. A series of summaries are stored, providing flexibility for future summarization strategies.
- **Vector search** over memories, with messages automatically embedded on creation. 
- **Auto-token counting** of memories and summaries, allowing finer-grained control over prompt assembly.
- **Python** and **JavaScript** SDKs.

Coming (very) soon:
- Langchain `memory` and `retriever` support.
- Support for other conversational AI and agentic AI frameworks.

## Quick Start
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

**JavaScript**
```typescript
const client = new ZepClient(base_url);
const role = "user";
...
```
## Why Zep?
Chat history storage is an infrastructure challenge all developers and enterprises face as they look to move from prototypes to deploying conversational AI applications that provide rich and intimate experiences to users.

Long-term memory persistence enables a variety of use cases, including:
- Personalized re-engagement of users based on their chat history.
- Prompt evaluation based on historical data.
- Training of new models and evaluation of existing models.
- Analysis of historical data to understand user behavior and preferences.

However:
- Most AI chat history or memory implementations run in-memory, and are not designed for stateless deployments or long-term persistence.
- Standing up and managing low-latency infrastructure to store, manage, and enrich memories is non-trivial.
- When storing messages long-term, developers are exposes to privacy and regulatory obligations around retention and deletion of user data.

The Zep server and clients SDKs are designed to address these challenges.

## Client SDKs
- [zep-python](https://github.com/getzep/zep-python): A python client with both async and sync APIs.
- zep-js: TBC

## Configuration
Zep is configured via a yaml configuration file and/or environment variables. The `zep` server accepts a CLI argument `--config` to specify the location of the config file. If no config file is specified, the server will look for a `config.yaml` file in the current working directory.

The OpenAI API key is not expected to be in the config file, rather the environment variable `ZEP_OPENAI_API_KEY` should be set. This can also be configured in a `.env` file in the current working directory.

The Docker compose setup mounts a `config.yaml` file in the current working directory. Modify the compose file, Dockerfile, and `config.yaml` to your taste.

The following table lists the available configuration options.

| Config Key                     | Environment Variable             | Default                                                      |
|--------------------------------|----------------------------------|--------------------------------------------------------------|
| llm.model                      | ZEP_LLM_MODEL                    | gpt-3.5-turbo                                                |
| memory.message_window          | ZEP_MEMORY_MESSAGE_WINDOW        | 12                                                           |
| extractors.summarizer.enabled  | ZEP_EXTRACTORS_SUMMARIZER_ENABLE | true                                                         |
| extractors.embeddings.enabled  | ZEP_EMBEDDINGS_ENABLED           | true                                                         |
| extractors.embeddings.dimensions | ZEP_EMBEDDINGS_DIMENSIONS       | 1536                                                         |
| extractors.embeddings.model    | ZEP_EMBEDDINGS_MODEL             | AdaEmbeddingV2                                               |
| memory_store.type              | ZEP_MEMORY_STORE_TYPE            | postgres                                                     |
| memory_store.postgres.dsn      | ZEP_MEMORY_STORE_POSTGRES_DSN    | postgres://postgres:postgres@localhost:5432/?sslmode=disable |
| server.port                    | ZEP_SERVER_PORT                  | 8000                                                         |
| log.level                      | ZEP_LOG_LEVEL                    | info                                                         |

## Production Deployment
Dockerfiles for both the Zep server and a Postgres database with `pgvector` installed may be found in this repo.

Prebuilt containers for both `amd64` and `arm64` may be installed as follows:
```bash
docker pull ghcr.io/getzep/zep:latest
```

Many cloud providers, including AWS, now offer managed Postgres services with `pgvector` installed.

## Using Zep's Vector Search
Zep allows developers to search the long-term memory store for relevant historical conversations.

Contextual search over chat histories is challenging: chat messages are typically short and when combined with high-dimensional embedding vectors, result in significant sparsity. This vector sparsity can result in many false positives when searching for relevant messages.

Zep returns all messages up to a default limit, which can overridden by passing a `limit` querystring argument to the search API. Given the sparsity issue discussed above, we suggest only using the top 2-3 messages in your prompts. Alternatively, analyze your search results and use a distance threshold to filter out irrelevant messages.

By default, Zep uses OpenAI's 1536-wide AdaV2 embeddings and cosine distance for search ranking.

## REST API

Alongside the Python and JavaScript SDKs, Zep exposes a REST API for interacting with the server. View the [REST API documentation](https://getzep.github.io/zep/api). 

## Key Concepts

### Sessions

### Memory
A memory is the core data structure in Zep. It contains a list of Messages and a Summary (if created). The Memory and Summary are returned with UUIDs, token counts, timestamps, and other metadata, allowing for a rich set of application-level functionality.

### Message Window
The Message Window, as set in the config file, defines when the Summarizer will summarize Memory contents. Once the number of unsummarized memories exceeds the message window, the summarizer will summarize any old memories over half the message window size. This is intended to limit significant LLM usage.

**NOTE REGARDING MEMORY GETS**

When retrieving 

### Search

### Extractors

#### Summarization
#### Embedding
#### Token Counting


## Developing for Zep

## Acknowledgements
h/t to the [Motorhead](https://github.com/getmetal/motorhead) and [Langchain](https://github.com/hwchase17/langchain) projects for inspiration.