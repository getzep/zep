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
const GRAPH_ID = process.env.ZEP_COMPANY_GRAPH_ID?.trim() || "eve-demo-company";

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

async function ensureGraph(client: ZepClient, graphId: string): Promise<void> {
  try {
    await client.graph.create({
      graphId,
      name: "Acme company knowledge",
      description: "Standalone graph of company-wide product and policy facts for the Eve demo.",
    });
    console.log(`Created graph: ${graphId}`);
  } catch (error) {
    try {
      await client.graph.get(graphId);
      console.log(`Graph already exists: ${graphId}`);
    } catch {
      throw error;
    }
  }
}

async function waitUntilProcessed(
  client: ZepClient,
  graphId: string,
  expectedMin: number,
): Promise<void> {
  const started = Date.now();
  let lastPending = expectedMin;
  while (Date.now() - started < 240_000) {
    const response = await client.graph.episode.getByGraphId(graphId, {
      lastn: 50,
    });
    const episodes = response.episodes ?? [];
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

  console.log(`Seeding company graph: ${GRAPH_ID}`);
  await ensureGraph(client, GRAPH_ID);

  const existing = await client.graph.episode.getByGraphId(GRAPH_ID, {
    lastn: 50,
  });
  const existingCount = existing.episodes?.length ?? 0;
  if (existingCount >= EPISODES.length) {
    console.log(
      `Graph already has ${existingCount} episodes (≥ ${EPISODES.length}). Skipping ingest to avoid duplicates.`,
    );
    console.log(
      `To re-seed, delete graph "${GRAPH_ID}" in the Zep app (or use a new ZEP_COMPANY_GRAPH_ID) and re-run.`,
    );
  } else {
    for (const [index, data] of EPISODES.entries()) {
      const episode = await client.graph.add({
        graphId: GRAPH_ID,
        type: "text",
        data,
        sourceDescription: `company-seed-${index + 1}`,
      });
      console.log(`Added episode ${index + 1}/${EPISODES.length}: ${episode.uuid}`);
    }

    console.log("Waiting for graph processing…");
    await waitUntilProcessed(client, GRAPH_ID, EPISODES.length);
  }

  const sample = await client.graph.search({
    graphId: GRAPH_ID,
    query: "refund policy Service A billing",
    scope: "auto",
    limit: 5,
    returnRawResults: true,
  });
  console.log("\nSample search:");
  if (sample.context?.trim()) {
    console.log(sample.context.trim());
  } else {
    for (const edge of sample.edges ?? []) {
      console.log(`- ${edge.fact}`);
    }
  }

  console.log(`\nDone. Set ZEP_COMPANY_GRAPH_ID=${GRAPH_ID} in .env (already the default).`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
