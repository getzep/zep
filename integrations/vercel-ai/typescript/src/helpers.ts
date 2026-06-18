import { Zep } from "@getzep/zep-cloud";
import type { ZepClient } from "@getzep/zep-cloud";
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
 * user graph. It is the building block behind {@link createZepOnFinish}; reach
 * for it directly when you want to persist a turn by hand (e.g. inside your own
 * `onFinish`, where `text` is the final assistant text for the whole turn):
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
 * For the common case, prefer {@link createZepOnFinish} — it builds this
 * callback for you and persists the whole turn exactly once.
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

/**
 * The minimal shape we read off the AI SDK `onFinish` event: the final,
 * aggregated assistant text for the whole turn. Both `generateText` and
 * `streamText` pass an `OnFinishEvent` that carries this (and much more); we
 * intentionally depend only on `text` so the callback works for both without
 * coupling to the SDK's heavy generic event type.
 */
interface ZepOnFinishEvent {
  /** The final assistant text for the turn (aggregated across all steps). */
  readonly text: string;
}

/** Options for {@link createZepOnFinish}. */
export interface ZepOnFinishOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /** The Zep thread that receives the persisted turn. */
  threadId: string;
  /**
   * The user ID for the turn. Optional and not required for persistence
   * (`thread.addMessages` is scoped by `threadId`); accepted for symmetry with
   * the rest of the API and for callers that want it in scope.
   */
  userId?: string;
  /**
   * The user's input for this turn — the `onFinish` event carries only the
   * assistant text, so supply the user side here. Pass the string directly, or
   * a resolver if you build the callback once and reuse it across turns. When
   * omitted, only the assistant message is persisted.
   */
  user?: string | ((event: ZepOnFinishEvent) => string | undefined);
  /** Speaker name recorded on the persisted user message (the user's real name). */
  userName?: string;
  /** Name recorded on the persisted assistant message. */
  assistantName?: string;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * Build an AI SDK `onFinish` callback that persists the **whole turn once** to
 * Zep — the user's input plus the final assistant text from the event.
 *
 * This is the verified-correct persistence path for the middleware pattern.
 * `onFinish` fires exactly **once per turn** with the final aggregated
 * assistant text (after the entire tool loop completes) for **both**
 * `generateText` and `streamText`. Persisting here — rather than from a
 * per-step middleware hook — records exactly one user message and one assistant
 * message per turn, and never writes the model's intermediate tool-call
 * preamble into the graph.
 *
 * ```ts
 * const userInput = "What do you know about me?";
 * const { text } = await generateText({
 *   model,
 *   prompt: userInput,
 *   stopWhen: stepCountIs(5),
 *   onFinish: createZepOnFinish({ client, threadId, user: userInput, userName: "Jane" }),
 * });
 * ```
 *
 * Persistence is fire-and-forget at the call site (the returned callback awaits
 * {@link persistZepTurn} internally, which never throws): a Zep outage degrades
 * to "turn not persisted" and never crashes or blocks the host call. Over-long
 * content is truncated to Zep's 4,096-char message limit with a lengths-only
 * warning.
 *
 * @param options - The client, thread, and how to source the user message.
 * @returns An `onFinish` callback for `generateText`/`streamText`.
 */
export function createZepOnFinish(
  options: ZepOnFinishOptions,
): (event: ZepOnFinishEvent) => Promise<void> {
  const { client, threadId } = options;
  const logger = resolveLogger(options.logger);

  return async (event: ZepOnFinishEvent): Promise<void> => {
    const assistant = event.text?.trim();
    const user =
      typeof options.user === "function" ? options.user(event)?.trim() : options.user?.trim();

    if (!user && !assistant) return;

    await persistZepTurn(
      client,
      threadId,
      {
        ...(user ? { user } : {}),
        ...(assistant ? { assistant } : {}),
        ...(options.userName !== undefined ? { userName: options.userName } : {}),
        ...(options.assistantName !== undefined
          ? { assistantName: options.assistantName }
          : {}),
      },
      { logger },
    );
  };
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
    await client.user.add({
      userId,
      ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
      ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
      ...(options.email !== undefined ? { email: options.email } : {}),
    });
  } catch (error) {
    if (isAlreadyExists(error)) {
      // A 409 Conflict means the user already exists — that's fine.
      logger.debug?.("[zep] user.add: user already exists; continuing.");
    } else {
      // Anything else (401 auth, network, 5xx) is a real failure we must not
      // hide. Surface it but keep going — thread.create may still succeed, and
      // its own error handling decides the final result.
      logger.warn(`[zep] user.add failed: ${errorMessage(error)}`);
    }
  }

  try {
    await client.thread.create({ threadId, userId });
    return true;
  } catch (error) {
    if (isAlreadyExists(error)) {
      // Thread already exists — treat as success.
      return true;
    }
    logger.warn(`[zep] Failed to ensure thread: ${errorMessage(error)}`);
    return false;
  }
}

/**
 * Whether a thrown Zep error is a 409 Conflict (resource already exists).
 *
 * Gated on the SDK's typed signal — a {@link Zep.ConflictError} or any error
 * carrying `statusCode === 409` — rather than a loose message regex, so a 401
 * (bad key) or a network error is never mistaken for "already exists".
 */
function isAlreadyExists(error: unknown): boolean {
  if (error instanceof Zep.ConflictError) return true;
  return (
    typeof error === "object" &&
    error !== null &&
    "statusCode" in error &&
    (error as { statusCode?: unknown }).statusCode === 409
  );
}
