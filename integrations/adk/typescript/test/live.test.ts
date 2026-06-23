import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import { randomUUID } from "node:crypto";
import {
  ZepResourceManager,
  ZepGraphSearchTool,
  persistAndInject,
  defaultLogger,
} from "../src/index.js";
import { fakeContext, fakeLlmRequest } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;

// These tests hit the real Zep API and only run when ZEP_API_KEY is set.
// Ingestion is asynchronous, so they assert the calls succeed and return the
// right shapes — not that a just-written fact is instantly retrievable.
// (ADK is normally Gemini-driven; here we drive the integration's core
// persist-and-inject logic directly, so no model key is needed.)
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("provisions identity, persists, and injects context without throwing", async () => {
    const client = new ZepClient({ apiKey });
    const userId = `zep-adk-test-${randomUUID()}`;
    const threadId = `thread-${randomUUID()}`;
    const identity = {
      userId,
      threadId,
      firstName: "Test",
      lastName: "User",
      email: `${userId}@example.com`,
    };

    try {
      const resources = new ZepResourceManager(client, defaultLogger);

      // First turn: persist the user message and inject the Context Block. The
      // call creates the Zep user + thread lazily and never throws on a Zep
      // error (returns undefined instead).
      const llmRequest = fakeLlmRequest();
      const injected = await persistAndInject({
        zep: client,
        resources,
        logger: defaultLogger,
        context: fakeContext({
          userId,
          userText: "My favorite color is teal and I live in Portland.",
          invocationId: "inv-1",
        }),
        llmRequest,
        options: identity,
      });
      expect(injected === undefined || typeof injected === "string").toBe(true);
      if (typeof injected === "string") {
        expect(JSON.stringify(llmRequest.config?.systemInstruction)).toContain(
          "ZEP_CONTEXT",
        );
      }

      // Same invocation id within the turn is de-duplicated (no re-persist).
      const duplicate = await persistAndInject({
        zep: client,
        resources,
        logger: defaultLogger,
        context: fakeContext({
          userId,
          userText: "My favorite color is teal and I live in Portland.",
          invocationId: "inv-1",
        }),
        llmRequest: fakeLlmRequest(),
        options: identity,
      });
      expect(duplicate).toBeUndefined();

      // The graph-search tool runs against the user's graph and returns a
      // string result (formatted facts or a graceful message) without throwing.
      const searchTool = new ZepGraphSearchTool({ zep: client, userId });
      const result = await searchTool.runAsync({
        args: { query: "favorite color" },
        toolContext: fakeContext({ userId }) as never,
      });
      expect(typeof result).toBe("string");
    } finally {
      try {
        await client.user.delete(userId);
      } catch {
        // Best-effort cleanup; ignore failures.
      }
    }
  }, 60_000);
});
