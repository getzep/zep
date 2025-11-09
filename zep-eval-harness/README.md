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
   - Get your OpenAI API key: https://platform.openai.com/api-keys
   - Add both keys to your `.env` file

3. **Run ingestion script**
   ```bash
   uv run zep_ingest.py

   # Or with custom ontology
   uv run zep_ingest.py --custom-ontology
   ```
   This creates a new run (e.g., `1_20251103T123456`) and generates a manifest in `runs/1_20251103T123456/manifest.json`

4. **Run evaluation script**
   ```bash
   # Evaluate the latest run
   uv run zep_evaluate.py

   # Or evaluate a specific run
   uv run zep_evaluate.py 1
   ```

## Overview

This harness evaluates the complete Zep-powered QA pipeline in just **two scripts**:

### Architecture

```
data/conversations/*.json → [zep_ingest.py] → Zep Cloud Knowledge Graph
data/users.json           →                           ↓
                                                      ↓
data/test_cases/*.json → [zep_evaluate.py] → Search → Generate Response → Grade
                                                      ↓
                                            runs/{run_number}/evaluation_results.json
```

### Pipeline Steps (automated in zep_evaluate.py)

1. **Search**: Query Zep's knowledge graph (nodes, edges) using cross-encoder reranker
2. **Evaluate Context**: Assess whether retrieved context contains sufficient information (PRIMARY METRIC)
3. **Generate Response**: Use gpt-5-mini with retrieved context to answer questions
4. **Grade Answer**: Evaluate answers against golden answers using gpt-4.1 judge (SECONDARY METRIC)

## Run Tracking

Each ingestion creates a numbered run with a manifest file that tracks:
- Run number and timestamp
- Created user IDs (with random suffixes for idempotency)
- Thread IDs for each conversation
- Statistics (number of conversations, telemetry files)
- Ontology configuration

**Run Directory Structure:**
```
runs/
├── 1_20251103T092345/
│   ├── manifest.json
│   └── evaluation_results_20251103T093012.json
├── 2_20251103T143012/
│   ├── manifest.json
│   └── evaluation_results_20251103T144523.json
└── ...
```

**Manifest Example:**
```json
{
  "run_number": 1,
  "timestamp": "2025-11-03T09:23:45.123456",
  "ontology": {
    "type": "default_zep",
    "default_ontology_disabled": false
  },
  "users": [
    {
      "base_user_id": "zep_eval_test_user_001",
      "zep_user_id": "zep_eval_test_user_001_a7390b47",
      "first_name": "John",
      "last_name": "Doe",
      "thread_ids": [
        "conv_001_a7390b47"
      ],
      "num_conversations": 1,
      "num_telemetry_files": 0
    }
  ]
}
```

The evaluation script automatically uses the latest run, or you can specify a run number.

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
- Format: `{user_id}_{data_type}.json`
- Structure: Any JSON data with a `user_id` field
- Ingested using `graph.add(type="json")`
- Example: User preferences, activity history, structured data

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

The evaluation script uses `cross_encoder` reranker by default for best accuracy. Search is configured at the top of `zep_evaluate.py`:
- `FACTS_LIMIT = 20`: Number of facts (edges) to return
- `ENTITIES_LIMIT = 10`: Number of entities (nodes) to return
- `EPISODES_LIMIT = 0`: Episodes disabled by default (set >0 to enable)

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

The framework supports both default Zep ontology and custom ontologies:

1. Edit `ontology.py` to define custom entity and edge types
2. Implement the ontology setup function (e.g., `set_custom_ontology()`)
3. Run ingestion with `--custom-ontology` flag

The included `ontology.py` provides a custom ontology with Person, Location, Organization, Event, and Item entities.

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

Results are saved to `runs/{run_number}/evaluation_results_{timestamp}.json` with the following structure:
```json
{
  "evaluation_timestamp": "20251106T213303",
  "run_number": 1,
  "search_configuration": {
    "facts_limit": 20,
    "entities_limit": 10,
    "episodes_limit": 0
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
      "completeness": {...},
      "accuracy": {...}
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
Graph processing is asynchronous and can take 5-20 seconds per message. The ingestion script submits data without waiting, allowing evaluation to run once processing is complete.

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

The eval harness contains:
- **User Profile**: John Doe, Software Engineer at TechCorp Solutions
- **1 Sample Conversation**: About adopting a dog named Max
- **4 Test Cases**: Evaluating basic fact retrieval from the conversation

All conversation data is synthetic and designed to test basic memory and retrieval capabilities.
