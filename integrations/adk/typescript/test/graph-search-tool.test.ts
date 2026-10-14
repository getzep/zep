import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import { Type } from "@google/genai";
import { ZepGraphSearchTool } from "../src/graph-search-tool.js";
import {
  capturingLogger,
  fakeContext,
  mockZepClient,
  silentLogger,
} from "./helpers.js";

const GRAPH_UUID = "123e4567-e89b-42d3-a456-426614174002";

function run(
  tool: ZepGraphSearchTool,
  args: Record<string, unknown>,
  context = fakeContext({}),
) {
  return tool.runAsync({ args, toolContext: context as unknown as Context });
}

describe("ZepGraphSearchTool", () => {
  it("exposes all documented scopes and search parameters by default", () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });
    const declaration = tool._getDeclaration();
    const properties = declaration.parameters?.properties ?? {};

    expect(declaration.parameters?.required).toEqual(["query"]);
    expect(properties.query?.type).toBe(Type.STRING);
    expect(properties.scope?.enum).toEqual([
      "edges",
      "nodes",
      "episodes",
      "observations",
      "thread_summaries",
      "auto",
    ]);
    expect(properties).toHaveProperty("reranker");
    expect(properties).toHaveProperty("limit");
    expect(properties).toHaveProperty("mmrLambda");
    expect(properties).toHaveProperty("centerNodeUuid");
  });

  it("pins concrete parameters and hides null parameters", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "edges",
      reranker: "rrf",
      limit: 3,
      mmrLambda: null,
      centerNodeUuid: null,
      logger: silentLogger,
    });

    expect(Object.keys(tool._getDeclaration().parameters?.properties ?? {})).toEqual([
      "query",
    ]);
    await run(tool, { query: "q", scope: "nodes", limit: 50 });
    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ query: "q", pageSize: 3 }),
    );
    const params = mocks.searchEdges.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(params).not.toHaveProperty("mmrLambda");
    expect(params).not.toHaveProperty("centerNodeUuid");
  });

  it.each([
    ["edges", "searchEdges"],
    ["nodes", "searchNodes"],
    ["episodes", "searchEpisodes"],
    ["observations", "searchObservations"],
    ["thread_summaries", "searchThreadSummaries"],
  ] as const)("uses the v4 method for %s scope", async (scope, method) => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope,
      logger: silentLogger,
    });

    await run(tool, { query: "find it" });
    expect(mocks[method]).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ query: "find it" }),
    );
  });

  it("routes auto scope through graph.getContext", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: { context: "  useful context  " },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "auto",
      logger: silentLogger,
    });

    expect(await run(tool, { query: "q" })).toBe("useful context");
    expect(mocks.getContext).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ query: "q" }),
    );
  });

  it.each([
    ["edges", { edges: [{ fact: "Jane likes tea." }] }, "- Jane likes tea."],
    [
      "nodes",
      { nodes: [{ name: "Jane", summary: "Likes tea." }] },
      "- Jane: Likes tea.",
    ],
    [
      "episodes",
      { episodes: [{ content: "Jane likes tea." }] },
      "- Jane likes tea.",
    ],
    [
      "observations",
      { observations: [{ name: "Preference", summary: "Likes tea." }] },
      "- Preference: Likes tea.",
    ],
    [
      "thread_summaries",
      { threadSummaries: [{ summary: "Jane likes tea." }] },
      "- Jane likes tea.",
    ],
  ] as const)("formats %s scope results", async (scope, searchResults, expected) => {
    const { client } = mockZepClient({ searchResults });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope,
      logger: silentLogger,
    });

    await expect(run(tool, { query: "tea" })).resolves.toBe(expected);
  });

  it.each([
    ["nodes", { nodes: [{ name: "Jane" }] }, "- Jane"],
    ["nodes", { nodes: [{ summary: "Likes tea." }] }, "- Likes tea."],
    ["observations", { observations: [{ name: "Preference" }] }, "- Preference"],
    [
      "observations",
      { observations: [{ summary: "Likes tea." }] },
      "- Likes tea.",
    ],
  ] as const)("formats %s results when one text field is missing", async (
    scope,
    searchResults,
    expected,
  ) => {
    const { client } = mockZepClient({ searchResults });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope,
      logger: silentLogger,
    });

    await expect(run(tool, { query: "tea" })).resolves.toBe(expected);
  });

  it("applies constructor-only filters and BFS origins without exposing them", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "edges",
      searchFilters: { edgeTypes: ["LIKES"] },
      bfsOriginNodeUuids: ["node-uuid"],
      logger: silentLogger,
    });

    const properties = tool._getDeclaration().parameters?.properties ?? {};
    expect(properties).not.toHaveProperty("searchFilters");
    expect(properties).not.toHaveProperty("bfsOriginNodeUuids");
    await run(tool, { query: "tea" });
    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({
        filters: { edgeTypes: ["LIKES"] },
        bfsOriginNodeUuids: ["node-uuid"],
      }),
    );
  });

  it("uses model-provided values for exposed search parameters", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    await run(tool, {
      query: "tea",
      scope: "nodes",
      reranker: "mmr",
      limit: 5,
      mmrLambda: 0.25,
      centerNodeUuid: "center-uuid",
    });
    expect(mocks.searchNodes).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({
        pageSize: 5,
        reranker: "mmr",
        mmrLambda: 0.25,
        centerNodeUuid: "center-uuid",
      }),
    );
  });

  it("falls back to defaults for invalid model enums and logs warnings", async () => {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger,
    });

    await run(tool, { query: "tea", scope: "invalid", reranker: "invalid" });

    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ pageSize: 10, reranker: "rrf" }),
    );
    expect(logger.warns).toHaveLength(2);
  });

  it("returns no results for an empty search response", async () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "edges",
      logger: silentLogger,
    });

    await expect(run(tool, { query: "tea" })).resolves.toBe("No results found.");
  });

  it("does not call Zep for an empty query", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    await expect(run(tool, { query: "  " })).resolves.toContain(
      "non-empty 'query'",
    );
    expect(mocks.searchEdges).not.toHaveBeenCalled();
  });

  it("resolves and caches the user's graph UUID", async () => {
    const { client, mocks } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userUuid: "user-uuid",
      scope: "edges",
      logger: silentLogger,
    });

    await run(tool, { query: "one" });
    await run(tool, { query: "two" });
    expect(mocks.getUser).toHaveBeenCalledTimes(1);
    expect(mocks.getUser).toHaveBeenCalledWith("user-uuid");
  });

  it("returns an error instead of throwing when graph search fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.searchEdges.mockRejectedValueOnce(new Error("search failed"));
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "edges",
      logger: silentLogger,
    });
    expect(await run(tool, { query: "q" })).toContain("search failed");
  });
});
