import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context, LlmResponse } from "@google/adk";
import { createZepAfterModelCallback } from "../src/after-model-callback.js";
import {
  capturingLogger,
  fakeContext,
  fakeLlmResponse,
  mockZepClient,
  persistedContent,
  silentLogger,
} from "./helpers.js";
import { MESSAGE_CONTENT_MAX, MESSAGE_CONTENT_TRUNCATE_TO } from "../src/limits.js";

function run(
  client: ReturnType<typeof mockZepClient>["client"],
  response: ReturnType<typeof fakeLlmResponse>,
  options: Record<string, unknown> = {},
) {
  const cb = createZepAfterModelCallback(client as unknown as ZepClient, {
    logger: silentLogger,
    userId: "u",
    threadId: "t",
    ...options,
  });
  return cb({
    context: fakeContext({ userText: "prior" }) as unknown as Context,
    response: response as unknown as LlmResponse,
  });
}

describe("createZepAfterModelCallback", () => {
  it("persists assistant text with the default assistant name", async () => {
    const { client, mocks } = mockZepClient();
    await run(client, fakeLlmResponse({ text: "Hello there." }));

    expect(mocks.addMessages).toHaveBeenCalledWith("t", {
      messages: [
        { role: "assistant", content: "Hello there.", name: "Assistant" },
      ],
      ignoreRoles: undefined,
    });
  });

  it("uses a custom assistant name", async () => {
    const { client, mocks } = mockZepClient();
    await run(client, fakeLlmResponse({ text: "Hi" }), { assistantName: "Aria" });

    expect(mocks.addMessages).toHaveBeenCalledWith(
      "t",
      expect.objectContaining({
        messages: [{ role: "assistant", content: "Hi", name: "Aria" }],
      }),
    );
  });

  it("skips intermediate responses that contain a function call", async () => {
    const { client, mocks } = mockZepClient();
    await run(client, fakeLlmResponse({ text: "thinking...", functionCall: true }));
    expect(mocks.addMessages).not.toHaveBeenCalled();
  });

  it("skips partial streaming chunks", async () => {
    const { client, mocks } = mockZepClient();
    await run(client, fakeLlmResponse({ text: "partial", partial: true }));
    expect(mocks.addMessages).not.toHaveBeenCalled();
  });

  it("skips responses with no text", async () => {
    const { client, mocks } = mockZepClient();
    await run(client, fakeLlmResponse({}));
    expect(mocks.addMessages).not.toHaveBeenCalled();
  });

  it("never throws when persistence fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.addMessages.mockRejectedValueOnce(new Error("nope"));
    await expect(
      run(client, fakeLlmResponse({ text: "x" })),
    ).resolves.toBeUndefined();
  });

  it("truncates an over-long assistant message and warns with lengths only", async () => {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const longText = "z".repeat(MESSAGE_CONTENT_MAX + 1000);

    await run(client, fakeLlmResponse({ text: longText }), { logger });

    const persisted = persistedContent(mocks.addMessages);
    expect(persisted.length).toBe(MESSAGE_CONTENT_TRUNCATE_TO);

    const warning = logger.warns.find((w) => w.includes("Truncated"));
    expect(warning).toBeDefined();
    expect(warning).toContain(String(MESSAGE_CONTENT_MAX + 1000));
    expect(warning).not.toContain("zzzzz");
  });
});
