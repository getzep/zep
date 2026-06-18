/**
 * `createZepBeforeModelCallback` — the primary Zep integration for Google ADK.
 *
 * Returns a function compatible with ADK's `beforeModelCallback` on
 * `LlmAgentConfig`. On every model call it persists the latest user message to
 * Zep and injects the retrieved Context Block into the system instruction.
 * It always returns `undefined` so the (mutated) request proceeds to the model.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { Context, LlmRequest, LlmResponse } from "@google/adk";
import type { ZepIdentityOptions } from "./identity.js";
import { persistAndInject } from "./inject.js";
import { defaultLogger, type Logger } from "./logging.js";
import { ZepResourceManager } from "./resources.js";

/** Options for {@link createZepBeforeModelCallback}. */
export interface ZepBeforeModelCallbackOptions extends ZepIdentityOptions {
  /**
   * Roles to exclude from Zep's knowledge-graph ingestion. Messages are still
   * stored in the thread and used to contextualize other messages. A common
   * value is `["assistant"]`.
   */
  ignoreRoles?: Zep.RoleType[];
  /** Logger for Zep failures. Defaults to a `console`-backed logger. */
  logger?: Logger;
  /**
   * Shared resource manager. Pass the same instance used by the after-model
   * callback so the two hooks share ensure-thread and dedup state instead of
   * being split-brain. {@link createZepCallbacks} wires this automatically.
   */
  resources?: ZepResourceManager;
}

/**
 * The shape of the function returned by {@link createZepBeforeModelCallback},
 * matching ADK's `SingleBeforeModelCallback`.
 */
export type ZepBeforeModelCallback = (params: {
  context: Context;
  request: LlmRequest;
}) => Promise<LlmResponse | undefined>;

/**
 * Create a `beforeModelCallback` that wires Zep long-term memory into an ADK
 * `LlmAgent`.
 *
 * Pass the returned function to `new LlmAgent({ ..., beforeModelCallback })`.
 * On each model call it:
 *
 *   1. Persists the latest user message to the Zep thread.
 *   2. Retrieves the Context Block for the user's graph (single round-trip).
 *   3. Injects that block into `request.config.systemInstruction`.
 *
 * Identity is resolved per turn: explicit `userId` / `threadId` options take
 * precedence, then `zep_user_id` / `zep_thread_id` session-state keys, then the
 * ADK session's `userId` / `sessionId`. Omitting the IDs lets one callback
 * serve every user in a shared-agent deployment.
 *
 * The Zep user and thread are created lazily on first use. Pre-create the
 * thread (keyed on the ADK `sessionId`) out-of-band if you need it to exist
 * before the first turn.
 *
 * @example
 * ```ts
 * import { LlmAgent } from "@google/adk";
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { createZepBeforeModelCallback } from "@getzep/zep-adk";
 *
 * const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const agent = new LlmAgent({
 *   name: "memory_agent",
 *   model: "gemini-2.5-flash",
 *   instruction: "You are a helpful assistant with long-term memory.",
 *   beforeModelCallback: createZepBeforeModelCallback(zep, {
 *     userId: "user-123",
 *     threadId: "thread-abc",
 *     firstName: "Jane",
 *     lastName: "Smith",
 *   }),
 * });
 * ```
 *
 * @param zep An initialised `ZepClient`. The integration never closes it — the
 *   caller owns its lifecycle.
 * @param options Identity overrides and behaviour flags.
 * @returns An async `beforeModelCallback` that always resolves to `undefined`
 *   (the model call proceeds with the mutated request).
 */
export function createZepBeforeModelCallback(
  zep: ZepClient,
  options: ZepBeforeModelCallbackOptions = {},
): ZepBeforeModelCallback {
  const logger = options.logger ?? defaultLogger;
  const resources = options.resources ?? new ZepResourceManager(zep, logger);

  return async ({ context, request }) => {
    await persistAndInject({
      zep,
      resources,
      logger,
      context,
      llmRequest: request,
      options,
    });
    // Always return undefined: proceed to the model with the mutated request.
    return undefined;
  };
}
