import { defineTool } from "eve/tools";
import { z } from "zod";
import { getZepClient } from "../lib/zep-client";
import { truncateSearchQuery } from "../lib/zep-recall";

/**
 * Search the company-wide standalone graph.
 * The graph UUID is pinned from env — never accept a graph UUID from the model.
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
    if (!graphUuid) {
      throw new Error(
        "ZEP_COMPANY_GRAPH_UUID is missing. Run `npm run seed:company` and put the graph UUID in .env.",
      );
    }
    const searchQuery = truncateSearchQuery(query);

    const results = await getZepClient().graph.getContext(graphUuid, {
      query: searchQuery,
      maxCharacters: Math.min(50_000, Math.max(1_500, limit * 400)),
      includeResults: true,
    });

    const facts = (results.results?.edges ?? []).map((edge) => ({
      fact: edge.fact,
      validAt: edge.validAt ?? null,
      invalidAt: edge.invalidAt ?? null,
    }));

    return {
      graphUuid,
      query: searchQuery,
      context: results.context ?? null,
      facts,
    };
  },
});
