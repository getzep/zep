# Setup Guide

This guide walks you from a fresh machine to running the example agent with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get a Google API key (for the Gemini model)

The bundled example and live tests drive a Google ADK agent with a Gemini model,
so a live run needs a Google API key:

1. Create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Copy it for `GOOGLE_API_KEY`.

You only need this to run the model. The Zep integration wiring (persisting
messages, injecting context) works with any ADK-supported model — swap `model=`
in the example for your provider.

## 3. Install

Using `pip`:

```bash
pip install zep-adk
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/adk/python
make install        # uv sync --extra dev
```

Requirements: Python 3.11+, `google-adk>=1.19.0,<3`, `zep-cloud>=3.23.0`.

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

## 5. Run the example

From the repository:

```bash
uv run python examples/basic_agent.py
```

Or, if you installed with `pip`:

```bash
python examples/basic_agent.py
```

The example:

1. Provisions the Zep user and thread out-of-band with `ensure_user` /
   `ensure_thread` before the first turn — the agent's turn path
   (`ZepContextTool`) never creates them itself. When wiring your own agent,
   call these once (e.g. during account or session onboarding) before running
   any turns.
2. Seeds facts about a user across two turns in one thread.
3. Waits for Zep to process the knowledge graph (ingestion is asynchronous).
4. Asks recall questions in that same thread.
5. Provisions a **second, brand-new** thread for the same user and asks a
   recall question there — the agent answers using facts fused into the
   user's graph from the first thread, demonstrating cross-thread recall.

## 6. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

Live integration test (requires `ZEP_API_KEY` and `GOOGLE_API_KEY`). It runs as a
standalone script — not under pytest — and exits non-zero if any check fails:

```bash
uv run python tests/test_integration.py
```

## Troubleshooting

- **`ZEP_API_KEY is not set`** — export the key (step 4) before running.
- **`GOOGLE_API_KEY is not set`** — the example uses a Gemini model; export the
  key, or swap `model=` in the example for an ADK-supported provider you have a
  key for.
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The example waits for the graph to build;
  increase the wait if your graph is large or under load.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
