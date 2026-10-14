import { describe, it, expect } from "vitest";
import { randomUUID } from "node:crypto";
import { ZepClient } from "@getzep/zep-cloud";
import { createZepContextTool, createZepRememberTool, createZepUserAndThread } from "../src/index.js";
import { run } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("creates UUID resources, persists both roles, and retrieves context", async () => {
    const client = new ZepClient({ apiKey: apiKey! });
    const identity = await createZepUserAndThread({
      client,
      firstName: "Mastra",
      lastName: `Test-${randomUUID()}`,
    });
    if (!identity) throw new Error("Could not create the live-test user and thread.");

    try {
      const remember = createZepRememberTool({
        client,
        binding: identity,
        defaultMessageName: "Mastra Test",
      });
      const context = createZepContextTool({
        client,
        binding: identity,
      });

      const userStored = await run(remember, {
        content: `My favorite color is teal ${randomUUID()}.`,
        role: "user",
      });
      const assistantStored = await run(remember, {
        content: "I will remember that.",
        role: "assistant",
      });
      expect(userStored.stored).toBe(true);
      expect(assistantStored.stored).toBe(true);

      const retrieved = await run(context, {});
      expect(typeof retrieved.context).toBe("string");
    } finally {
      await client.thread.delete(identity.threadUuid);
      await client.user.delete(identity.userUuid);
    }
  }, 30_000);
});
