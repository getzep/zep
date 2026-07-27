---
name: zep-eval-harness
description: Run and manage the Zep eval harness pipeline — document chunking, user ingestion, document ingestion, evaluation, graph inspection, and results analysis. Use when the user asks to run eval harness scripts, use the Zep eval harness, get terminal commands for eval harness operations, chunk documents, ingest users or documents into Zep, run evaluations, inspect graphs, compare evaluation runs, analyze completeness/accuracy metrics, or work with the eval harness data/config/runs directories. Also triggers on "run the harness", "start ingestion", "run evaluation", "chunk the documents", "compare runs", "analyze results", or any reference to the eval harness pipeline.
---

# Zep Eval Harness

An end-to-end evaluation framework for testing Zep's memory retrieval and question-answering capabilities. The pipeline ingests user conversations, telemetry, and documents into Zep knowledge graphs, then evaluates retrieval quality by searching those graphs with test questions and grading the results against golden answers.

The pipeline has four steps:
1. **Chunk documents** — split documents and generate LLM-based summaries + contextualizations
2. **Ingest users** — create Zep users, add conversations and telemetry to user graphs
3. **Ingest documents** — send pre-chunked documents to a standalone Zep document graph
4. **Evaluate** — for each test case: search graphs → assess context completeness → generate LLM response → grade answer accuracy

### Evaluation Metrics

**Context Completeness (PRIMARY)** — Did Zep retrieve sufficient information to answer the question?
- **COMPLETE**: All necessary information present in the retrieved context
- **PARTIAL**: Some relevant information, but incomplete
- **INSUFFICIENT**: Missing critical information

This is the metric that matters most. It directly measures Zep's retrieval quality — whether the knowledge graph and search surface the right facts, entities, and relationships.

When completeness is low, the key diagnostic question is: **does the graph contain the right information but search failed to retrieve it, or is the information missing from the graph entirely?** Use `zep_graph_inspect.py` to examine what's actually in the graph. If the information is there but not retrieved, the issue is search configuration (limits, reranker, query phrasing). If the information is absent from the graph, the issue is upstream — ingestion, ontology, or custom instructions need adjustment.

**Answer Accuracy (SECONDARY)** — Did the LLM produce a correct answer from the retrieved context?
- **CORRECT**: Answer conveys the same key information as the golden answer
- **WRONG**: Answer is missing critical information or incorrect

This measures whether the response model uses the context well. It depends on the LLM and response prompt, not Zep. High completeness + low accuracy means retrieval is working but the response generation needs tuning (better model, better prompt).

Metrics are calculated in aggregate, per-category (based on test case `category` field), and per-user.

### Context Block: Edges vs Nodes vs Episodes

The evaluation script constructs a context block from graph search results. Understanding what each component contributes:

- **Edges (facts)**: Relationships extracted by Zep between entities — e.g., "Sarah WORKS_FOR TechCorp", "Biscuit IS_OWNED_BY Sarah". These are the primary source of structured knowledge. Controlled by `USER_FACTS_LIMIT` / `DOC_FACTS_LIMIT`.
- **Nodes (entities)**: Entity summaries — e.g., a Person node with name, relationship type, and a description synthesized from all conversations mentioning them. Controlled by `USER_ENTITIES_LIMIT` / `DOC_ENTITIES_LIMIT`.
- **Episodes (raw data)**: The original messages, document chunks, or JSON data that was ingested. These provide verbatim source text but are bulkier. Disabled by default (`*_EPISODES_LIMIT = 0`). Enable by setting limits > 0 in `config/evaluation_config/constants.py`.

Most evaluations work best with edges + nodes (structured, concise). Enable episodes when verbatim source text is needed for answering questions that require exact quotes or details not captured in the graph extraction.

All commands run from `zep-eval-harness/` using `uv run`. Depending on the user's request, either run the scripts directly or provide the terminal commands for the user.

## Data Folder (`data/`)

| Path | Purpose |
|------|---------|
| `data/users.json` | Array of user definitions (`user_id`, `first_name`, `last_name`, `email`, `metadata`) |
| `data/conversations/{user_id}_{conv_id}.json` | Conversation files with `messages` array (role + content + timestamp) |
| `data/telemetry/{user_id}_*.json` | Optional JSON data ingested via `graph.add(type="json")` |
| `data/documents/*` | Text/markdown files for the shared document graph |
| `data/test_cases/{user_id}_tests.json` | Eval test cases with `id`, `category`, `query`, `golden_answer` fields |

## Configuration Folder (`config/`)

```
config/
├── constants.py                        # GEMINI_BASE_URL, POLL_INTERVAL, POLL_TIMEOUT
├── user_ingestion_config/
│   ├── ontology.py                     # User graph entity/edge types + set_custom_ontology()
│   ├── custom_instructions.py          # User custom instructions + set_custom_instructions()
│   └── user_summary_instructions.py    # User node summary instructions
├── document_ingestion_config/
│   ├── constants.py                    # DOCUMENTS_GRAPH_ID
│   ├── ontology.py                     # Document graph entity/edge types
│   └── custom_instructions.py          # Document custom instructions
├── document_chunking_config/
│   └── constants.py                    # CHUNK_SIZE, CHUNK_OVERLAP, LLM_CONTEXTUALIZATION_MODEL
└── evaluation_config/
    ├── constants.py                    # Search limits, LLM_RESPONSE_MODEL, LLM_JUDGE_MODEL
    └── response_prompt.py              # get_response_system_prompt() — AI persona for eval
```

## Scripts

### 1. Chunk Documents (`zep_chunk_documents.py`)

Splits documents into chunks and generates LLM summaries + contextualizations. Writes to `runs/chunk_sets/`.

```bash
uv run zep_chunk_documents.py [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--chunk-size N` | from config | Override character count per chunk |
| `--chunk-overlap N` | from config | Characters of overlap between consecutive chunks |
| `--concurrency N` | 5 | Max parallel LLM calls (semaphore) |
| `--resume PATH` | — | Resume interrupted run from chunk set directory |

### 2. Ingest Users (`zep_ingest_users.py`)

Creates Zep users, adds conversations and telemetry, polls for processing completion.

```bash
uv run zep_ingest_users.py [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--custom-ontology` | off | Apply user ontology from config |
| `--custom-instructions` | off | Apply user custom instructions from config |
| `--user-summary-instructions` | off | Apply user summary instructions from config |
| `--no-poll` | off | Zep processes episodes asynchronously — the API returns immediately but graph extraction happens in the background. By default the script polls until all episodes finish processing. This flag skips that wait. |
| `--graphs ID [ID ...]` | all | Ingest only specific user base IDs |

### 3. Ingest Documents (`zep_ingest_documents.py`)

Reads a chunk set and sends chunks to a standalone Zep document graph.

```bash
uv run zep_ingest_documents.py [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--chunk-set N` | — | Use chunk set #N (supports follow mode if still in progress) |
| `--custom-ontology` | off | Apply document ontology from config |
| `--custom-instructions` | off | Apply document custom instructions from config |
| `--chunk-size N` | — | Inline mode: chunk + ingest in one step (no reuse) |
| `--no-poll` | off | Same as user ingestion: Zep processes episodes asynchronously, and by default the script polls until complete. This flag skips that wait. |
| `--resume PATH` | — | Resume from checkpoint file |

### 4. Evaluate (`zep_evaluate.py`)

Searches graphs, generates LLM responses, grades against golden answers. Calculates aggregate, per-category, and per-user metrics.

```bash
uv run zep_evaluate.py [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--user-run N` | latest | Which user ingestion run to evaluate |
| `--doc-run N` | none | Which document ingestion run to include |
| `--concurrency N` | 15 | Max parallel test case evaluations (semaphore) |

### 5. Inspect Graph (`zep_graph_inspect.py`)

Print all entities and facts for any graph.

```bash
uv run zep_graph_inspect.py [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--user USER_ID` | Inspect a user graph (full zep_user_id from manifest) |
| `--graph GRAPH_ID` | Inspect a standalone document graph |
| `--nodes-only` | Show only entity nodes |
| `--edges-only` | Show only fact edges |

## Run Artifacts (`runs/`)

Each pipeline step creates a numbered, timestamped directory with a config snapshot for reproducibility.

| Directory | Contents |
|-----------|----------|
| `runs/chunk_sets/{N}_{ts}/` | `chunks.jsonl`, `meta.json`, `document_chunking_config_snapshot/` |
| `runs/users/{N}_{ts}/` | `manifest.json`, `user_ingestion_config_snapshot/` |
| `runs/documents/{N}_{ts}/` | `manifest.json`, `document_ingestion_config_snapshot/` |
| `runs/evaluations/{N}_{ts}/` | `results.json` (with `parent_runs` referencing user/doc runs), `evaluation_config_snapshot/` |
| `runs/checkpoints/` | Temporary resume files, removed on success |

## Follow Mode

Start document ingestion while chunking is still running — the ingestion script tails the in-progress chunk set JSONL and ingests chunks as they appear:

```bash
# Terminal 1
uv run zep_chunk_documents.py --concurrency 10

# Terminal 2 (start immediately, even before chunking finishes)
uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology --custom-instructions
```

The ingestion script detects `"status": "in_progress"` in `meta.json` and polls for new chunks.

## Concurrency Guidance

Prefer higher concurrency values to maximize throughput:
- **Chunking**: `--concurrency 10` to `20` (default 5 is conservative)
- **Evaluation**: `--concurrency 30` or higher (default 15 is conservative)

All scripts include retry logic with exponential backoff (8 retries, max 5-minute delay) that handles rate limits automatically. If rate limit errors persist, reduce concurrency and retry.

## Analyzing Results

Evaluation results live at `runs/evaluations/{N}_{ts}/results.json`. Each result references its parent user and document runs via the `parent_runs` field. When asked to analyze or compare runs:

1. Read the `aggregate_scores`, `category_scores`, and `user_scores` from each results file.
2. Present comparisons as a table — rows are runs/configs, columns are metrics.
3. Draw conclusions about which configurations perform better and with what level of certainty (e.g., small test sets = low confidence, large differences = higher signal).

### Which Metrics Matter

**Context completeness is the primary metric.** It measures whether Zep retrieved sufficient information to answer the question — this directly evaluates Zep's retrieval quality. A COMPLETE rate of 100% means Zep always surfaces the right facts.

**Answer accuracy is secondary.** It measures whether the LLM used the retrieved context correctly to produce a good answer. This depends on the response model and prompt, not on Zep. A run with high completeness but lower accuracy indicates the retrieval is working but the response generation could be improved (better model, better prompt in `response_prompt.py`, etc.) — not a Zep issue.

When comparing runs, focus on completeness differences. Accuracy is still worth tracking (it catches cases where context is technically present but hard for the LLM to use), but completeness is what tells you whether Zep's graph and search are doing their job.

## Typical Full Pipeline

```bash
# 1. Chunk documents
uv run zep_chunk_documents.py --concurrency 15

# 2. Ingest users (can run in parallel with chunking)
uv run zep_ingest_users.py --custom-ontology --custom-instructions --user-summary-instructions

# 3. Ingest documents (can follow in-progress chunk set)
uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology --custom-instructions

# 4. Evaluate
uv run zep_evaluate.py --user-run 1 --doc-run 1 --concurrency 30
```
