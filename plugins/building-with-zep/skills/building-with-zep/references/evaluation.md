# Evaluating and benchmarking Zep for your use case

Don't guess whether retrieval is good enough — measure it, before you tune anything. Zep ships an evaluation harness for exactly this. It lets you evaluate retrieval for your domain and conversation patterns, experiment systematically with ontology and search settings, and keep a regression suite for CI.

## What to measure (and why two metrics)

- **Context completeness** — did the retrieved context contain the information needed? (`COMPLETE` / `PARTIAL` / `INSUFFICIENT`.) **This is Zep's job** and the primary metric.
- **Answer accuracy** — was the final answer correct vs. a golden answer? (`CORRECT` / `WRONG`.) Secondary, because it also depends on your LLM and prompt, not only retrieval.

Separating them tells you *where* a failure is: a wrong answer with `COMPLETE` context is an LLM/prompt problem; `INSUFFICIENT` context is a retrieval/ingestion problem.

## Workflow

1. **Write 3–5 example interactions** — the things you want the agent to answer from memory (e.g. "What is my dog's name?" → "Max").
2. **Generate test data with an LLM** — use the harness prompt template to expand each interaction into ~10 test cases (variations, edge cases) and ~5 short conversations (≈6 messages each) that *contain* the answers, with all needed information distributed across them.
3. **Set up the harness:**
   ```bash
   git clone https://github.com/getzep/zep.git
   cd zep/zep-eval-harness
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv sync
   cp .env.example .env        # add ZEP_API_KEY and OPENAI_API_KEY
   ```
4. **Ingest conversations:** `uv run zep_ingest.py` (add `--custom-ontology` to test your types). Allow for async processing — roughly **5–10 seconds per message** (≈2.5–5 min for 5×6-message conversations).
5. **Run evaluation:** `uv run zep_evaluate.py` (or `uv run zep_evaluate.py 1` for a specific run). Each run: searches the graph (cross-encoder rerank) → scores context completeness → generates an answer → grades it against the golden answer. Results land in `runs/{n}/evaluation_results_{ts}.json`.

## Iterating on misses

For each missed question: confirm the conversation data actually contains the info; check the golden answer is clear and specific; read the retrieved context in the results JSON; adjust data or question; re-ingest and re-run.

Then expand: add more examples and variations, define a domain ontology, ingest larger background datasets so target facts are "buried" (realistic recall test), add JSON/unstructured data, and tune the search strategy (rerankers, scopes, parameters) against the suite.

## Reference numbers

Zep's published benchmarks set expectations for the recall/latency/token tradeoff: **LoCoMo 94.7% accuracy @ 155ms**, **LongMemEval 90.2% @ 162ms**. Use them as orientation, not as a substitute for evaluating on *your* data — your domain, conversation style, and ontology are what determine real-world quality.
