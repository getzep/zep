import "dotenv/config";
import { ZepClient } from "@getzep/zep-cloud";
import { demoEmailForUserId, splitDisplayName } from "../agent/lib/zep-user-fields";

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
  userId: string,
  expectedMin: number,
): Promise<void> {
  const started = Date.now();
  while (Date.now() - started < TIMEOUT_MS) {
    const response = await zep.graph.episode.getByUserId(userId, {
      lastn: 50,
    });
    const episodes = response.episodes ?? [];
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

  const userId = process.env.ZEP_DEMO_USER_ID?.trim() || "eve-demo-user";
  const userName = process.env.ZEP_DEMO_USER_NAME?.trim() || "Demo User";
  const { firstName, lastName } = splitDisplayName(userName);
  const threadId = `eve-smoke-${Date.now()}`;
  const zep = new ZepClient({
    apiKey,
    ...(process.env.ZEP_API_URL?.trim()
      ? { baseUrl: process.env.ZEP_API_URL.trim() }
      : {}),
  });

  // Do not log userId — sourced from env and flagged by CodeQL clear-text logging.
  console.log("Provisioning user + thread…", { threadId });

  try {
    await zep.user.add({
      userId,
      firstName,
      ...(lastName ? { lastName } : {}),
      email: demoEmailForUserId(userId),
    });
  } catch {
    await zep.user.get(userId);
  }

  await zep.thread.create({ threadId, userId });

  console.log("Adding preference messages…");
  await zep.thread.addMessages(threadId, {
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
  await waitForUserEpisodesProcessed(zep, userId, 2);

  const context = await zep.thread.getUserContext(threadId);
  const block = context.context?.trim() ?? "";
  console.log("\n=== Context Block ===\n");
  console.log(block || "(empty)");
  console.log(`\nThread: ${threadId}`);

  if (!block) {
    console.error(
      "Smoke test failed: Context Block is still empty after episodes processed.",
    );
    process.exit(1);
  }

  const search = await zep.graph.search({
    userId,
    query: "preferred billing service",
    scope: "auto",
    limit: 5,
  });
  const hit =
    Boolean(search.context?.trim()) ||
    (search.edges ?? []).some((e) =>
      /service a/i.test(e.fact ?? ""),
    );

  if (!hit) {
    console.error(
      "Smoke test failed: graph.search did not return Service A preference.",
    );
    process.exit(1);
  }

  console.log("\nSmoke test passed.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
