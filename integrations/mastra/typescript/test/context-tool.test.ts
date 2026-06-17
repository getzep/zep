import { describe, it, expect, vi } from "vitest";
import { createZepContextTool } from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

const ctx = {} as never;

describe("createZepContextTool", () => {
  it("retrieves the user context block via thread.getUserContext", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "FACTS: Jane is in Portland" });
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });

    const result = await tool.execute!({}, ctx);

    expect(result).toEqual({ context: "FACTS: Jane is in Portland", found: true });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", {});
  });

  it("passes a templateId when provided", async () => {
    const zep = makeFakeZep();
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      templateId: "tmpl-9",
    });
    await tool.execute!({}, ctx);
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", { templateId: "tmpl-9" });
  });

  it("returns found: false on an empty context", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "   " });
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });
    const result = await tool.execute!({}, ctx);
    expect(result).toEqual({ context: "", found: false });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockRejectedValueOnce(new Error("boom"));
    const warn = vi.fn();
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      logger: { warn },
    });
    const result = await tool.execute!({}, ctx);
    expect(result).toEqual({ context: "", found: false });
    expect(warn).toHaveBeenCalledOnce();
  });
});
