import { describe, it, expect, vi } from "vitest";
import {
  createZepContextTool,
  createZepRememberTool,
  createZepSearchTool,
  createZepTools,
} from "../src/index.js";
import { asZep, makeFakeZep, run } from "./helpers.js";

describe("createZepSearchTool", () => {
  it("searches edges by default and formats V4 page data", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce({
      data: [{ fact: "Jane lives in Portland" }, { fact: "Jane likes hiking" }],
    });
    const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
    const result = await run(tool, { query: "where does Jane live" });
    expect(result.facts).toEqual(["Jane lives in Portland", "Jane likes hiking"]);
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", {
      query: "where does Jane live",
    });
  });

  it.each([
    ["edges", "searchEdges", { fact: "fact" }, "fact"],
    ["nodes", "searchNodes", { name: "Jane", summary: "A person" }, "Jane: A person"],
    ["episodes", "searchEpisodes", { content: "Raw episode" }, "Raw episode"],
    [
      "observations",
      "searchObservations",
      { name: "Preference", summary: "Likes tea" },
      "Preference: Likes tea",
    ],
    ["thread_summaries", "searchThreadSummaries", { summary: "A short summary" }, "A short summary"],
  ] as const)("routes %s to the V4 scoped method and formats its result", async (scope, method, item, fact) => {
    const zep = makeFakeZep();
    const search = zep.graph[method] as ReturnType<typeof vi.fn>;
    search.mockResolvedValueOnce({ data: [item] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      scope,
    });
    const result = await run(tool, { query: "lookup" });
    expect(result.facts).toEqual([fact]);
    expect(search).toHaveBeenCalledWith("g1", { query: "lookup" });
  });

  it("uses graph.getContext for auto scope", async () => {
    const zep = makeFakeZep();
    zep.graph.getContext.mockResolvedValueOnce({ context: "Relevant context" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      scope: "auto",
    });
    expect((await run(tool, { query: "lookup" })).facts).toEqual(["Relevant context"]);
    expect(zep.graph.getContext).toHaveBeenCalledWith("g1", { query: "lookup" });
    expect(zep.graph.searchEdges).not.toHaveBeenCalled();
  });

  it("exposes scope and search controls in the model schema by default", () => {
    const tool = createZepSearchTool({ client: asZep(makeFakeZep()), binding: { graphUuid: "g1" } });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).toEqual([
      "query",
      "scope",
      "reranker",
      "limit",
      "mmrLambda",
      "centerNodeUuid",
    ]);
  });

  it("accepts all six scopes in the exposed schema", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
    for (const scope of [
      "edges",
      "nodes",
      "episodes",
      "observations",
      "thread_summaries",
      "auto",
    ]) {
      await run(tool, { query: "lookup", scope });
    }
    expect(zep.graph.searchEdges).toHaveBeenCalledOnce();
    expect(zep.graph.searchNodes).toHaveBeenCalledOnce();
    expect(zep.graph.searchEpisodes).toHaveBeenCalledOnce();
    expect(zep.graph.searchObservations).toHaveBeenCalledOnce();
    expect(zep.graph.searchThreadSummaries).toHaveBeenCalledOnce();
    expect(zep.graph.getContext).toHaveBeenCalledOnce();
  });

  it("pins parameters, hides them from the schema, and sends the pinned values", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      pinnedParams: { scope: "nodes", limit: 5, reranker: "rrf" },
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).toEqual(["query", "mmrLambda", "centerNodeUuid"]);
    await run(tool, { query: "lookup", scope: "episodes", limit: 2 });
    expect(zep.graph.searchNodes).toHaveBeenCalledWith("g1", {
      query: "lookup",
      pageSize: 5,
      reranker: "rrf",
    });
  });

  it("hides unpinned parameters from both the schema and request", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      hiddenParams: ["scope", "reranker", "limit"],
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).toEqual(["query", "mmrLambda", "centerNodeUuid"]);
    await run(tool, { query: "lookup", scope: "nodes", reranker: "mmr", limit: 4 });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", { query: "lookup" });
  });

  it("omits unset optional parameters and forwards exposed V4 parameters", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
    await run(tool, { query: "lookup" });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", { query: "lookup" });
    await run(tool, {
      query: "lookup",
      scope: "nodes",
      limit: 3,
      reranker: "mmr",
      mmrLambda: 0.5,
      centerNodeUuid: "node-1",
    });
    expect(zep.graph.searchNodes).toHaveBeenCalledWith("g1", {
      query: "lookup",
      pageSize: 3,
      reranker: "mmr",
      mmrLambda: 0.5,
      centerNodeUuid: "node-1",
    });
  });

  it("keeps the legacy scope, reranker, and limit options as pinned aliases", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      scope: "nodes",
      reranker: "mmr",
      limit: 4,
    });
    const schema = tool.inputSchema as unknown as { shape: Record<string, unknown> };
    expect(Object.keys(schema.shape)).toEqual(["query", "mmrLambda", "centerNodeUuid"]);
    await run(tool, { query: "lookup" });
    expect(zep.graph.searchNodes).toHaveBeenCalledWith("g1", {
      query: "lookup",
      pageSize: 4,
      reranker: "mmr",
    });
  });

  it("passes constructor filters and BFS origins to scoped search only", async () => {
    const zep = makeFakeZep();
    const filters = { nodeLabels: ["Person"] };
    const bfsOriginNodeUuids = ["node-1"];
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      filters,
      bfsOriginNodeUuids,
    });
    await run(tool, { query: "lookup" });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", {
      query: "lookup",
      filters,
      bfsOriginNodeUuids,
    });
  });

  it("sends only supported query and filters for auto scope", async () => {
    const zep = makeFakeZep();
    const filters = { nodeLabels: ["Person"] };
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      scope: "auto",
      filters,
      bfsOriginNodeUuids: ["node-1"],
    });
    await run(tool, { query: "lookup", limit: 10, reranker: "mmr" });
    expect(zep.graph.getContext).toHaveBeenCalledWith("g1", { query: "lookup", filters });
  });

  it("clamps a pinned limit to the V4 maximum", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      limit: 100,
      logger: { warn },
    });
    await run(tool, { query: "lookup" });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", {
      query: "lookup",
      pageSize: 50,
    });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("clamps a model-provided limit", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      logger: { warn },
    });
    await run(tool, { query: "lookup", limit: 100 });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", {
      query: "lookup",
      pageSize: 50,
    });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns empty results and never throws when search fails or is unbound", async () => {
    const zep = makeFakeZep();
    const warn = vi.fn();
    zep.graph.searchEdges.mockRejectedValueOnce(new Error("timeout"));
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      logger: { warn },
    });
    expect(await run(tool, { query: "lookup" })).toEqual({ facts: [], found: false });
    const unbound = createZepSearchTool({ client: asZep(zep), binding: {} });
    expect(await run(unbound, { query: "lookup" })).toEqual({ facts: [], found: false });
    expect(await run(unbound, { query: "  " })).toEqual({ facts: [], found: false });
    expect(warn).toHaveBeenCalledOnce();
  });
});

describe("createZepRememberTool", () => {
  it("persists conversational messages through the bound thread", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: "g1", threadUuid: "t1" },
      defaultMessageName: "Jane",
    });
    expect(await run(tool, { content: "Hello", role: "user" })).toMatchObject({ stored: true });
    expect(zep.thread.addMessages).toHaveBeenCalledWith("t1", {
      messages: [{ role: "user", content: "Hello", name: "Jane" }],
    });
    expect(zep.graph.episode.add).not.toHaveBeenCalled();
  });

  it("persists facts and standalone graph data through graph.episode.add", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: "kb-1" },
    });
    expect(await run(tool, { content: "The launch is in June." })).toMatchObject({ stored: true });
    expect(zep.graph.episode.add).toHaveBeenCalledWith("kb-1", {
      type: "text",
      data: "The launch is in June.",
    });
  });

  it("omits an unknown role instead of sending a V3-only role", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: "g1", threadUuid: "t1" },
    });
    await run(tool, { content: "Remember this.", role: "wizard" });
    expect(zep.thread.addMessages).toHaveBeenCalledWith("t1", {
      messages: [{ content: "Remember this." }],
    });
  });

  it("truncates long messages and graph episodes", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: "g1", threadUuid: "t1" },
    });
    await run(tool, { content: "x".repeat(5000), role: "user" });
    expect(zep.thread.addMessages.mock.calls[0]![1].messages[0].content).toHaveLength(4000);
    const graphTool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });
    await run(graphTool, { content: "y".repeat(12000) });
    expect(zep.graph.episode.add.mock.calls[0]![1].data).toHaveLength(9900);
  });

  it("returns stored false for empty input, missing bindings, and Zep errors", async () => {
    const zep = makeFakeZep();
    const tool = createZepRememberTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });
    expect(await run(tool, { content: " " })).toMatchObject({ stored: false });
    expect(zep.graph.episode.add).not.toHaveBeenCalled();
    zep.graph.episode.add.mockRejectedValueOnce(new Error("503 upstream"));
    expect(await run(tool, { content: "data" })).toMatchObject({ stored: false });
    const unbound = createZepRememberTool({ client: asZep(zep), binding: {} });
    expect(await run(unbound, { content: "data" })).toMatchObject({ stored: false });
  });
});

describe("createZepContextTool", () => {
  it("retrieves context and passes a template UUID", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockResolvedValueOnce({ context: "  Context Block  " });
    const tool = createZepContextTool({
      client: asZep(zep),
      threadUuid: "t1",
      templateUuid: "template-1",
    });
    expect((await run(tool, {})).context).toBe("Context Block");
    expect(zep.thread.getContext).toHaveBeenCalledWith("t1", { templateUuid: "template-1" });
  });

  it("returns found false for empty context or a Zep failure", async () => {
    const zep = makeFakeZep();
    zep.thread.getContext.mockResolvedValueOnce({ context: "   " });
    const tool = createZepContextTool({ client: asZep(zep), threadUuid: "t1" });
    expect(await run(tool, {})).toEqual({ context: "", found: false });
    zep.thread.getContext.mockRejectedValueOnce(new Error("down"));
    expect(await run(tool, {})).toEqual({ context: "", found: false });
  });

  it("returns found false when no thread is bound", async () => {
    const zep = makeFakeZep();
    const tool = createZepContextTool({ client: asZep(zep), threadUuid: "" });
    expect(await run(tool, {})).toEqual({ context: "", found: false });
    expect(zep.thread.getContext).not.toHaveBeenCalled();
  });
});

describe("createZepTools", () => {
  it("returns all three tools with UUID binding and configured search scope", async () => {
    const zep = makeFakeZep();
    const tools = createZepTools(asZep(zep), {
      binding: { graphUuid: "g1", threadUuid: "t1" },
      searchScope: "episodes",
      searchLimit: 4,
      templateUuid: "template-1",
    });
    expect(Object.keys(tools)).toEqual(["zepSearch", "zepRemember", "zepContext"]);
    await run(tools.zepSearch, { query: "raw text" });
    expect(zep.graph.searchEpisodes).toHaveBeenCalledWith("g1", {
      query: "raw text",
      pageSize: 4,
    });
    await run(tools.zepContext, {});
    expect(zep.thread.getContext).toHaveBeenCalledWith("t1", { templateUuid: "template-1" });
  });
});
