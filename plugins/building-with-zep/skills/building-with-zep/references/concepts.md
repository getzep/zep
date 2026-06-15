# Zep core concepts, in depth

Each concept below includes what it **is**, what it **is not** (the misconceptions that cause integration bugs), and how to read it.

## Users and user graphs

A **user** represents an individual using your application (`user_id`, plus optional `email`, `first_name`, `last_name`). Each user has an associated **user graph**, created automatically the first time you add data for them.

The user graph fuses **everything** about that user — every thread, plus any business data added with `graph.add(user_id=...)` — into one unified Context Graph. It carries a single **user node** with a regenerated **user summary**, and uses Zep's default ontology unless you customize it.

- **It is not** partitioned by thread. > "The knowledge graph does not separate the data from different threads, but integrates the data together to create a unified picture of the user."
- **It is not** something you create explicitly — adding a user (or adding data for one) creates it.
- Deleting the user deletes all their threads, artifacts, and graph knowledge in one operation (Right To Be Forgotten).

## Standalone graphs

A standalone graph (`graph.create(graph_id=...)`) is a general-purpose Context Graph about an *object or system* rather than a person: shared knowledge bases, product catalogs, policy/runbook corpora, domain knowledge used across many users, or test graphs.

- **It is not** for personalized user memory. It has **no user node and no user summary**. For up-to-date knowledge about a user, use a user graph.
- Retrieval from a standalone graph uses `graph.search` (there is no `get_user_context` for it) — typically `scope="auto"`, or manual construction for a custom block.

Use the same `graph.add` / `graph.search` methods, passing `graph_id=` instead of `user_id=`. Direct getters: `graph.node.get_by_graph_id`, `graph.edge.get_by_graph_id`, etc.

## Threads

A **thread** is a conversation: an ordered sequence of messages belonging to one user. A user can have many. `thread.add_messages` records history *and* ingests messages into the user graph (each message is also stored as a `message` episode).

- **It is not** an isolated conversation store, and **not** the container for facts. Facts extracted from a thread land in the *user* graph and are fused with everything else.
- **It is not** the scope of retrieval. `thread.get_user_context(thread_id)` searches the whole user graph; the thread's last two messages only decide *what's relevant now*.
- Creating a thread warms the user's graph cache in the background, improving first-retrieval latency.

## Entities (nodes) and entity summaries

An **entity** is a noun extracted from ingested data — a person, account, product, place, or concept. Each has a short **name** and a narrative **summary** that is regenerated incrementally as new facts arrive (previous summary + new information).

- The summary gives **depth** (contextualized history of that entity). It **is not** a precise dated claim — those are facts on edges.
- Zep **deduplicates and merges** entities automatically when it decides two refer to the same thing. You don't manage node identity.

Read them:
```python
entities = client.graph.node.get_by_user_id(user_id="emily-painter", limit=20)
for e in entities:
    print(e.name, e.summary)
one = client.graph.node.get(uuid_=entities[0].uuid_)
```

## Edges and facts

A **fact** is a precise, time-stamped claim stored **on an edge** between two entities — e.g. "User account Emily0e62 has a suspended status due to payment failure." Edges have a SCREAMING_SNAKE_CASE `name` (`WORKS_AT`, `OWNS`) and four timestamps:

| Field | Meaning |
|-------|---------|
| `created_at` | when Zep learned the fact |
| `valid_at` | when the fact became true (real-world time) |
| `invalid_at` | when the fact stopped being true |
| `expired_at` | when Zep learned it was no longer valid |

Facts give **breadth** — many granular, citable, dated claims.

- A fact **is not** independent of its edge, and **not** a vector chunk.
- **Temporal updates are automatic.** When new data contradicts a fact, Zep invalidates the old fact (sets `invalid_at`) and adds new ones — keeping the old as history. You don't manage temporal consistency. Example: "Kendra loves Adidas shoes" → later → invalidate that, add "Kendra's Adidas shoes broke" and "Kendra likes Puma shoes."

Read them:
```python
edges = client.graph.edge.get_by_user_id(user_id="emily-painter")
for edge in edges:
    print(edge.name, edge.fact, edge.valid_at, edge.invalid_at)
```

**Entity summaries vs facts:** the Context Block uses both deliberately. Facts = discrete dated claims; entity summaries = rolled-up narrative. Reach for facts when you need a specific, citable claim; for entity summaries when you need an entity's overall story.

## Episodes

An **episode** is the raw artifact you handed Zep — a chat message, a text chunk, or a JSON object — stored **verbatim** alongside the entities, edges, and summaries derived from it.

- **It is not** the extracted knowledge. Reach for episodes when the agent needs the exact source wording, a citation, or surrounding context that didn't become its own fact.

```python
recent = client.graph.episode.get_by_user_id(user_id="emily-painter", lastn=20)
for ep in recent.episodes:
    print(ep.created_at, ep.source, ep.content)
```

## User summary

A single derived narrative of **who the user is** — stable traits, preferences, what they've done — synthesized from the whole graph and attached to the user node. It is included **unconditionally** at the top of every Context Block.

- **It is not** query-filtered and **not** per-conversation: the same baseline regardless of the latest message. It answers "who am I talking to," independent of what was just said.
- Standalone graphs have no user summary.
- You don't write it directly; Zep regenerates it as facts accumulate. Shape it with [user summary instructions](customization.md).

```python
resp = client.user.get_node(user_id="emily-painter")
print(resp.node.summary if resp.node else None)
```

## Thread summaries

A natural-language summary of the messages in **one** thread — the arc of a single conversation (e.g. what problem arose and how it resolved). One per thread, auto-generated and incrementally updated, persisted on the user's graph.

- **It is not** a user-wide view (that's the user summary and facts across threads), and **not** something you trigger manually.

```python
s = client.thread.get_summary(thread_id="thread-42")            # one thread
page = client.graph.thread_summary.get_by_user_id(user_id="u", limit=20)  # all for a user
```

## Observations (Flex Plus and Enterprise plans)

A **durable, evidence-backed pattern** Zep automatically derives by analyzing graph structure — a decision, commitment, constraint, preference, state transition, recurring pattern, or stable relationship spanning one or more entities. It captures *why something matters* across many facts.

- **It is not** "just more facts" (granular edge claims) nor an entity summary (single-node narrative) — it's a cross-entity synthesis.
- **Read-only.** You can't create, edit, or delete observations; they follow the evidence and are regenerated/retired as the graph changes.

```python
obs = client.graph.observation.get_by_user_id(user_id="emily-painter", limit=20)
for o in obs:
    print(o.name, o.summary)
```

## How a concept maps to the Context Block

`thread.get_user_context` returns a string containing the **user summary (always)** plus the most relevant **facts, entities, episodes, observations, and thread summaries** for the current moment — sized for the token budget. Everything above is what fills it.
