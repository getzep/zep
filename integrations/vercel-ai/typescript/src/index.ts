/**
 * `@getzep/zep-vercel-ai` — Zep long-term memory for the
 * [Vercel AI SDK](https://ai-sdk.dev) (v6).
 *
 * Three layers wrap Zep's two real operations — persist and retrieve — over its
 * temporal Context Graph, so you can pick the integration point that fits your
 * call:
 *
 * 1. **Middleware** — {@link createZepMiddleware} wraps a language model and
 *    **injects** the user's Context Block as a system message on each genuine
 *    new user turn. It is context-injection only; pair it with
 *    {@link createZepOnFinish} to persist.
 * 2. **Helpers** — {@link getZepContext}, {@link persistZepTurn}, and
 *    {@link createZepOnFinish} are plain functions for the `system:` +
 *    `onFinish` pattern. {@link createZepOnFinish} persists the whole turn once
 *    per turn (the verified-correct path for both `generateText` and
 *    `streamText`, since `onFinish` fires exactly once with the final assistant
 *    text).
 * 3. **Tools** — {@link createZepTools} returns model-callable
 *    `tool()`s (`zepSearch`, `zepRemember`, `zepContext`) for retrieve/persist
 *    inside a tool loop.
 *
 * The division of labor: **inject via middleware, persist via `onFinish`.** The
 * AI SDK tool loop calls the wrapped model once per step, so a per-step
 * middleware persistence hook would fragment one turn across many writes and
 * record intermediate tool-call preamble as a real assistant message;
 * `onFinish` fires exactly once per turn with the final text.
 *
 * Every layer handles Zep failures gracefully — a Zep outage degrades to "no
 * memory" and never crashes the host call. Warnings log lengths only, never
 * content/PII.
 *
 * @example Middleware (inject) + onFinish (persist) + tools with `generateText`
 * ```ts
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { wrapLanguageModel, generateText, stepCountIs } from "ai";
 * import { openai } from "@ai-sdk/openai";
 * import {
 *   createZepMiddleware,
 *   createZepOnFinish,
 *   createZepTools,
 *   ensureZepUserAndThread,
 * } from "@getzep/zep-vercel-ai";
 *
 * const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * await ensureZepUserAndThread({ client, userId: "u1", threadId: "t1", firstName: "Jane" });
 *
 * const model = wrapLanguageModel({
 *   model: openai("gpt-4o-mini"),
 *   middleware: createZepMiddleware({ client, threadId: "t1" }),
 * });
 *
 * const tools = createZepTools(client, { binding: { userId: "u1", threadId: "t1" } });
 * const prompt = "What do you remember about me?";
 * const { text } = await generateText({
 *   model,
 *   tools,
 *   stopWhen: stepCountIs(5),
 *   prompt,
 *   onFinish: createZepOnFinish({ client, threadId: "t1", user: prompt }),
 * });
 * ```
 *
 * @packageDocumentation
 */

export { createZepMiddleware } from "./middleware.js";
export type { ZepMiddlewareOptions } from "./middleware.js";

export {
  getZepContext,
  persistZepTurn,
  createZepOnFinish,
  ensureZepUserAndThread,
} from "./helpers.js";
export type { EnsureIdentityOptions, ZepOnFinishOptions } from "./helpers.js";

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

export {
  toRoleType,
  resolveGraphTarget,
  truncateForZep,
  MESSAGE_MAX_CHARS,
  GRAPH_MAX_CHARS,
} from "./zep-utils.js";

export type { ZepBinding, ZepLogger, ZepTurn, RoleType } from "./types.js";

/** Package version. */
export const VERSION = "0.1.0";
