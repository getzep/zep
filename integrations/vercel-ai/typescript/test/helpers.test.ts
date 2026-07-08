import { describe, it, expect, vi } from "vitest";
import { Zep, ZepError } from "@getzep/zep-cloud";
import {
  getZepContext,
  persistZepTurn,
  createZepOnFinish,
  ensureZepUserAndThread,
} from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

describe("getZepContext", () => {
  it("returns the trimmed context block", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "  BLOCK  " });
    const context = await getZepContext(asZep(zep), "t1");
    expect(context).toBe("BLOCK");
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", {});
  });

  it("passes a templateId when provided", async () => {
    const zep = makeFakeZep();
    await getZepContext(asZep(zep), "t1", { templateId: "tmpl-1" });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", { templateId: "tmpl-1" });
  });

  it("returns an empty string and warns when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockRejectedValueOnce(new Error("boom"));
    const warn = vi.fn();
    const context = await getZepContext(asZep(zep), "t1", { logger: { warn } });
    expect(context).toBe("");
    expect(warn).toHaveBeenCalledOnce();
  });

  it("skips and warns when threadId is empty", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const context = await getZepContext(asZep(zep), "", { logger: { warn } });
    expect(context).toBe("");
    expect(zep.thread.getUserContext).not.toHaveBeenCalled();
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
    const [threadId, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(threadId).toBe("t1");
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
      threadId: "t1",
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
      threadId: "t1",
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
      threadId: "t1",
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
    const onFinish = createZepOnFinish({ client: asZep(zep), threadId: "t1" });
    await onFinish({ text: "reply only" });
    const req = zep.thread.addMessages.mock.calls[0]![1];
    expect(req.messages).toEqual([{ role: "assistant", content: "reply only" }]);
  });

  it("does nothing when both sides are empty", async () => {
    const zep = makeFakeZep();
    const onFinish = createZepOnFinish({ client: asZep(zep), threadId: "t1", user: "  " });
    await onFinish({ text: "   " });
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("never throws when Zep persistence fails", async () => {
    const zep = makeFakeZep();
    zep.thread.addMessages.mockRejectedValueOnce(new Error("503"));
    const warn = vi.fn();
    const onFinish = createZepOnFinish({
      client: asZep(zep),
      threadId: "t1",
      user: "hi",
      logger: { warn },
    });
    await expect(onFinish({ text: "reply" })).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("ensureZepUserAndThread", () => {
  it("creates the user and thread and returns true", async () => {
    const zep = makeFakeZep();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(ok).toBe(true);
    expect(zep.user.add).toHaveBeenCalledWith({
      userId: "u1",
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(zep.thread.create).toHaveBeenCalledWith({ threadId: "t1", userId: "u1" });
  });

  it("treats a typed 409 Conflict on user.add as success (debug, no warn)", async () => {
    const zep = makeFakeZep();
    zep.user.add.mockRejectedValueOnce(new Zep.ConflictError({}));
    const warn = vi.fn();
    const debug = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn, debug },
    });
    expect(ok).toBe(true);
    expect(zep.thread.create).toHaveBeenCalledOnce();
    // A genuine "already exists" is tolerated quietly, not warned.
    expect(warn).not.toHaveBeenCalled();
    expect(debug).toHaveBeenCalledOnce();
  });

  it("surfaces a non-conflict user.add error (e.g. 401) as a warning", async () => {
    const zep = makeFakeZep();
    // Not a 409 — must NOT be swallowed as "already exists".
    zep.user.add.mockRejectedValueOnce(
      new ZepError({ message: "unauthorized", statusCode: 401 }),
    );
    const warn = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn },
    });
    // thread.create still succeeds, so identity is ready overall.
    expect(ok).toBe(true);
    expect(warn).toHaveBeenCalledOnce();
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("user.add failed");
  });

  it("treats a typed 409 Conflict on thread.create as success", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(new Zep.ConflictError({}));
    const warn = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn },
    });
    expect(ok).toBe(true);
    expect(warn).not.toHaveBeenCalled();
  });

  it("returns false (not throw) when thread creation hard-fails", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(
      new ZepError({ message: "internal", statusCode: 500 }),
    );
    const warn = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn },
    });
    expect(ok).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
  });

  it("onUserCreated fires once when user.add succeeds", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn().mockResolvedValue(undefined);
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
    });
    expect(ok).toBe(true);
    expect(onUserCreated).toHaveBeenCalledTimes(1);
    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), "u1");
  });

  it("onUserCreated does not fire on 409 already-exists", async () => {
    const zep = makeFakeZep();
    zep.user.add.mockRejectedValueOnce(new Zep.ConflictError({}));
    const onUserCreated = vi.fn().mockResolvedValue(undefined);
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
    });
    expect(ok).toBe(true);
    expect(onUserCreated).not.toHaveBeenCalled();
  });

  it("onUserCreated hook errors are logged, not thrown", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn().mockRejectedValue(new Error("hook exploded"));
    const warn = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
      logger: { warn },
    });
    expect(ok).toBe(true);
    expect(onUserCreated).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalled();
    const warnArg = warn.mock.calls.at(-1)![0] as string;
    expect(warnArg).toContain("onUserCreated");
  });
});
