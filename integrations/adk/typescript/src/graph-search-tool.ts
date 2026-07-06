/**
 * `ZepGraphSearchTool` — a model-callable ADK tool for searching a Zep
 * knowledge graph on demand.
 *
 * Unlike `ZepContextTool` (which injects context automatically on every turn),
 * this tool appears in the model's tool list and is called when the model
 * decides it needs to look something up.
 *
 * Search target resolution:
 *   - If `graphId` is set, every search targets that standalone graph.
 *   - Otherwise the current user's graph is searched, with the user ID resolved
 *     from explicit options → `zep_user_id` state key → the ADK `userId`.
 *
 * ## Pin-or-expose parameter model
 *
 * Every search knob (`scope`, `reranker`, `limit`, `mmrLambda`,
 * `centerNodeUuid`) is **tri-state** at construction time:
 *
 * - **Concrete value** (e.g. `scope: "edges"`) → *pinned*. Hidden from the
 *   model's tool schema and always used, regardless of what the model sends.
 * - **`null`** → *hidden*. Hidden from the model's tool schema AND omitted
 *   from the `graph.search` call entirely (useful for suppressing optional
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
  resolveIdentity,
  type AdkContextLike,
  type ZepIdentityOptions,
} from "./identity.js";
import { defaultLogger, type Logger } from "./logging.js";

const DEFAULT_DESCRIPTION =
  "Search the user's knowledge graph for information from previous " +
  "conversations, known facts about the user, or general context. " +
  "Use this to look up specific details the user has shared before.";

/** Zep's supported search scopes (`GraphSearchScope`), model-exposable subset. */
const SCOPE_ENUM = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;

/** Zep's supported rerankers (`Reranker`). */
const RERANKER_ENUM = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const;

/**
 * Search scopes this tool knows how to format — every member of
 * `GraphSearchScope`, matching {@link SCOPE_ENUM} exactly.
 */
const SUPPORTED_SCOPES = [
  "auto",
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
] as const satisfies readonly Zep.GraphSearchScope[];

const DEFAULT_SCOPE: Zep.GraphSearchScope = "edges";
const DEFAULT_RERANKER: Zep.Reranker = "rrf";
const DEFAULT_LIMIT = 10;

/** Options for the {@link ZepGraphSearchTool} constructor. */
export interface ZepGraphSearchToolOptions extends ZepIdentityOptions {
  /** An initialised `ZepClient`. The caller owns its lifecycle. */
  zep: ZepClient;
  /**
   * Fixed standalone graph ID. When set, all searches target this graph
   * regardless of the active user. When omitted, the current user's graph is
   * searched. Mutually exclusive with per-user search.
   */
  graphId?: string;
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
   * always used); `null` hides it AND omits it from the `graph.search` call;
   * `undefined`/omitted exposes it to the model, defaulting to `"edges"`.
   */
  scope?: Zep.GraphSearchScope | null;
  /**
   * Result ordering algorithm: `"rrf"` (balanced), `"mmr"` (diverse),
   * `"cross_encoder"` (highest accuracy), `"episode_mentions"` (frequently
   * referenced), `"node_distance"` (near a specific entity).
   *
   * Tri-state: a concrete value pins the reranker (hidden from the model,
   * always used); `null` hides it AND omits it from the `graph.search` call;
   * `undefined`/omitted exposes it to the model, defaulting to `"rrf"`.
   */
  reranker?: Zep.Reranker | null;
  /**
   * Maximum number of results to return.
   *
   * Tri-state: a concrete value pins the limit (hidden from the model,
   * always used); `null` hides it AND omits it from the `graph.search` call;
   * `undefined`/omitted exposes it to the model, defaulting to `10`.
   */
  limit?: number | null;
  /**
   * Balance between diversity (`0.0`) and relevance (`1.0`). Only used when
   * `reranker` is `"mmr"`.
   *
   * Tri-state: a concrete value pins `mmrLambda` (hidden from the model,
   * always used); `null` hides it AND omits it from the `graph.search` call;
   * `undefined`/omitted exposes it to the model with no default.
   */
  mmrLambda?: number | null;
  /**
   * UUID of the center node for distance-based reranking. Required when
   * `reranker` is `"node_distance"`.
   *
   * Tri-state: a concrete value pins `centerNodeUuid` (hidden from the
   * model, always used); `null` hides it AND omits it from the
   * `graph.search` call; `undefined`/omitted exposes it to the model with no
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
 *   tools: [new ZepGraphSearchTool({ zep, userId: "user-123" })],
 * });
 * ```
 *
 * @example Restoring the old "always pinned" behavior
 * ```ts
 * // Model only ever sees `query`; scope/reranker/limit are fixed.
 * new ZepGraphSearchTool({
 *   zep,
 *   userId: "user-123",
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
  private readonly graphId?: string;
  private readonly identity: ZepIdentityOptions;
  private readonly declaration: FunctionDeclaration;

  private readonly scopeState: PinState<Zep.GraphSearchScope>;
  private readonly rerankerState: PinState<Zep.Reranker>;
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
    this.graphId = options.graphId;
    this.identity = {
      userId: options.userId,
      threadId: options.threadId,
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

    const target = this.resolveTarget(toolContext);
    if ("error" in target) {
      return target.error;
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

    const searchParams: Zep.GraphSearchQuery = {
      ...target,
      query,
    };
    if (scope !== undefined) searchParams.scope = scope;
    if (reranker !== undefined) searchParams.reranker = reranker;
    if (limit !== undefined) searchParams.limit = limit;
    if (mmrLambda !== undefined) searchParams.mmrLambda = mmrLambda;
    if (centerNodeUuid !== undefined) searchParams.centerNodeUuid = centerNodeUuid;
    if (this.searchFilters !== undefined) {
      searchParams.searchFilters = this.searchFilters;
    }
    if (this.bfsOriginNodeUuids !== undefined) {
      searchParams.bfsOriginNodeUuids = this.bfsOriginNodeUuids;
    }

    try {
      const results = await this.zep.graph.search(searchParams);
      return this.formatResults(results, scope ?? DEFAULT_SCOPE);
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

  private resolveTarget(
    context: AdkContextLike,
  ): { graphId: string } | { userId: string } | { error: string } {
    if (this.graphId) {
      return { graphId: this.graphId };
    }
    try {
      return { userId: resolveIdentity(context, this.identity).userId };
    } catch (error) {
      this.logger.warn("Could not resolve user ID for graph search", error);
      return { error: "Error: could not determine which graph to search." };
    }
  }

  private formatResults(
    results: Zep.GraphSearchResults,
    scope: Zep.GraphSearchScope,
  ): string {
    // Auto scope returns a pre-assembled context string.
    if (scope === "auto") {
      const context = results.context?.trim();
      return context && context.length > 0 ? context : "No results found.";
    }

    const texts = scopeResultsToTexts(results, scope);
    return texts.length > 0
      ? texts.map((text) => `- ${text}`).join("\n")
      : "No results found.";
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

/**
 * Flattens a `graph.search` result into plain-text items for one scope.
 *
 * Shared by {@link ZepGraphSearchTool} (which prefixes each item with `"- "`
 * for the model) and `ZepMemoryService` (which wraps each item in its own
 * `MemoryEntry`). The `"auto"` scope is intentionally excluded: it returns a
 * single pre-materialized Context Block on `results.context` rather than a
 * list of discrete items, so callers handle it separately. Mirrors Python's
 * `scope_results_to_texts` in `graph_search_tool.py`.
 */
export function scopeResultsToTexts(
  results: Zep.GraphSearchResults,
  scope: Zep.GraphSearchScope,
): string[] {
  const texts: string[] = [];

  if (scope === "edges") {
    for (const edge of results.edges ?? []) {
      if (edge.fact) texts.push(edge.fact);
    }
  } else if (scope === "nodes") {
    for (const node of results.nodes ?? []) {
      const text = nameSummaryText(node.name, node.summary);
      if (text) texts.push(text);
    }
  } else if (scope === "episodes") {
    for (const episode of results.episodes ?? []) {
      if (episode.content) texts.push(episode.content);
    }
  } else if (scope === "observations") {
    for (const observation of results.observations ?? []) {
      const text = nameSummaryText(observation.name, observation.summary);
      if (text) texts.push(text);
    }
  } else if (scope === "thread_summaries") {
    for (const summaryNode of results.threadSummaries ?? []) {
      const text = nameSummaryText(summaryNode.name, summaryNode.summary);
      if (text) texts.push(text);
    }
  }

  return texts;
}
