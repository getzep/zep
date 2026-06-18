import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import { randomUUID } from "node:crypto";
import {
  ensureZepUserAndThread,
  getZepContext,
  persistZepTurn,
  createZepTools,
} from "../src/index.js";
import { run } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;

// These tests hit the real Zep API and only run when ZEP_API_KEY is set.
// Ingestion is asynchronous, so they assert the calls succeed — not that a
// just-written fact is instantly retrievable.
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("provisions identity, persists, and retrieves without throwing", async () => {
    const client = new ZepClient({ apiKey });
    const userId = `zep-vercel-test-${randomUUID()}`;
    const threadId = `thread-${randomUUID()}`;

    const ready = await ensureZepUserAndThread({
      client,
      userId,
      threadId,
      firstName: "Test",
      lastName: "User",
    });
    expect(ready).toBe(true);

    const ctx = await persistZepTurn(
      client,
      threadId,
      { user: "My favorite color is teal.", userName: "Test User" },
      { returnContext: true },
    );
    expect(ctx === null || typeof ctx === "string").toBe(true);

    const context = await getZepContext(client, threadId);
    expect(typeof context).toBe("string");

    const { zepSearch } = createZepTools(client, { binding: { userId, threadId } });
    const result = await run(zepSearch, { query: "favorite color" });
    expect(Array.isArray(result.facts)).toBe(true);
  }, 30_000);
});
