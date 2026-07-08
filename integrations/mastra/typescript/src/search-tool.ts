import { createTool } from "@mastra/core/tools";
import type { ZepClient, Zep } from "@getzep/zep-cloud";
import { z } from "zod";
import type { ZepBinding, ZepIdentityResolver, ZepLogger } from "./types.js";
import {
  errorMessage,
  resolveGraphTarget,
  resolveLogger,
  resolveToolIdentity,
} from "./zep-utils.js";

/** Zep's supported search scopes (`GraphSearchScope`), all six, in schema order. */
const SCOPE_VALUES = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const satisfies readonly Zep.GraphSearchScope[];

/** Zep's supported rerankers (`Reranker`), all five, in schema order. */
const RERANKER_VALUES = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const satisfies readonly Zep.Reranker[];

const DEFAULT_SCOPE: Zep.GraphSearchScope = "edges";
const DEFAULT_RERANKER: Zep.Reranker = "rrf";
const DEFAULT_LIMIT = 10;

/**
 * Every `graph.search` parameter that can be pinned, hidden, or exposed to
 * the model. Keys match the Zep SDK's `graph.search()` field names.
 */
export interface ZepSearchPinnableParams {
  scope?: Zep.GraphSearchScope;
  reranker?: Zep.Reranker;
  limit?: number;
  mmrLambda?: number;
  centerNodeUuid?: string;
}

const PINNABLE_PARAM_NAMES = [
  "scope",
  "reranker",
  "limit",
  "mmrLambda",
  "centerNodeUuid",
] as const;
type PinnableParamName = (typeof PINNABLE_PARAM_NAMES)[number];

/** Options for {@link createZepSearchTool}. */
export interface ZepSearchToolOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /** The graph to search — a user graph (`userId`) or standalone graph (`graphId`). */
  binding: ZepBinding;
  /** Override the tool id (default `"zep-search"`). */
  id?: string;
  /** Override the tool description shown to the model. */
  description?: string;
  /**
   * Pin one or more of `scope`, `reranker`, `limit`, `mmrLambda`,
   * `centerNodeUuid` to a fixed value. Pinned parameters are removed from the
   * model's tool schema and always sent with the given value, regardless of
   * what the model would otherwise choose.
   */
  pinnedParams?: ZepSearchPinnableParams;
  /**
   * Remove one or more of `scope`, `reranker`, `limit`, `mmrLambda`,
   * `centerNodeUuid` from the model's tool schema *without* pinning them —
   * the parameter is simply omitted from the `graph.search` call, so Zep's
   * own server-side default applies.
   */
  hiddenParams?: Set<PinnableParamName>;
  /**
   * Optional Zep search filters (entity/edge types, properties, dates).
   * Always constructor-only — never exposed to the model — and applied
   * whenever set.
   */
  searchFilters?: Zep.SearchFilters;
  /**
   * Node UUIDs seeding a breadth-first search. Always constructor-only —
   * never exposed to the model — and applied whenever set.
   */
  bfsOriginNodeUuids?: string[];
  /**
   * Deprecated back-compat alias for `pinnedParams.scope`. If set, pins (and
   * hides) `scope`, same as passing it via `pinnedParams`.
   */
  scope?: Zep.GraphSearchScope;
  /** Deprecated back-compat alias for `pinnedParams.limit`. */
  limit?: number;
  /** Deprecated back-compat alias for `pinnedParams.reranker`. */
  reranker?: Zep.Reranker;
  /**
   * Resolve the search target per call from the tool's `requestContext`,
   * overriding the constructor-bound `binding`. Return `undefined` (or omit
   * `userId`) to fall back to `binding`.
   */
  resolveIdentity?: ZepIdentityResolver;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

const outputSchema = z.object({
  facts: z
    .array(z.string())
    .describe("Relevant facts retrieved from the knowledge graph."),
  found: z.boolean().describe("Whether any relevant memory was found."),
});

type SearchOutput = z.infer<typeof outputSchema>;

/** Format a node-like result ("name: summary" or just "name"). */
function nameAndSummary(n: { name?: string; summary?: string }): string | undefined {
  if (!n.name) return undefined;
  return n.summary ? `${n.name}: ${n.summary}` : n.name;
}

const isNonEmpty = (s: string | undefined): s is string => Boolean(s);

/**
 * Extract human-readable strings from a Zep search result for the active scope.
 *
 * - `edges` → facts on edges
 * - `nodes` → "name: summary" for entity nodes
 * - `episodes` → raw episode content
 * - `thread_summaries` → "name: summary" for thread summary nodes
 * - `observations` → "name: summary" for derived observation nodes
 * - `auto` → the materialized context block as a single entry
 *
 * The switch is exhaustive over every {@link Zep.GraphSearchScope} the public
 * type allows; the `never` default makes a new SDK scope a compile error rather
 * than a silently-empty result.
 */
function extractResults(result: Zep.GraphSearchResults, scope: Zep.GraphSearchScope): string[] {
  switch (scope) {
    case "auto": {
      const ctx = result.context?.trim();
      return ctx ? [ctx] : [];
    }
    case "edges":
      return (result.edges ?? []).map((e) => e.fact).filter(isNonEmpty);
    case "nodes":
      return (result.nodes ?? []).map(nameAndSummary).filter(isNonEmpty);
    case "episodes":
      return (result.episodes ?? []).map((e) => e.content).filter(isNonEmpty);
    case "thread_summaries":
      return (result.threadSummaries ?? []).map(nameAndSummary).filter(isNonEmpty);
    case "observations":
      return (result.observations ?? []).map(nameAndSummary).filter(isNonEmpty);
    default: {
      // Exhaustiveness guard: if a new GraphSearchScope is added to the SDK,
      // this line fails to compile until a branch above handles it.
      const _exhaustive: never = scope;
      return _exhaustive;
    }
  }
}

/** Tri-state resolution for a single pinnable/exposable parameter. */
type PinState<T> = { kind: "pinned"; value: T } | { kind: "hidden" } | { kind: "exposed" };

function resolvePinState<T>(
  name: PinnableParamName,
  pinned: ZepSearchPinnableParams,
  hidden: Set<PinnableParamName>,
): PinState<T> {
  const value = pinned[name as keyof ZepSearchPinnableParams] as T | undefined;
  if (value !== undefined) return { kind: "pinned", value };
  if (hidden.has(name)) return { kind: "hidden" };
  return { kind: "exposed" };
}

/**
 * Build a model-callable Mastra tool that **searches** the bound Zep graph and
 * returns relevant facts.
 *
 * Unlike {@link createZepContextTool} (which retrieves the whole-user-graph
 * Context Block in one shot), this tool exposes a free-text `query` so the
 * model can decide *when* and *what* to look up — ideal for targeted recall
 * during a tool-use loop.
 *
 * **Pin-or-expose.** Every `graph.search` knob (`scope`, `reranker`, `limit`,
 * `mmrLambda`, `centerNodeUuid`) is exposed to the model in the tool's input
 * schema by default, with Zep's documented defaults (`scope: "edges"`,
 * `reranker: "rrf"`, `limit: 10`). Use `pinnedParams` to fix a parameter to a
 * constant value and remove it from the schema (the model can no longer
 * choose it); use `hiddenParams` to remove a parameter from the schema
 * *without* pinning it — Zep's own server-side default applies, and the
 * parameter is omitted from the SDK call entirely. `searchFilters` and
 * `bfsOriginNodeUuids` are always constructor-only.
 *
 * A Zep failure is logged and returned as `found: false` with an empty list;
 * it never throws.
 */
export function createZepSearchTool(options: ZepSearchToolOptions) {
  const { client, binding, resolveIdentity } = options;
  const logger = resolveLogger(options.logger);

  const pinned: ZepSearchPinnableParams = { ...options.pinnedParams };
  // Legacy constructor args pin (and thus hide) their parameter, same as
  // passing it via pinnedParams — back-compat for the pre-pin-or-expose API.
  if (options.scope !== undefined) pinned.scope ??= options.scope;
  if (options.reranker !== undefined) pinned.reranker ??= options.reranker;
  if (options.limit !== undefined) pinned.limit ??= options.limit;

  const hidden = new Set(options.hiddenParams ?? []);
  for (const name of hidden) {
    if (!(PINNABLE_PARAM_NAMES as readonly string[]).includes(name)) {
      throw new Error(
        `Unknown hiddenParams entry: '${name}'. Allowed: ${PINNABLE_PARAM_NAMES.join(", ")}`,
      );
    }
  }

  const scopeState = resolvePinState<Zep.GraphSearchScope>("scope", pinned, hidden);
  const rerankerState = resolvePinState<Zep.Reranker>("reranker", pinned, hidden);
  const limitState = resolvePinState<number>("limit", pinned, hidden);
  const mmrLambdaState = resolvePinState<number>("mmrLambda", pinned, hidden);
  const centerNodeUuidState = resolvePinState<string>("centerNodeUuid", pinned, hidden);

  const schemaFields: Record<string, z.ZodTypeAny> = {
    query: z
      .string()
      .min(1)
      .max(400)
      .describe(
        "What to look up in long-term memory (max 400 characters). Phrase it as " +
          "the information you need, e.g. 'where the user lives'.",
      ),
  };

  if (scopeState.kind === "exposed") {
    schemaFields.scope = z
      .enum(SCOPE_VALUES)
      .optional()
      .describe(
        "What to search for: 'edges' for facts and relationships, 'nodes' for " +
          "entities and their summaries, 'episodes' for raw text data " +
          "(unstructured text, messages, or JSON), 'observations' for derived " +
          "memories, 'thread_summaries' for incremental thread summaries, " +
          "'auto' to let Zep decide the best mix of results. Defaults to 'edges'.",
      );
  }
  if (rerankerState.kind === "exposed") {
    schemaFields.reranker = z
      .enum(RERANKER_VALUES)
      .optional()
      .describe(
        "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), " +
          "'cross_encoder' (highest accuracy), 'episode_mentions' (frequently " +
          "referenced), 'node_distance' (near a specific entity). Defaults to 'rrf'.",
      );
  }
  if (limitState.kind === "exposed") {
    schemaFields.limit = z
      .number()
      .int()
      .optional()
      .describe("Maximum number of results to return. Defaults to 10 (Zep caps at 50).");
  }
  if (mmrLambdaState.kind === "exposed") {
    schemaFields.mmrLambda = z
      .number()
      .optional()
      .describe(
        "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'.",
      );
  }
  if (centerNodeUuidState.kind === "exposed") {
    schemaFields.centerNodeUuid = z
      .string()
      .optional()
      .describe(
        "UUID of the center node for distance-based reranking. Required when " +
          "reranker is 'node_distance'.",
      );
  }

  const inputSchema = z.object(schemaFields);
  type SearchInput = z.infer<typeof inputSchema> & {
    scope?: Zep.GraphSearchScope;
    reranker?: Zep.Reranker;
    limit?: number;
    mmrLambda?: number;
    centerNodeUuid?: string;
  };

  /** Pinned beats model-provided beats default (or "unset" when there is none). */
  function resolveParam<T>(
    state: PinState<T>,
    modelValue: T | undefined,
    fallbackDefault?: T,
  ): T | undefined {
    if (state.kind === "pinned") return state.value;
    if (state.kind === "hidden") return undefined;
    return modelValue ?? fallbackDefault;
  }

  return createTool({
    id: options.id ?? "zep-search",
    description:
      options.description ??
      "Search long-term memory for facts about the user or domain that were " +
        "learned in previous turns or conversations. Use this to recall " +
        "specific details the user shared before.",
    inputSchema,
    outputSchema,
    execute: async (
      inputData: SearchInput,
      context?: { requestContext?: unknown },
    ): Promise<SearchOutput> => {
      const query = inputData.query?.trim();
      if (!query) {
        return { facts: [], found: false };
      }

      const identity = resolveToolIdentity(binding, resolveIdentity, context);
      const target = resolveGraphTarget({ userId: identity.userId, graphId: binding.graphId });
      if (!target) {
        logger.warn("[zep-search] No userId or graphId bound; skipping search.");
        return { facts: [], found: false };
      }

      const scope = resolveParam(scopeState, inputData.scope, DEFAULT_SCOPE);
      const reranker = resolveParam(rerankerState, inputData.reranker, DEFAULT_RERANKER);
      const limit = resolveParam(limitState, inputData.limit, DEFAULT_LIMIT);
      const mmrLambda = resolveParam(mmrLambdaState, inputData.mmrLambda);
      const centerNodeUuid = resolveParam(centerNodeUuidState, inputData.centerNodeUuid);

      try {
        const searchRequest: Zep.GraphSearchQuery = { ...target, query };
        if (scope !== undefined) searchRequest.scope = scope;
        if (reranker !== undefined) searchRequest.reranker = reranker;
        if (limit !== undefined) searchRequest.limit = limit;
        if (mmrLambda !== undefined) searchRequest.mmrLambda = mmrLambda;
        if (centerNodeUuid !== undefined) searchRequest.centerNodeUuid = centerNodeUuid;
        if (options.searchFilters !== undefined) {
          searchRequest.searchFilters = options.searchFilters;
        }
        if (options.bfsOriginNodeUuids !== undefined) {
          searchRequest.bfsOriginNodeUuids = options.bfsOriginNodeUuids;
        }

        const result = await client.graph.search(searchRequest);
        const facts = extractResults(result, scope ?? DEFAULT_SCOPE);
        return { facts, found: facts.length > 0 };
      } catch (error) {
        logger.warn(`[zep-search] Zep graph search failed: ${errorMessage(error)}`);
        return { facts: [], found: false };
      }
    },
  });
}
