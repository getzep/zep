/**
 * Vercel AI SDK + Zep long-term memory — `generateText` example.
 *
 * Demonstrates the full loop:
 *   1. Create a Zep user and thread and keep their server-generated UUIDs.
 *   2. Attach `createZepTools` so the model can search and store memory as
 *      tool results.
 *   3. Persist each completed turn once with `createZepOnFinish`.
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

import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { generateText, stepCountIs } from "ai";
import {
  createZepOnFinish,
  createZepTools,
  createZepUserAndThread,
} from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!ZEP_API_KEY) throw new Error("ZEP_API_KEY is not set.");
if (!OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not set.");

const SYSTEM =
  "You are a helpful assistant with long-term memory powered by Zep. Use the " +
  "zepRemember tool to store new facts the user shares, and zepSearch to " +
  "look up specific details. Personalize your replies.";

async function main(): Promise<void> {
  const client = new ZepClient({ apiKey: ZEP_API_KEY });
  const identity = await createZepUserAndThread({
    client,
    firstName: "Alice",
    lastName: "Smith",
    email: "alice@example.com",
  });
  if (!identity) throw new Error("Could not create Zep user and thread.");
  const threadUuid = identity.threadUuid;

  console.log("=".repeat(60));
  console.log("Vercel AI SDK + Zep Memory Example (generateText)");
  console.log(`  User UUID:   ${identity.userUuid}`);
  console.log(`  Graph UUID:  ${identity.graphUuid}`);
  console.log(`  Thread UUID: ${identity.threadUuid}`);
  console.log("=".repeat(60));

  // Retrieved graph content stays in tool results, not system instructions.
  const model = openai.chat("gpt-5-mini");
  const tools = createZepTools(client, {
    binding: { graphUuid: identity.graphUuid, threadUuid: identity.threadUuid },
    defaultMessageName: "Alice",
  });

  async function ask(prompt: string): Promise<void> {
    console.log(`\nUser:  ${prompt}`);
    // Persist the whole turn once after the tool loop completes.
    const { text } = await generateText({
      model,
      system: SYSTEM,
      tools,
      stopWhen: stepCountIs(5),
      prompt,
      onFinish: createZepOnFinish({
        client,
        threadUuid,
        user: prompt,
        userName: "Alice",
      }),
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

  // 4c. Recall with the memory tools.
  console.log("\n--- Phase 2: Recall ---");
  await ask("What do you remember about where I live and what I do?");

  console.log("\nDone.");
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exitCode = 1;
});
