# agent-with-zep

A reference agent that uses a Zep context graph as its context layer. It shows
the pattern: learn the graph once, inject domain knowledge, plan, run targeted
retrieval tools, and evaluate.

The demo company is **Pemberline Medical**, a fictional manufacturer of infusion
pumps and patient monitors. All names, products, reports, and identifiers are
fictional.

What it shows:

- **Orientation**: the agent learns the graph schema (the ontology the
  application defines) once per graph and caches it. The system prompt carries
  the schema; a sample of the most connected nodes goes in the first user
  message as graph data.
- **Domain knowledge**: a file the application team writes. It says what
  matters (rank patient-safety facts first, what counts as verification). It is
  not retrieved from the graph.
- **Planning**: the model must call `submit_plan` before any retrieval tool is
  offered. It may submit one revision (2 plans total).
- **Retrieval tools**: six tools with clear contracts. The application pins the
  graph id, reranker, and limits; the model picks queries, filters, and
  handles. Results say "ranked sample", "complete", or "truncated at N", and
  items are referenced by short handles (n1, e1, p1) instead of UUIDs.
- **Budget**: 12 retrieval calls per run, 4,000 characters per result, and a
  duplicate-call notice.
- **Eval**: `eval/run_eval.py` runs 12 gold questions under four ablation
  configs (A–D) and grades each answer with a judge model.

## Setup

Requires Python >= 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
cd examples/python/agent-with-zep
cp .env.example .env   # set ZEP_API_KEY and your model provider key
uv sync
```

Model providers are selected with Pydantic AI model strings in `AGENT_MODEL`
and `JUDGE_MODEL` (default `openai:gpt-5-mini`). Set the matching provider key
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, ...).

## Ingest the dataset

```bash
uv run python -m agent_with_zep.ingest          # add records + reports
uv run python -m agent_with_zep.ingest --reset  # delete and recreate the graph
uv run python -m agent_with_zep.orientation     # build the orientation cache
```

Ingest is idempotent: it creates the graph if missing and adds the episodes;
`--reset` starts clean. Run it once per `GRAPH_ID`.

## Run the CLI

```bash
uv run python -m agent_with_zep.cli "Which products are cleared for sale in the EU?"
uv run python -m agent_with_zep.cli --no-planning "Which members of Reliability Engineering work on the Aster 410?"
```

The CLI prints the plan, each tool call with its arguments and latency, the
answer, and token usage.

## Run the server + frontend

```bash
uv run uvicorn agent_with_zep.server:app --reload --port 8000
cd frontend && npm install && npm run dev   # Vite dev server on :5173
```

Endpoints:

- `POST /api/chat` — Vercel AI SDK UI message request in, streamed UI message
  stream out (data stream protocol, **sdk_version=7**). Consume it with
  `ai@7` / `@ai-sdk/react@4` `useChat`. Tool calls (including `submit_plan`)
  appear as `tool-*` stream parts with the tool name and input/output; the
  final answer is the text part.
- Per-request toggles are **query parameters** (the Vercel request body has a
  fixed message shape): `/api/chat?domain_knowledge=false&planning=false`.
  Both default to `true`.
- `GET /api/orientation` — the cached ontology text and node sample (for the
  UI's orientation panel).
- `GET /api/domain-knowledge` — the domain knowledge file.

## Run the eval

```bash
uv run python eval/run_eval.py --dry-run                 # print the run count
uv run python eval/run_eval.py --configs D               # one config
uv run python eval/run_eval.py --questions q01 q05 --repeats 2
```

Configs: **A** naive tools only (search_context), no orientation, no domain
knowledge, no plan; **B** full tools + orientation; **C** B + domain
knowledge; **D** C + planning. Each result is graded with the judge prompt in
`agent_with_zep/prompts.py` and a structured `Grade` output. The script writes
`eval/results-*.jsonl` and prints a summary table (context completeness,
accuracy, plan quality, tool-selection rate, tool calls, latency, tokens).

## File map

```
agent_with_zep/
  config.py       env settings, AS_OF_DATE, budgets
  ontology.py     entity/edge types + prompt rendering
  ingest.py       CLI: create graph, set ontology, add episodes, poll
  orientation.py  learn once per graph; .cache/orientation-{graph_id}.json
  handles.py      n*/e*/p* handle registry (UUID dedup, "seen" marking)
  tools.py        the six retrieval tools + shared deps
  planner.py      RetrievalPlan schema, submit_plan, tool gating
  prompts.py      verbatim prompt constants + render helpers
  agent.py        build_agent(AgentConfig), run_agent
  server.py       FastAPI + Vercel AI adapter (sdk_version=7)
  cli.py          python -m agent_with_zep.cli "question"
data/
  domain_knowledge.md   application-authored ranking rules
  records/*.json        one file per record kind (facts.md tables)
  reports/R01..R20.md   text episodes with YAML front matter
eval/
  gold_questions.yaml   12 graded questions
  run_eval.py           ablation runner + judge
tests/                  fake Zep client, no network
```

## Tests

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```
