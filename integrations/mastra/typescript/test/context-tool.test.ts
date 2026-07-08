import { describe, it, expect, vi } from "vitest";
import { createZepContextTool } from "../src/index.js";
import { makeFakeZep, asZep, run } from "./helpers.js";

describe("createZepContextTool", () => {
  it("retrieves the user context block via thread.getUserContext", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "FACTS: Jane is in Portland" });
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });

    const result = await run(tool, {});

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
    await run(tool, {});
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", { templateId: "tmpl-9" });
  });

  it("returns found: false on an empty context", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "   " });
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });
    const result = await run(tool, {});
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
    const result = await run(tool, {});
    expect(result).toEqual({ context: "", found: false });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("resolves identity from requestContext when resolveIdentity is provided", async () => {
    const zep = makeFakeZep();
    const resolveIdentity = vi.fn().mockReturnValue({ threadId: "t2" });
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      resolveIdentity,
    });

    await run(tool, {}, { requestContext: { tenant: "acme" } });

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t2", {});
  });

  it("falls back to constructor binding when resolveIdentity is unset", async () => {
    const zep = makeFakeZep();
    const tool = createZepContextTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });

    await run(tool, {}, { requestContext: {} });

    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", {});
  });
});
