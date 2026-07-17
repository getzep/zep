---
name: building-with-zep
description: Guide for building, designing, reviewing, evaluating, and troubleshooting applications that use Zep — agent memory built on temporal Context Graphs, for use cases needing low-latency retrieval, one or many users and agents, multi-source ingestion, and governance. Use whenever you write or design code that integrates Zep — adding memory or long-term context to an agent or chatbot, ingesting chat/business/document/JSON data into a Context Graph, retrieving a Context Block or searching the graph, choosing between user graphs and standalone graphs, scoping graphs, defining a custom ontology or custom instructions, or deciding how to evaluate and tune Zep for a use case. Triggers on requests like "add memory to my agent", "integrate Zep", "store this in Zep", "search the Zep graph", "set up a Zep ontology", "how should I structure my Zep graphs", "why is Zep not returning the right context", "help me evaluate Zep", or "make my agent remember users".
---

# Building with Zep

This skill is the **decision-and-workflow layer** for building on Zep: how to
reason about Zep, scope graphs, ingest data, retrieve context, and evaluate
whether Zep delivers your use case. It is **not** an API reference or a full
best-practices manual — for exact, current details (method names, parameters,
limits, plan availability) and the complete best practices for any given
feature, query the **`zep-docs` MCP server** first — preferring to load the
whole relevant page (see
[Documentation index](#documentation-index) for how to read pages vs. search). If
it is unavailable, use [help.getzep.com](https://help.getzep.com) and the
[SDK/API reference](https://help.getzep.com/sdk-reference). If this skill and the
live docs ever disagree, **the live docs win** (see [Source authority](#source-authority-and-validation)).

Work backward from the **end use case and the business value** it must deliver.
Success is whether the agent receives **complete context** and produces
**accurate answers** for that use case — not whether the graph is perfect or the
ingested data is perfect.

> Guidance here targets **Zep V3** (SDK packages `zep-cloud` for
> Python/TypeScript, `github.com/getzep/zep-go/v3` for Go). Ignore the legacy V2
> `Memory` API. Zep is a paid product; some features are plan-gated — confirm
> availability in the docs.

## Conceptual overview

Zep builds **temporal Context Graphs**. *You* control the data that goes in and
the context retrieved out. A graph is a substrate that **fuses many data
sources** — conversations, emails, Slack, documents, transcripts, user
interactions, business data — into one time-aware picture, and Zep supports
**many graphs with governance** for enterprise scale (many graphs, sources,
agents, and humans, with security and control over creation, usage, and
retrieval).

The mental model: **Zep is not a chat-log store and not a vector database.** It
extracts structured, time-aware knowledge from whatever you feed it, fuses it
into the graph, and returns the slice that matters for the current moment. That
sets it apart from the tools you might otherwise reach for:

- **vs. a store per data type** — chat, documents, JSON, and business events all
  fuse into *one* graph per subject; you don't stand up and stitch together a
  separate store for each source.
- **vs. plain vector search** — retrieval is **hybrid** (semantic + keyword +
  graph traversal, then reranked), not similarity alone, so it captures exact
  terms and relationships, not just conceptual matches.
- **vs. static GraphRAG** — Zep is built for **change**: it ingests streaming,
  frequently-updated data incrementally and time-stamps every fact (see the
  bitemporal model below), whereas GraphRAG targets one-time summarization of
  static documents. Reach for Zep when knowledge evolves.

Two kinds of graph. A **user graph is a specialization of a standalone graph**:
anything a standalone graph can do, a user graph can do too. Only some features
are **user-graph-only** — flagged **(user graphs only)** below; everything else
applies to every graph.

- **Standalone graph** — the base graph type (`graph_id`), for shared or domain
  knowledge (knowledge bases, product/runbook data). See
  [Graph overview](https://help.getzep.com/graph-overview).
- **User graph** — a standalone graph **specialized for a user**: auto-created
  per user, the home of agent memory (an agent remembering prior conversations
  with that user). Adds user-only features — a **user node**, a **user
  summary**, and **threads** — and fuses all of that user's threads and business
  data. See [Users and user graphs](https://help.getzep.com/users-and-user-graphs).

An integration with Zep can use **one or both** kinds of graph. The developer
integrating Zep decides which graphs exist and what data feeds each. Access to
graphs is determined in the **application layer**: at retrieval time the
application decides which graph(s) a given request reads from — for example,
granting a user access to their own user graph *and* a companywide standalone
graph. See [Architecture patterns](https://help.getzep.com/architecture-patterns).

**How an episode is processed.** Each ingested artifact (an "episode") is
processed asynchronously: Zep extracts the **entities** mentioned, extracts the
**facts/relationships** between them, **deduplicates** the new entities and
facts against what is already in the graph, and **invalidates** any facts the
new data supersedes (e.g. a changed preference — the old fact is marked invalid
but kept as history).

**Bitemporal model.** Facts carry validity timestamps (valid/invalid/created/
expired), so you can ask what is true *now* or what was true at a past date.

**Context types** (what ingestion creates, what retrieval returns): episodes, entities, facts, thread
summaries, the user summary, and observations. Each captures a different value;
**auto** search finds the most relevant artifacts across all of them. A useful
contrast: **entity/node summaries** give *depth* (a narrative rolling up one
entity's history) while **facts** give *breadth* (granular, individually-dated
claims) — good retrieval draws on both. See
[Context types](https://help.getzep.com/context-types).

## Architectural philosophy and invariants

- **Test end to end for your use case.** Zep's many choices (scoping, ontology,
  retrieval) rarely have one "correct" setting; what matters is whether
  the whole pipeline delivers **complete context and accurate answers** for
  *your* use case, not whether any single part looks right in isolation. Tune and
  validate against an **end-to-end evaluation**, not a "perfect" graph — several
  trade-offs below (under-merging, recall over precision) only make sense through
  this lens. See [Evaluating Zep](#evaluating-zep).
- **Zep vs. your application.** Zep manages the graph (extraction, dedup,
  retrieval). The application controls **what data is sent**, **which graphs
  are retrieved from**, and **how the agent uses the context Zep returns**
  (prompt, model, logic). Retrieving from one vs. many graphs is an
  application-layer decision.
- **Zep does not infer beyond the data provided; it is only as good as the data
  it receives.** If context was never sent to Zep, Zep cannot surface it — a
  possible cause of "missing" context is that it was never ingested.
- **Deduplication philosophy — prefer under- to over-merging.** Wrongly merging
  two distinct entities is worse than failing to merge two that are the same,
  because unmerged duplicates are *both* still retrievable, so the agent still
  gets complete context. This is a concrete reason not to chase a "perfect"
  graph — what matters is complete retrieval when you **test end to end** for
  your use case.
- **Dedup needs context.** Threads automatically use prior messages as
  extraction context; `graph.add` does **not**. Episodes with pronouns or bare
  first names (e.g. multiple "John"s with no last name) deduplicate poorly —
  pre-process ambiguous data with stable identifiers (full names, IDs).
- **Retrieval philosophy — favor recall over precision.** Retrieve broadly and
  let the downstream LLM ignore what is irrelevant; missing relevant context is
  worse than including some extra. See [Retrieval philosophy](https://help.getzep.com/retrieval-philosophy).
- **Ingestion is asynchronous.** Added data is processed before it becomes
  retrievable (seconds or more). Design for eventual availability rather than
  reading back immediately; check status when it matters via
  [Check ingestion status](https://help.getzep.com/check-data-ingestion-status).

## Implementation: scope → ingest → retrieve

Implementing Zep has four steps — **scope → ingest → retrieve → evaluate**.
Evaluation has its own section below; the choices for the first three follow.
These are cross-cutting decisions; confirm exact signatures and limits in the
docs (see the [index](#documentation-index)) rather than guessing.

### 1. Scope your graphs and data sources

- Choose **user graphs** (per-user memory) vs. **standalone graphs** (shared or
  domain knowledge). Use **separate graphs wherever you need hard data
  separation** — per user, per team, per tenant.
- Decide which data sources feed which graphs. One graph can hold many sources;
  you can also have many graphs.
- **Zep threads apply only to user graphs.** A Zep thread represents a
  conversation between the user and the agent; it records that conversation
  history *and* ingests it into the user graph. Note the available episode/data
  types (message, text, JSON). Standalone graphs have no first-class thread
  support, but can still ingest arbitrary text — e.g. Slack or email threads —
  at a lower level via `graph.add` (as text/JSON, not as a thread).

### 2. Ingest data into graphs

- Use `thread.add_messages` for conversation; `graph.add` for documents, JSON,
  and business data.
- **Prepare the data.** Chunk large documents to fit the per-call size limit
  ([Chunking](https://help.getzep.com/chunking-large-documents)); follow
  [JSON best practices](https://help.getzep.com/adding-json-best-practices);
  pass real identifiers (full names, emails) so identity resolves and dedup
  works; attach [**episode metadata**](https://help.getzep.com/adding-business-data#episode-metadata)
  (such as `source`) at ingest to enable episode-metadata filtering when
  searching the graph.
- **Seed vs. stream.** Decide between backfilling initial/historical data (use
  [batch ingestion](https://help.getzep.com/adding-batch-data) for large
  volumes) and live/streaming updates.
- **Customize extraction (iterate, don't front-load).** Rule of thumb:
  **ontology defines the *shape* of the graph (which entity/edge types exist);
  instructions define *how to interpret* your domain** — don't conflate them.
  - [Custom ontology](https://help.getzep.com/customizing-graph-structure) —
    your entity/edge types. Model entity types as **nouns** and edge types as
    **verbs/relationships**, and start with a few generic types rather than
    modeling everything up front (custom attributes are advanced and often
    unnecessary). Sharpens extraction and enables type-filtered retrieval.
  - [Custom instructions](https://help.getzep.com/custom-instructions) —
    describe your domain (terminology, concepts) so Zep interprets data better
    on ingest. Not for defining types — that is the ontology's job.
  - [User summary instructions](https://help.getzep.com/user-summary-instructions)
    **(user graphs only)** — steer what the always-on user summary captures.

### 3. Provide retrieval to agents

- **Choose the context surface:**
  - *Default Context Block* **(user graphs only)** — `thread.get_user_context`;
    returns whole-user-graph context, relevance driven by the most recent thread
    messages. Best for most conversational agents.
  - *Context templates* **(user graphs only)** — automatic relevance, your fixed
    layout/sections. See [Context templates](https://help.getzep.com/context-templates).
  - *Advanced/manual construction* — run searches and assemble the string
    yourself; the **only** context surface for standalone graphs, and used for
    custom blocks on any graph. See
    [Advanced construction](https://help.getzep.com/advanced-context-block-construction).
- **Search** with `graph.search`: `scope="auto"` (recommended entry point for
  standalone/non-thread queries, spans all context types) or a specific scope
  (edges, nodes, episodes, observations, thread_summaries) with rerankers and
  **filters** (metadata, timestamp, entity/edge type, property). See
  [Searching the graph](https://help.getzep.com/searching-the-graph).
- **Decide how the agent retrieves:** expose search as a **tool call** (LLM
  decides when) vs. **deterministic/programmatic** retrieval on every turn.
- **Decide how many graphs** to read (one versus many, using parallel graph
  searches) — an application-layer decision. See
  [Architecture patterns](https://help.getzep.com/architecture-patterns).

## Evaluating Zep

- **Anchor to the end business task** and evaluate **end-to-end**, not each part
  in isolation. See [Evaluate Zep for your use case](https://help.getzep.com/evaluate-zep-for-your-use-case).
- **Two distinct measures:**
  - *Context completeness* — did Zep provide the context needed? (Zep's job.)
  - *Answer accuracy* — did your agent use that context to produce the correct
    result? (Your LLM/prompt's job, assuming context is complete.)
- **Diagnose with them:**
  - Completeness **high**, accuracy **low** → fix the **agent** (prompt, model,
    logic), not Zep.
  - Completeness **low** → accuracy will be low too. Localize the failure: is it
    ingestion or retrieval? First check whether the needed information is in the
    graph **at all** — [read/export the graph](https://help.getzep.com/reading-data-from-the-graph)
    (edges, entities, observations) and inspect the **episodes** (the raw data
    that was sent).
    - Not in the episodes → the data was **never sent** to Zep; fix what your
      application ingests. Not a Zep problem.
    - In the episodes but not in derived artifacts → tune **ingestion** (custom
      instructions, ontology, pre-processing the data).
    - In the derived artifacts but not in the retrieved context → tune
      **retrieval** (search scope, rerankers, filters, context assembly).
- This is why under-deduplication is acceptable (see philosophy above): an
  imperfect graph can still yield complete retrieval, which is what the
  end-to-end evaluation actually measures.
- Zep provides an **evaluation harness** to help measure context completeness
  and answer accuracy — but you supply a **gold dataset**: the kinds of
  questions you want your agent to answer, paired with the correct answers.
  See the full guidance in
  [Evaluate Zep for your use case](https://help.getzep.com/evaluate-zep-for-your-use-case).

## Documentation index

The `zep-docs` MCP server is the source for exact, current details and **best
practices** — query it before relying on memory, and **refer to the
documentation before implementing any feature**. The pages below are curated
entry points ("read X to do Y"), grouped into foundational concepts, pages that
apply to all graphs, and the smaller sets that are user-graph-only or
standalone-graph-only.

**How to retrieve — read a page (preferred), or search.** Prefer loading a whole
page over searching. The server exposes two mechanisms:

1. **Read a whole page (preferred).** Load a full doc page in one shot as an MCP
   **resource** — `zep-docs://<slug>`, where `<slug>` is the page's
   `help.getzep.com` path (everything after the domain, no leading slash). E.g.
   `https://help.getzep.com/searching-the-graph` →
   `zep-docs://searching-the-graph`; nested paths keep their slashes. Every page
   linked below is reachable at its `zep-docs://<slug>` resource by this rule;
   discover the full list with your client's resource-listing capability (both
   Claude Code and Codex expose MCP resources — Codex via `list_mcp_resources` /
   `read_mcp_resource`). **Prefer this whenever you implement, verify, or debug a
   specific feature** — you get the complete, current page, not fragments — and it
   needs only the MCP connection, so it works even when the agent has no general
   web access.
   - *Fallbacks* if the client can't read MCP resources or a resource errors:
     fetch the identical markdown at `https://help.getzep.com/<slug>.md` (the
     resource is just a cached proxy to that file), or the rendered page at
     `https://help.getzep.com/<slug>`. Both require web access to
     `help.getzep.com`.
2. **Search the docs (discovery).** Use the **`search_documentation`** tool
   (served by `zep-docs`; params: `query`, and optional `max_results`, 1–10,
   default 5) when you don't know where something is documented, whether it exists
   at all, or you want a broad look. It returns reranked text chunks with **no
   page URLs**, so use it to find *what* to read, then load that page in full via
   its `zep-docs://<slug>` resource.

**Foundational concepts**

| Read | To |
|------|----|
| [Key concepts](https://help.getzep.com/concepts) | Understand graphs, entities, facts, episodes |
| [Architecture patterns](https://help.getzep.com/architecture-patterns) | Scope graphs and choose one-vs-many-graph retrieval |
| [Context types](https://help.getzep.com/context-types) | Understand each context type and auto search |
| [Retrieval philosophy](https://help.getzep.com/retrieval-philosophy) | Understand recall-over-precision retrieval |

**All graphs**

| Read | To |
|------|----|
| [Adding context](https://help.getzep.com/adding-context) · [business data](https://help.getzep.com/adding-business-data) | Ingest documents/JSON/business data (`graph.add`) |
| [Batch ingestion](https://help.getzep.com/adding-batch-data) | Backfill large volumes |
| [JSON best practices](https://help.getzep.com/adding-json-best-practices) · [Chunking](https://help.getzep.com/chunking-large-documents) | Prepare data for ingestion |
| [Check ingestion status](https://help.getzep.com/check-data-ingestion-status) | Handle asynchronous processing |
| [Webhooks](https://help.getzep.com/webhooks) | Receive pushed events (episode processed, batch completed) instead of polling |
| [Customizing graph structure](https://help.getzep.com/customizing-graph-structure) | Define a custom ontology (entity/edge types) |
| [Custom instructions](https://help.getzep.com/custom-instructions) | Steer domain interpretation on ingest |
| [Searching the graph](https://help.getzep.com/searching-the-graph) | Scoped search, filters, rerankers |
| [Assembling context](https://help.getzep.com/assembling-context) · [Advanced construction](https://help.getzep.com/advanced-context-block-construction) | Build custom context blocks (the only context surface for standalone graphs) |
| [Manually updating the graph](https://help.getzep.com/adding-fact-triplets) | Add nodes/fact triplets and update existing edges, nodes, and facts by UUID |
| [Reading data](https://help.getzep.com/reading-data-from-the-graph) · [Deleting data](https://help.getzep.com/deleting-data-from-the-graph) | Inspect or remove graph data |
| [Cloning graphs](https://help.getzep.com/cloning-graphs) | Copy a graph (e.g. for testing) |
| [Evaluate Zep for your use case](https://help.getzep.com/evaluate-zep-for-your-use-case) | Benchmark completeness vs. accuracy |
| [Performance best practices](https://help.getzep.com/performance) | Reduce latency and optimize production performance (SDK client reuse, cache warming, concise search) |

**User graphs only**

| Read | To |
|------|----|
| [Quick start](https://help.getzep.com/quick-start-guide) | Stand up the basic loop (the user-graph quick start) |
| [Users and user graphs](https://help.getzep.com/users-and-user-graphs) | Create users and per-user memory |
| [Threads](https://help.getzep.com/threads) | Record conversations and ingest into the user graph |
| [Retrieving context](https://help.getzep.com/retrieving-context) | Get the default Context Block |
| [Context templates](https://help.getzep.com/context-templates) | Fixed custom layout with automatic relevance |
| [User summary](https://help.getzep.com/user-summary) · [summary instructions](https://help.getzep.com/user-summary-instructions) | Shape the always-on user baseline |
| [Add user business data](https://help.getzep.com/how-to-add-user-specific-business-data-to-user-graphs) | Add non-chat data to a user graph |

**Standalone graphs only**

| Read | To |
|------|----|
| [Give your agent domain knowledge](https://help.getzep.com/give-your-agent-domain-knowledge) | Stand up the basic loop (the standalone-graph quick start) |
| [Graph overview](https://help.getzep.com/graph-overview) · [Create graph](https://help.getzep.com/create-graph) | Create and manage standalone graphs |

**Reference and governance**

- SDK / API reference: <https://help.getzep.com/sdk-reference> — confirm exact
  signatures, parameters, and limits here or via the `zep-docs` MCP.
- Docs MCP server setup: <https://help.getzep.com/docs-mcp-server>.
- Governance (enterprise):
  - [Security & compliance](https://help.getzep.com/security-compliance) — the hub
    for Zep's security posture: SOC 2 Type II, HIPAA BAAs, access controls,
    audit/API logging, and BYOK/BYOC deployment options.
  - [RBAC](https://help.getzep.com/role-based-access-control) — role-based access
    control. Governs **human** teammates' access to the Zep dashboard via account-
    and project-scoped roles, so each person gets the right level of access.
  - [ABAC](https://help.getzep.com/attribute-based-access-control) — attribute-based
    access control. Scopes an individual **API key** to a subset of a project's
    actions and data (by action, and by data class via ingestion metadata) to
    enforce least privilege — useful when each agent authenticates with its own key.
  - [BYOK](https://help.getzep.com/bring-your-own-key) — bring your own key. Encrypt
    data at rest with your own AWS KMS key, keeping full control including revocation.

## Source authority and validation

- **Query the `zep-docs` MCP server first** for anything that must be exact or
  current — method names, parameters, limits, plan availability, newer features.
  Prefer loading the whole relevant page via its **`zep-docs://<slug>` resource**;
  use the **`search_documentation`** tool to find the right page or check whether
  something exists (see [Documentation index](#documentation-index) for both). It
  covers the guides and the SDK/API reference.
- **Fallback if resources or the MCP are unavailable:** fetch the page markdown at
  `https://help.getzep.com/<slug>.md`, else the guides at <https://help.getzep.com>
  and the SDK/API reference at <https://help.getzep.com/sdk-reference>.
- **The live docs win on conflict.** Treat this skill's summaries as stale if
  they disagree with current, version-matched documentation. Verify
  version-sensitive code against the SDK reference and, when available, the
  installed SDK's types/source.
- **Validate behavior, not just plausibility.** Don't stop at code that looks
  right — confirm ingestion completed, retrieval returns the expected context,
  and (per [Evaluating Zep](#evaluating-zep)) the end use case actually improves.
