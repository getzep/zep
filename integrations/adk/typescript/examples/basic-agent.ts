/**
 * Basic Google ADK agent with Zep long-term memory (TypeScript).
 *
 * Wires the before- and after-model callbacks to persist conversation turns.
 * A model-callable graph-search tool retrieves user-provided memory without
 * placing it in the system instruction.
 *
 * The Zep user and thread are provisioned explicitly, out-of-band, via
 * `createUser` and `createThread` — before the agent runs its first turn.
 * The `onCreated` hook demonstrates one-time per-user setup. The turn path
 * itself (the before/after-model callbacks)
 * never creates the user or thread.
 *
 * The agent's model is Gemini, so a live run requires GOOGLE_API_KEY in
 * addition to ZEP_API_KEY. When GOOGLE_API_KEY is absent, the example builds
 * the fully-wired agent and Zep thread, prints the configuration, and exits —
 * so you can verify the integration wiring without a model call.
 *
 * Prerequisites:
 *   npm install
 *   export ZEP_API_KEY="your-zep-api-key"
 *   export GOOGLE_API_KEY="your-google-api-key"   # required for a live run
 *
 * Run:
 *   npm run example
 */

import { randomUUID } from "node:crypto";
import { ZepClient } from "@getzep/zep-cloud";
import {
  InMemoryRunner,
  LlmAgent,
  isFinalResponse,
  type Event,
} from "@google/adk";
import type { Content } from "@google/genai";
import {
  ZepGraphSearchTool,
  createZepAfterModelCallback,
  createZepBeforeModelCallback,
  createThread,
  createUser,
} from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const GOOGLE_API_KEY = process.env.GOOGLE_API_KEY;

if (!ZEP_API_KEY) {
  console.error("ZEP_API_KEY is not set. See SETUP.md to create a Zep API key.");
  process.exit(1);
}

const suffix = randomUUID().slice(0, 8);
const USER_ID = `adk-ts-example-user-${suffix}`;
const SESSION_ID = `adk-ts-example-session-${suffix}`;
const APP_NAME = "zep-adk-ts-example";

async function collectResponse(
  runner: InMemoryRunner,
  text: string,
): Promise<string> {
  const newMessage: Content = { role: "user", parts: [{ text }] };
  const chunks: string[] = [];
  for await (const event of runner.runAsync({
    userId: USER_ID,
    sessionId: SESSION_ID,
    newMessage,
  }) as AsyncGenerator<Event>) {
    if (isFinalResponse(event) && event.content?.parts) {
      for (const part of event.content.parts) {
        if (part.text) chunks.push(part.text);
      }
    }
  }
  return chunks.join(" ").trim();
}

/**
 * One-time setup for a newly created Zep user.
 *
 * Runs after `createUser` creates a new user. Use this hook for per-user
 * ontology or extraction setup.
 */
async function onUserCreated(_zep: ZepClient, userUuid: string): Promise<void> {
  console.log(`  [onCreated] Created Zep user ${userUuid}.`);
}

async function main(): Promise<void> {
  const zep = new ZepClient({ apiKey: ZEP_API_KEY });

  // Provision the Zep user and thread out-of-band, before the first turn.
  // The agent's turn path (the before/after-model callbacks) never creates
  // users or threads itself. See README.md for the v4 migration notes.
  console.log("--- Provisioning Zep user + thread ---\n");
  let userUuid = "";
  let graphUuid = "";
  let threadUuid = "";
  try {
    const createdUser = await createUser(zep, {
      firstName: "Alice",
      lastName: "Smith",
      email: "alice@example.com",
      onCreated: onUserCreated,
    });
    userUuid = createdUser.userUuid;
    graphUuid = createdUser.graphUuid;
    threadUuid = (await createThread(zep, { userUuid })).threadUuid;
  } catch (error) {
    console.error("Provisioning Zep user/thread failed:", error);
    process.exit(1);
  }

  const agent = new LlmAgent({
    name: "zep_memory_agent",
    model: "gemini-2.5-flash",
    description: "An assistant that can search the user's Zep knowledge graph.",
    instruction:
      "You are a helpful assistant. Use the zep_graph_search tool to look up " +
      "facts the user shared in earlier conversations when it helps.",
    tools: [new ZepGraphSearchTool({ zep, graphUuid })],
    beforeModelCallback: createZepBeforeModelCallback(zep, {
      userUuid,
      threadUuid,
      firstName: "Alice",
      lastName: "Smith",
      // Persist turns without injecting user-provided content into the system instruction.
      contextBuilder: async () => undefined,
    }),
    afterModelCallback: createZepAfterModelCallback(zep, {
      userUuid,
      threadUuid,
    }),
  });

  console.log("=".repeat(60));
  console.log("ADK + Zep Memory Example (TypeScript)");
  console.log("=".repeat(60));
  console.log(`  User ID:    ${USER_ID}`);
  console.log(`  Session ID: ${SESSION_ID}`);
  console.log("=".repeat(60));

  if (!GOOGLE_API_KEY) {
    console.log(
      "\nGOOGLE_API_KEY is not set — skipping the live model run.\n" +
        "The agent above is fully wired with Zep memory. Set GOOGLE_API_KEY " +
        "to run the conversation.",
    );
    return;
  }

  const runner = new InMemoryRunner({ agent, appName: APP_NAME });
  await runner.sessionService.createSession({
    appName: APP_NAME,
    userId: USER_ID,
    sessionId: SESSION_ID,
  });

  console.log("\n--- Phase 1: Seeding facts ---\n");
  for (const message of [
    "My name is Alice and I'm a software engineer.",
    "I live in Portland and love hiking on weekends.",
  ]) {
    console.log(`User:  ${message}`);
    console.log(`Agent: ${await collectResponse(runner, message)}\n`);
  }

  const waitSeconds = 15;
  console.log(`--- Waiting ${waitSeconds}s for Zep graph processing ---\n`);
  await new Promise((resolve) => setTimeout(resolve, waitSeconds * 1000));

  console.log("--- Phase 2: Testing memory recall ---\n");
  for (const message of ["What do I do for work?", "Where do I live?"]) {
    console.log(`User:  ${message}`);
    console.log(`Agent: ${await collectResponse(runner, message)}\n`);
  }

  console.log("Done.");
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exit(1);
});
