import { describe, it, expect, vi } from "vitest";
import { createZepToolset, ensureZepUserAndThread } from "../src/index.js";
import { makeFakeZep, asZep, run } from "./helpers.js";

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
    const result = await run(zepSearch, { query: "q" });
    expect(result.facts).toEqual(["raw text"]);
    // scope/limit are pinned by createZepToolset; reranker is left exposed
    // (pin-or-expose default) and so is sent with Zep's documented default.
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "q",
      scope: "episodes",
      reranker: "rrf",
      limit: 3,
    });
  });

  it("forwards resolveIdentity to all three tools", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ edges: [{ fact: "f" }] });
    const resolveIdentity = vi.fn().mockReturnValue({ userId: "u2", threadId: "t2" });
    const { zepRemember, zepSearch, zepContext } = createZepToolset({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      resolveIdentity,
    });
    const requestContext = { tenant: "acme" };

    await run(zepSearch, { query: "q" }, { requestContext });
    expect(zep.graph.search).toHaveBeenCalledWith(expect.objectContaining({ userId: "u2" }));

    await run(zepRemember, { content: "hi", role: "user" }, { requestContext });
    expect(zep.thread.addMessages).toHaveBeenCalledWith("t2", expect.anything());

    await run(zepContext, {}, { requestContext });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t2", {});

    expect(resolveIdentity).toHaveBeenCalledTimes(3);
    expect(resolveIdentity).toHaveBeenCalledWith(requestContext);
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

  it("onUserCreated fires only on actual user creation", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
    });
    expect(ok).toBe(true);
    expect(onUserCreated).toHaveBeenCalledOnce();
    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), "u1");
  });

  it("onUserCreated still fires when thread creation fails after the user was created", async () => {
    // A transient thread.create failure must not skip the hook forever: a
    // retry hits the already-exists path with userCreated=false.
    const zep = makeFakeZep();
    zep.thread.create.mockRejectedValueOnce(new Error("500 internal"));
    const onUserCreated = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
      logger: { warn: vi.fn() },
    });
    expect(ok).toBe(false);
    expect(onUserCreated).toHaveBeenCalledOnce();
    expect(onUserCreated).toHaveBeenCalledWith(asZep(zep), "u1");
  });

  it("onUserCreated does not fire on 409 already-exists", async () => {
    const zep = makeFakeZep();
    const conflict = Object.assign(new Error("user already exists"), { statusCode: 409 });
    zep.user.add.mockRejectedValueOnce(conflict);
    const onUserCreated = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
    });
    expect(ok).toBe(true);
    expect(onUserCreated).not.toHaveBeenCalled();
  });

  it("genuine ensure failures (401/500) log warn and return false", async () => {
    const zep = makeFakeZep();
    const authError = Object.assign(new Error("unauthorized"), { statusCode: 401 });
    zep.user.add.mockRejectedValueOnce(authError);
    const warn = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      logger: { warn },
    });
    expect(ok).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.thread.create).not.toHaveBeenCalled();
  });

  it("a genuine 500 during user.add is not misread as already-exists, even if the message mentions conflict", async () => {
    const zep = makeFakeZep();
    const serverError = Object.assign(new Error("internal conflict while writing"), {
      statusCode: 500,
    });
    zep.user.add.mockRejectedValueOnce(serverError);
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

  it("onUserCreated errors are logged and not thrown", async () => {
    const zep = makeFakeZep();
    const onUserCreated = vi.fn().mockRejectedValue(new Error("hook boom"));
    const warn = vi.fn();
    const ok = await ensureZepUserAndThread({
      client: asZep(zep),
      userId: "u1",
      threadId: "t1",
      onUserCreated,
      logger: { warn },
    });
    expect(ok).toBe(true);
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0]![0]).toContain("hook boom");
  });
});
