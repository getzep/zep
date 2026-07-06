import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context, LlmRequest, LlmResponse } from "@google/adk";
import { createZepCallbacks } from "../src/callbacks.js";
import {
  fakeContext,
  fakeLlmRequest,
  fakeLlmResponse,
  mockZepClient,
  silentLogger,
} from "./helpers.js";

describe("createZepCallbacks", () => {
  it("returns a paired before/after callback and a shared dedup guard", () => {
    const { client } = mockZepClient({ addMessagesContext: "ctx" });
    const { beforeModelCallback, afterModelCallback, dedup } =
      createZepCallbacks(client as unknown as ZepClient, {
        logger: silentLogger,
        userId: "u",
        threadId: "t",
      });

    expect(typeof beforeModelCallback).toBe("function");
    expect(typeof afterModelCallback).toBe("function");
    expect(dedup).toBeDefined();
  });

  it("never calls user.add or thread.create via either paired callback", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const { beforeModelCallback, afterModelCallback } = createZepCallbacks(
      client as unknown as ZepClient,
      { logger: silentLogger, userId: "u", threadId: "t" },
    );

    await beforeModelCallback({
      context: fakeContext({
        userId: "u",
        sessionId: "t",
        invocationId: "inv-1",
        userText: "hello",
      }) as unknown as Context,
      request: fakeLlmRequest() as unknown as LlmRequest,
    });
    await afterModelCallback({
      context: fakeContext({
        userId: "u",
        sessionId: "t",
        invocationId: "inv-1",
      }) as unknown as Context,
      response: fakeLlmResponse({ text: "hi there" }) as unknown as LlmResponse,
    });

    // The turn path never provisions resources.
    expect(mocks.userAdd).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();

    // One user message + one assistant message persisted.
    expect(mocks.addMessages).toHaveBeenCalledTimes(2);
  });

  it("shares the dedup guard: a tool-loop turn persists the user message once", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const { beforeModelCallback } = createZepCallbacks(
      client as unknown as ZepClient,
      { logger: silentLogger, userId: "u", threadId: "t" },
    );

    const userContent = {
      role: "user" as const,
      parts: [{ text: "do a thing" }],
    };
    for (let i = 0; i < 3; i++) {
      await beforeModelCallback({
        context: fakeContext({
          userId: "u",
          sessionId: "t",
          invocationId: "inv-loop",
          userContent,
        }) as unknown as Context,
        request: fakeLlmRequest() as unknown as LlmRequest,
      });
    }

    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
  });
});
