import { describe, it, expect, vi } from "vitest";
import { ZepError } from "@getzep/zep-cloud";
import {
  getZepContext,
  persistZepTurn,
  createZepOnFinish,
  createZepUserAndThread,
} from "../src/index.js";
import {
  makeFakeZep,
  asZep,
  USER_UUID,
  GRAPH_UUID,
  THREAD_UUID,
} from "./helpers.js";

describe("getZepContext", () => {
  it("returns the trimmed context block", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockResolvedValueOnce({ context: "  BLOCK  " });
    const context = await getZepContext(asZep(zep), "t1");
    expect(context).toBe("BLOCK");
    expect(zep.thread.getContext).toHaveBeenCalledWith("t1", {});
  });

  it("passes a templateUuid when provided", async () => {
    const zep = makeFakeZep();
    await getZepContext(asZep(zep), "t1", { templateUuid: "tmpl-1" });
    expect(zep.thread.getContext).toHaveBeenCalledWith("t1", { templateUuid: "tmpl-1" });
  });

  it("returns an empty string and warns when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockRejectedValueOnce(new Error("boom"));
    const warn = vi.fn();
    const context = await getZepContext(asZep(zep), "t1", { logger: { warn } });
    expect(context).toBe("");
    expect(warn).toHaveBeenCalledOnce();
  });

  it("skips and warns when threadUuid is empty", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const context = await getZepContext(asZep(zep), "", { logger: { warn } });
    expect(context).toBe("");
    expect(zep.thread.getContext).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("persistZepTurn", () => {
  it("persists both user and assistant messages", async () => {
    const zep = makeFakeZep();
    await persistZepTurn(asZep(zep), "t1", {
      user: "Hi there",
      assistant: "Hello!",
      userName: "Jane",
    });
    const [threadUuid, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(threadUuid).toBe("t1");
    expect(req.messages).toEqual([
      { role: "user", content: "Hi there", name: "Jane" },
      { role: "assistant", content: "Hello!" },
    ]);
  });

  it("persists only the side that is present", async () => {
    const zep = makeFakeZep();
    await persistZepTurn(asZep(zep), "t1", { assistant: "reply only" });
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([{ role: "assistant", content: "reply only" }]);
  });

  it("returns the context block when returnContext is set", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockResolvedValueOnce({ context: "  fresh ctx  " });
    const ctx = await persistZepTurn(
      asZep(zep),
      "t1",
      { user: "hi" },
      { returnContext: true },
    );
    expect(ctx).toBe("fresh ctx");
    expect(zep.thread.addMessages.mock.calls[0]![1].returnContext).toBe(true);
  });

  it("truncates over-long content and warns with lengths only", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    await persistZepTurn(
      asZep(zep),
      "t1",
      { user: "a".repeat(5000) },
      { logger: { warn } },
    );
    const sent = zep.thread.addMessages.mock.calls[0]![1].messages[0].content as string;
    expect(sent.length).toBe(4000);
    expect(warn).toHaveBeenCalledOnce();
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("5000");
    expect(warnArg).not.toContain("aaaa");
  });

  it("returns null without calling Zep when nothing to persist", async () => {
    const zep = makeFakeZep();
    const result = await persistZepTurn(asZep(zep), "t1", { user: "  ", assistant: "" });
    expect(result).toBeNull();
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("503"));
    const warn = vi.fn();
    const result = await persistZepTurn(
      asZep(zep),
      "t1",
      { user: "hi" },
      { logger: { warn } },
    );
    expect(result).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("createZepOnFinish", () => {
  it("persists the turn exactly once with the final assistant text", async () => {
    const zep = makeFakeZep();
    const onFinish = createZepOnFinish({
      client: asZep(zep),
      threadUuid: "t1",
      user: "What do you know about me?",
      userName: "Jane",
    });

    // Simulate the single onFinish event the SDK fires per turn — its `text`
    // is the FINAL assistant text, not any intermediate tool-call preamble.
    await onFinish({ text: "You live in Portland and love hiking." });

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([
      { role: "user", content: "What do you know about me?", name: "Jane" },
      { role: "assistant", content: "You live in Portland and love hiking." },
    ]);
  });

  it("does not persist intermediate preamble — only the event's final text lands", async () => {
    // A multi-step tool loop would call doGenerate per step with preamble like
    // "Let me look that up...", but onFinish is invoked once with the final
    // answer. The callback persists that single event, never the preamble.
    const zep = makeFakeZep();
    const onFinish = createZepOnFinish({
      client: asZep(zep),
      threadUuid: "t1",
      user: "Find my last order.",
    });

    await onFinish({ text: "Your last order was a blue mug, shipped Tuesday." });

    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const sent = zep.thread.addMessages.mock.calls[0]![1].messages as Array<{
      role: string;
      content: string;
    }>;
    const assistant = sent.find((m) => m.role === "assistant");
    expect(assistant?.content).toBe("Your last order was a blue mug, shipped Tuesday.");
    expect(sent.filter((m) => m.role === "assistant")).toHaveLength(1);
  });

  it("resolves the user side from a function when provided", async () => {
    const zep = makeFakeZep();
    const onFinish = createZepOnFinish({
      client: asZep(zep),
      threadUuid: "t1",
      user: () => "resolved user input",
    });
    await onFinish({ text: "reply" });
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([
      { role: "user", content: "resolved user input" },
      { role: "assistant", content: "reply" },
    ]);
  });

  it("persists assistant-only when no user is supplied", async () => {
    const zep = makeFakeZep();
    const onFinish = createZepOnFinish({ client: asZep(zep), threadUuid: "t1" });
    await onFinish({ text: "reply only" });
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([{ role: "assistant", content: "reply only" }]);
  });

  it("does nothing when both sides are empty", async () => {
    const zep = makeFakeZep();
    const onFinish = createZepOnFinish({ client: asZep(zep), threadUuid: "t1", user: "  " });
    await onFinish({ text: "   " });
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("never throws when Zep persistence fails", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("503"));
    const warn = vi.fn();
    const onFinish = createZepOnFinish({
      client: asZep(zep),
      threadUuid: "t1",
      user: "hi",
      logger: { warn },
    });
    await expect(onFinish({ text: "reply" })).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("createZepUserAndThread", () => {
  it("creates the user and thread and returns their UUIDs", async () => {
    const zep = makeFakeZep();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(identity).toEqual({
      userUuid: USER_UUID,
      graphUuid: GRAPH_UUID,
      threadUuid: THREAD_UUID,
    });
    expect(zep.user.create).toHaveBeenCalledWith({
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(zep.thread.create).toHaveBeenCalledWith({ userUuid: USER_UUID });
  });

  it("passes the optional developer-assigned names through", async () => {
    const zep = makeFakeZep();
    await createZepUserAndThread({
      client: asZep(zep),
      userId: "app-user-1",
      threadId: "app-thread-1",
    });
    expect(zep.user.create).toHaveBeenCalledWith({ userId: "app-user-1" });
    expect(zep.thread.create).toHaveBeenCalledWith({
      userUuid: USER_UUID,
      threadId: "app-thread-1",
    });
  });

  it("returns null and warns when user.create fails", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockRejectedValueOnce(
      new ZepError({ message: "unauthorized", statusCode: 401 }),
    );
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });
    expect(identity).toBeNull();
    expect(zep.thread.create).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0]![0] as string).toContain("user.create failed");
  });

  it("returns null when the created user has no uuid or graphUuid", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockResolvedValueOnce({});
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });
    expect(identity).toBeNull();
    expect(zep.thread.create).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns null (not throw) when thread creation hard-fails", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(
      new ZepError({ message: "internal", statusCode: 500 }),
    );
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });
    expect(identity).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
  });

  it("onUserCreated fires once with the new user UUID", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn().mockResolvedValue(undefined);
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
    });
    expect(identity?.userUuid).toBe(USER_UUID);
    expect(onUserCreated).toHaveBeenCalledTimes(1);
    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), USER_UUID);
  });

  it("onUserCreated does not fire when user.create fails", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockRejectedValueOnce(new Error("boom"));
    const onUserCreated = vi.fn().mockResolvedValue(undefined);
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
      logger: { warn: vi.fn() },
    });
    expect(identity).toBeNull();
    expect(onUserCreated).not.toHaveBeenCalled();
  });

  it("onUserCreated hook errors are logged, not thrown", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn().mockRejectedValue(new Error("hook exploded"));
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
      logger: { warn },
    });
    expect(identity?.threadUuid).toBe(THREAD_UUID);
    expect(onUserCreated).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalled();
    const warnArg = warn.mock.calls.at(-1)![0] as string;
    expect(warnArg).toContain("onUserCreated");
  });
});
