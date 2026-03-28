# Standalone graph for shared documents (ingested once, accessible to all users)
DOCUMENTS_GRAPH_ID = "zep_eval_shared_documents"

# Search configuration — user graphs
USER_FACTS_LIMIT = 20  # Number of facts (edges) to return
USER_ENTITIES_LIMIT = 10  # Number of entities (nodes) to return
USER_EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# Search configuration — standalone document graph
DOC_FACTS_LIMIT = 10  # Number of facts (edges) to return
DOC_ENTITIES_LIMIT = 5  # Number of entities (nodes) to return
DOC_EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# Google Gemini configuration
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LLM_RESPONSE_MODEL = "gemini-2.5-flash-lite"  # Model used for generating responses
LLM_JUDGE_MODEL = "gemini-2.5-flash-lite"  # Model used for grading responses
LLM_CONTEXTUALIZATION_MODEL = "gemini-2.5-flash-lite"  # Model used for document chunk contextualization

# Document chunking
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Polling
POLL_INTERVAL = 2  # seconds between status checks
POLL_TIMEOUT = 600  # 10 minutes max wait
