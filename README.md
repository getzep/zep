# Zep:A long-term memory store for conversational AI applications
Zep stores, summarizes, embeds, indexes, and enriches conversational AI chat histories, and exposes them via simple, low-latency APIs. Zep allows developers to focus on developing their AI apps, rather than on building memory persistence, search, and enrichment infrastructure.

Zep's Extractor model is easily extensible, with a simple, clean interface available to build new enrichment functionality, such as summarizers, entity extractors, embedders, and more.

Key Features:
- Long-term memory persistence, with access to historical messages irrespective of your summarization strategy.
- Auto-summarization of memory messages based on a configurable message window. A series of summaries are stored, providing flexibility for future summarization strategies.
- Vector search over memories, with messages automatically embedded on creation. 
- Auto-token counting of memories and summaries, allowing finer-grained control over prompt assembly.
- Python and JavaScript/TypeScript SDKs.

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
Chat history storage is an infrastructure challenge all developers and enterprises face as they look to move from prototypes to deploying conversational AI applications that provide rich and intimate experiences to customers.

Long-term memory persistence enables a variety of use cases, including:
- Personalized re-engagement of users based on their chat history.
- Prompt evaluation and selection based on historical data.
- Training of new models and evaluation of existing models.
- Analysis of historical data to understand user behavior and preferences.

However:
- Most AI chat history or memory implementations run in-memory, and are not designed for stateless deployments or long-term persistence.
- Standing up and managing low-latency infrastructure to store, manage, and enrich memories is non-trivial.
- When storing messages long-term, developers are exposes to privacy and regulatory obligations around retention and deletion of user data.

The Zep server and clients SDKs are designed to address these challenges.


## Client SDKs

## Configuration
| Config Key | Environment Variable | Default | 
|-------------------------------|------------------------------|------------------| 
| embeddings.enable | ZEP_EMBEDDINGS_ENABLE | true | 
| embeddings.dimensions | ZEP_EMBEDDINGS_DIMENSIONS | 1536 | 
| embeddings.model | ZEP_EMBEDDINGS_MODEL | adaembeddingv2 | 
| llm | ZEP_LLM | gpt-3.5-turbo | 
| messages.summarize | ZEP_MESSAGES_SUMMARIZE | true | 
| memory.message_window.window | ZEP_MEMORY_MESSAGE_WINDOW_WINDOW | 12 | 
| memory.token_window.window | ZEP_MEMORY_TOKEN_WINDOW_WINDOW | 500 | 
| memory.summary.window | ZEP_MEMORY_SUMMARY_WINDOW | 12 | 
| memory.search.metric | ZEP_MEMORY_SEARCH_METRIC | COSINE | 
| datastore.type | ZEP_DATASTORE_TYPE | redis | 
| datastore.url | ZEP_DATASTORE_URL | localhost:6379 | | server.port | ZEP_SERVER_PORT | 8000 |

## Using Zep's Vector Search

## Production Deployment

## API


## Key Concepts

### Memory

### Message Window

### Search

### Extractors

#### Summarization
#### Embedding
#### Token Counting


## Developing for Zep