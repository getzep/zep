/**
 * Zep size-limit handling.
 *
 * Zep rejects over-long payloads with a 400:
 *   - A thread message's `content` must be <= 4,096 characters.
 *   - `graph.add` data must be <= 10,000 characters.
 *
 * Rather than letting an over-long message 400 and be silently swallowed, we
 * truncate it (preferring a safety margin under the hard limit) and log a
 * warning. The warning carries ONLY lengths — never message content — so no
 * user text or PII reaches the logs.
 */

import type { Logger } from "./logging.js";

/** Hard Zep limit on a thread message's `content`, in characters. */
export const MESSAGE_CONTENT_MAX = 4096;

/**
 * Target length we truncate over-long messages to. Kept under
 * {@link MESSAGE_CONTENT_MAX} to leave headroom for any server-side encoding
 * differences.
 */
export const MESSAGE_CONTENT_TRUNCATE_TO = 4000;

/** Hard Zep limit on `graph.add` data, in characters. */
export const GRAPH_DATA_MAX = 10000;

/**
 * Truncate a thread-message content string to Zep's message limit, logging a
 * warning (lengths only — never content) when truncation happens.
 *
 * Content within the limit is returned unchanged. Over-long content is
 * truncated to {@link MESSAGE_CONTENT_TRUNCATE_TO} characters so the message is
 * persisted rather than dropped on a 400.
 *
 * @param content The message content to bound.
 * @param logger Logger used for the truncation warning.
 * @param label A short, non-PII label for the message (e.g. `"user"`,
 *   `"assistant"`) included in the warning to aid debugging.
 * @returns The original content, or a truncated copy when it exceeds the limit.
 */
export function truncateMessageContent(
  content: string,
  logger: Logger,
  label = "message",
): string {
  if (content.length <= MESSAGE_CONTENT_MAX) {
    return content;
  }
  const truncated = content.slice(0, MESSAGE_CONTENT_TRUNCATE_TO);
  logger.warn(
    `Truncated ${label} content before persisting to Zep: ` +
      `original ${content.length} chars exceeds the ${MESSAGE_CONTENT_MAX}-char ` +
      `message limit; truncated to ${truncated.length} chars.`,
  );
  return truncated;
}
