# Zep examples

Runnable examples for the public Zep Cloud SDKs under `examples/python`, `examples/typescript`, and `examples/go`.

| Language | SDK |
| --- | --- |
| Python | [`zep-cloud`](https://pypi.org/project/zep-cloud/) v3 |
| TypeScript | [`@getzep/zep-cloud`](https://www.npmjs.com/package/@getzep/zep-cloud) v3 |
| Go | [`github.com/getzep/zep-go/v3`](https://pkg.go.dev/github.com/getzep/zep-go/v3) |

Imports use published packages only (no local `../../src` SDK checkout).

## Toolchain

| Tool | Requirement |
| --- | --- |
| Python | 3.10+ (3.12 common in CI/dev images) |
| Node.js | 18+ for most TypeScript examples |
| Node.js | **24+ for `typescript/eve`** |
| Package managers | `pip` / `uv`, `npm`, **`yarn` for `typescript/zep-graph-visualization`** |
| Go | 1.22+ |

## Regression runner

Lightweight gate: [`run_checks.py`](./run_checks.py) (kept under `examples/`, not `.github`).

```bash
# From repo root — default STATIC mode (keyless). Does not mutate live Zep state.
python3 examples/run_checks.py
python3 examples/run_checks.py --mode static

# Print the plan without executing
python3 examples/run_checks.py --dry-run-plan

# Live smokes only when matching API keys are present
python3 examples/run_checks.py --live
python3 examples/run_checks.py --mode live --run-prefix "exrun-$(whoami)-1"

# Limit to inventory group id(s)
python3 examples/run_checks.py --group typescript-eve
```

Inventory / credential-gating tests:

```bash
python3 -m pytest examples/tests/test_run_checks.py -q
```

| Mode | Behavior | Mutates Zep? |
| --- | --- | --- |
| `static` (default) | compile / typecheck / unit tests / `--help` | **No** |
| `live` | static + explicit smokes gated on env keys | Only for unlocked live checks |

Live checks receive a unique `exrun-…` prefix via argv where supported and via:
`ZEP_EXAMPLE_RUN_PREFIX`, `ZEP_EXAMPLE_USER_PREFIX`, `ZEP_EXAMPLE_THREAD_PREFIX`.

Interactive servers and notebooks are **not** started by the default static gate
(`streamlit`, `eve dev`, `next`/`yarn` dev servers, Jupyter, etc.).

## API keys

| Key | Used by | Live-covered by runner? |
| --- | --- | --- |
| `ZEP_API_KEY` | Nearly every example | Yes (with `--live`) |
| `OPENAI_API_KEY` | Chunking (all langs), Streamlit apps, openai-agents-sdk, langgraph | Yes for selected smokes |
| `ANTHROPIC_API_KEY` | `python/claude-prompt-caching-example` | Chat/benchmark manual; ingest may run with Zep only |
| `GOOGLE_API_KEY` | `typescript/eve` Gemini agent UI | Agent UI manual; `npm run smoke` is Zep-only |
| `TAVILY_API_KEY` | `typescript/langgraph` web search | **No — not live-covered** |
| ElevenLabs agent / API keys | `python/elevenlabs-zep-example` | **No — not live-covered** |

## Inventory

Commands are relative to each group directory. Prefer the matching `.env.example` when present.

### Python (`examples/python`)

#### `python` — Python root scripts (simple / advanced / user_example)

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `python simple.py` · `python advanced.py` · `python user_example.py` |
| **Static test** | `python -m compileall -q .` · `python -m pytest tests/test_v3_regression.py -q` |
| **Keys** | `ZEP_API_KEY` |
| **Notes** | Live scripts create users/threads; runner injects ZEP_EXAMPLE_RUN_PREFIX. |

#### `python/graph_example` — Python graph_example

| | |
| --- | --- |
| **Install** | `pip install -r ../requirements.txt` |
| **Run** | `python graph_example.py` · `python user_graph_example.py` · `python entity_types.py` · `python tickets_example.py` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY` |

#### `python/chat_history` — Python chat_history

| | |
| --- | --- |
| **Install** | `pip install -r ../requirements.txt` |
| **Run** | `python memory.py` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY` |

#### `python/chunking-example` — Python chunking-example

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `python chunk_and_ingest.py sample_document.txt --user-id <id>` · `python chunk_and_ingest.py sample_document.txt --user-id <id> --dry-run` |
| **Static test** | `python chunk_and_ingest.py --help` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | OPENAI_API_KEY is required even for --dry-run (contextualization). |

#### `python/claude-prompt-caching-example` — Python claude-prompt-caching-example

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `python ingest.py` · `python chat.py` |
| **Static test** | `python test_structure.py` |
| **Keys** | `ZEP_API_KEY`, `ANTHROPIC_API_KEY` |
| **Notes** | Full chat/benchmark needs ANTHROPIC_API_KEY; default live smoke is ingest only. |

#### `python/openai-agents-sdk` — Python openai-agents-sdk

| | |
| --- | --- |
| **Install** | `uv sync  # or: pip install -e .` |
| **Run** | `python openai_agents_sdk_example.py` · `python openai_agents_sdk_example.py --interactive` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | Install deps (`uv sync` / `pip install -e .`) before live runs. Avoid --interactive in automation. |

#### `python/agent-memory-full-example` — Python agent-memory-full-example (Streamlit)

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `streamlit run ui.py` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | Interactive Streamlit UI excluded from default static/live runner. |

#### `python/context-templates-example` — Python context-templates-example (Streamlit)

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `python set-context-templates.py` · `python zep_ingest.py` · `streamlit run ui.py` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | streamlit run ui.py is interactive — not in the default gate. |

#### `python/user-summary-instructions-example` — Python user-summary-instructions-example (Streamlit)

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `python zep_ingest.py` · `streamlit run ui.py` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | streamlit run ui.py is interactive — not in the default gate. |

#### `python/zep-quickstart-dashboard` — Python zep-quickstart-dashboard (Streamlit)

| | |
| --- | --- |
| **Install** | `pip install -r requirements.txt` |
| **Run** | `python zep_ingest.py` · `streamlit run ui.py` |
| **Static test** | `python -m compileall -q .` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | streamlit run ui.py is interactive — not in the default gate. |

#### `python/elevenlabs-zep-example` — Python elevenlabs-zep-example (NOT live-covered)

| | |
| --- | --- |
| **Install** | `pip install -r llm-proxy/requirements.txt` · `cd react-app && npm install` |
| **Run** | `cd llm-proxy && python proxy_server.py` · `cd react-app && npm run dev` |
| **Static test** | `python -m py_compile llm-proxy/proxy_server.py` · `python -m py_compile llm-proxy/setup_test_user.py` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Live coverage** | **Not live-covered** |
| **Notes** | ElevenLabs agent keys are separate and not live-covered by this runner. |

### TypeScript (`examples/typescript`)

#### `typescript` — TypeScript graph / memory / users snippets

| | |
| --- | --- |
| **Install** | `npm install` |
| **Run** | `npm run example:graph` · `npm run example:graph:user` · `npm run example:graph:entity-types` · `npm run example:memory` · `npm run example:users` |
| **Static test** | `npm test` |
| **Keys** | `ZEP_API_KEY` |
| **Notes** | Packaged apps (eve, langgraph, chunking-example, zep-graph-visualization) are separate groups. |

#### `typescript/chunking-example` — TypeScript chunking-example

| | |
| --- | --- |
| **Install** | `npm install` |
| **Run** | `npx tsx src/index.ts sample_document.txt --user-id <id>` · `npx tsx src/index.ts sample_document.txt --user-id <id> --dry-run` |
| **Static test** | `npm test` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | OPENAI_API_KEY required for --dry-run contextualization. |

#### `typescript/langgraph` — TypeScript langgraph CLI agent

| | |
| --- | --- |
| **Install** | `npm install` |
| **Run** | `npm start -- --userId <id> --threadId <id>` |
| **Static test** | `npm test` · `npx tsx agent.ts --help` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |
| **Notes** | TAVILY_API_KEY is optional for web search and is NOT live-covered. `npm start` is an interactive readline agent — run manually with unique --userId/--threadId prefixes; not auto-started by this runner. |

#### `typescript/eve` — TypeScript eve (Node 24+)

| | |
| --- | --- |
| **Install** | `npm install  # requires Node.js 24+` |
| **Run** | `npm run smoke` · `npm run seed:company` · `npm run dev` |
| **Static test** | `npm run typecheck` |
| **Keys** | `ZEP_API_KEY`, `GOOGLE_API_KEY` |
| **Notes** | Requires Node.js 24+. `npm run smoke` is Zep-only. `npm run dev` is an interactive Eve server — not in the default gate. GOOGLE_API_KEY is required for the Gemini-backed agent UI. |

#### `typescript/zep-graph-visualization` — TypeScript zep-graph-visualization

| | |
| --- | --- |
| **Install** | `yarn install` |
| **Run** | `yarn dev` |
| **Static test** | `yarn build` |
| **Keys** | `ZEP_API_KEY` |
| **Notes** | yarn dev / yarn start are long-lived servers — excluded from default gate. |

### Go (`examples/go`)

#### `go` — Go graph / ontology snippets

| | |
| --- | --- |
| **Install** | `go mod download` |
| **Run** | `go run .` · `go run . entity-types` |
| **Static test** | `go test ./...` |
| **Keys** | `ZEP_API_KEY` |
| **Notes** | entity-types replaces project-level ontology — use a disposable project key. |

#### `go/chunking-example` — Go chunking-example

| | |
| --- | --- |
| **Install** | `go mod download` |
| **Run** | `go run . sample_document.txt --user-id <id>` · `go run . sample_document.txt --user-id <id> --dry-run` |
| **Static test** | `go test ./...` |
| **Keys** | `ZEP_API_KEY`, `OPENAI_API_KEY` |

### Python notebooks (manual; excluded from default static gate)

| Path | Notes |
| --- | --- |
| `python/quickstart/quickstart.ipynb` | Stale-API guards in `python/tests/test_v3_regression.py`; do not auto-exec in the gate |
| `python/autogen-agent/agent.ipynb` | Same |
| `python/langgraph-agent/agent.ipynb` | Same |

## Out of scope for this runner

- `integrations/`, `ingestion/`, `benchmarks/`, `legacy/`
- Rewriting Streamlit UIs
- Full Claude prompt-caching benchmarks
- Live ElevenLabs or Tavily paths
