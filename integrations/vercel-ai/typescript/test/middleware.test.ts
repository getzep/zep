import { describe, it, expect, vi } from "vitest";
import type {
  LanguageModelV3CallOptions,
  LanguageModelV3GenerateResult,
  LanguageModelV3StreamResult,
} from "@ai-sdk/provider";
import { createZepMiddleware } from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

/** Build a minimal V3 call-options object with a single user message. */
function makeParams(userText: string): LanguageModelV3CallOptions {
  return {
    prompt: [{ role: "user", content: [{ type: "text", text: userText }] }],
  } as LanguageModelV3CallOptions;
}

/** A `doGenerate` stub that resolves to a result whose text content is `text`. */
function makeDoGenerate(text: string): () => Promise<LanguageModelV3GenerateResult> {
  return async () =>
    ({
      content: [{ type: "text", text }],
      finishReason: { unified: "stop", raw: "stop" },
      usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
      warnings: [],
    }) as unknown as LanguageModelV3GenerateResult;
}

const doStreamStub = (async () => ({}) as unknown as LanguageModelV3StreamResult) as () => Promise<LanguageModelV3StreamResult>;
const modelStub = {} as never;

describe("createZepMiddleware", () => {
  it("uses specificationVersion v3", () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    expect(mw.specificationVersion).toBe("v3");
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
    expect(warn).toHaveBeenCalledOnce();
  });

  it("wrapGenerate persists the user+assistant turn when persist is enabled", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      persist: true,
      userName: "Jane",
    });

    const params = makeParams("I live in Portland");
    const result = await mw.wrapGenerate!({
      doGenerate: makeDoGenerate("Noted!"),
      doStream: doStreamStub,
      params,
      model: modelStub,
    });

    expect((result.content[0] as { text: string }).text).toBe("Noted!");
    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([
      { role: "user", content: "I live in Portland", name: "Jane" },
      { role: "assistant", content: "Noted!" },
    ]);
  });

  it("wrapGenerate truncates an over-long user message and warns (lengths only)", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      persist: true,
      logger: { warn },
    });

    const params = makeParams("u".repeat(5000));
    await mw.wrapGenerate!({
      doGenerate: makeDoGenerate("ok"),
      doStream: doStreamStub,
      params,
      model: modelStub,
    });

    const sent = zep.thread.addMessages.mock.calls[0]![1].messages[0].content as string;
    expect(sent.length).toBe(4000);
    expect(warn).toHaveBeenCalledOnce();
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("5000");
    expect(warnArg).not.toContain("uuuu");
  });

  it("wrapGenerate does not persist when persist is disabled (default)", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    await mw.wrapGenerate!({
      doGenerate: makeDoGenerate("hi"),
      doStream: doStreamStub,
      params: makeParams("hello"),
      model: modelStub,
    });
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("wrapGenerate never throws when Zep persistence fails", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("503"));
    const warn = vi.fn();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      persist: true,
      logger: { warn },
    });
    const result = await mw.wrapGenerate!({
      doGenerate: makeDoGenerate("done"),
      doStream: doStreamStub,
      params: makeParams("q"),
      model: modelStub,
    });
    expect((result.content[0] as { text: string }).text).toBe("done");
    expect(warn).toHaveBeenCalledOnce();
  });
});
