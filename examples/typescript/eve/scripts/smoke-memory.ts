import "dotenv/config";
import { ZepClient } from "@getzep/zep-cloud";
import { demoEmailForUserId, splitDisplayName } from "../agent/lib/zep-user-fields";

/**
 * Standalone smoke test (no Eve runtime required).
 * Verifies Zep user/thread provisioning, ingest, processing, and context retrieval.
 *
 * Usage: npm run smoke
 */

const TIMEOUT_MS = 1_200_000;

async function waitForTask(
  zep: ZepClient,
  taskUuid: string,
): Promise<void> {
  const started = Date.now();
  let nextStatusReport = started + 30_000;
  while (Date.now() - started < TIMEOUT_MS) {
    const task = await zep.task.get(taskUuid);
    if (task.status === "succeeded") return;
    if (task.status === "partial") {
      console.warn(`Warning: task ${taskUuid} ended with partial status.`);
      return;
    }
    if (task.status === "failed") {
      throw new Error(`Message ingestion task failed: ${task.error?.message ?? ""}`);
    }
    if (Date.now() >= nextStatusReport) {
      console.log(`Task ${taskUuid} status=${task.status}; still waiting.`);
      nextStatusReport = Date.now() + 30_000;
    }
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  throw new Error(
    `Timed out after ${TIMEOUT_MS}ms waiting for messages to process.`,
  );
}

async function main() {
  const apiKey = process.env.ZEP_API_KEY?.trim();
  if (!apiKey) {
    throw new Error("ZEP_API_KEY is required");
  }

  const userName = process.env.ZEP_DEMO_USER_NAME?.trim() || "Demo User";
  const { firstName, lastName } = splitDisplayName(userName);
  const zep = new ZepClient({
    apiKey,
  });

  const user = await zep.user.create({
    firstName,
    ...(lastName ? { lastName } : {}),
    email: demoEmailForUserId(`eve-smoke-${Date.now()}`),
  });
  const userUuid = user.uuid;
  if (!userUuid) throw new Error("Zep did not return a user UUID");
  const thread = await zep.thread.create({ userUuid });
  const threadUuid = thread.uuid;
  if (!threadUuid) throw new Error("Zep did not return a thread UUID");
  console.log("Provisioned user and thread.", { userUuid, threadUuid });

  console.log("Adding preference messages…");
  const result = await zep.thread.addMessages(threadUuid, {
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

  if (result.task?.uuid) await waitForTask(zep, result.task.uuid);

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

  const search = await zep.graph.getContext(userUuid, {
    query: "preferred billing service",
    maxCharacters: 5000,
    includeResults: true,
  });
  const hit = /service a/i.test(search.context ?? "");

  if (!hit) {
    console.error(
      "Smoke test failed: graph.getContext did not return the Service A preference.",
    );
    process.exit(1);
  }

  console.log("\nSmoke test passed.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
