import { createTool } from "@mastra/core/tools";
import type { ZepClient, Zep } from "@getzep/zep-cloud";
import { z } from "zod";
import type { ZepBinding, ZepIdentityResolver, ZepLogger } from "./types.js";
import {
  errorMessage,
  resolveGraphUuid,
  resolveLogger,
  resolveToolIdentity,
} from "./zep-utils.js";

/**
 * The search scopes this tool supports, one for each scope-specific Zep v4
 * search method, plus `auto` for `graph.getContext`.
 *
 * Zep v4 replaced the single `graph.search` call and its `scope` argument
 * with one method for each result type. The tool keeps `scope` as a
 * model-visible parameter and dispatches to the matching method.
 */
const SCOPE_VALUES = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;

/** A search scope accepted by {@link createZepSearchTool}. */
export type ZepSearchScope = (typeof SCOPE_VALUES)[number];

/** Zep's supported rerankers, all five, in schema order. */
const RERANKER_VALUES = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const satisfies readonly Zep.V4SearchRequestReranker[];

type Reranker = Zep.V4SearchRequestReranker;

const DEFAULT_SCOPE: ZepSearchScope = "edges";
const DEFAULT_RERANKER: Reranker = "rrf";
const DEFAULT_LIMIT = 10;

/** Zep caps a search page at 50 results; a higher limit is rejected with a 400. */
const MAX_SEARCH_LIMIT = 50;

/**
 * Rerankers Zep rejects outright when `scope` is `"auto"` (context assembly
 * always uses RRF internally and ignores `reranker` entirely).
 */
const AUTO_INCOMPATIBLE_RERANKERS: readonly Reranker[] = [
  "node_distance",
  "episode_mentions",
];

/**
 * Every search parameter that can be pinned, hidden, or exposed to the model.
 * Keys match the Zep v4 SDK's search field names, except `scope`, which
 * selects the search method.
 */
export interface ZepSearchPinnableParams {
  scope?: ZepSearchScope;
  reranker?: Reranker;
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
  /** The graph to search, addressed by its UUID (`graphUuid`). */
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
   * the parameter is simply omitted from the search call, so Zep's own
   * server-side default applies.
   */
  hiddenParams?: Set<PinnableParamName>;
  /**
   * Optional Zep search filters (entity/edge types, properties, dates), sent
   * as the `filters` field of the v4 search body. Always constructor-only —
   * never exposed to the model — and applied whenever set.
   */
  filters?: Zep.SearchFilters;
  /**
   * Node UUIDs seeding a breadth-first search. Always constructor-only —
   * never exposed to the model — and applied whenever set.
   */
  bfsOriginNodeUuids?: string[];
  /**
   * Deprecated back-compat alias for `pinnedParams.scope`. If set, pins (and
   * hides) `scope`, same as passing it via `pinnedParams`.
   */
  scope?: ZepSearchScope;
  /** Deprecated back-compat alias for `pinnedParams.limit`. */
  limit?: number;
  /** Deprecated back-compat alias for `pinnedParams.reranker`. */
  reranker?: Reranker;
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
 * Run the Zep v4 search method for `scope` and extract human-readable strings
 * from the returned page.
 *
 * - `edges` → `graph.searchEdges`, facts on edges
 * - `nodes` → `graph.searchNodes`, "name: summary" for entity nodes
 * - `episodes` → `graph.searchEpisodes`, raw episode content
 * - `observations` → `graph.searchObservations`, "name: summary"
 * - `thread_summaries` → `graph.searchThreadSummaries`, summary text
 * - `auto` → `graph.getContext`, the assembled context block as one entry
 *
 * The switch is exhaustive over {@link ZepSearchScope}; the `never` default
 * makes a new scope a compile error rather than a silently-empty result.
 */
async function runSearch(
  client: ZepClient,
  graphUuid: string,
  scope: ZepSearchScope,
  body: Zep.SearchRequest,
  limit: number | undefined,
): Promise<string[]> {
  const page = limit !== undefined ? { limit } : {};
  switch (scope) {
    case "auto": {
      const request: Zep.GraphContextRequest = { query: body.query };
      if (body.filters !== undefined) request.filters = body.filters;
      const result = await client.graph.getContext(graphUuid, request);
      const ctx = result.context?.trim();
      return ctx ? [ctx] : [];
    }
    case "edges": {
      const result = await client.graph.searchEdges(graphUuid, { ...page, body });
      return result.data.map((e) => e.fact).filter(isNonEmpty);
    }
    case "nodes": {
      const result = await client.graph.searchNodes(graphUuid, { ...page, body });
      return result.data.map(nameAndSummary).filter(isNonEmpty);
    }
    case "episodes": {
      const result = await client.graph.searchEpisodes(graphUuid, { ...page, body });
      return result.data.map((e) => e.content).filter(isNonEmpty);
    }
    case "observations": {
      const result = await client.graph.searchObservations(graphUuid, { ...page, body });
      return result.data.map(nameAndSummary).filter(isNonEmpty);
    }
    case "thread_summaries": {
      const result = await client.graph.searchThreadSummaries(graphUuid, { ...page, body });
      return result.data.map((s) => s.summary?.trim()).filter(isNonEmpty);
    }
    default: {
      // Exhaustiveness guard: if a new scope is added above, this line fails
      // to compile until a branch handles it.
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
 * **Pin-or-expose.** Every search knob (`scope`, `reranker`, `limit`,
 * `mmrLambda`, `centerNodeUuid`) is exposed to the model in the tool's input
 * schema by default, with Zep's documented defaults (`scope: "edges"`,
 * `reranker: "rrf"`, `limit: 10`). Use `pinnedParams` to fix a parameter to a
 * constant value and remove it from the schema (the model can no longer
 * choose it); use `hiddenParams` to remove a parameter from the schema
 * *without* pinning it — Zep's own server-side default applies, and the
 * parameter is omitted from the SDK call entirely. `filters` and
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

  // Clamp a pinned limit to Zep's ceiling at construction so the call never 400s.
  if (pinned.limit !== undefined) {
    if (pinned.limit > MAX_SEARCH_LIMIT) {
      logger.warn(
        `[zep-search] Pinned limit ${pinned.limit} exceeds Zep ceiling ` +
          `${MAX_SEARCH_LIMIT}; clamping to ${MAX_SEARCH_LIMIT}.`,
      );
      pinned.limit = MAX_SEARCH_LIMIT;
    } else if (pinned.limit < 1) {
      pinned.limit = 1;
    }
  }

  // Auto scope rejects node_distance/episode_mentions and ignores reranker
  // entirely. If both are pinned, resolve the conflict once, here, so the
  // call path is always valid.
  if (pinned.scope === "auto" && pinned.reranker !== undefined) {
    if (AUTO_INCOMPATIBLE_RERANKERS.includes(pinned.reranker)) {
      logger.warn(
        `[zep-search] Reranker '${pinned.reranker}' is invalid for scope 'auto'; ` +
          "omitting reranker (auto search uses RRF).",
      );
    }
    delete pinned.reranker;
  }

  const hidden = new Set(options.hiddenParams ?? []);
  for (const name of hidden) {
    if (!(PINNABLE_PARAM_NAMES as readonly string[]).includes(name)) {
      throw new Error(
        `Unknown hiddenParams entry: '${name}'. Allowed: ${PINNABLE_PARAM_NAMES.join(", ")}`,
      );
    }
  }

  const scopeState = resolvePinState<ZepSearchScope>("scope", pinned, hidden);
  const rerankerState = resolvePinState<Reranker>("reranker", pinned, hidden);
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
    scope?: ZepSearchScope;
    reranker?: Reranker;
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

      const identity = await resolveToolIdentity(binding, resolveIdentity, context);
      const graphUuid = resolveGraphUuid({ graphUuid: identity.graphUuid });
      if (!graphUuid) {
        logger.warn("[zep-search] No graphUuid bound; skipping search.");
        return { facts: [], found: false };
      }

      const scope = resolveParam(scopeState, inputData.scope, DEFAULT_SCOPE);
      let reranker = resolveParam(rerankerState, inputData.reranker, DEFAULT_RERANKER);
      let limit = resolveParam(limitState, inputData.limit, DEFAULT_LIMIT);
      const mmrLambda = resolveParam(mmrLambdaState, inputData.mmrLambda);
      const centerNodeUuid = resolveParam(centerNodeUuidState, inputData.centerNodeUuid);

      // Auto search always uses RRF internally and ignores reranker entirely;
      // Zep rejects node_distance/episode_mentions outright. Omit the reranker
      // for auto scope, warning only when the value is one Zep would reject.
      if (scope === "auto" && reranker !== undefined) {
        if (AUTO_INCOMPATIBLE_RERANKERS.includes(reranker)) {
          logger.warn(
            `[zep-search] Reranker '${reranker}' is invalid for scope 'auto'; ` +
              "omitting reranker.",
          );
        }
        reranker = undefined;
      }

      // Clamp the effective limit to Zep's ceiling so the call never 400s.
      if (limit !== undefined) {
        limit = Math.min(MAX_SEARCH_LIMIT, Math.max(1, limit));
      }

      try {
        const body: Zep.SearchRequest = { query };
        if (reranker !== undefined) body.reranker = reranker;
        if (mmrLambda !== undefined) body.mmrLambda = mmrLambda;
        if (centerNodeUuid !== undefined) body.centerNodeUuid = centerNodeUuid;
        if (options.filters !== undefined) {
          body.filters = options.filters;
        }
        if (options.bfsOriginNodeUuids !== undefined) {
          body.bfsOriginNodeUuids = options.bfsOriginNodeUuids;
        }

        const facts = await runSearch(client, graphUuid, scope ?? DEFAULT_SCOPE, body, limit);
        return { facts, found: facts.length > 0 };
      } catch (error) {
        logger.warn(`[zep-search] Zep graph search failed: ${errorMessage(error)}`);
        return { facts: [], found: false };
      }
    },
  });
}
