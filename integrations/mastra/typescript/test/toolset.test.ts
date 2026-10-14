import { describe, it, expect, vi } from "vitest";
import { createZepToolset, createZepUserAndThread } from "../src/index.js";
import { asZep, makeFakeZep, run } from "./helpers.js";

describe("createZepToolset", () => {
  it("returns tools for the Agent tools record", () => {
    const toolset = createZepToolset({
      client: asZep(makeFakeZep()),
      binding: { graphUuid: "graph-1", threadUuid: "thread-1" },
    });
    expect(toolset.zepRemember.id).toBe("zep-remember");
    expect(toolset.zepSearch.id).toBe("zep-search");
    expect(toolset.zepContext.id).toBe("zep-context");
  });

  it("applies search scope and limit options", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEpisodes.mockResolvedValueOnce({
      data: [{ content: "raw text" }],
    });
    const { zepSearch } = createZepToolset({
      client: asZep(zep),
      binding: { graphUuid: "graph-1", threadUuid: "thread-1" },
      searchScope: "episodes",
      searchLimit: 3,
    });

    const result = await run(zepSearch, { query: "query" });

    expect(result.facts).toEqual(["raw text"]);
    expect(zep.graph.searchEpisodes).toHaveBeenCalledWith("graph-1", {
      query: "query",
      pageSize: 3,
      reranker: "rrf",
    });
  });

  it("forwards per-call identity to all tools", async () => {
    const zep = makeFakeZep();
    const resolveIdentity = vi.fn().mockReturnValue({
      graphUuid: "graph-2",
      threadUuid: "thread-2",
    });
    const { zepRemember, zepSearch, zepContext } = createZepToolset({
      client: asZep(zep),
      binding: { graphUuid: "graph-1", threadUuid: "thread-1" },
      resolveIdentity,
    });
    const requestContext = { tenant: "acme" };

    await run(zepSearch, { query: "query" }, { requestContext });
    await run(zepRemember, { content: "hello", role: "user" }, { requestContext });
    await run(zepContext, {}, { requestContext });

    expect(zep.graph.searchEdges).toHaveBeenCalledWith("graph-2", expect.any(Object));
    expect(zep.thread.addMessages).toHaveBeenCalledWith("thread-2", expect.any(Object));
    expect(zep.thread.getContext).toHaveBeenCalledWith("thread-2", {});
    expect(resolveIdentity).toHaveBeenCalledTimes(3);
    expect(resolveIdentity).toHaveBeenCalledWith(requestContext);
  });
});

describe("createZepUserAndThread", () => {
  it("creates the user and thread and returns server-generated UUIDs", async () => {
    const zep = makeFakeZep();

    const result = await createZepUserAndThread({
      client: asZep(zep),
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });

    expect(result).toEqual({
      userUuid: "u1",
      graphUuid: "g1",
      threadUuid: "t1",
    });
    expect(zep.user.create).toHaveBeenCalledWith({
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(zep.thread.create).toHaveBeenCalledWith({ userUuid: "u1" });
    expect(zep.user.create.mock.calls[0]![0]).not.toHaveProperty("userId");
    expect(zep.thread.create.mock.calls[0]![0]).not.toHaveProperty("threadId");
  });

  it("forwards supported user creation options", async () => {
    const zep = makeFakeZep();
    const metadata = { tenant: "acme" };

    await createZepUserAndThread({
      client: asZep(zep),
      metadata,
      timeZone: "America/Los_Angeles",
      disableDefaultOntology: true,
    });

    expect(zep.user.create).toHaveBeenCalledWith({
      metadata,
      timeZone: "America/Los_Angeles",
      disableDefaultOntology: true,
    });
  });

  it("runs the setup hook after creating the user", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn();

    await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
    });

    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), "u1");
    expect(zep.user.create.mock.invocationCallOrder[0]!).toBeLessThan(
      onUserCreated.mock.invocationCallOrder[0]!,
    );
    expect(onUserCreated.mock.invocationCallOrder[0]!).toBeLessThan(
      zep.thread.create.mock.invocationCallOrder[0]!,
    );
  });

  it("logs a setup hook failure and continues to create the thread", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const onUserCreated = vi.fn().mockRejectedValue(new Error("hook failed"));

    const result = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
      logger: { warn },
    });

    expect(result?.threadUuid).toBe("t1");
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.thread.create).toHaveBeenCalledOnce();
  });

  it("runs the setup hook before returning a thread-creation failure", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(new Error("thread create failed"));
    const onUserCreated = vi.fn();
    const warn = vi.fn();

    const result = await createZepUserAndThread({
      client: asZep(zep),
      onUserCreated,
      logger: { warn },
    });

    expect(result).toBeNull();
    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), "u1");
    expect(zep.thread.create).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns null and logs when user creation fails", async () => {
    const zep = makeFakeZep();
    zep.user.create.mockRejectedValueOnce(new Error("503 upstream"));
    const warn = vi.fn();

    const result = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });

    expect(result).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.thread.create).not.toHaveBeenCalled();
  });

  it("returns null and logs when thread creation fails", async () => {
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(new Error("503 upstream"));
    const warn = vi.fn();

    const result = await createZepUserAndThread({
      client: asZep(zep),
      logger: { warn },
    });

    expect(result).toBeNull();
    expect(warn).toHaveBeenCalledOnce();
  });
});
