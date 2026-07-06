import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import { Type } from "@google/genai";
import { ZepGraphSearchTool } from "../src/graph-search-tool.js";
import { fakeContext, capturingLogger, mockZepClient, silentLogger } from "./helpers.js";

function callTool(
  tool: ZepGraphSearchTool,
  args: Record<string, unknown>,
  ctx = fakeContext({ userId: "u" }),
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
      "episode_mentions",
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
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
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

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({
        query: "q",
        scope: "edges",
        reranker: "rrf",
        limit: 10,
      }),
    );
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
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      mmrLambda: null,
      centerNodeUuid: null,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    const call = mocks.search.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(call).not.toHaveProperty("mmrLambda");
    expect(call).not.toHaveProperty("centerNodeUuid");
  });
});

describe("ZepGraphSearchTool — merge precedence", () => {
  it("pinned beats model-provided arg", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      limit: 3,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q", limit: 50 });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 3 }),
    );
  });

  it("model-provided arg beats default when not pinned", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      logger: silentLogger,
    });

    await callTool(tool, { query: "q", limit: 25, scope: "nodes" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 25, scope: "nodes" }),
    );
  });

  it("default is used when the param is neither pinned nor model-provided", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "edges", reranker: "rrf", limit: 10 }),
    );
  });

  it("invalid model enum value falls back to default and logs a warning", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const logger = capturingLogger();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      logger,
    });

    await callTool(tool, { query: "q", scope: "bogus", reranker: "not-a-reranker" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "edges", reranker: "rrf" }),
    );
    expect(logger.warns.length).toBeGreaterThan(0);
  });
});

describe("ZepGraphSearchTool — constructor-only params", () => {
  it("searchFilters is passed through to graph.search", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const searchFilters = { nodeLabels: ["Person"] };
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      searchFilters,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ searchFilters }),
    );
  });

  it("bfsOriginNodeUuids is passed through to graph.search", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const bfsOriginNodeUuids = ["uuid-1", "uuid-2"];
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      bfsOriginNodeUuids,
      logger: silentLogger,
    });

    await callTool(tool, { query: "q" });

    expect(mocks.search).toHaveBeenCalledWith(
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
  it("searches the user's graph and formats edge facts", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: {
        edges: [{ fact: "Alice lives in Portland." }, { fact: "Alice hikes." }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "alice",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "where does alice live" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({
        userId: "alice",
        query: "where does alice live",
        scope: "edges",
        reranker: "rrf",
        limit: 10,
      }),
    );
    expect(out).toBe("- Alice lives in Portland.\n- Alice hikes.");
  });

  it("searches a standalone graph when graphId is set", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: { nodes: [{ name: "Zep", summary: "A memory service." }] },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphId: "kb-1",
      scope: "nodes",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what is zep" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ graphId: "kb-1", scope: "nodes" }),
    );
    expect(out).toBe("- Zep: A memory service.");
  });
});

describe("ZepGraphSearchTool — result formatting per scope", () => {
  it("returns the assembled context string for auto scope", async () => {
    const { client } = mockZepClient({
      searchResults: { context: "  assembled context block  " },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphId: "kb-1",
      scope: "auto",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "x" });
    expect(out).toBe("assembled context block");
  });

  it("formats thread_summaries results as 'name: summary' when both are present", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: {
        threadSummaries: [
          { name: "Travel Planning", summary: "User discussed travel plans." },
          { name: "Booking", summary: "User confirmed a booking." },
        ],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      scope: "thread_summaries" as never,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what did we discuss" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "thread_summaries" }),
    );
    expect(out).toBe(
      "- Travel Planning: User discussed travel plans.\n- Booking: User confirmed a booking.",
    );
  });

  it("formats a thread_summaries result with only a name as just the name", async () => {
    const { client } = mockZepClient({
      searchResults: {
        threadSummaries: [{ name: "Travel Planning" }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      scope: "thread_summaries" as never,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what did we discuss" });
    expect(out).toBe("- Travel Planning");
  });

  it("formats a thread_summaries result with only a summary as just the summary", async () => {
    const { client } = mockZepClient({
      searchResults: {
        threadSummaries: [{ name: "", summary: "User discussed travel plans." }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      scope: "thread_summaries" as never,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what did we discuss" });
    expect(out).toBe("- User discussed travel plans.");
  });

  it("formats observations results as 'name: summary' when both are present", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: {
        observations: [{ name: "Dark Mode Preference", summary: "User prefers dark mode." }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      scope: "observations" as never,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "preferences" });

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "observations" }),
    );
    expect(out).toBe("- Dark Mode Preference: User prefers dark mode.");
  });

  it("formats an observations result with only a name as just the name", async () => {
    const { client } = mockZepClient({
      searchResults: {
        observations: [{ name: "Dark Mode Preference" }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      scope: "observations" as never,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "preferences" });
    expect(out).toBe("- Dark Mode Preference");
  });

  it("formats an observations result with only a summary as just the summary", async () => {
    const { client } = mockZepClient({
      searchResults: {
        observations: [{ name: "", summary: "User prefers dark mode." }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      scope: "observations" as never,
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "preferences" });
    expect(out).toBe("- User prefers dark mode.");
  });

  it("formats a nodes result with only a name as just the name", async () => {
    const { client } = mockZepClient({
      searchResults: {
        nodes: [{ name: "Zep" }],
      },
    });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      graphId: "kb-1",
      scope: "nodes",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "what is zep" });
    expect(out).toBe("- Zep");
  });

  it("returns 'No results found.' when nothing matches", async () => {
    const { client } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
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
      userId: "u",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "   " });
    expect(out).toContain("Error");
    expect(mocks.search).not.toHaveBeenCalled();
  });

  it("returns an error string (never throws) when the search fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.search.mockRejectedValueOnce(new Error("search exploded"));
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      logger: silentLogger,
    });

    const out = await callTool(tool, { query: "q" });
    expect(out).toContain("Graph search failed");
    expect(out).toContain("search exploded");
  });
});
