/**
 * Core persist-and-inject logic shared by `createZepBeforeModelCallback` and
 * `ZepContextTool`.
 *
 * On each turn it:
 *   1. Extracts the latest user message text.
 *   2. Resolves the Zep identity (explicit options → session state → ADK IDs).
 *   3. Persists the user message and retrieves a Context Block — either via a
 *      single `thread.addMessages(returnContext: true)` round-trip (default),
 *      or, when a custom `contextBuilder` is configured, by running
 *      `thread.addMessages` (without `returnContext`) concurrently with the
 *      builder.
 *   4. Injects the resulting context block into
 *      `llmRequest.config.systemInstruction`, rendered through
 *      `contextTemplate`.
 *
 * This function never creates the Zep user or thread. Callers must
 * provision them out-of-band before the first turn — see
 * `ensureUser` / `ensureThread` in `src/provisioning.ts`. If a persist call
 * fails with a Zep "not found" error (the user/thread was never
 * provisioned), a warning is logged naming the fix.
 *
 * Every Zep call is wrapped so a failure is logged and the turn proceeds
 * without injected context — a Zep outage never crashes the agent.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { LlmRequest } from "@google/adk";
import type { GenerateContentConfig, Part } from "@google/genai";
import { isNotFoundError } from "./errors.js";
import {
  extractText,
  resolveIdentity,
  type AdkContextLike,
  type ResolvedIdentity,
  type ZepIdentityOptions,
} from "./identity.js";
import { truncateMessageContent } from "./limits.js";
import type { Logger } from "./logging.js";
import type { TurnDedup } from "./resources.js";

/**
 * Default template used to wrap retrieved Zep context before injecting it
 * into the LLM's system instructions. Rendered via plain string replacement
 * (`template.split("{context}").join(contextText)`), never `String.prototype
 * .replace` with a pattern — so context text or a custom template containing
 * `{`/`}`/`%`/`$` is always safe to inject.
 *
 * This exact string is canonical across zep-adk's Python, Go, and TypeScript
 * implementations — keep them in sync.
 */
export const DEFAULT_CONTEXT_TEMPLATE =
  "The following context is retrieved from Zep, the agent's long-term memory. " +
  "It contains relevant facts, entities, and prior knowledge about the user. " +
  "Use it to inform your responses.\n\n" +
  "<ZEP_CONTEXT>\n" +
  "{context}\n" +
  "</ZEP_CONTEXT>";

/**
 * Build the system-instruction snippet that carries the Zep Context Block.
 *
 * Exposed for testing and for callers that assemble their own prompt.
 *
 * @param contextBlock The Context Block returned by Zep (or produced by a
 *   custom `contextBuilder`).
 * @param template Template to render `contextBlock` into. Must contain a
 *   literal `{context}` placeholder; ALL occurrences are replaced (matching
 *   Python's `str.replace` semantics). Defaults to
 *   {@link DEFAULT_CONTEXT_TEMPLATE}.
 * @returns A prompt-ready instruction with `{context}` replaced by
 *   `contextBlock`.
 */
export function formatContextInstruction(
  contextBlock: string,
  template: string = DEFAULT_CONTEXT_TEMPLATE,
): string {
  return template.split("{context}").join(contextBlock);
}

/**
 * Input handed to a custom {@link ContextBuilder}.
 *
 * Bundling the builder's inputs into a single object (rather than positional
 * arguments) lets us add fields later without breaking existing builders.
 */
export interface ContextBuilderInput {
  /** The `ZepClient` in use by the integration. */
  zep: ZepClient;
  /** The resolved Zep user ID for this turn. */
  userId: string;
  /** The resolved Zep thread ID for this turn. */
  threadId: string;
  /** The user's message text for this turn. */
  userMessage: string;
  /** The ADK context for this turn (session state, invocation metadata). */
  context: AdkContextLike;
  /** The outgoing model request. */
  llmRequest: LlmRequest;
}

/**
 * A custom context builder function.
 *
 * Receives a single {@link ContextBuilderInput} and returns the context block
 * to inject into the LLM prompt (or `undefined`/empty to skip injection).
 *
 * Error semantics: if the builder rejects, a warning is logged (lengths /
 * counts only) and injection is skipped for that turn — it never crashes the
 * host agent and never prevents message persistence from completing. See
 * {@link persistAndInject} for the full error-isolation contract between
 * persistence and the builder.
 */
export type ContextBuilder = (
  input: ContextBuilderInput,
) => Promise<string | undefined>;

/**
 * Append a system-instruction string to an `LlmRequest`, preserving any
 * existing instruction.
 *
 * ADK ships `appendInstructions`, but its module is not reachable under
 * NodeNext (the package only exports `"."`), so we mutate
 * `config.systemInstruction` directly. `systemInstruction` accepts a string
 * (`ContentUnion`), so we concatenate when one is already present.
 */
function appendSystemInstruction(
  llmRequest: LlmRequest,
  instruction: string,
): void {
  const config: GenerateContentConfig = (llmRequest.config ??= {});
  const existing = config.systemInstruction;

  if (existing === undefined || existing === null) {
    config.systemInstruction = instruction;
    return;
  }

  if (typeof existing === "string") {
    config.systemInstruction = `${existing}\n\n${instruction}`;
    return;
  }

  // `systemInstruction` may also be a `Content`, a `Part[]`, or a single
  // `Part`. Normalise to a `Part[]` (a valid `ContentUnion`) and append our
  // instruction as a trailing text part, preserving the existing instruction.
  const parts: Part[] = [];
  for (const item of Array.isArray(existing) ? existing : [existing]) {
    if (typeof item === "string") {
      parts.push({ text: item });
    } else if ("parts" in item && Array.isArray(item.parts)) {
      // A `Content` object — pull out its parts.
      parts.push(...item.parts);
    } else {
      // A single `Part`.
      parts.push(item as Part);
    }
  }
  parts.push({ text: instruction });
  config.systemInstruction = parts;
}

/** Options accepted by {@link persistAndInject}. */
export interface InjectOptions extends ZepIdentityOptions {
  /**
   * Roles to exclude from Zep's knowledge-graph ingestion. The messages are
   * still stored in the thread and used to contextualize other messages.
   */
  ignoreRoles?: Zep.RoleType[];
  /**
   * An optional async function that builds the context block to inject,
   * instead of using `thread.addMessages(returnContext: true)`.
   *
   * When set, message persistence (`thread.addMessages`, without
   * `returnContext`) and the builder run **concurrently** for lower latency.
   * When unset (the default), the tool uses a single
   * `thread.addMessages(returnContext: true)` round-trip.
   *
   * Error isolation: persistence and the builder are isolated from each
   * other's failure — one raising/rejecting never cancels or masks the
   * other's result. See {@link persistAndInject} for the full contract.
   */
  contextBuilder?: ContextBuilder;
  /**
   * Template used to wrap retrieved context before injecting it into the
   * LLM's system instructions. Must contain a literal `{context}`
   * placeholder, replaced with the retrieved context text via plain string
   * replacement (never a regex or `String.prototype.replace` pattern).
   * Defaults to {@link DEFAULT_CONTEXT_TEMPLATE}.
   */
  contextTemplate?: string;
}

/**
 * Persist the latest user message to Zep and inject a Context Block into the
 * outgoing LLM request.
 *
 * This is the single implementation behind both the `beforeModelCallback` and
 * `ZepContextTool`. It never throws on a Zep error.
 *
 * By default, persistence and context retrieval happen in a single
 * `thread.addMessages(returnContext: true)` round-trip. When
 * `options.contextBuilder` is set, persistence (`thread.addMessages`, without
 * `returnContext`) and the builder run **concurrently** instead — each is
 * isolated from the other's failure (`Promise.allSettled` semantics):
 *
 * - If the builder rejects, a warning is logged and injection is skipped for
 *   this turn — but persistence still completes and the turn is marked as
 *   persisted (dedup) on success.
 * - If persistence rejects, a warning is logged and the turn is **not**
 *   marked as persisted (so it can be retried on the next invocation) — but a
 *   successful builder result may still be injected into the prompt.
 *
 * @returns The injected Context Block, or `undefined` if nothing was injected
 *   (no user text, identity unresolved, builder skipped, or a Zep failure).
 */
export async function persistAndInject(params: {
  zep: ZepClient;
  dedup: TurnDedup;
  logger: Logger;
  context: AdkContextLike;
  llmRequest: LlmRequest;
  options: InjectOptions;
}): Promise<string | undefined> {
  const { zep, dedup, logger, context, llmRequest, options } = params;

  const userText = extractText(context.userContent);
  if (!userText) {
    return undefined;
  }

  let identity: ResolvedIdentity;
  try {
    identity = resolveIdentity(context, options);
  } catch (error) {
    logger.warn(
      "Skipping Zep persistence for this turn — could not resolve identity",
      error,
    );
    return undefined;
  }

  // Same-turn dedup guard. The before-model hook fires once per LLM call, so a
  // tool-using turn fires it multiple times with the same `invocationId`. Skip
  // re-persisting the user message for an invocation we already persisted to
  // this thread; otherwise the same user turn is stored two or more times.
  const { invocationId } = context;
  if (dedup.alreadyPersisted(identity.threadId, invocationId)) {
    return undefined;
  }

  const content = truncateMessageContent(userText, logger, "user");

  let persistOk: boolean;
  let contextBlock: string | undefined;
  if (options.contextBuilder) {
    ({ persistOk, contextBlock } = await persistAndBuildContext({
      zep,
      identity,
      content,
      userText,
      context,
      llmRequest,
      options,
      logger,
    }));
  } else {
    // Default: single round-trip.
    try {
      const response = await zep.thread.addMessages(identity.threadId, {
        messages: [
          {
            role: "user",
            content,
            name: identity.displayName,
          },
        ],
        returnContext: true,
        ignoreRoles: options.ignoreRoles,
      });
      contextBlock = response.context;
      persistOk = true;
      logger.info(
        `Persisted user message to Zep (thread=${identity.threadId}); ` +
          `context length: ${contextBlock?.length ?? 0}`,
      );
    } catch (error) {
      logPersistFailure(logger, error, identity.threadId);
      contextBlock = undefined;
      persistOk = false;
    }
  }

  // Mark as persisted only AFTER the API call succeeded, so that a transient
  // failure does not permanently suppress this turn's user message.
  if (persistOk) {
    dedup.markPersisted(identity.threadId, invocationId);
  }

  if (!contextBlock) {
    return undefined;
  }

  appendSystemInstruction(
    llmRequest,
    formatContextInstruction(contextBlock, options.contextTemplate),
  );
  return contextBlock;
}

/** Log a warning for a failed `thread.addMessages` call. */
function logPersistFailure(
  logger: Logger,
  error: unknown,
  threadId: string,
): void {
  if (isNotFoundError(error)) {
    logger.warn(
      `Zep user/thread not found (thread=${threadId}) — ` +
        "call ensureUser()/ensureThread() before the first turn",
      error,
    );
  } else {
    logger.warn("Failed to persist message / retrieve context from Zep", error);
  }
}

/**
 * Persist the message and build context concurrently.
 *
 * Runs `thread.addMessages` (without `returnContext`) and the custom
 * `contextBuilder` concurrently via `Promise.allSettled` to minimise latency
 * while ensuring one side's rejection cannot mask or cancel the other's
 * result — persistence and context building must be isolated from each
 * other's failures, and a Zep/builder failure must never crash the host
 * agent.
 */
async function persistAndBuildContext(params: {
  zep: ZepClient;
  identity: ResolvedIdentity;
  content: string;
  userText: string;
  context: AdkContextLike;
  llmRequest: LlmRequest;
  options: InjectOptions;
  logger: Logger;
}): Promise<{ persistOk: boolean; contextBlock: string | undefined }> {
  const { zep, identity, content, userText, context, llmRequest, options, logger } =
    params;
  const contextBuilder = options.contextBuilder;
  /* istanbul ignore next -- guarded by caller */
  if (!contextBuilder) {
    throw new Error("persistAndBuildContext requires options.contextBuilder");
  }

  const persist = async (): Promise<boolean> => {
    await zep.thread.addMessages(identity.threadId, {
      messages: [
        {
          role: "user",
          content,
          name: identity.displayName,
        },
      ],
      ignoreRoles: options.ignoreRoles,
    });
    logger.info(`Persisted user message to Zep (thread=${identity.threadId}).`);
    return true;
  };

  const buildContext = async (): Promise<string | undefined> => {
    const input: ContextBuilderInput = {
      zep,
      userId: identity.userId,
      threadId: identity.threadId,
      userMessage: userText,
      context,
      llmRequest,
    };
    return contextBuilder(input);
  };

  const [persistResult, contextResult] = await Promise.allSettled([
    persist(),
    buildContext(),
  ]);

  let persistOk: boolean;
  if (persistResult.status === "fulfilled") {
    persistOk = persistResult.value;
  } else {
    logPersistFailure(logger, persistResult.reason, identity.threadId);
    persistOk = false;
  }

  let contextBlock: string | undefined;
  if (contextResult.status === "fulfilled") {
    contextBlock = contextResult.value;
  } else {
    logger.warn(
      "Custom contextBuilder rejected — skipping context injection for this turn",
      contextResult.reason,
    );
    contextBlock = undefined;
  }

  return { persistOk, contextBlock };
}
