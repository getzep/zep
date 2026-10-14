/**
 * Vercel AI SDK + Zep — `streamText` example with memory tools.
 *
 * Zep returns user, graph, and thread UUIDs at setup. The tools return retrieved
 * context as tool results. The onFinish callback persists the completed turn.
 * Do not place untrusted retrieved content in system or developer instructions.
 *
 * Prerequisites:
 *   export ZEP_API_KEY="your-zep-api-key"
 *   export OPENAI_API_KEY="your-openai-api-key"
 *
 * Run:
 *   npx tsx examples/stream-text.ts
 */

import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { stepCountIs, streamText } from "ai";
import {
  createZepOnFinish,
  createZepTools,
  createZepUserAndThread,
} from "../src/index.js";

const ZEP_API_KEY = process.env.ZEP_API_KEY;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;

if (!ZEP_API_KEY) throw new Error("ZEP_API_KEY is not set.");
if (!OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is not set.");

async function main(): Promise<void> {
  const client = new ZepClient({ apiKey: ZEP_API_KEY });
  const identity = await createZepUserAndThread({
    client,
    firstName: "Bob",
    lastName: "Jones",
  });
  if (!identity) throw new Error("Could not create Zep user and thread.");

  const userInput = "Hi! I just adopted a beagle named Cooper.";

  const tools = createZepTools(client, {
    binding: { graphUuid: identity.graphUuid, threadUuid: identity.threadUuid },
  });

  // Retrieved graph content stays in tool results, not system instructions.
  const result = streamText({
    model: openai.chat("gpt-5-mini"),
    prompt: userInput,
    tools,
    stopWhen: stepCountIs(5),
    onFinish: createZepOnFinish({
      client,
      threadUuid: identity.threadUuid,
      user: userInput,
      userName: "Bob",
    }),
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
