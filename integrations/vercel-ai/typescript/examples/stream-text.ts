/**
 * Vercel AI SDK + Zep — `streamText` persistence example.
 *
 * IMPORTANT: the AI SDK only calls a middleware's `wrapGenerate` for
 * `generateText`, never for `streamText`. So for streaming:
 *
 *   - Context injection still works through the middleware's `transformParams`
 *     (or you can set `system:` yourself via `getZepContext`), but
 *   - Persistence must be done from `onFinish` using `persistZepTurn`.
 *
 * This example uses the plain helpers (no middleware) to make the streaming
 * persistence pattern explicit: fetch context with `getZepContext`, set it as
 * `system`, and persist the completed turn from `onFinish`.
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
import { streamText } from "ai";
import {
  ensureZepUserAndThread,
  getZepContext,
  persistZepTurn,
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

  // 1. Fetch the Context Block and put it in the system prompt.
  const context = await getZepContext(client, threadId);
  const system = context
    ? `You are a helpful assistant. Relevant long-term memory:\n\n${context}`
    : "You are a helpful assistant.";

  // 2. Stream the response. Persist the completed turn from onFinish, which
  //    fires for both streamText and generateText (wrapGenerate does NOT).
  const result = streamText({
    model: openai("gpt-4o-mini"),
    system,
    prompt: userInput,
    onFinish: ({ text }) => {
      // Fire-and-forget; never block the stream on memory persistence.
      void persistZepTurn(client, threadId, {
        user: userInput,
        assistant: text,
        userName: "Bob",
      });
    },
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
