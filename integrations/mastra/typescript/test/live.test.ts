import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import { randomUUID } from "node:crypto";
import {
  createZepToolset,
  ensureZepUserAndThread,
} from "../src/index.js";

const apiKey = process.env.ZEP_API_KEY;
const ctx = {} as never;

// These tests hit the real Zep API and only run when ZEP_API_KEY is set.
// Ingestion is asynchronous, so they assert the calls succeed — not that a
// just-written fact is instantly retrievable.
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("provisions identity and persists/retrieves without throwing", async () => {
    const client = new ZepClient({ apiKey });
    const userId = `zep-mastra-test-${randomUUID()}`;
    const threadId = `thread-${randomUUID()}`;

    const ready = await ensureZepUserAndThread({
      client,
      userId,
      threadId,
      firstName: "Test",
      lastName: "User",
    });
    expect(ready).toBe(true);

    const { zepRemember, zepContext } = createZepToolset({
      client,
      binding: { userId, threadId },
      defaultMessageName: "Test User",
    });

    const stored = await zepRemember.execute!(
      { content: "My favorite color is teal.", role: "user" },
      ctx,
    );
    expect(stored.stored).toBe(true);

    // Context retrieval should succeed (content may not be ingested yet).
    const context = await zepContext.execute!({}, ctx);
    expect(typeof context.context).toBe("string");
  }, 30_000);
});
