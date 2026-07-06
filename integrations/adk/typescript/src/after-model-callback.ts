/**
 * `createZepAfterModelCallback` — persists assistant responses to Zep.
 *
 * Pair it with `createZepBeforeModelCallback` (or `ZepContextTool`) so both
 * sides of the conversation reach the user's graph. Returns a function
 * compatible with ADK's `afterModelCallback` on `LlmAgentConfig`.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { Context, LlmResponse } from "@google/adk";
import { isNotFoundError } from "./errors.js";
import { extractText, resolveIdentity, type ZepIdentityOptions } from "./identity.js";
import { truncateMessageContent } from "./limits.js";
import { defaultLogger, type Logger } from "./logging.js";

/** Options for {@link createZepAfterModelCallback}. */
export interface ZepAfterModelCallbackOptions extends ZepIdentityOptions {
  /** Display name for the assistant in Zep messages. Defaults to `"Assistant"`. */
  assistantName?: string;
  /**
   * Roles to exclude from Zep's knowledge-graph ingestion. Passed through to
   * `thread.addMessages`.
   */
  ignoreRoles?: Zep.RoleType[];
  /** Logger for Zep failures. Defaults to a `console`-backed logger. */
  logger?: Logger;
}

/**
 * The shape of the function returned by {@link createZepAfterModelCallback},
 * matching ADK's `SingleAfterModelCallback`.
 */
export type ZepAfterModelCallback = (params: {
  context: Context;
  response: LlmResponse;
}) => Promise<LlmResponse | undefined>;

/**
 * Create an `afterModelCallback` that persists the assistant's response text to
 * the Zep thread.
 *
 * Intermediate responses that carry tool calls (the model's "thinking" turns)
 * are skipped, so only one clean assistant message per turn reaches Zep.
 *
 * This callback never creates the Zep user or thread. Provision them
 * out-of-band before the first turn with `ensureUser()` / `ensureThread()`
 * (see `src/provisioning.ts`) — e.g. during account/session onboarding. If
 * the user/thread do not exist, persistence for that turn is skipped and a
 * warning is logged naming the fix.
 *
 * @param zep An initialised `ZepClient`. The caller owns its lifecycle.
 * @param options Identity overrides and behaviour flags.
 * @returns An async `afterModelCallback` that always resolves to `undefined`
 *   (the response passes through unmodified).
 */
export function createZepAfterModelCallback(
  zep: ZepClient,
  options: ZepAfterModelCallbackOptions = {},
): ZepAfterModelCallback {
  const logger = options.logger ?? defaultLogger;
  const assistantName = options.assistantName ?? "Assistant";

  return async ({ context, response }) => {
    // Skip partial streaming chunks and intermediate tool-call turns.
    if (response.partial) {
      return undefined;
    }
    const hasFunctionCall = (response.content?.parts ?? []).some(
      (part) => part.functionCall,
    );
    if (hasFunctionCall) {
      return undefined;
    }

    const text = extractText(response.content);
    if (!text) {
      return undefined;
    }

    let identity;
    try {
      identity = resolveIdentity(context, options);
    } catch (error) {
      logger.warn(
        "Skipping assistant persistence — could not resolve Zep thread ID",
        error,
      );
      return undefined;
    }

    const content = truncateMessageContent(text, logger, "assistant");

    try {
      await zep.thread.addMessages(identity.threadId, {
        messages: [{ role: "assistant", content, name: assistantName }],
        ignoreRoles: options.ignoreRoles,
      });
      logger.info(
        `Persisted assistant response to Zep (thread=${identity.threadId}, ${content.length} chars)`,
      );
    } catch (error) {
      if (isNotFoundError(error)) {
        logger.warn(
          `Zep user/thread not found (thread=${identity.threadId}) — ` +
            "call ensureUser()/ensureThread() before the first turn",
          error,
        );
      } else {
        logger.warn("Failed to persist assistant response to Zep", error);
      }
    }

    return undefined;
  };
}
