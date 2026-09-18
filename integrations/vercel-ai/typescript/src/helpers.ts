import type { Zep, ZepClient } from "@getzep/zep-cloud";
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
 * @param threadUuid - The UUID of the Zep thread whose user context to fetch.
 * @param options - Optional `templateUuid` for custom Context Block formatting
 *   and a `logger` (defaults to `console`).
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
 * Over-long content is truncated to Zep's 4,096-char message limit with a
 * warning that logs **lengths only** (never content). A Zep failure is logged
 * and reported via the boolean return value rather than thrown.
 *
 * @param client - A shared, initialized Zep client.
 * @param threadUuid - The UUID of the Zep thread to append messages to.
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

  const messages: Zep.AddMessage[] = [];
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
  /** The UUID of the Zep thread that receives the persisted turn. */
  threadUuid: string;
  /**
   * The UUID of the Zep user for the turn. Optional and not required for
   * persistence (`thread.addMessages` is scoped by `threadUuid`); accepted for
   * symmetry with the rest of the API and for callers that want it in scope.
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
 * content is truncated to Zep's 4,096-char message limit with a lengths-only
 * warning.
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
export interface CreateIdentityOptions {
  /** A shared, initialized Zep client. */
  client: ZepClient;
  /**
   * An optional developer-assigned name for the user, for example the
   * application's own user identifier. Zep v4 addresses the user by the UUID
   * that it returns, so this name is a label only.
   */
  userId?: string;
  /**
   * An optional developer-assigned name for the thread. Zep v4 addresses the
   * thread by the UUID that it returns, so this name is a label only.
   */
  threadId?: string;
  /** User's first name — pass a real name to help Zep resolve identity. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
  /** User's email. */
  email?: string;
  /**
   * Runs exactly once, immediately after the Zep user is created, and before
   * the thread is created. The hook receives the UUID of the new user. Errors
   * are logged, not thrown — a failing hook never changes the identity that
   * this helper returns. Use this to configure per-user ontology, custom
   * instructions, or user summary instructions.
   */
  onUserCreated?: ZepUserCreatedHook;
  /** Logger for failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/** The Zep resources that {@link createZepUserAndThread} creates. */
export interface ZepIdentity {
  /** The UUID of the new Zep user. */
  userUuid: string;
  /** The UUID of the new user's graph. Bind the tools to this UUID. */
  graphUuid: string;
  /** The UUID of the new Zep thread. */
  threadUuid: string;
}

/**
 * Create the Zep user and thread for a conversation, and return their UUIDs.
 *
 * Zep requires the user and thread to exist before messages are added. Call
 * this once, out-of-band, before the first turn (the Zep "create user → create
 * thread" step), then store the returned UUIDs in your own database. Zep v4
 * addresses every resource by a server-generated UUID, so a second call creates
 * a second user. Failures are logged (no PII) and reported by a `null` return
 * value rather than thrown.
 *
 * ```ts
 * const identity = await createZepUserAndThread({ client, firstName: "Jane" });
 * if (identity) {
 *   await db.saveZepIdentity(appUserId, identity);
 * }
 * ```
 *
 * When `onUserCreated` is provided, the hook is awaited before `thread.create`
 * runs. A hook failure is logged and does not change the returned identity —
 * see {@link CreateIdentityOptions.onUserCreated}.
 *
 * @returns The new identity, or `null` when the creation failed.
 */
export async function createZepUserAndThread(
  options: CreateIdentityOptions,
): Promise<ZepIdentity | null> {
  const { client } = options;
  const logger = resolveLogger(options.logger);

  let user: Zep.User;
  try {
    user = await client.user.create({
      ...(options.userId !== undefined ? { userId: options.userId } : {}),
      ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
      ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
      ...(options.email !== undefined ? { email: options.email } : {}),
    });
  } catch (error) {
    logger.warn(`[zep] user.create failed: ${errorMessage(error)}`);
    return null;
  }

  const userUuid = user.uuid;
  const graphUuid = user.graphUuid;
  if (!userUuid || !graphUuid) {
    logger.warn("[zep] user.create returned no uuid or graphUuid; identity is not ready.");
    return null;
  }

  if (options.onUserCreated) {
    try {
      await options.onUserCreated(client, userUuid);
    } catch (hookError) {
      logger.warn(`[zep] onUserCreated hook failed: ${errorMessage(hookError)}`);
    }
  }

  try {
    const thread = await client.thread.create({
      userUuid,
      ...(options.threadId !== undefined ? { threadId: options.threadId } : {}),
    });
    if (!thread.uuid) {
      logger.warn("[zep] thread.create returned no uuid; identity is not ready.");
      return null;
    }
    return { userUuid, graphUuid, threadUuid: thread.uuid };
  } catch (error) {
    logger.warn(`[zep] thread.create failed: ${errorMessage(error)}`);
    return null;
  }
}
