/**
 * `@getzep/zep-mastra` — Zep long-term memory tools for Mastra agents.
 *
 * Three `createTool`-based tools wrap Zep's two real operations — persist and
 * retrieve — over its temporal Context Graph:
 *
 * - {@link createZepRememberTool} — persist a message or fact.
 * - {@link createZepSearchTool} — search the graph for relevant facts.
 * - {@link createZepContextTool} — fetch the whole-user-graph Context Block.
 *
 * {@link createZepToolset} builds all three bound to one client, and
 * {@link ensureZepUserAndThread} provisions the Zep user/thread before the first
 * turn. Every tool handles Zep failures gracefully — a Zep outage never crashes
 * the host agent.
 *
 * @example
 * ```ts
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { Agent } from "@mastra/core/agent";
 * import { createZepToolset, ensureZepUserAndThread } from "@getzep/zep-mastra";
 *
 * const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const binding = { userId: "user-123", threadId: "thread-abc" };
 * await ensureZepUserAndThread({ client, ...binding, firstName: "Jane" });
 *
 * const { zepRemember, zepSearch, zepContext } = createZepToolset({ client, binding });
 *
 * const agent = new Agent({
 *   id: "memory-agent",
 *   name: "Memory Agent",
 *   instructions: "You have long-term memory. Recall and store user facts.",
 *   model: "openai/gpt-4o-mini",
 *   tools: { zepRemember, zepSearch, zepContext },
 * });
 * ```
 *
 * @packageDocumentation
 */

export { createZepRememberTool } from "./remember-tool.js";
export type { ZepRememberToolOptions } from "./remember-tool.js";

export { createZepSearchTool } from "./search-tool.js";
export type { ZepSearchToolOptions } from "./search-tool.js";

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
} from "./toolset.js";

export { toRoleType, resolveGraphTarget } from "./zep-utils.js";

export type {
  ZepBinding,
  ZepThreadBinding,
  ZepLogger,
  RoleType,
} from "./types.js";

/** Package version. */
export const VERSION = "0.1.0";
