# Setup

This guide gets you from zero to a running AG2 agent with Zep memory.

## 1. Sign up for Zep and get an API key

1. Create a free account at [https://www.getzep.com](https://www.getzep.com).
2. Open the Zep dashboard at [https://app.getzep.com](https://app.getzep.com).
3. Create (or select) a project and copy its **API key**.

## 2. Install

```bash
pip install zep-ag2
```

This installs the integration along with its runtime dependencies (`ag2` and
`zep-cloud`).

For local development of this package:

```bash
make install   # uv sync --extra dev
```

## 3. Configure environment variables

The examples read configuration from environment variables. Copy the template
and fill in your keys:

```bash
cp .env.example .env
```

```bash
# Required
export ZEP_API_KEY="your-zep-cloud-api-key"

# Required for examples that call an LLM
export OPENAI_API_KEY="your-openai-api-key"

# Required for ag2_tools_search.py, which reads an existing Zep user
export ZEP_USER_UUID="uuid-of-an-existing-zep-user"
```

The Zep v4 SDK sends requests to `https://api.getzep.com/api/v4` by default.
Give the client an explicit `base_url` only when you use a different endpoint.

## 4. Run an example

```bash
python examples/ag2_basic.py
```

Other runnable examples live in [`examples/`](examples):

- `ag2_basic.py` — system message injection + memory tools
- `ag2_graph.py` — knowledge graph with `ZepGraphMemoryManager`
- `ag2_tools_search.py` — read-only search tool registration; needs `ZEP_USER_UUID`
- `ag2_tools_full.py` — all tools across a multi-agent GroupChat
- `manual_test.py` — end-to-end integration test against real APIs

## Identifiers

Zep v4 addresses every user, thread, and graph by a server-generated UUID. Use
`create_user` and `create_thread` one time for each user, and store
`user.uuid_`, `user.graph_uuid`, and `thread.uuid_` in your own database. The
integration takes those UUIDs and does not resolve a name at run time.

## Next steps

- Read the [README](README.md) for the API reference.
- See the [Zep documentation](https://help.getzep.com) for memory concepts,
  knowledge graphs, and best practices.
