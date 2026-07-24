# Setup

## 1. Get a Zep account and API key

1. Sign up at [getzep.com](https://www.getzep.com).
2. Create an API key in the [Zep dashboard](https://app.getzep.com) (Project → API Keys).
3. Export it:

```bash
export ZEP_API_KEY=your_key_here
```

## 2. Install

```bash
pip install zep-ingest
# optional, for LLM contextualization of chunked documents:
pip install "zep-ingest[anthropic]"   # and/or "zep-ingest[openai]"
```

Requires Python ≥ 3.11.

## 3. Batch API vs sequential (plan requirements)

- The **Batch API** is available to **enterprise plans only** — contact your
  Zep account team to enable it. It is the fastest path for large backfills
  (up to 50,000 items per batch).
- **Everything in this package also works without it**: the default
  `method="auto"` detects Batch API availability and falls back to sequential
  `graph.add` ingestion with rate-limit-aware pacing. Force a path with
  `method="batch"` or `method="sequential"`.

## 4. Getting a Slack export

Workspace admin → **Settings & administration** → **Workspace settings** →
**Import/Export Data** → **Export**. Download the .zip; `ingest_slack_export`
reads it directly (no need to extract). A small sample export is bundled at
`examples/data/slack_export/` so the example runs without one.

## 5. Run an example

Every example is self-contained: it creates a fresh `example-*-<timestamp>`
graph (or user), sets the starter ontology, and ingests bundled sample data —
no arguments needed, only `ZEP_API_KEY`. LLM keys (`ANTHROPIC_API_KEY` /
`OPENAI_API_KEY`) are optional and only enrich `documents_example.py`.

```bash
cd examples
python slack_export_example.py
python email_example.py
```

Re-running creates a new graph each time; delete old `example-*` graphs from
the [dashboard](https://app.getzep.com) when you're done. See
[`examples/README.md`](examples/README.md) for the full list.

## 6. Development setup

```bash
git clone https://github.com/getzep/zep
cd zep/ingestion
make install    # uv sync --extra dev
make all        # format + lint + type-check + test
```

Live integration tests (`tests/test_integration.py`) run only when
`ZEP_API_KEY` is set; they create and delete a throwaway graph.
