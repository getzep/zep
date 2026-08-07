# zep-strands

Add long-term agent memory to [Strands Agents](https://strandsagents.com) via Zep's temporal Context Graph.

`ZepMemoryStore` implements Strands' [`MemoryStore`](https://strandsagents.com/docs/user-guide/concepts/memory/overview/) interface, so it plugs into `MemoryManager` for automatic recall (injection + `search_memory`), server-side extraction (`add_messages` → Zep threads), and optional on-demand graph search.

## Installation

```bash
pip install zep-strands
```

Requires Python 3.11+, `strands-agents>=1.45.0`, `zep-cloud>=3.23.0`, and a Zep Cloud API key from [app.getzep.com](https://app.getzep.com/).

## Quick start

```python
from strands import Agent
from strands.memory import MemoryManager
from zep_cloud.client import AsyncZep
from zep_strands import ZepMemoryStore, ensure_thread, ensure_user

zep = AsyncZep(api_key="your-api-key")

await ensure_user(
    zep,
    user_id="user-123",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
)
await ensure_thread(zep, thread_id="thread-abc", user_id="user-123")

store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    first_name="Jane",
    last_name="Smith",
    writable=True,
    extraction=True,  # server-side via add_messages
)

agent = Agent(
    system_prompt="You are a helpful assistant with long-term memory.",
    memory_manager=MemoryManager(stores=[store]),
)
```

With no further configuration, the manager injects relevant Zep context before each user turn and runs server-side extraction on Strands' default cadence (**every 5 turns**). That means conversation turns are buffered and only sent to Zep when the trigger fires (or when you call `memory_manager.flush()`), so graph building is delayed relative to turn-by-turn persistence — and Zep's own ingestion remains asynchronous after messages arrive. Enable `add_tool_config=True` on the manager to also let the model call `add_memory`.

## How it works

| Strands hook | Zep call | Purpose |
|--------------|----------|---------|
| `MemoryStore.search` | `graph.search` | Recall for injection and the `search_memory` tool |
| `MemoryStore.add_messages` | `thread.add_messages` | Server-side extraction from conversation turns |
| `MemoryStore.add` | `graph.add` | Single-fact writes (`add_memory` tool / programmatic) |
| First `search` / write | `user.add` + `thread.create` | Provision resources on first use (see below) |
| `MemoryStore.get_tools` | `create_zep_search_tool` | Optional on-demand graph search (when enabled) |

Context comes from the **whole user graph**; the thread only scopes relevance and records the conversation. A new thread for the same user still recalls earlier facts.

## Automatic extraction and delayed graph building

`extraction=True` (the default when the store is writable with `user_id` + `thread_id`) opts into Strands' automatic extraction loop. With the manager's defaults that means:

1. Conversation turns are buffered in the manager.
2. Every **5 turns**, Strands calls `add_messages`, which posts the batch to Zep via `thread.add_messages`.
3. Zep then processes the batch asynchronously into the user graph.

Until step 2 runs, **nothing has been sent to Zep**, so the graph does not grow turn-by-turn. After step 2, facts are still not instantly searchable (Zep ingestion is async). Plan for both delays:

- Call `await memory_manager.flush()` at session boundaries (required after `invoke_async` / `stream_async` if you need pending turns persisted before shutdown).
- Or pass an every-turn trigger if you need messages sent to Zep more often:

```python
from strands.memory.extraction.triggers import InvocationTrigger
from strands.memory.extraction.types import ExtractionConfig

store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    writable=True,
    extraction=ExtractionConfig(trigger=InvocationTrigger()),  # after every turn
)
```

`extraction=True` (or an `ExtractionConfig`) **requires** writable user-graph mode with both `user_id` and `thread_id`. Construction raises `ValueError` otherwise — use `extraction=False` for standalone graphs or read-only stores.

## Scoping modes

**User graph** (default for conversational agents) — pass `user_id` and `thread_id`:

```python
ZepMemoryStore(zep_client=zep, user_id="user-123", thread_id="thread-abc", ...)
```

**Standalone graph** (shared / domain knowledge) — pass `graph_id`. Supports `search` and `add` only (no `add_messages`):

```python
ZepMemoryStore(zep_client=zep, graph_id="company-kb", writable=True, extraction=False)
```

Provide exactly one of `user_id` or `graph_id`.

## Provisioning users and threads

`ensure_user` / `ensure_thread` are idempotent create-then-catch-conflict helpers. Both return `True` when newly created and `False` when the resource already exists; genuine failures raise.

Call them out-of-band before the first turn so misconfiguration surfaces loudly. If you skip them, the store provisions itself on its first search or write instead.

`ZepMemoryStore.initialize()` deliberately makes **no** Zep calls. `Agent.__init__` is synchronous, so Strands runs that hook on a throwaway event loop in a worker thread; calling Zep there would drive your `AsyncZep` client from a second event loop and raise `RuntimeError: ... is bound to a different event loop` for any connection you had already opened. Deferring to first use keeps every Zep call on the agent's own loop, so one client can safely be shared between your application code and the store.

```python
from zep_strands import ensure_thread, ensure_user

created = await ensure_user(
    zep,
    user_id="user-123",
    first_name="Jane",
    last_name="Smith",
    on_created=configure_ontology,  # optional async hook
)
await ensure_thread(zep, thread_id="thread-abc", user_id="user-123")
```

## Search and injection

By default `search_scope="auto"`, so injection receives Zep's assembled Context Block as a single `MemoryEntry`. Pin a scoped search when you want discrete facts:

```python
store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    search_scope="edges",
    search_filters={"edge_types": ["PREFERS"]},
)
```

## On-demand graph search tool

Set `expose_search_tool=True` to register a model-callable `zep_search` tool via `get_tools()`:

```python
store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    expose_search_tool=True,
    search_pinned_params={"scope": "auto", "limit": 10},
)
```

Or build the tool yourself:

```python
from zep_strands import create_zep_search_tool

tool = create_zep_search_tool(
    zep_client=zep,
    user_id="user-123",
    search_pinned_params={"scope": "edges"},
)
agent = Agent(tools=[tool])
```

**Pin-or-expose.** Every `graph.search` parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) is model-exposed by default. `search_pinned_params` fixes a value and hides it; `search_hidden_params` hides without pinning (Zep's default applies). `search_filters` and `bfs_origin_node_uuids` are always constructor-only.

## Writing facts

```python
# Text fact into the user graph
await store.add("Prefers aisle seats", metadata={"source": "prefs"})

# JSON payload
await store.add('{"plan": "premium"}', metadata={"type": "json"})
```

`metadata["type"]` selects the Zep data type (`text` default, `json`, or `message`). Remaining metadata keys are forwarded as episode metadata.

Oversized `text`/`message` payloads are truncated to Zep's `graph.add` limit with a warning. Oversized `json` is **rejected with a `ValueError`** instead — slicing JSON strips its closing syntax, so a truncated document would just be rejected by Zep. Split large JSON into smaller documents before adding (see [chunking](https://help.getzep.com/chunking-large-documents)).

## Error handling

Zep SDK errors propagate out of the store methods deliberately: in Strands the **framework** owns failure isolation, and swallowing them breaks it.

| Path | Framework behavior on a raise |
|------|-------------------------------|
| `search` | `MemoryManager.search` logs and skips the failing store; injection additionally fails open, so the turn proceeds without memory |
| `add` | Surfaced as `AggregateMemoryError` so a failed write is never silent |
| `add_messages` | `ExtractionCoordinator` catches it and **rolls back its high-water mark so the batch retries** |

That last one matters most: returning `None` after swallowing an error would be read as success, advancing the mark and discarding those messages permanently. This matches the SDK's own vended stores, which raise rather than degrade.

The one exception is the model-callable `zep_search` tool, which catches Zep errors and returns an error string — a raw tool has no framework layer above it.

## Identity

Pass real names (`first_name`, `last_name`, `email`) so Zep anchors the user graph node. Display names on persisted messages default to the user's full name / `"Assistant"`.

One store instance is bound to one `user_id`/`thread_id` (or `graph_id`) at construction.

## Features

- Native Strands `MemoryStore` — works with `MemoryManager` injection, tools, and extraction
- Server-side extraction via Zep threads (`add_messages`); default cadence every 5 turns (delayed graph building)
- Fail-fast validation when `extraction` is enabled without a writable user/thread
- Whole-user-graph recall across threads
- Standalone-graph mode for shared knowledge
- Optional pin-or-expose `zep_search` tool
- Idempotent `ensure_user` / `ensure_thread` provisioning
- Message and graph payload truncation with length-only warnings

## Configuration

```bash
export ZEP_API_KEY="your-zep-api-key"
```

## Examples

See [`examples/basic_agent.py`](examples/basic_agent.py) for an end-to-end multi-thread recall demo. Setup steps are in [`SETUP.md`](SETUP.md).

## Development

```bash
cd integrations/strands/python
make install      # uv sync --extra dev
make all          # format + lint + type-check + test
```

## Requirements

- Python 3.11+
- `strands-agents>=1.45.0`
- `zep-cloud>=3.23.0`

## Support

- [Zep Documentation](https://help.getzep.com)
- [Strands Memory guide](https://strandsagents.com/docs/user-guide/concepts/memory/overview/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 — see the repository [`LICENSE`](../../../LICENSE).
