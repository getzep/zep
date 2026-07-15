# zep-ingest examples

Every example is **self-contained and re-runnable**: it creates a fresh
`example-*-<timestamp>` graph (or user), sets the starter ontology from
[`example_ontology.py`](example_ontology.py), ingests the bundled sample data
under [`data/`](data/), and finishes with a search (or user-context fetch) so
you see the graph pay off immediately.

```bash
pip install zep-ingest
export ZEP_API_KEY=...   # Zep dashboard → Project → API Keys
cd examples
python slack_export_example.py
```

Each run creates a new graph; delete old `example-*` graphs and users from
the [dashboard](https://app.getzep.com) when you're done.

## The examples

| Script | Demonstrates | Destination |
|---|---|---|
| [`email_example.py`](email_example.py) | `.eml` files → message episodes dated by their `Date:` headers; alias canonicalization | named graph |
| [`documents_example.py`](documents_example.py) | Markdown → ~500-char chunks; optional LLM contextualization (auto-detects `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) | named graph |
| [`fact_triples_example.py`](fact_triples_example.py) | molding a realistic directory export (no triple-shaped columns) into explicit fact triples; the manual create → set_ontology → seed lifecycle | named graph |
| [`json_records_example.py`](json_records_example.py) | structured records with identity-field mapping — Zep extracts the relationships | named graph |
| [`slack_export_example.py`](slack_export_example.py) | free `preview()` first, then a Slack export with thread grouping, `skip_subtypes`, and the opt-in risky-alias guard | named graph |
| [`user_graph_example.py`](user_graph_example.py) | **combined**: profile fact triples → chat-thread backfill → a document, all on one user's graph | user graph |
| [`thread_backfill_example.py`](thread_backfill_example.py) | historic chat history (JSONL) → threads that power `thread.get_user_context()` | user graph |

`example_ontology.py` is the starter ontology every example passes via
`ontology=` — copy it and adapt the types to your domain.

## The sample data

Everything under `data/` follows one scenario centered on **Alder Ridge
Robotics**. The same people, products, and projects recur across emails, a
handbook, a directory export, a catalog, a Slack export, and chat histories so
the resulting graph contains useful cross-source relationships.

Thread-message files (`chat_history.jsonl`, `combined_threads.jsonl`) are one
JSON object per line with columns matching `ThreadMessage`; a JSON array or
CSV with the same columns also works:

```json
{"thread_id": "support-1", "role": "user", "name": "Morgan Lee",
 "content": "...", "created_at": "2025-04-10T15:02:00Z"}
```

Every row is validated client-side before the first API call — role, RFC3339
`created_at`, metadata limits — so a bad line 500 fails fast, not mid-run.
