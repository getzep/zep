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
 *   - Otherwise the current user's graph is resolved from `userUuid` or the
 *     ADK session state and cached after the first `user.get` call.
 *
 * ## Pin-or-expose parameter model
 *
 * Every search knob (`scope`, `reranker`, `limit`, `mmrLambda`,
 * `centerNodeUuid`) is **tri-state** at construction time:
 *
 * - **Concrete value** (e.g. `scope: "edges"`) → *pinned*. Hidden from the
 *   model's tool schema and always used, regardless of what the model sends.
 * - **`null`** → *hidden*. Hidden from the model's tool schema and omitted
 *   from the search request entirely (useful for suppressing optional
 *   params, e.g. `mmrLambda: null` when the reranker is never `"mmr"`).
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
  resolveUserUuid,
  type AdkContextLike,
  type ZepIdentityOptions,
} from "./identity.js";
import { defaultLogger, type Logger } from "./logging.js";

const DEFAULT_DESCRIPTION =
  "Search the user's knowledge graph for information from previous " +
  "conversations, known facts about the user, or general context. " +
  "Use this to look up specific details the user has shared before.";

/** Supported graph search scopes that can be exposed to the model. */
const SCOPE_ENUM = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;

/** Supported search rerankers. */
const RERANKER_ENUM = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const;

type GraphSearchScope =
  | "edges"
  | "nodes"
  | "episodes"
  | "observations"
  | "thread_summaries"
  | "auto";
type SearchReranker =
  | "rrf"
  | "mmr"
  | "node_distance"
  | "episode_mentions"
  | "cross_encoder";

/**
 * Search scopes that this tool supports, matching {@link SCOPE_ENUM}.
 */
const SUPPORTED_SCOPES = [
  "auto",
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
] as const satisfies readonly GraphSearchScope[];

const DEFAULT_SCOPE: GraphSearchScope = "edges";
const DEFAULT_RERANKER: SearchReranker = "rrf";
const DEFAULT_LIMIT = 10;

/** Options for the {@link ZepGraphSearchTool} constructor. */
export interface ZepGraphSearchToolOptions extends ZepIdentityOptions {
  /** An initialised `ZepClient`. The caller owns its lifecycle. */
  zep: ZepClient;
  /**
   * Fixed standalone graph UUID. When set, all searches target this graph
   * regardless of the active user. When omitted, the current user's graph is
   * searched. Mutually exclusive with per-user search.
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
   * always used); `null` hides it and omits it from the search request;
   * `undefined`/omitted exposes it to the model, defaulting to `"edges"`.
   */
  scope?: GraphSearchScope | null;
  /**
   * Result ordering algorithm: `"rrf"` (balanced), `"mmr"` (diverse),
   * `"cross_encoder"` (highest accuracy), `"episode_mentions"` (frequently
   * referenced), `"node_distance"` (near a specific entity).
   *
   * Tri-state: a concrete value pins the reranker (hidden from the model,
   * always used); `null` hides it and omits it from the search request;
   * `undefined`/omitted exposes it to the model, defaulting to `"rrf"`.
   */
  reranker?: SearchReranker | null;
  /**
   * Maximum number of results to return.
   *
   * Tri-state: a concrete value pins the limit (hidden from the model,
   * always used); `null` hides it and omits it from the search request;
   * `undefined`/omitted exposes it to the model, defaulting to `10`.
   */
  limit?: number | null;
  /**
   * Balance between diversity (`0.0`) and relevance (`1.0`). Only used when
   * `reranker` is `"mmr"`.
   *
   * Tri-state: a concrete value pins `mmrLambda` (hidden from the model,
   * always used); `null` hides it and omits it from the search request;
   * `undefined`/omitted exposes it to the model with no default.
   */
  mmrLambda?: number | null;
  /**
   * UUID of the center node for distance-based reranking. Required when
   * `reranker` is `"node_distance"`.
   *
   * Tri-state: a concrete value pins `centerNodeUuid` (hidden from the
   * model, always used); `null` hides it AND omits it from the
   * search request; `undefined`/omitted exposes it to the model with no
   * default.
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
 *   tools: [new ZepGraphSearchTool({ zep, userUuid: "user-123" })],
 * });
 * ```
 *
 * @example Restoring the old "always pinned" behavior
 * ```ts
 * // Model only ever sees `query`; scope/reranker/limit are fixed.
 * new ZepGraphSearchTool({
 *   zep,
 *   userUuid: "user-123",
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
  private readonly graphUuidCache = new Map<string, Promise<string>>();
  private readonly identity: ZepIdentityOptions;
  private readonly declaration: FunctionDeclaration;

  private readonly scopeState: PinState<GraphSearchScope>;
  private readonly rerankerState: PinState<SearchReranker>;
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

    const selectedScope = scope ?? DEFAULT_SCOPE;
    if (
      selectedScope === "auto" &&
      (this.rerankerState.kind === "pinned" ||
        this.limitState.kind === "pinned" ||
        this.mmrLambdaState.kind === "pinned" ||
        this.centerNodeUuidState.kind === "pinned" ||
        typedArgs.reranker !== undefined ||
        typedArgs.limit !== undefined ||
        typedArgs.mmrLambda !== undefined ||
        typedArgs.centerNodeUuid !== undefined ||
        this.bfsOriginNodeUuids !== undefined)
    ) {
      return (
        "Error: the 'auto' scope uses graph.getContext and does not support " +
        "reranker, limit, mmrLambda, centerNodeUuid, or bfsOriginNodeUuids."
      );
    }

    try {
      const graphUuid = await this.resolveTarget(toolContext);
      const results = await searchGraphByScope(this.zep, graphUuid, query, {
        scope: selectedScope,
        pageSize: limit,
        reranker,
        mmrLambda,
        centerNodeUuid,
        searchFilters: this.searchFilters,
        bfsOriginNodeUuids: this.bfsOriginNodeUuids,
      });
      return this.formatResults(results, selectedScope);
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

  private async resolveTarget(context: AdkContextLike): Promise<string> {
    if (this.graphUuid) {
      return this.graphUuid;
    }
    const userUuid = resolveUserUuid(context, this.identity);
    let graphPromise = this.graphUuidCache.get(userUuid);
    if (!graphPromise) {
      graphPromise = this.zep.user
        .get(userUuid)
        .then((user) => {
          if (!user.graphUuid) {
            throw new Error("Zep did not return a graph UUID for the user.");
          }
          return user.graphUuid;
        });
      this.graphUuidCache.set(userUuid, graphPromise);
    }
    try {
      return await graphPromise;
    } catch (error) {
      this.graphUuidCache.delete(userUuid);
      throw error;
    }
  }

  private formatResults(
    results: ScopeSearchOutcome,
    scope: GraphSearchScope,
  ): string {
    if (scope === "auto") {
      const context = results.context?.trim();
      return context && context.length > 0 ? context : "No results found.";
    }

    const texts = scopeResultsToTexts(results.items, scope);
    return texts.length > 0
      ? texts.map((text) => `- ${text}`).join("\n")
      : "No results found.";
  }
}

interface ScopeSearchOutcome {
  context?: string;
  items: unknown[];
}

interface ScopeSearchOptions {
  scope: GraphSearchScope;
  pageSize?: number;
  reranker?: SearchReranker;
  mmrLambda?: number;
  centerNodeUuid?: string;
  searchFilters?: Zep.SearchFilters;
  bfsOriginNodeUuids?: string[];
}

const FILTER_KEYS_BY_SCOPE: Record<
  Exclude<GraphSearchScope, "auto">,
  readonly string[]
> = {
  edges: [
    "connectedNodeUuids",
    "dateFilters",
    "edgeTypes",
    "edgeUuids",
    "episodeUuids",
    "excludeEdgeTypes",
    "metadataFilters",
    "propertyFilters",
    "sourceNodeUuids",
    "targetNodeUuids",
  ],
  nodes: [
    "episodeUuids",
    "excludeNodeLabels",
    "metadataFilters",
    "nodeLabels",
    "propertyFilters",
  ],
  episodes: ["contentPolicyViolated", "metadataFilters"],
  observations: [],
  thread_summaries: ["metadataFilters"],
};

function validateScopeFilters(
  scope: GraphSearchScope,
  filters: Zep.SearchFilters | undefined,
): void {
  if (!filters || scope === "auto") return;
  const allowed = FILTER_KEYS_BY_SCOPE[scope];
  const unsupported = Object.keys(filters).filter((key) => !allowed.includes(key));
  if (unsupported.length > 0) {
    throw new Error(
      `The ${scope} search does not support these filters: ${unsupported.join(", ")}.`,
    );
  }
}

export async function searchGraphByScope(
  zep: ZepClient,
  graphUuid: string,
  query: string,
  options: ScopeSearchOptions,
): Promise<ScopeSearchOutcome> {
  const {
    scope,
    pageSize,
    reranker,
    mmrLambda,
    centerNodeUuid,
    searchFilters,
    bfsOriginNodeUuids,
  } = options;
  validateScopeFilters(scope, searchFilters);

  if (scope === "auto") {
    const response = await zep.graph.getContext(graphUuid, {
      query,
      filters: searchFilters,
    });
    return { context: response.context, items: [] };
  }

  const common = {
    query,
    ...(pageSize !== undefined ? { pageSize } : {}),
    ...(reranker !== undefined ? { reranker } : {}),
    ...(mmrLambda !== undefined ? { mmrLambda } : {}),
    ...(centerNodeUuid !== undefined ? { centerNodeUuid } : {}),
    ...(bfsOriginNodeUuids !== undefined ? { bfsOriginNodeUuids } : {}),
  };

  if (scope === "edges") {
    const page = await zep.graph.searchEdges(graphUuid, {
      ...common,
      filters: searchFilters as Zep.EdgeSearchFilters | undefined,
    });
    return { items: page.data };
  }
  if (scope === "nodes") {
    const page = await zep.graph.searchNodes(graphUuid, {
      ...common,
      filters: searchFilters as Zep.NodeSearchFilters | undefined,
    });
    return { items: page.data };
  }
  if (scope === "episodes") {
    const page = await zep.graph.searchEpisodes(graphUuid, {
      ...common,
      filters: searchFilters as Zep.EpisodeSearchFilters | undefined,
    });
    return { items: page.data };
  }
  if (scope === "observations") {
    const page = await zep.graph.searchObservations(graphUuid, common);
    return { items: page.data };
  }

  const page = await zep.graph.searchThreadSummaries(graphUuid, {
    ...common,
    filters: searchFilters as Zep.ThreadSummarySearchFilters | undefined,
  });
  return { items: page.data };
}

/**
 * Joins a name and summary as `"name: summary"`, falling back to whichever
 * half is present, or `""` when both are empty/undefined.
 */
function nameSummaryText(
  name: string | null | undefined,
  summary: string | null | undefined,
): string {
  if (name && summary) return `${name}: ${summary}`;
  if (name) return name;
  return summary ?? "";
}

/**
 * Flattens search results into plain-text items for one scope.
 */
export function scopeResultsToTexts(
  results: unknown[],
  scope: GraphSearchScope,
): string[] {
  const texts: string[] = [];
  const items = results as Array<Record<string, unknown>>;

  if (scope === "edges") {
    for (const edge of items) {
      if (typeof edge.fact === "string" && edge.fact) texts.push(edge.fact);
    }
  } else if (scope === "nodes") {
    for (const node of items) {
      const text = nameSummaryText(
        node.name as string | undefined,
        node.summary as string | undefined,
      );
      if (text) texts.push(text);
    }
  } else if (scope === "episodes") {
    for (const episode of items) {
      if (typeof episode.content === "string" && episode.content) {
        texts.push(episode.content);
      }
    }
  } else if (scope === "observations") {
    for (const observation of items) {
      const text = nameSummaryText(
        observation.name as string | undefined,
        observation.summary as string | undefined,
      );
      if (text) texts.push(text);
    }
  } else if (scope === "thread_summaries") {
    for (const summary of items) {
      if (typeof summary.summary === "string" && summary.summary) {
        texts.push(summary.summary);
      }
    }
  }

  return texts;
}
