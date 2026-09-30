/**
 * `ZepGraphSearchTool` — a model-callable ADK tool for searching a Zep
 * knowledge graph on demand.
 *
 * Unlike `ZepContextTool` (which injects context automatically on every turn),
 * this tool appears in the model's tool list and is called when the model
 * decides it needs to look something up.
 *
 * Search target resolution:
 *   - If `graphUuid` is set, every search targets that graph.
 *   - Otherwise the graph of the current user is searched. The tool resolves
 *     the user UUID from explicit options → `zep_user_uuid` state key → the
 *     ADK `userId`, then reads the graph UUID of that user with
 *     `user.get(userUuid)` and caches it for the life of the tool.
 *
 * ## Pin-or-expose parameter model
 *
 * Every search knob (`scope`, `reranker`, `limit`, `mmrLambda`,
 * `centerNodeUuid`) is **tri-state** at construction time:
 *
 * - **Concrete value** (e.g. `scope: "edges"`) → *pinned*. Hidden from the
 *   model's tool schema and always used, regardless of what the model sends.
 * - **`null`** → *hidden*. Hidden from the model's tool schema AND omitted
 *   from the search call entirely (useful for suppressing optional params,
 *   e.g. `mmrLambda: null` when the reranker is never `"mmr"`).
 * - **`undefined` / omitted** → *exposed*. Included in the model's tool
 *   schema with the documented default, so the model can choose a value.
 *
 * `searchFilters` and `bfsOriginNodeUuids` are always constructor-only — they
 * are never exposed to the model and, when set, are always applied.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import {
  BaseTool,
  type Context,
} from "@google/adk";
import { type FunctionDeclaration, type Schema, Type } from "@google/genai";
import {
  resolveIdentity,
  type AdkContextLike,
  type ZepIdentityOptions,
} from "./identity.js";
import { defaultLogger, type Logger } from "./logging.js";

const DEFAULT_DESCRIPTION =
  "Search the user's knowledge graph for information from previous " +
  "conversations, known facts about the user, or general context. " +
  "Use this to look up specific details the user has shared before.";

/**
 * The search scopes of the tool.
 *
 * Zep v4 has one search method for each scope, so the SDK no longer exports
 * a scope enum. The tool keeps the scope as its own parameter and calls the
 * matching v4 method.
 */
export type ZepGraphSearchScope =
  | "edges"
  | "nodes"
  | "episodes"
  | "observations"
  | "thread_summaries"
  | "auto";

/** The scopes that the tool can expose to the model. */
const SCOPE_ENUM = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;

/** Zep's supported rerankers (`V4SearchRequestReranker`). */
const RERANKER_ENUM = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const;

/**
 * Search scopes this tool knows how to format, matching {@link SCOPE_ENUM}
 * exactly.
 */
const SUPPORTED_SCOPES = [
  "auto",
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
] as const satisfies readonly ZepGraphSearchScope[];

const DEFAULT_SCOPE: ZepGraphSearchScope = "edges";
const DEFAULT_RERANKER: Zep.V4SearchRequestReranker = "rrf";
const DEFAULT_LIMIT = 10;

/** Options for the {@link ZepGraphSearchTool} constructor. */
export interface ZepGraphSearchToolOptions extends ZepIdentityOptions {
  /** An initialised `ZepClient`. The caller owns its lifecycle. */
  zep: ZepClient;
  /**
   * Fixed graph UUID. When set, all searches target this graph regardless of
   * the active user. When omitted, the tool reads the graph UUID of the
   * resolved user from Zep and searches that graph.
   */
  graphUuid?: string;
  /** Tool name shown to the model. Defaults to `"zep_graph_search"`. */
  name?: string;
  /** Tool description shown to the model. */
  description?: string;
  /**
   * Search scope: `"edges"` for facts and relationships, `"nodes"` for
   * entities and their summaries, `"episodes"` for raw text data
   * (unstructured text, messages, or JSON), `"observations"` for derived
   * memories, `"thread_summaries"` for incremental thread summaries, `"auto"`
   * to let Zep decide the best mix of results.
   *
   * Tri-state: a concrete value pins the scope (hidden from the model,
   * always used); `null` hides it AND omits it from the search call;
   * `undefined`/omitted exposes it to the model, defaulting to `"edges"`.
   */
  scope?: ZepGraphSearchScope | null;
  /**
   * Result ordering algorithm: `"rrf"` (balanced), `"mmr"` (diverse),
   * `"cross_encoder"` (highest accuracy), `"episode_mentions"` (frequently
   * referenced), `"node_distance"` (near a specific entity).
   *
   * Tri-state: a concrete value pins the reranker (hidden from the model,
   * always used); `null` hides it AND omits it from the search call;
   * `undefined`/omitted exposes it to the model, defaulting to `"rrf"`.
   */
  reranker?: Zep.V4SearchRequestReranker | null;
  /**
   * Maximum number of results to return.
   *
   * Tri-state: a concrete value pins the limit (hidden from the model,
   * always used); `null` hides it AND omits it from the search call;
   * `undefined`/omitted exposes it to the model, defaulting to `10`.
   */
  limit?: number | null;
  /**
   * Balance between diversity (`0.0`) and relevance (`1.0`). Only used when
   * `reranker` is `"mmr"`.
   *
   * Tri-state: a concrete value pins `mmrLambda` (hidden from the model,
   * always used); `null` hides it AND omits it from the search call;
   * `undefined`/omitted exposes it to the model with no default.
   */
  mmrLambda?: number | null;
  /**
   * UUID of the center node for distance-based reranking. Required when
   * `reranker` is `"node_distance"`.
   *
   * Tri-state: a concrete value pins `centerNodeUuid` (hidden from the
   * model, always used); `null` hides it AND omits it from the search call;
   * `undefined`/omitted exposes it to the model with no default.
   */
  centerNodeUuid?: string | null;
  /**
   * Search filters to apply (`nodeLabels`, `edgeTypes`,
   * `excludeNodeLabels`, `excludeEdgeTypes`, property filters, etc.).
   * Constructor-only — never exposed to the model. Always applied when set.
   */
  searchFilters?: Zep.SearchFilters;
  /**
   * Node UUIDs seeding a breadth-first search. Constructor-only — never
   * exposed to the model. Always applied when set.
   */
  bfsOriginNodeUuids?: string[];
  /** Logger for Zep failures and invalid-argument warnings. Defaults to a `console`-backed logger. */
  logger?: Logger;
}

/** Arguments the model supplies when calling the tool. */
interface GraphSearchArgs {
  query?: unknown;
  scope?: unknown;
  reranker?: unknown;
  limit?: unknown;
  mmrLambda?: unknown;
  centerNodeUuid?: unknown;
}

/** Tri-state resolution for a single pinnable/exposable parameter. */
type PinState<T> =
  | { kind: "pinned"; value: T }
  | { kind: "hidden" }
  | { kind: "exposed" };

function resolvePinState<T>(value: T | null | undefined): PinState<T> {
  if (value === null) return { kind: "hidden" };
  if (value === undefined) return { kind: "exposed" };
  return { kind: "pinned", value };
}

/**
 * A model-callable tool that searches a Zep knowledge graph.
 *
 * Every search parameter (`scope`, `reranker`, `limit`, `mmrLambda`,
 * `centerNodeUuid`) can be pinned, hidden, or exposed to the model — see the
 * {@link ZepGraphSearchToolOptions} tri-state documentation. Only exposed
 * parameters appear in the model's tool schema; `query` is always required.
 *
 * Errors are returned to the model as text rather than thrown, so a Zep
 * failure cannot crash the agent run.
 *
 * @example
 * ```ts
 * import { LlmAgent } from "@google/adk";
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { ZepGraphSearchTool } from "@getzep/zep-adk";
 *
 * const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const agent = new LlmAgent({
 *   name: "memory_agent",
 *   model: "gemini-2.5-flash",
 *   instruction: "You are a helpful assistant.",
 *   tools: [new ZepGraphSearchTool({ zep, userUuid })],
 * });
 * ```
 *
 * @example Restoring the old "always pinned" behavior
 * ```ts
 * // Model only ever sees `query`; scope/reranker/limit are fixed.
 * new ZepGraphSearchTool({
 *   zep,
 *   userUuid,
 *   scope: "edges",
 *   reranker: "rrf",
 *   limit: 10,
 *   mmrLambda: null,
 *   centerNodeUuid: null,
 * });
 * ```
 */
export class ZepGraphSearchTool extends BaseTool {
  private readonly zep: ZepClient;
  private readonly logger: Logger;
  private readonly graphUuid?: string;
  private readonly identity: ZepIdentityOptions;
  private readonly declaration: FunctionDeclaration;
  /** Graph UUID for each resolved user UUID, cached for the life of the tool. */
  private readonly graphUuidByUser = new Map<string, string>();

  private readonly scopeState: PinState<ZepGraphSearchScope>;
  private readonly rerankerState: PinState<Zep.V4SearchRequestReranker>;
  private readonly limitState: PinState<number>;
  private readonly mmrLambdaState: PinState<number>;
  private readonly centerNodeUuidState: PinState<string>;
  private readonly searchFilters?: Zep.SearchFilters;
  private readonly bfsOriginNodeUuids?: string[];

  constructor(options: ZepGraphSearchToolOptions) {
    super({
      name: options.name ?? "zep_graph_search",
      description: options.description ?? DEFAULT_DESCRIPTION,
    });
    this.zep = options.zep;
    this.logger = options.logger ?? defaultLogger;
    this.graphUuid = options.graphUuid;
    this.identity = {
      userUuid: options.userUuid,
      threadUuid: options.threadUuid,
      firstName: options.firstName,
      lastName: options.lastName,
    };

    this.scopeState = resolvePinState(options.scope);
    this.rerankerState = resolvePinState(options.reranker);
    this.limitState = resolvePinState(options.limit);
    this.mmrLambdaState = resolvePinState(options.mmrLambda);
    this.centerNodeUuidState = resolvePinState(options.centerNodeUuid);

    if (
      this.scopeState.kind === "pinned" &&
      !SUPPORTED_SCOPES.includes(this.scopeState.value)
    ) {
      throw new Error(
        `Unsupported Zep graph search scope: '${this.scopeState.value}'. ` +
          `Supported scopes are: ${SUPPORTED_SCOPES.join(", ")}.`,
      );
    }

    this.searchFilters = options.searchFilters;
    this.bfsOriginNodeUuids = options.bfsOriginNodeUuids;

    this.declaration = this.buildDeclaration();
  }

  /**
   * Builds the function declaration from exposed (non-pinned, non-hidden)
   * parameters only. `query` is always present and required.
   */
  private buildDeclaration(): FunctionDeclaration {
    const properties: Record<string, Schema> = {
      query: {
        type: Type.STRING,
        description: "Search query text (max 400 characters).",
      },
    };

    if (this.scopeState.kind === "exposed") {
      properties.scope = {
        type: Type.STRING,
        description:
          "What to search for: 'edges' for facts and relationships, " +
          "'nodes' for entities and their summaries, " +
          "'episodes' for raw text data (unstructured text, messages, or JSON), " +
          "'observations' for derived memories, " +
          "'thread_summaries' for incremental thread summaries, " +
          "'auto' to let Zep decide the best mix of results.",
        enum: [...SCOPE_ENUM],
      };
    }

    if (this.rerankerState.kind === "exposed") {
      properties.reranker = {
        type: Type.STRING,
        description:
          "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), " +
          "'cross_encoder' (highest accuracy), 'episode_mentions' " +
          "(frequently referenced), 'node_distance' (near a specific entity).",
        enum: [...RERANKER_ENUM],
      };
    }

    if (this.limitState.kind === "exposed") {
      properties.limit = {
        type: Type.INTEGER,
        description: "Maximum number of results to return.",
      };
    }

    if (this.mmrLambdaState.kind === "exposed") {
      properties.mmrLambda = {
        type: Type.NUMBER,
        description:
          "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'.",
      };
    }

    if (this.centerNodeUuidState.kind === "exposed") {
      properties.centerNodeUuid = {
        type: Type.STRING,
        description:
          "UUID of the center node for distance-based reranking. " +
          "Required when reranker is 'node_distance'.",
      };
    }

    return {
      name: this.name,
      description: this.description,
      parameters: {
        type: Type.OBJECT,
        properties,
        required: ["query"],
      },
    };
  }

  /** Exposes the declaration built from non-pinned, non-hidden parameters. */
  override _getDeclaration(): FunctionDeclaration {
    return this.declaration;
  }

  /**
   * Run the graph search and return formatted results as text for the model.
   *
   * Returns a human-readable error string (never throws) when the search
   * target cannot be resolved or the Zep call fails.
   */
  override async runAsync(request: {
    args: Record<string, unknown>;
    toolContext: Context;
  }): Promise<unknown> {
    const { args, toolContext } = request;
    const typedArgs = args as GraphSearchArgs;
    const query = typedArgs.query;
    if (typeof query !== "string" || query.trim().length === 0) {
      return "Error: a non-empty 'query' string is required.";
    }

    const scope = this.resolveEnum(
      "scope",
      this.scopeState,
      typedArgs.scope,
      SCOPE_ENUM,
      DEFAULT_SCOPE,
    );
    const reranker = this.resolveEnum(
      "reranker",
      this.rerankerState,
      typedArgs.reranker,
      RERANKER_ENUM,
      DEFAULT_RERANKER,
    );
    const limit = this.resolveNumber(
      "limit",
      this.limitState,
      typedArgs.limit,
      DEFAULT_LIMIT,
    );
    const mmrLambda = this.resolveNumber(
      "mmrLambda",
      this.mmrLambdaState,
      typedArgs.mmrLambda,
    );
    const centerNodeUuid = this.resolveString(
      this.centerNodeUuidState,
      typedArgs.centerNodeUuid,
    );

    const body: Zep.SearchRequest = { query };
    if (reranker !== undefined) body.reranker = reranker;
    if (mmrLambda !== undefined) body.mmrLambda = mmrLambda;
    if (centerNodeUuid !== undefined) body.centerNodeUuid = centerNodeUuid;
    if (this.searchFilters !== undefined) {
      body.filters = this.searchFilters;
    }
    if (this.bfsOriginNodeUuids !== undefined) {
      body.bfsOriginNodeUuids = this.bfsOriginNodeUuids;
    }

    let graphUuid: string;
    try {
      graphUuid = await this.resolveGraphUuid(toolContext);
    } catch (error) {
      this.logger.warn("Could not resolve the graph to search", error);
      return "Error: could not determine which graph to search.";
    }

    const resolvedScope = scope ?? DEFAULT_SCOPE;
    try {
      const texts = await searchGraphScope(this.zep, graphUuid, {
        scope: resolvedScope,
        body,
        limit,
      });
      if (texts.length === 0) {
        return "No results found.";
      }
      // The "auto" scope returns one pre-assembled Context Block.
      return resolvedScope === "auto"
        ? texts[0]
        : texts.map((text) => `- ${text}`).join("\n");
    } catch (error) {
      this.logger.warn("Zep graph search failed", error);
      return `Graph search failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }

  /**
   * Resolve an enum-valued parameter: pinned beats model-provided arg beats
   * default. An invalid model-provided value falls back to the default with
   * a logged warning rather than throwing.
   */
  private resolveEnum<T extends string>(
    paramName: string,
    state: PinState<T>,
    modelValue: unknown,
    validValues: readonly T[],
    fallbackDefault: T,
  ): T | undefined {
    if (state.kind === "pinned") return state.value;
    if (state.kind === "hidden") return undefined;
    if (typeof modelValue === "string") {
      if ((validValues as readonly string[]).includes(modelValue)) {
        return modelValue as T;
      }
      this.logger.warn(
        `Zep graph search: invalid '${paramName}' value from model: ${JSON.stringify(
          modelValue,
        )}. Falling back to default '${fallbackDefault}'.`,
      );
      return fallbackDefault;
    }
    return fallbackDefault;
  }

  /**
   * Resolve a numeric parameter: pinned beats model-provided arg beats
   * default. A non-numeric model-provided value falls back to the default
   * (or is omitted, if there is none) with a logged warning.
   */
  private resolveNumber(
    paramName: string,
    state: PinState<number>,
    modelValue: unknown,
    fallbackDefault?: number,
  ): number | undefined {
    if (state.kind === "pinned") return state.value;
    if (state.kind === "hidden") return undefined;
    if (modelValue !== undefined) {
      if (typeof modelValue === "number" && Number.isFinite(modelValue)) {
        return modelValue;
      }
      this.logger.warn(
        `Zep graph search: invalid '${paramName}' value from model: ${JSON.stringify(
          modelValue,
        )}. Falling back to default.`,
      );
      return fallbackDefault;
    }
    return fallbackDefault;
  }

  /**
   * Resolve a string parameter with no default: pinned beats model-provided
   * arg beats "unset" (`undefined`, meaning omitted from the SDK call).
   */
  private resolveString(
    state: PinState<string>,
    modelValue: unknown,
  ): string | undefined {
    if (state.kind === "pinned") return state.value;
    if (state.kind === "hidden") return undefined;
    if (typeof modelValue === "string" && modelValue.length > 0) {
      return modelValue;
    }
    return undefined;
  }

  /**
   * Resolve the UUID of the graph to search.
   *
   * A configured `graphUuid` wins. Otherwise the tool resolves the user UUID
   * of the turn and reads the graph UUID of that user once, then serves
   * later turns of the same user from its cache. The tool never calls
   * `user.lookup`: a `userId` name is not an address in v4.
   */
  private async resolveGraphUuid(context: AdkContextLike): Promise<string> {
    if (this.graphUuid) {
      return this.graphUuid;
    }

    const { userUuid } = resolveIdentity(context, this.identity);
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

/**
 * Joins a name and summary as `"name: summary"`, falling back to whichever
 * half is present, or `""` when both are empty/undefined. Mirrors Go's
 * `nameSummaryText` (see `integrations/adk/go/search.go`), which Python's
 * `_format_results` also matches for the observations/thread_summaries cases.
 */
function nameSummaryText(
  name: string | null | undefined,
  summary: string | null | undefined,
): string {
  if (name && summary) return `${name}: ${summary}`;
  if (name) return name;
  return summary ?? "";
}

/** Parameters of a single scoped graph search. */
export interface GraphScopeSearchParams {
  /** The scope to search. */
  scope: ZepGraphSearchScope;
  /** The v4 search body. The `"auto"` scope uses its `query` and `filters`. */
  body: Zep.SearchRequest;
  /**
   * Maximum number of results on the first page. Zep v4 has no result limit
   * for the `"auto"` scope, so the value is ignored for that scope.
   */
  limit?: number;
}

/**
 * Runs one scoped v4 graph search and returns plain-text items.
 *
 * Shared by {@link ZepGraphSearchTool} (which prefixes each item with `"- "`
 * for the model) and `ZepMemoryService` (which wraps each item in its own
 * `MemoryEntry`).
 *
 * Zep v4 replaces the single v3 `graph.search` method with one method for
 * each scope, and the `"auto"` scope with `graph.getContext`. The v4 search
 * methods return one page of results with an opaque cursor; this function
 * reads the first page only, as the v3 call did.
 */
export async function searchGraphScope(
  zep: ZepClient,
  graphUuid: string,
  params: GraphScopeSearchParams,
): Promise<string[]> {
  const { scope, body, limit } = params;

  if (scope === "auto") {
    const response = await zep.graph.getContext(graphUuid, {
      query: body.query,
      filters: body.filters,
    });
    const context = response.context?.trim();
    return context ? [context] : [];
  }

  const request = { limit, body };
  const texts: string[] = [];

  if (scope === "edges") {
    const page = await zep.graph.searchEdges(graphUuid, request);
    for (const edge of page.data) {
      if (edge.fact) texts.push(edge.fact);
    }
  } else if (scope === "nodes") {
    const page = await zep.graph.searchNodes(graphUuid, request);
    for (const node of page.data) {
      const text = nameSummaryText(node.name, node.summary);
      if (text) texts.push(text);
    }
  } else if (scope === "episodes") {
    const page = await zep.graph.searchEpisodes(graphUuid, request);
    for (const episode of page.data) {
      if (episode.content) texts.push(episode.content);
    }
  } else if (scope === "observations") {
    const page = await zep.graph.searchObservations(graphUuid, request);
    for (const observation of page.data) {
      const text = nameSummaryText(observation.name, observation.summary);
      if (text) texts.push(text);
    }
  } else {
    const page = await zep.graph.searchThreadSummaries(graphUuid, request);
    for (const threadSummary of page.data) {
      const text = threadSummary.summary?.trim();
      if (text) texts.push(text);
    }
  }

  return texts;
}
