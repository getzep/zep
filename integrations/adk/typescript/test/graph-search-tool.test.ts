import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { Context } from "@google/adk";
import { ZepGraphSearchTool } from "../src/graph-search-tool.js";
import { fakeContext, mockZepClient, silentLogger } from "./helpers.js";

function callTool(
  tool: ZepGraphSearchTool,
  args: Record<string, unknown>,
  ctx = fakeContext({ userId: "u" }),
) {
  return tool.runAsync({ args, toolContext: ctx as unknown as Context });
}

describe("ZepGraphSearchTool", () => {
  it("exposes a query parameter declaration to the model", () => {
    const { client } = mockZepClient();
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    const decl = tool._getDeclaration();
    expect(decl.name).toBe("zep_graph_search");
    expect(decl.parameters?.required).toEqual(["query"]);
    expect(decl.parameters?.properties?.query).toBeDefined();
  });

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

  it("returns 'No results found.' when nothing matches", async () => {
    const { client } = mockZepClient({ searchResults: { edges: [] } });
    const tool = new ZepGraphSearchTool({
      zep: client as unknown as ZepClient,
      userId: "u",
      logger: silentLogger,
    });
    expect(await callTool(tool, { query: "q" })).toBe("No results found.");
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
