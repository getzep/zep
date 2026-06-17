import { describe, it, expect, vi } from "vitest";
import type { LanguageModelV3CallOptions, LanguageModelV3Prompt } from "@ai-sdk/provider";
import { createZepMiddleware } from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

const modelStub = {} as never;

/** Build a minimal V3 call-options object from an explicit prompt. */
function makeParamsFromPrompt(prompt: LanguageModelV3Prompt): LanguageModelV3CallOptions {
  return { prompt } as LanguageModelV3CallOptions;
}

/** Build a minimal V3 call-options object with a single user message. */
function makeParams(userText: string): LanguageModelV3CallOptions {
  return makeParamsFromPrompt([
    { role: "user", content: [{ type: "text", text: userText }] },
  ]);
}

describe("createZepMiddleware", () => {
  it("uses specificationVersion v3", () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    expect(mw.specificationVersion).toBe("v3");
  });

  it("does not expose a wrapGenerate hook (injection only)", () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    expect(mw.wrapGenerate).toBeUndefined();
  });

  it("transformParams injects the Context Block as a leading system message", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "Jane lives in Portland" });
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });

    const params = makeParams("Where do I live?");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });

    expect(out.prompt[0]!.role).toBe("system");
    expect((out.prompt[0] as { content: string }).content).toContain("Jane lives in Portland");
    // The original user message is preserved after the injected system message.
    expect(out.prompt[1]!.role).toBe("user");
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", {});
  });

  it("transformParams honors a custom formatContext", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "FACT" });
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      formatContext: (c) => `MEMORY>>>${c}<<<`,
    });
    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "stream", params, model: modelStub });
    expect((out.prompt[0] as { content: string }).content).toBe("MEMORY>>>FACT<<<");
  });

  it("transformParams injects nothing when the context is empty", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "" });
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });
    expect(out.prompt[0]!.role).toBe("user");
  });

  it("transformParams degrades gracefully when Zep fails (no system message)", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockRejectedValueOnce(new Error("down"));
    const warn = vi.fn();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", logger: { warn } });
    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });
    expect(out.prompt[0]!.role).toBe("user");
    // getZepContext logs the failure; transformParams returns the prompt as-is.
    expect(warn).toHaveBeenCalled();
  });

  it("injects on a genuine new user turn but NOT on tool-loop continuation steps", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValue({ context: "CONTEXT BLOCK" });
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });

    // Step 1: the triggering new user turn — last message role is 'user'.
    const step1 = makeParamsFromPrompt([
      { role: "user", content: [{ type: "text", text: "Look up my orders." }] },
    ]);
    const out1 = await mw.transformParams!({ type: "generate", params: step1, model: modelStub });
    expect(out1.prompt[0]!.role).toBe("system");
    expect((out1.prompt[0] as { content: string }).content).toContain("CONTEXT BLOCK");

    // Step 2: continuation step — model emitted a tool call, last message is 'tool'.
    const step2 = makeParamsFromPrompt([
      { role: "user", content: [{ type: "text", text: "Look up my orders." }] },
      {
        role: "assistant",
        content: [
          {
            type: "tool-call",
            toolCallId: "c1",
            toolName: "zepSearch",
            input: JSON.stringify({ query: "orders" }),
          },
        ],
      } as LanguageModelV3Prompt[number],
      {
        role: "tool",
        content: [
          {
            type: "tool-result",
            toolCallId: "c1",
            toolName: "zepSearch",
            output: { type: "json", value: { facts: [], found: false } },
          },
        ],
      } as LanguageModelV3Prompt[number],
    ]);
    const out2 = await mw.transformParams!({ type: "generate", params: step2, model: modelStub });
    // No injection on the continuation step: prompt is unchanged, no system head.
    expect(out2.prompt[0]!.role).toBe("user");
    expect(out2.prompt.some((m) => m.role === "system")).toBe(false);

    // Context fetched exactly once across the whole turn (step 1 only).
    expect(zep.thread.getUserContext).toHaveBeenCalledTimes(1);
  });

  it("does not inject when the last message is an assistant message", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValue({ context: "CONTEXT" });
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });

    const params = makeParamsFromPrompt([
      { role: "user", content: [{ type: "text", text: "hi" }] },
      { role: "assistant", content: [{ type: "text", text: "partial" }] },
    ]);
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });
    expect(out.prompt.some((m) => m.role === "system")).toBe(false);
    expect(zep.thread.getUserContext).not.toHaveBeenCalled();
  });
});
