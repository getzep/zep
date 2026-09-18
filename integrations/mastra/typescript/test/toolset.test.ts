import { describe, it, expect, vi } from "vitest";
import { createZepToolset, createZepUserAndThread } from "../src/index.js";
import { makeFakeZep, asZep, run, page } from "./helpers.js";

describe("createZepToolset", () => {
  it("returns the three tools keyed for an Agent tools record", () => {
    const zep = makeFakeZep();
    const toolset = createZepToolset({
      client: asZep(zep),
      binding: { graphUuid: "g1", threadUuid: "t1" },
    });
    expect(toolset.zepRemember.id).toBe("zep-remember");
    expect(toolset.zepSearch.id).toBe("zep-search");
    expect(toolset.zepContext.id).toBe("zep-context");
  });

  it("propagates search scope/limit to the search tool", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEpisodes.mockResolvedValueOnce(page([{ content: "raw text" }]));
    const { zepSearch } = createZepToolset({
      client: asZep(zep),
      binding: { graphUuid: "g1", threadUuid: "t1" },
      searchScope: "episodes",
      searchLimit: 3,
    });
    const result = await run(zepSearch, { query: "q" });
    expect(result.facts).toEqual(["raw text"]);
    // scope/limit are pinned by createZepToolset; reranker is left exposed
    // (pin-or-expose default) and so is sent with Zep's documented default.
    expect(zep.graph.searchEpisodes).toHaveBeenCalledWith("g1", {
      limit: 3,
      body: { query: "q", reranker: "rrf" },
    });
  });

  it("forwards resolveIdentity to all three tools", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce(page([{ fact: "f" }]));
    const resolveIdentity = vi.fn().mockReturnValue({ graphUuid: "g2", threadUuid: "t2" });
    const { zepRemember, zepSearch, zepContext } = createZepToolset({
      client: asZep(zep),
      binding: { graphUuid: "g1", threadUuid: "t1" },
      resolveIdentity,
    });
    const requestContext = { tenant: "acme" };

    await run(zepSearch, { query: "q" }, { requestContext });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g2", expect.anything());

    await run(zepRemember, { content: "hi", role: "user" }, { requestContext });
    expect(zep.thread.addMessages).toHaveBeenCalledWith("t2", expect.anything());

    await run(zepContext, {}, { requestContext });
    expect(zep.thread.getContext).toHaveBeenCalledWith("t2", {});

    expect(resolveIdentity).toHaveBeenCalledTimes(3);
    expect(resolveIdentity).toHaveBeenCalledWith(requestContext);
  });
});

describe("createZepUserAndThread", () => {
  it("creates the user and thread and returns their UUIDs", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockResolvedValueOnce({ uuid: "user-uuid", graphUuid: "graph-uuid" });
    zep.thread.create.mockResolvedValueOnce({ uuid: "thread-uuid" });

    const identity = await createZepUserAndThread({
      client: asZep(zep),
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });

    expect(identity).toEqual({
      userUuid: "user-uuid",
      graphUuid: "graph-uuid",
      threadUuid: "thread-uuid",
    });
    expect(zep.user.create).toHaveBeenCalledWith({
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(zep.thread.create).toHaveBeenCalledWith({ userUuid: "user-uuid" });
  });

  it("forwards the optional developer-assigned names", async () => {
    const zep = makeFakeZep();
    await createZepUserAndThread({
      client: asZep(zep),
      userId: "name-of-user",
      threadId: "name-of-thread",
    });
    expect(zep.user.create).toHaveBeenCalledWith({ userId: "name-of-user" });
    expect(zep.thread.create).toHaveBeenCalledWith({
      userUuid: "u1",
      threadId: "name-of-thread",
    });
  });

  it("returns null (not throw) when user creation fails", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockRejectedValueOnce(new Error("500 internal"));
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });
    expect(identity).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.thread.create).not.toHaveBeenCalled();
  });

  it("returns null (not throw) when thread creation fails", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(new Error("500 internal"));
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });
    expect(identity).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns null when the created user carries no UUID", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockResolvedValueOnce({ graphUuid: "graph-uuid" });
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });
    expect(identity).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.thread.create).not.toHaveBeenCalled();
  });

  it("onUserCreated receives the new user UUID and runs before the thread step", async () => {
    const zep = makeFakeZep();
    const order: string[] = [];
    zep.thread.create.mockImplementationOnce(async () => {
      order.push("thread.create");
      return { uuid: "t1" };
    });
    const onUserCreated = vi.fn(() => {
      order.push("onUserCreated");
    });

    const identity = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
    });

    expect(identity).not.toBeNull();
    expect(onUserCreated).toHaveBeenCalledOnce();
    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), "u1");
    expect(order).toEqual(["onUserCreated", "thread.create"]);
  });

  it("onUserCreated errors are logged and not thrown", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn().mockRejectedValue(new Error("hook boom"));
    const warn = vi.fn();
    const identity = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
      logger: { warn },
    });
    expect(identity).not.toBeNull();
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0]![0]).toContain("hook boom");
  });
});
