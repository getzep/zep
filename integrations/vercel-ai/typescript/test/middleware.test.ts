import { describe, it, expect, vi } from "vitest";
import type { LanguageModelV3CallOptions, LanguageModelV3Prompt } from "@ai-sdk/provider";
import { createZepMiddleware, DEFAULT_CONTEXT_TEMPLATE } from "../src/index.js";
import type { ZepContextBuilderInput } from "../src/index.js";
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

  it("does not expose wrapGenerate/wrapStream hooks when persist is unset (injection only)", () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    expect(mw.wrapGenerate).toBeUndefined();
    expect(mw.wrapStream).toBeUndefined();
  });

  it("exposes wrapGenerate/wrapStream hooks when persist is set", () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });
    expect(mw.wrapGenerate).toBeDefined();
    expect(mw.wrapStream).toBeDefined();
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

describe("createZepMiddleware contextBuilder", () => {
  it("contextBuilder replaces default retrieval", async () => {
    const zep = makeFakeZep();
    const builder = vi.fn().mockResolvedValue("BUILT CONTEXT");
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      userId: "u1",
      contextBuilder: builder,
    });

    const params = makeParams("Where do I live?");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });

    expect(zep.thread.getUserContext).not.toHaveBeenCalled();
    expect(builder).toHaveBeenCalledOnce();
    const input = builder.mock.calls[0]![0] as ZepContextBuilderInput;
    expect(input.client).toBe(asZep(zep));
    expect(input.userId).toBe("u1");
    expect(input.threadId).toBe("t1");
    expect(input.userMessage).toBe("Where do I live?");
    expect(input.params).toBe(params);

    expect(out.prompt[0]!.role).toBe("system");
    expect((out.prompt[0] as { content: string }).content).toContain("BUILT CONTEXT");
  });

  it("contextBuilder returning undefined injects nothing", async () => {
    const zep = makeFakeZep();
    const builder = vi.fn().mockResolvedValue(undefined);
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", contextBuilder: builder });

    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });

    expect(builder).toHaveBeenCalledOnce();
    expect(out.prompt.some((m) => m.role === "system")).toBe(false);
  });

  it("contextBuilder throw degrades gracefully (warn, no system message)", async () => {
    const zep = makeFakeZep();
    const builder = vi.fn().mockRejectedValue(new Error("builder exploded"));
    const warn = vi.fn();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      contextBuilder: builder,
      logger: { warn },
    });

    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });

    expect(out.prompt.some((m) => m.role === "system")).toBe(false);
    expect(warn).toHaveBeenCalled();
  });
});

describe("createZepMiddleware DEFAULT_CONTEXT_TEMPLATE", () => {
  // Canonical across every Zep framework integration (Python, Go, TypeScript) —
  // byte-identical to e.g. zep_langgraph.context.DEFAULT_CONTEXT_TEMPLATE.
  const CANONICAL_TEMPLATE =
    "The following context is retrieved from Zep, the agent's long-term memory. " +
    "It contains relevant facts, entities, and prior knowledge about the user. " +
    "Use it to inform your responses.\n\n" +
    "<ZEP_CONTEXT>\n" +
    "{context}\n" +
    "</ZEP_CONTEXT>";

  it("matches the canonical template text byte-for-byte", () => {
    expect(DEFAULT_CONTEXT_TEMPLATE).toBe(CANONICAL_TEMPLATE);
  });

  it("default formatContext uses the canonical template", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "FACT" });
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });
    const expected = DEFAULT_CONTEXT_TEMPLATE.split("{context}").join("FACT");
    expect((out.prompt[0] as { content: string }).content).toBe(expected);
  });

  it("custom formatContext still wins", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "FACT" });
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      formatContext: (c) => `CUSTOM[${c}]`,
    });
    const params = makeParams("hi");
    const out = await mw.transformParams!({ type: "generate", params, model: modelStub });
    expect((out.prompt[0] as { content: string }).content).toBe("CUSTOM[FACT]");
  });
});
