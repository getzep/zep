# Zep Agent-Framework Integrations — Research Spike Findings

**Date:** 2026-06-15 · **Status:** complete · **Method:** every extension point was
verified by reading the **installed package source / type definitions** (not docs or
memory) in isolated `/tmp/zep-spike/<fw>` scratch dirs, and each minimal integration
sketch was proven to import / type-check / compile. File paths in the "Confirmed hook"
columns cite the installed source the signature was read from.

> Zep SDK note: all three V3 SDKs are at **3.23.0** (latest, confirmed 2026-06-15) —
> Python `zep-cloud`, TypeScript `@getzep/zep-cloud`, Go `github.com/getzep/zep-go/v3`
> (the `/v3` module path; `/v2` tops out at v2.22.0 and is superseded). The npm
> `preview` dist-tag points to an *older* `2.0.0-rc.2`, not a newer release — use `latest`.
> All integrations must pin and target these.

## Go / No-Go summary

| Framework | Lang | Package (verified) | Extension point | Verdict |
|---|---|---|---|---|
| Microsoft Agent Framework | Python | `agent-framework-core` 1.8.1 (stable) | `ContextProvider.before_run/after_run` | **GO** |
| Pydantic AI | Python | `pydantic-ai` 1.107.0 | `capabilities=[ProcessHistory(fn)]` + `@agent.tool` | **GO** |
| LangGraph | Python | `langgraph` 1.2.5 | node/tool helpers (primary) + hybrid `BaseStore` | **GO** |
| Mastra | TypeScript | `@mastra/core` 1.42.0 | custom tools via `createTool` | **GO** |
| Google ADK | Go | `google.golang.org/adk` v1.4.0 | `BeforeModelCallback` + `memory.Service` | **GO** |
| Google ADK | TypeScript | `@google/adk` 1.2.0 | `beforeModelCallback` (primary) / `BaseTool.processLlmRequest` | **CONDITIONAL GO** |
| CrewAI (improve existing) | Python | `crewai` | `Storage` / `ExternalMemory` (already shipped) | **GO** (refresh) |

The two **CONDITIONAL GO**s are both green-to-build — the condition is scoping, not
feasibility (see each section). Sketches live under `/tmp/zep-spike/<fw>/` (throwaway;
no spike code ships — build PRs write fresh production code from these confirmed APIs).

---

## 1. Microsoft Agent Framework (Python) — **GO**

- **Install:** `pip install agent-framework-core` — **1.8.1, stable** (installs cleanly,
  pulls stable `pydantic 2.13.4`; **no `--prerelease` flag needed**). Both `agent-framework`
  and `agent-framework-core` have a stable 1.x line (1.2.0 → 1.8.1; the `1.0.0rcN`
  pre-releases predate it). Depend on **`agent-framework-core`** (the lightweight core).
  NOTE: the earlier spike claim that "the umbrella has no stable release" was a
  **misdiagnosis** — installing the full `agent-framework` *with all extras* fails without
  `--prerelease=allow`, but only because some optional extras (Azure AI Search, etc.) have
  pre-release *dependencies*; the framework itself is stable.
- **Confirmed hook** (`agent_framework/_sessions.py`): subclass `ContextProvider`
  (`__init__(self, source_id: str)`, `:362`) and override the two **keyword-only async**
  hooks (`:370-410`):
  - `async def before_run(self, *, agent, session, context: SessionContext, state: dict) -> None`
  - `async def after_run(self, *, agent, session, context: SessionContext, state: dict) -> None`
  - `invoking`/`invoked`/`thread_created` do **not** exist in this version (grep: no matches).
  - Read input: `context.input_messages: list[Message]`, `Message.text` (`_types.py:1755`).
    Read response (in `after_run`): `context.response.messages`.
  - Inject by **mutating** `context`: `extend_instructions(source_id, ...)` (`:253`),
    `extend_messages(...)` (`:220`), `extend_tools(...)` (`:264)` — **not** by returning a `Context`.
  - **Attach:** `context_providers: Sequence[ContextProvider]` kwarg on the public
    `Agent` class (exported from `agent_framework`).
- **Corrections to prior plan:** public agent class is **`Agent`, not `ChatAgent`** (no
  `ChatAgent` symbol). Hooks are keyword-only with `agent/session/state` args (richer than
  assumed); injected object is `SessionContext`.
- **Reference impl:** `agent_framework_mem0.Mem0ContextProvider` overrides exactly
  `before_run`/`after_run` — validates the approach.
- **Sketch:** `imports-ok` (`ZepContextProvider.__mro__ = [ZepContextProvider, ContextProvider, object]`).
- **Build approach:** `ZepContextProvider(thread_id, user_id, AsyncZep(...))`; `before_run`
  persists the latest user turn via `thread.add_messages(..., return_context=True)` and
  injects the returned `.context` via `extend_instructions`; `after_run` persists
  `context.response.messages`. Async-only — require `AsyncZep`, reuse one client.
- **Risks:** symbols (`ContextProvider`/`SessionContext`/`SupportsAgentRun`) come from
  private `_sessions`/`_agents` modules (public re-exports exist) — internals may shift
  across minors, so pin a compatible range. Depend on `agent-framework-core`, **not** the
  `[all]` umbrella, to avoid dragging in pre-release extra deps. No `thread_created` hook →
  create user/thread out-of-band before first run; Zep async ingestion (returned context
  predates the just-added message); Zep limits (≤4096 chars/msg, ≤30 msgs/call).

## 2. Pydantic AI (Python) — **GO**

- **Install:** `pip install pydantic-ai` — **1.107.0**.
- **Confirmed hook:** current API is **`capabilities=[ProcessHistory(fn)]`** (from
  `pydantic_ai.capabilities`). `history_processors=` **is deprecated** in 1.107.0 —
  swallowed by `**_deprecated_kwargs` (`agent/__init__.py:343`), auto-remapped to
  `ProcessHistory` (`_utils.py:923-955`) with a "removed in v2.0" warning. Build on
  `capabilities=[ProcessHistory(...)]`; `Hooks(before_model_request=fn)` is the lower-level fallback.
  - Processor signature (`_history_processor.py:11-26`): receives `list[ModelMessage]`,
    **optional `RunContext` as first arg**, **sync or async**.
  - `@agent.tool` (RunContext first) / `@agent.tool_plain`; `deps_type=` +
    `RunContext.deps` + `deps=`/`message_history=` on `run`/`run_sync`;
    `result.new_messages()` / `result.all_messages()`.
- **Corrections to prior plan:** none — prior claim exact. (Use `capabilities=`, not the
  deprecated `history_processors=` kwarg.)
- **Sketch:** `imports-ok` + agent constructs with `capabilities=[ProcessHistory(zep_history_processor)]`
  and `@agent.tool zep_search`.
- **Build approach:** `Agent(deps_type=ZepDeps)`, async processor persists the latest user
  turn + prepends Zep's context block; `@agent.tool` `zep_search` → `graph.search(user_id=...)`.
- **Risks:** `ProcessHistory` fires **once per model request, not per run** — multi-step
  tool runs re-invoke it → duplicate episodes; dedupe per-run or persist in a `before_run`-style
  hook and only prepend context in the processor. Hot-path latency (use `return_context=True`
  to fold persist+retrieve). Async ingestion (no read-after-write within a turn). v2.0 will
  remove the legacy kwarg (pin `>=1,<2`). For user graphs use `graph.search(user_id=...)`, not `graph_id=`.

## 3. LangGraph (Python) — **GO** (node/tool helpers primary; hybrid `BaseStore` secondary)

- **Install:** `pip install langgraph` — **1.2.5**. Adapter subclasses
  `langgraph.store.base.BaseStore`.
- **Confirmed hook** (`langgraph/store/base/__init__.py`): a concrete subclass must
  implement **exactly two `@abstractmethod`s** — `def batch(self, ops) -> list[Result]`
  (`:724`) and `async def abatch(self, ops) -> list[Result]` (`:746`). All public
  `get/put/search/delete/list_namespaces` + async mirrors are **concrete** and delegate
  by building `Op`s (`GetOp`/`SearchOp`/`PutOp`/`ListNamespacesOp`); `delete` is a
  `PutOp(value=None)`. `InMemoryStore` confirms the pattern. Attach via
  `StateGraph.compile(store=...)` (`graph/state.py:1169`); access via `get_store()`
  (`config.py:32`) or `Runtime.store` (`runtime.py:115`).
- **Corrections to prior plan:** none — exact. (Key efficiency: implement only `batch`/`abatch`.)
- **Sketch:** `imports-ok` + instantiates (`isinstance(s, BaseStore)`, `__abstractmethods__ == frozenset()`).
- **Build approach:** dispatch over Op types in `batch`/`abatch`; `put` → `graph.add(type="json")`,
  `search` → `graph.search(scope="edges"|"auto")`. Map a namespace that identifies an end
  user to `user_id=` (user graph + `thread.get_user_context`) rather than `graph_id=`.
  Use **`AsyncZep` in `abatch`** (don't wrap the sync client).
- **Condition (the "conditional"):** Zep is a **temporal semantic graph, not a KV store**.
  `put` does not round-trip the stored dict; `search` returns extracted **facts/entities**,
  not the original value or caller key. Exact-key `get`, `list_namespaces`, hard `delete`,
  and synchronous read-after-write have **no faithful Zep equivalent**. `BaseStore` is the
  cross-thread long-term-memory layer — **not** the checkpointer (`BaseCheckpointSaver`),
  so graph execution / threads / short-term state are unaffected regardless.
- **Verified ecosystem evidence (informs the recommended shape):**
  - **Zep already documents** a LangGraph integration (`help.getzep.com/langgraph-memory.md`)
    using **direct Zep client calls inside graph nodes** — `thread.get_user_context` to inject
    a system message, `thread.add_messages` to persist, `graph.search(scope="edges"/"nodes")`
    as agent tools — **not** `BaseStore`, **not** the checkpointer.
  - **mem0** (verified against `mem0ai` 2.0.6 source — zero `BaseStore`/`langgraph` references)
    uses the same **direct-client-in-node** pattern (`mem0.search` + `mem0.add`, injected into
    the system prompt); it ships **no** `BaseStore`. (A third-party blog claiming mem0 has a
    BaseStore adapter is false.)
  - **langmem** (LangChain's memory tools) **hard-requires** a `BaseStore`:
    `create_manage_memory_tool` / `create_search_memory_tool` "will not work if you do not
    provide a store," and langmem ships no store itself. No major memory vendor currently ships
    a first-party `BaseStore` — so one would be **differentiating**, not table-stakes.
- **DECISION (locked) — node/tool helpers PRIMARY + hybrid-delegate `BaseStore` SECONDARY**
  (revises the earlier "BaseStore-only" scope):
  1. **Primary (`zep-langgraph`):** node/tool helpers calling the Zep client directly — a
     context-injection helper around `thread.get_user_context`, a persistence helper around
     `thread.add_messages`, and prebuilt `graph.search` tools — with a `create_react_agent`/
     `StateGraph` example. Matches Zep's own guide and mem0's proven pattern; preserves Zep's
     temporal-graph retrieval (no KV flattening).
  2. **Secondary — `ZepStore(BaseStore)` to unlock the langmem / `create_react_agent(store=...)`
     audience.** langmem does put→get/update/delete **by key** plus `search`, so a *pure* Zep
     store degrades on exact-key round-trip / update / delete / read-after-write (per the
     Condition above). Two builds: **(a) hybrid-delegate** — wrap a KV store
     (`InMemoryStore`/`PostgresStore`) for exact-key ops, route `search` to Zep — faithful
     drop-in; **(b) thin direct adapter** — `put`→`graph.add`, `search`→`graph.search` —
     simpler, but must raise `NotImplementedError` on KV-only ops (loud) and accept eventual
     consistency. Recommend **(a)** for a true drop-in langmem backend; **(b)** only if scoped
     to semantic-recall-only and clearly documented.

## 4. Mastra (TypeScript) — **GO**

- **Install:** `npm i @mastra/core@^1.42.0 @getzep/zep-cloud@^3.23.0 zod` — `@mastra/core`
  **1.42.0**. Integrate as **custom tools via `createTool`** (à la `@mastra/mem0`), **not**
  a `MastraStorage` adapter.
- **Confirmed hook** (`@mastra/core/dist/tools/`): `createTool({ id, description,
  inputSchema?, outputSchema?, execute? })` (`tool.d.ts:274`). `execute` is
  **`(inputData, context) => Promise<...>`** (`types.d.ts:510`) — first arg is the
  **validated input object directly** (not `{ context }`); second is `ToolExecutionContext`
  (`mastra?`, `requestContext?`, `abortSignal?`, …). Tools attach to an agent as a
  **record**: `new Agent({ id, name, instructions, model, tools: { ... } })`.
- **Corrections to prior plan:** (1) `execute(inputData, context)` — **not** `{ context }`;
  no `runtimeContext` positional (use `context.requestContext` / `context.mastra`).
  (2) `new Agent({...})` requires **both `id` and `name`** in 1.42.0.
- **Zep TS API confirmed** (`@getzep/zep-cloud` .d.ts): `import { ZepClient }`;
  `new ZepClient({ apiKey })`; `zep.thread.addMessages(threadId, { messages, returnContext?,
  ignoreRoles? }) → { context?, messageUuids?, taskId? }`; `Message = { role: RoleType,
  content, name? }`; `RoleType` is a **closed enum** (`user|assistant|system|tool|function|norole`);
  `zep.graph.search({ query, userId?, graphId?, scope?, ... }) → { context?, edges?, ... }`,
  `EntityEdge.fact: string`.
- **Sketch:** `tsc-pass` (NodeNext + strict). Two tools `zep-remember` / `zep-search`
  wired into `new Agent({ id, name, ..., tools: { zepRemember, zepSearch } })`; optional
  3rd tool wrapping `thread.getUserContext` for whole-user-graph recall.
- **Why tools over an adapter:** a `MastraStorage`/`MemoryStorage` adapter forces ~9
  row-oriented thread/message CRUD methods Zep's graph model can't honor faithfully; tools
  expose Zep's two real ops cleanly.
- **Risks:** map app roles to the closed `RoleType` enum; async ingestion; `createTool`
  `execute` shape differs across Mastra versions (pin); young SDK churn.

## 5. Google ADK — Go — **GO**

- **Install:** `go get google.golang.org/adk@v1.4.0` (== `github.com/google/adk-go@v1.4.0`)
  + `go get github.com/getzep/zep-go/v3@v3.23.0`; `go mod tidy` (pulls `genai@v1.57.0`,
  `jsonschema-go@v0.4.2`).
- **Confirmed hook:**
  - **Context injection — `BeforeModelCallback`** (`agent/llmagent/llmagent.go`):
    `func(ctx agent.CallbackContext, llmRequest *model.LLMRequest) (*model.LLMResponse, error)`.
    Return `(nil, nil)` to proceed with the mutated request; return non-nil to short-circuit.
    `agent.CallbackContext` embeds `context.Context` and exposes `UserContent()`,
    `SessionID()`, `UserID()`, `AppName()`. Inject via `req.Config.SystemInstruction`
    (`*genai.Content`; `genai.NewContentFromText`). Attach via `llmagent.Config.BeforeModelCallbacks`.
  - **Memory — `memory.Service`** (`memory/service.go:31`, **not** `BaseMemoryService`):
    `AddSessionToMemory(ctx, s session.Session) error` + `SearchMemory(ctx, *SearchRequest)
    (*SearchResponse, error)`. **Attaches at the Runner** (`runner.Config.MemoryService`),
    reached by tools via `ToolContext.SearchMemory` — **not** on the agent.
  - **Tool:** `tool.Tool` interface (`Name`/`Description`/`IsLongRunning`); build a
    graph-search tool with `functiontool.New[TArgs,TResults](Config, func(tool.Context, TArgs)(TResults,error))`.
- **Corrections to prior plan:** `InMemoryService` is a constructor **function**, not a type;
  `CallbackContext` accessors are inherited from embedded `ReadonlyContext`; **memory.Service
  is Runner-level** (the biggest structural note). Otherwise exact.
- **Zep Go SDK confirmed:** `client.NewClient(option.WithAPIKey(...))`; `Thread.AddMessages(ctx,
  threadID, *AddThreadMessagesRequest{Messages, ReturnContext}) → {Context *string, ...}`;
  `Thread.GetUserContext`; `Graph.Search(ctx, *GraphSearchQuery{Query, UserID|GraphID, Scope, ...})`.
- **Sketch:** `go build ./...` + `go vet ./...` → **exit 0** (compiles + vets clean,
  ~184 lines).
- **Build approach:** primary = `BeforeModelCallback` + direct Zep client (persist user
  turn via `AddMessages{ReturnContext:true}`, inject `.Context` into `req.Config.SystemInstruction`);
  optional `functiontool.New` graph-search tool; implement `memory.Service` only to route
  ADK's built-in memory tools through Zep.
- **Risks:** `UserID`/`GraphID` are mutually exclusive in `GraphSearchQuery`; async ingestion;
  ADK is pre-stable + zep-go is Fern-generated → wrap SDK calls in a thin adapter and pin
  `adk`/`zep-go/v3`/`genai`.

## 6. Google ADK — TypeScript (`@google/adk`) — **CONDITIONAL GO**

- **Install:** `npm i @google/adk@1.2.0 @getzep/zep-cloud@3.23.0` (pulls `@google/genai 1.52.0`).
  `@google/adk` **1.2.0** is post-1.0 but young — pin exactly.
- **Confirmed hook:**
  - **Primary = `beforeModelCallback`** on `LlmAgentConfig` (`agents/llm_agent.d.ts`):
    `({ context, request }) => LlmResponse | undefined | Promise<...>`. Barrel-exported,
    stable; `context.userId`/`sessionId`/`userContent`; mutate `request`.
  - **Alternative = `BaseTool.processLlmRequest`** (`tools/base_tool.d.ts`): exists; the
    base destructures `{ llmRequest }` but the param type `ToolProcessLlmRequest` carries
    `{ toolContext, llmRequest }` so overriding with both is valid.
  - **Memory — `BaseMemoryService`** (`memory/base_memory_service.d.ts`):
    `searchMemory({appName,userId,query})` + `addSessionToMemory(session)`.
    `addSessionToMemory` is **session-end** granularity — per-turn persistence must go via
    the callback/tool, not this.
- **Corrections to prior plan:** `appendInstructions`/`appendTools` are **unreachable under
  NodeNext** — the package `exports` map exposes only `"."`, so deep-importing
  `@google/adk/dist/types/models/llm_request.js` fails (TS2307, reproduced). **Inject by
  mutating `llmRequest.config.systemInstruction` (a `string` is valid) / `llmRequest.contents`
  directly.**
- **Sketch:** `tsc-pass` (NodeNext + strict; `ZepContextTool extends BaseTool` and the
  `beforeModelCallback` variant).
- **Build approach (the "conditional"):** target the **official `@google/adk`**; use
  **`beforeModelCallback` as the primary hook** (stable, exported), offer `ZepContextTool`
  as a tool-centric alternative. Conditions: inject via direct `config.systemInstruction`
  mutation (not `appendInstructions`); pin `@google/adk` exactly + gate on `tsc`; per-turn
  persistence via callback/tool (never `addSessionToMemory`); pre-create the Zep thread keyed
  on ADK `sessionId`.
- **Risks:** young-GA churn in callback/tool internals; reliance on internal field
  semantics for injection; async ingestion.

## 7. CrewAI (Python, improve existing) — **GO (refresh)**

Not part of the installed-source spike (the package already ships at
`integrations/crewai/python`). Build PR scope: bump `zep-cloud` to **3.23.0**,
deduplicate the public surface (legacy `ZepStorage` in `memory.py` vs newer
`user_storage.py`/`graph_storage.py`/`tools.py`), add `SETUP.md`, refresh examples, and
raise the README to the ADK README quality bar.

---

## Corrections folded back into the build plan

1. **Versions to pin:** MS `agent-framework-core>=1.8.1` (stable, no flag);
   `pydantic-ai>=1.107,<2`; `langgraph>=1.2.5`; `@mastra/core@^1.42.0`;
   `google.golang.org/adk@v1.4.0` + `genai@v1.57.0`; `@google/adk@1.2.0`. Zep SDKs **3.23.0**.
2. **MS Agent Framework:** target the public **`Agent`** class + `before_run`/`after_run`
   (not `ChatAgent`/`invoking`).
3. **Pydantic AI:** build on **`capabilities=[ProcessHistory(...)]`**, not the deprecated
   `history_processors=` kwarg; guard the once-per-model-request re-invocation.
4. **LangGraph:** implement only `batch`/`abatch`; explicitly NotImplement the KV-only ops.
5. **Mastra:** `createTool` `execute(inputData, context)`; `Agent` needs `id`+`name`.
6. **ADK Go:** `memory.Service` is **Runner-level**; `BeforeModelCallback` is the agent-level
   injection path; `GraphSearchQuery.UserID`/`GraphID` mutually exclusive.
7. **ADK TS:** inject via `config.systemInstruction` mutation (the documented helpers are
   unreachable under NodeNext); `beforeModelCallback` is the primary hook.
8. **Cross-cutting:** Zep ingestion is **asynchronous** (no read-after-write in a turn) —
   every integration must design for eventual availability; reuse one client; prefer
   `add_messages(return_context=True)` to fold persist+retrieve into one round-trip.
9. **`zep-docs` MCP:** during the spike it returned `llms.txt`-style index/nav chunks
   instead of page bodies, so all Zep signatures were confirmed from installed SDK source.
   It has **since been fixed** — it now returns full page bodies with Python/TS/Go code
   (verified against `retrieving-context.md`, `sdk-reference/thread/get-user-context.md`).
   Build PRs should use it (plus installed source) for Zep semantics.

## Recommended next step

All seven are GO (two scoped). Proceed to **per-integration build PRs**, parallel agents
in separate worktrees, branching off the **foundation PR** (platform-first restructure +
CI/release rework) described in the build plan. Each PR ships: README, `SETUP.md`
(Zep signup at getzep.com), a runnable example, tests, and CI wiring.
