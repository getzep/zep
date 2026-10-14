import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import { Type } from "@google/genai";
import {
  DEFAULT_MMR_LAMBDA,
  ZepGraphSearchTool,
} from "../src/graph-search-tool.js";
import {
  fakeContext,
  capturingLogger,
  mockZepClient,
  silentLogger,
  MOCK_GRAPH_UUID,
  MOCK_USER_UUID,
} from "./helpers.js";

const GRAPH_UUID = "44444444-4444-4444-4444-444444444444";

function callTool(
  tool: ZepGraphSearchTool,
  args: Record<string, unknown>,
  ctx = fakeContext({ userId: MOCK_USER_UUID }),
) {
  return tool.runAsync({ args, toolContext: ctx as unknown as Context });
}

describe("ZepGraphSearchTool — default declaration", () => {
  it("default declaration exposes query + all five exposable params with the six-value scope enum", () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    const decl = tool._getDeclaration();
    expect(decl.name).toBe("zep_graph_search");
    expect(decl.parameters?.required).toEqual(["query"]);

    const props = decl.parameters?.properties ?? {};
    expect(Object.keys(props).sort()).toEqual(
      [
        "query",
        "scope",
        "reranker",
        "limit",
        "mmrLambda",
        "centerNodeUuid",
      ].sort(),
    );

    expect(props.query?.type).toBe(Type.STRING);

    expect(props.scope?.type).toBe(Type.STRING);
    expect(props.scope?.enum).toEqual([
      "edges",
      "nodes",
      "episodes",
      "observations",
      "thread_summaries",
      "auto",
    ]);

    expect(props.reranker?.type).toBe(Type.STRING);
    expect(props.reranker?.enum).toEqual([
      "rrf",
      "mmr",
      "node_distance",
      "cross_encoder",
    ]);

    expect(props.limit?.type).toBe(Type.INTEGER);
    expect(props.mmrLambda?.type).toBe(Type.NUMBER);
    expect(props.centerNodeUuid?.type).toBe(Type.STRING);
  });
});

describe("ZepGraphSearchTool — pin behavior", () => {
  it("pinning scope+reranker+limit hides them from declaration (old-behavior recipe)", () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      scope: "edges",
      reranker: "rrf",
      limit: 10,
      mmrLambda: null,
      centerNodeUuid: null,
      logger: silentLogger,
    });
    const decl = tool._getDeclaration();
    expect(decl.parameters?.required).toEqual(["query"]);
    expect(Object.keys(decl.parameters?.properties ?? {})).toEqual(["query"]);
  });

  it("pinned values are used in the SDK call even if the model passes others", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "edges",
      reranker: "rrf",
      limit: 10,
      logger: silentLogger,
    });

    await callTool(tool, {
      query: "q",
      scope: "nodes",
      reranker: "mmr",
      limit: 99,
    });

    expect(mocks.searchNodes).not.toHaveBeenCalled();
    expect(mocks.searchEdges).toHaveBeenCalledWith(GRAPH_UUID, {
      pageSize: 10,
      query: "q",
      reranker: "rrf",
    });
  });
});

describe("ZepGraphSearchTool — hidden (null) params", () => {
  it("hidden (null) mmrLambda/centerNodeUuid are absent from declaration", () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      mmrLambda: null,
      centerNodeUuid: null,
      logger: silentLogger,
    });
    const props = tool._getDeclaration().parameters?.properties ?? {};
    expect(props.mmrLambda).toBeUndefined();
    expect(props.centerNodeUuid).toBeUndefined();
    // Still exposed
    expect(props.scope).toBeDefined();
    expect(props.reranker).toBeDefined();
    expect(props.limit).toBeDefined();
  });

  it("hidden (null) mmrLambda/centerNodeUuid are absent from the SDK call payload", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      mmrLambda: null,
      centerNodeUuid: null,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    const call = mocks.searchEdges.mock.calls[0]?.[1] as Record<
      string,
      unknown
    >;
    expect(call).not.toHaveProperty("mmrLambda");
    expect(call).not.toHaveProperty("centerNodeUuid");
  });
});

describe("ZepGraphSearchTool — merge precedence", () => {
  it("pinned beats model-provided arg", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      limit: 3,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q", limit: 50 });

    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ pageSize: 3 }),
    );
  });

  it("model-provided arg beats default when not pinned", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q", limit: 25, scope: "nodes" });

    expect(mocks.searchNodes).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ pageSize: 25 }),
    );
    expect(mocks.searchEdges).not.toHaveBeenCalled();
  });

  it("default is used when the param is neither pinned nor model-provided", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    expect(mocks.searchEdges).toHaveBeenCalledWith(GRAPH_UUID, {
      pageSize: 10,
      query: "q",
      reranker: "rrf",
    });
  });

  it("invalid model enum value falls back to default and logs a warning", async () => {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger,
    });

    await callTool(tool, {
      query: "q",
      scope: "bogus",
      reranker: "not-a-reranker",
    });

    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ reranker: "rrf" }),
    );
    expect(logger.warns.length).toBeGreaterThan(0);
  });
});

describe("ZepGraphSearchTool — constructor-only params", () => {
  it("searchFilters is passed through as the v4 filters", async () => {
    const { client, mocks } = mockZepClient();
    const searchFilters = { nodeLabels: ["Person"] };
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      searchFilters,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ filters: searchFilters }),
    );
  });

  it("bfsOriginNodeUuids is passed through in the v4 request", async () => {
    const { client, mocks } = mockZepClient();
    const bfsOriginNodeUuids = ["uuid-1", "uuid-2"];
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      bfsOriginNodeUuids,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ bfsOriginNodeUuids }),
    );
  });

  it("searchFilters/bfsOriginNodeUuids never appear in the model declaration", () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      searchFilters: { nodeLabels: ["Person"] },
      bfsOriginNodeUuids: ["uuid-1"],
      logger: silentLogger,
    });
    const props = tool._getDeclaration().parameters?.properties ?? {};
    expect(props.searchFilters).toBeUndefined();
    expect(props.bfsOriginNodeUuids).toBeUndefined();
  });
});

describe("ZepGraphSearchTool — target resolution", () => {
  it("reads the graph UUID of the user and formats edge facts", async () => {
    const { client, mocks } = mockZepClient({
      searchData: [
        { fact: "Alice lives in Portland." },
        { fact: "Alice hikes." },
      ],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userUuid: MOCK_USER_UUID,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "where does alice live" });

    expect(mocks.userGet).toHaveBeenCalledWith(MOCK_USER_UUID);
    expect(mocks.searchEdges).toHaveBeenCalledWith(MOCK_GRAPH_UUID, {
      pageSize: 10,
      query: "where does alice live",
      reranker: "rrf",
    });
    expect(out).toBe("- Alice lives in Portland.\n- Alice hikes.");
  });

  it("reads the graph UUID of a user only once", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userUuid: MOCK_USER_UUID,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });
    await callTool(tool, { query: "q2" });

    expect(mocks.userGet).toHaveBeenCalledTimes(1);
    expect(mocks.searchEdges).toHaveBeenCalledTimes(2);
  });

  it("searches a fixed graph when graphUuid is set, and never reads the user", async () => {
    const { client, mocks } = mockZepClient({
      searchData: [{ name: "Zep", summary: "A memory service." }],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "nodes",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what is zep" });

    expect(mocks.userGet).not.toHaveBeenCalled();
    expect(mocks.searchNodes).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ query: "what is zep" }),
    );
    expect(out).toBe("- Zep: A memory service.");
  });

  it("returns an error string when the user has no graph UUID", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userGet.mockResolvedValueOnce({ uuid: MOCK_USER_UUID });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userUuid: MOCK_USER_UUID,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "q" });

    expect(out).toContain("could not determine which graph to search");
    expect(mocks.searchEdges).not.toHaveBeenCalled();
  });
});

describe("ZepGraphSearchTool — result formatting per scope", () => {
  it("returns the assembled context string from graph.getContext for auto scope", async () => {
    const { client, mocks } = mockZepClient({
      autoContext: "  assembled context block  ",
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "auto",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "x" });

    expect(mocks.getContext).toHaveBeenCalledWith(GRAPH_UUID, {
      query: "x",
      filters: undefined,
    });
    expect(mocks.searchEdges).not.toHaveBeenCalled();
    expect(out).toBe("assembled context block");
  });

  it("formats episodes results as their content", async () => {
    const { client, mocks } = mockZepClient({
      searchData: [{ content: "Alice booked a flight." }],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "episodes",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "flight" });

    expect(mocks.searchEpisodes).toHaveBeenCalled();
    expect(out).toBe("- Alice booked a flight.");
  });

  it("formats thread_summaries results as their summary", async () => {
    const { client, mocks } = mockZepClient({
      searchData: [
        { summary: "User discussed travel plans." },
        { summary: "User confirmed a booking." },
      ],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "thread_summaries",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what did we discuss" });

    expect(mocks.searchThreadSummaries).toHaveBeenCalled();
    expect(out).toBe(
      "- User discussed travel plans.\n- User confirmed a booking.",
    );
  });

  it("skips a thread summary with no summary text", async () => {
    const { client } = mockZepClient({
      searchData: [{ summary: "   " }, { summary: "Kept." }],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "thread_summaries",
      logger: silentLogger,
    });

    expect(await callTool(tool, { query: "q" })).toBe("- Kept.");
  });

  it("formats observations results as 'name: summary' when both are present", async () => {
    const { client, mocks } = mockZepClient({
      searchData: [
        { name: "Dark Mode Preference", summary: "User prefers dark mode." },
      ],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "observations",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "preferences" });

    expect(mocks.searchObservations).toHaveBeenCalled();
    expect(out).toBe("- Dark Mode Preference: User prefers dark mode.");
  });

  it("formats an observations result with only a name as just the name", async () => {
    const { client } = mockZepClient({
      searchData: [{ name: "Dark Mode Preference" }],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "observations",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "preferences" });
    expect(out).toBe("- Dark Mode Preference");
  });

  it("formats an observations result with only a summary as just the summary", async () => {
    const { client } = mockZepClient({
      searchData: [{ name: "", summary: "User prefers dark mode." }],
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "observations",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "preferences" });
    expect(out).toBe("- User prefers dark mode.");
  });

  it("formats a nodes result with only a name as just the name", async () => {
    const { client } = mockZepClient({ searchData: [{ name: "Zep" }] });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "nodes",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what is zep" });
    expect(out).toBe("- Zep");
  });

  it("returns 'No results found.' when nothing matches", async () => {
    const { client } = mockZepClient({ searchData: [] });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });
    expect(await callTool(tool, { query: "q" })).toBe("No results found.");
  });

  it("returns 'No results found.' when auto scope gives an empty context", async () => {
    const { client } = mockZepClient({ autoContext: "   " });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "auto",
      logger: silentLogger,
    });
    expect(await callTool(tool, { query: "q" })).toBe("No results found.");
  });
});

describe("ZepGraphSearchTool — errors", () => {
  it("rejects an empty query", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "   " });
    expect(out).toContain("Error");
    expect(mocks.searchEdges).not.toHaveBeenCalled();
  });

  it("returns an error string (never throws) when the search fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.searchEdges.mockRejectedValueOnce(new Error("search exploded"));
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "q" });
    expect(out).toContain("Graph search failed");
    expect(out).toContain("search exploded");
  });
});

describe("ZepGraphSearchTool — scope and reranker validation", () => {
  function makeTool(options: Record<string, unknown> = {}) {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger,
      ...options,
    });
    return { tool, mocks, logger };
  }

  function searchCalls(mocks: ReturnType<typeof mockZepClient>["mocks"]) {
    return [
      mocks.searchEdges,
      mocks.searchNodes,
      mocks.searchEpisodes,
      mocks.searchObservations,
      mocks.searchThreadSummaries,
      mocks.getContext,
    ].reduce((total, fn) => total + fn.mock.calls.length, 0);
  }

  it.each([
    ["episodes", "cross_encoder", "rrf, mmr"],
    ["episodes", "node_distance", "rrf, mmr"],
    ["observations", "node_distance", "rrf, mmr, cross_encoder"],
    ["thread_summaries", "node_distance", "rrf, mmr, cross_encoder"],
  ])(
    "returns a retryable error for scope '%s' with reranker '%s' and sends no request",
    async (scope, reranker, valid) => {
      const { tool, mocks } = makeTool();
      const result = await callTool(tool, {
        query: "q",
        scope,
        reranker,
        centerNodeUuid: "node-1",
      });
      expect(result).toBe(
        `Error: reranker '${reranker}' is not supported for scope '${scope}'. ` +
          `Use one of these rerankers for scope '${scope}': ${valid}.`,
      );
      expect(searchCalls(mocks)).toBe(0);
    },
  );

  it("returns a retryable error for episode_mentions without searchFilters.episodeUuids", async () => {
    const { tool, mocks } = makeTool();
    const result = await callTool(tool, {
      query: "q",
      scope: "nodes",
      reranker: "episode_mentions",
    });
    expect(result).toBe(
      "Error: reranker 'episode_mentions' is not available in this tool, " +
        "because 'episode_mentions' requires 'searchFilters.episodeUuids'. " +
        "Use one of these rerankers for scope 'nodes': rrf, mmr, node_distance, cross_encoder.",
    );
    expect(searchCalls(mocks)).toBe(0);
  });

  it("returns a retryable error for node_distance without centerNodeUuid", async () => {
    const { tool, mocks } = makeTool();
    const result = await callTool(tool, {
      query: "q",
      scope: "edges",
      reranker: "node_distance",
    });
    expect(result).toBe(
      "Error: reranker 'node_distance' requires 'centerNodeUuid'. Give the " +
        "UUID of a graph node in 'centerNodeUuid', or use one of these " +
        "rerankers: rrf, mmr, cross_encoder.",
    );
    expect(searchCalls(mocks)).toBe(0);
  });

  it("sends the default mmrLambda when the reranker is mmr and no value is set", async () => {
    const { tool, mocks } = makeTool();
    await callTool(tool, { query: "q", scope: "episodes", reranker: "mmr" });
    expect(mocks.searchEpisodes).toHaveBeenCalledWith(GRAPH_UUID, {
      pageSize: 10,
      query: "q",
      reranker: "mmr",
      mmrLambda: DEFAULT_MMR_LAMBDA,
    });
  });

  it("sends the mmrLambda of the model when it is set", async () => {
    const { tool, mocks } = makeTool();
    await callTool(tool, { query: "q", reranker: "mmr", mmrLambda: 0.2 });
    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ reranker: "mmr", mmrLambda: 0.2 }),
    );
  });

  it("sends each supported pair to the scope method", async () => {
    const { tool, mocks } = makeTool();
    await callTool(tool, { query: "q", scope: "nodes", reranker: "cross_encoder" });
    await callTool(tool, {
      query: "q",
      scope: "observations",
      reranker: "cross_encoder",
    });
    expect(mocks.searchNodes).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ reranker: "cross_encoder" }),
    );
    expect(mocks.searchObservations).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ reranker: "cross_encoder" }),
    );
  });

  it("allows episode_mentions on edges when searchFilters.episodeUuids is set", async () => {
    const { tool, mocks } = makeTool({
      searchFilters: { episodeUuids: ["ep-1"] },
    });
    const props = tool._getDeclaration().parameters?.properties ?? {};
    expect(props.reranker?.enum).toContain("episode_mentions");
    await callTool(tool, { query: "q", reranker: "episode_mentions" });
    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({
        reranker: "episode_mentions",
        filters: { episodeUuids: ["ep-1"] },
      }),
    );
  });

  it("shows only the rerankers of a pinned scope", () => {
    const { tool } = makeTool({ scope: "episodes" });
    const props = tool._getDeclaration().parameters?.properties ?? {};
    expect(props.reranker?.enum).toEqual(["rrf", "mmr"]);
  });

  it("removes mmr when mmrLambda is null and node_distance when centerNodeUuid is null", () => {
    const { tool } = makeTool({ mmrLambda: null, centerNodeUuid: null });
    const props = tool._getDeclaration().parameters?.properties ?? {};
    expect(props.reranker?.enum).toEqual(["rrf", "cross_encoder"]);
  });

  it("shows only the scopes that support a pinned reranker", () => {
    const { tool } = makeTool({ reranker: "cross_encoder" });
    const props = tool._getDeclaration().parameters?.properties ?? {};
    expect(props.scope?.enum).toEqual([
      "edges",
      "nodes",
      "observations",
      "thread_summaries",
      "auto",
    ]);
  });

  it("returns a retryable error when the model selects a scope that the pinned reranker does not support", async () => {
    const { tool, mocks } = makeTool({ reranker: "cross_encoder" });
    const result = await callTool(tool, { query: "q", scope: "episodes" });
    expect(result).toBe(
      "Error: scope 'episodes' does not support reranker 'cross_encoder', " +
        "which this tool always uses. Use one of these scopes: edges, nodes, " +
        "observations, thread_summaries, auto.",
    );
    expect(searchCalls(mocks)).toBe(0);
  });

  it("sends no reranker for the auto scope", async () => {
    const { tool, mocks } = makeTool();
    await callTool(tool, { query: "q", scope: "auto", reranker: "cross_encoder" });
    expect(mocks.getContext).toHaveBeenCalledWith(GRAPH_UUID, {
      query: "q",
      filters: undefined,
    });
  });

  it.each([
    [{ scope: "episodes", reranker: "cross_encoder" }, "scope 'episodes' does not support reranker 'cross_encoder'. Rerankers for scope 'episodes' are: rrf, mmr."],
    [{ scope: "nodes", reranker: "episode_mentions", searchFilters: { episodeUuids: ["ep-1"] } }, "scope 'nodes' does not support reranker 'episode_mentions'. Rerankers for scope 'nodes' are: rrf, mmr, node_distance, cross_encoder."],
    [{ scope: "observations", reranker: "node_distance" }, "scope 'observations' does not support reranker 'node_distance'. Rerankers for scope 'observations' are: rrf, mmr, cross_encoder."],
    [{ reranker: "episode_mentions" }, "'episode_mentions' requires 'searchFilters.episodeUuids'."],
    [{ reranker: "mmr", mmrLambda: null }, "'mmr' requires 'mmrLambda', but 'mmrLambda' is null."],
    [{ reranker: "node_distance", centerNodeUuid: null }, "'node_distance' requires 'centerNodeUuid', but 'centerNodeUuid' is null."],
    [{ reranker: "bogus" }, "Unsupported Zep graph search reranker: 'bogus'."],
  ])("throws at construction for invalid pinned options %j", (options, message) => {
    expect(() => makeTool(options)).toThrow(message);
  });

  it("accepts every supported pinned pair at construction", () => {
    const pairs: [string, string][] = [
      ["edges", "rrf"],
      ["edges", "mmr"],
      ["edges", "node_distance"],
      ["edges", "cross_encoder"],
      ["nodes", "node_distance"],
      ["nodes", "cross_encoder"],
      ["episodes", "mmr"],
      ["observations", "cross_encoder"],
      ["thread_summaries", "cross_encoder"],
      ["auto", "rrf"],
    ];
    for (const [scope, reranker] of pairs) {
      expect(() => makeTool({ scope, reranker })).not.toThrow();
    }
  });
});
