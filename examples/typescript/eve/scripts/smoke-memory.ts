import "dotenv/config";
import { ZepClient } from "@getzep/zep-cloud";
import { demoEmailForUserKey, splitDisplayName } from "../agent/lib/zep-user-fields";

/**
 * Standalone smoke test (no Eve runtime required).
 * Verifies Zep user/thread provisioning, ingest, processing, and context retrieval.
 *
 * Usage: npm run smoke
 */

const POLL_MS = 3000;
const TIMEOUT_MS = 180_000;

async function waitForUserEpisodesProcessed(
  zep: ZepClient,
  graphUuid: string,
  expectedMin: number,
): Promise<void> {
  const started = Date.now();
  while (Date.now() - started < TIMEOUT_MS) {
    const episodes = [];
    for await (const episode of await zep.graph.episode.list(graphUuid, {
      limit: 50,
      body: {},
    })) {
      episodes.push(episode);
      if (episodes.length >= 50) break;
    }
    const processed = episodes.filter((e) => e.processed).length;
    const pending = episodes.length - processed;
    console.log(
      `Episodes: ${episodes.length} total, ${processed} processed, ${pending} pending`,
    );
    if (episodes.length >= expectedMin && pending === 0) {
      return;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
  throw new Error(
    `Timed out after ${TIMEOUT_MS}ms waiting for user episodes to process.`,
  );
}

async function main() {
  const apiKey = process.env.ZEP_API_KEY?.trim();
  if (!apiKey) {
    throw new Error("ZEP_API_KEY is required");
  }

  const userKey = process.env.ZEP_DEMO_USER_KEY?.trim() || "eve-demo-user";
  const userName = process.env.ZEP_DEMO_USER_NAME?.trim() || "Demo User";
  const { firstName, lastName } = splitDisplayName(userName);
  const zep = new ZepClient({
    apiKey,
    ...(process.env.ZEP_API_URL?.trim()
      ? { baseUrl: process.env.ZEP_API_URL.trim() }
      : {}),
  });

  console.log("Provisioning user + thread…");

  // v4 gives every user, graph, and thread a server-generated UUID.
  const user = await zep.user.create({
    firstName,
    ...(lastName ? { lastName } : {}),
    email: demoEmailForUserKey(userKey),
  });
  const userUuid = user.uuid!;
  const graphUuid = user.graphUuid!;

  const thread = await zep.thread.create({ userUuid });
  const threadUuid = thread.uuid!;

  console.log("Adding preference messages…");
  await zep.thread.addMessages(threadUuid, {
    messages: [
      {
        role: "user",
        name: userName,
        content: "I prefer Service A over Service B for billing.",
      },
      {
        role: "assistant",
        name: "Eve Agent",
        content: "Got it — I'll use Service A for billing going forward.",
      },
    ],
  });

  console.log("Polling until graph episodes are processed…");
  // Two chat messages → at least two episodes on the user graph.
  await waitForUserEpisodesProcessed(zep, graphUuid, 2);

  const context = await zep.thread.getContext(threadUuid);
  const block = context.context?.trim() ?? "";
  console.log("\n=== Context Block ===\n");
  console.log(block || "(empty)");
  console.log(`\nThread: ${threadUuid}`);

  if (!block) {
    console.error(
      "Smoke test failed: Context Block is still empty after episodes processed.",
    );
    process.exit(1);
  }

  const search = await zep.graph.getContext(graphUuid, {
    query: "preferred billing service",
    includeResults: true,
  });
  const hit =
    Boolean(search.context?.trim()) ||
    (search.results?.edges ?? []).some((e) =>
      /service a/i.test(e.fact ?? ""),
    );

  if (!hit) {
    console.error(
      "Smoke test failed: graph.getContext did not return Service A preference.",
    );
    process.exit(1);
  }

  console.log("\nSmoke test passed.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
