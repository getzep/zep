import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import {
  createZepUserAndThread,
  getZepContext,
  persistZepTurn,
  createZepSearchTool,
  createZepTools,
} from "../src/index.js";
import { run } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;

// These tests hit the real Zep API and only run when ZEP_API_KEY is set.
// Ingestion is asynchronous, so they assert the calls succeed — not that a
// just-written fact is instantly retrievable.
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("creates UUID resources, persists a turn, retrieves context, and searches every scope", async () => {
    const client = new ZepClient({ apiKey });
    const identity = await createZepUserAndThread({
      client,
      firstName: "Test",
      lastName: "User",
    });
    expect(identity).not.toBeNull();
    if (!identity) return;

    try {
      const ctx = await persistZepTurn(
        client,
        identity.threadUuid,
        {
          user: "My favorite color is teal.",
          assistant: "I will remember that your favorite color is teal.",
          userName: "Test User",
        },
        { returnContext: true },
      );
      expect(ctx === null || typeof ctx === "string").toBe(true);
      expect(typeof await getZepContext(client, identity.threadUuid)).toBe("string");

      const binding = { graphUuid: identity.graphUuid, threadUuid: identity.threadUuid };
      const tools = createZepTools(client, { binding });
      expect(Array.isArray((await run(tools.zepSearch, { query: "favorite color" })).facts)).toBe(
        true,
      );

      for (const scope of [
        "edges",
        "nodes",
        "episodes",
        "observations",
        "thread_summaries",
        "auto",
      ] as const) {
        const search = createZepSearchTool({ client, binding, scope });
        const result = await run(search, { query: "favorite color" });
        expect(Array.isArray(result.facts)).toBe(true);
      }
    } finally {
      await client.thread.delete(identity.threadUuid).catch(() => undefined);
      await client.user.delete(identity.userUuid).catch(() => undefined);
    }
  }, 90_000);
});
