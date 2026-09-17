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

async function ensureGraph(
  client: ZepClient,
  graphUuid?: string,
): Promise<string> {
  // v4 addresses a graph by a server-generated UUID. The seed creates the
  // graph one time, and the application keeps the UUID.
  if (graphUuid) {
    const graph = await client.graph.get(graphUuid);
    console.log(`Graph already exists: ${graph.uuid}`);
    return graphUuid;
  }

  const graph = await client.graph.create({
    name: "Acme company knowledge",
    description: "Standalone graph of company-wide product and policy facts for the Eve demo.",
  });
  if (!graph.uuid) {
    throw new Error("Zep did not return a graph UUID");
  }
  console.log(`Created graph: ${graph.uuid}`);
  return graph.uuid;
}

async function listEpisodes(client: ZepClient, graphUuid: string) {
  const episodes = [];
  for await (const episode of await client.graph.episode.list(graphUuid, {
    limit: 50,
    body: {},
  })) {
    episodes.push(episode);
    if (episodes.length >= 50) break;
  }
  return episodes;
}

async function waitUntilProcessed(
  client: ZepClient,
  graphUuid: string,
  expectedMin: number,
): Promise<void> {
  const started = Date.now();
  let lastPending = expectedMin;
  while (Date.now() - started < 240_000) {
    const episodes = await listEpisodes(client, graphUuid);
    const processed = episodes.filter((e) => e.processed).length;
    const pending = episodes.length - processed;
    lastPending = pending;
    console.log(
      `Episodes: ${episodes.length} total, ${processed} processed, ${pending} pending`,
    );
    if (episodes.length >= expectedMin && pending === 0) {
      console.log("All episodes processed.");
      return;
    }
    // Demo seed: accept near-complete graphs so one stuck episode doesn't block.
    if (episodes.length >= expectedMin && processed >= expectedMin - 1 && Date.now() - started > 90_000) {
      console.warn(
        `Proceeding with ${processed}/${episodes.length} processed (1 may still be pending).`,
      );
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new Error(
    `Timed out waiting for company graph episodes to process (${lastPending} still pending).`,
  );
}

async function main() {
  const apiKey = process.env.ZEP_API_KEY?.trim();
  if (!apiKey) {
    throw new Error("ZEP_API_KEY is required");
  }

  const client = new ZepClient({
    apiKey,
    ...(process.env.ZEP_API_URL?.trim()
      ? { baseUrl: process.env.ZEP_API_URL.trim() }
      : {}),
  });

  console.log("Seeding the company graph…");
  const graphUuid = await ensureGraph(client, GRAPH_UUID);

  const existing = await listEpisodes(client, graphUuid);
  const existingCount = existing.length;
  if (existingCount >= EPISODES.length) {
    console.log(
      `Graph already has ${existingCount} episodes (≥ ${EPISODES.length}). Skipping ingest to avoid duplicates.`,
    );
    console.log(
      `To re-seed, delete graph "${graphUuid}" in the Zep app (or clear ZEP_COMPANY_GRAPH_UUID) and re-run.`,
    );
  } else {
    for (const [index, data] of EPISODES.entries()) {
      const result = await client.graph.episode.add(graphUuid, {
        type: "text",
        data,
        sourceDescription: `company-seed-${index + 1}`,
      });
      console.log(
        `Added episode ${index + 1}/${EPISODES.length}: ${result.episode?.uuid}`,
      );
    }

    console.log("Waiting for graph processing…");
    await waitUntilProcessed(client, graphUuid, EPISODES.length);
  }

  const sample = await client.graph.getContext(graphUuid, {
    query: "refund policy Service A billing",
    includeResults: true,
  });
  console.log("\nSample search:");
  if (sample.context?.trim()) {
    console.log(sample.context.trim());
  } else {
    for (const edge of sample.results?.edges ?? []) {
      console.log(`- ${edge.fact}`);
    }
  }

  console.log(`\nDone. Set ZEP_COMPANY_GRAPH_UUID=${graphUuid} in .env.`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
