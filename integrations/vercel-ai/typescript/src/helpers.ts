import { Zep } from "@getzep/zep-cloud";
import type { ZepClient } from "@getzep/zep-cloud";
import type { ZepLogger, ZepTurn, ZepUserCreatedHook } from "./types.js";
import {
  MESSAGE_MAX_CHARS,
  errorMessage,
  resolveLogger,
  truncateForZep,
} from "./zep-utils.js";

/**
 * Retrieve the prompt-ready **Context Block** for a thread via
 * `thread.getContext`.
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
 * const context = await getZepContext(client, threadUuid);
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
 * @param threadUuid - The Zep thread whose context to fetch.
 * @param options - Optional `templateUuid` for custom Context Block formatting and
 *   a `logger` (defaults to `console`).
 * @returns The Context Block string, or `""` when unavailable.
 */
export async function getZepContext(
  client: ZepClient,
  threadUuid: string,
  options?: { templateUuid?: string; logger?: ZepLogger },
): Promise<string> {
  const logger = resolveLogger(options?.logger);
  if (!threadUuid) {
    logger.warn("[zep-context] No threadUuid provided; skipping context retrieval.");
    return "";
  }

  try {
    const response = await client.thread.getContext(
      threadUuid,
      options?.templateUuid ? { templateUuid: options.templateUuid } : {},
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
 *   system: await getZepContext(client, threadUuid),
 *   messages,
 *   onFinish: ({ text }) => {
 *     void persistZepTurn(client, threadUuid, { user: userInput, assistant: text });
 *   },
 * });
 * ```
 *
 * For the common case, prefer {@link createZepOnFinish} — it builds this
 * callback for you and persists the whole turn exactly once.
 *
 * Over-long content is truncated to 4,000 characters, below Zep's 4,096-char
 * message limit, with a lengths-only warning. A Zep failure is logged and
 * reported via the boolean return value rather than thrown.
 *
 * @param client - A shared, initialized Zep client.
 * @param threadUuid - The Zep thread to append messages to.
 * @param turn - The user and/or assistant content to persist.
 * @param options - Optional `returnContext` (fold retrieval into the same
 *   round-trip) and a `logger`.
 * @returns The Context Block if `returnContext` was set and the call succeeded,
 *   otherwise `null`. A `null` return after a failure is logged.
 */
export async function persistZepTurn(
  client: ZepClient,
  threadUuid: string,
  turn: ZepTurn,
  options?: { returnContext?: boolean; logger?: ZepLogger },
): Promise<string | null> {
  const logger = resolveLogger(options?.logger);
  if (!threadUuid) {
    logger.warn("[zep-persist] No threadUuid provided; skipping persist.");
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
    const response = await client.thread.addMessages(threadUuid, {
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
  threadUuid: string;
  /**
   * The user ID for the turn. Optional and not required for persistence
   * (`thread.addMessages` is scoped by `threadUuid`); accepted for symmetry with
   * the rest of the API and for callers that want it in scope.
   */
  userUuid?: string;
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
 *   onFinish: createZepOnFinish({ client, threadUuid, user: userInput, userName: "Jane" }),
 * });
 * ```
 *
 * Persistence is fire-and-forget at the call site (the returned callback awaits
 * {@link persistZepTurn} internally, which never throws): a Zep outage degrades
 * to "turn not persisted" and never crashes or blocks the host call. Over-long
 * content is truncated to 4,000 characters, below Zep's 4,096-char message
 * limit, with a lengths-only warning.
 *
 * @param options - The client, thread, and how to source the user message.
 * @returns An `onFinish` callback for `generateText`/`streamText`.
 */
export function createZepOnFinish(
  options: ZepOnFinishOptions,
): (event: ZepOnFinishEvent) => Promise<void> {
  const { client, threadUuid } = options;
  const logger = resolveLogger(options.logger);

  return async (event: ZepOnFinishEvent): Promise<void> => {
    const assistant = event.text?.trim();
    const user =
      typeof options.user === "function" ? options.user(event)?.trim() : options.user?.trim();

    if (!user && !assistant) return;

    await persistZepTurn(
      client,
      threadUuid,
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

/** Options for {@link createZepUserAndThread}. */
export interface CreateZepUserAndThreadOptions {
  /** A shared, initialized Zep client. */
  client: ZepClient;
  /** User's first name — pass a real name to help Zep resolve identity. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
  /** User's email. */
  email?: string;
  /**
   * Runs once, immediately after Zep creates the user. Hook errors are logged
   * and do not prevent thread creation.
   */
  onUserCreated?: ZepUserCreatedHook;
  /** Logger for failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * Create a Zep user and thread for a conversation.
 *
 * Zep assigns the user, graph, and thread UUIDs. Store these UUIDs in the
 * application database and reuse them on later turns. This helper does not
 * perform runtime lookups.
 *
 * The `onUserCreated` hook runs after user creation and before thread creation.
 * A hook failure is logged and does not stop the helper.
 *
 * @returns The server-generated UUIDs, or `null` if setup failed.
 */
export async function createZepUserAndThread(
  options: CreateZepUserAndThreadOptions,
): Promise<{ userUuid: string; graphUuid: string; threadUuid: string } | null> {
  const { client } = options;
  const logger = resolveLogger(options.logger);

  let userUuid: string;
  let graphUuid: string;
  try {
    const user = await client.user.create({
      ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
      ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
      ...(options.email !== undefined ? { email: options.email } : {}),
    });
    if (!user.uuid || !user.graphUuid) {
      logger.warn("[zep] User creation returned no user or graph UUID.");
      return null;
    }
    userUuid = user.uuid;
    graphUuid = user.graphUuid;
    if (options.onUserCreated) {
      try {
        await options.onUserCreated(client, userUuid);
      } catch (hookError) {
        logger.warn(`[zep] onUserCreated hook failed: ${errorMessage(hookError)}`);
      }
    }
  } catch (error) {
    logger.warn(`[zep] Failed to create user: ${errorMessage(error)}`);
    return null;
  }

  try {
    const thread = await client.thread.create({ userUuid });
    if (!thread.uuid) {
      logger.warn("[zep] Thread creation returned no thread UUID.");
      return null;
    }
    return { userUuid, graphUuid, threadUuid: thread.uuid };
  } catch (error) {
    logger.warn(`[zep] Failed to create thread: ${errorMessage(error)}`);
    return null;
  }
}
