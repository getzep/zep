import { randomUUID } from "node:crypto";
import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import {
  createZepToolset,
  createZepUserAndThread,
} from "../src/index.js";
import { run } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;

// These tests hit the real Zep API and only run when ZEP_API_KEY is set.
// Ingestion is asynchronous, so they assert the calls succeed — not that a
// just-written fact is instantly retrievable.
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("provisions identity and persists/retrieves without throwing", async () => {
    const client = new ZepClient({ apiKey });

    const identity = await createZepUserAndThread({
      client,
      // The Zep v4 API returns 404 from the thread message and context routes
      // when the user has no userId. The userId is a name, not an address.
      userId: `zep-mastra-live-${randomUUID().slice(0, 8)}`,
      firstName: "Test",
      lastName: "User",
    });
    expect(identity).not.toBeNull();
    if (!identity) return;

    const { zepRemember, zepContext } = createZepToolset({
      client,
      binding: { graphUuid: identity.graphUuid, threadUuid: identity.threadUuid },
      defaultMessageName: "Test User",
    });

    const stored = await run(zepRemember, {
      content: "My favorite color is teal.",
      role: "user",
    });
    expect(stored.stored).toBe(true);

    // Context retrieval should succeed (content may not be ingested yet).
    const context = await run(zepContext, {});
    expect(typeof context.context).toBe("string");
  }, 30_000);
});
