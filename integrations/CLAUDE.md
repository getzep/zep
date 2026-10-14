# Claude's Guide to Building Zep Framework Integrations

Patterns and requirements for building and maintaining Zep integrations across
frameworks and languages. Read this before adding or changing an integration.

## Layout: platform-first

Integrations are organized **framework-first, then language**:

```
integrations/
  <framework>/
    <language>/        # python | typescript | go
  CLAUDE.md            # this file
  README.md            # integrations index
```

Current packages:

| Path | Distribution | Import / module |
|------|--------------|-----------------|
| `adk/python` | `zep-adk` (PyPI) | `zep_adk` |
| `adk/typescript` | `@getzep/zep-adk` (npm) | `@getzep/zep-adk` |
| `adk/go` | module-path tag | `github.com/getzep/zep/integrations/adk/go` |
| `ag2/python` | `zep-ag2` (PyPI) | `zep_ag2` |
| `autogen/python` | `zep-autogen` (PyPI) | `zep_autogen` |
| `crewai/python` | `zep-crewai` (PyPI) | `zep_crewai` |
| `langgraph/python` | `zep-langgraph` (PyPI) | `zep_langgraph` |
| `livekit/python` | `zep-livekit` (PyPI) | `zep_livekit` |
| `mastra/typescript` | `@getzep/zep-mastra` (npm) | `@getzep/zep-mastra` |
| `ms-agent-framework/python` | `zep-ms-agent-framework` (PyPI) | `zep_ms_agent_framework` |
| `pydantic-ai/python` | `zep-pydantic-ai` (PyPI) | `zep_pydantic_ai` |
| `strands/python` | `zep-strands` (PyPI) | `zep_strands` |
| `vercel-ai/typescript` | `@getzep/zep-vercel-ai` (npm) | `@getzep/zep-vercel-ai` |

**Naming convention (keep CI derivation simple):** the framework directory is the package
**key**. For Python, the import name is `zep_<key with hyphens→underscores>` and the PyPI
name is `zep-<key>` (e.g. `pydantic-ai` → import `zep_pydantic_ai`, dist `zep-pydantic-ai`).
The CI composite action derives the path (`integrations/<key>/python`) and import name from
the key, so a new Python package only needs a `paths-filter` entry.

## Per-package structure

### Python
```
integrations/<framework>/python/
├── src/zep_<framework>/
│   ├── __init__.py        # package entry point + __version__
│   ├── py.typed            # PEP 561 marker; required and included in wheel/sdist
│   ├── <core>.py          # core integration (memory/context/tools)
│   └── exceptions.py
├── tests/                 # mock-client tests (+ optional live tests gated on ZEP_API_KEY)
├── examples/              # runnable example(s)
├── pyproject.toml
├── README.md
├── SETUP.md               # Zep signup (getzep.com) + API key + install + run
├── CHANGELOG.md
└── Makefile
```

### TypeScript
```
integrations/<framework>/typescript/
├── src/                   # integration source
├── test/ or *.test.ts     # vitest tests
├── examples/
├── package.json           # scripts: build (tsup), lint, typecheck (tsc --noEmit), test (vitest)
├── tsconfig.json          # NodeNext + strict
├── README.md
├── SETUP.md
└── CHANGELOG.md
```

### Go
```
integrations/<framework>/go/
├── *.go                   # integration source
├── *_test.go
├── examples/
├── go.mod                 # module github.com/getzep/zep/integrations/<framework>/go
├── README.md
├── SETUP.md
└── CHANGELOG.md
```

Every integration ships: **README**, **SETUP.md** (including how to sign up for Zep at
https://www.getzep.com and get an API key), a **runnable example**, **tests**, and a
**CHANGELOG**.

## Zep SDKs — use V4

| Language | SDK | Version | Import |
|----------|-----|---------|--------|
| Python | `zep-cloud` | 4.0.0 | `from zep_cloud.client import Zep, AsyncZep` |
| TypeScript | `@getzep/zep-cloud` | 4.0.0 | `import { ZepClient } from "@getzep/zep-cloud"` |
| Go | `github.com/getzep/zep-go/v4` | v4.0.0 | `zepclient "github.com/getzep/zep-go/v4/client"` |

All integration packages use the Zep V4 SDK. The Go module path is `/v4`.

## Zep integration patterns

The common flow is **create a user → create a thread → add messages → retrieve context**.

- **Identity:** create the user and thread before the first turn. The server returns
  UUIDs for the user, graph, and thread. Store and reuse those UUIDs.
- **Persist conversation:** `thread.add_messages(thread_uuid, messages=[...])`. Pass
  `return_context=True` to combine persistence and context retrieval in one request.
- **Retrieve context:** `thread.get_context(thread_uuid)` returns a prompt-ready context
  block. The thread scopes relevance across the user's graph.
- **Ingest data:** use `graph.episode.add(...)` for text, messages, or JSON episodes.
  Keep each episode within the 10,000-character limit.
- **Targeted search:** use the V4 scope-specific methods `graph.search_edges`,
  `graph.search_nodes`, `graph.search_episodes`, `graph.search_observations`, and
  `graph.search_thread_summaries`. Use `graph.get_context` for the `auto` scope.
  Expose search as a tool when the model should choose when to retrieve data.
- **Ingestion is asynchronous** — a just-added fact is not instantly retrievable; design
  for eventual availability. No read-after-write within a turn.
- Reuse a single client instance. Log Zep errors; never crash the agent on a Zep failure.

Use the bundled **building-with-zep** skill and the **zep-docs MCP server** to confirm
exact SDK signatures before writing Zep code.

## Choosing the extension point

Implement the framework's *native* memory/context hook where one exists; otherwise
integrate idiomatically (tools + context injection). Confirm the hook against the
framework's own docs and the existing integration packages before building.

## Local development

### Python (per package)
```bash
make install      # uv sync --extra dev
make format       # uv run ruff format .
make lint         # uv run ruff check .
make type-check   # uv run mypy src/
make test         # uv run pytest tests/
make all          # format + lint + type-check + test
make build        # uv build
```
CI runs the equivalent steps directly (see `.github/actions/test-python`): `uv sync
--extra dev`, `ruff check .`, `ruff format --check .`, `mypy src/`, `pytest tests/ --cov`.

### TypeScript (per package)
```bash
npm ci && npm run lint && npm run typecheck && npm test
```

### Go (per package)
```bash
go vet ./... && go test ./... && golangci-lint run
```

## CI / release

- **Tests:** `.github/workflows/test-integrations.yml` triggers on `integrations/**` and
  runs three language lanes (`test-python`, `test-typescript`, `test-go`), each detecting
  changed packages via `dorny/paths-filter` and matrixing over them. Adding a Python
  package = add one `paths-filter` entry (`<key>: integrations/<key>/python/**`).
  Composite actions live in `.github/actions/test-{python,typescript,go}`.
- **Release:** `.github/workflows/release-integrations.yml`. Tag scheme encodes language:
  **`zep-<framework>-<language>-v<version>`** (e.g. `zep-adk-python-v0.2.0`,
  `zep-mastra-typescript-v0.1.0`). Python → PyPI, TypeScript → npm.
  **Go exception:** manually dispatch the workflow from `main` with the framework and
  version. After tests and release-environment approval, it creates the module-path tag and
  GitHub Release: `integrations/<framework>/go/vX.Y.Z`. Consumers install it via
  `go get github.com/getzep/zep/integrations/<framework>/go@vX.Y.Z`.

## Quality bar

Every integration must: pass ruff + mypy (Python) / tsc + lint (TS) / vet + lint (Go);
ship mock-based tests; include a runnable example; handle errors gracefully (never crash
the host agent); target the latest Zep SDK; and document all public APIs. Published Python
packages must ship an empty `py.typed` marker in both the wheel and source distribution.

## Testing pattern (Python, mock client)
```python
@pytest.mark.asyncio
async def test_persist_with_mock():
    client = MagicMock(spec=AsyncZep)
    client.thread = MagicMock()
    client.thread.add_messages = AsyncMock()
    # ... exercise the integration ...
    client.thread.add_messages.assert_called_once()
```
Don't close externally-provided clients (the caller owns the lifecycle). Guard optional
`thread_uuid`/`user_uuid` values against `None`.
