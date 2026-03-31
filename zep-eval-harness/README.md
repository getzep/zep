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
   Creates a run in `runs/users/{N}_{timestamp}/manifest.json`.

4. **Chunk documents** (optional, separate from users)
   ```bash
   # Chunk + contextualize all documents (writes to runs/chunk_sets/)
   uv run zep_chunk_documents.py

   # Custom chunk size and higher concurrency
   uv run zep_chunk_documents.py --chunk-size 1000 --concurrency 10

   # Resume an interrupted chunking run
   uv run zep_chunk_documents.py --resume runs/chunk_sets/1_20260331T120000
   ```
   Creates a chunk set in `runs/chunk_sets/{N}_{timestamp}/` with `chunks.jsonl` and `meta.json`.

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
   Creates a run in `runs/documents/{N}_{timestamp}/manifest.json`.

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
   ```

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
                                                              runs/users/{N}/evaluation_results.json

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

**Follow mode**: If the chunk set is still being generated (`status: "in_progress"` in `meta.json`), the ingestion script automatically tails the JSONL file and ingests chunks as they appear. This lets you run chunking and ingestion concurrently in separate terminals.

**Inline mode**: For convenience, `--chunk-size N` on the ingestion script runs chunking inline first, then ingests. This creates a chunk set under the hood but doesn't allow reuse.

**Concurrency**: The chunking script parallelizes LLM calls (summarization + contextualization) using an asyncio semaphore. Control with `--concurrency N` (default: 5). The retry logic handles rate limits with exponential backoff up to 5 minutes.

### Pipeline Steps (automated in zep_evaluate.py)

1. **Search**: Query Zep's knowledge graph (nodes, edges) using cross-encoder reranker
2. **Evaluate Context**: Assess whether retrieved context contains sufficient information (PRIMARY METRIC)
3. **Generate Response**: Use LLM with retrieved context to answer questions
4. **Grade Answer**: Evaluate answers against golden answers using LLM judge (SECONDARY METRIC)

## Run Tracking

User ingestion, document ingestion, and chunk sets are stored separately:

```
runs/
├── users/
│   ├── 1_20251103T092345/
│   │   ├── manifest.json
│   │   └── evaluation_results_20251103T093012.json
│   └── 2_20251103T143012/
│       └── manifest.json
├── documents/
│   ├── 1_20251103T092400/
│   │   └── manifest.json
│   └── 2_20251103T143100/
│       └── manifest.json
├── chunk_sets/
│   └── 1_20251103T092000/
│       ├── meta.json
│       └── chunks.jsonl
└── checkpoints/
    └── doc_xxx.json          (temporary, removed on success)
```

**User Manifest Example:**
```json
{
  "run_number": 1,
  "type": "users",
  "timestamp": "2025-11-03T09:23:45.123456",
  "ontology": {
    "type": "default_zep",
    "default_ontology_disabled": false
  },
  "custom_instructions": {
    "enabled": false,
    "instruction_names": []
  },
  "user_summary_instructions": {
    "enabled": false,
    "instruction_names": []
  },
  "users": [
    {
      "base_user_id": "zep_eval_test_user_001",
      "zep_user_id": "zep_eval_test_user_001_a7390b47",
      "first_name": "John",
      "last_name": "Doe",
      "thread_ids": ["conv_001_a7390b47"],
      "num_conversations": 1,
      "num_telemetry_files": 2
    }
  ]
}
```

**Document Manifest Example:**
```json
{
  "run_number": 1,
  "type": "documents",
  "timestamp": "2025-11-03T09:24:00.123456",
  "graph_id": "zep_eval_shared_documents_f1a2b3c4",
  "num_chunks": 12,
  "ontology": {
    "type": "custom",
    "custom_entity_types": ["Concept", "Topic", "Process", "Specification", "Component"],
    "custom_edge_types": ["DESCRIBES", "DEPENDS_ON", "PART_OF", "REFERENCES", "IMPLEMENTS"]
  },
  "custom_instructions": {
    "enabled": true,
    "instruction_names": ["document_extraction", "structure_preservation", "cross_reference_tracking"]
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
- Chunking parameters configurable via CLI (`--chunk-size`) or `constants.py` (`CHUNK_SIZE`)

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

### Tune Zep Search Parameters

The evaluation script uses `cross_encoder` reranker by default for best accuracy. Search parameters and LLM models are configured in `constants.py`:
- `USER_FACTS_LIMIT = 20`: Number of facts (edges) from user graph
- `USER_ENTITIES_LIMIT = 10`: Number of entities (nodes) from user graph
- `USER_EPISODES_LIMIT = 0`: User episodes disabled by default (set >0 to enable)
- `DOC_FACTS_LIMIT = 10`: Number of facts from document graph
- `DOC_ENTITIES_LIMIT = 5`: Number of entities from document graph
- `DOC_EPISODES_LIMIT = 0`: Document episodes disabled by default
- `LLM_RESPONSE_MODEL`: Model for generating responses
- `LLM_JUDGE_MODEL`: Model for grading answers
- `LLM_CONTEXTUALIZATION_MODEL`: Model for document chunk contextualization
- `DOCUMENT_INGEST_LIMIT`: Maximum number of documents to ingest from `data/documents/`. Documents are sorted alphabetically by filename, so the selection is deterministic. Set to `None` to ingest all documents (default). Set to an integer (e.g. `1`) to ingest only the first N documents.

You can experiment with different rerankers by modifying the `reranker` parameter in `perform_graph_search()`:
- `cross_encoder`: Best accuracy, slower (default)
- `rrf`: Reciprocal Rank Fusion, balanced
- `mmr`: Maximal Marginal Relevance, diversity-focused

For guidance, check out the [Searching the Graph documentation](https://help.getzep.com/searching-the-graph).

### Customize Context Block

The harness constructs a custom context block from graph search results. You can modify the `construct_context_block()` function in `zep_evaluate.py` to format results differently. See the [Customize Your Context Block documentation](https://help.getzep.com/cookbook/customize-your-context-block) for best practices.

### Add JSON/Text Data

Beyond conversation data, you can add:
- **JSON data**: Structured information (telemetry, user profiles, business data)
- **Text data**: Unstructured documents, notes, transcripts
- **Message data**: Non-conversational messages (emails, SMS)

The ingestion script automatically handles JSON telemetry files. For more information, see the [Adding Data to the Graph documentation](https://help.getzep.com/adding-data-to-the-graph).

### Custom Ontology Support

The framework supports custom ontologies for both user graphs and document graphs:

**User graphs** (`ontology.py`):
1. Edit the user entity/edge types (Person, Location, Organization, Event, Item)
2. Run ingestion with `uv run zep_ingest_users.py --custom-ontology`

**Document graphs** (`ontology.py`):
1. Edit the document entity/edge types (Concept, Topic, Process, Specification, Component)
2. Run ingestion with `uv run zep_ingest_documents.py --custom-ontology`

### Custom Instructions Support

Similarly, custom instructions are defined separately for user and document graphs in `custom_instructions.py`:
- User instructions: `uv run zep_ingest_users.py --custom-instructions`
- Document instructions: `uv run zep_ingest_documents.py --custom-instructions`

## Evaluation Metrics

### PRIMARY METRIC: Context Completeness
Evaluates whether Zep retrieved sufficient information to answer the question:
- **COMPLETE**: All necessary information present
- **PARTIAL**: Some relevant information, but incomplete
- **INSUFFICIENT**: Missing critical information

This metric directly assesses Zep's retrieval quality, independent of the AI's answer generation.

### SECONDARY METRIC: Answer Accuracy
Evaluates whether the AI's generated answer matches the golden answer:
- **CORRECT**: Answer conveys the same key information
- **WRONG**: Answer is missing critical information or incorrect

### Correlation Analysis
The results include analysis of how context completeness correlates with answer accuracy, helping identify whether issues are with retrieval or generation.

## Output

Results are saved to `runs/users/{run_number}/evaluation_results_{timestamp}.json` with the following structure:
```json
{
  "evaluation_timestamp": "20251106T213303",
  "run_number": 1,
  "search_configuration": {
    "user_facts_limit": 20,
    "user_entities_limit": 10,
    "user_episodes_limit": 0,
    "doc_facts_limit": 10,
    "doc_entities_limit": 5,
    "doc_episodes_limit": 0
  },
  "model_configuration": {
    "response_model": "gpt-5-mini",
    "judge_model": "gpt-4.1"
  },
  "aggregate_scores": {
    "total_tests": 40,
    "completeness": {
      "complete": 35,
      "complete_rate": 87.5
    },
    "accuracy": {
      "correct": 32,
      "accuracy_rate": 80.0
    }
  },
  "user_scores": {
    "zep_eval_test_user_001": {
      "total_tests": 4,
      "completeness": {},
      "accuracy": {}
    }
  },
  "detailed_results": {
    "zep_eval_test_user_001": [
      {
        "question": "What is my dog's name?",
        "golden_answer": "Your dog's name is Max.",
        "completeness_grade": "COMPLETE",
        "completeness_reasoning": "...",
        "completeness_missing_elements": [],
        "completeness_present_elements": ["dog's name"],
        "answer": "Your dog's name is Max...",
        "answer_grade": true,
        "answer_reasoning": "...",
        "context": "...",
        "search_duration_ms": 245,
        "completeness_duration_ms": 850,
        "response_duration_ms": 420,
        "grading_duration_ms": 380,
        "total_duration_ms": 1895,
        "response_prompt_tokens": 850
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
