# zep-ingest

Bulk data ingestion pipeline for [Zep](https://www.getzep.com). Owns everything
upstream of the Zep API so getting unstructured **and** structured data into
Context Graphs safely is a one-liner, with validation and preview warnings for
the failure modes the package can detect before submission.

```bash
pip install zep-ingest
# optional LLM contextualization:
pip install "zep-ingest[anthropic]"   # or [openai]
```

```python
from example_ontology import ONTOLOGY  # copy examples/example_ontology.py, adapt it
from zep_cloud.client import Zep
from zep_ingest import ingest_slack_export, ingest_documents, ingest_json_records

client = Zep(api_key="...")

# Setup is yours, once per graph: zep-ingest writes only into graphs that
# already exist and already carry their ontology (see Ontology below). The
# ontology is not retroactive, so set it before the first ingest.
for graph_id in ("team_knowledge", "company_kb", "catalog"):
    client.graph.create(graph_id=graph_id)
    client.graph.set_ontology(
        entities=ONTOLOGY["entities"], edges=ONTOLOGY["edges"], graph_ids=[graph_id]
    )

ingest_slack_export(client, "slack-export.zip", graph_id="team_knowledge")
ingest_documents(client, "handbook/**/*.md", graph_id="company_kb")
ingest_json_records(client, "products.csv", graph_id="catalog", id_field="sku")
```

## Why this package exists

The raw ingestion surface has sharp edges people hit constantly: a 10,000-character
episode limit, DIY chunking, timestamps that silently default to ingestion time
(corrupting fact timelines), entity aliases that never merge, a separate Batch
API for bulk throughput, and rate limits. `zep-ingest` encodes all of those
rules so you don't have to know them.

Graph *setup* stays outside the package: create the graph and set its ontology
yourself, once, then ingest into it. See
[Ontology](#ontology-set-it-before-you-ingest).

## Choosing your path

| Your data | Use | Episodes |
|---|---|---|
| Slack export (conversations) | `ingest_slack_export` | `text`, thread-grouped with speaker/channel labels inline; public channels only unless `conversation_types=` widens it; join/leave noise skipped (tune with `skip_subtypes=`) |
| Documents (text/Markdown, long PDFs pre-converted to text) | `ingest_documents` | `text`, chunked (+optional LLM context) |
| Speaker-labeled or WebVTT transcripts | `ingest_transcripts` | `text`, chunked at turn boundaries with speaker labels inline and source offsets |
| Email exports (.eml files) | `ingest_emails` | `text`, with sender/recipient/subject inline, dated by the `Date:` header |
| A user's own chat history (app conversations) | `ingest_thread_messages` | thread messages on the **user graph** |
| Records (CSV / JSONL / JSON array; CRM rows, catalogs) | `ingest_json_records` | one `json` episode per record, as you provide it (shaping is yours) |
| Known, exact relationships (org chart, entity seeding) | `ingest_fact_triples` | fact triples via `graph.add_fact_triple` |
| Anything else | implement a `Loader`, use `ingest(...)` | you decide |

**Extraction vs explicit facts:** narrative content (conversations, documents)
goes through Zep's LLM extraction as episodes. When you already know the exact
relationship ("Avery Brown is RESPONSIBLE for OPERATIONS-DASHBOARD"), assert it as a fact
triple instead — no extraction variance, and pre-seeded canonical entities
anchor later extraction.

**User graph vs named graph:** pass exactly one of `user_id=` (memory about one
user) or `graph_id=` (a shared/business graph). Passing both or neither is a
`ConfigurationError` before any API call. For a user's own conversations,
use `ingest_thread_messages` — messages land on the user graph via threads
(the same store `thread.get_user_context()` reads). Every message requires
`thread_id`, `role`, `name`, `content`, and `created_at` (only `metadata` is
optional). The user must already exist; missing threads are created for you
(they are backfill-owned), messages over the 4,096-character thread-message
limit are split at sentence boundaries, and per-thread order is preserved.
`method="auto"` uses the Batch API and transparently falls back to sequential
`thread.add_messages` only when the deployment doesn't serve the batch endpoint
at all. Thread ids are global to a project — pass `thread_id_suffix=` to
namespace a backfill without rewriting the source data.
Pass `ignore_roles=["assistant"]` to keep assistant turns as conversational
context but exclude them from graph extraction (they stay in thread history);
it applies on both the batch and sequential paths.

**Slack conversation types:** a Slack export indexes its conversations in four
files — `channels.json` (public channels), `groups.json` (private channels),
`dms.json` (1:1 DMs) and `mpims.json` (group DMs). All four are read, but only
**public channels are ingested by default**, so private conversations never
start flowing into a graph unnoticed. Whatever the export contains and
`conversation_types=` does not select is reported in `result.warnings` with
counts and the exact parameter to pass — never skipped in silence. Any subset
works:

```python
# Public channels only — the default, e.g. a company-wide standalone graph:
ingest_slack_export(client, "export.zip", graph_id="company_wide")

# DMs only, into their own graph:
ingest_slack_export(
    client,
    "export.zip",
    graph_id="dm_history",
    conversation_types=["dm", "group_dm"],
)
```

`channels=` composes with it: it filters *by name within* the selected types,
matching a channel name, an `mpdm-…` slug, or a DM id (`D01ABC234`) or its
resolved member label. Naming a conversation whose type isn't selected is a
`ConfigurationError` that says which type to add, rather than a silently empty
run. DMs and group DMs are labeled by their resolved members ("Avery Brown,
Blake Carter") instead of the opaque id or slug Slack names their folder with —
raw ids degrade entity extraction — and every episode carries its
`conversation_type` in `metadata` for filtering at search time.

**Slack names:** messages store author *ids*, so every speaker, `@mention`, and
DM label is resolved through the export's `users.json` roster, preferring
`profile.real_name` over `profile.display_name` (then the username, then the raw
id). Slack's own precedence is the opposite, but it optimizes for how a name
reads in a chat client: a display name is often a short handle ("morgan") that
Zep cannot merge with the same person written in full ("Morgan Lee") in an email
or document, which silently splits one person into two nodes. Authors whose
roster entry has no `real_name` are counted in `result.warnings`. When your
roster is thin, `formatter=` receives each `SlackMessage` — including its raw
`user_id` — so you can substitute names from your own directory:

```python
ingest_slack_export(
    client,
    "export.zip",
    graph_id="team_knowledge",
    formatter=lambda m: f"{DIRECTORY.get(m.user_id, m.sender)}: {m.text}",
)
```

**Batch vs sequential:** the Batch API (fast, 50k items/batch) is the default
high-throughput submission path. `method="auto"` tries batch and transparently
falls back to sequential `graph.add` calls with rate-limit-aware pacing in
exactly one case: the deployment has no batch endpoint to call (HTTP 404 — an
older server, a self-hosted or Community deployment, or a base URL that doesn't
route `/batches`). Authorization and quota errors are raised as errors instead
of quietly downgrading the run. Every source here ingests fine without the
Batch API — pass `method="sequential"` to take that path deliberately.

## The pipeline

```
Loader  →  Transforms (chunk / contextualize / canonicalize)  →  LimitGuard  →  Submitter
```

Each stage is a small protocol (`Loader`, `Transform`, `Submitter`,
`LLMClient`) — a new source, prep step, submission path, or LLM provider is one
small class. Everything is lazy generators: a 500k-message export never sits in
memory.

```python
from zep_ingest import Pipeline, TextFileLoader, TextChunker, LLMContextualizer, AliasCanonicalizer
from zep_ingest.llm.anthropic import AnthropicLLM

pipeline = Pipeline(
    TextFileLoader("handbook/**/*.md"),
    transforms=[
        AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]}),
        TextChunker(chunk_size=500, overlap=50),
        LLMContextualizer(AnthropicLLM()),
    ],
)
report = pipeline.preview()  # NO Zep API calls: inspect episodes + warnings first
result = pipeline.run(client, graph_id="company_kb")
result.wait(timeout=3600)  # submission returns immediately; blocking is opt-in
```

`preview()` shows the transformed episodes and validation warnings (including
missing timestamps, oversize splits, and alias rewrites) before a Zep API call.
It lazily inspects at most 10 transformed episodes by default and clearly marks
its warning counts as sample-scoped. Use `preview(limit=None)` when you need an
exhaustive preflight of the entire stream.

## Temporal correctness (the silent backfill killer)

If an episode has no `created_at`, Zep silently uses the **ingestion time** to
date extracted facts. Zep resolves contradictions by "latest `valid_at` wins" —
so a timestamp-less backfill doesn't just lose history, it makes fact
invalidation pick wrong survivors, permanently.

Loaders preserve source timestamps when available (for example Slack `ts` or
a configured record date field). `result.warnings` counts every episode missing
one because `run()` validates the entire stream before submission. The default
`preview()` reports on its 10-episode sample; use `preview(limit=None)` to count
every missing timestamp before ingestion. Filesystem mtime requires explicit
`use_file_mtime=True`. For document corpora with
publication dates, the contextualizer's default prompt also asks the LLM to
include the date in each chunk's context.

## Chunking

The 10k-character limit is handled twice over:

- `TextChunker` implements Zep's own cookbook: paragraph-boundary splitting
  with sentence fallback, 500-char chunks, 50-char overlap. Smaller chunks
  yield richer graphs — 500 is the documented optimum, 10k is only the hard cap.
- `LimitGuard` is always appended to every pipeline as a safety net: nothing
  ever reaches the API oversized, split boundary-aware per data type (lines
  for `message`, paragraphs for `text`, top-level structure for `json`).
- `LLMContextualizer` adds contextual retrieval (the technique the docs
  recommend): an LLM situates each chunk within its source document before
  ingestion. An LLM failure never aborts a run — the raw chunk is kept and a
  warning recorded.

### Bring any LLM

The contextualizer talks to LLMs through a one-method protocol — **anything
with `complete(prompt: str) -> str` works**, so you can inject your own client
for any provider, proxy, or local model:

```python
class MyLLM:
    def complete(self, prompt: str) -> str: ...


ingest_documents(client, "docs/**/*.md", graph_id="kb", llm=MyLLM())
```

Three adapters ship as conveniences (mirroring the pattern Graphiti — Zep's
own engine — uses for its LLM clients):

```python
from zep_ingest.llm.openai import OpenAILLM, OpenAICompatibleLLM
from zep_ingest.llm.anthropic import AnthropicLLM

OpenAILLM()  # OpenAI (pip install "zep-ingest[openai]")
AnthropicLLM()  # Anthropic (pip install "zep-ingest[anthropic]")
# The universal connector — any OpenAI-compatible /chat/completions endpoint:
OpenAICompatibleLLM(
    model="llama3.1:70b",  # LiteLLM, Ollama, vLLM,
    base_url="http://localhost:11434/v1",  # OpenRouter, Together, ...
    api_key="ollama",
)
```

`OpenAICompatibleLLM` is the docs-recommended route to "any provider":
LiteLLM alone proxies 100+ models behind this interface, with no extra
dependency here beyond the `openai` package.

## Entity canonicalization

Zep merges entities by the names it sees in text; semantic aliases ("PROTOTYPE-202" vs
"ROBOT-202") stay separate nodes. The supported fix is canonicalizing
**before ingestion**:

```python
AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202", "Picker X1"]})  # rewrite
AliasCanonicalizer(
    {"ROBOT-202": ["PROTOTYPE-202"]}, mode="annotate"
)  # "PROTOTYPE-202 (also known as ROBOT-202)"
```

Ambiguous aliases are a data-corruption hazard (alias `"Will"` must not rewrite
"he will go"). The exported `DEFAULT_RISKY_WORDS` guard is enabled by default
and rejects risky or very short aliases; extend it with `| {"your", "words"}`
or pass an empty set to opt out explicitly. Matching is
case-sensitive and word-boundary by default, URLs/code spans are never
touched, the transform is idempotent, and per-alias replacement counts surface
as warnings so you see "will → Will Hughes: 4,213 replacements" in `preview()`
— not after your graph is poisoned.

## Structured data (JSON): shape it before you ingest

Zep extracts best from JSON that is small, flat, self-contained, and about one
entity, and poorly from large, deeply nested, or multi-entity records.
`ingest_json_records` ingests each record **as you provide it** — one `json`
episode per record — so shaping the data for good extraction is your
responsibility (see the JSON best-practices in the Zep docs). `LimitGuard` still
guarantees no episode exceeds the API size limit — it top-level-splits an
oversize record as a safety net, with a warning — but it does not restructure
your data.

`JsonRecordsLoader` (and the `ingest_json_records` one-liner) helps you attach
meaning to opaque records: map your own fields onto the canonical
`id`/`name`/`description` identity keys, lift a timestamp field into
`created_at`, tag records with a `record_type`, and promote fields to episode
`metadata`.

Every record episode is stamped with `source_type` and `file_name` provenance,
which spends 2 of the API's 10 metadata keys — so `metadata_fields` may name at
most **8** fields of your own, and an over-budget list is rejected up front
(a `ConfigurationError` before any file is read, not an error mid-run). Naming
`source_type` or `file_name` there does not override the provenance: those
fields are skipped with a warning, and the record still reaches the graph whole
— that field included — in the episode body.

## Ontology: set it before you ingest

Two facts drive everything here:

1. **The ontology is not retroactive.** Data ingested before `set_ontology` is
   never re-typed; the only fix is re-ingesting. So set it on the graph *once*,
   before your first ingest run:

   ```python
   from zep_cloud import EntityEdgeSourceTarget

   client.graph.set_ontology(
       entities={"Person": Person, "Organization": Organization},
       edges={
           "WORKS_AT": (
               WorksAt,
               [EntityEdgeSourceTarget(source="Person", target="Organization")],
           ),
       },
       graph_ids=["org"],  # or user_ids=[...]; omit both to apply project-wide
   )
   ```

   The ontology is a property of the **graph**, not of an ingest run, so
   `zep-ingest` has no `ontology=` parameter — running several one-liners
   against one graph would otherwise re-declare it per call, and
   `set_ontology` *replaces* the whole ontology for its scope, so the last
   call would silently win. Configure the graph first; then ingest into it.
2. **The ontology guides classification; it is not enforced.** Extraction
   reuses a declared type when it confidently matches and derives a new name
   otherwise. Your levers: richer type **descriptions** (enumerate synonym
   verbs), wider source→target **signatures**, and graph **custom
   instructions**. Custom entity and edge types are capped per scope (10 of each
   and 10 fields per type at time of writing, but the cap depends on your plan —
   check the [Zep docs](https://help.getzep.com/customizing-graph-structure) or
   your project's limits).

Typing quality is therefore an iteration loop: sample-ingest, inspect the node
labels and edge type names in the Zep dashboard (a long tail of derived types
like OWNS/LEADS or untyped entities tells you which description or signature
to widen), refine, and re-ingest into a fresh graph. Start simple (few generic
types), add precision incrementally.

**Default types are user-graph-only.** Zep's default ontology (User, Assistant,
Preference, Location, Event, Object, Topic, Organization, Document +
LOCATED_AT/OCCURRED_AT edges) is applied **only to user graphs** — named
(standalone) graphs carry **no** default types. The rule therefore differs by
graph kind:

- **Named graphs** (the destination for most one-liners here): nothing is typed
  unless you declare it. Declare every entity and edge type you rely on —
  *including* ones that reuse a default's name like Location or Organization — or
  those entities stay untyped and any custom edge whose signature needs them
  never applies. This is exactly why
  [`examples/example_ontology.py`](https://github.com/getzep/zep/blob/main/ingestion/examples/example_ontology.py)
  declares Location and LOCATED_AT itself.
- **User graphs:** your custom types are *additive* to the defaults; a same-name
  declaration overrides how that type classifies, and the defaults can be turned
  off with `user.add(disable_default_ontology=True)`.

Either way, avoid the reserved field names (`uuid`, `name`, `graph_id`,
`name_embedding`, `summary`, `created_at`).

**Don't start from a blank page:**
[`examples/example_ontology.py`](https://github.com/getzep/zep/blob/main/ingestion/examples/example_ontology.py) ships a starter
ontology (Person / Organization / Project / Product / Location +
RESPONSIBLE / WORKS_AT / SUPPLIES / SELLS / CUSTOMER_OF / LOCATED_AT) built with
those levers — every example applies it with `client.graph.set_ontology(...)`
before ingesting. Copy the file and adapt the types to your domain. The examples
themselves are self-contained and re-runnable: each creates a fresh graph, sets
that ontology, and ingests bundled sample data with zero arguments —
see [`examples/README.md`](https://github.com/getzep/zep/blob/main/ingestion/examples/README.md).

## Seeding a graph from scratch

The full lifecycle, in the order that works
(see [`examples/fact_triples_example.py`](https://github.com/getzep/zep/blob/main/ingestion/examples/fact_triples_example.py)
for a named graph and
[`examples/user_graph_example.py`](https://github.com/getzep/zep/blob/main/ingestion/examples/user_graph_example.py) for a user
graph):

1. Create the graph: `client.graph.create(graph_id=...)` (or `client.user.add(...)`
   for a user graph). The ingestion package writes only into existing graphs —
   it never creates them.
2. Set the ontology before any data flows: `client.graph.set_ontology(...)`,
   scoped with `graph_ids=`/`user_ids=` (or project-wide by omitting both). It is
   not retroactive, and the ingestion package never sets it for you.
3. Optionally connect fact triples to existing canonical entities by pinning
   endpoints with `source_node_uuid`/`target_node_uuid`. Extraction dedups
   against the existing graph, so known entities anchor resolution.
4. Ingest the corpus with real `created_at` timestamps and alias
   canonicalization, then block on the bound result with `result.wait(...)`.

## Fact triples

```python
from zep_ingest import FactTriple, ingest_fact_triples

ingest_fact_triples(
    client,
    [
        FactTriple(
            fact="Ana Azimova is responsible for GTM analytics",
            fact_name="RESPONSIBLE",
            source_node_name="Ana Azimova",
            source_node_labels=["Person"],  # ties the node to a declared type
            target_node_name="GTM analytics",
            target_node_labels=["Project"],
            valid_at="2024-06-15T00:00:00Z",
        ),
    ],
    graph_id="org",
)
```

Triples skip extraction entirely, so nodes they create are **untyped unless
you label them** — pass `source_node_labels`/`target_node_labels` (one
declared entity type each) or the declared ontology never touches a
triples-only graph.

Every documented limit (fact ≤250 chars, names ≤50, summaries ≤500,
SCREAMING_SNAKE_CASE `fact_name`, string attribute and metadata keys whose
values are scalars or arrays of scalars, ≤10 metadata keys) is
validated **client-side at construction** — a clear Python error naming the
field, not an HTTP 400 three hours into a run. Also accepts a JSONL or
JSON object or array path whose columns match the field names. Sequential only (the
Batch API doesn't take triples).

## Direct node seeding

When you have canonical entities to create up front — before any extraction, and
without relationships — `ingest_nodes` adds them directly via
`client.graph.add_nodes`:

```python
from zep_ingest import NodeItem, ingest_nodes

ingest_nodes(
    client,
    [
        NodeItem(name="Ana Azimova", label="Person", uuid="…"),
        NodeItem(name="GTM analytics", label="Project", uuid="…"),
    ],
    graph_id="org",
)
```

Pass a persisted UUIDv4 per node (required by default): it is the node's only
identity/dedup key, so a re-run upserts instead of duplicating. Up to 100 nodes
per request, every documented limit (name ≤50, summary ≤500, label ≤100, ≤10
attributes, ≤10 metadata keys, each value a scalar or an array of scalars)
validated client-side at construction.
Sequential only (the Batch API doesn't take direct nodes).

## Monitoring a run

```python
result = ingest_slack_export(client, "export.zip", graph_id="g1")
result.status  # queued | processing | untracked | succeeded | partial | failed | canceled
result.wait(timeout=3600)
result.failed_items()  # Batch API item records and/or submission AddErrors
result.warnings  # everything the pipeline noticed
result.raise_for_status()  # opt-in strictness
```

Every ingest call returns as soon as the data is submitted; `result.wait()` is
the only thing that blocks. Bind the result first, as above — don't chain
`ingest_slack_export(...).wait()`. The chain works (`wait()` returns `self`),
but when it raises — a timeout, or a submission the API left untracked —
nothing was ever bound, and `batch_ids` / `task_ids` are the only handles for
resuming or diagnosing that run.

Ingestion is asynchronous — a just-added fact is not instantly retrievable,
even after `wait()`: search indexing lands a few seconds after processing.
`search_when_ready` owns that gap so scripts don't hand-roll poll loops:

```python
from zep_ingest import search_when_ready

response = search_when_ready(client, "who runs the pilot?", graph_id="g1")
```

Partial failures never crash a run: pages/episodes that keep failing are
recorded as `AddError`s (indices and API messages only — never episode content)
and the run continues. `batch_ids` / `episode_uuids` / `task_ids` are the
resume handles. Task IDs are used by asynchronous operations such as fact
triples, direct node creation, and sequential thread submissions, and `wait()`
polls them through `client.task`.

If the API accepts a task-backed submission without returning a completion
handle, the result reports `status == "untracked"` instead of claiming success.
`wait()` raises `IngestUntrackedError` immediately in that state; use
`search_when_ready` or an application-specific read to verify availability.

**Checking status later** — if you never call `wait()`, persist
`result.batch_ids` and reconstruct in another process:

```python
from zep_ingest import IngestResult

result = IngestResult.from_batch_ids(client, ["batch-id-1"])
result.refresh()  # status reads cached state, so fetch it first
result.status
result.wait()  # or skip refresh() entirely — wait() polls for you

# Task-backed ingestion can be resumed the same way:
result = IngestResult.from_task_ids(client, ["task-id-1"])
result.wait()
```

## Security notes

- `LLMContextualizer` sends document content to **your** LLM. The prompt marks
  the content as data-not-instructions, strips the tag vocabulary from inputs
  so hostile text can't break the prompt structure, and length-caps + sanitizes
  the LLM's output — but if you ingest untrusted content, remember that graph
  content ultimately reaches your agents' prompts; sanitize upstream where that
  matters.
- Alias maps are validated (no control characters, sane lengths) since they
  often come from config files.
- Stored `AddError` records and package-generated warnings never add content
  from the episodes you submitted. They do carry the API's own response body
  verbatim, so a failure reads exactly as it would from a direct SDK call —
  worth knowing if you ship `failed_items()` output to a log aggregator.

## What this package does NOT fix

Retrieval-side behavior is out of scope: survivor selection under contradiction
(strictly latest-`valid_at`), confidence/authority weighting, and as-of search.
The ingestion-side mitigations — correct `created_at`, canonical names, and
`source_type` episode `metadata` you can filter on at search time — are all
supported here.

## Extending

```python
class MyLoader:  # any source
    def load(self) -> Iterator[Episode]: ...


class MyTransform:  # any prep step; optional .warnings list
    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]: ...


class MyLLM:  # any LLM provider
    def complete(self, prompt: str) -> str: ...
```

Planned as future work: PDF/Drive loaders, LLM date extraction for undated corpora, idempotency/resume manifests (re-running
the same export today duplicates episodes), per-item destination routing, an
async API, and a CLI.

## Development

```bash
make install   # uv sync --extra dev
make all       # format + lint + type-check + test
```

Live integration tests run only when `ZEP_API_KEY` is set. See
[`SETUP.md`](https://github.com/getzep/zep/blob/main/ingestion/SETUP.md) for account setup.
