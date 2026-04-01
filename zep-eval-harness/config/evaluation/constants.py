# Search configuration — user graphs
USER_FACTS_LIMIT = 20  # Number of facts (edges) to return
USER_ENTITIES_LIMIT = 10  # Number of entities (nodes) to return
USER_EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# Search configuration — standalone document graph
DOC_FACTS_LIMIT = 10  # Number of facts (edges) to return
DOC_ENTITIES_LIMIT = 5  # Number of entities (nodes) to return
DOC_EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# LLM models for evaluation
LLM_RESPONSE_MODEL = "gemini-2.5-flash-lite"  # Model used for generating responses
LLM_JUDGE_MODEL = "gemini-2.5-flash-lite"  # Model used for grading responses
