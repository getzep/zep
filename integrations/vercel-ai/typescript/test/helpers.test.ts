import { describe, it, expect, vi } from "vitest";
import { getZepContext, persistZepTurn, ensureZepUserAndThread } from "../src/index.js";
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

  it("treats an already-existing user as success", async () => {
    const zep = makeFakeZep();
    zep.user.add.mockRejectedValueOnce(new Error("user already exists (409)"));
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn: vi.fn(), debug: vi.fn() },
    });
    expect(ok).toBe(true);
    expect(zep.thread.create).toHaveBeenCalledOnce();
  });

  it("returns false (not throw) when thread creation hard-fails", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(new Error("500 internal"));
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
});
