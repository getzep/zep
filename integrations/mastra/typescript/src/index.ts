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
 * {@link createZepUserAndThread} provisions the Zep user and thread before
 * the first turn either way, and returns their server-generated UUIDs. Zep
 * v4 addresses every user, thread, and graph by UUID, so store the returned
 * values in your own database and pass them to the processors and tools.
 * Every processor and tool handles Zep failures gracefully — a Zep outage
 * never crashes the host agent.
 *
 * @example Automatic memory loop
 * ```ts
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { Agent } from "@mastra/core/agent";
 * import { createZepProcessors, createZepUserAndThread } from "@getzep/zep-mastra";
 *
 * const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const identity = await createZepUserAndThread({ client, firstName: "Jane" });
 * if (!identity) throw new Error("Zep setup failed");
 *
 * const { inputProcessor, outputProcessor } = createZepProcessors({
 *   client,
 *   graphUuid: identity.graphUuid,
 *   threadUuid: identity.threadUuid,
 * });
 *
 * const agent = new Agent({
 *   id: "memory-agent",
 *   name: "Memory Agent",
 *   instructions: "You have long-term memory about the user.",
 *   model: "openai/gpt-5-mini",
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
export type {
  ZepSearchToolOptions,
  ZepSearchPinnableParams,
  ZepSearchScope,
} from "./search-tool.js";

export { createZepContextTool } from "./context-tool.js";
export type { ZepContextToolOptions } from "./context-tool.js";

export {
  createZepToolset,
  createZepUserAndThread,
} from "./toolset.js";
export type {
  ZepToolset,
  ZepToolsetOptions,
  CreateIdentityOptions,
  ZepIdentity,
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

export { toRoleType, resolveGraphUuid } from "./zep-utils.js";

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
