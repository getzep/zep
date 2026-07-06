import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import { randomUUID } from "node:crypto";
import {
  ZepGraphSearchTool,
  ZepMemoryService,
  createZepAfterModelCallback,
  ensureThread,
  ensureUser,
  persistAndInject,
  defaultLogger,
} from "../src/index.js";
import { TurnDedup } from "../src/resources.js";
import { fakeContext, fakeLlmRequest, fakeLlmResponse } from "./helpers.js";

const apiKey = process.env.ZEP_API_KEY;

// These tests hit the real Zep API and only run when ZEP_API_KEY is set.
// Ingestion is asynchronous, so they assert the calls succeed and return the
// right shapes — not that a just-written fact is instantly retrievable.
// (ADK is normally Gemini-driven; here we drive the integration's core
// persist-and-inject logic directly, so no model key is needed.)
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("provisions identity out-of-band, persists, and injects context without throwing", async () => {
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
      // Explicit, out-of-band provisioning — the turn path itself never
      // creates the Zep user or thread.
      const userCreated = await ensureUser(client, {
        userId,
        firstName: identity.firstName,
        lastName: identity.lastName,
        email: identity.email,
      });
      expect(userCreated).toBe(true);

      const threadCreated = await ensureThread(client, { threadId, userId });
      expect(threadCreated).toBe(true);

      // ensure-twice: the second call against the same IDs reports "already
      // exists" (false) rather than throwing.
      const userCreatedAgain = await ensureUser(client, { userId });
      expect(userCreatedAgain).toBe(false);

      const threadCreatedAgain = await ensureThread(client, {
        threadId,
        userId,
      });
      expect(threadCreatedAgain).toBe(false);

      const dedup = new TurnDedup();

      // First turn: persist the user message and inject the Context Block.
      // The call never creates the Zep user/thread and never throws on a Zep
      // error (returns undefined instead).
      const llmRequest = fakeLlmRequest();
      const injected = await persistAndInject({
        zep: client,
        dedup,
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
        dedup,
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

      // Assistant-turn persistence: createZepAfterModelCallback persists the
      // model's reply to the same thread. Must not throw.
      const afterCallback = createZepAfterModelCallback(client, {
        threadId,
      });
      const afterResult = await afterCallback({
        context: fakeContext({ userId }) as unknown as Context,
        response: fakeLlmResponse({
          text: "Noted — teal it is, and Portland sounds lovely.",
        }) as never,
      });
      expect(afterResult).toBeUndefined();

      // ZepMemoryService.searchMemory (the ADK-native memory extension
      // point) round-trips against the live client without rejecting.
      // Ingestion is asynchronous, so the array may be empty or populated.
      const memoryService = new ZepMemoryService({ zep: client });
      const memoryResponse = await memoryService.searchMemory({
        appName: "zep-adk-live-test",
        userId,
        query: "favorite color",
      });
      expect(Array.isArray(memoryResponse.memories)).toBe(true);
    } finally {
      try {
        await client.user.delete(userId);
      } catch {
        // Best-effort cleanup; ignore failures.
      }
    }
  }, 60_000);

  it("fires the onCreated hook exactly once for a freshly created user", async () => {
    const client = new ZepClient({ apiKey });
    const userId = `zep-adk-test-oncreated-${randomUUID()}`;

    const hookCalls: string[] = [];
    const onCreated = async (_zep: ZepClient, createdUserId: string) => {
      hookCalls.push(createdUserId);
    };

    try {
      const created = await ensureUser(client, {
        userId,
        firstName: "Hook",
        lastName: "Test",
        email: `${userId}@example.com`,
        onCreated,
      });
      expect(created).toBe(true);
      expect(hookCalls).toEqual([userId]);

      // A second call against the same (now-existing) user must not re-fire
      // the hook.
      const createdAgain = await ensureUser(client, {
        userId,
        onCreated,
      });
      expect(createdAgain).toBe(false);
      expect(hookCalls).toEqual([userId]);
    } finally {
      try {
        await client.user.delete(userId);
      } catch {
        // Best-effort cleanup; ignore failures.
      }
    }
  }, 60_000);
});
