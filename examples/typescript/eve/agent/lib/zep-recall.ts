import { getZepClient } from "./zep-client";

/** Zep truncates graph.search queries at 400 characters. */
export const ZEP_SEARCH_QUERY_MAX_CHARS = 400;

/**
 * Auto-context budget for turn-scoped system-instruction recall.
 * Replaced each `turn.started`, so it does not accumulate across turns.
 */
export const INSTRUCTION_RECALL_MAX_CHARS = 4000;

/**
 * Trim a search query to Zep's documented max length.
 */
export function truncateSearchQuery(
  query: string,
  maxChars = ZEP_SEARCH_QUERY_MAX_CHARS,
): string {
  const trimmed = query.trim();
  if (trimmed.length <= maxChars) return trimmed;
  return trimmed.slice(0, maxChars).trimEnd();
}

/**
 * Turn-relevant recall against a user graph (Zep Pattern 3 / advanced context).
 * Prefer this when you have the current user utterance — `getUserContext` only
 * queries from messages already stored on the Zep thread.
 */
export async function searchUserMemory(options: {
  userId: string;
  query: string;
  maxCharacters?: number;
}): Promise<string | undefined> {
  const query = truncateSearchQuery(options.query);
  if (!query) return undefined;

  const search = await getZepClient().graph.search({
    userId: options.userId,
    query,
    scope: "auto",
    maxCharacters: options.maxCharacters ?? 4000,
  });

  if (search.context?.trim()) return search.context.trim();

  const facts = (search.edges ?? [])
    .map((e) => e.fact?.trim())
    .filter((f): f is string => Boolean(f));
  if (facts.length === 0) return undefined;
  return facts.map((f) => `- ${f}`).join("\n");
}
