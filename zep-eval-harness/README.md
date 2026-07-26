# Zep Eval Harness

An end-to-end evaluation framework for testing Zep's memory retrieval and question-answering capabilities for general conversational scenarios.

## Quick Start

1. **Install dependencies**

   This project uses [uv](https://docs.astral.sh/uv/) for fast, reliable Python package management.

   ```bash
   # Install uv (macOS/Linux)
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Install dependencies
   uv sync
   ```

2. **Set API keys**
   - Copy `.env.example` to `.env`: `cp .env.example .env`
   - Get your Zep API key: https://app.getzep.com
   - Get your Google API key (for document contextualization and LLM evaluation)
   - Add both keys to your `.env` file

3. **Run user ingestion**
   ```bash
   # Ingest all users and poll until processing completes
   uv run zep_ingest_users.py

   # Ingest without waiting for processing
   uv run zep_ingest_users.py --no-poll

   # Ingest only specific users
   uv run zep_ingest_users.py --graphs zep_eval_test_user_001

   # Use custom ontology and/or instructions
   uv run zep_ingest_users.py --custom-ontology --custom-instructions --user-summary-instructions
   ```
   Creates a run in `runs/users/{N}_{timestamp}/manifest.json` with a `user_ingestion_config_snapshot/` of the config used.

4. **Chunk documents** (optional, separate from users)
   ```bash
   # Chunk + contextualize all documents (writes to runs/chunk_sets/)
   uv run zep_chunk_documents.py

   # Custom chunk size and higher concurrency
   uv run zep_chunk_documents.py --chunk-size 1000 --concurrency 10

   # Resume an interrupted chunking run
   uv run zep_chunk_documents.py --resume runs/chunk_sets/1_20260331T120000
   ```
   Creates a chunk set in `runs/chunk_sets/{N}_{timestamp}/` with `chunks.jsonl`, `meta.json`, and a `document_chunking_config_snapshot/`.

5. **Ingest documents into Zep** (uses chunk set from step 4)
   ```bash
   # Ingest chunk set #1 into a standalone Zep graph
   uv run zep_ingest_documents.py --chunk-set 1

   # With custom ontology and/or instructions
   uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology --custom-instructions

   # Inline mode (chunk + ingest in one command, no reuse)
   uv run zep_ingest_documents.py --chunk-size 500

   # Resume an interrupted ingestion
   uv run zep_ingest_documents.py --resume runs/checkpoints/doc_xxx.json
   ```
   Creates a run in `runs/documents/{N}_{timestamp}/manifest.json` with a `document_ingestion_config_snapshot/`.

6. **Run evaluation**
   ```bash
   # Evaluate the latest user run (no document graph)
   uv run zep_evaluate.py

   # Evaluate a specific user run
   uv run zep_evaluate.py --user-run 3

   # Combine a user run with a document run
   uv run zep_evaluate.py --user-run 3 --doc-run 2

   # Latest user run + specific document run
   uv run zep_evaluate.py --doc-run 2

   # Adjust evaluation concurrency (default: 15)
   uv run zep_evaluate.py --concurrency 30

   # Agentic retrieval: the model calls Zep tools itself instead of being handed a context block
   uv run zep_evaluate.py --tools --no-context-block

   # Both: context block injected up front, tools available for follow-up retrieval
   uv run zep_evaluate.py --tools
   ```
   Saves results to `runs/evaluations/{N}_{timestamp}/results.json` with an `evaluation_config_snapshot/`. Each evaluation run references its parent user and document ingestion runs.

7. **Inspect a graph** (optional)
   ```bash
   # Inspect a user graph (use the full zep_user_id from manifest.json)
   uv run zep_graph_inspect.py --user zep_eval_test_user_001_a7390b47

   # Inspect the shared documents graph (use graph_id from manifest.json)
   uv run zep_graph_inspect.py --graph zep_eval_shared_documents_f1a2b3c4

   # Show only nodes or only edges
   uv run zep_graph_inspect.py --user zep_eval_test_user_001_a7390b47 --nodes-only
   uv run zep_graph_inspect.py --user zep_eval_test_user_001_a7390b47 --edges-only
   ```

## Overview

This harness evaluates the complete Zep-powered QA pipeline across five scripts:

| Script | Purpose |
|--------|---------|
| `zep_ingest_users.py` | Ingest users, conversations, and telemetry into Zep user graphs |
| `zep_chunk_documents.py` | Chunk documents and generate LLM-based summaries + contextualizations |
| `zep_ingest_documents.py` | Ingest pre-chunked documents into a standalone Zep document graph |
| `zep_evaluate.py` | Search graphs, generate responses, and grade against golden answers |
| `zep_graph_inspect.py` | Print all entities and facts for a user or standalone graph |

### Architecture

```
data/users.json            ┐
data/conversations/*.json  ├→ [zep_ingest_users.py]       → User Graphs     → runs/users/{N}/manifest.json
data/telemetry/*.json      ┘

data/documents/*           → [zep_chunk_documents.py]     → runs/chunk_sets/{N}/chunks.jsonl
                                                                    ↓
                             [zep_ingest_documents.py]    → Document Graph  → runs/documents/{N}/manifest.json

data/test_cases/*.json     → [zep_evaluate.py --user-run N --doc-run M]   → Search → Generate → Grade
                                                                                       ↓
                                                              runs/evaluations/{N}/results.json

                             [zep_graph_inspect.py]       → Print all nodes & edges for any graph
```

### Decoupled Ingestion

User graphs and document graphs are ingested independently, each producing their own manifest.
This means you can:
- Ingest 4 document graph variants (2 ontology options × 2 instruction options)
- Ingest 8 user graph variants (2 ontology × 2 instructions × 2 summary instructions)
- Evaluate any combination at eval time (e.g. `--user-run 3 --doc-run 2`)

This avoids re-ingesting identical graphs just to test different pairings.

### Document Pipeline: Chunk Sets

Document ingestion is split into two steps to avoid redundant LLM calls:

1. **Chunking** (`zep_chunk_documents.py`): Splits documents, generates summaries and per-chunk contextualizations via LLM. Writes results to `runs/chunk_sets/{N}_{timestamp}/chunks.jsonl`. This is the expensive step.
2. **Ingestion** (`zep_ingest_documents.py`): Reads a chunk set and sends chunks to Zep via `graph.add()`. No LLM calls — just API calls.

A single chunk set can be reused across multiple ingestion runs with different ontology/instruction configurations:
```bash
# Chunk once
uv run zep_chunk_documents.py                              # → chunk set #1

# Ingest multiple times with different configs
uv run zep_ingest_documents.py --chunk-set 1               # default ontology
uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology
uv run zep_ingest_documents.py --chunk-set 1 --custom-instructions
uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology --custom-instructions
```

**Follow mode**: If the chunk set is still being generated (`status: "in_progress"` in `meta.json`), the ingestion script automatically tails the JSONL file and ingests chunks as they appear. This lets you run chunking and ingestion concurrently in separate terminals — the ingestion script waits for new chunks and ingests them as they become available.

**Inline mode**: For convenience, `--chunk-size N` on the ingestion script runs chunking inline first, then ingests. This creates a chunk set under the hood but doesn't allow reuse.

### Concurrency and Resilience

All pipeline scripts include retry logic with exponential backoff (up to 8 retries, max 5-minute delay) for handling rate limits and transient API errors. This applies to:

- **Chunking** (`zep_chunk_documents.py`): LLM calls for summarization and contextualization. Control concurrency with `--concurrency N` (default: 5).
- **Evaluation** (`zep_evaluate.py`): Graph search, LLM response generation, and LLM grading. Control concurrency with `--concurrency N` (default: 15). All test cases run in parallel, bounded by an asyncio semaphore.
- **Ingestion** (`zep_ingest_users.py`, `zep_ingest_documents.py`): Zep API calls for creating users, adding messages, and adding graph episodes.

Rate limits are handled automatically — if you hit limits, the retry backoff with jitter will naturally throttle requests.

### Pipeline Steps (automated in zep_evaluate.py)

1. **Retrieve**: Either the harness searches the graph and builds a context block, or the response model retrieves for itself via tool calls, or both — see [Retrieval Modes](#retrieval-modes)
2. **Evaluate Context**: Assess whether the retrieved context contains sufficient information (PRIMARY METRIC)
3. **Generate Response**: Answer the question from the retrieved context
4. **Grade Answer**: Evaluate answers against golden answers using LLM judge (SECONDARY METRIC)

### Retrieval Modes

The harness supports two retrieval paths, toggled independently. At least one must be on.

| Mode | Command | What the model gets |
|------|---------|---------------------|
| Context block (default) | `uv run zep_evaluate.py` | The harness searches the graph and injects a formatted context block into the prompt |
| Tools | `--tools --no-context-block` | No context up front — the model calls Zep retrieval tools itself, then answers |
| Both | `--tools` | A context block up front, plus tools to retrieve anything else it needs |

`--tools` is the only switch that decides whether tools are used. The tools stay defined in `config/evaluation_config/tools.py` either way, so you can flip agentic retrieval on and off between runs without touching config.

In tool mode, **context completeness is graded on the union of every successful tool call's output** (plus the context block, if injected) — not on what made it into the answer. That keeps completeness a measure of retrieval and answer accuracy a measure of generation, exactly as in context-block mode. See [Tool-Based (Agentic) Retrieval](#tool-based-agentic-retrieval).

## Configuration

All use-case-specific configuration lives in `config/`, organized by pipeline step:

```
config/
├── constants.py                              # Shared: GEMINI_BASE_URL, POLL_INTERVAL, POLL_TIMEOUT
├── user_ingestion_config/
│   ├── ontology.py                           # User graph entity/edge types + set_custom_ontology()
│   ├── custom_instructions.py                # User graph custom instructions + set_custom_instructions()
│   └── user_summary_instructions.py          # User node summary instructions
├── document_ingestion_config/
│   ├── constants.py                          # DOCUMENTS_GRAPH_ID
│   ├── ontology.py                           # Document graph entity/edge types + set_document_custom_ontology()
│   └── custom_instructions.py                # Document graph custom instructions
├── document_chunking_config/
│   └── constants.py                          # CHUNK_SIZE, CHUNK_OVERLAP, LLM_CONTEXTUALIZATION_MODEL
└── evaluation_config/
    ├── constants.py                          # Retrieval mode, tool budgets, search limits, LLM models
    ├── tools.py                              # TOOL_SPECS — the retrieval tools the agent can call
    ├── formatting.py                         # How search results are rendered into context text
    ├── judge_prompts.py                      # The completeness and accuracy judge rubrics
    └── response_prompt.py                    # System prompts for AI responses (context-block and tool modes)
```

Each script imports only from its relevant config subfolder. The response prompt used during evaluation is defined in `config/evaluation_config/response_prompt.py` and can be customized independently from the evaluation logic.

## Run Tracking

Each pipeline step writes its output to a numbered, timestamped subdirectory under `runs/`. Every run includes a **config snapshot** — a copy of the config files that were active at the time the run was created. This ensures reproducibility even if config files are changed later.

```
runs/
├── users/
│   └── 1_20260331T222436/
│       ├── manifest.json
│       └── user_ingestion_config_snapshot/     # Snapshot of config/user_ingestion_config/
├── documents/
│   └── 1_20260331T222500/
│       ├── manifest.json
│       └── document_ingestion_config_snapshot/ # Snapshot of config/document_ingestion_config/
├── chunk_sets/
│   └── 1_20260331T222430/
│       ├── meta.json
│       ├── chunks.jsonl
│       └── document_chunking_config_snapshot/  # Snapshot of config/document_chunking_config/
├── evaluations/
│   └── 1_20260331T222821/
│       ├── results.json                       # Evaluation results + parent run references
│       └── evaluation_config_snapshot/        # Snapshot of config/evaluation_config/
└── checkpoints/
    └── doc_xxx.json                           (temporary, removed on success)
```

**User Manifest Example:**
```json
{
  "run_number": 1,
  "type": "users",
  "timestamp": "2026-03-31T22:24:36.839291",
  "ontology": {
    "type": "custom",
    "default_ontology_disabled": true,
    "custom_entity_types": ["Person", "Location", "Organization", "Event", "Item"],
    "custom_edge_types": ["RELATED_TO", "LOCATED_AT", "WORKS_FOR", "OWNS", "SCHEDULED_AT", "INVOLVES"]
  },
  "custom_instructions": {
    "enabled": true,
    "instruction_names": ["real_estate_domain", "property_and_location", "household_context"]
  },
  "user_summary_instructions": {
    "enabled": true,
    "instruction_names": ["property_requirements", "budget_and_finances", "location_preferences", "household_composition", "work_and_lifestyle"]
  },
  "users": [
    {
      "base_user_id": "zep_eval_test_user_001",
      "zep_user_id": "zep_eval_test_user_001_3f701979",
      "first_name": "Sarah",
      "last_name": "Chen",
      "thread_ids": ["conv_002_3f701979", "conv_001_3f701979"],
      "num_conversations": 2,
      "num_telemetry_files": 1
    }
  ]
}
```

**Document Manifest Example:**
```json
{
  "run_number": 1,
  "type": "documents",
  "timestamp": "2026-03-31T22:25:00.282382",
  "graph_id": "zep_eval_shared_documents_1d4d9a28",
  "num_chunks": 10,
  "ontology": {
    "type": "custom",
    "custom_entity_types": ["Concept", "Topic", "Process", "Specification", "Component"],
    "custom_edge_types": ["DESCRIBES", "DEPENDS_ON", "PART_OF", "REFERENCES", "IMPLEMENTS"]
  },
  "custom_instructions": {
    "enabled": true,
    "instruction_names": ["real_estate_reference_domain", "home_buying_process", "financial_concepts"]
  }
}
```

## Data Structure

The harness automatically discovers and processes data files based on naming conventions:

### Users
- Location: `data/users.json`
- Structure:
  ```json
  [
    {
      "user_id": "zep_eval_test_user_001",
      "first_name": "John",
      "last_name": "Doe",
      "email": "john.doe@example.com",
      "metadata": {
        "occupation": "Software Engineer",
        "company": "TechCorp Solutions",
        "date_of_birth": "1985-06-15"
      }
    }
  ]
  ```
- Used to create users in Zep with full names
- Random suffixes added during ingestion for idempotency

### Conversations
- Location: `data/conversations/`
- Format: `{user_id}_{conversation_id}.json`
- Structure:
  ```json
  {
    "conversation_id": "conv_001",
    "user_id": "zep_eval_test_user_001",
    "messages": [
      {
        "role": "user",
        "content": "message content",
        "timestamp": "2024-03-15T10:30:00Z"
      }
    ]
  }
  ```

### Telemetry (Optional)
- Location: `data/telemetry/`
- Format: `{user_id}_*.json`
- Structure: Any JSON data
- Ingested using `graph.add(type="json")` into the user's graph
- Example: User preferences, activity history, structured data

### Documents (Optional)
- Location: `data/documents/`
- Format: Any text file (`.md`, `.txt`, etc.)
- User-agnostic — ingested into a shared standalone graph, not per-user
- Processed via two-step pipeline: chunk (`zep_chunk_documents.py`) then ingest (`zep_ingest_documents.py`)
- LLM-based summarization and per-chunk contextualization resolve ambiguous pronouns and add document context
- Chunking parameters configurable via CLI (`--chunk-size`) or `config/document_chunking_config/constants.py`

### Test Cases
- Location: `data/test_cases/`
- Format: `{user_id}_tests.json`
- Structure:
  ```json
  {
    "user_id": "zep_eval_test_user_001",
    "test_cases": [
      {
        "id": "test_001_dog_name",
        "category": "basic_facts",
        "query": "What is my dog's name?",
        "golden_answer": "Your dog's name is Max.",
        "requires_telemetry": false
      }
    ]
  }
  ```
- The `category` field is used for per-category metric breakdowns in evaluation results

## Multi-User Support

The harness automatically handles multiple users:
- Each user's data is kept separate in Zep's knowledge graph
- Tests run independently for each user
- Results are organized by user in the output

To add more users:
1. Add user definitions to `data/users.json`
2. Create conversation files following the naming pattern
3. Create test case files for each user
4. Run ingestion and evaluation as normal

## Advanced Evaluation

### Tool-Based (Agentic) Retrieval

Instead of handing the model a context block the harness built, you can give it tools and let it decide what to retrieve. This evaluates the retrieval pattern most agents actually use: search as a tool call, with the LLM choosing queries.

```bash
# Agent retrieves everything itself
uv run zep_evaluate.py --tools --no-context-block

# Context block up front, tools for follow-up retrieval
uv run zep_evaluate.py --tools

# Tighter budget: 4 calls across at most 2 rounds
uv run zep_evaluate.py --tools --max-tool-calls 4 --max-tool-iterations 2
```

#### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--tools` / `--no-tools` | `USE_TOOLS` | Whether tools are used at all. Tools stay defined in config either way |
| `--context-block` / `--no-context-block` | `USE_CONTEXT_BLOCK` | Whether deterministic search runs and injects a context block. Combinable with `--tools` |
| `--max-tool-iterations N` | `MAX_TOOL_ITERATIONS` (3) | Max LLM turns that may request tools |
| `--max-tool-calls N` | `MAX_TOOL_CALLS` (8) | Max individual tool calls per test case |
| `--require-tool-call` / `--no-require-tool-call` | `REQUIRE_TOOL_CALL` (on) | Force a tool call on the agent's first turn so it can't answer from the question alone |

Defaults live in `config/evaluation_config/constants.py`; the flags override them per run.

**Iterations vs calls**: an iteration is one LLM turn that requests tools. Three tools requested in parallel in the same turn is **1 iteration and 3 calls**. Budgets are enforced independently: calls requested beyond `--max-tool-calls` are refused (the model is told its budget is spent, and every requested call still gets a response so the conversation stays valid), and once either budget is exhausted the harness makes one final turn with tool calls forbidden so the model must answer from what it gathered.

#### Defining tools

Tools are defined in `config/evaluation_config/tools.py` as a list of `ToolSpec`s. The defaults are:

| Tool | Arguments | Purpose |
|------|-----------|---------|
| `search_memory` | `query`, `scope`, `limit` | Search the user's graph. `scope` accepts `auto` (default, spans all context types), `edges`, `nodes`, `episodes` |
| `search_documents` | `query`, `scope`, `limit` | Search the shared document graph. Registered only on runs with `--doc-run` (`requires_doc_graph=True`) |
| `get_user_profile` | — | Return the user node summary |

To add one, write an async executor and append a spec:

```python
async def search_recent_activity(ctx: ToolContext, days: int = 7) -> str:
    # ctx gives you zep_client, user_id, doc_graph_id, user_summary
    results = await ctx.zep_client.graph.search(
        user_id=ctx.user_id, query=f"activity in the last {days} days",
        scope="episodes", limit=10,
    )
    return "\n".join(format_episodes(results.episodes or []))

TOOL_SPECS.append(
    ToolSpec(
        name="search_recent_activity",
        # The model chooses tools from this text — be specific
        description="Get the user's activity from the last N days.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90,
                         "description": "How many days back to look."},
            },
            "required": ["days"],
        },
        executor=search_recent_activity,
        enabled=True,                # False keeps it defined but out of the run
        requires_doc_graph=False,    # True = only register when --doc-run is passed
    )
)
```

Executor output is text, and successful output is fed verbatim both to the model and to the completeness judge — so format it like context, not like an API dump. Raise an exception to record a tool failure (the model still sees the error and can retry); return a string for a normal, if empty, result. A no-argument tool should leave `parameters` empty: the schema is then omitted entirely, which some providers require.

Whichever tools were active is recorded in each run's `retrieval_configuration`, and `tools.py` itself is captured in the run's `evaluation_config_snapshot/`.

#### How tool searches are bounded

Zep honours different search knobs depending on the scope the model picks, so `tools.py` sends only what applies and the constants in `constants.py` are named accordingly:

| Constant | Applies to | Effect |
|----------|-----------|--------|
| `TOOL_SEARCH_MAX_CHARACTERS` | `scope="auto"` | Total characters returned per call (API default 2500, max 50000). `limit` and `reranker` are **ignored** in this scope — auto always retrieves with RRF and reranks internally |
| `TOOL_SEARCH_DEFAULT_LIMIT` | other scopes | Results per call when the model passes no `limit` |
| `TOOL_SEARCH_MAX_LIMIT` | other scopes | Caps whatever `limit` the model asks for |
| `TOOL_SEARCH_RERANKER` | other scopes | Reranker; defaults to the same one the context block uses, so the two retrieval modes stay comparable |

Both sets are recorded in `retrieval_configuration.tool_search`. The context block's own limits live in `search_configuration`, which is marked `applied: false` on runs where the context block was disabled.

#### Tool prompt

`get_tool_agent_system_prompt()` in `config/evaluation_config/response_prompt.py` is the system prompt for tool mode (the context-block prompt is `get_response_system_prompt()`). It receives the context block when there is one, plus the run's budgets so the model can plan its retrieval.

#### Comparing modes fairly

In "both" mode (`--tools` with the context block on) the graded context is the context block **plus** everything the tools returned, so completeness can only match or exceed context-block mode — that comparison cannot tell you whether tools helped. To isolate what agentic retrieval adds, compare `--tools --no-context-block` against the default. "Both" mode answers a different question: does letting the agent top up a context block improve the end result?

### Tune Zep Search Parameters

The context block searches use the `CONTEXT_BLOCK_RERANKER` (`cross_encoder` by default) for best accuracy; tool searches use it too for every scope that honours a reranker (see [How tool searches are bounded](#how-tool-searches-are-bounded)). Search parameters and LLM models are configured in `config/evaluation_config/constants.py`:
- `USER_FACTS_LIMIT = 20`: Number of facts (edges) from user graph
- `USER_ENTITIES_LIMIT = 10`: Number of entities (nodes) from user graph
- `USER_EPISODES_LIMIT = 0`: User episodes disabled by default (set >0 to enable)
- `DOC_FACTS_LIMIT = 10`: Number of facts from document graph
- `DOC_ENTITIES_LIMIT = 5`: Number of entities from document graph
- `DOC_EPISODES_LIMIT = 0`: Document episodes disabled by default
- `LLM_RESPONSE_MODEL`: Model for generating responses
- `LLM_JUDGE_MODEL`: Model for grading answers

Chunking-specific constants are in `config/document_chunking_config/constants.py`:
- `CHUNK_SIZE`: Character count per chunk (default: 500)
- `CHUNK_OVERLAP`: Characters of overlap between consecutive chunks (default: 100)
- `LLM_CONTEXTUALIZATION_MODEL`: Model for document chunk contextualization

To experiment with rerankers, set `CONTEXT_BLOCK_RERANKER` (and `TOOL_SEARCH_RERANKER`, which follows it by default) to one of `SUPPORTED_RERANKERS`:
- `cross_encoder`: Best accuracy, slower (default)
- `rrf`: Reciprocal Rank Fusion, balanced
- `episode_mentions`: Favors entities and facts mentioned most often

Zep also offers `mmr` and `node_distance`, which are **not** in `SUPPORTED_RERANKERS` because they need extra per-search arguments the harness doesn't send — `mmr_lambda` for MMR, and a `center_node_uuid` to rerank around for node distance. Setting one as the default is rejected at startup rather than failing on every search; to evaluate them, pass their arguments at the call sites in `perform_graph_search()` and `config/evaluation_config/tools.py`. Note also that `scope="auto"` ignores the reranker entirely (it always retrieves with RRF), so only the non-auto scopes are affected.

For guidance, check out the [Searching the Graph documentation](https://help.getzep.com/searching-the-graph).

### Customize Response Prompt

The system prompt used when generating AI responses during evaluation is defined in `config/evaluation_config/response_prompt.py`. Edit the `get_response_system_prompt()` function to customize the AI's persona, response style, or instructions for your use case. This is snapshotted into each evaluation run for reproducibility.

### Customize Context Block

The harness constructs a custom context block from graph search results. You can modify the `construct_context_block()` function in `zep_evaluate.py` to format results differently. See the [Customize Your Context Block documentation](https://help.getzep.com/cookbook/customize-your-context-block) for best practices.

### Add JSON/Text Data

Beyond conversation data, you can add:
- **JSON data**: Structured information (telemetry, user profiles, business data)
- **Text data**: Unstructured documents, notes, transcripts
- **Message data**: Non-conversational messages (emails, SMS)

The ingestion script automatically handles JSON telemetry files. For more information, see the [Adding Data to the Graph documentation](https://help.getzep.com/adding-data-to-the-graph).

### Custom Ontology Support

The framework supports custom ontologies for both user graphs and document graphs, defined in separate files:

**User graphs** (`config/user_ingestion_config/ontology.py`):
1. Edit the user entity/edge types (Person, Location, Organization, Event, Item)
2. Run ingestion with `uv run zep_ingest_users.py --custom-ontology`

**Document graphs** (`config/document_ingestion_config/ontology.py`):
1. Edit the document entity/edge types (Concept, Topic, Process, Specification, Component)
2. Run ingestion with `uv run zep_ingest_documents.py --custom-ontology`

### Custom Instructions Support

Custom instructions are defined separately for user and document graphs:
- User instructions (`config/user_ingestion_config/custom_instructions.py`): `uv run zep_ingest_users.py --custom-instructions`
- Document instructions (`config/document_ingestion_config/custom_instructions.py`): `uv run zep_ingest_documents.py --custom-instructions`

## Evaluation Metrics

### PRIMARY METRIC: Context Completeness
Evaluates whether Zep retrieved sufficient information to answer the question:
- **COMPLETE**: All necessary information present
- **PARTIAL**: Some relevant information, but incomplete
- **INSUFFICIENT**: Missing critical information

This metric directly assesses Zep's retrieval quality, independent of the AI's answer generation.

In tool mode the graded context is the union of the context block (if injected) and **every successful tool call's full output**, including output the agent ignored when answering. So a run can score COMPLETE on retrieval and still get the answer wrong — which is exactly the signal you want: the tools surfaced the information, the agent failed to use it.

### SECONDARY METRIC: Answer Accuracy
Evaluates whether the AI's generated answer matches the golden answer:
- **CORRECT**: Answer conveys the same key information
- **WRONG**: Answer is missing critical information or incorrect

### Per-Category Breakdown
Test cases can be labeled with a `category` field (e.g. `"basic_facts"`, `"temporal_reasoning"`, `"cross_document"`). The evaluation script calculates completeness and accuracy metrics both in aggregate and per-category, making it easy to identify which types of questions the system handles well or poorly.

### Per-User Breakdown
Metrics are also broken down per user, so you can see if retrieval quality varies across different users' graphs.

### Correlation Analysis
The results include analysis of how context completeness correlates with answer accuracy, helping identify whether issues are with retrieval or generation.

### Latency and Tool Usage

Timing is reported as median ± stdev per query, broken into the stages that matter:

| Metric | Meaning |
|--------|---------|
| `answer_latency_*` | **Overall user-facing latency**: context block search + answer generation. Excludes the LLM judges, which a real application wouldn't run |
| `search_*` | Deterministic context block retrieval (0 when `--no-context-block`) |
| `response_*` | Answer generation as a whole — in tool mode this covers the full agent loop |
| `tool_*` | Time inside tool execution, wall clock: tools called in parallel in one round count once |
| `tool_calls_summed_*` | Sum of individual tool call durations. Exceeds `tool_*` by roughly the parallelism factor — compare the two to see how much parallel tool calling actually saved |
| `answer_llm_*` | Answer generation minus tool execution — the "everything else" half of `response_*` (model turns, deciding what to call, writing the answer) |
| `completeness_*`, `grading_*` | The two LLM judges. Evaluation overhead, not user-facing latency |
| `total_*` | Whole pipeline including judges |

Two caveats when reading latency:
- **Retry backoff is inside these timers.** A rate-limited call adds its backoff sleep to the stage it happened in. Tool mode issues several LLM calls per test case, so it absorbs proportionally more rate limiting at the same `--concurrency`; if latency looks surprising, lower concurrency before concluding anything about retrieval cost.
- **`response_*` changed meaning in this version.** It previously recorded the whole `asyncio.gather` window shared with the completeness judge, so it was really `max(generation, judge)`. It now records answer generation alone. Don't compare `response_*` or a derived `answer_latency_*` against runs produced before this change.

The **completeness rubric also changed in this version** — `judge_prompts.py` gained three lines telling the judge that tool results are context and that it shouldn't grade *how* context was retrieved. Completeness scores from before this change were produced by a slightly different rubric, so re-run your baseline rather than comparing against an older run. From here on the rubric is snapshotted per run under `evaluation_config_snapshot/`, so the comparison is checkable.

Tool usage is aggregated under `aggregate_scores.tools`: whether tools were `enabled`, calls and rounds per query, tests where the agent made no calls at all, tests that hit the call or round cap, failed / invalid / refused calls, `tool_choice` downgrades, and a per-tool breakdown of call counts, failures, and latency. Empty answers are reported at the top level as `aggregate_scores.empty_answers`, since they can happen in either mode. Per-test-case detail lands in `tool_trace` — one entry per requested call, with arguments, latency, and an `outcome` of `ok`, `error` (the executor raised), `invalid` (unknown tool or unparseable arguments), or `refused` (call budget spent).

Three counters are worth watching:
- **Cap hits.** Many tests hitting the round or call cap means the budget is throttling retrieval, and completeness is measuring your budget rather than Zep. Both caps can be flagged on the same test when both budgets ran out together.
- **`tests_with_no_calls`.** The agent retrieved nothing. It counts tests with no *executed* calls, so a model that only ever named a nonexistent tool lands here too — check `invalid_calls` before concluding the agent chose not to retrieve.
- **`tool_choice_downgrades`.** The provider rejected a `tool_choice` the harness asked for and the turn was retried with `auto`. On a first turn that means `require_tool_call` wasn't enforced; on the forced-answer turn it means the model was allowed to request tools instead of answering (the harness refuses those calls and re-asks). Either way the run isn't quite the run you configured.

Token counts differ by mode by construction: in tool mode `response_prompt_tokens` is the **sum over every LLM turn**, and each turn re-sends the growing history, so it is not comparable to the single-turn count from context-block mode. Per-turn counts are kept in `response_prompt_tokens_per_turn` so the total stays decomposable.

## Output

Results are saved to `runs/evaluations/{run_number}_{timestamp}/results.json` with the following structure:
```json
{
  "evaluation_timestamp": "20260331T222821",
  "run_number": 1,
  "parent_runs": {
    "user_run": { "run_number": 1, "run_dir": "runs/users/1_20260331T222436" },
    "document_run": { "run_number": 1, "run_dir": "runs/documents/1_20260331T222500", "graph_id": "zep_eval_shared_documents_1d4d9a28" }
  },
  "retrieval_configuration": {
    "use_context_block": false,
    "use_tools": true,
    "max_tool_iterations": 3,
    "max_tool_calls": 8,
    "require_tool_call": true,
    "tools": [
      { "name": "search_memory", "description": "..." },
      { "name": "search_documents", "description": "..." },
      { "name": "get_user_profile", "description": "..." }
    ],
    "tool_search": {
      "max_characters_auto_scope": 5000,
      "default_limit_other_scopes": 15,
      "max_limit_other_scopes": 50,
      "reranker_other_scopes": "cross_encoder"
    }
  },
  "search_configuration": {
    "applied": false,
    "user_facts_limit": 20,
    "user_entities_limit": 10,
    "user_episodes_limit": 0,
    "doc_facts_limit": 10,
    "doc_entities_limit": 5,
    "doc_episodes_limit": 0,
    "reranker": "cross_encoder"
  },
  "model_configuration": {
    "response_model": "gemini-2.5-flash-lite",
    "judge_model": "gemini-2.5-flash-lite"
  },
  "aggregate_scores": {
    "total_tests": 4,
    "completeness": {
      "complete": 4,
      "complete_rate": 100.0
    },
    "accuracy": {
      "correct": 3,
      "accuracy_rate": 75.0
    },
    "timing": {
      "answer_latency_median_ms": 1420,
      "search_median_ms": 0,
      "response_median_ms": 1420,
      "tool_median_ms": 310,
      "tool_calls_summed_median_ms": 580,
      "answer_llm_median_ms": 1110,
      "completeness_median_ms": 850,
      "grading_median_ms": 380,
      "total_median_ms": 2270
    },
    "tokens": {
      "prompt_median": 3200,
      "completion_median": 90,
      "llm_turns_median": 2
    },
    "empty_answers": 0,
    "tools": {
      "enabled": true,
      "total_calls": 9,
      "calls_median": 2,
      "iterations_median": 1,
      "errors": 0,
      "invalid_calls": 0,
      "refused_calls": 0,
      "tool_choice_downgrades": 0,
      "tests_with_no_calls": 0,
      "tests_hitting_call_cap": 0,
      "tests_hitting_iteration_cap": 1,
      "per_tool": {
        "search_memory": {
          "calls": 7,
          "errors": 0,
          "latency_median_ms": 290,
          "latency_stdev_ms": 64,
          "latency_total_ms": 2030
        }
      }
    }
  },
  "category_scores": {
    "basic_facts": {
      "total_tests": 4,
      "completeness": { "complete": 4, "complete_rate": 100.0 },
      "accuracy": { "correct": 3, "accuracy_rate": 75.0 }
    }
  },
  "user_scores": {
    "zep_eval_test_user_001": {
      "total_tests": 2,
      "completeness": {},
      "accuracy": {}
    }
  },
  "detailed_results": {
    "zep_eval_test_user_001": [
      {
        "question": "What is the name of my dog?",
        "golden_answer": "Your dog's name is Biscuit.",
        "category": "basic_facts",
        "completeness_grade": "COMPLETE",
        "completeness_reasoning": "...",
        "answer": "Your dog's name is Biscuit.",
        "answer_grade": true,
        "answer_reasoning": "...",
        "context": "...",
        "search_duration_ms": 0,
        "completeness_duration_ms": 850,
        "response_duration_ms": 420,
        "tool_duration_ms": 310,
        "tool_call_duration_ms": 580,
        "answer_llm_duration_ms": 110,
        "grading_duration_ms": 380,
        "answer_latency_ms": 420,
        "total_duration_ms": 1650,
        "response_prompt_tokens": 850,
        "response_completion_tokens": 24,
        "response_prompt_tokens_per_turn": [320, 530],
        "llm_turns": 2,
        "tool_calls": 2,
        "tool_calls_executed": 2,
        "tool_calls_retrieved": 2,
        "tool_iterations": 1,
        "tool_errors": 0,
        "tool_invalid_calls": 0,
        "tool_refused_calls": 0,
        "tool_choice_downgrades": 0,
        "hit_tool_call_cap": false,
        "hit_tool_iteration_cap": false,
        "answer_empty": false,
        "tool_trace": [
          {
            "iteration": 1,
            "name": "search_memory",
            "arguments": { "query": "user's dog name", "scope": "auto" },
            "outcome": "ok",
            "duration_ms": 290,
            "ok": true,
            "executed": true,
            "error": null,
            "output_chars": 1840
          }
        ]
      }
    ]
  }
}
```

## Troubleshooting

### User Already Exists Error
The ingestion script uses randomized user ID suffixes, so this shouldn't happen. If it does:
1. Delete existing users via Zep API or dashboard
2. Or modify the user_id in `data/users.json`

### Episode Processing Time
Graph processing is asynchronous and can take 5-20 seconds per message. By default the ingestion scripts poll until all episodes are processed. Use `--no-poll` to skip waiting.

### No Test Cases Found
Ensure your test case files:
- Are in `data/test_cases/` directory
- Follow the naming pattern `{user_id}_tests.json`
- Contain valid JSON with `user_id` and `test_cases` fields

### Conversations Not Found
Ensure your conversation files:
- Are in `data/conversations/` directory
- Follow the naming pattern `{user_id}_{conversation_id}.json`
- Contain valid JSON with `user_id` and `messages` fields

## Best Practices for Test Design

### 1. Ensure Answer Availability
The answer to each test question must be present somewhere in the ingested data (conversations or telemetry). Tests become unfair when they expect the AI to answer questions about information that was never provided.

### 2. Write Clear Golden Answers
Golden answers should be specific and concise, focusing on the key information needed to answer the question.

**Example:**
- **Test Question**: "What is my dog's name?"
- **Good Golden Answer**: "Your dog's name is Max."
- **Poor Golden Answer**: "You mentioned that you adopted a golden retriever puppy named Max from the shelter last Tuesday, and he's 3 months old" (too verbose)

### 3. Write Unambiguous Test Questions
Clear, specific questions produce more consistent and reliable evaluation results.

**Example of an ambiguous question:**
- "What did I request?" (ambiguous if multiple requests were discussed)

**Better alternatives:**
- "What is my dog's name?"
- "When is my vet appointment?"
- "What training classes did I sign up for?"

### 4. Consider Context and Scope
Ensure your test questions clearly specify necessary context such as timeframes, locations, or specific instances when multiple similar topics might exist in the conversation history.

## Data Provenance

The eval harness contains sample data for a real estate AI agent scenario:
- **2 Users**: Sarah Chen (Product Manager) and Marcus Rivera (Teacher)
- **4 Conversations**: Each user has 2 conversations about finding their ideal home (Austin, TX and Denver, CO)
- **2 Telemetry Files**: Property search and viewing activity for each user
- **4 Documents**: Home buying checklist, mortgage types guide, HOA guide, home inspection tips
- **8 Test Cases**: 4 per user, evaluating basic fact retrieval from conversations

All data is synthetic and designed to test memory retrieval capabilities.
