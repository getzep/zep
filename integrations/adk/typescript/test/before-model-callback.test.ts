import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context, LlmRequest } from "@google/adk";
import { createZepBeforeModelCallback } from "../src/before-model-callback.js";
import {
  fakeContext,
  fakeLlmRequest,
  mockZepClient,
  silentLogger,
} from "./helpers.js";

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

    // Lazy resource creation.
    expect(mocks.userAdd).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "user-1", firstName: "Alice" }),
    );
    expect(mocks.create).toHaveBeenCalledWith({
      threadId: "thread-1",
      userId: "user-1",
    });

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
    expect(mocks.userAdd).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "explicit-user" }),
    );
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

  it("treats an 'already exists' user/thread error as success", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    mocks.userAdd.mockRejectedValueOnce(new Error("user already exists"));
    mocks.create.mockRejectedValueOnce(new Error("thread already exists"));
    const req = fakeLlmRequest();

    await run(
      client,
      fakeContext({ userId: "u", sessionId: "t", userText: "hi" }),
      req,
    );

    // Persistence still happens despite the conflicts.
    expect(mocks.addMessages).toHaveBeenCalled();
    expect((req.config?.systemInstruction as string)).toContain("ctx");
  });

  it("creates Zep resources only once across turns", async () => {
    const { client, mocks } = mockZepClient({ addMessagesContext: "ctx" });
    const cb = createZepBeforeModelCallback(client as unknown as ZepClient, {
      logger: silentLogger,
      userId: "u",
      threadId: "t",
    });

    for (const text of ["first", "second", "third"]) {
      await cb({
        context: fakeContext({ userText: text }) as unknown as Context,
        request: fakeLlmRequest() as unknown as LlmRequest,
      });
    }

    expect(mocks.userAdd).toHaveBeenCalledTimes(1);
    expect(mocks.create).toHaveBeenCalledTimes(1);
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
