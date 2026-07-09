/**
 * `@getzep/zep-mastra` — Zep long-term memory for Mastra agents.
 *
 * Two complementary surfaces, both built on Zep's temporal Context Graph:
 *
 * - **Automatic memory (recommended):** {@link createZepProcessors} builds a
 *   {@link ZepInputProcessor} / {@link ZepOutputProcessor} pair that plugs
 *   directly into an `Agent`'s native `inputProcessors`/`outputProcessors`
 *   pipeline — no tool-calling round-trip required. The input processor
 *   injects a Zep Context Block as a system message before every model call;
 *   the output processor persists the completed turn afterward.
 * - **Tools:** {@link createZepRememberTool}, {@link createZepSearchTool}, and
 *   {@link createZepContextTool} (bundled by {@link createZepToolset}) let the
 *   model decide when to persist or recall — a tool-centric alternative or
 *   complement to the automatic loop.
 *
 * {@link ensureZepUserAndThread} provisions the Zep user/thread before the
 * first turn either way. Every processor and tool handles Zep failures
 * gracefully — a Zep outage never crashes the host agent.
 *
 * @example Automatic memory loop
 * ```ts
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { Agent } from "@mastra/core/agent";
 * import { createZepProcessors, ensureZepUserAndThread } from "@getzep/zep-mastra";
 *
 * const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const userId = "user-123";
 * const threadId = "thread-abc";
 * await ensureZepUserAndThread({ client, userId, threadId, firstName: "Jane" });
 *
 * const { inputProcessor, outputProcessor } = createZepProcessors({ client, userId, threadId });
 *
 * const agent = new Agent({
 *   id: "memory-agent",
 *   name: "Memory Agent",
 *   instructions: "You have long-term memory about the user.",
 *   model: "openai/gpt-4o-mini",
 *   inputProcessors: [inputProcessor],
 *   outputProcessors: [outputProcessor],
 * });
 * ```
 *
 * @packageDocumentation
 */

export { createZepRememberTool } from "./remember-tool.js";
export type { ZepRememberToolOptions } from "./remember-tool.js";

export { createZepSearchTool } from "./search-tool.js";
export type { ZepSearchToolOptions, ZepSearchPinnableParams } from "./search-tool.js";

export { createZepContextTool } from "./context-tool.js";
export type { ZepContextToolOptions } from "./context-tool.js";

export {
  createZepToolset,
  ensureZepUserAndThread,
} from "./toolset.js";
export type {
  ZepToolset,
  ZepToolsetOptions,
  EnsureIdentityOptions,
  ZepUserCreatedHook,
} from "./toolset.js";

export {
  ZepInputProcessor,
  ZepOutputProcessor,
  createZepProcessors,
  DEFAULT_CONTEXT_TEMPLATE,
} from "./processors.js";
export type {
  ZepInputProcessorOptions,
  ZepOutputProcessorOptions,
  ZepProcessorsOptions,
  ZepProcessorSharedOptions,
  ZepIdentityResolver,
  ResolvedZepIdentity,
  ZepContextBuilder,
  ZepContextBuilderInput,
} from "./processors.js";

export { toRoleType, resolveGraphTarget } from "./zep-utils.js";

export type {
  ZepBinding,
  ZepThreadBinding,
  ZepLogger,
  RoleType,
} from "./types.js";
// Note: ZepIdentityResolver / ResolvedZepIdentity are exported above from
// ./processors.js (which re-exports the canonical definitions in types.js).

/** Package version. */
export const VERSION = "0.2.0";
