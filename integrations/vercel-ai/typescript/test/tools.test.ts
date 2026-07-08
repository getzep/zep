import { describe, it, expect, vi } from "vitest";
import {
  createZepTools,
  createZepSearchTool,
  createZepRememberTool,
  createZepContextTool,
} from "../src/index.js";
import { makeFakeZep, asZep, run } from "./helpers.js";

describe("createZepSearchTool", () => {
  it("searches edges by default and returns facts", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({
      edges: [{ fact: "Jane lives in Portland" }, { fact: "Jane is an engineer" }],
    });
    const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });

    const result = await run(tool, { query: "where does Jane live" });

    expect(result).toEqual({
      facts: ["Jane lives in Portland", "Jane is an engineer"],
      found: true,
    });
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "where does Jane live",
      scope: "edges",
    });
  });

  it("honors a pinned scope, limit, and graphId binding", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({
      nodes: [{ name: "Apollo", summary: "A spacecraft program" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphId: "kb-1" },
      scope: "nodes",
      limit: 5,
    });

    const result = await run(tool, { query: "Apollo" });

    expect(result.facts).toEqual(["Apollo: A spacecraft program"]);
    expect(zep.graph.search).toHaveBeenCalledWith({
      graphId: "kb-1",
      query: "Apollo",
      scope: "nodes",
      limit: 5,
    });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockRejectedValueOnce(new Error("timeout"));
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      logger: { warn },
    });
    const result = await run(tool, { query: "x" });
    expect(result).toEqual({ facts: [], found: false });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns empty when nothing is bound", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: {},
      logger: { warn: vi.fn() },
    });
    const result = await run(tool, { query: "x" });
    expect(result.found).toBe(false);
    expect(zep.graph.search).not.toHaveBeenCalled();
  });
});

describe("createZepSearchTool pin-or-expose", () => {
  it("exposes scope/reranker/limit/mmrLambda/centerNodeUuid by default", () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    const keys = Object.keys(schema.shape);
    expect(keys).toEqual(
      expect.arrayContaining(["query", "scope", "reranker", "limit", "mmrLambda", "centerNodeUuid"]),
    );
  });

  it("six scopes accepted in the exposed schema", () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
    const schema = tool.inputSchema as unknown as {
      shape: { scope: { unwrap: () => { options: string[] } } };
    };
    const scopeValues = schema.shape.scope.unwrap().options;
    expect(scopeValues).toEqual([
      "edges",
      "nodes",
      "episodes",
      "observations",
      "thread_summaries",
      "auto",
    ]);
  });

  it("pinned params are hidden from the schema and always sent", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ nodes: [{ name: "Apollo" }] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      pinnedParams: { scope: "nodes", limit: 5 },
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).not.toContain("scope");
    expect(Object.keys(schema.shape)).not.toContain("limit");

    await run(tool, { query: "Apollo" });
    expect(zep.graph.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "nodes", limit: 5 }),
    );
  });

  it("hidden params are omitted from the schema and the SDK call", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      hiddenParams: ["mmrLambda", "centerNodeUuid"],
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).not.toContain("mmrLambda");
    expect(Object.keys(schema.shape)).not.toContain("centerNodeUuid");

    await run(tool, { query: "x" });
    const sentCall = zep.graph.search.mock.calls[0]![0] as Record<string, unknown>;
    expect(sentCall).not.toHaveProperty("mmrLambda");
    expect(sentCall).not.toHaveProperty("centerNodeUuid");
  });

  it("query-only call omits unset optional params from graph.search", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
    await run(tool, { query: "hello" });
    const sentCall = zep.graph.search.mock.calls[0]![0] as Record<string, unknown>;
    // Only query, userId, and scope's own default (edges) plus limit default may
    // be present; mmrLambda/centerNodeUuid must never be sent as null/undefined.
    expect(sentCall).not.toHaveProperty("mmrLambda");
    expect(sentCall).not.toHaveProperty("centerNodeUuid");
    expect(Object.values(sentCall).every((v) => v !== null && v !== undefined)).toBe(true);
  });

  it("legacy option args (scope/reranker/limit) pin the corresponding parameter", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      scope: "episodes",
      limit: 7,
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).not.toContain("scope");
    expect(Object.keys(schema.shape)).not.toContain("limit");

    await run(tool, { query: "x" });
    expect(zep.graph.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "episodes", limit: 7 }),
    );
  });

  it("model-supplied exposed params are forwarded to graph.search", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
    await run(tool, { query: "x", scope: "nodes", reranker: "mmr", mmrLambda: 0.5 });
    expect(zep.graph.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "nodes", reranker: "mmr", mmrLambda: 0.5 }),
    );
  });
});

describe("createZepRememberTool", () => {
  it("persists conversational messages via thread.addMessages", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      defaultMessageName: "Jane",
    });

    const result = await run(tool, { content: "I live in Portland", role: "user" });

    expect(result.stored).toBe(true);
    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const [threadId, req] = zep.thread.addMessages.mock.calls[0]!;
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
    const tool = createZepRememberTool({ client: asZep(zep), binding: { graphId: "kb-1" } });

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

  it("truncates over-long graph.add data to the 10,000-char limit and warns (lengths only)", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1" }, // no thread + no role → graph.add path
      logger: { warn },
    });

    // 12,000 chars — over Zep's 10,000-char graph.add limit.
    const result = await run(tool, { content: "z".repeat(12000) });

    expect(result.stored).toBe(true);
    const data = zep.graph.add.mock.calls[0]![0].data as string;
    // Capped below 10,000 (the package leaves headroom).
    expect(data.length).toBeLessThanOrEqual(10000);
    expect(data.length).toBeLessThan(12000);
    expect(warn).toHaveBeenCalledOnce();
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("12000");
    expect(warnArg).not.toContain("zzzz");
  });

  it("truncates an over-long message to the limit and warns (lengths only)", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { userId: "u1", threadId: "t1" },
      logger: { warn },
    });

    const result = await run(tool, { content: "a".repeat(5000), role: "user" });

    expect(result.stored).toBe(true);
    const sent = zep.thread.addMessages.mock.calls[0]![1].messages[0].content as string;
    expect(sent.length).toBe(4000);
    expect(warn).toHaveBeenCalledOnce();
    const warnArg = warn.mock.calls[0]![0] as string;
    expect(warnArg).toContain("5000");
    expect(warnArg).toContain("4000");
    expect(warnArg).not.toContain("aaaa");
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
    const tool = createZepRememberTool({ client: asZep(zep), binding: {}, logger: { warn } });
    const result = await run(tool, { content: "x" });
    expect(result.stored).toBe(false);
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("createZepContextTool", () => {
  it("retrieves the context block via thread.getUserContext", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "FACTS: Jane is in Portland" });
    const tool = createZepContextTool({ client: asZep(zep), threadId: "t1" });

    const result = await run(tool, {});

    expect(result).toEqual({ context: "FACTS: Jane is in Portland", found: true });
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", {});
  });

  it("passes a templateId when provided", async () => {
    const zep = makeFakeZep();
    const tool = createZepContextTool({
      client: asZep(zep),
      threadId: "t1",
      templateId: "tmpl-9",
    });
    await run(tool, {});
    expect(zep.thread.getUserContext).toHaveBeenCalledWith("t1", { templateId: "tmpl-9" });
  });

  it("returns found: false on an empty context", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockResolvedValueOnce({ context: "   " });
    const tool = createZepContextTool({ client: asZep(zep), threadId: "t1" });
    const result = await run(tool, {});
    expect(result).toEqual({ context: "", found: false });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.thread.getUserContext.mockRejectedValueOnce(new Error("boom"));
    const warn = vi.fn();
    const tool = createZepContextTool({
      client: asZep(zep),
      threadId: "t1",
      logger: { warn },
    });
    const result = await run(tool, {});
    expect(result).toEqual({ context: "", found: false });
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("createZepTools", () => {
  it("returns the three tools and propagates search scope/limit", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ episodes: [{ content: "raw text" }] });
    const { zepSearch, zepRemember, zepContext } = createZepTools(asZep(zep), {
      binding: { userId: "u1", threadId: "t1" },
      searchScope: "episodes",
      searchLimit: 3,
    });

    expect(typeof zepRemember.execute).toBe("function");
    expect(typeof zepContext.execute).toBe("function");

    const result = await run(zepSearch, { query: "q" });
    expect(result.facts).toEqual(["raw text"]);
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "q",
      scope: "episodes",
      limit: 3,
    });
  });
});
