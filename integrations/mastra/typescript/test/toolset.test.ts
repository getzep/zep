import { describe, it, expect, vi } from "vitest";
import { createZepToolset, ensureZepUserAndThread } from "../src/index.js";
import { makeFakeZep, asZep } from "./helpers.js";

const ctx = {} as never;

describe("createZepToolset", () => {
  it("returns the three tools keyed for an Agent tools record", () => {
    const zep = makeFakeZep();
    const toolset = createZepToolset({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
    });
    expect(toolset.zepRemember.id).toBe("zep-remember");
    expect(toolset.zepSearch.id).toBe("zep-search");
    expect(toolset.zepContext.id).toBe("zep-context");
  });

  it("propagates search scope/limit to the search tool", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ episodes: [{ content: "raw text" }] });
    const { zepSearch } = createZepToolset({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      searchScope: "episodes",
      searchLimit: 3,
    });
    const result = await zepSearch.execute!({ query: "q" }, ctx);
    expect(result.facts).toEqual(["raw text"]);
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "q",
      scope: "episodes",
      limit: 3,
    });
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
