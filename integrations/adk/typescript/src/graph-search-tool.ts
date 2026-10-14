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
import { BaseTool, type Context } from "@google/adk";
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
  "edges" | "nodes" | "episodes" | "observations" | "thread_summaries" | "auto";

/** The scopes that the tool can expose to the model. */
const SCOPE_ENUM = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;

type Reranker = Zep.SearchEdgesRequestReranker;

/** Zep's supported rerankers (`SearchEdgesRequestReranker`). */
const RERANKER_ENUM = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const satisfies readonly Reranker[];

/**
 * The rerankers that each v4 search scope accepts. Zep returns HTTP 400
 * "reranker is not supported on this search scope" for any other pair. The
 * `"auto"` scope calls `graph.getContext`, which takes no reranker.
 */
const RERANKERS_BY_SCOPE: Record<
  Exclude<ZepGraphSearchScope, "auto">,
  readonly Reranker[]
> = {
  edges: ["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"],
  nodes: ["rrf", "mmr", "node_distance", "cross_encoder"],
  episodes: ["rrf", "mmr"],
  observations: ["rrf", "mmr", "cross_encoder"],
  thread_summaries: ["rrf", "mmr", "cross_encoder"],
};

function scopeSupportsReranker(
  scope: ZepGraphSearchScope,
  reranker: Reranker,
): boolean {
  return scope === "auto" || RERANKERS_BY_SCOPE[scope].includes(reranker);
}

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
const DEFAULT_RERANKER: Reranker = "rrf";
const DEFAULT_LIMIT = 10;
/** The `mmrLambda` that the tool sends when the reranker is `"mmr"` and no value is set. */
export const DEFAULT_MMR_LAMBDA = 0.5;

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
   * Each scope accepts only some rerankers: `edges` accepts all five,
   * `nodes` accepts all except `"episode_mentions"`, `episodes` accepts
   * `"rrf"` and `"mmr"`, and `observations` and `thread_summaries` accept
   * `"rrf"`, `"mmr"`, and `"cross_encoder"`. `"node_distance"` requires a
   * `centerNodeUuid`, and `"episode_mentions"` requires
   * `searchFilters.episodeUuids`. The constructor throws for a pinned value
   * that cannot work with the other pinned options.
   *
   * Tri-state: a concrete value pins the reranker (hidden from the model,
   * always used); `null` hides it AND omits it from the search call;
   * `undefined`/omitted exposes it to the model, defaulting to `"rrf"`.
   * The exposed list contains only the rerankers that the tool can use.
   */
  reranker?: Reranker | null;
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
   * `reranker` is `"mmr"`. Zep requires it for `"mmr"`, so the tool sends
   * {@link DEFAULT_MMR_LAMBDA} when the reranker is `"mmr"` and no value is
   * set.
   *
   * Tri-state: a concrete value pins `mmrLambda` (hidden from the model,
   * always used); `null` hides it AND omits it from the search call, which
   * also removes `"mmr"` from the rerankers that the tool can use;
   * `undefined`/omitted exposes it to the model.
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
  { kind: "pinned"; value: T } | { kind: "hidden" } | { kind: "exposed" };

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
  private readonly rerankerState: PinState<Reranker>;
  private readonly limitState: PinState<number>;
  private readonly mmrLambdaState: PinState<number>;
  private readonly centerNodeUuidState: PinState<string>;
  private readonly searchFilters?: Zep.SearchFilters;
  private readonly bfsOriginNodeUuids?: string[];
  /** The rerankers that the other options let the tool use, for any scope. */
  private readonly usableRerankers: readonly Reranker[];
  /** The scopes that the model can select. */
  private readonly selectableScopes: readonly ZepGraphSearchScope[];

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

    this.usableRerankers = RERANKER_ENUM.filter(
      (reranker) => this.rerankerUnavailableReason(reranker) === undefined,
    );
    this.validatePinnedReranker();
    this.selectableScopes = this.computeSelectableScopes();

    this.declaration = this.buildDeclaration();
  }

  /**
   * Returns why the other options prevent the use of a reranker, or
   * `undefined` when the tool can use it.
   */
  private rerankerUnavailableReason(reranker: Reranker): string | undefined {
    if (reranker === "mmr" && this.mmrLambdaState.kind === "hidden") {
      return "'mmr' requires 'mmrLambda', but 'mmrLambda' is null";
    }
    if (
      reranker === "node_distance" &&
      this.centerNodeUuidState.kind === "hidden"
    ) {
      return "'node_distance' requires 'centerNodeUuid', but 'centerNodeUuid' is null";
    }
    if (
      reranker === "episode_mentions" &&
      !this.searchFilters?.episodeUuids?.length
    ) {
      return "'episode_mentions' requires 'searchFilters.episodeUuids'";
    }
    return undefined;
  }

  /** Throws when a pinned reranker cannot work with the other options. */
  private validatePinnedReranker(): void {
    if (this.rerankerState.kind !== "pinned") return;
    const reranker = this.rerankerState.value;
    if (!(RERANKER_ENUM as readonly string[]).includes(reranker)) {
      throw new Error(
        `Unsupported Zep graph search reranker: '${reranker}'. ` +
          `Supported rerankers are: ${RERANKER_ENUM.join(", ")}.`,
      );
    }
    const reason = this.rerankerUnavailableReason(reranker);
    if (reason !== undefined) {
      throw new Error(`Invalid Zep graph search options: ${reason}.`);
    }
    if (
      this.scopeState.kind === "pinned" &&
      !scopeSupportsReranker(this.scopeState.value, reranker)
    ) {
      const scope = this.scopeState.value as Exclude<
        ZepGraphSearchScope,
        "auto"
      >;
      throw new Error(
        `Invalid Zep graph search options: scope '${scope}' does not ` +
          `support reranker '${reranker}'. Rerankers for scope '${scope}' ` +
          `are: ${RERANKERS_BY_SCOPE[scope].join(", ")}.`,
      );
    }
  }

  /** The scopes that support the pinned reranker, or every scope. */
  private computeSelectableScopes(): readonly ZepGraphSearchScope[] {
    if (this.scopeState.kind === "pinned") return [this.scopeState.value];
    const pinned =
      this.rerankerState.kind === "pinned"
        ? this.rerankerState.value
        : undefined;
    if (pinned === undefined) return SCOPE_ENUM;
    return SCOPE_ENUM.filter((scope) => scopeSupportsReranker(scope, pinned));
  }

  /** The usable rerankers that a scope accepts. */
  private rerankersForScope(
    scope: Exclude<ZepGraphSearchScope, "auto">,
  ): Reranker[] {
    return this.usableRerankers.filter((reranker) =>
      RERANKERS_BY_SCOPE[scope].includes(reranker),
    );
  }

  /**
   * The rerankers that the model can select: the usable rerankers that at
   * least one selectable scope accepts.
   */
  private selectableRerankers(): Reranker[] {
    return this.usableRerankers.filter((reranker) =>
      this.selectableScopes.some(
        (scope) => scope !== "auto" && scopeSupportsReranker(scope, reranker),
      ),
    );
  }

  /**
   * Checks the resolved scope and reranker before the request. Returns an
   * error message for the model, or `undefined` when the pair is valid.
   */
  private validateSearchSettings(
    scope: ZepGraphSearchScope,
    reranker: Reranker | undefined,
    centerNodeUuid: string | undefined,
  ): string | undefined {
    if (scope === "auto" || reranker === undefined) return undefined;
    const reason = this.rerankerUnavailableReason(reranker);
    if (reason !== undefined) {
      return (
        `Error: reranker '${reranker}' is not available in this tool, ` +
        `because ${reason}. Use one of these rerankers for scope ` +
        `'${scope}': ${this.rerankersForScope(scope).join(", ")}.`
      );
    }
    if (!scopeSupportsReranker(scope, reranker)) {
      if (this.rerankerState.kind === "pinned") {
        return (
          `Error: scope '${scope}' does not support reranker ` +
          `'${reranker}', which this tool always uses. Use one of these ` +
          `scopes: ${this.selectableScopes.join(", ")}.`
        );
      }
      return (
        `Error: reranker '${reranker}' is not supported for scope ` +
        `'${scope}'. Use one of these rerankers for scope '${scope}': ` +
        `${this.rerankersForScope(scope).join(", ")}.`
      );
    }
    if (reranker === "node_distance" && centerNodeUuid === undefined) {
      const others = this.rerankersForScope(scope).filter(
        (other) => other !== "node_distance",
      );
      return (
        "Error: reranker 'node_distance' requires 'centerNodeUuid'. Give " +
        "the UUID of a graph node in 'centerNodeUuid', or use one of " +
        `these rerankers: ${others.join(", ")}.`
      );
    }
    return undefined;
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
        enum: [...this.selectableScopes],
      };
    }

    const rerankers = this.selectableRerankers();
    if (this.rerankerState.kind === "exposed" && rerankers.length > 0) {
      properties.reranker = {
        type: Type.STRING,
        description: this.rerankerDescription(rerankers),
        enum: rerankers,
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
          "Balance between diversity (0.0) and relevance (1.0). Only used " +
          `when reranker is 'mmr'. Defaults to ${DEFAULT_MMR_LAMBDA}.`,
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

  /** Describes each selectable reranker and the scopes that accept it. */
  private rerankerDescription(rerankers: readonly Reranker[]): string {
    const meaning: Record<Reranker, string> = {
      rrf: "balanced",
      mmr: "diverse",
      cross_encoder: "highest accuracy",
      episode_mentions: "frequently referenced",
      node_distance: "near the entity in centerNodeUuid, which is required",
    };
    const lines = rerankers.map((reranker) => {
      const scopes = this.selectableScopes.filter(
        (scope) => scope !== "auto" && scopeSupportsReranker(scope, reranker),
      );
      return `'${reranker}' (${meaning[reranker]}; scopes: ${scopes.join(", ")})`;
    });
    const autoNote = this.selectableScopes.includes("auto")
      ? " The 'auto' scope ignores the reranker."
      : "";
    return (
      `Result ordering algorithm. Each reranker works only with the listed scopes: ${lines.join(", ")}.` +
      autoNote
    );
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

    const resolvedScope = scope ?? DEFAULT_SCOPE;
    const settingsError = this.validateSearchSettings(
      resolvedScope,
      reranker,
      centerNodeUuid,
    );
    if (settingsError !== undefined) {
      this.logger.warn(`Zep graph search: ${settingsError}`);
      return settingsError;
    }

    const body: GraphScopeSearchBody = { query };
    if (reranker !== undefined) body.reranker = reranker;
    if (mmrLambda !== undefined) {
      body.mmrLambda = mmrLambda;
    } else if (reranker === "mmr") {
      body.mmrLambda = DEFAULT_MMR_LAMBDA;
    }
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

/**
 * The fields that every v4 scoped search method accepts. The five search
 * methods share these fields, and each method has its own filter type.
 */
export interface GraphScopeSearchBody {
  /** The search query. */
  query: string;
  /** The reranker. Each scope accepts only some rerankers. */
  reranker?: Reranker;
  /** The diversity weight for the `"mmr"` reranker. */
  mmrLambda?: number;
  /** The node to rank results by distance from. */
  centerNodeUuid?: string;
  /** Nodes used as BFS origins. */
  bfsOriginNodeUuids?: string[];
  /**
   * Search filters. Zep returns HTTP 400 `unsupported_filter` when a filter
   * key does not apply to the scope.
   */
  filters?: Zep.SearchFilters;
}

/** Parameters of a single scoped graph search. */
export interface GraphScopeSearchParams {
  /** The scope to search. */
  scope: ZepGraphSearchScope;
  /** The v4 search fields. The `"auto"` scope uses its `query` and `filters`. */
  body: GraphScopeSearchBody;
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

  const { filters, ...fields } = body;
  const request = { ...fields, pageSize: limit };
  const texts: string[] = [];

  if (scope === "edges") {
    const page = await zep.graph.searchEdges(graphUuid, {
      ...request,
      filters,
    });
    for (const edge of page.data) {
      if (edge.fact) texts.push(edge.fact);
    }
  } else if (scope === "nodes") {
    const page = await zep.graph.searchNodes(graphUuid, {
      ...request,
      filters,
    });
    for (const node of page.data) {
      const text = nameSummaryText(node.name, node.summary);
      if (text) texts.push(text);
    }
  } else if (scope === "episodes") {
    const page = await zep.graph.searchEpisodes(graphUuid, {
      ...request,
      filters,
    });
    for (const episode of page.data) {
      if (episode.content) texts.push(episode.content);
    }
  } else if (scope === "observations") {
    const page = await zep.graph.searchObservations(graphUuid, {
      ...request,
      filters: filters && { ...filters },
    });
    for (const observation of page.data) {
      const text = nameSummaryText(observation.name, observation.summary);
      if (text) texts.push(text);
    }
  } else {
    const page = await zep.graph.searchThreadSummaries(graphUuid, {
      ...request,
      filters,
    });
    for (const threadSummary of page.data) {
      const text = threadSummary.summary?.trim();
      if (text) texts.push(text);
    }
  }

  return texts;
}
