/**
 * Vercel AI SDK + Zep long-term memory — `generateText` example.
 *
 * Demonstrates the full loop:
 *   1. Provision a Zep user + thread (`ensureZepUserAndThread`).
 *   2. Wrap the model with `createZepMiddleware` so the user's Context Block is
 *      injected as a system message on every call, and the non-streaming turn is
 *      persisted automatically (`persist: true`).
 *   3. Attach `createZepTools` so the model can also search/store on demand.
 *   4. Seed a couple of facts across turns, wait for asynchronous ingestion,
 *      then ask the agent to recall them.
 *
 * Prerequisites:
 *   npm install
 *   export ZEP_API_KEY="your-zep-api-key"
 *   export OPENAI_API_KEY="your-openai-api-key"
 *
 * Run:
 *   npm run example
 */

import { randomUUID } from "node:crypto";
import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { generateText, stepCountIs, wrapLanguageModel } from "ai";
import {
  createZepMiddleware,
  createZepTools,
  ensureZepUserAndThread,
} from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!ZEP_API_KEY) throw new Error("ZEP_API_KEY is not set.");
if (!OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not set.");

const userId = `zep-vercel-example-${randomUUID().slice(0, 8)}`;
const threadId = `thread-${randomUUID().slice(0, 8)}`;

const SYSTEM =
  "You are a helpful assistant with long-term memory powered by Zep. " +
  "Relevant user context is injected for you automatically. Use the " +
  "zepRemember tool to store new facts the user shares, and zepSearch to " +
  "look up specific details. Personalize your replies.";

async function main(): Promise<void> {
  const client = new ZepClient({ apiKey: ZEP_API_KEY });

  console.log("=".repeat(60));
  console.log("Vercel AI SDK + Zep Memory Example (generateText)");
  console.log(`  User ID:   ${userId}`);
  console.log(`  Thread ID: ${threadId}`);
  console.log("=".repeat(60));

  // 1. Provision identity before the first turn.
  await ensureZepUserAndThread({
    client,
    userId,
    threadId,
    firstName: "Alice",
    lastName: "Smith",
    email: "alice@example.com",
  });

  // 2. Wrap the model: inject context on every call + persist non-streaming turns.
  //    We use the Chat Completions API (`openai.chat`) rather than the default
  //    Responses API so the multi-step tool loop also works on OpenAI Zero Data
  //    Retention (ZDR) organizations, which don't persist Responses item IDs.
  const model = wrapLanguageModel({
    model: openai.chat("gpt-4o-mini"),
    middleware: createZepMiddleware({
      client,
      threadId,
      persist: true,
      userName: "Alice",
    }),
  });

  // 3. Tools the model can call to search/store memory explicitly.
  const tools = createZepTools(client, {
    binding: { userId, threadId },
    defaultMessageName: "Alice",
  });

  async function ask(prompt: string): Promise<void> {
    console.log(`\nUser:  ${prompt}`);
    const { text } = await generateText({
      model,
      system: SYSTEM,
      tools,
      stopWhen: stepCountIs(5),
      prompt,
    });
    console.log(`Agent: ${text}`);
  }

  // 4a. Seed facts across a short conversation.
  console.log("\n--- Phase 1: Seeding facts ---");
  await ask("Hi! Please remember that I live in Portland and love hiking.");
  await ask("Also remember that I work as a software engineer.");

  // 4b. Wait for asynchronous graph ingestion.
  const waitSeconds = 15;
  console.log(`\n--- Waiting ${waitSeconds}s for Zep graph processing ---`);
  await new Promise((resolve) => setTimeout(resolve, waitSeconds * 1000));

  // 4c. Recall (context is injected by the middleware; the model may also search).
  console.log("\n--- Phase 2: Recall ---");
  await ask("What do you remember about where I live and what I do?");

  console.log("\nDone.");
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exitCode = 1;
});
