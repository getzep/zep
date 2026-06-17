/**
 * `createZepAfterModelCallback` — persists assistant responses to Zep.
 *
 * Pair it with `createZepBeforeModelCallback` (or `ZepContextTool`) so both
 * sides of the conversation reach the user's graph. Returns a function
 * compatible with ADK's `afterModelCallback` on `LlmAgentConfig`.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { Context, LlmResponse } from "@google/adk";
import { extractText, resolveIdentity, type ZepIdentityOptions } from "./identity.js";
import { truncateMessageContent } from "./limits.js";
import { defaultLogger, type Logger } from "./logging.js";
import { ZepResourceManager } from "./resources.js";

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
  /**
   * Shared resource manager. Pass the same instance used by the before-model
   * callback so the two hooks share ensure-thread and dedup state instead of
   * being split-brain. {@link createZepCallbacks} wires this automatically.
   */
  resources?: ZepResourceManager;
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
 * The Zep thread must already exist — `createZepBeforeModelCallback` /
 * `ZepContextTool` create it on the user turn that precedes the model
 * response, so in normal use no extra setup is needed.
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
  const resources = options.resources ?? new ZepResourceManager(zep, logger);

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

    // Ensure the user/thread exist. Normally the before-model hook already
    // created them, but sharing the manager keeps this a cheap cache hit and
    // also covers an after-only setup.
    if (!(await resources.ensure(identity))) {
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
      logger.warn("Failed to persist assistant response to Zep", error);
    }

    return undefined;
  };
}
