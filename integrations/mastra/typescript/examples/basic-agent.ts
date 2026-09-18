/**
 * Basic Mastra agent with automatic Zep long-term memory.
 *
 * Demonstrates the full loop:
 *   1. Create a Zep user + thread and read their server-generated UUIDs
 *      (`createZepUserAndThread`).
 *   2. Build the Zep input/output processor pair (`createZepProcessors`).
 *   3. Attach the processors to a Mastra `Agent` via `inputProcessors` /
 *      `outputProcessors` — no tool-calling round-trip needed.
 *   4. Seed facts across turns (persisted automatically by the output
 *      processor), wait for asynchronous ingestion, then ask a question that
 *      requires recalling them (answered using context the input processor
 *      injects automatically).
 *
 * Known defect (ZEPAI-3605): a user that is created without a `userId` cannot
 * receive a thread message until the fix is deployed. The output processor
 * reports the failure and the agent continues.
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
import { Agent } from "@mastra/core/agent";
import { createZepProcessors, createZepUserAndThread } from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!ZEP_API_KEY) throw new Error("ZEP_API_KEY is not set.");
if (!OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not set.");

async function ask(agent: Agent, prompt: string): Promise<string> {
  console.log(`\nUser:  ${prompt}`);
  const result = await agent.generate(prompt);
  console.log(`Agent: ${result.text}`);
  return result.text;
}

async function main(): Promise<void> {
  const client = new ZepClient({ apiKey: ZEP_API_KEY });

  console.log("=".repeat(60));
  console.log("Mastra + Zep Automatic Memory Example");
  console.log("=".repeat(60));

  // 1. Create the user and the thread before the first turn. Zep generates
  //    the UUIDs; a real application stores them in its own database.
  const identity = await createZepUserAndThread({
    client,
    firstName: "Alice",
    lastName: "Smith",
    email: "alice@example.com",
  });
  if (!identity) throw new Error("Could not create the Zep user and thread.");
  console.log(`  User UUID:   ${identity.userUuid}`);
  console.log(`  Graph UUID:  ${identity.graphUuid}`);
  console.log(`  Thread UUID: ${identity.threadUuid}`);

  // 2. Build the Zep input/output processor pair bound to these UUIDs.
  const { inputProcessor, outputProcessor } = createZepProcessors({
    client,
    graphUuid: identity.graphUuid,
    threadUuid: identity.threadUuid,
  });

  // 3. Attach to a Mastra agent (id AND name are both required). No tools
  //    needed — the processors persist and inject context automatically.
  const agent = new Agent({
    id: "memory-agent",
    name: "Memory Agent",
    instructions:
      "You are a helpful assistant with long-term memory about the user, " +
      "injected automatically into your context. Personalize your replies " +
      "using what you know about the user.",
    model: "openai/gpt-5-mini",
    inputProcessors: [inputProcessor],
    outputProcessors: [outputProcessor],
  });

  // 4a. Seed facts — the output processor persists each turn automatically.
  console.log("\n--- Phase 1: Seeding facts ---");
  await ask(agent, "Hi! I live in Portland and love hiking.");
  await ask(agent, "I also work as a software engineer.");

  // 4b. Wait for asynchronous graph ingestion.
  const waitSeconds = 15;
  console.log(`\n--- Waiting ${waitSeconds}s for Zep graph processing ---`);
  await new Promise((resolve) => setTimeout(resolve, waitSeconds * 1000));

  // 4c. Recall — the input processor injects the Context Block automatically.
  console.log("\n--- Phase 2: Recall ---");
  await ask(agent, "What do you remember about where I live and what I do?");

  console.log("\nDone.");
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exitCode = 1;
});
