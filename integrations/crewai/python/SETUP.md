# Setup Guide

This guide walks you from a fresh machine to running the example agent with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get an OpenAI API key (for the example)

The integration itself is model-agnostic, but the bundled examples and live
tests drive the CrewAI agent with OpenAI. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Install

Using `pip`:

```bash
pip install zep-crewai
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/crewai/python
make install        # uv sync --extra dev
```

Requirements: Python 3.11+, `crewai>=1.0.0`, `zep-cloud==4.0.0a5`.

`zep-cloud` 4.x is a pre-release. To install it with `pip`, add the `--pre`
flag, or pin the version:

```bash
pip install "zep-cloud==4.0.0a5"
```

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"

# Optional: override the Zep API URL (the SDK defaults to the v4 API)
export ZEP_API_URL="https://api.getzep.com"

# Optional: override the OpenAI model used by the live test (default: gpt-4o-mini)
export OPENAI_MODEL="gpt-4o-mini"
```

## 5. Run the example

From the repository:

```bash
uv run python examples/simple_example.py
```

Or, if you installed with `pip`:

```bash
python examples/simple_example.py
```

Other runnable examples live in [`examples/`](examples):

- `simple_example.py` — `ZepStorage` adapter + the Zep search tool
- `crewai_user.py` — `ZepUserStorage` for per-user conversation + graph memory
- `crewai_graph.py` — `ZepGraphStorage` for a shared knowledge graph
- `crewai_tools.py` — search + add tools across a multi-agent crew

## 6. Identifiers

Zep v4 addresses every user, thread, and graph by a server-generated UUID. An
example creates each resource, reads the UUID from the create response, and
passes the UUID to the storage adapters and the tools:

```python
user = zep_client.user.create(first_name="Alice", email="alice@example.com")
thread = zep_client.thread.create(user_uuid=user.uuid_)

storage = ZepUserStorage(
    client=zep_client,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
)
```

Your application stores these UUIDs in its own database. The integration does
not resolve a `user_id` or a `thread_id` at run time.

## 7. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

Live integration test (requires `ZEP_API_KEY` and `OPENAI_API_KEY`):

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

## Troubleshooting

- **`ZepDependencyError` on import** — CrewAI is not installed. Run
  `pip install zep-crewai` (which pulls `crewai`).
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The example and live test wait for the graph to
  build; increase the wait if your graph is large or under load.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
