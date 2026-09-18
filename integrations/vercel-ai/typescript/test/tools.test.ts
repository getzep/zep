import { describe, it, expect, vi } from "vitest";
import {
  createZepTools,
  createZepSearchTool,
  createZepRememberTool,
  createZepContextTool,
} from "../src/index.js";
import { makeFakeZep, asZep, run, GRAPH_UUID, THREAD_UUID } from "./helpers.js";

describe("createZepSearchTool", () => {
  it("searches edges by default and returns facts", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce({
      data: [{ fact: "Jane lives in Portland" }, { fact: "Jane is an engineer" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });

    const result = await run(tool, { query: "where does Jane live" });

    expect(result).toEqual({
      facts: ["Jane lives in Portland", "Jane is an engineer"],
      found: true,
    });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith(GRAPH_UUID, {
      body: { query: "where does Jane live" },
    });
  });

  it("honors a pinned scope and limit", async () => {
    const zep = makeFakeZep();
    zep.graph.searchNodes.mockResolvedValueOnce({
      data: [{ name: "Apollo", summary: "A spacecraft program" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      scope: "nodes",
      limit: 5,
    });

    const result = await run(tool, { query: "Apollo" });

    expect(result.facts).toEqual(["Apollo: A spacecraft program"]);
    expect(zep.graph.searchNodes).toHaveBeenCalledWith(GRAPH_UUID, {
      limit: 5,
      body: { query: "Apollo" },
    });
  });

  it("searches observations and thread summaries with the scope method", async () => {
    const zep = makeFakeZep();
    zep.graph.searchObservations.mockResolvedValueOnce({
      data: [{ name: "Habit", summary: "Jane hikes on Sundays" }],
    });
    zep.graph.searchThreadSummaries.mockResolvedValueOnce({
      data: [{ summary: "Jane described her move to Portland." }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });

    const observations = await run(tool, { query: "habits", scope: "observations" });
    expect(observations.facts).toEqual(["Habit: Jane hikes on Sundays"]);

    const summaries = await run(tool, { query: "moves", scope: "thread_summaries" });
    expect(summaries.facts).toEqual(["Jane described her move to Portland."]);
  });

  it("maps the auto scope onto graph.getContext", async () => {
    const zep = makeFakeZep();
    zep.graph.getContext.mockResolvedValueOnce({ context: "  CONTEXT BLOCK  " });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });

    const result = await run(tool, { query: "everything", scope: "auto" });

    expect(result.facts).toEqual(["CONTEXT BLOCK"]);
    expect(zep.graph.getContext).toHaveBeenCalledWith(GRAPH_UUID, { query: "everything" });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockRejectedValueOnce(new Error("timeout"));
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
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
    expect(zep.graph.searchEdges).not.toHaveBeenCalled();
  });
});

describe("createZepSearchTool pin-or-expose", () => {
  it("exposes scope/reranker/limit/mmrLambda/centerNodeUuid by default", () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    const keys = Object.keys(schema.shape);
    expect(keys).toEqual(
      expect.arrayContaining(["query", "scope", "reranker", "limit", "mmrLambda", "centerNodeUuid"]),
    );
  });

  it("six scopes accepted in the exposed schema", () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });
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
    zep.graph.searchNodes.mockResolvedValueOnce({ data: [{ name: "Apollo" }] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      pinnedParams: { scope: "nodes", limit: 5 },
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).not.toContain("scope");
    expect(Object.keys(schema.shape)).not.toContain("limit");

    await run(tool, { query: "Apollo" });
    expect(zep.graph.searchNodes).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ limit: 5 }),
    );
  });

  it("hidden params are omitted from the schema and the SDK call", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      hiddenParams: ["mmrLambda", "centerNodeUuid"],
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).not.toContain("mmrLambda");
    expect(Object.keys(schema.shape)).not.toContain("centerNodeUuid");

    await run(tool, { query: "x" });
    const body = zep.graph.searchEdges.mock.calls[0]![1].body as Record<string, unknown>;
    expect(body).not.toHaveProperty("mmrLambda");
    expect(body).not.toHaveProperty("centerNodeUuid");
  });

  it("query-only call omits unset optional params from the search body", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });
    await run(tool, { query: "hello" });
    const body = zep.graph.searchEdges.mock.calls[0]![1].body as Record<string, unknown>;
    // mmrLambda/centerNodeUuid must never be sent as null/undefined.
    expect(body).not.toHaveProperty("mmrLambda");
    expect(body).not.toHaveProperty("centerNodeUuid");
    expect(Object.values(body).every((v) => v !== null && v !== undefined)).toBe(true);
  });

  it("legacy option args (scope/reranker/limit) pin the corresponding parameter", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      scope: "episodes",
      limit: 7,
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).not.toContain("scope");
    expect(Object.keys(schema.shape)).not.toContain("limit");

    await run(tool, { query: "x" });
    expect(zep.graph.searchEpisodes).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ limit: 7 }),
    );
  });

  it("model-supplied exposed params are forwarded in the search body", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });
    await run(tool, { query: "x", scope: "nodes", reranker: "mmr", mmrLambda: 0.5 });
    expect(zep.graph.searchNodes).toHaveBeenCalledWith(GRAPH_UUID, {
      body: { query: "x", reranker: "mmr", mmrLambda: 0.5 },
    });
  });

  it("sends constructor-only filters and bfsOriginNodeUuids in the body", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      filters: { nodeLabels: ["Person"] },
      bfsOriginNodeUuids: ["44444444-4444-4444-8444-444444444444"],
    });
    await run(tool, { query: "x" });
    const body = zep.graph.searchEdges.mock.calls[0]![1].body as Record<string, unknown>;
    expect(body.filters).toEqual({ nodeLabels: ["Person"] });
    expect(body.bfsOriginNodeUuids).toEqual(["44444444-4444-4444-8444-444444444444"]);
  });
});

describe("createZepSearchTool sanitization", () => {
  it("drops a pinned reranker incompatible with pinned scope 'auto' and warns at construction", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      pinnedParams: { scope: "auto", reranker: "node_distance" },
      logger: { warn },
    });
    expect(warn).toHaveBeenCalledOnce();

    await run(tool, { query: "x" });
    const request = zep.graph.getContext.mock.calls[0]![1] as Record<string, unknown>;
    expect(request).not.toHaveProperty("reranker");
  });

  it("drops a model-provided reranker incompatible with scope 'auto' at execute", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      logger: { warn },
    });

    await run(tool, { query: "x", scope: "auto", reranker: "episode_mentions" });
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.graph.getContext).toHaveBeenCalledWith(GRAPH_UUID, { query: "x" });
  });

  it("clamps a pinned limit above Zep's ceiling to 50 and warns at construction", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
      pinnedParams: { limit: 200 },
      logger: { warn },
    });
    expect(warn).toHaveBeenCalledOnce();

    await run(tool, { query: "x" });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ limit: 50 }),
    );
  });

  it("clamps a model-provided limit above Zep's ceiling to 50 at execute", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });

    await run(tool, { query: "x", limit: 200 });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ limit: 50 }),
    );
  });
});

describe("createZepRememberTool", () => {
  it("persists conversational messages via thread.addMessages", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
      defaultMessageName: "Jane",
    });

    const result = await run(tool, { content: "I live in Portland", role: "user" });

    expect(result.stored).toBe(true);
    expect(zep.thread.addMessages).toHaveBeenCalledTimes(1);
    const [threadUuid, req] = zep.thread.addMessages.mock.calls[0]!;
    expect(threadUuid).toBe(THREAD_UUID);
    expect(req.messages[0]).toMatchObject({
      role: "user",
      content: "I live in Portland",
      name: "Jane",
    });
    expect(zep.graph.episode.add).not.toHaveBeenCalled();
  });

  it("persists non-conversational data via graph.episode.add", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
    });

    const result = await run(tool, { content: "Project Apollo ships Q3" });

    expect(result.stored).toBe(true);
    expect(zep.graph.episode.add).toHaveBeenCalledWith(GRAPH_UUID, {
      type: "text",
      data: "Project Apollo ships Q3",
    });
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("routes to graph.episode.add when no thread is bound", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID },
    });

    await run(tool, { content: "Refunds take 5 days", role: "assistant" });
    expect(zep.graph.episode.add).toHaveBeenCalledWith(GRAPH_UUID, {
      type: "text",
      data: "Refunds take 5 days",
    });
  });

  it("maps unknown roles to a valid Zep RoleType", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
    });
    await run(tool, { content: "hi", role: "Human" });
    expect(zep.thread.addMessages.mock.calls[0]![1].messages[0].role).toBe("user");
  });

  it("omits the role when the host role has no Zep role", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
      logger: { warn: vi.fn(), debug: vi.fn() },
    });
    await run(tool, { content: "hi", role: "wizard" });
    expect(zep.thread.addMessages.mock.calls[0]![1].messages[0]).not.toHaveProperty("role");
  });

  it("truncates over-long episode data to the 10,000-char limit and warns (lengths only)", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID }, // no thread + no role → episode path
      logger: { warn },
    });

    // 12,000 chars — over Zep's 10,000-char episode limit.
    const result = await run(tool, { content: "z".repeat(12000) });

    expect(result.stored).toBe(true);
    const data = zep.graph.episode.add.mock.calls[0]![1].data as string;
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
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
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
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
    });
    const result = await run(tool, { content: "   " });
    expect(result.stored).toBe(false);
    expect(zep.graph.episode.add).not.toHaveBeenCalled();
    expect(zep.thread.addMessages).not.toHaveBeenCalled();
  });

  it("never throws when Zep fails — logs and returns stored: false", async () => {
    const zep = makeFakeZep();
    zep.graph.episode.add.mockRejectedValueOnce(new Error("503 upstream"));
    const warn = vi.fn();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
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
  it("retrieves the context block via thread.getContext", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockResolvedValueOnce({ context: "FACTS: Jane is in Portland" });
    const tool = createZepContextTool({ client: asZep(zep), threadUuid: THREAD_UUID });

    const result = await run(tool, {});

    expect(result).toEqual({ context: "FACTS: Jane is in Portland", found: true });
    expect(zep.thread.getContext).toHaveBeenCalledWith(THREAD_UUID, {});
  });

  it("passes a templateUuid when provided", async () => {
    const zep = makeFakeZep();
    const tool = createZepContextTool({
      client: asZep(zep),
      threadUuid: THREAD_UUID,
      templateUuid: "tmpl-9",
    });
    await run(tool, {});
    expect(zep.thread.getContext).toHaveBeenCalledWith(THREAD_UUID, {
      templateUuid: "tmpl-9",
    });
  });

  it("returns found: false on an empty context", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockResolvedValueOnce({ context: "   " });
    const tool = createZepContextTool({ client: asZep(zep), threadUuid: THREAD_UUID });
    const result = await run(tool, {});
    expect(result).toEqual({ context: "", found: false });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockRejectedValueOnce(new Error("boom"));
    const warn = vi.fn();
    const tool = createZepContextTool({
      client: asZep(zep),
      threadUuid: THREAD_UUID,
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
    zep.graph.searchEpisodes.mockResolvedValueOnce({ data: [{ content: "raw text" }] });
    const { zepSearch, zepRemember, zepContext } = createZepTools(asZep(zep), {
      binding: { graphUuid: GRAPH_UUID, threadUuid: THREAD_UUID },
      searchScope: "episodes",
      searchLimit: 3,
    });

    expect(typeof zepRemember.execute).toBe("function");
    expect(typeof zepContext.execute).toBe("function");

    const result = await run(zepSearch, { query: "q" });
    expect(result.facts).toEqual(["raw text"]);
    expect(zep.graph.searchEpisodes).toHaveBeenCalledWith(GRAPH_UUID, {
      limit: 3,
      body: { query: "q" },
    });
  });
});
