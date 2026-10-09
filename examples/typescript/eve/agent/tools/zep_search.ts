import { defineTool } from "eve/tools";
import { z } from "zod";
import { resolveZepIdentity } from "../lib/identity";
import { getZepClient } from "../lib/zep-client";
import { ensureZepUserAndThread } from "../lib/zep-memory";
import { truncateSearchQuery } from "../lib/zep-recall";

/**
 * On-demand graph search (Zep Pattern 3). The graph UUID is pinned from session
 * auth — the model never chooses whose memory to search.
 */
export default defineTool({
  description:
    "Search the current user's Zep memory graph for facts, preferences, and past context. Use when the turn's Zep memory section is missing or incomplete.",
  inputSchema: z.object({
    query: z
      .string()
      .min(1)
      .max(500)
      .describe("Short natural-language search query, e.g. preferred billing service"),
    limit: z.number().int().min(1).max(20).optional().default(8),
  }),
  async execute({ query, limit }, ctx) {
    const identity = resolveZepIdentity(ctx);
    const { graphUuid } = await ensureZepUserAndThread(identity);
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
