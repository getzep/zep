import { defineTool } from "eve/tools";
import { z } from "zod";
import { getZepClient } from "../lib/zep-client";
import { truncateSearchQuery } from "../lib/zep-recall";

/**
 * Search the company-wide standalone graph.
 * graphUuid is pinned from env. The model never selects a graph.
 */
export default defineTool({
  description:
    "Search company-wide Acme knowledge (product policies, support hours, plans, refunds, status page). Use for org/shared facts, not personal user preferences.",
  inputSchema: z.object({
    query: z
      .string()
      .min(1)
      .max(500)
      .describe(
        "Short natural-language search query, e.g. refund policy or support hours",
      ),
    limit: z.number().int().min(1).max(20).optional().default(8),
  }),
  async execute({ query, limit }) {
    const graphUuid = process.env.ZEP_COMPANY_GRAPH_UUID?.trim();
    if (!graphUuid) throw new Error("ZEP_COMPANY_GRAPH_UUID is required");
    const searchQuery = truncateSearchQuery(query);

    const results = await getZepClient().graph.getContext(graphUuid, {
      query: searchQuery,
      maxCharacters: Math.min(50_000, Math.max(1_500, limit * 400)),
      includeResults: true,
    });

    return {
      graphUuid,
      query: searchQuery,
      context: results.context ?? null,
      results: results.results ?? [],
    };
  },
});
