import type { StructuredToolInterface } from "@langchain/core/tools";
import { TavilySearchResults } from "@langchain/community/tools/tavily_search";

/**
 * Build agent tools. Tavily web search is optional — omit TAVILY_API_KEY to
 * run help and non-search (memory) turns without it.
 */
export function buildTools(
  env: NodeJS.ProcessEnv = process.env,
): StructuredToolInterface[] {
  const apiKey = env.TAVILY_API_KEY?.trim();
  if (!apiKey) {
    return [];
  }

  return [
    new TavilySearchResults({
      maxResults: 3,
      apiKey,
    }),
  ];
}
