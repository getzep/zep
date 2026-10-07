# Agent with Zep and Google ADK

This example uses Google ADK to run an agent with a Zep Context Graph. The
agent uses retrieval tools to answer questions about product quality,
regulatory status, suppliers, and ownership.

This example shares the Pemberline dataset, graph ID, gold questions, and
frontend with the [Pydantic AI example](../agent-with-zep). It does not copy the
dataset or change the existing example.

## Security boundary

The system prompt contains text that the application writes. It includes the
role, the ontology, the domain knowledge, and the tool rules. Retrieved graph
content is evidence, not instructions. The application sends the graph sample
in the first user message and sends tool results as tool output.

## Tools

| Tool | What it returns |
|---|---|
| `search_context` | A ranked sample of edges, nodes, or episodes |
| `list_nodes` | Every node of one entity type, up to a limit |
| `get_neighborhood` | The edges and neighbor nodes of one node |
| `get_details` | The attributes of a node or the full text of a report |
| `get_employees` | Employees on a team or product |
| `search_products` | Products and their regulatory filings |

The application sets the graph ID, reranker, and result limits. The model
selects queries, filters, and handles. Each result uses a short handle, such as
`n1`, `e1`, or `p1`, instead of a UUID.

## Requirements

- The example uses Python 3.10 or later and [uv](https://docs.astral.sh/uv/).
- The frontend uses Node.js 20.19 or later.
- The example uses a Zep API key and one model-provider API key.

## Set up

```bash
cd examples/python/agent-with-zep-adk
cp .env.example .env
uv sync
```

Set `ZEP_API_KEY` and the key for the selected model provider in `.env`.
`AGENT_MODEL` and `JUDGE_MODEL` use LiteLLM model strings in
`provider/model` format. Their default is `openai/gpt-6-luna`.

For a Gemini model, set `AGENT_MODEL=gemini-3.5-flash` and provide
`GOOGLE_API_KEY`. If your shell stores the key in `GEMINI_API_KEY`, set
`GOOGLE_API_KEY` to the same value for the command.

`MODEL_THINKING` sets the thinking level for the agent and the judge. The
default thinking level is `low`. Gemini supports `minimal`, `low`, `medium`,
and `high`. LiteLLM model strings can also use `xhigh`.

## Load the dataset

The default graph ID is `pemberline-demo`. The Pydantic AI example uses the
same graph. Skip ingestion if that example has already loaded the graph.

```bash
uv run python -m agent_with_zep_adk.ingest
uv run python -m agent_with_zep_adk.orientation
```

The `ingest` command creates the graph, sets the ontology, adds the shared
records and reports, and waits for processing. Use `--reset` to delete and
recreate the graph.

The `orientation` command reads the most connected nodes and writes a cache to
`.cache/` inside this example. Use `--refresh` to read the graph again.

## Ask a question from the command line

```bash
uv run python -m agent_with_zep_adk.cli "Which products are cleared for sale in the EU?"
uv run python -m agent_with_zep_adk.cli --no-planning "Which members of Reliability Engineering work on the Aster 410?"
```

The command prints the retrieval plan, tool calls, answer, and token use.

## Run the server and the frontend

Start the server in one terminal:

```bash
uv run uvicorn agent_with_zep_adk.server:app --port 8000
```

Start the shared frontend in a second terminal:

```bash
cd ../agent-with-zep/frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The frontend sends requests to the server on port
8000. The server returns Vercel AI SDK UI message stream version 1. The query
parameters `domain_knowledge` and `planning` turn those features on or off.
Both are on by default.

Each chat request uses a new in-memory ADK session. The server adds prior user
and assistant text turns to that session. The server drops prior tool-call and
tool-result parts. The model does not receive tool history from earlier
requests.

| Endpoint | Description |
|---|---|
| `POST /api/chat` | Runs the agent and streams the answer and tool events |
| `GET /api/orientation` | Returns the cached ontology and node sample |
| `GET /api/domain-knowledge` | Returns the domain-knowledge file |

## Run the evaluation

```bash
uv run python eval/run_eval.py --dry-run
uv run python eval/run_eval.py
uv run python eval/run_eval.py --configs D --questions q01 q05
```

The `--dry-run` option prints the run count and does not call a model. A full
evaluation calls the agent model and the judge model.

| Configuration | Tools | Graph orientation | Domain knowledge | Plan |
|---|---|---|---|---|
| A | `search_context` only | No | No | No |
| B | All six tools | Yes | No | No |
| C | All six tools | Yes | Yes | No |
| D | All six tools | Yes | Yes | Yes |

The judge grades context completeness, answer accuracy, and plan quality. The
script reads gold questions from `../agent-with-zep/eval/gold_questions.yaml`.
It writes results to `eval/results-*.jsonl`.

## Differences from the Pydantic AI version

| Area | Google ADK example |
|---|---|
| Planning gate | Callbacks filter declarations and reject unoffered calls |
| Retrieval guard | A new user message asks the agent to retrieve before it answers |
| Model strings | LiteLLM uses `provider/model`; Gemini uses a native model name |
| Server stream | An ADK event encoder sends Vercel AI SDK UI message chunks |
| Judge | An ADK agent validates `Grade` output and retries invalid JSON |

## Files

```text
agent_with_zep_adk/
  config.py       Settings, the dataset date, and the budgets
  ontology.py     Entity and edge types
  ingest.py       Creates the graph and adds the episodes
  orientation.py  Reads the graph orientation and caches it
  handles.py      Short handles and UUID deduplication
  tools.py        The six retrieval tools
  planner.py      The retrieval plan and tool gates
  prompts.py      The system prompt and judge prompt
  models.py       Resolves LiteLLM and Gemini models
  agent.py        Builds and runs the agent
  ui_stream.py    Encodes ADK events for the frontend
  server.py       FastAPI server
  cli.py          Command-line interface
data/             Shared with ../agent-with-zep/data
eval/
  run_eval.py     Runs the configurations and the judge
../agent-with-zep/eval/gold_questions.yaml  Shared gold questions
tests/            Offline tests with a fake Zep client
```

## Tests

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```
