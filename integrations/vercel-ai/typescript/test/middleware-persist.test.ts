import { describe, it, expect, vi } from "vitest";
import type {
  LanguageModelV3CallOptions,
  LanguageModelV3GenerateResult,
  LanguageModelV3StreamPart,
  LanguageModelV3StreamResult,
} from "@ai-sdk/provider";
import { createZepMiddleware } from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

const modelStub = {} as never;

/** Build a minimal V3 call-options object with a single user message. */
function makeParams(userText: string): LanguageModelV3CallOptions {
  return {
    prompt: [{ role: "user", content: [{ type: "text", text: userText }] }],
  } as LanguageModelV3CallOptions;
}

/** Build V3 call options from an arbitrary prompt message list. */
function makeParamsFromPrompt(prompt: unknown[]): LanguageModelV3CallOptions {
  return { prompt } as LanguageModelV3CallOptions;
}

/** Build a minimal successful (non-tool-call) generate result. */
function makeGenerateResult(
  assistantText: string,
  finishReasonUnified: string = "stop",
): LanguageModelV3GenerateResult {
  return {
    content: [{ type: "text", text: assistantText }],
    finishReason: { unified: finishReasonUnified, raw: finishReasonUnified } as never,
    usage: {
      inputTokens: { total: 1, noCache: 1, cacheRead: undefined, cacheWrite: undefined },
      outputTokens: { total: 1, text: 1, reasoning: undefined },
    },
    warnings: [],
  } as LanguageModelV3GenerateResult;
}

/** Wait for pending microtasks (fire-and-forget persistence) to settle. */
async function flushMicrotasks(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** Collect every part emitted by a V3 stream result. */
async function collectParts(
  result: LanguageModelV3StreamResult,
): Promise<LanguageModelV3StreamPart[]> {
  const parts: LanguageModelV3StreamPart[] = [];
  const reader = result.stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
  }
  return parts;
}

function makeStreamResult(parts: LanguageModelV3StreamPart[]): LanguageModelV3StreamResult {
  return {
    stream: new ReadableStream<LanguageModelV3StreamPart>({
      start(controller) {
        for (const part of parts) controller.enqueue(part);
        controller.close();
      },
    }),
  } as LanguageModelV3StreamResult;
}

describe("createZepMiddleware persist (wrapGenerate/wrapStream)", () => {
  it("wrapGenerate/wrapStream are undefined when persist is unset", () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    expect(mw.wrapGenerate).toBeUndefined();
    expect(mw.wrapStream).toBeUndefined();
  });

  it("wrapGenerate persists user and assistant once on the final step", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });
    expect(mw.wrapGenerate).toBeDefined();

    const params = makeParams("Where do I live?");
    const generateResult = makeGenerateResult("You live in Portland.");
    const doGenerate = vi.fn().mockResolvedValue(generateResult);
    const doStream = vi.fn();

    const result = await mw.wrapGenerate!({ doGenerate, doStream, params, model: modelStub });
    expect(result).toBe(generateResult);

    await flushMicrotasks();

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const [threadId, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(threadId).toBe("t1");
    expect(req.messages).toEqual([
      { role: "user", content: "Where do I live?" },
      { role: "assistant", content: "You live in Portland." },
    ]);
  });

  it("wrapGenerate does not persist on tool-call continuation steps", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });

    const params = makeParams("Look up my orders.");
    const generateResult = makeGenerateResult("", "tool-calls");
    const doGenerate = vi.fn().mockResolvedValue(generateResult);
    const doStream = vi.fn();

    await mw.wrapGenerate!({ doGenerate, doStream, params, model: modelStub });
    await flushMicrotasks();

    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("wrapGenerate does not re-persist a user message already answered by assistant text", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });

    // Continuation call: the prompt ends with an assistant TEXT message, so the
    // user message belongs to an earlier, already-persisted turn.
    const params = makeParamsFromPrompt([
      { role: "user", content: [{ type: "text", text: "Tell me a story." }] },
      { role: "assistant", content: [{ type: "text", text: "Once upon a time" }] },
    ]);
    const doGenerate = vi.fn().mockResolvedValue(makeGenerateResult(", the end."));
    const doStream = vi.fn();

    await mw.wrapGenerate!({ doGenerate, doStream, params, model: modelStub });
    await flushMicrotasks();

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([{ role: "assistant", content: ", the end." }]);
  });

  it("wrapGenerate persists the user message exactly once on the final tool-loop step", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });

    // Final step of a tool loop: the prompt ends with a tool result, and the
    // intermediate assistant message carries only tool calls (no text) — the
    // user message has not been persisted yet and still must be.
    const params = makeParamsFromPrompt([
      { role: "user", content: [{ type: "text", text: "Look up my orders." }] },
      {
        role: "assistant",
        content: [{ type: "tool-call", toolCallId: "c1", toolName: "search", input: "{}" }],
      },
      {
        role: "tool",
        content: [
          {
            type: "tool-result",
            toolCallId: "c1",
            toolName: "search",
            output: { type: "text", value: "3 orders" },
          },
        ],
      },
    ]);
    const doGenerate = vi.fn().mockResolvedValue(makeGenerateResult("You have 3 orders."));
    const doStream = vi.fn();

    await mw.wrapGenerate!({ doGenerate, doStream, params, model: modelStub });
    await flushMicrotasks();

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([
      { role: "user", content: "Look up my orders." },
      { role: "assistant", content: "You have 3 orders." },
    ]);
  });

  it("wrapGenerate persists custom userName/assistantName when persist carries them", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      persist: { userName: "Jane", assistantName: "Assistant" },
    });

    const params = makeParams("hi");
    const generateResult = makeGenerateResult("hello!");
    const doGenerate = vi.fn().mockResolvedValue(generateResult);
    const doStream = vi.fn();

    await mw.wrapGenerate!({ doGenerate, doStream, params, model: modelStub });
    await flushMicrotasks();

    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([
      { role: "user", content: "hi", name: "Jane" },
      { role: "assistant", content: "hello!", name: "Assistant" },
    ]);
  });

  it("wrapStream accumulates text-delta parts and persists on finish", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });
    expect(mw.wrapStream).toBeDefined();

    const params = makeParams("Tell me a joke.");
    const streamParts: LanguageModelV3StreamPart[] = [
      { type: "text-start", id: "1" },
      { type: "text-delta", id: "1", delta: "Why did " },
      { type: "text-delta", id: "1", delta: "the chicken cross the road?" },
      { type: "text-end", id: "1" },
      {
        type: "finish",
        usage: {
          inputTokens: { total: 1, noCache: 1, cacheRead: undefined, cacheWrite: undefined },
          outputTokens: { total: 1, text: 1, reasoning: undefined },
        },
        finishReason: { unified: "stop", raw: "stop" } as never,
      },
    ];
    const doGenerate = vi.fn();
    const doStream = vi.fn().mockResolvedValue(makeStreamResult(streamParts));

    const wrapped = await mw.wrapStream!({ doGenerate, doStream, params, model: modelStub });
    const collected = await collectParts(wrapped);

    await flushMicrotasks();

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([
      { role: "user", content: "Tell me a joke." },
      { role: "assistant", content: "Why did the chicken cross the road?" },
    ]);

    // All parts pass through unmodified.
    expect(collected).toEqual(streamParts);
  });

  it("wrapStream does not re-persist a user message already answered by assistant text", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });

    const params = makeParamsFromPrompt([
      { role: "user", content: [{ type: "text", text: "Tell me a story." }] },
      { role: "assistant", content: [{ type: "text", text: "Once upon a time" }] },
    ]);
    const streamParts: LanguageModelV3StreamPart[] = [
      { type: "text-delta", id: "1", delta: ", the end." },
      {
        type: "finish",
        usage: {
          inputTokens: { total: 1, noCache: 1, cacheRead: undefined, cacheWrite: undefined },
          outputTokens: { total: 1, text: 1, reasoning: undefined },
        },
        finishReason: { unified: "stop", raw: "stop" } as never,
      },
    ];
    const doGenerate = vi.fn();
    const doStream = vi.fn().mockResolvedValue(makeStreamResult(streamParts));

    const wrapped = await mw.wrapStream!({ doGenerate, doStream, params, model: modelStub });
    await collectParts(wrapped);
    await flushMicrotasks();

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([{ role: "assistant", content: ", the end." }]);
  });

  it("wrapStream does not persist when finishReason is tool-calls", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1", persist: true });

    const params = makeParams("Search my orders.");
    const streamParts: LanguageModelV3StreamPart[] = [
      { type: "text-delta", id: "1", delta: "Let me check." },
      {
        type: "finish",
        usage: {
          inputTokens: { total: 1, noCache: 1, cacheRead: undefined, cacheWrite: undefined },
          outputTokens: { total: 1, text: 1, reasoning: undefined },
        },
        finishReason: { unified: "tool-calls", raw: "tool_calls" } as never,
      },
    ];
    const doGenerate = vi.fn();
    const doStream = vi.fn().mockResolvedValue(makeStreamResult(streamParts));

    const wrapped = await mw.wrapStream!({ doGenerate, doStream, params, model: modelStub });
    await collectParts(wrapped);
    await flushMicrotasks();

    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("all stream parts pass through unmodified even when persist is unset", async () => {
    const zep = makeFakeZep();
    const mw = createZepMiddleware({ client: asZep(zep), threadId: "t1" });
    // wrapStream is undefined when persist is unset — nothing to test here for
    // pass-through beyond confirming the hook itself is absent.
    expect(mw.wrapStream).toBeUndefined();
  });

  it("persistence failure is logged and never thrown (wrapGenerate)", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("503"));
    const warn = vi.fn();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      persist: true,
      logger: { warn },
    });

    const params = makeParams("hi");
    const generateResult = makeGenerateResult("hello!");
    const doGenerate = vi.fn().mockResolvedValue(generateResult);
    const doStream = vi.fn();

    const result = await mw.wrapGenerate!({ doGenerate, doStream, params, model: modelStub });
    expect(result).toBe(generateResult);

    await flushMicrotasks();
    expect(warn).toHaveBeenCalled();
  });

  it("persistence failure is logged and never thrown (wrapStream)", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("503"));
    const warn = vi.fn();
    const mw = createZepMiddleware({
      client: asZep(zep),
      threadId: "t1",
      persist: true,
      logger: { warn },
    });

    const params = makeParams("hi");
    const streamParts: LanguageModelV3StreamPart[] = [
      { type: "text-delta", id: "1", delta: "hello!" },
      {
        type: "finish",
        usage: {
          inputTokens: { total: 1, noCache: 1, cacheRead: undefined, cacheWrite: undefined },
          outputTokens: { total: 1, text: 1, reasoning: undefined },
        },
        finishReason: { unified: "stop", raw: "stop" } as never,
      },
    ];
    const doGenerate = vi.fn();
    const doStream = vi.fn().mockResolvedValue(makeStreamResult(streamParts));

    const wrapped = await mw.wrapStream!({ doGenerate, doStream, params, model: modelStub });
    await collectParts(wrapped);
    await flushMicrotasks();

    expect(warn).toHaveBeenCalled();
  });
});
