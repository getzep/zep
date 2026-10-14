import "dotenv/config";
import { ZepClient } from "@getzep/zep-cloud";

/**
 * Seed a standalone Zep graph with simple company-wide knowledge.
 * Pure Zep SDK — no Eve runtime required.
 *
 * Usage: npm run seed:company
 *
 * Re-runs are skipped when the graph already has at least as many episodes
 * as this seed list (avoids duplicate-fact pollution).
 */
const GRAPH_UUID = process.env.ZEP_COMPANY_GRAPH_UUID?.trim();

const EPISODES: string[] = [
  "Acme Platform is a B2B SaaS company that sells Service A, Service B, and Capability X.",
  "Service A is the preferred modern billing and checkout workflow for most customers.",
  "Service B is the legacy billing workflow. It remains available for customers who have not migrated.",
  "Capability X is the day-to-day productivity suite used for task tracking and internal ops.",
  "Company support hours are Monday through Friday, 9am to 6pm US Eastern time.",
  "The standard Enterprise plan includes priority support and a dedicated success manager.",
  "Refunds under $100 can be approved by any support agent; refunds of $100 or more need a team lead.",
  "Acme's primary status page is status.acme.example and should be checked during outages.",
  "The company HQ mailing address is 100 Market Street, Suite 400, San Francisco, CA 94105.",
  "Internal escalation channel for production incidents is the company Slack channel named incidents.",
];

async function ensureGraph(client: ZepClient): Promise<string> {
  if (GRAPH_UUID) {
    await client.graph.get(GRAPH_UUID);
    return GRAPH_UUID;
  }

  const graph = await client.graph.create({
    name: "Acme company knowledge",
    description: "Standalone graph of company-wide product and policy facts for the Eve demo.",
  });
  if (!graph.uuid) throw new Error("Zep did not return a graph UUID");
  return graph.uuid;
}

async function waitForTask(client: ZepClient, taskUuid: string): Promise<void> {
  const deadline = Date.now() + 240_000;
  while (Date.now() < deadline) {
    const task = await client.task.get(taskUuid);
    if (task.status === "succeeded") return;
    if (task.status === "failed" || task.status === "partial") {
      throw new Error(`Company graph ingestion task ${task.status}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  throw new Error("Timed out waiting for company graph episodes to process.");
}

async function main() {
  const apiKey = process.env.ZEP_API_KEY?.trim();
  if (!apiKey) {
    throw new Error("ZEP_API_KEY is required");
  }

  const client = new ZepClient({
    apiKey,
  });

  const graphUuid = await ensureGraph(client);
  console.log(`Seeding company graph: ${graphUuid}`);

  const existing = await client.graph.episode.list(graphUuid, { pageSize: 50 });
  const existingCount = existing.data.length;
  if (existingCount >= EPISODES.length) {
    console.log(
      `Graph already has ${existingCount} episodes (≥ ${EPISODES.length}). Skipping ingest to avoid duplicates.`,
    );
    console.log(
      `To re-seed, delete graph "${graphUuid}" in the Zep app and run without ZEP_COMPANY_GRAPH_UUID.`,
    );
  } else {
    const addedEpisodes = await Promise.all(
      EPISODES.map((data, index) =>
        client.graph.episode.add(graphUuid, {
          type: "text",
          data,
          sourceDescription: `company-seed-${index + 1}`,
        }),
      ),
    );
    const taskUuid = addedEpisodes.at(-1)?.task?.uuid;
    if (taskUuid) {
      console.log("Waiting for graph processing…");
      await waitForTask(client, taskUuid);
    }
  }

  const sample = await client.graph.getContext(graphUuid, {
    query: "refund policy Service A billing",
    maxCharacters: 5000,
    includeResults: true,
  });
  console.log("\nSample search:");
  if (sample.context?.trim()) {
    console.log(sample.context.trim());
  }

  console.log(`\nDone. Set ZEP_COMPANY_GRAPH_UUID=${graphUuid} in .env.`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
