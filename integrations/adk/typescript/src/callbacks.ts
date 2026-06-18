/**
 * `createZepCallbacks` — build a paired before- and after-model callback that
 * share a single {@link ZepResourceManager}.
 *
 * Wiring both callbacks through one manager keeps their ensure-thread cache and
 * same-turn dedup state in sync, so they are not split-brain: the thread is
 * created once, and the user turn is persisted exactly once per invocation even
 * when both hooks run.
 */

import type { ZepClient } from "@getzep/zep-cloud";
import {
  createZepBeforeModelCallback,
  type ZepBeforeModelCallback,
  type ZepBeforeModelCallbackOptions,
} from "./before-model-callback.js";
import {
  createZepAfterModelCallback,
  type ZepAfterModelCallback,
  type ZepAfterModelCallbackOptions,
} from "./after-model-callback.js";
import { defaultLogger } from "./logging.js";
import { ZepResourceManager } from "./resources.js";

/** Options for {@link createZepCallbacks}. */
export interface ZepCallbacksOptions
  extends ZepBeforeModelCallbackOptions,
    ZepAfterModelCallbackOptions {}

/** The pair of callbacks returned by {@link createZepCallbacks}. */
export interface ZepCallbacks {
  /** Wire into `LlmAgent.beforeModelCallback`. */
  beforeModelCallback: ZepBeforeModelCallback;
  /** Wire into `LlmAgent.afterModelCallback`. */
  afterModelCallback: ZepAfterModelCallback;
  /** The shared resource manager backing both callbacks. */
  resources: ZepResourceManager;
}

/**
 * Create the before- and after-model callbacks together, backed by one shared
 * {@link ZepResourceManager}.
 *
 * Prefer this over constructing the two callbacks independently: a shared
 * manager means the Zep thread is ensured once and the same-turn dedup guard is
 * consistent across both hooks.
 *
 * @example
 * ```ts
 * import { LlmAgent } from "@google/adk";
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { createZepCallbacks } from "@getzep/zep-adk";
 *
 * const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const { beforeModelCallback, afterModelCallback } = createZepCallbacks(zep, {
 *   userId: "user-123",
 *   threadId: "thread-abc",
 * });
 * const agent = new LlmAgent({
 *   name: "memory_agent",
 *   model: "gemini-2.5-flash",
 *   instruction: "You are a helpful assistant with long-term memory.",
 *   beforeModelCallback,
 *   afterModelCallback,
 * });
 * ```
 *
 * @param zep An initialised `ZepClient`. The caller owns its lifecycle.
 * @param options Identity overrides and behaviour flags shared by both hooks.
 * @returns The paired callbacks and the shared resource manager.
 */
export function createZepCallbacks(
  zep: ZepClient,
  options: ZepCallbacksOptions = {},
): ZepCallbacks {
  const logger = options.logger ?? defaultLogger;
  const resources = options.resources ?? new ZepResourceManager(zep, logger);

  const beforeModelCallback = createZepBeforeModelCallback(zep, {
    ...options,
    logger,
    resources,
  });
  const afterModelCallback = createZepAfterModelCallback(zep, {
    ...options,
    logger,
    resources,
  });

  return { beforeModelCallback, afterModelCallback, resources };
}
