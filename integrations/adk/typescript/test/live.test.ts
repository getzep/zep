import { describe, it, expect } from "vitest";
import { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import { randomUUID } from "node:crypto";
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
const describeLive = apiKey ? describe : describe.skip;

describeLive("live Zep integration", () => {
  it("creates UUID resources, persists messages, searches memory, and cleans up", async () => {
    const client = new ZepClient({ apiKey });
    let userUuid: string | undefined;
    let threadUuid: string | undefined;
    let graphUuid: string | undefined;

    try {
      const user = await createUser(client, {
        firstName: "Integration",
        lastName: "Test",
        email: `zep-adk-${randomUUID()}@example.com`,
      });
      userUuid = user.userUuid;
      graphUuid = user.graphUuid;
      threadUuid = (await createThread(client, { userUuid })).threadUuid;

      const identity = { userUuid, threadUuid, firstName: "Integration" };
      const dedup = new TurnDedup();
      const request = fakeLlmRequest();
      const injected = await persistAndInject({
        zep: client,
        dedup,
        logger: defaultLogger,
        context: fakeContext({
          userId: userUuid,
          userText: "My favorite color is teal.",
          invocationId: "live-1",
        }),
        llmRequest: request,
        options: identity,
      });
      expect(injected === undefined || typeof injected === "string").toBe(true);

      const searchTool = new ZepGraphSearchTool({
        zep: client,
        graphUuid,
        scope: "edges",
      });
      const search = await searchTool.runAsync({
        args: { query: "favorite color" },
        toolContext: fakeContext({ userId: userUuid }) as never,
      });
      expect(typeof search).toBe("string");

      const afterCallback = createZepAfterModelCallback(client, { threadUuid });
      await expect(
        afterCallback({
          context: fakeContext({ userId: userUuid }) as unknown as Context,
          response: fakeLlmResponse({ text: "I will remember teal." }) as never,
        }),
      ).resolves.toBeUndefined();

      const memory = new ZepMemoryService({ zep: client, graphUuid });
      const result = await memory.searchMemory({
        appName: "zep-adk-live-test",
        userId: userUuid,
        query: "favorite color",
      });
      expect(Array.isArray(result.memories)).toBe(true);
    } finally {
      if (threadUuid) {
        try {
          await client.thread.delete(threadUuid);
        } catch {
          // Cleanup is best effort if the thread was already removed.
        }
      }
      if (userUuid) {
        try {
          await client.user.delete(userUuid);
        } catch {
          // Cleanup is best effort if the user was already removed.
        }
      }
    }
  }, 60_000);

  it("runs the createUser hook with the server-generated user UUID", async () => {
    const client = new ZepClient({ apiKey });
    let userUuid: string | undefined;
    const hookCalls: string[] = [];
    try {
      const user = await createUser(client, {
        email: `zep-adk-hook-${randomUUID()}@example.com`,
        onCreated: async (_zep, createdUserUuid) => {
          hookCalls.push(createdUserUuid);
        },
      });
      userUuid = user.userUuid;
      expect(hookCalls).toEqual([userUuid]);
    } finally {
      if (userUuid) {
        try {
          await client.user.delete(userUuid);
        } catch {
          // Cleanup is best effort if the user was already removed.
        }
      }
    }
  }, 60_000);
});
