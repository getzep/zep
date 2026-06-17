/**
 * Zep long-term memory integration for Google ADK (TypeScript).
 *
 * Two interchangeable ways to give an ADK `LlmAgent` persistent memory:
 *
 *   - {@link createZepBeforeModelCallback} — the primary hook. Wire it into
 *     `LlmAgent.beforeModelCallback`; it persists the user message and injects
 *     the Zep Context Block on every turn.
 *   - {@link ZepContextTool} — the same behaviour packaged as a `BaseTool`, for
 *     teams that prefer composing memory through `LlmAgent.tools`.
 *
 * Pair either with {@link createZepAfterModelCallback} to persist assistant
 * responses, and add {@link ZepGraphSearchTool} to let the model search the
 * graph on demand.
 *
 * Every Zep call is wrapped: failures are logged, never thrown, so a Zep outage
 * cannot crash the host agent.
 *
 * @packageDocumentation
 */

export {
  createZepBeforeModelCallback,
  type ZepBeforeModelCallback,
  type ZepBeforeModelCallbackOptions,
} from "./before-model-callback.js";

export {
  createZepAfterModelCallback,
  type ZepAfterModelCallback,
  type ZepAfterModelCallbackOptions,
} from "./after-model-callback.js";

export {
  createZepCallbacks,
  type ZepCallbacks,
  type ZepCallbacksOptions,
} from "./callbacks.js";

export { ZepContextTool, type ZepContextToolOptions } from "./context-tool.js";

export {
  ZepGraphSearchTool,
  type ZepGraphSearchToolOptions,
} from "./graph-search-tool.js";

export {
  formatContextInstruction,
  persistAndInject,
  type InjectOptions,
} from "./inject.js";

export {
  resolveIdentity,
  extractText,
  STATE_KEYS,
  type AdkContextLike,
  type ResolvedIdentity,
  type ZepIdentityOptions,
} from "./identity.js";

export { ZepResourceManager } from "./resources.js";
export {
  truncateMessageContent,
  MESSAGE_CONTENT_MAX,
  MESSAGE_CONTENT_TRUNCATE_TO,
  GRAPH_DATA_MAX,
} from "./limits.js";
export { defaultLogger, type Logger } from "./logging.js";
export { ZepIdentityError } from "./errors.js";

/** The installed package version. */
export const VERSION = "0.1.0";
