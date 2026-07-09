import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context, LlmRequest } from "@google/adk";
import { createZepBeforeModelCallback } from "../src/before-model-callback.js";
import {
  capturingLogger,
  fakeContext,
  fakeLlmRequest,
  mockZepClient,
  persistedContent,
  silentLogger,
} from "./helpers.js";
import { MESSAGE_CONTENT_MAX, MESSAGE_CONTENT_TRUNCATE_TO } from "../src/limits.js";

function run(
  zep: ReturnType<typeof mockZepClient>["client"],
  ctx: ReturnType<typeof fakeContext>,
  req: ReturnType<typeof fakeLlmRequest>,
  options: Record<string, unknown> = {},
) {
  const cb = createZepBeforeModelCallback(zep as unknown as ZepClient, {
    logger: silentLogger,
    ...options,
  });
  return cb({ context: ctx as unknown as Context, request: req as unknown as LlmRequest });
}

describe("createZepBeforeModelCallback", () => {
  it("persists the user message and injects the returned context block", async () => {
    const { client, mocks } = mockZepClient({
      addMessagesContext: "<FACTS>\n- Alice lives in Portland.\n</FACTS>",
    });
    const ctx = fakeContext({
      userId: "user-1",
      sessionId: "thread-1",
      userText: "Where do I live?",
    });
    const req = fakeLlmRequest();

    const result = await run(client, ctx, req, {
      firstName: "Alice",
      lastName: "Smith",
    });

    // Callback always returns undefined (proceed to the model).
    expect(result).toBeUndefined();

    // Persisted with returnContext + display name.
    expect(mocks.addMessages).toHaveBeenCalledWith("thread-1", {
      messages: [
        { role: "user", content: "Where do I live?", name: "Alice Smith" },
      ],
      returnContext: true,
      ignoreRoles: undefined,
    });

    // Context injected into systemInstruction.
    const sys = req.config?.systemInstruction as string;
    expect(sys).toContain("Alice lives in Portland.");
    expect(sys).toContain("<ZEP_CONTEXT>");
  });

  it("prefers explicit identity options over the ADK session", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const ctx = fakeContext({
      userId: "adk-user",
      sessionId: "adk-session",
      userText: "hi",
    });

    await run(client, ctx, fakeLlmRequest(), {
      userId: "explicit-user",
      threadId: "explicit-thread",
    });

    expect(mocks.addMessages).toHaveBeenCalledWith(
      "explicit-thread",
      expect.anything(),
    );
  });

  it("never calls user.add or thread.create on the turn path", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const ctx = fakeContext({
      userId: "user-1",
      sessionId: "thread-1",
      userText: "hello",
    });

    await run(client, ctx, fakeLlmRequest(), {
      firstName: "Alice",
      lastName: "Smith",
    });

    expect(mocks.userAdd).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("logs a warning naming ensureUser/ensureThread on a Zep NotFound error and resolves without throwing", async () => {
    const { client, mocks } = mockZepClient();
    const notFound = Object.assign(new Error("user not found"), {
      statusCode: 404,
    });
    mocks.addMessages.mockRejectedValueOnce(notFound);
    const logger = capturingLogger();

    const result = await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
      fakeLlmRequest(),
      { logger },
    );

    expect(result).toBeUndefined();
    const warning = logger.warns.find((w) => w.includes("ensureUser"));
    expect(warning).toBeDefined();
    expect(warning).toContain("ensureThread");
  });

  it("resolves identity from session-state keys when no options are given", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const ctx = fakeContext({
      userId: "adk-user",
      sessionId: "adk-session",
      userText: "hi",
      state: {
        zep_user_id: "state-user",
        zep_thread_id: "state-thread",
        zep_first_name: "Bob",
      },
    });

    await run(client, ctx, fakeLlmRequest());

    expect(mocks.addMessages).toHaveBeenCalledWith(
      "state-thread",
      expect.objectContaining({
        messages: [{ role: "user", content: "hi", name: "Bob" }],
      }),
    );
  });

  it("does nothing when there is no user text", async () => {
    const { client, mocks } = mockZepClient();
    const ctx = fakeContext({ userId: "u", sessionId: "t" });

    await run(client, ctx, fakeLlmRequest());

    expect(mocks.addMessages).not.toHaveBeenCalled();
    expect(mocks.userAdd).not.toHaveBeenCalled();
  });

  it("does not inject when Zep returns no context", async () => {
    const { client } = mockZepClient({ addMessagesContext: undefined });
    const req = fakeLlmRequest();

    await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
      req,
    );

    expect(req.config?.systemInstruction).toBeUndefined();
  });

  it("never throws when Zep.addMessages fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.addMessages.mockRejectedValueOnce(new Error("Zep down"));
    const req = fakeLlmRequest();

    const result = await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
      req,
    );

    expect(result).toBeUndefined();
    expect(req.config?.systemInstruction).toBeUndefined();
  });

  it("persists across turns without ever provisioning the user/thread itself", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const cb = createZepBeforeModelCallback(client as unknown as ZepClient, {
      logger: silentLogger,
      userId: "u",
      threadId: "t",
    });

    for (const text of ["first", "second", "third"]) {
      await cb({
        context: fakeContext({
          userText: text,
          invocationId: `inv-${text}`,
        }) as unknown as Context,
        request: fakeLlmRequest() as unknown as LlmRequest,
      });
    }

    expect(mocks.userAdd).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
    expect(mocks.addMessages).toHaveBeenCalledTimes(3);
  });

  it("preserves an existing string system instruction", async () => {
    const { client } = mockZepClient({ addMessagesContext: "memory-block" });
    const req = fakeLlmRequest({ systemInstruction: "You are helpful." });

    await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
      req,
    );

    const sys = req.config?.systemInstruction as string;
    expect(sys).toContain("You are helpful.");
    expect(sys).toContain("memory-block");
  });

  it("persists the user message once per invocation across a tool-loop turn", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const cb = createZepBeforeModelCallback(client as unknown as ZepClient, {
      logger: silentLogger,
      userId: "u",
      threadId: "t",
    });

    // A tool-using turn fires the before-model hook multiple times with the
    // SAME invocationId and the same user content.
    const userContent = {
      role: "user" as const,
      parts: [{ text: "what's the weather and book me a table" }],
    };
    for (let i = 0; i < 3; i++) {
      await cb({
        context: fakeContext({
          userId: "u",
          sessionId: "t",
          invocationId: "inv-tool-loop",
          userContent,
        }) as unknown as Context,
        request: fakeLlmRequest() as unknown as LlmRequest,
      });
    }

    // The user message reaches Zep exactly once despite three hook firings.
    expect(mocks.addMessages).toHaveBeenCalledTimes(1);
  });

  it("re-persists on the next invocation even with identical user text", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const cb = createZepBeforeModelCallback(client as unknown as ZepClient, {
      logger: silentLogger,
      userId: "u",
      threadId: "t",
    });

    for (const invocationId of ["inv-1", "inv-2"]) {
      await cb({
        context: fakeContext({
          userId: "u",
          sessionId: "t",
          invocationId,
          userText: "same question",
        }) as unknown as Context,
        request: fakeLlmRequest() as unknown as LlmRequest,
      });
    }

    // A genuinely new turn (new invocationId) is not blocked by the guard.
    expect(mocks.addMessages).toHaveBeenCalledTimes(2);
  });

  it("does not set the dedup marker when addMessages fails", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    mocks.addMessages.mockRejectedValueOnce(new Error("transient"));
    const cb = createZepBeforeModelCallback(client as unknown as ZepClient, {
      logger: silentLogger,
      userId: "u",
      threadId: "t",
    });

    const userContent = {
      role: "user" as const,
      parts: [{ text: "hi" }],
    };
    // First firing fails; the marker must NOT be set, so a retry within the
    // same invocation re-attempts persistence rather than being suppressed.
    for (let i = 0; i < 2; i++) {
      await cb({
        context: fakeContext({
          userId: "u",
          sessionId: "t",
          invocationId: "inv-retry",
          userContent,
        }) as unknown as Context,
        request: fakeLlmRequest() as unknown as LlmRequest,
      });
    }

    expect(mocks.addMessages).toHaveBeenCalledTimes(2);
  });

  it("truncates an over-long user message and warns with lengths only", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const logger = capturingLogger();
    const longText = "x".repeat(MESSAGE_CONTENT_MAX + 500);

    await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: longText }),
      fakeLlmRequest(),
      { logger },
    );

    // Persisted content is bounded to the truncation target.
    const persisted = persistedContent(mocks.addMessages);
    expect(persisted.length).toBe(MESSAGE_CONTENT_TRUNCATE_TO);

    // A warning was logged, carrying lengths but never the message content.
    const warning = logger.warns.find((w) => w.includes("Truncated"));
    expect(warning).toBeDefined();
    expect(warning).toContain(String(MESSAGE_CONTENT_MAX + 500));
    expect(warning).toContain(String(MESSAGE_CONTENT_TRUNCATE_TO));
    expect(warning).not.toContain("xxxxx");
  });

  it("does not truncate a user message at the limit", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const logger = capturingLogger();
    const exactText = "y".repeat(MESSAGE_CONTENT_MAX);

    await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: exactText }),
      fakeLlmRequest(),
      { logger },
    );

    const persisted = persistedContent(mocks.addMessages);
    expect(persisted.length).toBe(MESSAGE_CONTENT_MAX);
    expect(logger.warns.some((w) => w.includes("Truncated"))).toBe(false);
  });

  it("forwards ignoreRoles to addMessages", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
      fakeLlmRequest(),
      { ignoreRoles: ["assistant"] },
    );

    expect(mocks.addMessages).toHaveBeenCalledWith(
      "t",
      expect.objectContaining({ ignoreRoles: ["assistant"] }),
    );
  });
});
