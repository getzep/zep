/**
 * Basic Mastra agent with Zep long-term memory.
 *
 * Demonstrates the full loop:
 *   1. Provision a Zep user + thread (`ensureZepUserAndThread`).
 *   2. Build the Zep tool set bound to that user/thread (`createZepToolset`).
 *   3. Attach the tools to a Mastra `Agent` as a `tools` record.
 *   4. Seed a fact, wait for asynchronous ingestion, then recall it.
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
import { Agent } from "@mastra/core/agent";
import { createZepToolset, ensureZepUserAndThread } from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!ZEP_API_KEY) throw new Error("ZEP_API_KEY is not set.");
if (!OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not set.");

const userId = `zep-mastra-example-${randomUUID().slice(0, 8)}`;
const threadId = `thread-${randomUUID().slice(0, 8)}`;

async function ask(agent: Agent, prompt: string): Promise<string> {
  console.log(`\nUser:  ${prompt}`);
  const result = await agent.generate(prompt);
  console.log(`Agent: ${result.text}`);
  return result.text;
}

async function main(): Promise<void> {
  const client = new ZepClient({ apiKey: ZEP_API_KEY });

  console.log("=".repeat(60));
  console.log("Mastra + Zep Memory Example");
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

  // 2. Build the Zep tool set bound to this user + thread.
  const { zepRemember, zepSearch, zepContext } = createZepToolset({
    client,
    binding: { userId, threadId },
    defaultMessageName: "Alice",
  });

  // 3. Attach to a Mastra agent (id AND name are both required).
  const agent = new Agent({
    id: "memory-agent",
    name: "Memory Agent",
    instructions:
      "You are a helpful assistant with long-term memory powered by Zep. " +
      "Use the zep-remember tool to store facts the user shares, the " +
      "zep-search tool to look up specific facts, and the zep-context tool " +
      "to recall everything known about the user. Personalize your replies.",
    model: "openai/gpt-4o-mini",
    tools: { zepRemember, zepSearch, zepContext },
  });

  // 4a. Seed facts.
  console.log("\n--- Phase 1: Seeding facts ---");
  await ask(agent, "Hi! Please remember that I live in Portland and love hiking.");
  await ask(agent, "Also remember that I work as a software engineer.");

  // 4b. Wait for asynchronous graph ingestion.
  const waitSeconds = 15;
  console.log(`\n--- Waiting ${waitSeconds}s for Zep graph processing ---`);
  await new Promise((resolve) => setTimeout(resolve, waitSeconds * 1000));

  // 4c. Recall.
  console.log("\n--- Phase 2: Recall ---");
  await ask(agent, "What do you remember about where I live and what I do?");

  console.log("\nDone.");
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exitCode = 1;
});
