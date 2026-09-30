import { describe, it, expect, vi } from "vitest";
import { createZepSearchTool } from "../src/index.js";
import { makeFakeZep, asZep, run, page } from "./helpers.js";

describe("createZepSearchTool", () => {
  it("searches edges by default and returns facts", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce(
      page([{ fact: "Jane lives in Portland" }, { fact: "Jane is an engineer" }]),
    );
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });

    const result = await run(tool, { query: "where does Jane live" });

    expect(result).toEqual({
      facts: ["Jane lives in Portland", "Jane is an engineer"],
      found: true,
    });
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", {
      limit: 10,
      body: { query: "where does Jane live", reranker: "rrf" },
    });
  });

  it("returns the assembled context string for auto scope", async () => {
    const zep = makeFakeZep();
    zep.graph.getContext.mockResolvedValueOnce({ context: "  assembled block  " });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });
    const result = await run(tool, { query: "anything", scope: "auto" });
    expect(result.facts).toEqual(["assembled block"]);
    expect(result.found).toBe(true);
    expect(zep.graph.getContext).toHaveBeenCalledWith("g1", { query: "anything" });
  });

  it("returns summary text for the thread_summaries scope", async () => {
    const zep = makeFakeZep();
    zep.graph.searchThreadSummaries.mockResolvedValueOnce(
      page([{ summary: "User set up their account" }, {}]),
    );
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });

    const result = await run(tool, { query: "what happened", scope: "thread_summaries" });

    expect(result.facts).toEqual(["User set up their account"]);
    expect(result.found).toBe(true);
  });

  it("returns formatted entries for the observations scope", async () => {
    const zep = makeFakeZep();
    zep.graph.searchObservations.mockResolvedValueOnce(
      page([
        { name: "Pattern A", summary: "User logs in every morning" },
        { name: "Pattern B" },
      ]),
    );
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });

    const result = await run(tool, { query: "habits", scope: "observations" });

    expect(result.facts).toEqual(["Pattern A: User logs in every morning", "Pattern B"]);
    expect(result.found).toBe(true);
  });

  it("returns found: false with no results", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce(page([]));
    const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
    const result = await run(tool, { query: "nothing here" });
    expect(result).toEqual({ facts: [], found: false });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockRejectedValueOnce(new Error("timeout"));
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
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

  it("resolves identity from requestContext when resolveIdentity is provided", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce(page([{ fact: "override fact" }]));
    const resolveIdentity = vi.fn().mockReturnValue({ graphUuid: "g2" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      resolveIdentity,
    });

    const result = await run(tool, { query: "q" }, { requestContext: { tenant: "acme" } });

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(result.facts).toEqual(["override fact"]);
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g2", expect.anything());
  });

  it("awaits an async resolveIdentity and uses the resolved identity", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce(page([{ fact: "async fact" }]));
    const resolveIdentity = vi.fn().mockResolvedValue({ graphUuid: "g2" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
      resolveIdentity,
    });

    const result = await run(tool, { query: "q" }, { requestContext: { tenant: "acme" } });

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(result.facts).toEqual(["async fact"]);
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g2", expect.anything());
  });

  it("falls back to constructor binding when resolveIdentity is unset or returns nothing", async () => {
    const zep = makeFakeZep();
    zep.graph.searchEdges.mockResolvedValueOnce(page([{ fact: "base fact" }]));
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphUuid: "g1" },
    });

    const result = await run(tool, { query: "q" }, { requestContext: {} });

    expect(result.facts).toEqual(["base fact"]);
    expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", expect.anything());
  });

  describe("pin-or-expose", () => {
    it("exposes scope/reranker/limit/mmrLambda/centerNodeUuid by default", () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(Object.keys(shape).sort()).toEqual(
        ["centerNodeUuid", "limit", "mmrLambda", "query", "reranker", "scope"].sort(),
      );
    });

    it("supports all six search scopes in the exposed enum", () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
      const shape = (
        tool.inputSchema as unknown as {
          shape: { scope: { unwrap(): { options: string[] } } };
        }
      ).shape;
      expect(shape.scope.unwrap().options.sort()).toEqual(
        ["auto", "edges", "episodes", "nodes", "observations", "thread_summaries"].sort(),
      );
    });

    it("supports all five reranker values in the exposed enum", () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });
      const shape = (
        tool.inputSchema as unknown as {
          shape: { reranker: { unwrap(): { options: string[] } } };
        }
      ).shape;
      expect(shape.reranker.unwrap().options.sort()).toEqual(
        ["cross_encoder", "episode_mentions", "mmr", "node_distance", "rrf"].sort(),
      );
    });

    it("pinned params are hidden from the schema and always sent to Zep", async () => {
      const zep = makeFakeZep();
      zep.graph.searchNodes.mockResolvedValueOnce(page([{ name: "Apollo" }]));
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        pinnedParams: { scope: "nodes", limit: 3 },
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.scope).toBeUndefined();
      expect(shape.limit).toBeUndefined();

      // Model attempts to override a pinned param — pinned value always wins.
      const result = await run(tool, { query: "q", scope: "edges", limit: 99 } as never);
      expect(result.facts).toEqual(["Apollo"]);
      expect(zep.graph.searchEdges).not.toHaveBeenCalled();
      expect(zep.graph.searchNodes).toHaveBeenCalledWith(
        "g1",
        expect.objectContaining({ limit: 3 }),
      );
    });

    it("hidden params are omitted from the schema AND from the Zep call", async () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        hiddenParams: new Set(["mmrLambda", "centerNodeUuid"]),
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.mmrLambda).toBeUndefined();
      expect(shape.centerNodeUuid).toBeUndefined();

      await run(tool, { query: "q" });
      const sentBody = (
        zep.graph.searchEdges.mock.calls[0]![1] as { body: Record<string, unknown> }
      ).body;
      expect(sentBody).not.toHaveProperty("mmrLambda");
      expect(sentBody).not.toHaveProperty("centerNodeUuid");
    });

    it("omits unset optional params from the Zep call (query-only call)", async () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        hiddenParams: new Set(["scope", "reranker", "limit"]),
      });

      await run(tool, { query: "q" });
      expect(zep.graph.searchEdges).toHaveBeenCalledWith("g1", { body: { query: "q" } });
    });

    it("legacy scope/limit/reranker constructor args pin (and hide) their parameter", async () => {
      const zep = makeFakeZep();
      zep.graph.searchEpisodes.mockResolvedValueOnce(page([{ content: "raw text" }]));
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        scope: "episodes",
        limit: 5,
        reranker: "mmr",
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.scope).toBeUndefined();
      expect(shape.limit).toBeUndefined();
      expect(shape.reranker).toBeUndefined();

      const result = await run(tool, { query: "q" } as never);
      expect(result.facts).toEqual(["raw text"]);
      expect(zep.graph.searchEpisodes).toHaveBeenCalledWith("g1", {
        limit: 5,
        body: { query: "q", reranker: "mmr" },
      });
    });

    it("filters and bfsOriginNodeUuids are constructor-only and always applied", async () => {
      const zep = makeFakeZep();
      const filters = { nodeLabels: ["Person"] };
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        filters,
        bfsOriginNodeUuids: ["uuid-1", "uuid-2"],
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.filters).toBeUndefined();
      expect(shape.bfsOriginNodeUuids).toBeUndefined();

      await run(tool, { query: "q" });
      expect(zep.graph.searchEdges).toHaveBeenCalledWith(
        "g1",
        expect.objectContaining({
          body: expect.objectContaining({
            filters,
            bfsOriginNodeUuids: ["uuid-1", "uuid-2"],
          }),
        }),
      );
    });

    it("omits a model-provided auto-incompatible reranker when scope is 'auto'", async () => {
      const zep = makeFakeZep();
      zep.graph.getContext.mockResolvedValueOnce({ context: "block" });
      const warn = vi.fn();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        logger: { warn },
      });

      await run(tool, { query: "q", scope: "auto", reranker: "node_distance" });

      expect(zep.graph.getContext).toHaveBeenCalledWith("g1", { query: "q" });
      expect(warn).toHaveBeenCalledOnce();
    });

    it("resolves a pinned auto-incompatible scope/reranker pair at construction", async () => {
      const zep = makeFakeZep();
      zep.graph.getContext.mockResolvedValueOnce({ context: "block" });
      const warn = vi.fn();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        pinnedParams: { scope: "auto", reranker: "episode_mentions" },
        logger: { warn },
      });
      expect(warn).toHaveBeenCalledOnce();

      await run(tool, { query: "q" });

      expect(zep.graph.getContext).toHaveBeenCalledWith("g1", { query: "q" });
    });

    it("clamps a model-provided limit to Zep's 50-result ceiling", async () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });

      await run(tool, { query: "q", limit: 200 });

      expect(zep.graph.searchEdges).toHaveBeenCalledWith(
        "g1",
        expect.objectContaining({ limit: 50 }),
      );
    });

    it("clamps a pinned limit to Zep's 50-result ceiling at construction, with a warning", async () => {
      const zep = makeFakeZep();
      const warn = vi.fn();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { graphUuid: "g1" },
        pinnedParams: { limit: 200 },
        logger: { warn },
      });
      expect(warn).toHaveBeenCalledOnce();

      await run(tool, { query: "q" });

      expect(zep.graph.searchEdges).toHaveBeenCalledWith(
        "g1",
        expect.objectContaining({ limit: 50 }),
      );
    });

    it("model-provided scope/reranker/limit/mmrLambda/centerNodeUuid are forwarded when exposed", async () => {
      const zep = makeFakeZep();
      zep.graph.searchNodes.mockResolvedValueOnce(page([{ name: "Apollo" }]));
      const tool = createZepSearchTool({ client: asZep(zep), binding: { graphUuid: "g1" } });

      await run(tool, {
        query: "q",
        scope: "nodes",
        reranker: "mmr",
        limit: 7,
        mmrLambda: 0.5,
        centerNodeUuid: "uuid-9",
      });

      expect(zep.graph.searchNodes).toHaveBeenCalledWith("g1", {
        limit: 7,
        body: {
          query: "q",
          reranker: "mmr",
          mmrLambda: 0.5,
          centerNodeUuid: "uuid-9",
        },
      });
    });
  });
});
