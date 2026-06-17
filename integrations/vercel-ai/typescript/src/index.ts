/**
 * `@getzep/zep-vercel-ai` — Zep long-term memory for the
 * [Vercel AI SDK](https://ai-sdk.dev) (v6).
 *
 * Three layers wrap Zep's two real operations — persist and retrieve — over its
 * temporal Context Graph, so you can pick the integration point that fits your
 * call:
 *
 * 1. **Middleware** — {@link createZepMiddleware} wraps a language model and
 *    injects the user's Context Block as a system message on every call (and,
 *    optionally, persists the turn for non-streaming `generateText`).
 * 2. **Helpers** — {@link getZepContext} and {@link persistZepTurn} are plain
 *    functions for the `system:` + `onFinish` pattern, which is the required
 *    persistence path for `streamText` (where middleware `wrapGenerate` does not
 *    fire).
 * 3. **Tools** — {@link createZepTools} returns model-callable
 *    `tool()`s (`zepSearch`, `zepRemember`, `zepContext`) for retrieve/persist
 *    inside a tool loop.
 *
 * Every layer handles Zep failures gracefully — a Zep outage degrades to "no
 * memory" and never crashes the host call. Warnings log lengths only, never
 * content/PII.
 *
 * @example Middleware + tools with `generateText`
 * ```ts
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { wrapLanguageModel, generateText, stepCountIs } from "ai";
 * import { openai } from "@ai-sdk/openai";
 * import {
 *   createZepMiddleware,
 *   createZepTools,
 *   ensureZepUserAndThread,
 * } from "@getzep/zep-vercel-ai";
 *
 * const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * await ensureZepUserAndThread({ client, userId: "u1", threadId: "t1", firstName: "Jane" });
 *
 * const model = wrapLanguageModel({
 *   model: openai("gpt-4o-mini"),
 *   middleware: createZepMiddleware({ client, threadId: "t1", persist: true }),
 * });
 *
 * const tools = createZepTools(client, { userId: "u1", threadId: "t1" });
 * const { text } = await generateText({
 *   model,
 *   tools,
 *   stopWhen: stepCountIs(5),
 *   prompt: "What do you remember about me?",
 * });
 * ```
 *
 * @packageDocumentation
 */

export { createZepMiddleware } from "./middleware.js";
export type { ZepMiddlewareOptions } from "./middleware.js";

export { getZepContext, persistZepTurn, ensureZepUserAndThread } from "./helpers.js";
export type { EnsureIdentityOptions } from "./helpers.js";

export {
  createZepTools,
  createZepSearchTool,
  createZepRememberTool,
  createZepContextTool,
} from "./tools.js";
export type {
  ZepTools,
  ZepToolsOptions,
  ZepSearchToolOptions,
  ZepRememberToolOptions,
  ZepContextToolOptions,
} from "./tools.js";

export { toRoleType, resolveGraphTarget, truncateForZep, MESSAGE_MAX_CHARS } from "./zep-utils.js";

export type { ZepBinding, ZepLogger, ZepTurn, RoleType } from "./types.js";

/** Package version. */
export const VERSION = "0.1.0";
