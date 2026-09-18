import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import {
  createZepUserAndThread,
  getZepContext,
  persistZepTurn,
  createZepTools,
} from "../src/index.js";
import { run } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;

// These tests hit the real Zep API. They are skipped until ZEPAI-3605 is
// fixed: the thread message and context routes return 404 for a user that
// has no `userId`, and the test creates the user without one, as the v4
// convention requires. Ingestion is asynchronous, so the test asserts the
// calls succeed — not that a just-written fact is instantly retrievable.
const describeLive = describe.skip;

describeLive("live Zep integration", () => {
  it("creates identity, persists, and retrieves without throwing", async () => {
    const client = new ZepClient({ apiKey });

    const identity = await createZepUserAndThread({
      client,
      firstName: "Test",
      lastName: "User",
    });
    expect(identity).not.toBeNull();
    const { graphUuid, threadUuid } = identity!;

    const ctx = await persistZepTurn(
      client,
      threadUuid,
      { user: "My favorite color is teal.", userName: "Test User" },
      { returnContext: true },
    );
    expect(ctx === null || typeof ctx === "string").toBe(true);

    const context = await getZepContext(client, threadUuid);
    expect(typeof context).toBe("string");

    const { zepSearch } = createZepTools(client, { binding: { graphUuid, threadUuid } });
    const result = await run(zepSearch, { query: "favorite color" });
    expect(Array.isArray(result.facts)).toBe(true);
  }, 30_000);
});
