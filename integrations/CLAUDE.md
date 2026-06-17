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
  SPIKE_FINDINGS.md    # verified extension points + per-integration approach
```

Current packages:

| Path | Distribution | Import / module |
|------|--------------|-----------------|
| `adk/python` | `zep-adk` (PyPI) | `zep_adk` |
| `autogen/python` | `zep-autogen` (PyPI) | `zep_autogen` |
| `crewai/python` | `zep-crewai` (PyPI) | `zep_crewai` |
| `livekit/python` | `zep-livekit` (PyPI) | `zep_livekit` |

Planned (see `SPIKE_FINDINGS.md` for verified hooks): `microsoft-agent-framework/python`,
`pydantic-ai/python`, `langgraph/python`, `mastra/typescript`, `adk/go`, `adk/typescript`.

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

## Zep SDKs — always target the latest

| Language | SDK | Latest (2026-06) | Import |
|----------|-----|------------------|--------|
| Python | `zep-cloud` | 3.23.0 | `from zep_cloud.client import Zep, AsyncZep` |
| TypeScript | `@getzep/zep-cloud` | 3.23.0 | `import { ZepClient } from "@getzep/zep-cloud"` |
| Go | `github.com/getzep/zep-go/v3` | v3.23.0 | `zepclient "github.com/getzep/zep-go/v3/client"` |

> Zep V3 only. The npm `preview` dist-tag points to an *older* 2.0.0-rc.2 — use `latest`.
> The Go module path is `/v3` (`/v2` is superseded).

## Zep integration patterns

The Zep loop is the same everywhere: **create user → create thread → add messages →
retrieve context**.

- **Persist conversation:** `thread.add_messages(thread_id, messages=[...])`. Pass
  `return_context=True` to fold persist + retrieval into one round-trip (returns `.context`).
- **Retrieve a Context Block:** `thread.get_user_context(thread_id)` — returns a
  prompt-ready string assembled from the *whole user graph* (the thread just scopes
  relevance). This is the default for conversational agents.
- **Ingest business data / documents / JSON:** `graph.add(...)` (≤10,000 chars/call — chunk).
- **Targeted search:** `graph.search(...)` with a `scope` (`edges`, `nodes`, `episodes`,
  `auto`, …) — expose as a tool when the model should decide when to search.
- **Identity:** always pass real user names (ideally last name + email) so the graph
  resolves identity. Create the user/thread out-of-band before the first turn.
- **Ingestion is asynchronous** — a just-added fact is not instantly retrievable; design
  for eventual availability. No read-after-write within a turn.
- Reuse a single client instance. Log Zep errors; never crash the agent on a Zep failure.

Use the bundled **building-with-zep** skill and the **zep-docs MCP server** to confirm
exact SDK signatures before writing Zep code.

## Choosing the extension point

Implement the framework's *native* memory/context hook where one exists; otherwise
integrate idiomatically (tools + context injection). The verified per-framework hooks,
corrections, and code sketches are in **`SPIKE_FINDINGS.md`** — consult it before building.

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
  **Go exception:** Go modules are versioned by a tag matching the module subpath —
  `integrations/<framework>/go/vX.Y.Z` — consumed via
  `go get github.com/getzep/zep/integrations/<framework>/go@vX.Y.Z`.

## Quality bar

Every integration must: pass ruff + mypy (Python) / tsc + lint (TS) / vet + lint (Go);
ship mock-based tests; include a runnable example; handle errors gracefully (never crash
the host agent); target the latest Zep SDK; and document all public APIs.

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
`thread_id`/`user_id` against `None`.
