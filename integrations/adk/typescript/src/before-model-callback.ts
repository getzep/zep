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
import { persistAndInject, type ContextBuilder } from "./inject.js";
import { defaultLogger, type Logger } from "./logging.js";
import { TurnDedup } from "./resources.js";

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
   * Same-turn dedup guard. Prevents this callback from re-persisting the same
   * user message when ADK re-invokes `beforeModelCallback` multiple times
   * within one turn (tool-use loops). The after-model callback has no dedup
   * guard of its own — it doesn't need one, since it only ever sees one final
   * (non-partial, non-function-call) response per turn. Defaults to a
   * callback-local `TurnDedup` instance if omitted. {@link createZepCallbacks}
   * creates and wires one automatically.
   */
  dedup?: TurnDedup;
  /**
   * An optional async function that builds the context block to inject,
   * instead of the default `thread.addMessages(returnContext: true)`
   * round-trip. When set, persistence and the builder run concurrently. See
   * `persistAndInject` in `src/inject.ts` for the full error-isolation
   * contract.
   */
  contextBuilder?: ContextBuilder;
  /**
   * Template used to wrap retrieved context before injecting it into the
   * LLM's system instructions. Must contain a literal `{context}`
   * placeholder. Defaults to `DEFAULT_CONTEXT_TEMPLATE`.
   */
  contextTemplate?: string;
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
 * This callback never creates the Zep user or thread. Provision them
 * out-of-band before the first turn with `ensureUser()` / `ensureThread()`
 * (see `src/provisioning.ts`) — e.g. during account/session onboarding. If
 * the user/thread do not exist, persistence for that turn is skipped and a
 * warning is logged.
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
  const dedup = options.dedup ?? new TurnDedup();

  return async ({ context, request }) => {
    await persistAndInject({
      zep,
      dedup,
      logger,
      context,
      llmRequest: request,
      options,
    });
    // Always return undefined: proceed to the model with the mutated request.
    return undefined;
  };
}
