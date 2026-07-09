import { describe, it, expect, vi } from "vitest";
import {
  ZepInputProcessor,
  ZepOutputProcessor,
  createZepProcessors,
  DEFAULT_CONTEXT_TEMPLATE,
} from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

/** Build a minimal fake MastraDBMessage carrying a single text part. */
function userMessage(text: string, id = "m1") {
  return {
    id,
    role: "user" as const,
    createdAt: new Date(),
    content: { format: 2 as const, parts: [{ type: "text", text }] },
  };
}

/** No-op abort — assertions check it was never called. */
function neverAbort(): never {
  throw new Error("abort() should never be called");
}

describe("ZepInputProcessor", () => {
  it("prepends a system message with the Context Block", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "Jane lives in Portland" });
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
    });

    const messages = [userMessage("where do I live?")];
    const result = await processor.processInput({
      messages,
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    expect(result.messages).toBe(messages);
    expect(result.systemMessages).toHaveLength(1);
    const sysMessage = result.systemMessages[0] as { role: string; content: string };
    expect(sysMessage.role).toBe("system");
    expect(sysMessage.content).toContain("Jane lives in Portland");
    expect(sysMessage.content).toContain("<ZEP_CONTEXT>");
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", {});
  });

  it("uses contextBuilder instead of getUserContext", async () => {
    const zep = makeFakeZep();
    const contextBuilder = vi.fn().mockResolvedValue("custom built context");
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      contextBuilder,
    });

    const result = await processor.processInput({
      messages: [userMessage("hello")],
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    expect(zep.thread.getUserContext).not.toHaveBeenCalled();
    expect(contextBuilder).toHaveBeenCalledOnce();
    const call = contextBuilder.mock.calls[0]![0] as {
      client: unknown;
      userId?: string;
      threadId: string;
      userMessage: string;
    };
    expect(call.userId).toBe("u1");
    expect(call.threadId).toBe("t1");
    expect(call.userMessage).toBe("hello");
    const sysMessage = result.systemMessages[0] as { content: string };
    expect(sysMessage.content).toContain("custom built context");
  });

  it("resolves per-call identity via resolveIdentity, overriding constructor binding", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "override context" });
    const resolveIdentity = vi.fn().mockReturnValue({ userId: "u2", threadId: "t2" });
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      resolveIdentity,
    });

    await processor.processInput({
      messages: [userMessage("hi")],
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
      requestContext: { foo: "bar" } as never,
    } as never);

    expect(resolveIdentity).toHaveBeenCalledWith({ foo: "bar" });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t2", {});
  });

  it("awaits an async resolveIdentity and uses the resolved identity", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "async override context" });
    const resolveIdentity = vi.fn().mockResolvedValue({ userId: "u2", threadId: "t2" });
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      resolveIdentity,
    });

    await processor.processInput({
      messages: [userMessage("hi")],
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
      requestContext: { tenant: "acme" } as never,
    } as never);

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t2", {});
  });

  it("passes through unchanged when threadId is missing", async () => {
    const zep = makeFakeZep();
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      // no threadId, and resolveIdentity also omits it
      resolveIdentity: () => ({ userId: "u1" }),
    });

    const messages = [userMessage("hi")];
    const systemMessages = [{ role: "system" as const, content: "existing" }];
    const result = await processor.processInput({
      messages,
      messageList: {} as never,
      systemMessages,
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    expect(result.messages).toBe(messages);
    expect(result.systemMessages).toBe(systemMessages);
    expect(zep.thread.getUserContext).not.toHaveBeenCalled();
  });

  it("degrades gracefully on a Zep failure: messages unchanged, warn called, abort never invoked", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockRejectedValueOnce(new Error("503 upstream"));
    const warn = vi.fn();
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn },
    });

    const messages = [userMessage("hi")];
    const systemMessages: unknown[] = [];
    const result = await processor.processInput({
      messages,
      messageList: {} as never,
      systemMessages,
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    expect(result.messages).toBe(messages);
    expect(result.systemMessages).toBe(systemMessages);
    expect(warn).toHaveBeenCalledOnce();
  });

  it("has the fixed name 'zep-context'", () => {
    const zep = makeFakeZep();
    const processor = new ZepInputProcessor({ client: asZep(zep), userId: "u1", threadId: "t1" });
    expect(processor.name).toBe("zep-context");
  });
});

describe("ZepOutputProcessor", () => {
  it("persists the turn once via thread.addMessages", async () => {
    const zep = makeFakeZep();
    const processor = new ZepOutputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
    });

    await processor.processOutputResult({
      messages: [userMessage("what's the weather?")],
      messageList: {} as never,
      state: {},
      abort: neverAbort,
      retryCount: 0,
      result: {
        text: "It's sunny.",
        usage: {} as never,
        finishReason: "stop",
        steps: [],
      },
    } as never);

    // fire-and-forget: allow the microtask queue to flush
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const [threadId, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(threadId).toBe("t1");
    expect(req.messages).toHaveLength(2);
    expect(req.messages[0]).toMatchObject({ role: "user", content: "what's the weather?" });
    expect(req.messages[1]).toMatchObject({ role: "assistant", content: "It's sunny." });
  });

  it("still persists the user message when the generation ends with finishReason 'tool-calls'", async () => {
    // processOutputResult runs exactly once per generation, so a turn that
    // exhausts its step budget mid-tool-loop must not drop the user message.
    const zep = makeFakeZep();
    const processor = new ZepOutputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
    });

    await processor.processOutputResult({
      messages: [userMessage("do a thing")],
      messageList: {} as never,
      state: {},
      abort: neverAbort,
      retryCount: 0,
      result: {
        text: "",
        usage: {} as never,
        finishReason: "tool-calls",
        steps: [],
      },
    } as never);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const [threadId, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(threadId).toBe("t1");
    expect(req.messages).toEqual([{ role: "user", content: "do a thing" }]);
  });

  it("persists only the final step's text, not the accumulated multi-step result.text", async () => {
    // result.text joins every step's text with no separator, so tool-call
    // preamble would concatenate with the final answer.
    const zep = makeFakeZep();
    const processor = new ZepOutputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
    });

    await processor.processOutputResult({
      messages: [userMessage("what's the weather?")],
      messageList: {} as never,
      state: {},
      abort: neverAbort,
      retryCount: 0,
      result: {
        text: "Let me check the weather.It's sunny in Portland.",
        usage: {} as never,
        finishReason: "stop",
        steps: [
          { text: "Let me check the weather." },
          { text: "It's sunny in Portland." },
        ],
      },
    } as never);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const [, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(req.messages).toEqual([
      { role: "user", content: "what's the weather?" },
      { role: "assistant", content: "It's sunny in Portland." },
    ]);
  });

  it("skips persistence entirely when there is no user text and no assistant text", async () => {
    const zep = makeFakeZep();
    const processor = new ZepOutputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
    });

    await processor.processOutputResult({
      messages: [],
      messageList: {} as never,
      state: {},
      abort: neverAbort,
      retryCount: 0,
      result: {
        text: "",
        usage: {} as never,
        finishReason: "tool-calls",
        steps: [],
      },
    } as never);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("awaits an async resolveIdentity and uses the resolved identity", async () => {
    const zep = makeFakeZep();
    const resolveIdentity = vi.fn().mockResolvedValue({ userId: "u2", threadId: "t2" });
    const processor = new ZepOutputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      resolveIdentity,
    });

    await processor.processOutputResult({
      messages: [userMessage("hi")],
      messageList: {} as never,
      state: {},
      abort: neverAbort,
      retryCount: 0,
      requestContext: { tenant: "acme" } as never,
      result: {
        text: "hello!",
        usage: {} as never,
        finishReason: "stop",
        steps: [],
      },
    } as never);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(zep.thread.addMessages).toHaveBeenCalledWith("t2", expect.anything());
  });

  it("never aborts or throws on a persist failure", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("boom"));
    const warn = vi.fn();
    const processor = new ZepOutputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn },
    });

    const outputMessages = [userMessage("hi")];
    await expect(
      processor.processOutputResult({
        messages: outputMessages,
        messageList: {} as never,
        state: {},
        abort: neverAbort,
        retryCount: 0,
        result: {
          text: "hello!",
          usage: {} as never,
          finishReason: "stop",
          steps: [],
        },
      } as never),
    ).resolves.toBe(outputMessages);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(warn).toHaveBeenCalledOnce();
  });

  it("has the fixed name 'zep-persist'", () => {
    const zep = makeFakeZep();
    const processor = new ZepOutputProcessor({ client: asZep(zep), userId: "u1", threadId: "t1" });
    expect(processor.name).toBe("zep-persist");
  });
});

describe("createZepProcessors", () => {
  it("builds a bound input/output processor pair", () => {
    const zep = makeFakeZep();
    const { inputProcessor, outputProcessor } = createZepProcessors({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
    });
    expect(inputProcessor).toBeInstanceOf(ZepInputProcessor);
    expect(outputProcessor).toBeInstanceOf(ZepOutputProcessor);
  });
});

describe("DEFAULT_CONTEXT_TEMPLATE", () => {
  it("contains the {context} placeholder and ZEP_CONTEXT wrapper", () => {
    expect(DEFAULT_CONTEXT_TEMPLATE).toContain("{context}");
    expect(DEFAULT_CONTEXT_TEMPLATE).toContain("<ZEP_CONTEXT>");
    expect(DEFAULT_CONTEXT_TEMPLATE).toContain("</ZEP_CONTEXT>");
  });

  it("is byte-identical to the canonical template shared across siblings", () => {
    expect(DEFAULT_CONTEXT_TEMPLATE).toBe(
      "The following context is retrieved from Zep, the agent's long-term memory. " +
        "It contains relevant facts, entities, and prior knowledge about the user. " +
        "Use it to inform your responses.\n\n" +
        "<ZEP_CONTEXT>\n" +
        "{context}\n" +
        "</ZEP_CONTEXT>",
    );
  });
});

describe("ZepInputProcessor template rendering", () => {
  it("safely renders context containing '{' and '%' characters", async () => {
    const zep = makeFakeZep();
    const weirdContext = "50% of users like {curly braces} and $pecial ch@rs";
    zep.thread.getUserContext.mockResolvedValueOnce({ context: weirdContext });
    const processor = new ZepInputProcessor({ client: asZep(zep), userId: "u1", threadId: "t1" });

    const result = await processor.processInput({
      messages: [userMessage("hi")],
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    const sysMessage = result.systemMessages[0] as { content: string };
    expect(sysMessage.content).toContain(weirdContext);
  });

  it("formatContext override wins over contextTemplate", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "raw facts" });
    const formatContext = vi.fn((context: string) => `CUSTOM[${context}]`);
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      contextTemplate: "IGNORED {context}",
      formatContext,
    });

    const result = await processor.processInput({
      messages: [userMessage("hi")],
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    expect(formatContext).toHaveBeenCalledWith("raw facts");
    const sysMessage = result.systemMessages[0] as { content: string };
    expect(sysMessage.content).toBe("CUSTOM[raw facts]");
    expect(sysMessage.content).not.toContain("IGNORED");
  });

  it("contextBuilder replaces getUserContext (assert not called)", async () => {
    const zep = makeFakeZep();
    const contextBuilder = vi.fn().mockResolvedValue("builder context");
    const processor = new ZepInputProcessor({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      contextBuilder,
    });

    await processor.processInput({
      messages: [userMessage("hi")],
      messageList: {} as never,
      systemMessages: [],
      state: {},
      abort: neverAbort,
      retryCount: 0,
    } as never);

    expect(zep.thread.getUserContext).not.toHaveBeenCalled();
  });
});
