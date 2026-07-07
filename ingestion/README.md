# zep-ingest

Bulk data ingestion pipeline for [Zep](https://www.getzep.com). Owns everything
upstream of the Zep API so getting unstructured **and** structured data into
Context Graphs correctly is a one-liner — and getting it *incorrectly* is caught
before it corrupts a graph.

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
| Slack export (conversations) | `ingest_slack_export` | `message`, thread-grouped; join/leave noise skipped (tune with `skip_subtypes=`) |
| Documents (text/Markdown, long PDFs pre-converted to text) | `ingest_documents` | `text`, chunked (+optional LLM context) |
| Email exports (.eml files) | `ingest_emails` | `message`, dated by the `Date:` header |
| A user's own chat history (app conversations) | `ingest_thread_messages` | thread messages on the **user graph** |
| Records (CSV / JSONL / JSON array; CRM rows, catalogs) | `ingest_json_records` | `json`, normalized per Zep's JSON guidance |
| Known, exact relationships (org chart, entity seeding) | `ingest_fact_triples` | fact triples via `graph.add_fact_triple` |
| Anything else | implement a `Loader`, use `ingest(...)` | you decide |

**Extraction vs explicit facts:** narrative content (conversations, documents)
goes through Zep's LLM extraction as episodes. When you already know the exact
relationship ("Ana is RESPONSIBLE for GTM analytics"), assert it as a fact
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
everything in this package works on every plan.

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
        AliasCanonicalizer({"Atlas": ["MR-42"]}),
        TextChunker(chunk_size=500, overlap=50),
        LLMContextualizer(AnthropicLLM()),
    ],
)
report = pipeline.preview()      # NO API calls: inspect episodes + warnings first
result = pipeline.run(client, graph_id="company_kb", wait=True)
```

`preview()` shows exactly what would be ingested — including every warning
(missing timestamps, oversize splits, runaway alias rewrites, metadata
truncation) — before a single API call.

## Temporal correctness (the silent backfill killer)

If an episode has no `created_at`, Zep silently uses the **ingestion time** to
date extracted facts. Zep resolves contradictions by "latest `valid_at` wins" —
so a timestamp-less backfill doesn't just lose history, it makes fact
invalidation pick wrong survivors, permanently.

Every loader in this package stamps `created_at` from source data (Slack `ts`,
a record date field, file mtime), and the pipeline **warns about every episode
missing one** in `preview()` and `result.warnings`. For document corpora with
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

OpenAILLM()                                       # OpenAI (pip install "zep-ingest[openai]")
AnthropicLLM()                                    # Anthropic (pip install "zep-ingest[anthropic]")
# The universal connector — any OpenAI-compatible /chat/completions endpoint:
OpenAICompatibleLLM(model="llama3.1:70b",         # LiteLLM, Ollama, vLLM,
                    base_url="http://localhost:11434/v1",  # OpenRouter, Together, ...
                    api_key="ollama")
```

`OpenAICompatibleLLM` is the docs-recommended route to "any provider":
LiteLLM alone proxies 100+ models behind this interface, with no extra
dependency here beyond the `openai` package.

## Entity canonicalization

Zep merges entities by the names it sees in text; semantic aliases ("MR-42" vs
"Atlas") stay separate nodes. The supported fix is canonicalizing
**before ingestion**:

```python
AliasCanonicalizer({"Atlas": ["MR-42", "Picker X1"]})         # rewrite
AliasCanonicalizer({"Atlas": ["MR-42"]}, mode="annotate")     # "MR-42 (also known as Atlas)"
```

Ambiguous aliases are a data-corruption hazard (alias `"Will"` must not rewrite
"he will go"). Pass `risky_words=DEFAULT_RISKY_WORDS` (an exported starter set
of common words — extend it with `| {"your", "words"}`) and construction
rejects aliases that match it or are shorter than 3 characters; omit it and no
guard runs. Matching is
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

**Custom types are additive to Zep's defaults.** Every graph already carries
the default ontology (User, Assistant, Preference, Location, Event, Object,
Topic, Organization, Document + LOCATED_AT/OCCURRED_AT edges); your declared
types are prioritized on top of it, and a same-name declaration overrides how
that type classifies. Don't re-declare a default unless you're deliberately
overriding it, and avoid the reserved field names (`uuid`, `name`, `graph_id`,
`name_embedding`, `summary`, `created_at`). Defaults can only be disabled on
user graphs (`user.add(disable_default_ontology=True)`).

**Don't start from a blank page:**
[`examples/example_ontology.py`](examples/example_ontology.py) ships a starter
ontology (Person / Organization / Project / Product / Location +
RESPONSIBLE / WORKS_AT / SUPPLIES / CUSTOMER_OF / LOCATED_AT) built with
those levers —
every example passes it via `ontology=`. Copy the file and adapt the types to
your domain. The examples themselves are self-contained and re-runnable: each
creates a fresh graph and ingests bundled sample data with zero arguments —
see [`examples/README.md`](examples/README.md).

## Seeding a graph from scratch

The full lifecycle, in the order that works
(see [`examples/fact_triples_example.py`](examples/fact_triples_example.py)
for a named graph and
[`examples/user_graph_example.py`](examples/user_graph_example.py) for a user
graph):

1. Create the graph (`create_if_missing=True` does it for you).
2. Set the ontology (`ontology=` preflight).
3. Optionally seed canonical entities as fact triples — extraction dedups
   against the existing graph, so seeded entities anchor resolution.
4. Ingest the corpus with real `created_at` timestamps and alias
   canonicalization; `wait=True`.

## Fact triples

```python
from zep_ingest import FactTriple, ingest_fact_triples

ingest_fact_triples(client, [
    FactTriple(
        fact="Ana Azimova is responsible for GTM analytics",
        fact_name="RESPONSIBLE",
        source_node_name="Ana Azimova",
        source_node_labels=["Person"],       # ties the node to a declared type
        target_node_name="GTM analytics",
        target_node_labels=["Project"],
        valid_at="2024-06-15T00:00:00Z",
    ),
], graph_id="org")
```

Triples skip extraction entirely, so nodes they create are **untyped unless
you label them** — pass `source_node_labels`/`target_node_labels` (one
declared entity type each) or the declared ontology never touches a
triples-only graph.

Every documented limit (fact ≤250 chars, names ≤50, summaries ≤500,
SCREAMING_SNAKE_CASE `fact_name`, scalar attributes, ≤10 metadata keys) is
validated **client-side at construction** — a clear Python error naming the
field, not an HTTP 400 three hours into a run. Also accepts a JSON-array,
JSONL, or CSV path whose columns match the field names. Sequential only (the
Batch API doesn't take triples).

## Monitoring a run

```python
result = ingest_slack_export(client, "export.zip", graph_id="g1")
result.status          # queued | processing | succeeded | partial | failed
result.wait(timeout=3600)
result.failed_items()  # Batch API item records, or AddErrors on the sequential path
result.warnings        # everything the pipeline noticed
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
and the run continues. `batch_ids` / `episode_uuids` are the resume handles.

**Checking status later** — if you skipped `wait=True`, persist
`result.batch_ids` and reconstruct in another process:

```python
from zep_ingest import IngestResult

result = IngestResult.from_batch_ids(client, ["batch-id-1"])
result.status          # refreshed on demand
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
- Errors and warnings never include episode bodies.

## What this package does NOT fix

Retrieval-side behavior is out of scope: survivor selection under contradiction
(strictly latest-`valid_at`), confidence/authority weighting, and as-of search.
The ingestion-side mitigations — correct `created_at`, canonical names, and
`source`/`confidence` episode `metadata` you can filter on at search time — are
all supported here.

## Extending

```python
class MyLoader:                       # any source
    def load(self) -> Iterator[Episode]: ...

class MyTransform:                    # any prep step; optional .warnings list
    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]: ...

class MyLLM:                          # any LLM provider
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
[`SETUP.md`](SETUP.md) for account setup.
