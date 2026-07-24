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
from zep_cloud.client import Zep
from zep_ingest import ingest_slack_export, ingest_documents, ingest_json_records

client = Zep(api_key="...")

ingest_slack_export(client, "slack-export.zip", graph_id="team_knowledge")
ingest_documents(client, "handbook/**/*.md", graph_id="company_kb")
ingest_json_records(client, "products.csv", graph_id="catalog", id_field="sku")
```

## Why this package exists

The raw ingestion surface has sharp edges people hit constantly: a 10,000-character
episode limit, DIY chunking, timestamps that silently default to ingestion time
(corrupting fact timelines), entity aliases that never merge, elaborate manual
JSON-shaping rules, an enterprise-only Batch API, rate limits, and an ontology
that only applies to data ingested *after* it is set. `zep-ingest` encodes all of
those rules so you don't have to know them.

## Choosing your path

| Your data | Use | Episodes |
|---|---|---|
| Slack export (conversations) | `ingest_slack_export` | `text`, thread-grouped with speaker/channel labels inline; join/leave noise skipped (tune with `skip_subtypes=`) |
| Documents (text/Markdown, long PDFs pre-converted to text) | `ingest_documents` | `text`, chunked (+optional LLM context) |
| Speaker-labeled or WebVTT transcripts | `ingest_transcripts` | `text`, chunked at turn boundaries with speaker labels inline and source offsets |
| Email exports (.eml files) | `ingest_emails` | `text`, with sender/recipient/subject inline, dated by the `Date:` header |
| A user's own chat history (app conversations) | `ingest_thread_messages` | thread messages on the **user graph** |
| Records (CSV / JSONL / JSON array; CRM rows, catalogs) | `ingest_json_records` | `json`, normalized per Zep's JSON guidance |
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
(the same store `thread.get_user_context()` reads), the user and missing
threads are created automatically, messages over the 4,096-character
thread-message limit are split at sentence boundaries, and per-thread order
is preserved on both the Batch API (`thread_message` items) and the
sequential `thread.add_messages` fallback. When messages carry `created_at`
timestamps, `method="auto"` submits sequentially: the Batch API currently
ignores `created_at` on `thread_message` items, which would silently date a
backfill at ingestion time. Thread ids are global to a project — pass
`thread_id_suffix=` to namespace a backfill without rewriting the source data.

**Batch vs sequential:** the Batch API (fast, 50k items/batch) is
enterprise-only. The default `method="auto"` tries batch and transparently
falls back to sequential `graph.add` calls with rate-limit-aware pacing —
the episode ingestion paths also work on plans without Batch API access.

## The pipeline

```
Loader  →  Transforms (chunk / contextualize / canonicalize / normalize)  →  LimitGuard  →  Submitter
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
report = pipeline.preview()  # NO API calls: inspect episodes + warnings first
result = pipeline.run(client, graph_id="company_kb", wait=True)
```

`preview()` shows the transformed episodes and validation warnings (including
missing timestamps, oversize splits, and alias rewrites) before an API call.

## Temporal correctness (the silent backfill killer)

If an episode has no `created_at`, Zep silently uses the **ingestion time** to
date extracted facts. Zep resolves contradictions by "latest `valid_at` wins" —
so a timestamp-less backfill doesn't just lose history, it makes fact
invalidation pick wrong survivors, permanently.

Loaders preserve source timestamps when available (for example Slack `ts` or
a configured record date field), and the pipeline **warns about every episode
missing one** in `preview()` and `result.warnings`. Filesystem mtime requires
explicit `use_file_mtime=True`. For document corpora with
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

## Structured data (JSON) shaping

Zep extracts poorly from large, nested, or multi-entity JSON. The docs give a
manual shaping algorithm; `JsonNormalizer` automates it: flatten nesting deeper
than 3–4 levels (preserving key-path context), explode long lists into
per-element episodes with a contextualizing `item_type` field, extract long
string values as separate text episodes, and split wide objects into ≤6-property
pieces that each duplicate the `id`/`name`/`description` identity fields.
`JsonRecordsLoader` maps your columns onto those identity fields and parses a
date column into `created_at`.

## Ontology: set it before you ingest

Two facts drive everything here:

1. **The ontology is not retroactive.** Data ingested before `set_ontology` is
   never re-typed; the only fix is re-ingesting. Pass `ontology=` to
   `Pipeline.run` / any one-liner and it is applied to the destination graph
   *before* any data flows — the ordering mistake becomes impossible.
2. **The ontology guides classification; it is not enforced.** Extraction
   reuses a declared type when it confidently matches and derives a new name
   otherwise. Your levers: richer type **descriptions** (enumerate synonym
   verbs), wider source→target **signatures**, and graph **custom
   instructions**. Limits: 10 entity types + 10 edge types, 10 fields each.

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
RESPONSIBLE / WORKS_AT / SUPPLIES / CUSTOMER_OF / LOCATED_AT) built with
those levers —
every example passes it via `ontology=`. Copy the file and adapt the types to
your domain. The examples themselves are self-contained and re-runnable: each
creates a fresh graph and ingests bundled sample data with zero arguments —
see [`examples/README.md`](https://github.com/getzep/zep/blob/main/ingestion/examples/README.md).

## Seeding a graph from scratch

The full lifecycle, in the order that works
(see [`examples/fact_triples_example.py`](https://github.com/getzep/zep/blob/main/ingestion/examples/fact_triples_example.py)
for a named graph and
[`examples/user_graph_example.py`](https://github.com/getzep/zep/blob/main/ingestion/examples/user_graph_example.py) for a user
graph):

1. Create the graph (`create_if_missing=True` does it for you).
2. Set the ontology (`ontology=` preflight).
3. Optionally connect fact triples to existing canonical entities by pinning
   endpoints with `source_node_uuid`/`target_node_uuid`. Extraction dedups
   against the existing graph, so known entities anchor resolution.
4. Ingest the corpus with real `created_at` timestamps and alias
   canonicalization; `wait=True`.

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
SCREAMING_SNAKE_CASE `fact_name`, scalar attributes, ≤10 metadata keys) is
validated **client-side at construction** — a clear Python error naming the
field, not an HTTP 400 three hours into a run. Also accepts a JSONL or
JSON-array path whose columns match the field names. Sequential only (the
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
scalar attributes, ≤10 metadata keys) validated client-side at construction.
Sequential only (the Batch API doesn't take direct nodes).

## Monitoring a run

```python
result = ingest_slack_export(client, "export.zip", graph_id="g1")
result.status  # queued | processing | succeeded | partial | failed
result.wait(timeout=3600)
result.failed_items()  # Batch API item records, or AddErrors on the sequential path
result.warnings  # everything the pipeline noticed
result.raise_for_status()  # opt-in strictness
```

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
triples and direct node creation, and `wait()` polls them through `client.task`.

**Checking status later** — if you skipped `wait=True`, persist
`result.batch_ids` and reconstruct in another process:

```python
from zep_ingest import IngestResult

result = IngestResult.from_batch_ids(client, ["batch-id-1"])
result.status  # refreshed on demand
result.wait()

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
- Stored `AddError` records and package-generated warnings omit episode bodies
  and raw API response bodies.

## What this package does NOT fix

Retrieval-side behavior is out of scope: survivor selection under contradiction
(strictly latest-`valid_at`), confidence/authority weighting, and as-of search.
The ingestion-side mitigations — correct `created_at`, canonical names, and
`source`/`confidence` episode `metadata` you can filter on at search time — are
all supported here.

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
