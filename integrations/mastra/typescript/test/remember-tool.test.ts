import { describe, it, expect, vi } from "vitest";
import { createZepRememberTool } from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

const ctx = {} as never;

describe("createZepRememberTool", () => {
  it("persists conversational messages via thread.addMessages", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      defaultMessageName: "Jane",
    });

    const result = await tool.execute!(
      { content: "I live in Portland", role: "user" },
      ctx,
    );

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

    const result = await tool.execute!({ content: "Project Apollo ships Q3" }, ctx);

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
    await tool.execute!({ content: "Refunds take 5 days", role: "assistant" }, ctx);
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
    await tool.execute!({ content: "hi", role: "Human" }, ctx);
    expect(zep.thread.addMessages.mock.calls[0]![1].messages[0].role).toBe("user");
  });

  it("returns stored: false on empty content without calling Zep", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });
    const result = await tool.execute!({ content: "   " }, ctx);
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

    const result = await tool.execute!({ content: "remember this" }, ctx);
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
    const result = await tool.execute!({ content: "x" }, ctx);
    expect(result.stored).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
  });
});
