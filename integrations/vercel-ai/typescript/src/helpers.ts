import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { ZepLogger, ZepTurn } from "./types.js";
import {
  MESSAGE_MAX_CHARS,
  errorMessage,
  resolveLogger,
  truncateForZep,
} from "./zep-utils.js";

/**
 * Retrieve the prompt-ready **Context Block** for a thread via
 * `thread.getUserContext`.
 *
 * The Context Block is an optimized string (user summary + relevant facts and
 * entities) assembled from the *whole* user graph, with the thread's most recent
 * messages used only to focus relevance. Inject it into a system message on
 * every turn.
 *
 * This is the plain-function counterpart to {@link createZepMiddleware} — use it
 * with `streamText`/`generateText` when you want to set `system:` yourself:
 *
 * ```ts
 * const context = await getZepContext(client, threadId);
 * const result = streamText({
 *   model,
 *   system: context ? `Relevant context:\n${context}` : undefined,
 *   messages,
 * });
 * ```
 *
 * A Zep failure is logged (no PII) and returns an empty string; it never throws.
 *
 * @param client - A shared, initialized Zep client. The caller owns its lifecycle.
 * @param threadId - The Zep thread whose user context to fetch.
 * @param options - Optional `templateId` for custom Context Block formatting and
 *   a `logger` (defaults to `console`).
 * @returns The Context Block string, or `""` when unavailable.
 */
export async function getZepContext(
  client: ZepClient,
  threadId: string,
  options?: { templateId?: string; logger?: ZepLogger },
): Promise<string> {
  const logger = resolveLogger(options?.logger);
  if (!threadId) {
    logger.warn("[zep-context] No threadId provided; skipping context retrieval.");
    return "";
  }

  try {
    const response = await client.thread.getUserContext(
      threadId,
      options?.templateId ? { templateId: options.templateId } : {},
    );
    return response.context?.trim() ?? "";
  } catch (error) {
    logger.warn(`[zep-context] Failed to retrieve Zep context: ${errorMessage(error)}`);
    return "";
  }
}

/**
 * Persist a user/assistant turn to Zep via `thread.addMessages`.
 *
 * This both records conversation history and ingests the turn into the bound
 * user graph. Use it from `streamText`/`generateText`'s `onFinish` callback
 * (the recommended persistence path for streaming, where middleware
 * `wrapGenerate` does not fire):
 *
 * ```ts
 * const result = streamText({
 *   model,
 *   system: await getZepContext(client, threadId),
 *   messages,
 *   onFinish: ({ text }) => {
 *     void persistZepTurn(client, threadId, { user: userInput, assistant: text });
 *   },
 * });
 * ```
 *
 * Over-long content is truncated to Zep's 4,096-char message limit with a
 * warning that logs **lengths only** (never content). A Zep failure is logged
 * and reported via the boolean return value rather than thrown.
 *
 * @param client - A shared, initialized Zep client.
 * @param threadId - The Zep thread to append messages to.
 * @param turn - The user and/or assistant content to persist.
 * @param options - Optional `returnContext` (fold retrieval into the same
 *   round-trip) and a `logger`.
 * @returns The Context Block if `returnContext` was set and the call succeeded,
 *   otherwise `null`. A `null` return after a failure is logged.
 */
export async function persistZepTurn(
  client: ZepClient,
  threadId: string,
  turn: ZepTurn,
  options?: { returnContext?: boolean; logger?: ZepLogger },
): Promise<string | null> {
  const logger = resolveLogger(options?.logger);
  if (!threadId) {
    logger.warn("[zep-persist] No threadId provided; skipping persist.");
    return null;
  }

  const messages: Zep.Message[] = [];
  const user = turn.user?.trim();
  const assistant = turn.assistant?.trim();

  if (user) {
    messages.push({
      role: "user",
      content: truncateForZep(user, MESSAGE_MAX_CHARS, "zep-persist", logger),
      ...(turn.userName !== undefined ? { name: turn.userName } : {}),
    });
  }
  if (assistant) {
    messages.push({
      role: "assistant",
      content: truncateForZep(assistant, MESSAGE_MAX_CHARS, "zep-persist", logger),
      ...(turn.assistantName !== undefined ? { name: turn.assistantName } : {}),
    });
  }

  if (messages.length === 0) {
    logger.debug?.("[zep-persist] Nothing to persist (empty user and assistant).");
    return null;
  }

  try {
    const response = await client.thread.addMessages(threadId, {
      messages,
      ...(options?.returnContext ? { returnContext: true } : {}),
    });
    return response.context?.trim() ?? null;
  } catch (error) {
    logger.warn(`[zep-persist] Failed to persist turn to Zep: ${errorMessage(error)}`);
    return null;
  }
}

/** Options for {@link ensureZepUserAndThread}. */
export interface EnsureIdentityOptions {
  /** A shared, initialized Zep client. */
  client: ZepClient;
  /** The Zep user ID. */
  userId: string;
  /** The Zep thread ID to create for this conversation. */
  threadId: string;
  /** User's first name — pass a real name to help Zep resolve identity. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
  /** User's email. */
  email?: string;
  /** Logger for failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * Idempotently create the Zep user and thread for a conversation.
 *
 * Zep requires the user and thread to exist before messages are added. Call this
 * once, out-of-band, before the first turn (the Zep "create user → create thread"
 * step). Already-existing resources are treated as success. Failures are logged
 * (no PII) and reported via the return value rather than thrown.
 *
 * @returns `true` if the user and thread are ready, `false` if setup failed.
 */
export async function ensureZepUserAndThread(
  options: EnsureIdentityOptions,
): Promise<boolean> {
  const { client, userId, threadId } = options;
  const logger = resolveLogger(options.logger);

  try {
    try {
      await client.user.add({
        userId,
        ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
        ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
        ...(options.email !== undefined ? { email: options.email } : {}),
      });
    } catch (error) {
      // A 409/duplicate means the user already exists — that's fine. Re-raise
      // only if a subsequent thread.create also fails.
      logger.debug?.(`[zep] user.add: ${errorMessage(error)} (may already exist)`);
    }

    await client.thread.create({ threadId, userId });
    return true;
  } catch (error) {
    const message = errorMessage(error);
    // Treat "already exists" style conflicts as success.
    if (/exist|conflict|409|duplicate/i.test(message)) {
      return true;
    }
    logger.warn(`[zep] Failed to ensure user/thread: ${message}`);
    return false;
  }
}
