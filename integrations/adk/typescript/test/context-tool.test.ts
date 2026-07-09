import { describe, expect, it, vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context, LlmRequest } from "@google/adk";
import { ZepContextTool } from "../src/context-tool.js";
import {
  fakeContext,
  fakeLlmRequest,
  mockZepClient,
  silentLogger,
} from "./helpers.js";

describe("ZepContextTool", () => {
  it("is not model-callable (_getDeclaration returns undefined)", () => {
    const { client } = mockZepClient();
    const tool = new ZepContextTool({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    expect(tool._getDeclaration()).toBeUndefined();
    expect(tool.name).toBe("zep_context");
  });

  it("runAsync is a no-op", async () => {
    const { client } = mockZepClient();
    const tool = new ZepContextTool({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    await expect(tool.runAsync()).resolves.toBeUndefined();
  });

  it("persists and injects via processLlmRequest", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx-block" });
    const tool = new ZepContextTool({
      zep: client as unknown as ZepClient,
      userId: "u-1",
      threadId: "t-1",
      logger: silentLogger,
    });
    const req = fakeLlmRequest();

    await tool.processLlmRequest({
      toolContext: fakeContext({ userText: "remember this" }) as unknown as Context,
      llmRequest: req as unknown as LlmRequest,
    });

    expect(mocks.addMessages).toHaveBeenCalledWith(
      "t-1",
      expect.objectContaining({ returnContext: true }),
    );
    expect((req.config?.systemInstruction as string)).toContain("ctx-block");
  });

  it("never throws when Zep fails inside processLlmRequest", async () => {
    const { client, mocks } = mockZepClient();
    mocks.addMessages.mockRejectedValueOnce(new Error("boom"));
    const tool = new ZepContextTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      threadId: "t",
      logger: silentLogger,
    });
    const req = fakeLlmRequest();

    await expect(
      tool.processLlmRequest({
        toolContext: fakeContext({ userText: "hi" }) as unknown as Context,
        llmRequest: req as unknown as LlmRequest,
      }),
    ).resolves.toBeUndefined();
    expect(req.config?.systemInstruction).toBeUndefined();
  });

  it("threads contextBuilder and contextTemplate through to persistAndInject", async () => {
    const { client, mocks } = mockZepClient();
    const contextBuilder = vi.fn(async () => "built via tool");
    const tool = new ZepContextTool({
      zep: client as unknown as ZepClient,
      userId: "u-1",
      threadId: "t-1",
      logger: silentLogger,
      contextBuilder,
      contextTemplate: "TOOL[{context}]",
    });
    const req = fakeLlmRequest();

    await tool.processLlmRequest({
      toolContext: fakeContext({ userText: "remember this" }) as unknown as Context,
      llmRequest: req as unknown as LlmRequest,
    });

    expect(contextBuilder).toHaveBeenCalledTimes(1);
    const [, payload] = mocks.addMessages.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.returnContext).toBeUndefined();
    expect(req.config?.systemInstruction).toBe("TOOL[built via tool]");
  });

  it("never calls user.add or thread.create via processLlmRequest", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const tool = new ZepContextTool({
      zep: client as unknown as ZepClient,
      userId: "u-1",
      threadId: "t-1",
      logger: silentLogger,
    });

    await tool.processLlmRequest({
      toolContext: fakeContext({ userText: "remember this" }) as unknown as Context,
      llmRequest: fakeLlmRequest() as unknown as LlmRequest,
    });

    expect(mocks.userAdd).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
  });
});
