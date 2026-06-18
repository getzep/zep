/**
 * Vercel AI SDK + Zep — `streamText` example (middleware + onFinish).
 *
 * The same division of labor as the `generateText` example applies to
 * streaming: **inject via middleware, persist via `onFinish`.**
 *
 *   - Context injection runs in the middleware's `transformParams` (it fires for
 *     `stream` calls too, on each new user turn).
 *   - Persistence runs from `onFinish`, which fires exactly once per turn with
 *     the final assistant text — for both `streamText` and `generateText`. (The
 *     middleware never persists; a per-step hook would fragment the turn.)
 *
 * If you'd rather set `system:` yourself instead of using the middleware, the
 * plain `getZepContext` helper does that — but the middleware keeps the
 * streaming and non-streaming paths identical.
 *
 * Prerequisites:
 *   export ZEP_API_KEY="your-zep-api-key"
 *   export OPENAI_API_KEY="your-openai-api-key"
 *
 * Run:
 *   npx tsx examples/stream-text.ts
 */

import { randomUUID } from "node:crypto";
import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { streamText, wrapLanguageModel } from "ai";
import {
  createZepMiddleware,
  createZepOnFinish,
  ensureZepUserAndThread,
} from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!ZEP_API_KEY) throw new Error("ZEP_API_KEY is not set.");
if (!OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not set.");

const userId = `zep-vercel-stream-${randomUUID().slice(0, 8)}`;
const threadId = `thread-${randomUUID().slice(0, 8)}`;

async function main(): Promise<void> {
  const client = new ZepClient({ apiKey: ZEP_API_KEY });

  await ensureZepUserAndThread({
    client,
    userId,
    threadId,
    firstName: "Bob",
    lastName: "Jones",
  });

  const userInput = "Hi! I just adopted a beagle named Cooper.";

  // 1. Wrap the model: inject the Context Block on each new user turn.
  const model = wrapLanguageModel({
    model: openai("gpt-4o-mini"),
    middleware: createZepMiddleware({ client, threadId }),
  });

  // 2. Stream the response. Persist the completed turn from onFinish, which
  //    fires once for both streamText and generateText.
  const result = streamText({
    model,
    system: "You are a helpful assistant.",
    prompt: userInput,
    onFinish: createZepOnFinish({ client, threadId, user: userInput, userName: "Bob" }),
  });

  process.stdout.write("Agent: ");
  for await (const chunk of result.textStream) {
    process.stdout.write(chunk);
  }
  process.stdout.write("\n\nDone (turn persisted to Zep via onFinish).\n");
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exitCode = 1;
});
