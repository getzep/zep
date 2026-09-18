import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import {
  ZepGraphSearchTool,
  ZepMemoryService,
  createThread,
  createUser,
  createZepAfterModelCallback,
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
  // Skipped because of ZEPAI-3605: a user that is created without a
  // `user_id` cannot receive a thread message until the fix is deployed.
  it.skip("provisions identity out-of-band, persists, and injects context without throwing", async () => {
    const client = new ZepClient({ apiKey });
    let userUuid: string | undefined;

    try {
      // Explicit, out-of-band provisioning — the turn path itself never
      // creates the Zep user or thread. Zep v4 assigns every UUID on the
      // server, and the application keeps the UUIDs it gets back.
      const user = await createUser(client, {
        firstName: "Test",
        lastName: "User",
        email: `zep-adk-test-${Date.now()}@example.com`,
      });
      userUuid = user.userUuid;
      expect(user.userUuid).toBeTruthy();
      expect(user.graphUuid).toBeTruthy();

      const thread = await createThread(client, { userUuid: user.userUuid });
      expect(thread.threadUuid).toBeTruthy();

      const identity = {
        userUuid: user.userUuid,
        threadUuid: thread.threadUuid,
        firstName: "Test",
        lastName: "User",
      };
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
          userId: user.userUuid,
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
          userId: user.userUuid,
          userText: "My favorite color is teal and I live in Portland.",
          invocationId: "inv-1",
        }),
        llmRequest: fakeLlmRequest(),
        options: identity,
      });
      expect(duplicate).toBeUndefined();

      // The graph-search tool runs against the graph of the user, addressed
      // by the graph UUID from the create response, and returns a string
      // result (formatted facts or a graceful message) without throwing.
      const searchTool = new ZepGraphSearchTool({
        zep: client,
        graphUuid: user.graphUuid,
      });
      const result = await searchTool.runAsync({
        args: { query: "favorite color" },
        toolContext: fakeContext({ userId: user.userUuid }) as never,
      });
      expect(typeof result).toBe("string");

      // Assistant-turn persistence: createZepAfterModelCallback persists the
      // model's reply to the same thread. Must not throw.
      const afterCallback = createZepAfterModelCallback(client, {
        threadUuid: thread.threadUuid,
      });
      const afterResult = await afterCallback({
        context: fakeContext({ userId: user.userUuid }) as unknown as Context,
        response: fakeLlmResponse({
          text: "Noted — teal it is, and Portland sounds lovely.",
        }) as never,
      });
      expect(afterResult).toBeUndefined();

      // ZepMemoryService.searchMemory (the ADK-native memory extension
      // point) round-trips against the live client without rejecting.
      // Ingestion is asynchronous, so the array may be empty or populated.
      const memoryService = new ZepMemoryService({
        zep: client,
        graphUuid: user.graphUuid,
      });
      const memoryResponse = await memoryService.searchMemory({
        appName: "zep-adk-live-test",
        userId: user.userUuid,
        query: "favorite color",
      });
      expect(Array.isArray(memoryResponse.memories)).toBe(true);
    } finally {
      if (userUuid) {
        try {
          await client.user.delete(userUuid);
        } catch {
          // Best-effort cleanup; ignore failures.
        }
      }
    }
  }, 60_000);

  it("fires the onCreated hook exactly once with the UUID of the new user", async () => {
    const client = new ZepClient({ apiKey });
    let userUuid: string | undefined;

    const hookCalls: string[] = [];
    const onCreated = async (_zep: ZepClient, createdUserUuid: string) => {
      hookCalls.push(createdUserUuid);
    };

    try {
      const user = await createUser(client, {
        firstName: "Hook",
        lastName: "Test",
        email: `zep-adk-test-oncreated-${Date.now()}@example.com`,
        onCreated,
      });
      userUuid = user.userUuid;
      expect(hookCalls).toEqual([user.userUuid]);
    } finally {
      if (userUuid) {
        try {
          await client.user.delete(userUuid);
        } catch {
          // Best-effort cleanup; ignore failures.
        }
      }
    }
  }, 60_000);
});
