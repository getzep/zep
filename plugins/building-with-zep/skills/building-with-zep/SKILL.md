---
name: building-with-zep
description: Guide for building and configuring applications that use Zep — agent memory built on temporal Context Graphs, for use cases that need low-latency retrieval, many users and agents, multi-source ingestion, and governance. Use whenever you are writing or designing code that integrates Zep: adding agent memory or long-term context to an agent or chatbot, ingesting chat/business/document data into a Context Graph, retrieving a Context Block or searching the graph, choosing between user graphs and standalone graphs, defining a custom ontology or custom instructions, or deciding how to evaluate and tune Zep for a use case. Triggers on requests like "add memory to my agent", "integrate Zep", "store this in Zep", "search the Zep graph", "set up a Zep ontology", "how should I structure my Zep graphs", "make my agent remember users". Covers what Zep is, its core concepts, the high-level vs low-level APIs, customization, and how to start simple and benchmark.
---

# Building with Zep

Zep is **agent memory** — for use cases that require low-latency retrieval, large numbers of users and agents, ingestion from many sources, and governance. This skill teaches you how to reason about Zep and build apps on it correctly — what it is, how its pieces fit together, which API to reach for, when to customize, and how to validate that it works for a given use case.

Read this top-to-bottom before writing Zep code. Pull in the reference files (under `references/`) when you need exact method signatures, parameters, or code.

> All guidance here is for **Zep V3** (SDK packages `zep-cloud` for Python/TypeScript, `github.com/getzep/zep-go/v3` for Go). Ignore anything you may know about the legacy V2 `Memory` API.
>
> Zep has no meaningful free tier — it is a paid product (Subscription and Enterprise plans). Some features noted below are limited to specific plans.

---

## Looking up current details (read first)

This plugin bundles the **`zep-docs` MCP server** — Zep's documentation search. Treat it as the source of truth for anything that must be exact or current: method names, parameters, limits, plan availability, and newer features the summaries here may not cover.

1. **Query the `zep-docs` MCP server first.** Use its documentation-search tool to look up the specific guide or API before relying on memory or on the summaries in this skill, whenever precision matters. It covers both the **guides** and the **SDK/API reference**.
2. **If the MCP server is unavailable, fall back to the Zep documentation directly:**
   - Guides: https://help.getzep.com
   - SDK / API reference: https://help.getzep.com/sdk-reference

The reference files in this skill are a fast, curated map of the API surface and the judgment around it — not a replacement for the live docs. If this skill and the live docs ever disagree, the live docs win.

---

## 1. What Zep does

Zep gives an agent durable, up-to-date memory of **users, the business, and work done**, and serves it back as ready-to-use context in milliseconds.

The flow is always the same three moves:

1. **Ingest** data from any source — chat messages, business records, documents, JSON.
2. Zep **builds a temporal knowledge graph** (its *Context Graph*) of the entities, relationships, and facts in that data, and keeps it current as new data arrives.
3. **Retrieve** a token-efficient *Context Block* (or run targeted graph searches) and drop it into your prompt.

This gives an agent access to relevant prior context across sessions without building and maintaining a separate retrieval pipeline.

The mental model to hold: **Zep is not a chat-log store and not a vector database.** It extracts structured, time-aware knowledge from whatever you feed it, fuses it into a graph per subject, and returns the slice that matters for the current moment.

## 2. How Zep is different

When you're deciding whether Zep fits, or explaining it, these are the differentiators that matter:

- **Many sources, one graph.** Chat, documents, JSON, and business events all flow into the same Context Graph and are fused into one coherent picture of a subject. You don't keep separate stores per data type.
- **Temporal by design.** Every fact carries validity timestamps. When new data contradicts an old fact, Zep invalidates the old one *and keeps it as history* — so you can ask what is true now, or what was true at a past date. This is the key advantage over static GraphRAG and over plain vector search, which have no notion of change over time.
- **Built for change, not static corpora.** Zep is designed for streaming, frequently-updated data and incremental graph updates. Traditional GraphRAG is built for one-time summarization of static documents; reach for Zep when knowledge evolves.
- **Hybrid retrieval, not similarity alone.** Retrieval combines semantic search, full-text (BM25) search, and graph traversal with reranking — capturing conceptual matches, exact keywords, and relationships in one ranked result. It optimizes for **recall** (give the agent everything relevant) over precision.
- **Low latency at scale.** Sub-200ms (P95) retrieval regardless of graph size or number of graphs, which supports large numbers of users and latency-sensitive applications such as voice agents.
- **Many users, many agents.** Each user gets their own graph; standalone graphs hold shared or domain knowledge. The same memory can be served to whichever agents need it.
- **Governance.** Authorization (RBAC/ABAC), retention policies, audit logging, and provenance (every fact traces to the source episode that produced it) apply across every graph and query. Deployment can be Cloud, Cloud + BYOK, or BYOC (your VPC); SOC 2 Type II and HIPAA BAA are available.

## 3. Core concepts

You must get these right — most integration mistakes come from misunderstanding what a thread or a graph actually *is*. Full detail and code in **`references/concepts.md`**; the essentials and the common misconceptions:

| Concept | What it is | What it is **not** |
|---------|-----------|--------------------|
| **User graph** | A Context Graph automatically created for one user; fuses *all* of that user's threads and business data into one picture. The home for agent memory. | Not something you create by hand, and not partitioned by thread. |
| **Standalone graph** | A general-purpose graph (`graph_id`) for knowledge about an object or system — shared knowledge bases, product/domain data, runbooks. | Not for personalized user memory (no user node, **no user summary**). For a user, use a user graph. |
| **Thread** | A conversation: an ordered sequence of messages for one user. Adding messages both records history *and* ingests into the user graph. | **Not** an isolated conversation store and **not** the container for facts. Facts are extracted across *all* threads into the user graph. |
| **Entity (node)** | A noun extracted from data — person, account, product, place, concept — with a regenerated narrative **summary**. | Not where precise claims live (those are facts on edges). Deduplication is automatic; you don't manage node identity. |
| **Edge / fact** | A fact is a precise, time-stamped claim ("Emily's account is suspended") stored *on an edge* between two entities, with `valid_at`/`invalid_at`/`created_at`/`expired_at`. | A fact is not independent of its edge, and not a vector chunk. |
| **Entity summary vs facts** | Summaries roll an entity's history into a narrative (depth); facts are granular dated claims (breadth). The Context Block uses both. | A summary is not a fact and is not citable as a precise dated claim. |
| **Episode** | The raw ingested artifact (a message, text chunk, or JSON object), stored verbatim alongside what Zep derives from it. | Not the extracted knowledge itself — reach for episodes when you need the exact source wording or a citation. |
| **User summary** | One always-on narrative of who the user is, on the user node; included **unconditionally** in every Context Block. | Not query-filtered and not per-thread; the same baseline regardless of the latest message. Standalone graphs don't have one. |
| **Thread summary** | An auto-maintained natural-language summary of *one* thread's arc (what happened in that conversation). One per thread. | Not a user-wide view (that's the user summary / facts) and not something you trigger manually. |
| **Observation** *(Flex Plus and Enterprise plans)* | A durable, evidence-backed pattern Zep derives across many facts/entities — a decision, commitment, preference, recurring behavior. | Not "just more facts" — it's a cross-entity synthesis, and it's read-only (you can't create or edit observations). |

The single most important runtime fact: **`thread.get_user_context(thread_id=...)` returns context from the entire user graph, not just that thread.** The thread is used only to figure out *what's relevant right now* (from its last two messages).

## 4. Choosing an API: high-level first, low-level when you must

Zep's high-level APIs cover most use cases. **Default to the high-level path** and only drop down when you have a concrete reason. Full API surface, parameters, scopes, rerankers, and filters are in **`references/apis.md`**.

**High-level (start here):**
- **`thread.get_user_context(thread_id)`** → returns the **Context Block**: an optimized, prompt-ready string assembled by *Smart Context Assembly* (auto search over the whole user graph, based on the last two messages). This is the right answer for most conversational agents. Sub-200ms.
- **Context templates** → same automatic relevance, but your own fixed layout/sections (`%{user_summary}`, `%{edges}`, `%{entities}`, …). Use when you need consistent custom formatting across threads.
- **`graph.search(..., scope="auto")`** → the recommended entry point for *standalone* graphs (and for non-thread queries). Retrieves across all data shapes, cross-scope reranks, packs to a character budget, returns a ready-to-use `context` string.

**Low-level (when the high-level path isn't enough):**
- **`graph.search` with a specific `scope`** (`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`), plus rerankers (`rrf` default, `mmr`, `cross_encoder`, `episode_mentions`, `node_distance`), `search_filters` (entity/edge types, properties, dates, metadata), and BFS from recent episodes. Use for UI features, tool-call retrieval where the LLM decides when to search, or precise control.
- **Advanced/manual context construction** → run several searches and assemble the string yourself. Maximum control, most code. This is the **required** pattern for retrieving from standalone graphs into a custom block.

Decision shortcut:
- Conversational agent, user memory → `thread.get_user_context`.
- Need fixed sections but automatic relevance → context template.
- Domain/standalone graph → `graph.search(scope="auto")`, or manual construction for a custom block.
- LLM-driven "search when needed" → expose `graph.search` as a tool.
- Need one result type, a filter, or a specific reranker → scoped `graph.search`.

## 5. Customization: ontology and instructions

Customization improves extraction quality, but it is **prompt engineering — iterate, don't front-load it.** Full APIs, limits, and code in **`references/customization.md`**.

- **Custom ontology** (`graph.set_ontology`) — define your own entity types and edge types (max 10 each per scope, ≤10 fields each). Good for: focusing extraction on the entities/relationships that matter in your domain, and enabling precise type-filtered retrieval. Recommended for most production use cases. **Not** good for: modeling everything up front, or as a substitute for good data — many use cases are fully served by default entities + node summaries + facts. Custom *attributes* on types are an advanced feature; you often don't need them. Design entity types as nouns and edge types as verbs/relationships, and start with a few generic types.
- **Custom instructions** (`graph.add_custom_instructions`, Enterprise) — describe your *domain* (terminology, concepts) so Zep interprets data better during extraction. Applied automatically on ingest. Good for: specialized vocabularies (legal, healthcare, internal jargon). **Not** for: defining entity/relationship *types* — that's what ontology is for.
- **User summary instructions** (`user.add_user_summary_instructions`) — steer what the user summary captures (short, question-shaped prompts). Good for: shaping the always-on user baseline. Note they don't apply to Batch-API-ingested data.

The unifying rule: **ontology = the *shape* of the graph (types); instructions = *how to interpret* your domain.** Both are scoped project-wide or per user/graph.

## 6. Best practices: start simple, then benchmark

1. **Start simple.** Get the basic loop working first — create user → create thread → `add_messages` → `get_user_context` — with default ontology and the default Context Block. Don't add custom ontology, templates, or low-level search until you've measured a concrete gap. See **`references/getting-started.md`**.
2. **Ingestion is asynchronous.** Graph building takes time (seconds per message). Don't expect a fact to be retrievable the instant you add it; design for eventual availability and check status when it matters.
3. **Feed data well.** Use `thread.add_messages` for conversation, `graph.add` for documents/JSON/business data (≤10,000 chars/call — chunk larger). Prepare JSON: split large/deeply-nested objects, keep each piece understandable in isolation. Always pass real user names (and ideally last name + email) so the graph resolves identity correctly.
4. **Optimize latency only when needed.** Reuse one client instance; use `return_context=True` on `add_messages` to fold retrieval into the same call; run ingest and search concurrently; warm the user cache on login. Relevant for voice/real-time agents.
5. **Benchmark for your use case before tuning.** Don't guess whether retrieval is good enough — measure it. Zep ships an evaluation harness: write 3–5 example interactions, generate test conversations + questions, ingest, and grade *context completeness* (Zep's job) separately from *answer accuracy* (also your LLM's job). Iterate on ontology and search strategy against that suite, and keep it as a regression test. Full workflow in **`references/evaluation.md`**.
6. **Lead with the high-level path.** Most "Zep isn't returning the right thing" problems are solved by better data and the default Context Block, not by jumping to low-level `graph.search` tuning.

---

## Reference files

- **`references/getting-started.md`** — install, client init, the quick-start loop, adding messages / business data / JSON, batch ingestion, ingestion status.
- **`references/concepts.md`** — every core concept in depth, with what-it-is / what-it-isn't and retrieval code.
- **`references/apis.md`** — the full retrieval and search API surface: Context Block, templates, `graph.search` scopes, rerankers, filters, BFS, direct getters.
- **`references/customization.md`** — ontology, custom instructions, user summary instructions, fact triplets, pattern detection, defaults and limits.
- **`references/evaluation.md`** — the evaluation harness, benchmarking methodology, and what to measure.
- **`references/governance.md`** — RBAC, audit logging, deployment models, compliance.

When you need an exact endpoint or a detail not covered here, look it up rather than guessing: query the **`zep-docs` MCP server** first, and fall back to the SDK/API reference at https://help.getzep.com/sdk-reference and the guides at https://help.getzep.com. This skill captures the shape and the judgment, not every parameter — the live docs are authoritative.
