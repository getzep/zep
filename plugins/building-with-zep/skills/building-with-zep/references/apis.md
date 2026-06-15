# Zep retrieval and search APIs

Three retrieval methods, from most automatic to most manual. Default to the top of this list; descend only with a reason.

| Method | Query control | Format control | Graph types | Best for |
|--------|---------------|----------------|-------------|----------|
| `thread.get_user_context()` (Context Block) | Automatic (last 2 messages) | Fixed | User graphs only | Most conversational agents |
| Context templates | Automatic (last 2 messages) | Custom | User graphs only | Consistent custom layout |
| `graph.search()` + manual assembly | Full | Full | User or standalone | Max control, standalone graphs |

## 1. Context Block — `thread.get_user_context`

Optimized, prompt-ready string built by **Smart Context Assembly** (auto search: semantic + full-text + breadth-first, over the entire user graph, relevance from the last two messages). P95 < 200ms.

```python
user_context = client.thread.get_user_context(thread_id=thread_id)
context_block = user_context.context     # drop into prompt
```

Output shape:
```text
<USER_SUMMARY>
Emily Painter is a user with account ID Emily0e62 who uses digital art tools...
</USER_SUMMARY>
<FACTS>
  - Emily is experiencing issues with logging in. (2024-11-14 02:13:19+00:00 - present)
  - User account Emily0e62 has a suspended status due to payment failure. (... - present)
</FACTS>
```

Always includes the user summary; adds the most relevant facts, entities, episodes, observations, and thread summaries. You can also get it from `add_messages(..., return_context=True)`.

## 2. Context templates

Automatic relevance, your fixed layout. Define once, reuse via `template_id`.

```python
client.context.create_context_template(
    template_id="customer-support",
    template="""# CUSTOMER PROFILE
%{user_summary}

# FACTS
%{edges limit=10}

# KEY ENTITIES
%{entities limit=5 types=[person,organization]}

# RECENT CONVERSATIONS
%{thread_summaries limit=3}""",
)

user_context = client.thread.get_user_context(thread_id=thread_id,
                                               template_id="customer-support")
```

Variables: `%{user_summary}` (no params), `%{edges}`, `%{entities}`, `%{episodes}`, `%{observations}`, `%{thread_summaries}`. Params (except user_summary): `limit=N` (≤1000; observations/thread_summaries render ≤20), `types=[...]`, `include_attributes=true/false`. Manage with `context.{get,list,update,delete}_context_template`.

## 3. `graph.search`

The general-purpose method for standalone graphs, non-thread queries, tool-call retrieval, and precise control. Works with `user_id=` or `graph_id=`.

### Auto search (recommended entry point)

```python
res = client.graph.search(
    user_id=user_id,
    query="What did we decide about the pricing rollout?",
    scope="auto",
    max_characters=2500,          # default 2500, max 50000
    return_raw_results=False,     # True to also get typed arrays
)
print(res.context)                # ready-to-use block
```

`scope="auto"` retrieves across all data shapes in parallel, applies a **cross-scope rerank**, and packs to the character budget. With `scope="auto"` the `reranker` param is ignored (it uses its own). `return_raw_results=True` exposes `res.edges`, `res.nodes`, etc. with `score` and `selection_rank`.

### Scoped search (one result type)

```python
res = client.graph.search(user_id=user_id, query="payment failures",
                          scope="edges", limit=5)   # facts
for edge in res.edges or []:
    print(edge.name, edge.fact, edge.score)
```

Scopes: `edges` (facts, default), `nodes` (entities → `res.nodes`), `episodes` (`res.episodes`), `observations` (`res.observations`), `thread_summaries` (`res.thread_summaries`).

### Rerankers (ignored when scope=auto)

| Reranker | Behavior | Notes |
|----------|----------|-------|
| `rrf` (default) | Reciprocal Rank Fusion of semantic + BM25 | general purpose |
| `mmr` | relevance + diversity | requires `mmr_lambda` (0.0 diversity → 1.0 relevance) |
| `cross_encoder` | joint neural model, higher accuracy, slower | adds `relevance` field (0–1) for thresholding |
| `episode_mentions` | favors items mentioned across many episodes | frequency bias |
| `node_distance` | ranks by graph hops from a center node | requires `center_node_uuid` |

### Filters — `search_filters`

```python
from zep_cloud.types import SearchFilters, PropertyFilter

res = client.graph.search(
    user_id=user_id, query="engineering team", scope="edges",
    search_filters=SearchFilters(
        property_filters=[PropertyFilter(comparison_operator="=",
                          property_name="department", property_value="Engineering")],
    ),
)
```

- Type filters: `{"node_labels": [...]}`, `{"edge_types": [...]}`, plus `exclude_node_labels` / `exclude_edge_types`.
- Property filters: operators `=`, `<>`, `>`, `<`, `>=`, `<=`, `IS NULL`, `IS NOT NULL`.
- Datetime filters (edge scope): on `created_at`/`valid_at`/`invalid_at`/`expired_at`; inner list = AND, outer list = OR.
- Episode metadata filters via `EpisodeMetadataFilter` / `MetadataFilterGroup`.

### Breadth-first search (recency bias)

Bias results toward what's connected to recent context by seeding from recent episode nodes:

```python
eps = client.graph.episode.get_by_user_id(user_id=user_id, lastn=10).episodes
res = client.graph.search(user_id=user_id, query="project updates", scope="edges",
                          bfs_origin_node_uuids=[e.uuid_ for e in eps], limit=10)
```

## 4. Manual context construction

Required for building a custom block from a **standalone** graph; also for full control on user graphs. Run several searches and format the string yourself:

```python
edges = client.graph.search(graph_id="company-kb", query=q, scope="edges", limit=10)
nodes = client.graph.search(graph_id="company-kb", query=q, scope="nodes", limit=5)
block = "<FACTS>\n" + "".join(f"  - {e.fact}\n" for e in edges.edges or []) + "</FACTS>\n"
block += "<ENTITIES>\n" + "".join(f"  - {n.name}: {n.summary}\n" for n in nodes.nodes or []) + "</ENTITIES>\n"
```

## Direct getters (no search)

| Method | Returns |
|--------|---------|
| `graph.edge.get(uuid_)` / `graph.edge.get_by_user_id(user_id, limit, uuid_cursor)` | fact(s) |
| `graph.node.get(uuid_)` / `graph.node.get_by_user_id(...)` | entity/entities |
| `graph.episode.get(uuid_)` / `graph.episode.get_by_user_id(user_id, lastn)` | episode(s) |
| `user.get_node(user_id)` | user node (+ `.summary`) |
| `graph.observation.get(...)` / `graph.observation.get_by_user_id(...)` | observation(s) |
| `thread.get_summary(thread_id)` / `graph.thread_summary.get_by_user_id(...)` | thread summary/summaries |

Replace `_by_user_id` with `_by_graph_id` for standalone graphs.

For exact parameters or endpoints not captured here, query the **`zep-docs` MCP server**, or see the SDK/API reference at https://help.getzep.com/sdk-reference and the guides at https://help.getzep.com.

## Retrieval philosophy

Zep optimizes for **recall over precision**: missing a critical fact can break the agent, so it returns everything relevant and lets the LLM filter, rather than risk omission. Hybrid search (semantic + BM25 + graph/BFS, reranked) is why it beats vector similarity alone — it captures concepts, exact terms, and relationships together. Benchmarks: LoCoMo 94.7% @ 155ms; LongMemEval 90.2% @ 162ms.
