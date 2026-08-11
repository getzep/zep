import { defineTool } from "eve/tools";
import { z } from "zod";
import { getZepClient } from "../lib/zep-client";
import { truncateSearchQuery } from "../lib/zep-recall";

/**
 * Search the company-wide standalone graph.
 * graphId is pinned from env — never accept a graph id from the model.
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
    const graphId =
      process.env.ZEP_COMPANY_GRAPH_ID?.trim() || "eve-demo-company";
    const searchQuery = truncateSearchQuery(query);

    const results = await getZepClient().graph.search({
      graphId,
      query: searchQuery,
      // auto: hybrid recall + composed context (`limit` is ignored for auto)
      scope: "auto",
      maxCharacters: Math.min(50_000, Math.max(1_500, limit * 400)),
      returnRawResults: true,
    });

    const facts = (results.edges ?? []).map((edge) => ({
      fact: edge.fact,
      validAt: edge.validAt ?? null,
      invalidAt: edge.invalidAt ?? null,
    }));

    return {
      graphId,
      query: searchQuery,
      context: results.context ?? null,
      facts,
    };
  },
});
