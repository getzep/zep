# Zep LiveKit Integration

This package uses the Zep V4 Python SDK.

## Resource IDs

Create users and threads before an agent turn with `create_user` and
`create_thread`. Zep assigns each resource UUID. Store `user.uuid_` and
`thread.uuid_` in the application database. Do not add runtime calls to
`user.lookup`, `thread.lookup`, `graph.lookup`, or `lookup.batch`.

## Agents

- `ZepUserAgent` takes `user_uuid` and `thread_uuid`. It stores user messages
  with `thread.add_messages(..., return_context=True)`. Its session handler
  stores assistant messages separately.
- `ZepGraphAgent` takes `graph_uuid`. It stores messages with
  `graph.episode.add` and retrieves context with `graph.get_context`.
- Both agents must isolate Zep errors from the LiveKit turn. Keep custom
  context builders async and validate custom templates for a literal
  `{context}` placeholder.
- Keep user messages under 4,096 characters and graph episode data under
  10,000 characters. Log lengths only when truncation occurs.

## Graph search tool

`create_graph_search_tool` requires `graph_uuid`. Route `edges`, `nodes`,
`episodes`, `observations`, and `thread_summaries` to their V4 search methods.
Route `auto` to `graph.get_context`. Use typed filters for each search scope.
Keep `search_filters` and BFS origin UUIDs constructor-only. Return errors as
strings from the tool.

## Checks

Run package checks with `make ci`. Run the live test with:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```
