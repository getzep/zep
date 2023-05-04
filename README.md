# zep

# Configuration
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