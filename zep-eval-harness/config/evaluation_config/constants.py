# ============================================================================
# Retrieval mode
# ============================================================================
# Two retrieval paths, independently toggleable. At least one must be enabled.

# Deterministic retrieval: the harness searches the graph itself before the
# response model runs and injects the resulting context block into the system
# prompt. Override per run with --context-block / --no-context-block.
USE_CONTEXT_BLOCK = True

# Agentic retrieval: the response model is given the tools defined in
# config/evaluation_config/tools.py and decides what to retrieve before
# answering. Tools stay defined either way — this is the only switch that
# decides whether they are used. Override per run with --tools / --no-tools.
USE_TOOLS = False

# ============================================================================
# Tool budgets (only apply when tools are enabled)
# ============================================================================
# An "iteration" is one LLM turn that requests tools. Three tools requested in
# parallel in the same turn is 1 iteration and 3 tool calls.

MAX_TOOL_ITERATIONS = 3  # Max LLM turns that may request tools
MAX_TOOL_CALLS = 8  # Max individual tool calls across all iterations

# Force the model to call at least one tool on its first turn (tool_choice=
# "required") so it cannot answer from the question alone. Falls back to
# "auto" if the provider rejects the parameter.
REQUIRE_TOOL_CALL = True

# ============================================================================
# Search configuration — deterministic context block
# ============================================================================
# Search configuration — user graphs
USER_FACTS_LIMIT = 20  # Number of facts (edges) to return
USER_ENTITIES_LIMIT = 10  # Number of entities (nodes) to return
USER_EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# Search configuration — standalone document graph
DOC_FACTS_LIMIT = 10  # Number of facts (edges) to return
DOC_ENTITIES_LIMIT = 5  # Number of entities (nodes) to return
DOC_EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# Reranker for the context block searches
# rrf | mmr | node_distance | episode_mentions | cross_encoder
CONTEXT_BLOCK_RERANKER = "cross_encoder"

# ============================================================================
# Search configuration — retrieval tools (see tools.py)
# ============================================================================
# Zep applies different knobs depending on the scope the model picks:
#   scope="auto"  → volume is bounded by max_characters; limit and reranker are
#                   ignored (auto always retrieves with RRF, then reranks
#                   internally)
#   other scopes  → limit and reranker apply, max_characters is ignored
# Both are set so every scope the model can choose is bounded.

TOOL_SEARCH_MAX_CHARACTERS = 5000  # Per-call cap for scope="auto" (API max 50000)
TOOL_SEARCH_DEFAULT_LIMIT = 15  # Non-auto scopes, when the model passes no limit
TOOL_SEARCH_MAX_LIMIT = 50  # Non-auto scopes, hard cap on what the model may ask
TOOL_SEARCH_RERANKER = CONTEXT_BLOCK_RERANKER  # Non-auto scopes only
TOOL_SEARCH_MAX_QUERY_CHARS = 400  # API limit; longer queries are truncated

# ============================================================================
# LLM models for evaluation
# ============================================================================
LLM_RESPONSE_MODEL = "gemini-3-flash-preview"  # Model used for generating responses
LLM_JUDGE_MODEL = "gemini-3-flash-preview"  # Model used for grading responses
