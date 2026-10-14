import { describe, it, expect, vi } from "vitest";
import { createZepSearchTool } from "../src/index.js";
import { asZep, makeFakeZep, run } from "./helpers.js";

describe("createZepSearchTool", () => {
  it("searches edges by graph UUID and formats facts", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce({
      data: [{ fact: "Jane lives in Portland" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
    });

    const result = await run(tool, { query: "Where does Jane live?" });

    expect(result).toEqual({ facts: ["Jane lives in Portland"], found: true });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("graph-1", {
      query: "Where does Jane live?",
      pageSize: 10,
      reranker: "rrf",
    });
  });

  it("uses graph.getContext for auto scope and omits scoped-only options", async () => {
    const zep = makeFakeZep();
    zep.graph.getContext.mockResolvedValueOnce({ context: "  assembled context  " });
    const filters = { nodeLabels: ["Person"] };
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      filters,
      bfsOriginNodeUuids: ["node-1"],
    });

    const result = await run(tool, { query: "Jane", scope: "auto" });

    expect(result).toEqual({ facts: ["assembled context"], found: true });
    expect(zep.graph.getContext).toHaveBeenCalledWith("graph-1", {
      query: "Jane",
      filters,
    });
    expect(zep.graph.searchEdges).not.toHaveBeenCalled();
  });

  it.each([
    ["edges", "searchEdges", { fact: "a fact" }, "a fact"],
    ["nodes", "searchNodes", { name: "Jane", summary: "Lives in Portland" }, "Jane: Lives in Portland"],
    ["episodes", "searchEpisodes", { content: "Jane said hello" }, "Jane said hello"],
    ["observations", "searchObservations", { name: "Routine", summary: "Runs each day" }, "Routine: Runs each day"],
    ["thread_summaries", "searchThreadSummaries", { name: "Trip", summary: "Visited Rome" }, "Trip: Visited Rome"],
  ] as const)("routes %s to the V4 scope method", async (scope, method, item, expected) => {
    const zep = makeFakeZep();
    const clientGraph = zep.graph as unknown as Record<string, ReturnType<typeof vi.fn>>;
    clientGraph[method]!.mockResolvedValueOnce({ data: [item] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
    });

    const result = await run(tool, { query: "query", scope });

    expect(result).toEqual({ facts: [expected], found: true });
    expect(clientGraph[method]).toHaveBeenCalledWith(
      "graph-1",
      expect.objectContaining({ query: "query", pageSize: 10, reranker: "rrf" }),
    );
  });

  it("formats observations that have only a name or a summary", async () => {
    const zep = makeFakeZep();
    zep.graph.searchObservations.mockResolvedValueOnce({
      data: [{ name: "Routine" }, { summary: "Runs each morning" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
    });

    const result = await run(tool, { query: "routine", scope: "observations" });

    expect(result).toEqual({
      facts: ["Routine", "Runs each morning"],
      found: true,
    });
  });

  it("exposes all six search scopes and five rerankers", () => {
    const tool = createZepSearchTool({
      client: asZep(makeFakeZep()),
      binding: { graphUuid: "graph-1" },
    });
    const shape = (
      tool.inputSchema as unknown as {
        shape: Record<string, { unwrap: () => { options: readonly string[] } }>;
      }
    ).shape;
    expect([...shape.scope!.unwrap().options].sort()).toEqual(
      ["auto", "edges", "episodes", "nodes", "observations", "thread_summaries"].sort(),
    );
    expect([...shape.reranker!.unwrap().options].sort()).toEqual(
      ["cross_encoder", "episode_mentions", "mmr", "node_distance", "rrf"].sort(),
    );
    expect(Object.keys(shape).sort()).toEqual(
      ["centerNodeUuid", "limit", "mmrLambda", "query", "reranker", "scope"].sort(),
    );
  });

  it("pins parameters and hides them from the schema", async () => {
    const zep = makeFakeZep();
    zep.graph.searchNodes.mockResolvedValueOnce({ data: [{ name: "Apollo" }] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      pinnedParams: { scope: "nodes", limit: 3 },
    });
    const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;

    const result = await run(tool, { query: "project", scope: "edges", limit: 99 } as never);

    expect(shape.scope).toBeUndefined();
    expect(shape.limit).toBeUndefined();
    expect(result.facts).toEqual(["Apollo"]);
    expect(zep.graph.searchNodes).toHaveBeenCalledWith("graph-1", {
      query: "project",
      pageSize: 3,
      reranker: "rrf",
    });
  });

  it("always sends pinned reranker and optional search parameters", async () => {
    const zep = makeFakeZep();
    zep.graph.searchNodes.mockResolvedValueOnce({ data: [{ name: "Apollo" }] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      pinnedParams: {
        scope: "nodes",
        reranker: "mmr",
        limit: 4,
        mmrLambda: 0.4,
        centerNodeUuid: "node-1",
      },
    });
    const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;

    await run(tool, { query: "Apollo" });

    expect(shape.scope).toBeUndefined();
    expect(shape.reranker).toBeUndefined();
    expect(shape.limit).toBeUndefined();
    expect(shape.mmrLambda).toBeUndefined();
    expect(shape.centerNodeUuid).toBeUndefined();
    expect(zep.graph.searchNodes).toHaveBeenCalledWith("graph-1", {
      query: "Apollo",
      pageSize: 4,
      reranker: "mmr",
      mmrLambda: 0.4,
      centerNodeUuid: "node-1",
    });
  });

  it("omits hidden optional parameters from the request", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      hiddenParams: new Set(["reranker", "limit", "mmrLambda", "centerNodeUuid"]),
    });

    await run(tool, { query: "query" });

    expect(zep.graph.searchEdges).toHaveBeenCalledWith("graph-1", { query: "query" });
  });

  it("hides scope when requested and sends no hidden defaults", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      hiddenParams: new Set(["scope", "reranker", "limit"]),
    });
    const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;

    await run(tool, { query: "query" });

    expect(shape.scope).toBeUndefined();
    expect(shape.reranker).toBeUndefined();
    expect(shape.limit).toBeUndefined();
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("graph-1", { query: "query" });
  });

  it("treats the legacy scope, reranker, and limit options as pinned values", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      scope: "episodes",
      reranker: "mmr",
      limit: 5,
    });
    const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;

    await run(tool, { query: "query" });

    expect(shape.scope).toBeUndefined();
    expect(shape.reranker).toBeUndefined();
    expect(shape.limit).toBeUndefined();
    expect(zep.graph.searchEpisodes).toHaveBeenCalledWith("graph-1", {
      query: "query",
      pageSize: 5,
      reranker: "mmr",
    });
  });

  it("applies constructor filters and BFS origins to scoped search only", async () => {
    const zep = makeFakeZep();
    const filters = { nodeLabels: ["Person"] };
    const bfsOriginNodeUuids = ["node-1"];
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      filters,
      bfsOriginNodeUuids,
    });

    await run(tool, { query: "query" });

    expect(zep.graph.searchEdges).toHaveBeenCalledWith(
      "graph-1",
      expect.objectContaining({ filters, bfsOriginNodeUuids }),
    );
    expect(
      (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape.filters,
    ).toBeUndefined();
    expect(
      (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape
        .bfsOriginNodeUuids,
    ).toBeUndefined();
  });

  it("clamps a model-provided limit to the 50-result ceiling", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
    });

    await run(tool, { query: "query", limit: 200 });

    expect(zep.graph.searchEdges).toHaveBeenCalledWith(
      "graph-1",
      expect.objectContaining({ pageSize: 50 }),
    );
  });

  it("clamps a pinned limit and warns during construction", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      pinnedParams: { limit: 200 },
      logger: { warn },
    });

    expect(warn).toHaveBeenCalledOnce();
    await run(tool, { query: "query" });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith(
      "graph-1",
      expect.objectContaining({ pageSize: 50 }),
    );
  });

  it("omits an incompatible model-provided reranker for auto search", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      logger: { warn },
    });

    await run(tool, { query: "query", scope: "auto", reranker: "node_distance" });

    expect(zep.graph.getContext).toHaveBeenCalledWith("graph-1", { query: "query" });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("omits a pinned incompatible reranker for auto search", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      pinnedParams: { scope: "auto", reranker: "episode_mentions" },
      logger: { warn },
    });

    expect(warn).toHaveBeenCalledOnce();
    await run(tool, { query: "query" });

    expect(zep.graph.getContext).toHaveBeenCalledWith("graph-1", { query: "query" });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("forwards model-provided scope and search parameters", async () => {
    const zep = makeFakeZep();
    zep.graph.searchNodes.mockResolvedValueOnce({ data: [{ name: "Apollo" }] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
    });

    await run(tool, {
      query: "Apollo",
      scope: "nodes",
      reranker: "mmr",
      limit: 7,
      mmrLambda: 0.5,
      centerNodeUuid: "node-9",
    });

    expect(zep.graph.searchNodes).toHaveBeenCalledWith("graph-1", {
      query: "Apollo",
      pageSize: 7,
      reranker: "mmr",
      mmrLambda: 0.5,
      centerNodeUuid: "node-9",
    });
  });

  it("returns an empty result when no memory matches", async () => {
    const tool = createZepSearchTool({
      client: asZep(makeFakeZep()),
      binding: { graphUuid: "graph-1" },
    });
    expect(await run(tool, { query: "query" })).toEqual({ facts: [], found: false });
  });

  it("returns an empty result when no graph is bound", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: {},
      logger: { warn },
    });

    expect(await run(tool, { query: "query" })).toEqual({ facts: [], found: false });
    expect(warn).toHaveBeenCalledOnce();
    expect(zep.graph.searchEdges).not.toHaveBeenCalled();
  });

  it("uses the graph UUID resolved from requestContext", async () => {
    const zep = makeFakeZep();
    const resolveIdentity = vi.fn().mockReturnValue({ graphUuid: "graph-2" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      resolveIdentity,
    });

    await run(tool, { query: "query" }, { requestContext: { tenant: "acme" } });

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith(
      "graph-2",
      expect.any(Object),
    );
  });

  it("awaits an asynchronous identity resolver", async () => {
    const zep = makeFakeZep();
    const resolveIdentity = vi.fn().mockResolvedValue({ graphUuid: "graph-async" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      resolveIdentity,
    });
    const requestContext = { tenant: "acme" };

    await run(tool, { query: "query" }, { requestContext });

    expect(resolveIdentity).toHaveBeenCalledWith(requestContext);
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("graph-async", expect.any(Object));
  });

  it("falls back to the constructor graph UUID when the resolver omits it", async () => {
    const zep = makeFakeZep();
    const resolveIdentity = vi.fn().mockReturnValue({ threadUuid: "thread-2" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      resolveIdentity,
    });

    await run(tool, { query: "query" });

    expect(zep.graph.searchEdges).toHaveBeenCalledWith("graph-1", expect.any(Object));
  });

  it("returns an empty result when search fails", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockRejectedValueOnce(new Error("timeout"));
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "graph-1" },
      logger: { warn },
    });

    expect(await run(tool, { query: "query" })).toEqual({ facts: [], found: false });
    expect(warn).toHaveBeenCalledOnce();
  });
});
