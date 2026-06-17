import { describe, it, expect, vi } from "vitest";
import { createZepRememberTool } from "../src/index.js";
import { makeFakeZep, asZep, run } from "./helpers.js";

describe("createZepRememberTool", () => {
  it("persists conversational messages via thread.addMessages", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      defaultMessageName: "Jane",
    });

    const result = await run(tool, { content: "I live in Portland", role: "user" });

    expect(result).toEqual({ stored: true, message: expect.any(String) });
    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const call = zep.thread.addMessages.mock.calls[0]!;
    const [threadId, req] = call;
    expect(threadId).toBe("t1");
    expect(req.messages[0]).toMatchObject({
      role: "user",
      content: "I live in Portland",
      name: "Jane",
    });
    expect(zep.graph.add).not.toHaveBeenCalled();
  });

  it("persists non-conversational data via graph.add", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });

    const result = await run(tool, { content: "Project Apollo ships Q3" });

    expect(result.stored).toBe(true);
    expect(zep.graph.add).toHaveBeenCalledWith({
      userId: "u1",
      type: "text",
      data: "Project Apollo ships Q3",
    });
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("routes to graph.add for a standalone graph binding", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphId: "kb-1" },
    });

    // Even with a role, no thread/user means graph.add is used.
    await run(tool, { content: "Refunds take 5 days", role: "assistant" });
    expect(zep.graph.add).toHaveBeenCalledWith({
      graphId: "kb-1",
      type: "text",
      data: "Refunds take 5 days",
    });
  });

  it("maps unknown roles to a valid Zep RoleType", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });
    await run(tool, { content: "hi", role: "Human" });
    expect(zep.thread.addMessages.mock.calls[0]![1].messages[0].role).toBe("user");
  });

  it("truncates an over-long message to the 4000-char limit and warns", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      logger: { warn },
    });

    const longContent = "a".repeat(5000);
    const result = await run(tool, { content: longContent, role: "user" });

    expect(result.stored).toBe(true);
    const sent = zep.thread.addMessages.mock.calls[0]![1].messages[0].content as string;
    expect(sent.length).toBe(4000);
    expect(warn).toHaveBeenCalledOnce();
    // The warning must carry only lengths/counts — never the content itself.
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("5000");
    expect(warnArg).toContain("4000");
    expect(warnArg).not.toContain("aaaa");
  });

  it("truncates over-long graph.add data to the 10000-char limit and warns", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      logger: { warn },
    });

    const longContent = "b".repeat(12000);
    const result = await run(tool, { content: longContent });

    expect(result.stored).toBe(true);
    const sent = zep.graph.add.mock.calls[0]![0].data as string;
    expect(sent.length).toBe(10000);
    expect(warn).toHaveBeenCalledOnce();
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("12000");
    expect(warnArg).toContain("10000");
    expect(warnArg).not.toContain("bbbb");
  });

  it("does not truncate or warn when content is within limits", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      logger: { warn },
    });

    const result = await run(tool, { content: "short message", role: "user" });

    expect(result.stored).toBe(true);
    expect(zep.thread.addMessages.mock.calls[0]![1].messages[0].content).toBe("short message");
    expect(warn).not.toHaveBeenCalled();
  });

  it("returns stored: false on empty content without calling Zep", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });
    const result = await run(tool, { content: "   " });
    expect(result.stored).toBe(false);
    expect(zep.graph.add).not.toHaveBeenCalled();
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("never throws when Zep fails — logs and returns stored: false", async () => {
    const zep = makeFakeZep();
    zep.graph.add.mockRejectedValueOnce(new Error("503 upstream"));
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      logger: { warn },
    });

    const result = await run(tool, { content: "remember this" });
    expect(result.stored).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
  });

  it("reports not-configured when nothing is bound", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: {},
      logger: { warn },
    });
    const result = await run(tool, { content: "x" });
    expect(result.stored).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
  });
});
