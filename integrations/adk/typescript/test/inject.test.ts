import { describe, expect, it, vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { LlmRequest } from "@google/adk";
import {
  DEFAULT_CONTEXT_TEMPLATE,
  formatContextInstruction,
  persistAndInject,
  type ContextBuilderInput,
} from "../src/inject.js";
import { TurnDedup } from "../src/resources.js";
import {
  capturingLogger,
  fakeContext,
  fakeLlmRequest,
  mockZepClient,
  silentLogger,
} from "./helpers.js";

function runInject(
  zep: ReturnType<typeof mockZepClient>["client"],
  overrides: Partial<Parameters<typeof persistAndInject>[0]> = {},
) {
  return persistAndInject({
    zep: zep as unknown as ZepClient,
    dedup: overrides.dedup ?? new TurnDedup(),
    logger: overrides.logger ?? silentLogger,
    context: overrides.context ?? fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
    llmRequest: overrides.llmRequest ?? (fakeLlmRequest() as unknown as LlmRequest),
    options: overrides.options ?? {},
  });
}

describe("formatContextInstruction", () => {
  it("uses DEFAULT_CONTEXT_TEMPLATE by default and contains <ZEP_CONTEXT>", () => {
    const instruction = formatContextInstruction("some facts");
    expect(DEFAULT_CONTEXT_TEMPLATE).toContain("<ZEP_CONTEXT>");
    expect(instruction).toContain("<ZEP_CONTEXT>");
    expect(instruction).toContain("some facts");
    expect(instruction).toContain("</ZEP_CONTEXT>");
  });

  it("respects a custom template override", () => {
    const instruction = formatContextInstruction("facts", "CUSTOM: {context}");
    expect(instruction).toBe("CUSTOM: facts");
  });

  it("replaces ALL occurrences of {context} in a custom template (Python parity)", () => {
    const instruction = formatContextInstruction(
      "facts",
      "first={context} second={context}",
    );
    expect(instruction).toBe("first=facts second=facts");
  });

  it("safely injects context text containing %, {}, and literal {context}", () => {
    const trickyContext = "100% done {} and literal {context} here";
    const instruction = formatContextInstruction(trickyContext, "T: {context}");
    expect(instruction).toBe(`T: ${trickyContext}`);
  });
});

describe("persistAndInject — context-builder seam", () => {
  it("builder receives a fully-populated ContextBuilderInput", async () => {
    const { client } = mockZepClient();
    let captured: ContextBuilderInput | undefined;
    const contextBuilder = vi.fn(async (input: ContextBuilderInput) => {
      captured = input;
      return "built context";
    });

    const ctx = fakeContext({
      userId: "user-1",
      sessionId: "thread-1",
      userText: "hello there",
    });
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, {
      context: ctx,
      llmRequest: req,
      options: { contextBuilder },
    });

    expect(contextBuilder).toHaveBeenCalledTimes(1);
    expect(captured).toBeDefined();
    expect(captured!.zep).toBe(client);
    expect(captured!.userId).toBe("user-1");
    expect(captured!.threadId).toBe("thread-1");
    expect(captured!.userMessage).toBe("hello there");
    expect(captured!.context).toBe(ctx);
    expect(captured!.llmRequest).toBe(req);
  });

  it("builder set: addMessages called WITHOUT returnContext, builder invoked, output injected via template", async () => {
    const { client, mocks } = mockZepClient();
    const contextBuilder = vi.fn(async () => "custom built context");
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, {
      llmRequest: req,
      options: { contextBuilder },
    });

    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
    const [, payload] = mocks.addMessages.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.returnContext).toBeUndefined();
    expect(contextBuilder).toHaveBeenCalledTimes(1);

    const sys = (req as unknown as ReturnType<typeof fakeLlmRequest>).config
      ?.systemInstruction as string;
    expect(sys).toContain("custom built context");
    expect(sys).toContain("<ZEP_CONTEXT>");
  });

  it("builder returns undefined: no injection, persist still happens", async () => {
    const { client, mocks } = mockZepClient();
    const contextBuilder = vi.fn(async () => undefined);
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, { llmRequest: req, options: { contextBuilder } });

    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
    expect((req as unknown as ReturnType<typeof fakeLlmRequest>).config?.systemInstruction).toBeUndefined();
  });

  it("builder returns empty string: no injection, persist still happens", async () => {
    const { client, mocks } = mockZepClient();
    const contextBuilder = vi.fn(async () => "");
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, { llmRequest: req, options: { contextBuilder } });

    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
    expect((req as unknown as ReturnType<typeof fakeLlmRequest>).config?.systemInstruction).toBeUndefined();
  });

  it("builder rejects: warns, skips injection, persist completes, dedup IS marked", async () => {
    const { client, mocks } = mockZepClient();
    const contextBuilder = vi.fn(async () => {
      throw new Error("builder boom");
    });
    const logger = capturingLogger();
    const dedup = new TurnDedup();
    const ctx = fakeContext({ userId: "u", sessionId: "t", invocationId: "inv-1", userText: "hi" });
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, {
      context: ctx,
      llmRequest: req,
      dedup,
      logger,
      options: { contextBuilder },
    });

    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
    expect((req as unknown as ReturnType<typeof fakeLlmRequest>).config?.systemInstruction).toBeUndefined();
    expect(logger.warns.some((w) => w.toLowerCase().includes("builder"))).toBe(true);
    expect(dedup.alreadyPersisted("t", "inv-1")).toBe(true);
  });

  it("persist rejects + builder resolves: warns, dedup NOT marked, builder result still injected", async () => {
    const { client, mocks } = mockZepClient();
    mocks.addMessages.mockRejectedValueOnce(new Error("persist boom"));
    const contextBuilder = vi.fn(async () => "still injected");
    const logger = capturingLogger();
    const dedup = new TurnDedup();
    const ctx = fakeContext({ userId: "u", sessionId: "t", invocationId: "inv-1", userText: "hi" });
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, {
      context: ctx,
      llmRequest: req,
      dedup,
      logger,
      options: { contextBuilder },
    });

    expect(logger.warns.length).toBeGreaterThan(0);
    expect(dedup.alreadyPersisted("t", "inv-1")).toBe(false);
    const sys = (req as unknown as ReturnType<typeof fakeLlmRequest>).config
      ?.systemInstruction as string;
    expect(sys).toContain("still injected");
  });

  it("template override is respected when a builder is used", async () => {
    const { client } = mockZepClient();
    const contextBuilder = vi.fn(async () => "builder facts");
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, {
      llmRequest: req,
      options: { contextBuilder, contextTemplate: "TPL[{context}]" },
    });

    const sys = (req as unknown as ReturnType<typeof fakeLlmRequest>).config
      ?.systemInstruction as string;
    expect(sys).toBe("TPL[builder facts]");
  });

  it("template override is respected on the default (no-builder) path", async () => {
    const { client } = mockZepClient({ addMessagesContext: "default ctx" });
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, {
      llmRequest: req,
      options: { contextTemplate: "TPL[{context}]" },
    });

    const sys = (req as unknown as ReturnType<typeof fakeLlmRequest>).config
      ?.systemInstruction as string;
    expect(sys).toBe("TPL[default ctx]");
  });

  it("no builder: unchanged single round-trip behavior (returnContext: true)", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx-block" });
    const req = fakeLlmRequest() as unknown as LlmRequest;

    await runInject(client, { llmRequest: req, options: {} });

    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
    const [, payload] = mocks.addMessages.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.returnContext).toBe(true);
  });
});
