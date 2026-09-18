/**
 * `ZepMemoryService` — an ADK-native `BaseMemoryService` backed by Zep.
 *
 * ADK's memory extension point (`BaseMemoryService`) lets a `Runner` search
 * long-term memory through a model-opt-in tool (`loadMemory` /
 * `preloadMemory`): the model decides when to call
 * `toolContext.searchMemory(query)`, which reaches this service via
 * `new Runner({ ..., memoryService: new ZepMemoryService(...) })`.
 *
 * This is a different extension point from `ZepContextTool` /
 * `createZepBeforeModelCallback`:
 *
 * - **ZepMemoryService** (this module) — ADK-native, model-opt-in. The model
 *   decides, per turn, whether to call `loadMemory`/`preloadMemory`. Use this
 *   when you want the model to actively decide when memory is relevant, or
 *   when wiring into ADK code paths that already expect a `memoryService`
 *   (e.g. ADK's own memory tools, evaluation harnesses).
 * - **ZepContextTool** / **createZepBeforeModelCallback** — guaranteed
 *   injection. Runs on every LLM turn, so Zep context is always present
 *   regardless of whether the model would have thought to ask for it. Use
 *   this for the common case: an assistant that should always have the
 *   user's long-term context available.
 *
 * The two are complementary, not mutually exclusive — e.g. use
 * `ZepContextTool` for always-on context and additionally register
 * `ZepMemoryService` so the model can explicitly search for more via
 * `loadMemory` when it decides the always-on context wasn't enough.
 *
 * Why `addSessionToMemory` is a no-op: Zep persistence already happens live,
 * on every turn, via `ZepContextTool` (or `createZepBeforeModelCallback` /
 * `createZepAfterModelCallback`, which call `thread.addMessages` directly).
 * ADK calls `addSessionToMemory` to flush a session's conversation into a
 * memory store at some point after it happens (e.g. session end); since Zep
 * already has every message as it's persisted turn-by-turn, doing it again
 * here would re-ingest the same conversation into the graph a second time.
 * This mirrors the Go integration's `NewMemoryService` and Python's
 * `ZepMemoryService`, whose `AddSessionToMemory`/`add_session_to_memory` are
 * the same intentional no-op for the same reason.
 */

import type { ZepClient } from "@getzep/zep-cloud";
import type {
  BaseMemoryService,
  MemoryEntry,
  SearchMemoryRequest,
  SearchMemoryResponse,
} from "@google/adk";
import type { Content } from "@google/genai";
import {
  searchGraphScope,
  type ZepGraphSearchScope,
} from "./graph-search-tool.js";
import { defaultLogger, type Logger } from "./logging.js";

// Minimal structural type for the `Session` ADK passes to
// `addSessionToMemory`. The method is a no-op (see module docstring), so the
// integration never needs to read a session's fields.
interface SessionLike {
  id?: string;
}

/**
 * Scopes supported by {@link ZepMemoryService.searchMemory}. Matches the
 * scope enum exposed by `ZepGraphSearchTool`.
 */
const SUPPORTED_SCOPES: readonly ZepGraphSearchScope[] = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
];

const DEFAULT_SCOPE: ZepGraphSearchScope = "edges";

/** Options for the {@link ZepMemoryService} constructor. */
export interface ZepMemoryServiceOptions {
  /** An initialised `ZepClient`. The caller owns its lifecycle. */
  zep: ZepClient;
  /**
   * Fixed graph UUID. When set, every `searchMemory` call targets this
   * graph. When omitted, the service reads the graph UUID of the user named
   * by `SearchMemoryRequest.userId`, which ADK carries from the session. In
   * Zep v4 that value must be the UUID of the Zep user.
   */
  graphUuid?: string;
  /**
   * The Zep graph search scope to use for every `searchMemory` call.
   * Matches `ZepGraphSearchTool`'s scope enum: `"edges"` (facts, the
   * default), `"nodes"` (entities and summaries), `"episodes"` (raw
   * message/data content), `"observations"` (derived memories),
   * `"thread_summaries"` (incremental thread summaries), or `"auto"` (Zep's
   * own pre-assembled Context Block, returned as a single memory entry).
   */
  scope?: ZepGraphSearchScope;
  /**
   * Maximum number of results per search. `undefined` (the default) omits
   * the parameter so the Zep SDK applies its own default.
   */
  limit?: number;
  /** Logger for Zep failures and unsupported-scope warnings. Defaults to a `console`-backed logger. */
  logger?: Logger;
}

/**
 * ADK-native memory service that searches a user's Zep knowledge graph.
 *
 * Register this on a `Runner` to give ADK's built-in memory tooling
 * (`loadMemory`, `preloadMemory`) access to Zep:
 *
 * @example
 * ```ts
 * import { Runner, LlmAgent } from "@google/adk";
 * import { LOAD_MEMORY } from "@google/adk";
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { ZepMemoryService } from "@getzep/zep-adk";
 *
 * const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const agent = new LlmAgent({
 *   name: "memory_agent",
 *   model: "gemini-2.5-flash",
 *   tools: [LOAD_MEMORY],
 * });
 * const runner = new Runner({
 *   agent,
 *   appName: "my_app",
 *   sessionService,
 *   memoryService: new ZepMemoryService({ zep }),
 * });
 * ```
 *
 * See the module docstring for guidance on when to use this versus
 * `ZepContextTool` / `createZepBeforeModelCallback`.
 */
export class ZepMemoryService implements BaseMemoryService {
  private readonly zep: ZepClient;
  private readonly graphUuid?: string;
  private readonly scope: ZepGraphSearchScope;
  private readonly limit?: number;
  private readonly logger: Logger;
  /** Graph UUID for each user UUID, cached for the life of the service. */
  private readonly graphUuidByUser = new Map<string, string>();

  constructor(options: ZepMemoryServiceOptions) {
    this.zep = options.zep;
    this.graphUuid = options.graphUuid;
    this.scope = options.scope ?? DEFAULT_SCOPE;
    this.limit = options.limit;
    this.logger = options.logger ?? defaultLogger;
  }

  /**
   * No-op. See the module docstring for the no-double-persist rationale.
   *
   * Conversation turns are already persisted live via `ZepContextTool` (or
   * `createZepBeforeModelCallback`/`createZepAfterModelCallback`). Ingesting
   * the session again here would duplicate that work in Zep's graph.
   */
  async addSessionToMemory(_session: SessionLike): Promise<void> {
    this.logger.debug(
      "ZepMemoryService.addSessionToMemory is a no-op: Zep already ingests " +
        "conversation turns live via ZepContextTool / " +
        "createZepBeforeModelCallback + createZepAfterModelCallback, so " +
        "re-ingesting the full session here would double-persist it.",
    );
  }

  /**
   * Search the user's Zep graph and map results to `MemoryEntry` objects.
   *
   * `request.appName` has no Zep equivalent — Zep scopes memory by user
   * graph, not by application — so it is accepted (to satisfy the ADK
   * interface) but not forwarded to Zep.
   *
   * Zep v4 searches a graph by UUID. The service uses the configured
   * `graphUuid` when there is one. Otherwise it reads the graph UUID of
   * `request.userId` with `user.get`, which requires the ADK session to
   * carry the UUID of the Zep user. The service never calls `user.lookup`.
   *
   * On any Zep failure, logs a warning (lengths only, never query or result
   * content) and returns an empty response rather than throwing, so a
   * memory lookup never breaks the agent.
   *
   * An unsupported scope is rejected before the search is issued: like Go's
   * `memoryService.SearchMemory` (see `searchScopeSupported` in
   * `integrations/adk/go/memory.go`) and Python's `ZepMemoryService`, this
   * avoids spending a live network call on a scope we can never map into
   * memory entries.
   */
  async searchMemory(
    request: SearchMemoryRequest,
  ): Promise<SearchMemoryResponse> {
    if (!SUPPORTED_SCOPES.includes(this.scope)) {
      this.logger.warn(
        `Unsupported Zep memory search scope '${this.scope}'; returning no memories`,
      );
      return { memories: [] };
    }

    let texts: string[];
    try {
      const graphUuid = await this.resolveGraphUuid(request.userId);
      texts = await searchGraphScope(this.zep, graphUuid, {
        scope: this.scope,
        body: { query: request.query },
        limit: this.limit,
      });
    } catch (error) {
      this.logger.warn(
        `Zep memory search failed (userId_len=${request.userId.length}, query_len=${request.query.length})`,
        error,
      );
      return { memories: [] };
    }

    const memories: MemoryEntry[] = texts.map((text) => ({
      content: { role: "model", parts: [{ text }] } satisfies Content,
      author: "Zep",
    }));
    return { memories };
  }

  /** Resolve the UUID of the graph to search for a user UUID. */
  private async resolveGraphUuid(userUuid: string): Promise<string> {
    if (this.graphUuid) {
      return this.graphUuid;
    }

    const cached = this.graphUuidByUser.get(userUuid);
    if (cached) {
      return cached;
    }

    const user = await this.zep.user.get(userUuid);
    if (!user.graphUuid) {
      throw new Error(`Zep user ${userUuid} has no graph UUID.`);
    }
    this.graphUuidByUser.set(userUuid, user.graphUuid);
    return user.graphUuid;
  }
}
