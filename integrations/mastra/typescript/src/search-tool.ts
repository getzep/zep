import { createTool } from "@mastra/core/tools";
import type { ZepClient, Zep } from "@getzep/zep-cloud";
import { z } from "zod";
import type { ZepBinding, ZepLogger } from "./types.js";
import { errorMessage, resolveGraphTarget, resolveLogger } from "./zep-utils.js";

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
   * Fixed search scope. Defaults to `"edges"` (facts/relationships), which is
   * the most useful scope for an agent recalling discrete claims. Pinning a
   * scope hides it from the model so it cannot choose a less useful one.
   */
  scope?: Zep.GraphSearchScope;
  /** Maximum number of results to retrieve (Zep caps non-auto scopes at 50). */
  limit?: number;
  /** Optional reranker (default Zep RRF). */
  reranker?: Zep.Reranker;
  /** Optional Zep search filters (entity/edge types, properties, dates). */
  searchFilters?: Zep.SearchFilters;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

const inputSchema = z.object({
  query: z
    .string()
    .min(1)
    .max(400)
    .describe(
      "What to look up in long-term memory (max 400 characters). Phrase it as " +
        "the information you need, e.g. 'where the user lives'.",
    ),
});

const outputSchema = z.object({
  facts: z
    .array(z.string())
    .describe("Relevant facts retrieved from the knowledge graph."),
  found: z.boolean().describe("Whether any relevant memory was found."),
});

type SearchInput = z.infer<typeof inputSchema>;
type SearchOutput = z.infer<typeof outputSchema>;

/**
 * Extract human-readable strings from a Zep search result for the active scope.
 *
 * Edges → facts, nodes → "name: summary", episodes → raw content, and `auto`
 * returns the materialized context block as a single entry.
 */
function extractResults(result: Zep.GraphSearchResults, scope: Zep.GraphSearchScope): string[] {
  if (scope === "auto") {
    const ctx = result.context?.trim();
    return ctx ? [ctx] : [];
  }
  if (scope === "nodes") {
    return (result.nodes ?? [])
      .map((n) => (n.summary ? `${n.name}: ${n.summary}` : n.name))
      .filter((s): s is string => Boolean(s));
  }
  if (scope === "episodes") {
    return (result.episodes ?? [])
      .map((e) => e.content)
      .filter((s): s is string => Boolean(s));
  }
  // Default + "edges": facts on edges.
  return (result.edges ?? [])
    .map((e) => e.fact)
    .filter((s): s is string => Boolean(s));
}

/**
 * Build a model-callable Mastra tool that **searches** the bound Zep graph and
 * returns relevant facts.
 *
 * Unlike {@link createZepContextTool} (which retrieves the whole-user-graph
 * Context Block in one shot), this tool exposes a free-text `query` so the model
 * can decide *when* and *what* to look up — ideal for targeted recall during a
 * tool-use loop. The scope and other retrieval parameters are pinned at
 * construction time and hidden from the model.
 *
 * A Zep failure is logged and returned as `found: false` with an empty list; it
 * never throws.
 */
export function createZepSearchTool(options: ZepSearchToolOptions) {
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);
  const target = resolveGraphTarget(binding);
  const scope: Zep.GraphSearchScope = options.scope ?? "edges";

  return createTool({
    id: options.id ?? "zep-search",
    description:
      options.description ??
      "Search long-term memory for facts about the user or domain that were " +
        "learned in previous turns or conversations. Use this to recall " +
        "specific details the user shared before.",
    inputSchema,
    outputSchema,
    execute: async (inputData: SearchInput): Promise<SearchOutput> => {
      const query = inputData.query?.trim();
      if (!query) {
        return { facts: [], found: false };
      }
      if (!target) {
        logger.warn("[zep-search] No userId or graphId bound; skipping search.");
        return { facts: [], found: false };
      }

      try {
        const searchRequest: Zep.GraphSearchQuery = {
          ...target,
          query,
          scope,
          ...(options.limit !== undefined ? { limit: options.limit } : {}),
          ...(options.reranker !== undefined ? { reranker: options.reranker } : {}),
          ...(options.searchFilters !== undefined
            ? { searchFilters: options.searchFilters }
            : {}),
        };
        const result = await client.graph.search(searchRequest);
        const facts = extractResults(result, scope);
        return { facts, found: facts.length > 0 };
      } catch (error) {
        logger.warn(`[zep-search] Zep graph search failed: ${errorMessage(error)}`);
        return { facts: [], found: false };
      }
    },
  });
}
