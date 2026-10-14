import { getZepClient } from "./zep-client";

/** Zep truncates graph context queries at 400 characters. */
export const ZEP_SEARCH_QUERY_MAX_CHARS = 400;

/**
 * Turn-scoped system-instruction recall budget. The block is replaced each
 * turn, so it does not accumulate in session history.
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
 * Turn-relevant recall against a user graph.
 */
export async function searchUserMemory(options: {
  userUuid: string;
  query: string;
  maxCharacters?: number;
}): Promise<string | undefined> {
  const query = truncateSearchQuery(options.query);
  if (!query) return undefined;

  const search = await getZepClient().graph.getContext(options.userUuid, {
    query,
    maxCharacters: options.maxCharacters ?? 4000,
    includeResults: true,
  });

  return search.context?.trim() || undefined;
}
