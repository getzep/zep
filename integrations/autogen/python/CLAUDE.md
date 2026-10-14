# AutoGen integration guide

This package implements the Zep v4 API for AutoGen. Use the v4 SDK models and
method signatures in `zep-cloud`; do not call `user.lookup`, `thread.lookup`,
`graph.lookup`, or `lookup.batch`.

## Memory contract

AutoGen calls `update_context()` before a model call. The application calls
`add()` to persist each user and assistant message. These operations are
separate. Do not add automatic persistence to `update_context()`.

`ZepUserMemory` uses `user_uuid`, an optional `thread_uuid`, and an optional
`graph_uuid`. When `thread_uuid` is absent, `add()` creates a thread. A thread
creation or persistence error is logged and swallowed. Create the thread with
`create_thread()` before the turn when the caller must handle a creation error.

`ZepGraphMemory` uses a caller-created `graph_uuid`. It reads the two most
recent episodes, then uses `graph.get_context()` to prepare trusted context.
The class does not create or delete the shared graph.

The optional user-memory `context_builder` replaces the default retrieval path.
It receives a `ContextInput` with the client, UUIDs, user message, and AutoGen
model context. A builder error is logged and does not fail the agent turn.
Persistence remains a separate call to `add()`.

## Provisioning

`create_user()` calls `client.user.create()` and returns the SDK model.
`create_thread()` calls `client.thread.create()` and returns the SDK model.
Both resources receive server-generated UUIDs. The `create_user()` hook runs
after creation and receives the client and returned user UUID. Hook errors
propagate to the caller.

## Search tools

AutoGen `FunctionTool` builds its schema from the wrapped function signature.
`create_search_graph_tool()` therefore assigns a typed, dynamic signature to
the wrapped function. Keep exposed parameters in that signature. Pinned and
hidden parameters must not appear in the signature.

The tool supports the `edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, and `auto` scopes. The first five scopes call their
scope-specific v4 search methods. The `auto` scope calls
`graph.get_context()`. Convert shared search filters to the typed filter model
for each scope before the SDK call.

`search_filters` and `bfs_origin_node_uuids` are constructor-only parameters.
Do not expose them in the model schema. Omit optional values when they are
unset so the SDK applies its own defaults.

## Checks

Run the package checks from this directory:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src/
uv run pytest tests/ -v
```

Run the live test with `ZEP_API_KEY` and `OPENAI_API_KEY` in the environment:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```
