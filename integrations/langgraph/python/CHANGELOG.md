# Changelog

## 0.1.0 (2026-06-16)

### Added

- **Node / tool helpers (primary path):**
  - `get_zep_context` / `get_zep_context_sync` — fetch a thread's Context Block
    via `thread.get_user_context`.
  - `build_system_message` / `build_system_message_sync` — fold the Context Block
    and base instructions into a LangChain `SystemMessage` for prompt injection.
  - `format_context_block` — combine base instructions with a Context Block.
  - `persist_messages` / `persist_messages_sync` — persist a conversation turn via
    `thread.add_messages`; accept LangChain or Zep messages, with optional
    `return_context` to fold persist + retrieve into one round-trip.
  - `to_zep_message` / `to_zep_messages` — convert LangChain messages to Zep
    messages (role mapping, multimodal-content flattening, length truncation).
  - `create_graph_search_tool` / `create_graph_search_tool_sync` — prebuilt
    LangChain `StructuredTool` over `graph.search`, ready for `create_react_agent`.
- **`ZepStore` (secondary path):** a hybrid-delegate
  `langgraph.store.base.BaseStore`. Implements only the two abstract methods
  (`batch` / `abatch`); delegates exact-key `get` / `put` / `delete` /
  `list_namespaces` to a backing KV store (default `InMemoryStore`), ingests every
  `put` into Zep (`graph.add` with `type="json"`), and routes `search` to Zep
  semantic `graph.search`. Configurable namespace→target resolver, search scope,
  ingestion toggle, and backing-store search merge.
- Graceful error handling throughout: a Zep failure is logged and never crashes
  the host agent.
- Mock-based test suite, two runnable examples (`react_agent.py`,
  `store_agent.py`), README, and SETUP guide.

### Notes

- Targets the Zep V3 SDK (`zep-cloud>=3.23.0`) and `langgraph>=1.2.5`.
- Zep ingestion is asynchronous — there is no read-after-write of graph facts
  within a turn.
