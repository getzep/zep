/**
 * ADK agent with a model-callable Zep graph-search tool (TypeScript).
 *
 * Combines `ZepContextTool` (automatic per-turn context injection) with
 * `ZepGraphSearchTool` (the model decides when to search the user's graph).
 * This is an alternative to the callback wiring in `basic-agent.ts`.
 *
 * A live run requires GOOGLE_API_KEY (Gemini) in addition to ZEP_API_KEY.
 * Without GOOGLE_API_KEY the example builds and inspects the agent without a
 * model call.
 *
 * Prerequisites:
 *   npm install
 *   export ZEP_API_KEY="your-zep-api-key"
 *   export GOOGLE_API_KEY="your-google-api-key"   # required for a live run
 *
 * Run:
 *   npx tsx examples/graph-search-agent.ts
 */

import { randomUUID } from "node:crypto";
import { ZepClient } from "@getzep/zep-cloud";
import { LlmAgent } from "@google/adk";
import {
  ZepContextTool,
  ZepGraphSearchTool,
  createZepAfterModelCallback,
} from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
if (!ZEP_API_KEY) {
  console.error("ZEP_API_KEY is not set. See SETUP.md to create a Zep API key.");
  process.exit(1);
}

const suffix = randomUUID().slice(0, 8);
const USER_ID = `adk-ts-search-user-${suffix}`;
const SESSION_ID = `adk-ts-search-session-${suffix}`;

async function main(): Promise<void> {
  const zep = new ZepClient({ apiKey: ZEP_API_KEY });

  const agent = new LlmAgent({
    name: "zep_search_agent",
    model: "gemini-2.5-flash",
    description: "An assistant that can search the user's Zep knowledge graph.",
    instruction:
      "You are a helpful assistant. Use the zep_graph_search tool to look up " +
      "facts the user shared in earlier conversations when it helps.",
    tools: [
      // Automatic context injection on every turn (not model-callable).
      new ZepContextTool({ zep, userId: USER_ID, threadId: SESSION_ID }),
      // On-demand graph search (model-callable).
      new ZepGraphSearchTool({ zep, userId: USER_ID, scope: "edges", limit: 5 }),
    ],
    afterModelCallback: createZepAfterModelCallback(zep, {
      userId: USER_ID,
      threadId: SESSION_ID,
    }),
  });

  console.log("Built ADK agent with Zep tools:");
  console.log(`  agent.name = ${agent.name}`);
  console.log(`  tools      = ${agent.tools.length}`);
  console.log(`  user_id    = ${USER_ID}`);

  if (!process.env.GOOGLE_API_KEY) {
    console.log(
      "\nGOOGLE_API_KEY is not set — agent wiring verified without a model run.",
    );
  }
}

main().catch((error) => {
  console.error("Example failed:", error);
  process.exit(1);
});
